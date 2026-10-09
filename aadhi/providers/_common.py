"""Small helpers shared by every provider adapter (errors, usage, per-loop caches, aspect ratios)."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import threading
from collections.abc import Callable
from typing import Any, Generic, TypeVar
from urllib.parse import urlsplit

from ..config import Settings
from .base import ProviderError, ProviderUnavailable, RateLimited, Usage, UsageSink

E = TypeVar("E", bound=ProviderError)
C = TypeVar("C")

MAX_ERROR_DETAIL = 300
MIN_PARTIAL_SECRET = 6  # shortest secret prefix treated as leaked


def sanitize_host(url: str) -> str:
    """Return only the host of ``url`` (no scheme, path, query or credentials)."""
    try:
        host = urlsplit(url if "//" in url else f"//{url}").hostname or ""
    except ValueError:
        return ""
    return host


def _redact(text: str, settings: Settings | None) -> str:
    return settings.redact(text) if settings is not None else text


def _cut_partial_secret(text: str, settings: Settings | None) -> str:
    """Remove a trailing *prefix* of a configured secret (left behind when an upstream layer cut the
    provider text in the middle of a key, so the full value no longer matches ``Settings.redact``)."""
    if settings is None or not text:
        return text
    for secret in settings.secret_values():
        for k in range(min(len(secret) - 1, len(text)), MIN_PARTIAL_SECRET - 1, -1):
            if text.endswith(secret[:k]):
                return text[: len(text) - k] + "[REDACTED]"
    return text


def make_error(
    cls: type[E],
    provider: str,
    message: str,
    *,
    settings: Settings | None,
    status: int | None = None,
    host: str = "",
    detail: str = "",
) -> E:
    """Build a redacted provider error: ``"<provider>: <message> (HTTP <status>, <host>): <detail>"``.

    ``detail`` is provider-supplied text (it may echo request data). It is redacted with
    ``Settings.redact`` *before* it is truncated to ``MAX_ERROR_DETAIL`` characters (a secret cut in
    half would no longer match), a dangling secret prefix is removed, and the final text is
    redacted again. Nothing else from the request is included.
    """
    parts = [f"{provider}: {message}"]
    meta = ", ".join(x for x in (f"HTTP {status}" if status else "", host) if x)
    if meta:
        parts.append(f" ({meta})")
    if detail:
        detail = _redact(" ".join(str(detail).split()), settings)
        detail = _cut_partial_secret(detail, settings)
        if len(detail) > MAX_ERROR_DETAIL:
            detail = detail[:MAX_ERROR_DETAIL] + "..."
        parts.append(f": {detail}")
    text = _redact("".join(parts), settings)
    return cls(text, status=status, provider=provider)


def error_for_status(status: int | None) -> type[ProviderError]:
    """Map an HTTP status to the error class raised after retries are exhausted.

    For adapters without per-user (BYOK) keys only (Pollinations, GIPHY, ElevenLabs): 401/402/403 is an
    account, payment or key problem of the server's provider (``ProviderUnavailable``, a
    ``ProviderError`` subclass, so a personal-key rejection would still be detected by its status).
    """
    if status == 429:
        return RateLimited
    if status in (401, 402, 403):
        return ProviderUnavailable
    return ProviderError


async def emit_usage(on_usage: UsageSink | None, usage: Usage) -> None:
    """Report ``usage`` to the sink. Exceptions (e.g. ``BudgetExceeded``) propagate."""
    if on_usage is None:
        return
    result = on_usage(usage)
    if inspect.isawaitable(result):
        await result


def fingerprint(secret: str) -> str:
    """Stable non-reversible id for a secret (used as a cache key instead of the secret)."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


class LoopLocal(Generic[C]):
    """Caches one object per running event loop (SDK HTTP clients are loop-bound).

    Worker threads each run their own loop, and the job worker runs every job in a fresh
    ``asyncio.run()``; sharing an ``httpx.AsyncClient`` across loops breaks its connection pool, so
    clients are created lazily per loop. The cached client's pool references its loop, so entries
    would never become unreachable on their own: every lookup first drops the entries whose loop
    is closed (a client on a closed loop cannot be awaited shut, but releasing the last reference
    lets the garbage collector close its sockets). This is the approach google-genai uses too.
    """

    def __init__(self, factory: Callable[[], C]) -> None:
        self._factory = factory
        self._items: dict[asyncio.AbstractEventLoop, C] = {}
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def _discard_closed(self) -> None:
        """Drop entries of closed loops (caller holds ``_lock``)."""
        for loop in [lp for lp in self._items if lp.is_closed()]:
            del self._items[loop]

    def get(self) -> C:
        """Object for the running loop, created synchronously on first use."""
        loop = asyncio.get_running_loop()
        with self._lock:
            self._discard_closed()
            item = self._items.get(loop)
            if item is None:
                item = self._factory()
                self._items[loop] = item
            return item

    async def aget(self) -> C:
        """Like ``get`` but builds the object in a worker thread (client construction loads TLS
        certificates from disk, which must not block the event loop)."""
        loop = asyncio.get_running_loop()
        with self._lock:
            self._discard_closed()
            item = self._items.get(loop)
        if item is not None:
            return item
        created = await asyncio.to_thread(self._factory)
        with self._lock:
            existing = self._items.get(loop)
            if existing is None:
                self._items[loop] = created
                return created
        return existing


ASPECT_DIMS: dict[str, tuple[int, int]] = {
    "16:9": (1280, 720),
    "9:16": (720, 1280),
    "1:1": (1024, 1024),
    "4:3": (1024, 768),
    "3:4": (768, 1024),
    "3:2": (1200, 800),
    "2:3": (800, 1200),
    "21:9": (1680, 720),
}


def aspect_dims(aspect: str) -> tuple[int, int]:
    """Pixel size used for an aspect ratio string (defaults to 16:9 at 1280x720)."""
    return ASPECT_DIMS.get((aspect or "").strip(), ASPECT_DIMS["16:9"])


def stable_seed(text: str, modulo: int = 2**31 - 1) -> int:
    """Deterministic integer seed derived from ``text``."""
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16) % modulo


def style_prompt(prompt: str, settings: Settings) -> str:
    """Append the configured house style (``IMAGE_STYLE_SUFFIX``) unless already present."""
    prompt = " ".join((prompt or "").split())
    suffix = (settings.image_style_suffix or "").strip()
    if suffix and suffix.lower() not in prompt.lower():
        prompt = f"{prompt}. {suffix}" if prompt else suffix
    return prompt


def describe_exception(exc: BaseException) -> str:
    """Short type + message text for an exception (to be redacted by the caller)."""
    msg = str(exc).strip()
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def safe_attr(obj: Any, *names: str, default: Any = None) -> Any:
    """``getattr`` chain that tolerates ``None`` links and missing attributes."""
    cur = obj
    for name in names:
        if cur is None:
            return default
        cur = cur.get(name) if isinstance(cur, dict) else getattr(cur, name, None)
    return default if cur is None else cur
