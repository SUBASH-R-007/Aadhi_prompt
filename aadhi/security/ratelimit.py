"""In-memory sliding-window rate limiting (per process, thread-safe).

Each ``(bucket, key)`` keeps the timestamps of its most recent hits (bounded by the largest limit);
a request is refused when the N-th most recent hit is younger than the window of an N-per-window
limit. Refusals do not count as hits. Tracked keys are LRU-bounded so random keys cannot exhaust
memory. Limits are per process: with several API replicas the effective limit scales with the
replica count (DB budgets remain the hard cost cap).

Key kinds for the ``rate_limit`` dependency:

* ``"ip"``      -- client IP (``TRUSTED_PROXIES`` aware);
* ``"user"``    -- the authenticated user id; anonymous requests fall back to the client IP;
* ``"user_ip"`` -- user id *and* client IP combined (``u:<id>@<ip>``); anonymous -> client IP.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from typing import Literal

from fastapi import Depends, Request

from ..auth.errors import AppHTTPException
from ..config import Settings, get_settings
from ..models import User
from .client_ip import client_ip

__all__ = ["RateLimited", "SlidingWindowLimiter", "check_rate_limit", "rate_limit", "reset_rate_limits"]

KeyKind = Literal["user", "ip", "user_ip"]
MAX_TRACKED_KEYS = 100_000


class RateLimited(AppHTTPException):
    """429 ``rate_limited`` with ``Retry-After`` (seconds)."""

    def __init__(self, retry_after: float) -> None:
        seconds = max(1, math.ceil(retry_after))
        super().__init__(
            429,
            f"Too many requests; try again in {seconds} s",
            code="rate_limited",
            headers={"Retry-After": str(seconds)},
        )
        self.retry_after = seconds


class SlidingWindowLimiter:
    """Thread-safe sliding-window log limiter."""

    def __init__(self, max_keys: int = MAX_TRACKED_KEYS, clock: Callable[[], float] = time.monotonic) -> None:
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._clock = clock

    def hit(self, key: str, limits: list[tuple[int, float]]) -> float | None:
        """Record a hit for ``key`` unless a ``(count, window_seconds)`` limit is exhausted.

        Returns None when allowed, otherwise the seconds until the next hit would be allowed.
        """
        limits = [(int(c), float(w)) for c, w in limits if c is not None and int(c) > 0]
        if not limits:
            return None
        depth = max(c for c, _ in limits)
        now = self._clock()
        with self._lock:
            dq = self._hits.get(key)
            if dq is None or dq.maxlen is None or dq.maxlen < depth:
                dq = deque(dq or (), maxlen=depth)
                self._hits[key] = dq
            self._hits.move_to_end(key)
            wait = 0.0
            for count, window in limits:
                if len(dq) >= count:
                    age = now - dq[-count]
                    if age < window:
                        wait = max(wait, window - age)
            if wait > 0:
                return wait
            dq.append(now)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return None

    def reset(self) -> None:
        """Forget every recorded hit."""
        with self._lock:
            self._hits.clear()


_LIMITER = SlidingWindowLimiter()


def reset_rate_limits() -> None:
    """Clear all rate-limit state (tests, admin tooling)."""
    _LIMITER.reset()


def _limits(per_minute: int | None, per_hour: int | None) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    if per_minute:
        out.append((int(per_minute), 60.0))
    if per_hour:
        out.append((int(per_hour), 3600.0))
    return out


def check_rate_limit(bucket: str, key: str, *, per_minute: int | None = None, per_hour: int | None = None) -> None:
    """Imperative form (e.g. login: ``check_rate_limit("login", f"{username}|{ip}", per_minute=10)``).

    Raises ``RateLimited`` (429 ``rate_limited`` + ``Retry-After``). No limits -> no-op.
    """
    wait = _LIMITER.hit(f"{bucket}\x00{key}", _limits(per_minute, per_hour))
    if wait is not None:
        raise RateLimited(wait)


def _request_settings(request: Request) -> Settings:
    """Settings of the app serving ``request`` (``TRUSTED_PROXIES`` and default limits), else global."""
    app = request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


def _optional_user_dep() -> Callable[..., User | None]:
    from ..auth.deps import get_optional_user  # local import: auth.deps pulls in the DB layer

    return get_optional_user


def rate_limit(
    bucket: str,
    *,
    per_minute: int | None = None,
    per_hour: int | None = None,
    key: KeyKind = "user_ip",
) -> Callable[..., None]:
    """FastAPI dependency factory: ``Depends(rate_limit("generate", per_hour=20, key="user"))``.

    Without explicit limits ``API_RATE_LIMIT_PER_MINUTE`` applies. Settings (that default and
    ``TRUSTED_PROXIES``) come from the app serving the request. Raises 429 ``rate_limited``.
    """
    if key not in ("user", "ip", "user_ip"):
        raise ValueError(f"invalid rate limit key kind {key!r}")

    def _apply(request: Request, user: User | None) -> None:
        settings = _request_settings(request)
        pm, ph = per_minute, per_hour
        if pm is None and ph is None:
            pm = settings.api_rate_limit_per_minute
        ip = client_ip(request, settings)
        if key == "ip" or user is None:
            ident = f"ip:{ip}"
        elif key == "user":
            ident = f"u:{user.id}"
        else:
            ident = f"u:{user.id}@{ip}"
        check_rate_limit(bucket, ident, per_minute=pm, per_hour=ph)

    if key == "ip":

        def _by_ip(request: Request) -> None:
            _apply(request, None)

        _by_ip.__name__ = f"rate_limit_{bucket}"
        return _by_ip

    # Default-value form: the Depends object is bound at definition time (string annotations of a
    # nested function cannot see enclosing locals).
    def _by_user(request: Request, user: User | None = Depends(_optional_user_dep())) -> None:  # noqa: B008
        _apply(request, user)

    _by_user.__name__ = f"rate_limit_{bucket}"
    return _by_user
