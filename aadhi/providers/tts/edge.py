"""Microsoft Edge read-aloud TTS (``edge-tts``; free, MP3 with word boundaries)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from types import ModuleType
from typing import Any

from ...config import Settings
from .._common import describe_exception, emit_usage, make_error
from .._lazy import import_off_loop, imported
from .._retry import NOT_RETRYABLE, ErrorInfo, SleepFn, call_with_retries
from ..base import ProviderError, SpeechResult, Usage, UsageSink, WordTiming
from .audio_utils import probe_duration
from .voices import default_voice, voices_for

EDGE_HOST = "speech.platform.bing.com"
TICKS_PER_SECOND = 10_000_000  # edge-tts offsets/durations are in 100 ns units
_VOICE_RE = re.compile(r"^[A-Za-z]{2,3}-[A-Za-z0-9]{2,8}(?:-[A-Za-z0-9]+)+$")
_RATE_RE = re.compile(r"^[+-]\d{1,3}%$")
_PUNCT = "\"'`.,;:!?()[]{}<>-–—…“”‘’«»/\\|*_~"

CommunicateFactory = Callable[..., Any]


_BASE_RETRYABLE: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError, asyncio.TimeoutError)


def _retryable_errors() -> tuple[type[BaseException], ...]:
    """Transient error types. ``edge_tts``/``aiohttp`` classes are included once ``EdgeTTS`` has
    imported them off the event loop (``import_off_loop``); this function never imports anything."""
    errs = list(_BASE_RETRYABLE)
    ex = imported("edge_tts.exceptions")
    if ex is not None:
        errs += [ex.WebSocketError, ex.NoAudioReceived, ex.UnexpectedResponse, ex.UnknownResponse]
    aiohttp = imported("aiohttp")
    if aiohttp is not None:
        errs.append(aiohttp.ClientError)
    return tuple(errs)


async def load_edge_tts() -> ModuleType:
    """``edge_tts`` imported in a worker thread (its import loads the certifi CA bundle)."""
    module = await import_off_loop("edge_tts")
    await import_off_loop("edge_tts.exceptions")
    await import_off_loop("aiohttp")
    return module


def classify_edge_error(exc: BaseException) -> ErrorInfo:
    """Retry websocket/transport failures (429 counts as rate limited)."""
    if isinstance(exc, ProviderError):
        return NOT_RETRYABLE
    if isinstance(exc, _retryable_errors()):
        status = getattr(exc, "status", None)
        if status == 429:
            return ErrorInfo(retry=True, rate_limited=True, status=429)
        return ErrorInfo(retry=True, status=status if isinstance(status, int) else None)
    return NOT_RETRYABLE


def _default_factory(text: str, voice: str, **kwargs: Any) -> Any:
    edge_tts = imported("edge_tts")
    if edge_tts is None:  # synthesize() loads it off-loop first; never import on the event loop
        raise RuntimeError("edge_tts has not been loaded")
    return edge_tts.Communicate(text, voice, **kwargs)


class EdgeTTS:
    """``TTSProvider`` using the Edge read-aloud websocket service."""

    name = "edge"
    supports_word_timings = True

    def __init__(
        self,
        settings: Settings,
        *,
        communicate_factory: CommunicateFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 3,
        receive_timeout_s: int = 60,
    ) -> None:
        self.settings = settings
        self._factory = communicate_factory or _default_factory
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._receive_timeout_s = receive_timeout_s

    def default_voice(self, language: str) -> str:
        return default_voice(self.name, language)

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        return voices_for(self.name, language)

    async def _stream(self, text: str, voice: str, rate: str) -> tuple[bytes, list[WordTiming]]:
        comm = self._factory(text, voice, rate=rate, boundary="WordBoundary", receive_timeout=self._receive_timeout_s)
        audio = bytearray()
        words: list[WordTiming] = []
        async for chunk in comm.stream():
            kind = chunk.get("type")
            if kind == "audio":
                audio.extend(chunk.get("data") or b"")
            elif kind == "WordBoundary":
                word = str(chunk.get("text") or "").strip(_PUNCT)
                if not word:
                    continue
                offset = float(chunk.get("offset") or 0)
                duration = float(chunk.get("duration") or 0)
                words.append(WordTiming(text=word, start=offset / TICKS_PER_SECOND,
                                        end=(offset + duration) / TICKS_PER_SECOND))
        if not audio:
            raise make_error(ProviderError, self.name, "no audio received", settings=self.settings, host=EDGE_HOST)
        return bytes(audio), words

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        rate: str = "+0%",
        on_usage: UsageSink | None = None,
    ) -> SpeechResult:
        """Synthesize ``text`` to MP3; duration measured with ffprobe."""
        if not (text or "").strip():
            raise make_error(ProviderError, self.name, "nothing to synthesise (empty text)", settings=self.settings)
        voice = voice or self.default_voice(language)
        rate = rate or "+0%"
        if not _VOICE_RE.match(voice):
            raise make_error(ProviderError, self.name, f"invalid voice id {voice[:64]!r}", settings=self.settings)
        if not _RATE_RE.match(rate):
            raise make_error(ProviderError, self.name, f"invalid rate {rate[:16]!r}", settings=self.settings)
        try:
            await load_edge_tts()
        except ImportError:
            if self._factory is _default_factory:
                raise make_error(ProviderError, self.name, "the edge-tts package is not installed",
                                 settings=self.settings) from None
        try:
            audio, words = await call_with_retries(
                lambda: self._stream(text, voice, rate),
                classify=classify_edge_error,
                max_attempts=self._max_attempts,
                base_delay=1.0,
                max_delay=10.0,
                sleep=self._sleep,
                label="edge tts",
                service="voice service (Edge)",
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise make_error(ProviderError, self.name, "speech synthesis failed", settings=self.settings,
                             host=EDGE_HOST, detail=describe_exception(exc)) from None
        await emit_usage(on_usage, Usage(provider="edge", model=voice, operation="tts", characters=len(text)))
        duration = await probe_duration(audio, settings=self.settings)
        for w in words:  # boundaries can overshoot the encoded audio by a few ms
            w.end = min(w.end, duration)
            w.start = min(w.start, w.end)
        return SpeechResult(audio=audio, mime="audio/mpeg", duration=duration, words=words, voice=voice,
                            provider=self.name, sample_rate=24000)
