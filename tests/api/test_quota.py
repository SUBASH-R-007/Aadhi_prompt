"""In-process daily quotas (``aadhi.api.quota``)."""

from __future__ import annotations

import datetime as dt
import threading

from aadhi.api.quota import DailyQuota, seconds_until_utc_midnight


class Clock:
    def __init__(self) -> None:
        self.day = dt.date(2026, 10, 1)

    def __call__(self) -> dt.date:
        return self.day


def test_consume_is_all_or_nothing_across_keys():
    q = DailyQuota()
    assert q.consume([("ip", 10), ("viewer", 5)], 4) is None
    assert q.used("ip") == 4 and q.used("viewer") == 4
    assert q.consume([("ip", 10), ("viewer", 5)], 2) == 1  # viewer would go to 6 > 5
    assert q.used("ip") == 4 and q.used("viewer") == 4  # nothing charged
    assert q.consume([("ip", 10), ("viewer", 5)], 1) is None
    assert q.consume([("ip", 5), ("viewer", 50)], 1) == 0  # first exhausted demand is reported
    assert q.used("ip") == 5 and q.used("viewer") == 5


def test_zero_cap_is_unlimited_and_negative_amounts_are_ignored():
    q = DailyQuota()
    assert q.consume([("k", 0)], 10**9) is None
    assert q.consume([("j", 3)], -5) is None and q.used("j") == 0


def test_counters_restart_every_utc_day():
    clock = Clock()
    q = DailyQuota(today=clock)
    assert q.consume([("ip", 3)], 3) is None
    assert q.consume([("ip", 3)], 1) == 0
    clock.day += dt.timedelta(days=1)
    assert q.used("ip") == 0
    assert q.consume([("ip", 3)], 3) is None


def test_keys_are_lru_bounded_and_exhausted_keys_stay_hot():
    q = DailyQuota(max_keys=3)
    assert q.consume([("attacker", 2)], 2) is None
    for i in range(2):
        q.consume([(f"v{i}", 10)], 1)
    assert q.consume([("attacker", 2)], 1) == 0  # refused, but touched: now most recently used
    q.consume([("v9", 10)], 1)  # evicts the least recently used key (v0), not the attacker
    assert len(q) == 3
    assert q.used("attacker") == 2 and q.used("v0") == 0
    q.reset()
    assert len(q) == 0


def test_concurrent_consumers_never_exceed_the_cap():
    q = DailyQuota()
    cap = 1_000
    granted: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        for _ in range(200):
            if q.consume([("shared", cap)], 3) is None:
                with lock:
                    granted.append(3)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(granted) == q.used("shared") <= cap
    assert cap - q.used("shared") < 3


def test_seconds_until_utc_midnight():
    noon = dt.datetime(2026, 10, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert seconds_until_utc_midnight(noon) == 12 * 3600
    last = dt.datetime(2026, 10, 1, 23, 59, 59, 900000, tzinfo=dt.timezone.utc)
    assert seconds_until_utc_midnight(last) == 1
    assert 1 <= seconds_until_utc_midnight() <= 86400
