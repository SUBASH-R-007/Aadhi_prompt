"""Gemini TTS (``response_modalities=["AUDIO"]``; 24 kHz mono PCM wrapped as WAV, no word timings)."""

from __future__ import annotations

import re
from typing import Any

from ...config import Settings
from .._common import emit_usage, make_error
from .._retry import SleepFn
from ..base import ProviderError, SpeechResult, UsageSink
from ..gemini_common import (
    ClientFactory,
    GeminiCaller,
    check_blocked,
    genai_types,
    response_parts,
    usage_from_response,
)
from .audio_utils import pcm_to_wav, probe_duration, wav_sample_rate
from .voices import default_voice, voices_for

_VOICE_RE = re.compile(r"^[A-Za-z]{2,32}$")
_RATE_IN_MIME = re.compile(r"rate=(\d+)")


def _audio_from_parts(parts: list[Any]) -> tuple[bytes, str]:
    for part in parts:
        blob = getattr(part, "inline_data", None)
        data = getattr(blob, "data", None)
        if data:
            return bytes(data), str(getattr(blob, "mime_type", "") or "")
    return b"", ""


class GeminiTTS:
    """``TTSProvider`` using a Gemini speech model with prebuilt voices."""

    name = "gemini"
    supports_word_timings = False

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int | None = None,
    ) -> None:
        self.settings = settings
        self._caller = GeminiCaller(settings, timeout_s=180.0, client_factory=client_factory, sleep=sleep,
                                    max_attempts=max_attempts, scope="tts")

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
        """Synthesize ``text`` (``rate`` is not supported by the model and is ignored)."""
        if not (text or "").strip():
            raise make_error(ProviderError, self.name, "nothing to synthesise (empty text)", settings=self.settings)
        voice = voice or self.default_voice(language)
        if not _VOICE_RE.match(voice):
            raise make_error(ProviderError, self.name, f"invalid voice id {voice[:64]!r}", settings=self.settings)
        model = self.settings.gemini_tts_model
        types = await genai_types()
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))
            ),
        )

        async def request(client: Any, _key: str) -> Any:
            return await client.aio.models.generate_content(model=model, contents=text, config=config)

        response = await self._caller.call(request, what="speech synthesis")
        # Tokens are billed even for blocked/empty answers: report before any check or probe can fail.
        await emit_usage(on_usage, usage_from_response(response, model=model, operation="tts",
                                                       characters=len(text), meta={"voice": voice}))
        check_blocked(response, self.settings, "speech synthesis")
        data, mime = _audio_from_parts(response_parts(response))
        if not data:
            raise make_error(ProviderError, self.name, "response contained no audio", settings=self.settings)
        if data.startswith(b"RIFF"):
            audio, sample_rate = data, wav_sample_rate(data) or 24000
        else:
            m = _RATE_IN_MIME.search(mime)
            sample_rate = int(m.group(1)) if m else 24000
            audio = pcm_to_wav(data, sample_rate=sample_rate)
        duration = await probe_duration(audio, settings=self.settings)
        return SpeechResult(audio=audio, mime="audio/wav", duration=duration, words=[], voice=voice,
                            provider=self.name, sample_rate=sample_rate)
