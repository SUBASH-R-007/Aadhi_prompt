"""Shared plumbing for Google Gemini adapters (LLM, TTS, images, Veo).

* ``KeyPool`` rotates through ``Settings.all_gemini_keys`` when a key hits 429; a rate-limited key
  cools down before it is used again. Pools are process-wide (shared by every thread/loop).
* ``GeminiCaller.call`` runs one SDK request with tenacity retries (429/5xx/timeouts/connection
  errors), key rotation and redacted error mapping.
* Response helpers detect safety blocks (a ``RECITATION`` stop is reported separately so text/JSON
  calls can re-ask), extract text and build ``Usage`` records.
* ``genai_types()`` imports the SDK in a worker thread the first time (never on the event loop).

``google.genai`` keeps one aiohttp session per event loop internally, so a single ``Client`` per key
is safe to share between worker threads.
"""

from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from types import ModuleType
from typing import Any, TypeVar

from ..config import Settings
from ._common import describe_exception, fingerprint, make_error, safe_attr
from ._lazy import import_off_loop
from ._retry import NOT_RETRYABLE, ErrorInfo, SleepFn, call_with_retries
from .base import ContentBlocked, ProviderError, ProviderNotConfigured, RateLimited, Usage

R = TypeVar("R")

GEMINI_HOST = "generativelanguage.googleapis.com"
DEFAULT_COOLDOWN_S = 60.0
#: Finish reasons that mean the output was withheld by a safety/policy filter (never re-asked).
BLOCK_FINISH_REASONS = frozenset(
    {
        "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
        "IMAGE_RECITATION",
    }
)
#: Stochastic stop because the output resembled source/training text: a paraphrase re-ask usually
#: succeeds, so text/JSON calls treat it as a validation problem rather than a safety block.
RECITATION_FINISH_REASON = "RECITATION"
_SAFETY_WORDS = re.compile(r"(?i)\b(safety|blocked|prohibited|responsible ai|rai filter|harm)")

ClientFactory = Callable[[str], Any]
#: How job-log notices name the service of each caller scope.
_SERVICE_NAMES = {
    "llm": "AI service (Gemini)",
    "tts": "voice service (Gemini)",
    "image": "image service (Gemini)",
    "video": "video service (Veo)",
}


class KeyPool:
    """Thread-safe API key rotation with per-key cooldown after rate limiting."""

    def __init__(self, keys: Sequence[str], *, cooldown_s: float = DEFAULT_COOLDOWN_S) -> None:
        if not keys:
            raise ValueError("KeyPool needs at least one key")
        self._keys = list(dict.fromkeys(keys))
        self._cooldown_until = [0.0] * len(self._keys)
        self._index = 0
        self._cooldown_s = cooldown_s
        self._lock = threading.Lock()

    @property
    def size(self) -> int:
        return len(self._keys)

    def acquire(self) -> str:
        """Current key, skipping keys that are cooling down (or the least-recently limited one)."""
        now = time.monotonic()
        with self._lock:
            n = len(self._keys)
            for step in range(n):
                i = (self._index + step) % n
                if self._cooldown_until[i] <= now:
                    self._index = i
                    return self._keys[i]
            i = min(range(n), key=lambda j: self._cooldown_until[j])
            self._index = i
            return self._keys[i]

    def mark_rate_limited(self, key: str, retry_after: float | None = None) -> None:
        """Put ``key`` on cooldown and advance to the next key."""
        with self._lock:
            try:
                i = self._keys.index(key)
            except ValueError:
                return
            self._cooldown_until[i] = time.monotonic() + max(1.0, retry_after or self._cooldown_s)
            if self._index == i:
                self._index = (i + 1) % len(self._keys)

    def has_fresh_key(self) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(t <= now for t in self._cooldown_until)


_POOLS: dict[tuple[str, ...], KeyPool] = {}
_POOLS_LOCK = threading.Lock()


def key_pool(keys: Sequence[str], scope: str = "default") -> KeyPool:
    """Process-wide pool for this exact key list and ``scope`` (quotas are per model family, so a
    TTS rate limit does not cool a key down for LLM calls)."""
    ident = (scope, *(fingerprint(k) for k in keys))
    with _POOLS_LOCK:
        pool = _POOLS.get(ident)
        if pool is None:
            pool = KeyPool(keys)
            _POOLS[ident] = pool
        return pool


def reset_key_pools() -> None:
    """Forget cooldown state (tests)."""
    with _POOLS_LOCK:
        _POOLS.clear()


