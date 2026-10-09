"""Shared plumbing for OpenAI adapters (LLM + TTS): per-loop clients, retries, error mapping."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from ..config import Settings
from ._common import LoopLocal, describe_exception, make_error
from ._http import parse_retry_after
from ._lazy import import_off_loop
from ._retry import NOT_RETRYABLE, ErrorInfo, SleepFn, call_with_retries
from .base import ContentBlocked, ProviderError, ProviderNotConfigured, RateLimited

R = TypeVar("R")

OPENAI_HOST = "api.openai.com"
ClientFactory = Callable[[], Any]

#: Error codes inside a streamed answer (``error`` events, ``response.failed``, mid-stream error
#: payloads) that a new attempt can fix -> HTTP status they stand for (``None``: connection-like).
_STREAM_RETRY_CODES: dict[str, int | None] = {
    "server_error": 500,
    "rate_limit_exceeded": 429,
    "stream_ended": None,
}


class OpenAIStreamError(Exception):
    """An error reported inside a streamed answer (an ``error`` event or ``response.failed``), or a
    stream that ended before its final event (code ``stream_ended``)."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = str(code or "stream_error")
        self.message = str(message or "")
        super().__init__(" ".join(x for x in (self.code, self.message) if x))


def _stream_error_info(code: str) -> ErrorInfo:
    if code not in _STREAM_RETRY_CODES:
        return NOT_RETRYABLE
    status = _STREAM_RETRY_CODES[code]
    return ErrorInfo(retry=True, rate_limited=status == 429, status=status)


def classify_openai_error(exc: BaseException) -> ErrorInfo:
    """Retry classification for openai SDK exceptions (and errors inside a streamed answer)."""
    import openai

    if isinstance(exc, OpenAIStreamError):
        return _stream_error_info(exc.code)
    if isinstance(exc, openai.RateLimitError):
        code = str(getattr(exc, "code", "") or "")
        if code == "insufficient_quota":  # billing problem: retrying cannot help
            return ErrorInfo(retry=False, rate_limited=True, status=429)
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        try:
            retry_after = parse_retry_after(str(headers.get("retry-after") or ""))
        except Exception:  # noqa: BLE001 - odd header objects: fall back to backoff
            retry_after = None
        return ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=retry_after)
    if isinstance(exc, (openai.APITimeoutError, openai.APIConnectionError)):
        return ErrorInfo(retry=True)
    if isinstance(exc, openai.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0)
        return ErrorInfo(retry=status in (408, 409) or status >= 500, status=status or None)
    if isinstance(exc, openai.APIError):  # an error payload in the middle of a stream (after HTTP 200)
        return _stream_error_info(str(getattr(exc, "code", "") or getattr(exc, "type", "") or ""))
    if isinstance(exc, ProviderError):
        return NOT_RETRYABLE
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return ErrorInfo(retry=True)
    return NOT_RETRYABLE


def openai_error(exc: BaseException, settings: Settings, what: str) -> ProviderError:
    """Convert a final openai exception into a redacted ``ProviderError`` subclass."""
    import openai

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, OpenAIStreamError):
        status = _STREAM_RETRY_CODES.get(exc.code)
        stream_cls: type[ProviderError] = ProviderError
        if status == 429:
            stream_cls = RateLimited
        elif "policy" in exc.code or "safety" in exc.message.lower():
            stream_cls = ContentBlocked
        return make_error(stream_cls, "openai", f"{what} failed", settings=settings, status=status, host=OPENAI_HOST,
                          detail=str(exc))
    if isinstance(exc, openai.APIStatusError):
        status = int(getattr(exc, "status_code", 0) or 0) or None
        code = str(getattr(exc, "code", "") or "")
        message = str(getattr(exc, "message", "") or "")
        cls: type[ProviderError] = ProviderError
        if status == 429:
            cls = RateLimited
        elif code in ("content_policy_violation", "content_filter") or "safety" in message.lower():
            cls = ContentBlocked
        return make_error(cls, "openai", f"{what} failed", settings=settings, status=status, host=OPENAI_HOST,
                          detail=" ".join(x for x in (code, message) if x))
    if isinstance(exc, (openai.APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return make_error(ProviderError, "openai", f"{what} timed out", settings=settings, host=OPENAI_HOST)
    return make_error(ProviderError, "openai", f"{what} failed", settings=settings, host=OPENAI_HOST,
                      detail=describe_exception(exc))


class OpenAICaller:
    """One ``AsyncOpenAI`` client per event loop + retries and redacted error mapping."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout_s: float,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
        service: str = "AI service (OpenAI)",
    ) -> None:
        key = settings.openai_api_key.get_secret_value()
        if not key and client_factory is None:
            raise ProviderNotConfigured("openai: OPENAI_API_KEY is not configured", provider="openai")
        self.settings = settings
        self.timeout_s = float(timeout_s)
        self._sleep = sleep
        self.max_attempts = max_attempts
        self.service = service  # how job-log notices name it
        if client_factory is not None:
            self._clients: LoopLocal[Any] | None = None
            self._fixed_factory: ClientFactory | None = client_factory
        else:
            self._fixed_factory = None

            def make() -> Any:
                import openai

                return openai.AsyncOpenAI(api_key=key, timeout=self.timeout_s, max_retries=0)

            self._clients = LoopLocal(make)

    async def client(self) -> Any:
        if self._fixed_factory is not None:
            return self._fixed_factory()
        assert self._clients is not None
        try:
            return await self._clients.aget()
        except Exception as exc:  # noqa: BLE001 - client construction problems are configuration errors
            raise make_error(ProviderError, "openai", "client initialisation failed", settings=self.settings,
                             detail=describe_exception(exc)) from None

    async def call(self, fn: Callable[[Any], Awaitable[R]], *, what: str) -> R:
        """Run ``fn(client)`` with retries; raise ``ProviderError``/``RateLimited``/``ContentBlocked``."""
        try:
            await import_off_loop("openai")  # error classification imports openai: load the SDK off-loop first
        except ImportError as exc:
            raise ProviderNotConfigured("openai: the openai package is not installed", provider="openai") from exc

        async def attempt() -> R:
            client = await self.client()
            return await asyncio.wait_for(fn(client), timeout=self.timeout_s)

        try:
            return await call_with_retries(
                attempt,
                classify=classify_openai_error,
                max_attempts=self.max_attempts,
                base_delay=1.0,
                max_delay=60.0,
                sleep=self._sleep,
                label="openai",
                service=self.service,
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise openai_error(exc, self.settings, what) from None
