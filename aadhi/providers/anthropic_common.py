"""Shared plumbing for the Anthropic adapter: per-loop clients, retries, error mapping."""

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

ANTHROPIC_HOST = "api.anthropic.com"
ClientFactory = Callable[[], Any]

# An error event in the middle of a stream arrives after "HTTP 200": the SDK raises a plain
# APIStatusError whose status is 200, so the error type in the body decides.
_STREAM_ERROR_STATUS = {"rate_limit_error": 429, "api_error": 500, "timeout_error": 504, "overloaded_error": 529}
_BLOCK_WORDS = ("content filtering", "usage policy", "safety")


def _retry_after(exc: BaseException) -> float | None:
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    try:
        return parse_retry_after(str(headers.get("retry-after") or ""))
    except Exception:  # noqa: BLE001 - odd header objects: fall back to backoff
        return None


def error_status(exc: BaseException) -> int | None:
    """HTTP status of an anthropic ``APIStatusError`` (mid-stream errors: derived from the error type)."""
    status = int(getattr(exc, "status_code", 0) or 0)
    if status < 400:
        status = _STREAM_ERROR_STATUS.get(str(getattr(exc, "type", "") or ""), 0)
    return status or None


def _stream_transport_errors() -> tuple[type[BaseException], ...]:
    """Transient ``httpx2`` errors that escape the SDK unwrapped once a stream is being read.

    Before the response starts the SDK wraps transport failures in ``APIConnectionError`` /
    ``APITimeoutError``; while the SSE body is iterated (``get_final_message``) a dropped
    connection (``RemoteProtocolError``), a reset (``ReadError``) or a per-read ``ReadTimeout``
    propagates as the raw ``httpx2`` exception. Configuration problems (``ProxyError``,
    ``UnsupportedProtocol``, ``LocalProtocolError``) are not transient and stay non-retryable.
    httpx2 is the anthropic SDK's own transport, so it is already imported whenever anthropic is.
    """
    import httpx2

    return (httpx2.TimeoutException, httpx2.NetworkError, httpx2.RemoteProtocolError)


def classify_anthropic_error(exc: BaseException) -> ErrorInfo:
    """Retry classification for anthropic SDK exceptions (429, 529 overloaded, 5xx, timeouts,
    connections dropped mid-stream)."""
    import anthropic

    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return ErrorInfo(retry=True)
    if isinstance(exc, anthropic.APIStatusError):
        status = error_status(exc)
        if status == 429:
            return ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=_retry_after(exc))
        overloaded = isinstance(
            exc, (anthropic.OverloadedError, anthropic.InternalServerError, anthropic.ServiceUnavailableError)
        )
        if overloaded or status in (408, 409) or (status or 0) >= 500:
            return ErrorInfo(retry=True, status=status, retry_after=_retry_after(exc))
        return ErrorInfo(retry=False, status=status)  # 400/401/403/404/413/422: retrying cannot help
    if isinstance(exc, ProviderError):
        return NOT_RETRYABLE
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError, *_stream_transport_errors())):
        return ErrorInfo(retry=True)
    return NOT_RETRYABLE


def anthropic_error(exc: BaseException, settings: Settings, what: str) -> ProviderError:
    """Convert a final anthropic exception into a redacted ``ProviderError`` subclass."""
    import anthropic
    import httpx2  # the SDK's transport (loaded with anthropic): a mid-stream ReadTimeout is a timeout too

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, anthropic.APIStatusError):
        status = error_status(exc)
        kind = str(getattr(exc, "type", "") or "")
        message = str(getattr(exc, "message", "") or "")
        cls: type[ProviderError] = ProviderError
        if status == 429:
            cls = RateLimited
        elif any(w in message.lower() for w in _BLOCK_WORDS):
            cls = ContentBlocked
        return make_error(cls, "anthropic", f"{what} failed", settings=settings, status=status,
                          host=ANTHROPIC_HOST, detail=" ".join(x for x in (kind, message) if x))
    if isinstance(exc, (anthropic.APITimeoutError, httpx2.TimeoutException, TimeoutError, asyncio.TimeoutError)):
        return make_error(ProviderError, "anthropic", f"{what} timed out", settings=settings, host=ANTHROPIC_HOST)
    return make_error(ProviderError, "anthropic", f"{what} failed", settings=settings, host=ANTHROPIC_HOST,
                      detail=describe_exception(exc))


class AnthropicCaller:
    """One ``AsyncAnthropic`` client per event loop + retries and redacted error mapping.

    ``timeout_s`` caps each attempt (``asyncio.wait_for``) and is also the SDK client's own request
    timeout, so neither layer cuts a long streamed answer short before the other. ``idle_timeout_s``
    is the client's HTTP read timeout: the longest wait for any bytes of a streamed answer. The API
    sends ``ping`` events while Claude thinks (the SDK drops them before they reach the caller, but
    they reset this timeout), so only a dead connection is silent that long.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        timeout_s: float,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int = 4,
        idle_timeout_s: float | None = None,
    ) -> None:
        key = settings.anthropic_api_key.get_secret_value()
        if not key and client_factory is None:
            raise ProviderNotConfigured("anthropic: ANTHROPIC_API_KEY is not configured", provider="anthropic")
        self.settings = settings
        self.timeout_s = float(timeout_s)
        self.idle_timeout_s = min(float(idle_timeout_s), self.timeout_s) if idle_timeout_s else None
        self._sleep = sleep
        self.max_attempts = max_attempts
        if client_factory is not None:
            self._clients: LoopLocal[Any] | None = None
            self._fixed_factory: ClientFactory | None = client_factory
        else:
            self._fixed_factory = None

            def make() -> Any:
                import anthropic

                # The SDK's own retries are off: call_with_retries owns backoff, Retry-After and logging.
                timeout: Any = self.timeout_s
                if self.idle_timeout_s is not None:
                    timeout = anthropic.Timeout(self.timeout_s, read=self.idle_timeout_s)
                return anthropic.AsyncAnthropic(api_key=key, timeout=timeout, max_retries=0)

            self._clients = LoopLocal(make)

    async def client(self) -> Any:
        if self._fixed_factory is not None:
            return self._fixed_factory()
        assert self._clients is not None
        try:
            return await self._clients.aget()
        except Exception as exc:  # noqa: BLE001 - client construction problems are configuration errors
            raise make_error(ProviderError, "anthropic", "client initialisation failed", settings=self.settings,
                             detail=describe_exception(exc)) from None

    async def call(self, fn: Callable[[Any], Awaitable[R]], *, what: str) -> R:
        """Run ``fn(client)`` with retries; raise ``ProviderError``/``RateLimited``/``ContentBlocked``."""
        try:
            await import_off_loop("anthropic")  # error classification imports anthropic: load the SDK off-loop
        except ImportError as exc:
            raise ProviderNotConfigured("anthropic: the anthropic package is not installed",
                                        provider="anthropic") from exc

        async def attempt() -> R:
            client = await self.client()
            return await asyncio.wait_for(fn(client), timeout=self.timeout_s)

        try:
            return await call_with_retries(
                attempt,
                classify=classify_anthropic_error,
                max_attempts=self.max_attempts,
                base_delay=1.0,
                max_delay=60.0,
                sleep=self._sleep,
                label="anthropic",
                service="AI service (Claude)",
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise anthropic_error(exc, self.settings, what) from None