def _parse_retry_delay(details: Any) -> float | None:
    """``RetryInfo.retryDelay`` (e.g. ``"37s"``) from a google.rpc error payload."""
    err = details.get("error", details) if isinstance(details, dict) else None
    items = err.get("details") if isinstance(err, dict) else None
    for item in items if isinstance(items, list) else []:
        delay = item.get("retryDelay") if isinstance(item, dict) else None
        if isinstance(delay, str):
            m = re.match(r"^\s*(\d+(?:\.\d+)?|\.\d+)s\s*$", delay)
            if m:
                return float(m.group(1))  # not capped: call_with_retries decides how long to wait
    return None


_TRANSPORT_CLASSES = (("httpx", "TransportError"), ("httpx2", "TransportError"), ("aiohttp", "ClientError"))


def _transport_errors() -> tuple[type[BaseException], ...]:
    """Retryable transport exception types of the HTTP stacks google-genai may use.

    Nothing is imported here (module-level imports of aiohttp/httpx would block whichever event loop
    first imports this module): a class from a module that was never imported cannot be raised.
    """
    errs: list[type[BaseException]] = [ConnectionError, TimeoutError, asyncio.TimeoutError]
    for module_name, attr in _TRANSPORT_CLASSES:
        cls = getattr(sys.modules.get(module_name), attr, None)
        if isinstance(cls, type) and issubclass(cls, BaseException):
            errs.append(cls)
    return tuple(errs)


def classify_gemini_error(exc: BaseException) -> ErrorInfo:
    """Retry classification for google-genai / transport exceptions."""
    from google.genai import errors as genai_errors

    if isinstance(exc, genai_errors.APIError):
        code = int(exc.code or 0)
        if code == 429:
            return ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=_parse_retry_delay(exc.details))
        if code in (408, 409) or code >= 500:
            return ErrorInfo(retry=True, status=code)
        return ErrorInfo(retry=False, status=code or None)
    if isinstance(exc, ProviderError):
        return NOT_RETRYABLE
    if isinstance(exc, _transport_errors()):
        return ErrorInfo(retry=True)
    return NOT_RETRYABLE


def gemini_error(exc: BaseException, settings: Settings, what: str) -> ProviderError:
    """Convert a final SDK/transport exception into a redacted ``ProviderError`` subclass."""
    from google.genai import errors as genai_errors

    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, genai_errors.APIError):
        code = int(exc.code or 0) or None
        message = str(exc.message or "")
        cls: type[ProviderError] = ProviderError
        if code == 429:
            cls = RateLimited
        elif code == 400 and _SAFETY_WORDS.search(message):
            cls = ContentBlocked
        detail = " ".join(x for x in (str(exc.status or ""), message) if x)
        return make_error(cls, "gemini", f"{what} failed", settings=settings, status=code, host=GEMINI_HOST,
                          detail=detail)
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return make_error(ProviderError, "gemini", f"{what} timed out", settings=settings, host=GEMINI_HOST)
    return make_error(ProviderError, "gemini", f"{what} failed", settings=settings, host=GEMINI_HOST,
                      detail=describe_exception(exc))


async def genai_types() -> ModuleType:
    """``google.genai.types``; the first call imports the SDK in a worker thread (a cold import takes
    about a second and must not block the event loop)."""
    await import_off_loop("google.genai")
    return await import_off_loop("google.genai.types")


def default_client_factory(timeout_s: float) -> ClientFactory:
    """Factory creating ``google.genai.Client`` objects with an HTTP timeout."""

    def make(key: str) -> Any:
        from google import genai
        from google.genai import types

        return genai.Client(api_key=key, http_options=types.HttpOptions(timeout=int(timeout_s * 1000)))

    return make


