"""ElevenLabs TTS (REST ``/v1/text-to-speech/{voice_id}/with-timestamps``; MP3 + character alignment)."""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any
from urllib.parse import quote

from ...config import Settings
from .._common import describe_exception, emit_usage, error_for_status, make_error
from .._http import HttpStatusError, classify_http_error, loop_local_client, raise_for_status
from .._retry import SleepFn, call_with_retries
from ..base import ProviderError, ProviderNotConfigured, SpeechResult, Usage, UsageSink
from .alignment import words_from_characters
from .audio_utils import probe_duration
from .fake import rate_factor
from .voices import default_voice, voices_for

ELEVENLABS_BASE_URL = "https://api.elevenlabs.io"
ELEVENLABS_HOST = "api.elevenlabs.io"
OUTPUT_FORMAT = "mp3_44100_128"
_VOICE_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")


class ElevenLabsTTS:
    """``TTSProvider`` with character-level timestamps (converted to word timings)."""

    name = "elevenlabs"
    supports_word_timings = True

    def __init__(
        self,
        settings: Settings,
        *,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
        timeout_s: float = 120.0,
        base_url: str = ELEVENLABS_BASE_URL,
    ) -> None:
        if not settings.elevenlabs_api_key.get_secret_value():
            raise ProviderNotConfigured("elevenlabs: ELEVENLABS_API_KEY is not configured", provider=self.name)
        self.settings = settings
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._base_url = base_url.rstrip("/")
        self._clients = loop_local_client(timeout_s)

    def default_voice(self, language: str) -> str:
        return default_voice(self.name, language)

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        return voices_for(self.name, language)

    async def _request(self, voice: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/v1/text-to-speech/{quote(voice, safe='')}/with-timestamps"
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key.get_secret_value(),
            "Accept": "application/json",
        }
        client = await self._clients.aget()
        response = await client.post(url, params={"output_format": OUTPUT_FORMAT}, json=body, headers=headers)
        raise_for_status(response)
        data = response.json()
        if not isinstance(data, dict):
            raise make_error(ProviderError, self.name, "unexpected response shape", settings=self.settings,
                             host=ELEVENLABS_HOST)
        return data

    async def synthesize(
        self,
        text: str,
        *,
        voice: str,
        language: str,
        rate: str = "+0%",
        on_usage: UsageSink | None = None,
    ) -> SpeechResult:
        """Synthesize ``text``; word timings come from the character alignment."""
        if not (text or "").strip():
            raise make_error(ProviderError, self.name, "nothing to synthesise (empty text)", settings=self.settings)
        voice = voice or self.default_voice(language)
        if not _VOICE_RE.match(voice):
            raise make_error(ProviderError, self.name, f"invalid voice id {voice[:64]!r}", settings=self.settings)
        body: dict[str, Any] = {
            "text": text,
            "model_id": self.settings.elevenlabs_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }
        speed = rate_factor(rate)
        if abs(speed - 1.0) > 1e-6:
            body["voice_settings"]["speed"] = round(min(1.2, max(0.7, speed)), 2)
        try:
            data = await call_with_retries(
                lambda: self._request(voice, body),
                classify=classify_http_error,
                max_attempts=self._max_attempts,
                base_delay=1.0,
                max_delay=30.0,
                sleep=self._sleep,
                label="elevenlabs tts",
                service="voice service (ElevenLabs)",
            )
        except ProviderError:
            raise
        except HttpStatusError as exc:
            raise make_error(error_for_status(exc.status), self.name, "speech synthesis failed",
                             settings=self.settings, status=exc.status, host=ELEVENLABS_HOST, detail=exc.body) from None
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise make_error(ProviderError, self.name, "speech synthesis failed", settings=self.settings,
                             host=ELEVENLABS_HOST, detail=describe_exception(exc)) from None
        # Characters are billed once the request succeeded: report before decoding/probing can fail.
        await emit_usage(on_usage, Usage(provider="elevenlabs", model=self.settings.elevenlabs_model,
                                         operation="tts", characters=len(text), meta={"voice": voice}))
        try:
            audio = base64.b64decode(str(data.get("audio_base64") or ""), validate=True)
        except (binascii.Error, ValueError):
            audio = b""
        if not audio:
            raise make_error(ProviderError, self.name, "response contained no audio", settings=self.settings,
                             host=ELEVENLABS_HOST)
        align = data.get("alignment") or data.get("normalized_alignment") or {}
        words = words_from_characters(
            align.get("characters") or [],
            align.get("character_start_times_seconds") or [],
            align.get("character_end_times_seconds") or [],
        )
        duration = await probe_duration(audio, settings=self.settings)
        for w in words:
            w.end = min(w.end, duration)
            w.start = min(w.start, w.end)
        return SpeechResult(audio=audio, mime="audio/mpeg", duration=duration, words=words, voice=voice,
                            provider=self.name, sample_rate=44100)
