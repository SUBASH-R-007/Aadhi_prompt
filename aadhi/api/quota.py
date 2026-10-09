"""In-process daily event quotas (reset at UTC midnight).

Used by analytics ingest so that no single client can use up a share link's daily capacity: every
batch is charged against several keys at once (e.g. the client IP and the viewer id) and is refused
when *any* of them would go over its cap. Charging is all-or-nothing under one lock.

Quotas are per process (like ``aadhi.security.ratelimit``): with several API replicas the effective
cap scales with the replica count; the share-wide database count stays the hard storage bound.
Tracked keys are LRU-bounded; a key that is charged on every request stays most recently used, so
key rotation by a client can only evict *other* (idle) keys, which merely resets their counters.
"""

from __future__ import annotations

import datetime as dt
import threading
from collections import OrderedDict
from collections.abc import Callable, Sequence

__all__ = ["DailyQuota", "seconds_until_utc_midnight", "utc_today"]

MAX_TRACKED_KEYS = 100_000


def utc_today() -> dt.date:
    """The current UTC date."""
    return dt.datetime.now(dt.timezone.utc).date()


def seconds_until_utc_midnight(now: dt.datetime | None = None) -> int:
    """Whole seconds (>= 1) until the next UTC midnight, for ``Retry-After``."""
    current = now or dt.datetime.now(dt.timezone.utc)
    tomorrow = dt.datetime.combine(current.date() + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc)
    return max(1, int((tomorrow - current).total_seconds()))


class DailyQuota:
    """Thread-safe per-key counters that start again at zero every UTC day."""

    def __init__(self, max_keys: int = MAX_TRACKED_KEYS, today: Callable[[], dt.date] = utc_today) -> None:
        self._counts: OrderedDict[str, tuple[dt.date, int]] = OrderedDict()
        self._lock = threading.Lock()
        self._max_keys = max(1, int(max_keys))
        self._today = today

    def _current(self, key: str, day: dt.date) -> int:
        entry = self._counts.get(key)
        return entry[1] if entry is not None and entry[0] == day else 0

    def used(self, key: str) -> int:
        """Amount charged to ``key`` today."""
        with self._lock:
            return self._current(key, self._today())

    def consume(self, demands: Sequence[tuple[str, int]], amount: int) -> int | None:
        """Charge ``amount`` to every ``(key, cap)`` in ``demands`` unless one would exceed its cap.

        Returns None when charged, otherwise the index of the first exhausted demand (nothing is
        charged then). A cap <= 0 means unlimited for that key.
        """
        amount = max(0, int(amount))
        day = self._today()
        with self._lock:
            for index, (key, cap) in enumerate(demands):
                if cap > 0 and self._current(key, day) + amount > cap:
                    # Keep the exhausted key hot so LRU eviction cannot silently reset it.
                    if key in self._counts:
                        self._counts.move_to_end(key)
                    return index
            for key, _cap in demands:
                self._counts[key] = (day, self._current(key, day) + amount)
                self._counts.move_to_end(key)
            while len(self._counts) > self._max_keys:
                self._counts.popitem(last=False)
            return None

    def reset(self) -> None:
        """Forget every counter."""
        with self._lock:
            self._counts.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._counts)
