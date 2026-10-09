"""Audio helpers: duration measurement and PCM -> WAV conversion."""

from __future__ import annotations

import io
import wave
from pathlib import Path

from ...config import Settings
from ..media import probe_media


def pcm_to_wav(pcm: bytes, *, sample_rate: int = 24000, channels: int = 1, sample_width: int = 2) -> bytes:
    """Wrap raw little-endian PCM samples in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def wav_duration(data: bytes) -> float | None:
    """Exact duration of a PCM WAV from its header, or ``None`` if ``data`` is not a readable WAV."""
    if not data.startswith(b"RIFF") or data[8:12] != b"WAVE":
        return None
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except (wave.Error, EOFError):
        return None


def wav_sample_rate(data: bytes) -> int | None:
    """Sample rate of a PCM WAV, or ``None``."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getframerate()
    except (wave.Error, EOFError):
        return None


async def probe_duration(source: bytes | Path | str, *, settings: Settings | None = None) -> float:
    """Measured duration in seconds.

    PCM WAV bytes are measured exactly from the header (no subprocess); everything else goes
    through async ffprobe with ``-protocol_whitelist file,pipe``.
    """
    if isinstance(source, (bytes, bytearray)):
        exact = wav_duration(bytes(source))
        if exact is not None:
            return exact
    info = await probe_media(source, settings=settings)
    return info.duration
