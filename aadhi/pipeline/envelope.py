"""Narration loudness envelope: what the live player's mascot moves to.

``ENVELOPE_FPS`` values per second of a scene's narration file (the same MP3 the page plays), each
the RMS loudness of one ``1/fps`` window mapped linearly from ``FLOOR_DBFS`` (silence, 0) to
``CEIL_DBFS`` (loud speech, 1), quantised to one byte (0..255) and stored as base64 (about 40
characters per second) in ``SceneAudio.envelope`` and ``TimedScene.audio_envelope``. The player
indexes it at ``floor((scene_t - audio_offset) * fps)`` (``web/js/player/audio.js``
``envelopeLevel``), so the speech motion is deterministic and needs no WebAudio analyser, which only
works for same-origin audio and after a user gesture (S3/CDN narration got a synthetic bob).

Computed once at build time from the decoded MP3 (``aadhi.pipeline.assets``): the per-beat TTS clips
and the scene-audio cache key are untouched, so adding it re-runs no TTS.
"""

from __future__ import annotations

import base64
import binascii

import numpy as np

from . import audio
from .audio import SAMPLE_RATE

ENVELOPE_FPS = 30  # = the render frame rate: one value per video frame
FLOOR_DBFS = -50.0  # at or below: 0 (silence, breaths)
CEIL_DBFS = -15.0  # at or above: 255 (loud speech)


def loudness_envelope(pcm: np.ndarray, sample_rate: int = SAMPLE_RATE, fps: int = ENVELOPE_FPS) -> bytes:
    """One byte per ``1/fps`` window of mono int16 ``pcm`` (the last window may be partial)."""
    if pcm.size == 0 or fps <= 0:
        return b""
    win = max(1, int(round(sample_rate / fps)))
    n = -(-pcm.size // win)  # ceil
    padded = np.zeros(n * win, dtype=np.float64)
    padded[: pcm.size] = pcm.astype(np.float64) / 32768.0
    frames = padded.reshape(n, win)
    counts = np.full(n, float(win))
    counts[-1] = pcm.size - (n - 1) * win
    rms = np.sqrt((frames * frames).sum(axis=1) / counts)
    db = 20.0 * np.log10(np.maximum(rms, 1e-9))
    level = np.clip((db - FLOOR_DBFS) / (CEIL_DBFS - FLOOR_DBFS), 0.0, 1.0)
    return np.rint(level * 255.0).astype(np.uint8).tobytes()


def encode_envelope(values: bytes) -> str | None:
    """base64 text of an envelope (None when empty)."""
    return base64.b64encode(values).decode("ascii") if values else None


def decode_envelope(text: str | None) -> bytes:
    """The envelope bytes of :func:`encode_envelope` text (empty when absent or malformed)."""
    if not text:
        return b""
    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        return b""


async def narration_envelope(data: bytes, mime: str, *, ffmpeg: str = "ffmpeg", fps: int = ENVELOPE_FPS) -> str | None:
    """Envelope (base64) of an encoded narration file, decoded the way the player hears it."""
    pcm = await audio.decode_pcm(data, mime, ffmpeg=ffmpeg)  # module lookup: the same decoder as the narration
    return encode_envelope(loudness_envelope(pcm, SAMPLE_RATE, fps))
