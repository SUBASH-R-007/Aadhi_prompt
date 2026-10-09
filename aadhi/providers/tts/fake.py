"""Deterministic offline TTS: mono 24 kHz WAV with one soft tone burst per word.

Layout: 0.12 s leading silence, then for each word a low-volume tone (~0.06 s per character, short
fades) followed by a 0.06 s gap, then 0.2 s trailing silence. Word timings are exact (sample
accurate), so silence trimming, beat joining and caption alignment can be tested for real.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import math
import re
import struct
import wave

from ...config import Settings
from .._common import emit_usage, make_error
from ..base import ProviderError, SpeechResult, Usage, UsageSink, WordTiming
from .audio_utils import probe_duration
from .voices import default_voice, voices_for

SAMPLE_RATE = 24000
LEAD_S = 0.12
TAIL_S = 0.2
SECONDS_PER_CHAR = 0.06
GAP_S = 0.06
MIN_WORD_S = 0.08
AMPLITUDE = 0.18  # about -15 dBFS: clearly above any silence threshold, still "soft"
FADE_S = 0.01
_PUNCT = "\"'`.,;:!?()[]{}<>-–—…“”‘’«»/\\|*_~"
_RATE = re.compile(r"^([+-])(\d{1,3})%$")


def rate_factor(rate: str) -> float:
    """``"+10%"`` -> 1.1 (speech speed multiplier, clamped to 0.5..2.0)."""
    m = _RATE.match((rate or "+0%").strip())
    if not m:
        return 1.0
    pct = int(m.group(2)) * (1 if m.group(1) == "+" else -1)
    return min(2.0, max(0.5, 1.0 + pct / 100.0))


def _tone(n: int, freq: float) -> list[int]:
    fade = max(1, int(FADE_S * SAMPLE_RATE))
    out: list[int] = []
    two_pi_f = 2 * math.pi * freq / SAMPLE_RATE
    for i in range(n):
        env = min(1.0, i / fade, (n - 1 - i) / fade) if n > 2 * fade else 0.5
        s = math.sin(two_pi_f * i) + 0.25 * math.sin(2 * two_pi_f * i)
        out.append(int(32767 * AMPLITUDE * env * s / 1.25))
    return out


def render(text: str, voice: str, rate: str = "+0%") -> tuple[bytes, list[WordTiming], float]:
    """Synthesize ``text`` -> (WAV bytes, word timings, exact duration)."""
    factor = rate_factor(rate)
    base_freq = 130.0 if voice.endswith("male") and not voice.endswith("female") else 210.0
    samples: list[int] = [0] * round(LEAD_S * SAMPLE_RATE)
    words: list[WordTiming] = []
    tokens = text.split()
    for idx, token in enumerate(tokens):
        n = round(max(MIN_WORD_S, SECONDS_PER_CHAR * len(token)) / factor * SAMPLE_RATE)
        clean = token.strip(_PUNCT)
        start = len(samples)
        if clean:
            digest = hashlib.sha256(clean.encode("utf-8")).digest()
            samples.extend(_tone(n, base_freq + digest[0] % 90))
            words.append(WordTiming(text=clean, start=start / SAMPLE_RATE, end=(start + n) / SAMPLE_RATE))
        else:
            samples.extend([0] * n)
        if idx < len(tokens) - 1:
            samples.extend([0] * round(GAP_S / factor * SAMPLE_RATE))
    samples.extend([0] * round(TAIL_S * SAMPLE_RATE))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue(), words, len(samples) / SAMPLE_RATE


class FakeTTS:
    """Offline ``TTSProvider`` with exact word timings."""

    name = "fake"
    supports_word_timings = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings

    def default_voice(self, language: str) -> str:
        return default_voice(self.name, language)

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        return voices_for(self.name, language)

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        rate: str = "+0%",
        on_usage: UsageSink | None = None,
    ) -> SpeechResult:
        """Render ``text`` deterministically (CPU work runs in a thread)."""
        if not (text or "").strip():
            raise make_error(ProviderError, self.name, "nothing to synthesise (empty text)", settings=self.settings)
        voice = voice or self.default_voice(language)
        audio, words, _ = await asyncio.to_thread(render, text, voice, rate)
        duration = await probe_duration(audio, settings=self.settings)
        await emit_usage(on_usage, Usage(provider="fake", model="fake-tts", operation="tts", characters=len(text),
                                         meta={"voice": voice}))
        return SpeechResult(audio=audio, mime="audio/wav", duration=duration, words=words, voice=voice,
                            provider=self.name, sample_rate=SAMPLE_RATE)