class GeminiCaller:
    """Runs Gemini SDK requests with retries, key rotation and error mapping."""

    def __init__(
        self,
        settings: Settings,
        *,
        timeout_s: float,
        client_factory: ClientFactory | None = None,
        sleep: SleepFn | None = None,
        max_attempts: int | None = None,
        scope: str = "default",
    ) -> None:
        keys = settings.all_gemini_keys
        if not keys:
            raise ProviderNotConfigured("gemini: GEMINI_API_KEY is not configured", provider="gemini")
        self.settings = settings
        self.pool = key_pool(keys, scope)
        self.scope = scope
        self.timeout_s = float(timeout_s)
        self._factory = client_factory
        self._sleep = sleep
        self.max_attempts = max_attempts or min(10, 4 + self.pool.size)
        self._clients: dict[str, Any] = {}
        self._lock = threading.Lock()

    async def client(self, key: str) -> Any:
        """Client for ``key`` (created once; creation loads TLS state so it runs in a thread)."""
        fp = fingerprint(key)
        with self._lock:
            client = self._clients.get(fp)
        if client is not None:
            return client
        if self._factory is not None:
            client = self._factory(key)
        else:
            client = await asyncio.to_thread(default_client_factory(self.timeout_s), key)
        with self._lock:
            return self._clients.setdefault(fp, client)

    async def call(self, fn: Callable[[Any, str], Awaitable[R]], *, what: str) -> R:
        """Run ``fn(client, key)`` with retries; raise ``ProviderError``/``RateLimited``/``ContentBlocked``."""
        await genai_types()  # error classification imports google.genai.errors: load the SDK off-loop first

        async def attempt() -> R:
            key = self.pool.acquire()
            client = await self.client(key)
            try:
                return await asyncio.wait_for(fn(client, key), timeout=self.timeout_s)
            except Exception as exc:
                info = classify_gemini_error(exc)
                if info.rate_limited:
                    self.pool.mark_rate_limited(key, info.retry_after)
                raise

        try:
            return await call_with_retries(
                attempt,
                classify=classify_gemini_error,
                max_attempts=self.max_attempts,
                base_delay=1.0,
                max_delay=60.0,
                has_fresh_key=self.pool.has_fresh_key,
                sleep=self._sleep,
                label=f"gemini {self.scope}",
                service=_SERVICE_NAMES.get(self.scope, "Gemini service"),
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a redacted provider error
            raise gemini_error(exc, self.settings, what) from None


# --- response helpers ------------------------------------------------------------------


def _enum_name(value: Any) -> str:
    if value is None:
        return ""
    name = getattr(value, "name", None)
    return str(name if name is not None else value).upper()


def finish_reason(response: Any) -> str:
    """Upper-case finish reason of the first candidate (``""`` when there is none)."""
    candidates = safe_attr(response, "candidates") or []
    return _enum_name(safe_attr(candidates[0], "finish_reason")) if candidates else ""


def block_reason(response: Any, *, include_recitation: bool = False) -> str | None:
    """``"prompt safety"`` / ``"finish reason safety"`` when the response was blocked, else ``None``.

    A ``RECITATION`` finish counts as blocked only with ``include_recitation`` (media outputs); text
    and JSON calls re-ask instead (see ``is_recitation``).
    """
    reason = _enum_name(safe_attr(response, "prompt_feedback", "block_reason"))
    if reason and reason != "BLOCKED_REASON_UNSPECIFIED":
        return f"prompt {reason.lower()}"
    finish = finish_reason(response)
    if finish in BLOCK_FINISH_REASONS or (include_recitation and finish == RECITATION_FINISH_REASON):
        return f"finish reason {finish.lower()}"
    return None


def is_recitation(response: Any) -> bool:
    """Whether generation stopped because the output recited source/training text."""
    return finish_reason(response) == RECITATION_FINISH_REASON


def check_blocked(response: Any, settings: Settings, what: str) -> None:
    """Raise ``ContentBlocked`` when the prompt or the first candidate was blocked (media outputs:
    a recitation stop is treated as blocked too)."""
    reason = block_reason(response, include_recitation=True)
    if reason:
        raise make_error(ContentBlocked, "gemini", f"{what} blocked by safety filters", settings=settings,
                         detail=reason)


def is_truncated(response: Any) -> bool:
    """Whether the first candidate stopped at ``max_output_tokens``."""
    return finish_reason(response) == "MAX_TOKENS"


def response_parts(response: Any) -> list[Any]:
    candidates = safe_attr(response, "candidates") or []
    if not candidates:
        return []
    return list(safe_attr(candidates[0], "content", "parts") or [])


def response_text(response: Any) -> str:
    """Concatenated non-thought text parts of the first candidate."""
    texts = [p.text for p in response_parts(response) if getattr(p, "text", None) and not getattr(p, "thought", False)]
    return "".join(texts)


def usage_from_response(response: Any, *, model: str, operation: str, **fields: Any) -> Usage:
    """``Usage`` from ``usage_metadata`` (thinking tokens are billed as output)."""
    meta = safe_attr(response, "usage_metadata")
    prompt_tokens = int(safe_attr(meta, "prompt_token_count", default=0) or 0)
    out_tokens = int(safe_attr(meta, "candidates_token_count", default=0) or 0)
    thoughts = int(safe_attr(meta, "thoughts_token_count", default=0) or 0)
    cached = int(safe_attr(meta, "cached_content_token_count", default=0) or 0)
    extra_meta = fields.pop("meta", {})
    return Usage(
        provider="gemini",
        model=model,
        operation=operation,
        input_tokens=prompt_tokens,
        output_tokens=out_tokens + thoughts,
        meta={"thoughts_tokens": thoughts, "cached_tokens": cached, **extra_meta},
        **fields,
    )
