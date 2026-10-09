"""FakeTTS: deterministic WAV, exact word timings, real silence to trim; audio utils."""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from aadhi.providers.base import ProviderError, Usage
from aadhi.providers.tts import fake as fake_tts
from aadhi.providers.tts.audio_utils import pcm_to_wav, probe_duration, wav_duration, wav_sample_rate
from aadhi.providers.tts.fake import FakeTTS, rate_factor


def _samples(wav: bytes) -> tuple[int, list[int]]:
    with wave.open(io.BytesIO(wav), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    return rate, list(struct.unpack(f"<{len(frames) // 2}h", frames))


def _rms(chunk: list[int]) -> float:
    return math.sqrt(sum(s * s for s in chunk) / len(chunk)) / 32768 if chunk else 0.0


@pytest.mark.asyncio
async def test_wav_format_duration_and_words() -> None:
    tts = FakeTTS()
    usages: list[Usage] = []
    text = "Ohm's law, simply put: V equals I R."
    res = await tts.synthesize(text, voice="", language="en-IN", on_usage=usages.append)
    assert res.mime == "audio/wav" and res.provider == "fake" and res.sample_rate == 24000
    assert res.voice == "fake-female"
    rate, samples = _samples(res.audio)
    assert rate == 24000
    assert res.duration == pytest.approx(len(samples) / 24000, abs=1e-9)
    # ~0.06 s per character plus lead/tail silence
    expected = 0.12 + 0.2 + sum(max(0.08, 0.06 * len(t)) for t in text.split()) + 0.06 * (len(text.split()) - 1)
    assert res.duration == pytest.approx(expected, abs=0.01)
    assert [w.text for w in res.words] == ["Ohm's", "law", "simply", "put", "V", "equals", "I", "R"]
    assert res.words[0].start == pytest.approx(0.12, abs=1e-4)
    assert all(a.end < b.start for a, b in zip(res.words, res.words[1:]))
    assert res.words[-1].end <= res.duration - 0.2 + 1e-6
    assert usages == [Usage(provider="fake", model="fake-tts", operation="tts", characters=len(text),
                            meta={"voice": "fake-female"})]


@pytest.mark.asyncio
async def test_tone_bursts_and_silence_are_real() -> None:
    res = await FakeTTS().synthesize("alpha beta", voice="fake-male", language="en-IN")
    rate, samples = _samples(res.audio)
    lead = samples[: int(0.12 * rate) - 5]
    tail = samples[-int(0.2 * rate) + 5 :]
    assert max(abs(s) for s in lead) == 0 and max(abs(s) for s in tail) == 0
    w = res.words[0]
    burst = samples[int(w.start * rate) : int(w.end * rate)]
    level = _rms(burst)
    assert 0.03 < level < 0.25  # soft, low volume, clearly above silence
    gap = samples[int(res.words[0].end * rate) + 2 : int(res.words[1].start * rate) - 2]
    assert gap and max(abs(s) for s in gap) == 0


@pytest.mark.asyncio
async def test_deterministic_and_rate() -> None:
    tts = FakeTTS()
    a = await tts.synthesize("same text here", voice="fake-female", language="en-IN")
    b = await tts.synthesize("same text here", voice="fake-female", language="en-IN")
    assert a.audio == b.audio and a.words == b.words
    fast = await tts.synthesize("same text here", voice="fake-female", language="en-IN", rate="+50%")
    assert fast.duration < a.duration
    assert rate_factor("+10%") == pytest.approx(1.1) and rate_factor("-20%") == pytest.approx(0.8)
    assert rate_factor("garbage") == 1.0 and rate_factor("+900%") == 2.0


@pytest.mark.asyncio
async def test_punctuation_only_and_empty() -> None:
    res = await FakeTTS().synthesize("... --", voice="fake-female", language="ta-IN")
    assert res.words == [] and res.duration > 0.32
    with pytest.raises(ProviderError):
        await FakeTTS().synthesize("   ", voice="fake-female", language="en-IN")


@pytest.mark.asyncio
async def test_unicode_words() -> None:
    res = await FakeTTS().synthesize("மின்னோட்டம் என்பது", voice="fake-female", language="ta-IN")
    assert [w.text for w in res.words] == ["மின்னோட்டம்", "என்பது"]


def test_voices_and_default() -> None:
    tts = FakeTTS()
    assert tts.default_voice("hi-IN") == "fake-female"
    assert {v["id"] for v in tts.voices("en-IN")} == {"fake-female", "fake-male"}
    assert fake_tts.SAMPLE_RATE == 24000


@pytest.mark.asyncio
async def test_audio_utils_wav() -> None:
    pcm = struct.pack("<4800h", *([1000] * 4800))
    wav = pcm_to_wav(pcm, sample_rate=24000)
    assert wav_duration(wav) == pytest.approx(0.2)
    assert wav_sample_rate(wav) == 24000
    assert wav_duration(b"ID3notawav") is None
    assert await probe_duration(wav) == pytest.approx(0.2)


@pytest.mark.slow
@pytest.mark.asyncio
async def test_ffprobe_agrees_with_header(tmp_path) -> None:
    from aadhi.providers.media import probe_media

    res = await FakeTTS().synthesize("checking ffprobe agreement", voice="fake-female", language="en-IN")
    info = await probe_media(res.audio)
    assert info.duration == pytest.approx(res.duration, abs=0.002)
    assert info.has_audio and not info.has_video and info.sample_rate == 24000
    path = tmp_path / "x.wav"
    path.write_bytes(res.audio)
    assert await probe_duration(path) == pytest.approx(res.duration, abs=0.002)
