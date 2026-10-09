"""Fake aadhi.security.ratelimit (in-memory sliding windows)."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from .errors import AppHTTPException

_LOCK = threading.Lock()
_HITS: dict[tuple[str, str, int], deque[float]] = defaultdict(deque)


def check_rate_limit(bucket: str, key: str, *, per_minute: int | None = None, per_hour: int | None = None) -> None:
    now = time.monotonic()
    with _LOCK:
        for limit, window in ((per_minute, 60), (per_hour, 3600)):
            if not limit:
                continue
            hits = _HITS[(bucket, key, window)]
            while hits and hits[0] <= now - window:
                hits.popleft()
            if len(hits) >= limit:
                retry = max(1, int(window - (now - hits[0])))
                raise AppHTTPException(429, "rate_limited", "Too many requests.", headers={"Retry-After": str(retry)})
        for limit, window in ((per_minute, 60), (per_hour, 3600)):
            if limit:
                _HITS[(bucket, key, window)].append(now)


def rate_limit(bucket: str, *, per_minute: int | None = None, per_hour: int | None = None, key: str = "user_ip"):
    def dependency(request: Request) -> None:
        ident = request.client.host if request.client else "unknown"
        check_rate_limit(bucket, ident, per_minute=per_minute, per_hour=per_hour)

    return dependency


def reset_rate_limits() -> None:
    with _LOCK:
        _HITS.clear()
