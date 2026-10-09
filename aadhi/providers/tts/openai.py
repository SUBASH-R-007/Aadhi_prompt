"""OpenAI TTS (``audio.speech``; MP3, no word timings)."""

from __future__ import annotations

import inspect
import re
from typing import Any

from ...config import Settings
from .._common import emit_usage, make_error
from .._retry import SleepFn
from ..base import ProviderError, SpeechResult, Usage, UsageSink
from ..openai_common import ClientFactory, OpenAICaller
from .audio_utils import probe_duration
from .fake import rate_factor
from .voices import default_voice, voices_for

MAX_INPUT_CHARS = 4096
_VOICE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


async def _read_binary(resp: Any) -> bytes:
    """Bytes from an ``HttpxBinaryResponseContent`` (or anything exposing ``content``/``read``)."""
    content = getattr(resp, "content", None)
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    reader = getattr(resp, "aread", None) or getattr(resp, "read", None)
    if reader is None:
        return b""
    data = reader()
    if inspect.isawaitable(data):
        data = await data
    return bytes(data or b"")


class OpenAITTS:
    """``TTSProvider`` backed by ``client.audio.speech.create``."""

    name = "openai"
    supports_word_timings = False

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
    ) -> None:
        self.settings = settings
        self._caller = OpenAICaller(settings, timeout_s=120.0, client_factory=client_factory, sleep=sleep,
                                    max_attempts=max_attempts, service="voice service (OpenAI)")

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
        """Synthesize ``text`` to MP3 (``rate`` maps to ``speed``)."""
        if not (text or "").strip():
            raise make_error(ProviderError, self.name, "nothing to synthesise (empty text)", settings=self.settings)
        if len(text) > MAX_INPUT_CHARS:
            raise make_error(ProviderError, self.name, f"text longer than {MAX_INPUT_CHARS} characters",
                             settings=self.settings)
        voice = voice or self.default_voice(language)
        if not _VOICE_RE.match(voice):
            raise make_error(ProviderError, self.name, f"invalid voice id {voice[:64]!r}", settings=self.settings)
        model = self.settings.openai_tts_model
        speed = round(min(4.0, max(0.25, rate_factor(rate))), 2)

        async def request(client: Any) -> bytes:
            kwargs: dict[str, Any] = {"model": model, "voice": voice, "input": text, "response_format": "mp3"}
            if abs(speed - 1.0) > 1e-6:
                kwargs["speed"] = speed
            return await _read_binary(await client.audio.speech.create(**kwargs))

        audio = await self._caller.call(request, what="speech synthesis")
        # Characters are billed once the request succeeded: report before probing can fail.
        await emit_usage(on_usage, Usage(provider="openai", model=model, operation="tts", characters=len(text),
                                         meta={"voice": voice}))
        if not audio:
            raise make_error(ProviderError, self.name, "response contained no audio", settings=self.settings)
        duration = await probe_duration(audio, settings=self.settings)
        return SpeechResult(audio=audio, mime="audio/mpeg", duration=duration, words=[], voice=voice,
                            provider=self.name, sample_rate=24000)
