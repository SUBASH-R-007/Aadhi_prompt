"""Scene narration audio: trim each beat clip, join with fixed gaps, measure exact offsets.

Every beat is synthesised as its own TTS clip. Here each clip is decoded to mono PCM (ffmpeg),
its lead/trail silence measured with an RMS threshold (numpy) and removed, and the clips are
joined with ``INTER_BEAT_GAP_SECONDS`` + the beat's ``pause_after`` (+ the quiz countdown between
question and reveal beats) into one MP3 per scene. Offsets come from sample counts, so beat starts
are exact: the MP3 is written to a seekable temp file so ffmpeg can store the LAME/Xing header
(encoder delay + padding), which decoders use to drop the encoder priming — the decoded audio then
starts exactly where the PCM did. Word timings are shifted into scene time; when the provider gave
none, words are estimated by length (``words_estimated=True``). Clip decoding is bounded per scene
(``DECODE_PARALLEL`` ffmpeg processes).
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ..schemas.manifest import INTER_BEAT_GAP_SECONDS, BeatAudio
from ..schemas.timeline import TimedWord
from .aio import gather_all

SAMPLE_RATE = 24_000
WINDOW_SECONDS = 0.01
KEEP_PAD_SECONDS = 0.03  # keep a little of the measured silence so consonants are not clipped
ABS_SILENCE_DBFS = -50.0
REL_SILENCE_DB = 35.0
MP3_BITRATE = "64k"
AUDIO_VERSION = "2"  # 2: MP3s carry the LAME header (gapless, sample-exact offsets)
DECODE_PARALLEL = 4  # ffmpeg decoders per scene (scenes themselves are built in parallel)
_FORMATS = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
            "audio/ogg": "ogg", "audio/webm": "webm", "audio/aac": "aac", "audio/mp4": "mp4"}


class AudioError(RuntimeError):
    """ffmpeg failed to decode/encode narration audio."""


async def run_ffmpeg(args: list[str], data: bytes, *, ffmpeg: str = "ffmpeg", timeout: float = 180.0) -> bytes:
    """Run ffmpeg with ``data`` on stdin and return stdout (no shell; killed on timeout)."""
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file,pipe", *args,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout)
    except (TimeoutError, asyncio.TimeoutError, asyncio.CancelledError):
        proc.kill()
        await proc.wait()
        raise
    if proc.returncode != 0:
        tail = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["unknown error"]
        raise AudioError(f"ffmpeg failed: {tail[0][:200]}")
    return out


async def decode_pcm(data: bytes, mime: str, *, ffmpeg: str = "ffmpeg") -> np.ndarray:
    """Decode audio bytes to mono int16 PCM at SAMPLE_RATE."""
    fmt = _FORMATS.get(mime.split(";")[0].strip().lower())
    if fmt is None:
        raise AudioError(f"unsupported audio type {mime!r}")
    out = await run_ffmpeg(
        ["-f", fmt, "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"],
        data, ffmpeg=ffmpeg,
    )
    return np.frombuffer(out, dtype=np.int16).copy()


async def encode_mp3(pcm: np.ndarray, *, ffmpeg: str = "ffmpeg") -> bytes:
    """Encode mono int16 PCM (SAMPLE_RATE) as MP3 with a LAME/Xing header.

    The header (encoder delay + padding) can only be written to a seekable output, so ffmpeg writes
    to a temp file; without it decoders play the ~46 ms encoder priming and every beat offset would
    be early by that much.
    """
    tmp = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="aadhi-mp3-"))
    out = tmp / "narration.mp3"
    try:
        await run_ffmpeg(
            ["-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1", "-i", "pipe:0", "-c:a", "libmp3lame",
             "-b:a", MP3_BITRATE, "-write_xing", "1", "-f", "mp3", str(out)],
            pcm.astype(np.int16).tobytes(), ffmpeg=ffmpeg,
        )
        return await asyncio.to_thread(out.read_bytes)
    finally:
        await asyncio.to_thread(shutil.rmtree, tmp, True)


def measure_silence(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE) -> tuple[float, float]:
    """(lead, trail) silence in seconds, from 10 ms RMS windows vs an adaptive threshold."""
    if pcm.size == 0:
        return 0.0, 0.0
    win = max(1, int(sample_rate * WINDOW_SECONDS))
    n = pcm.size // win
    if n == 0:
        return 0.0, 0.0
    frames = pcm[: n * win].astype(np.float64).reshape(n, win) / 32768.0
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    threshold = max(ABS_SILENCE_DBFS, float(db.max()) - REL_SILENCE_DB)
    loud = np.nonzero(db > threshold)[0]
    if loud.size == 0:
        return 0.0, 0.0
    duration = pcm.size / sample_rate
    lead = max(0.0, loud[0] * WINDOW_SECONDS - KEEP_PAD_SECONDS)
    trail = max(0.0, duration - (loud[-1] + 1) * WINDOW_SECONDS - KEEP_PAD_SECONDS)
    return round(lead, 4), round(trail, 4)


def estimate_words(tokens: list[str], start: float, duration: float) -> list[TimedWord]:
    """Spread written tokens over [start, start+duration] weighted by length (+ punctuation pauses)."""
    if not tokens or duration <= 0:
        return []
    weights = [len(t) + 1.0 + (1.5 if t[-1:] in ",;:" else 0.0) + (2.5 if t[-1:] in ".!?" else 0.0) for t in tokens]
    total = sum(weights)
    out, t = [], start
    for tok, w in zip(tokens, weights, strict=True):
        d = duration * w / total
        out.append(TimedWord(text=tok, start=round(t, 3), end=round(t + d, 3)))
        t += d
    return out


def map_words(tokens: list[str], provider: list[dict[str, Any]], offset: float, lead: float, speech: float) -> list[TimedWord]:
    """Map provider word timings (clip-relative) onto the written tokens, in scene-audio time."""
    lo, hi = offset, offset + speech

    def shift(x: float) -> float:
        return round(min(hi, max(lo, x - lead + offset)), 3)

    timed = [(str(w.get("text", "")), shift(float(w.get("start", 0.0))), shift(float(w.get("end", 0.0)))) for w in provider]
    timed = [w for w in timed if w[0].strip()]
    if not tokens or not timed:
        return []
    if len(timed) == len(tokens):
        return [TimedWord(text=tok, start=s, end=max(s, e)) for tok, (_, s, e) in zip(tokens, timed, strict=True)]
    # piecewise-linear mapping by cumulative character position
    sp_len = [max(1, len(w[0])) for w in timed]
    sp_total = float(sum(sp_len))
    knots_x: list[float] = []
    knots_t: list[float] = []
    acc = 0.0
    for (_, s, e), n in zip(timed, sp_len, strict=True):
        knots_x.append(acc / sp_total)
        knots_t.append(s)
        acc += n
        knots_x.append(acc / sp_total)
        knots_t.append(e)
    wr_len = [max(1, len(t)) for t in tokens]
    wr_total = float(sum(wr_len))
    out, acc = [], 0.0
    for tok, n in zip(tokens, wr_len, strict=True):
        s = float(np.interp(acc / wr_total, knots_x, knots_t))
        acc += n
        e = float(np.interp(acc / wr_total, knots_x, knots_t))
        out.append(TimedWord(text=tok, start=round(s, 3), end=round(max(s, e), 3)))
    return out


@dataclass
class ClipInput:
    beat_id: str
    data: bytes
    mime: str
    narration: str  # written text (captions); tokens for word timings
    spoken_text: str = ""
    phase: str = "main"
    pause_after: float = 0.0
    words: list[dict[str, Any]] = field(default_factory=list)  # provider timings, clip-relative
    clip_asset_key: str | None = None


@dataclass
class SceneAudioResult:
    mp3: bytes
    duration: float
    beats: list[BeatAudio]
    countdown_start: float | None = None


async def build_scene_audio(clips: list[ClipInput], *, countdown_seconds: int | None = None,
                            ffmpeg: str = "ffmpeg") -> SceneAudioResult:
    """Trim + join beat clips; ``countdown_seconds`` adds silence between main and reveal beats."""
    if not clips:
        raise AudioError("no clips")
    sem = asyncio.Semaphore(DECODE_PARALLEL)

    async def decode(clip: ClipInput) -> np.ndarray:
        async with sem:
            return await decode_pcm(clip.data, clip.mime, ffmpeg=ffmpeg)

    pcms = await gather_all(decode(c) for c in clips)  # a failure cancels (and reaps) the other decoders
    sr = SAMPLE_RATE
    gap = int(round(INTER_BEAT_GAP_SECONDS * sr))
    parts: list[np.ndarray] = []
    t = 0  # samples
    beats: list[BeatAudio] = []
    countdown_start: float | None = None
    last_main = max((i for i, c in enumerate(clips) if c.phase == "main"), default=-1)
    for i, (clip, pcm) in enumerate(zip(clips, pcms, strict=True)):
        lead, trail = measure_silence(pcm, sr)
        a, b = int(round(lead * sr)), pcm.size - int(round(trail * sr))
        trimmed = pcm[a:b] if b > a else pcm
        offset = t / sr
        speech = trimmed.size / sr
        parts.append(trimmed)
        t += trimmed.size
        tokens = clip.narration.split()
        words = map_words(tokens, clip.words, offset, lead, speech) if clip.words else []
        estimated = not words
        if estimated:
            words = estimate_words(tokens, offset, speech)
        beats.append(BeatAudio(
            beat_id=clip.beat_id, phase="reveal" if clip.phase == "reveal" else "main", offset=round(offset, 4),
            speech_duration=round(speech, 4), pause_after=clip.pause_after, lead_silence=lead, trail_silence=trail,
            spoken_text=clip.spoken_text, words=words, words_estimated=estimated, clip_asset_key=clip.clip_asset_key,
        ))
        silence = int(round(clip.pause_after * sr))
        if i < len(clips) - 1:
            silence += gap
            if countdown_seconds and i == last_main and clips[i + 1].phase == "reveal":
                countdown_start = round((t + silence) / sr, 4)
                silence += int(round(countdown_seconds * sr))
        if silence:
            parts.append(np.zeros(silence, dtype=np.int16))
            t += silence
    pcm_all = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
    mp3 = await encode_mp3(pcm_all, ffmpeg=ffmpeg)
    return SceneAudioResult(mp3=mp3, duration=round(pcm_all.size / sr, 4), beats=beats, countdown_start=countdown_start)
