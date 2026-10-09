"""Media provider failure categories and a process-wide health map (cooldowns).

``classify_failure`` names what went wrong with a media provider call; the category decides whether
the media chain (``aadhi.pipeline.assets``) may try the next provider and how long the failing one
is moved behind the others:

* ``blocked`` (safety refusal): never sent to another provider, no cooldown.
* ``unavailable`` (payment / account / credential refusal of the server's provider, HTTP 401/402/403):
  next provider, ``MEDIA_PROVIDER_AUTH_COOLDOWN_SECONDS``.
* ``rate_limited``, ``transient`` (5xx, timeouts, network), ``invalid_output``: next provider,
  ``MEDIA_PROVIDER_COOLDOWN_SECONDS``.
* ``not_configured``, ``lost``, ``failed`` (anything else, e.g. a request this provider refused): next
  provider, no cooldown (another prompt may work).

A refused *personal* API key is decided by the caller (``aadhi.credentials.personal_key_rejection``)
before this module is consulted: it fails the job and is never noted here. Only failures of the provider
call itself are classified here at all: a storage, claim or database failure around it never moves on to
the next provider (``aadhi.pipeline.assets``).

Cooldowns only reorder: a cooling provider is still tried when nothing else can serve the request.
They are kept per process (each worker learns on its own) and per key scope (``"server"`` or
``"user:<id>"``), so one user's bad personal key never demotes the server's provider for others. The
asset builder also logs each cooldown in a job event, which the admin status panel reads when the
workers run in other processes (``aadhi.api.routers.meta.worker_cooldowns``).
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from ..config import Settings
from .base import (
    ContentBlocked,
    InvalidMediaOutput,
    OperationLost,
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
)

T = TypeVar("T")

BLOCKED = "blocked"
UNAVAILABLE = "unavailable"
RATE_LIMITED = "rate_limited"
TRANSIENT = "transient"
INVALID_OUTPUT = "invalid_output"
NOT_CONFIGURED = "not_configured"
LOST = "lost"
FAILED = "failed"

_SHORT_COOLDOWN = frozenset({RATE_LIMITED, TRANSIENT, INVALID_OUTPUT})
_LONG_COOLDOWN = frozenset({UNAVAILABLE})


def classify_failure(exc: BaseException) -> str:
    """Category of a media provider failure (see the module docstring)."""
    if isinstance(exc, ContentBlocked):
        return BLOCKED
    if isinstance(exc, ProviderNotConfigured):
        return NOT_CONFIGURED
    if isinstance(exc, ProviderUnavailable):
        return UNAVAILABLE
    if isinstance(exc, RateLimited):
        return RATE_LIMITED
    if isinstance(exc, InvalidMediaOutput):
        return INVALID_OUTPUT
    if isinstance(exc, OperationLost):
        return LOST
    if isinstance(exc, ProviderError):
        status = exc.status
        if status in (401, 402, 403):
            return UNAVAILABLE
        if status == 429:
            return RATE_LIMITED
        if status is not None and (status in (408, 409, 425) or status >= 500):
            return TRANSIENT
        if "timed out" in str(exc).lower():
            return TRANSIENT
        return FAILED
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return TRANSIENT
    return FAILED


def fallback_allowed(category: str) -> bool:
    """Whether another provider may be tried after a failure of ``category`` (never after a safety refusal)."""
    return category != BLOCKED


def cooldown_seconds(category: str, settings: Settings) -> float:
    """How long a provider is moved behind the others after a failure of ``category``."""
    if category in _LONG_COOLDOWN:
        return float(settings.media_provider_auth_cooldown_seconds)
    if category in _SHORT_COOLDOWN:
        return float(settings.media_provider_cooldown_seconds)
    return 0.0


@dataclass(frozen=True)
class Cooling:
    seconds_left: float
    category: str


class ProviderHealth:
    """Thread-safe map ``(provider, scope) -> cooldown`` (process-wide; see the module docstring)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._until: dict[tuple[str, str], tuple[float, str]] = {}
        self._lock = threading.Lock()

    def note_failure(self, name: str, scope: str, exc: BaseException, settings: Settings) -> float:
        """Record a failure; returns the cooldown applied (0 = none)."""
        category = classify_failure(exc)
        seconds = cooldown_seconds(category, settings)
        if seconds > 0:
            with self._lock:
                until = self._clock() + seconds
                current = self._until.get((name, scope))
                if current is None or current[0] < until:
                    self._until[(name, scope)] = (until, category)
        return seconds

    def note_success(self, name: str, scope: str) -> None:
        with self._lock:
            self._until.pop((name, scope), None)

    def cooling(self, name: str, scope: str) -> Cooling | None:
        """The active cooldown of ``name`` in ``scope``, if any."""
        with self._lock:
            entry = self._until.get((name, scope))
            if entry is None:
                return None
            left = entry[0] - self._clock()
            if left <= 0:
                del self._until[(name, scope)]
                return None
            return Cooling(seconds_left=left, category=entry[1])

    def order(self, items: Sequence[T], scope_of: Callable[[T], tuple[str, str]]) -> list[T]:
        """``items`` with cooling providers moved last (stable; nothing is ever dropped).
        ``scope_of(item)`` returns ``(provider name, key scope)``."""
        cooling = [self.cooling(*scope_of(it)) is not None for it in items]
        return [it for it, c in zip(items, cooling, strict=True) if not c] + [
            it for it, c in zip(items, cooling, strict=True) if c
        ]

    def snapshot(self, names: Iterable[str], scope: str = "server") -> dict[str, Cooling]:
        out: dict[str, Cooling] = {}
        for name in names:
            cooling = self.cooling(name, scope)
            if cooling is not None:
                out[name] = cooling
        return out

    def reset(self) -> None:
        with self._lock:
            self._until.clear()


_HEALTH = ProviderHealth()


def provider_health() -> ProviderHealth:
    """The process-wide health map."""
    return _HEALTH


def reset_provider_health() -> None:
    """Forget every cooldown (tests / settings reload)."""
    _HEALTH.reset()
