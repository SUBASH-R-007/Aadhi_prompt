"""audio: silence measurement, word mapping, trimming + joining (fast fakes and real ffmpeg)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pytest

from aadhi.pipeline.audio import (
    SAMPLE_RATE,
    ClipInput,
    build_scene_audio,
    decode_pcm,
    estimate_words,
    map_words,
    measure_silence,
)
from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS
from tests.pipeline.fakes import GAP, LEAD, TRAIL, WORD, tone_wav


def clip(beat_id: str, text: str, *, pause: float = 0.0, phase: str = "main", words: bool = True) -> ClipInput:
    data, timings, _ = tone_wav(text.split())
    return ClipInput(beat_id=beat_id, data=data, mime="audio/wav", narration=text, spoken_text=text, phase=phase,
                     pause_after=pause, words=[{"text": w.text, "start": w.start, "end": w.end} for w in timings] if words else [],
                     clip_asset_key=f"tts-{beat_id}")


def speech_len(n_words: int) -> float:
    return n_words * WORD + (n_words - 1) * GAP


def test_measure_silence():
    sr = SAMPLE_RATE
    tone = (8000 * np.sin(2 * np.pi * 440 * np.arange(int(0.5 * sr)) / sr)).astype(np.int16)
    pcm = np.concatenate([np.zeros(int(0.4 * sr), np.int16), tone, np.zeros(int(0.6 * sr), np.int16)])
    lead, trail = measure_silence(pcm)
    assert lead == pytest.approx(0.37, abs=0.011) and trail == pytest.approx(0.57, abs=0.011)
    noisy = pcm + (np.random.default_rng(1).normal(0, 20, pcm.size)).astype(np.int16)  # -64 dBFS hiss stays silent
    assert measure_silence(noisy)[0] == pytest.approx(0.37, abs=0.02)
    assert measure_silence(np.zeros(sr, np.int16)) == (0.0, 0.0)
    assert measure_silence(np.zeros(0, np.int16)) == (0.0, 0.0)


def test_estimate_words_weighted_by_length():
    words = estimate_words(["a", "longerword."], 1.0, 2.0)
    assert words[0].start == 1.0 and words[-1].end == pytest.approx(3.0, abs=1e-3)
    assert (words[1].end - words[1].start) > (words[0].end - words[0].start)
    assert estimate_words([], 0, 1) == [] and estimate_words(["x"], 0, 0) == []


def test_map_words_one_to_one_and_proportional():
    provider = [{"text": "V", "start": 0.2, "end": 0.4}, {"text": "equals", "start": 0.5, "end": 0.9}]
    same = map_words(["V", "equals"], provider, offset=10.0, lead=0.2, speech=0.7)
    assert [(w.text, w.start, w.end) for w in same] == [("V", 10.0, 10.2), ("equals", 10.3, 10.7)]
    # written "V=IR" vs spoken "V equals I R": proportional mapping, monotonic and inside the beat
    spoken = [{"text": t, "start": 0.2 + i * 0.3, "end": 0.45 + i * 0.3} for i, t in enumerate(["V", "equals", "I", "R"])]
    mapped = map_words(["V=IR", "now."], spoken, offset=1.0, lead=0.2, speech=1.2)
    assert len(mapped) == 2 and mapped[0].start == pytest.approx(1.0, abs=0.01)
    assert mapped[0].end <= mapped[1].start + 1e-6 and mapped[1].end <= 2.2 + 1e-6


def test_build_scene_audio_offsets(fast_audio):
    clips = [clip("s-b1", "one two three", pause=0.5), clip("s-b2", "four five")]
    res = asyncio.run(build_scene_audio(clips))
    b1, b2 = res.beats
    pad = 0.03
    assert b1.offset == 0.0
    assert b1.lead_silence == pytest.approx(LEAD - pad, abs=0.011)
    assert b1.trail_silence == pytest.approx(TRAIL - pad, abs=0.011)
    assert b1.speech_duration == pytest.approx(speech_len(3) + 2 * pad, abs=0.021)
    assert b2.offset == pytest.approx(b1.speech_duration + 0.5 + INTER_BEAT_GAP_SECONDS, abs=1e-3)
    assert res.duration == pytest.approx(b2.offset + b2.speech_duration, abs=1e-3)
    assert [w.text for w in b1.words] == ["one", "two", "three"] and not b1.words_estimated
    assert b1.words[0].start == pytest.approx(pad, abs=0.011)  # provider timing shifted by the trimmed lead
    assert all(b2.offset - 1e-6 <= w.start <= w.end <= b2.offset + b2.speech_duration + 1e-6 for w in b2.words)
    assert b1.clip_asset_key == "tts-s-b1" and b1.spoken_text == "one two three"
    assert res.mp3.startswith(b"ID3")


def test_build_scene_audio_quiz_countdown_and_estimated_words(fast_audio):
    clips = [clip("q-b1", "what is it", pause=0.2, words=False), clip("q-b2", "it is this", phase="reveal")]
    res = asyncio.run(build_scene_audio(clips, countdown_seconds=8))
    q, r = res.beats
    assert q.words_estimated and [w.text for w in q.words] == ["what", "is", "it"]
    assert r.phase == "reveal"
    assert res.countdown_start == pytest.approx(q.offset + q.speech_duration + 0.2 + INTER_BEAT_GAP_SECONDS, abs=1e-3)
    assert r.offset == pytest.approx(res.countdown_start + 8, abs=1e-3)


def test_trailing_pause_is_kept(fast_audio):
    res = asyncio.run(build_scene_audio([clip("a-b1", "only beat", pause=2.0)]))
    assert res.duration == pytest.approx(res.beats[0].speech_duration + 2.0, abs=1e-3)


@pytest.mark.slow
def test_real_ffmpeg_trim_concat_roundtrip():
    """Real ffmpeg decode (WAV + MP3 input) and MP3 encode; durations verified with ffprobe."""
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")
    wav, _, _ = tone_wav("alpha beta gamma".split())
    with tempfile.TemporaryDirectory() as tmp:
        src, mp3_in = Path(tmp) / "in.wav", Path(tmp) / "in.mp3"
        src.write_bytes(wav)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src), "-c:a", "libmp3lame",
                        "-b:a", "64k", str(mp3_in)], check=True)
        mp3_clip = mp3_in.read_bytes()
    clips = [clip("s-b1", "alpha beta gamma", pause=0.3),
             ClipInput(beat_id="s-b2", data=mp3_clip, mime="audio/mpeg", narration="alpha beta gamma", phase="main")]
    res = asyncio.run(build_scene_audio(clips))
    pcm = asyncio.run(decode_pcm(wav, "audio/wav"))
    assert pcm.size == pytest.approx(len(wav) / 2 - 22, abs=30)
    b1, b2 = res.beats
    assert b2.offset == pytest.approx(b1.speech_duration + 0.3 + INTER_BEAT_GAP_SECONDS, abs=1e-3)
    assert b2.speech_duration == pytest.approx(b1.speech_duration, abs=0.06)  # mp3 priming trimmed as silence
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "scene.mp3"
        out.write_bytes(res.mp3)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,format_name", "-of", "json",
                                str(out)], capture_output=True, text=True, check=True)
    fmt = json.loads(probe.stdout)["format"]
    assert fmt["format_name"] == "mp3"
    assert float(fmt["duration"]) == pytest.approx(res.duration, abs=0.12)


def test_clip_decoding_is_bounded_and_failure_cancels_siblings(monkeypatch):
    """At most DECODE_PARALLEL ffmpeg decoders per scene; one failure cancels (and reaps) the others."""
    from aadhi.pipeline import audio as audio_mod
    from tests.pipeline.fakes import dummy_mp3, wav_decode

    state = {"now": 0, "peak": 0, "cancelled": 0}

    async def slow_decode(data, mime, *, ffmpeg="ffmpeg"):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        try:
            await asyncio.sleep(0.01)
            if mime == "audio/broken":
                raise audio_mod.AudioError("corrupt clip")
            if mime == "audio/slow":
                await asyncio.sleep(5)
            return await wav_decode(data, "audio/wav")
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise
        finally:
            state["now"] -= 1

    monkeypatch.setattr(audio_mod, "decode_pcm", slow_decode)
    monkeypatch.setattr(audio_mod, "encode_mp3", dummy_mp3)
    clips = [clip(f"s-b{i}", f"word{i} more") for i in range(12)]
    res = asyncio.run(build_scene_audio(clips))
    assert len(res.beats) == 12 and 1 < state["peak"] <= audio_mod.DECODE_PARALLEL

    state.update(now=0, peak=0)
    bad = [clip("s-b1", "one two"), clip("s-b2", "three four"), clip("s-b3", "five six")]
    bad[0].mime, bad[1].mime = "audio/broken", "audio/slow"
    with pytest.raises(audio_mod.AudioError):
        asyncio.run(build_scene_audio(bad))
    assert state["cancelled"] >= 1 and state["now"] == 0  # the slow sibling was cancelled, nothing left running


@pytest.mark.slow
def test_mp3_offsets_are_sample_exact_after_decoding():
    """The scene MP3 carries the LAME header: decoded speech starts exactly at the PCM offsets."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    from aadhi.pipeline.audio import encode_mp3

    sr = SAMPLE_RATE
    tone = (8000 * np.sin(2 * np.pi * 440 * np.arange(sr) / sr)).astype(np.int16)
    pcm = np.concatenate([np.zeros(sr, np.int16), tone, np.zeros(sr, np.int16)])
    decoded = asyncio.run(decode_pcm(asyncio.run(encode_mp3(pcm)), "audio/mpeg"))
    onset = int(np.nonzero(np.abs(decoded) > 2000)[0][0]) / sr
    assert onset == pytest.approx(1.0, abs=0.003)  # piped MP3s started ~46 ms late (encoder priming)
    assert decoded.size / sr == pytest.approx(pcm.size / sr, abs=0.03)
    # end to end: every beat onset in the decoded narration matches its BeatAudio.offset
    clips = [clip("s-b1", "one two three", pause=0.4), clip("s-b2", "four five"), clip("s-b3", "six")]
    res = asyncio.run(build_scene_audio(clips))
    scene = asyncio.run(decode_pcm(res.mp3, "audio/mpeg"))
    loud = np.abs(scene) > 2000
    for b in res.beats:
        start = int(b.offset * sr)
        first = start + int(np.nonzero(loud[start:])[0][0])
        assert first / sr - b.offset == pytest.approx(0.03, abs=0.012)  # the kept 30 ms pad, not 46 ms more
    assert scene.size / sr == pytest.approx(res.duration, abs=0.03)
