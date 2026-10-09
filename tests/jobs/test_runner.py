"""run_job_loop: a fresh event loop per job that never waits for abandoned thread work."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from aadhi.jobs.runner import default_thread_count, run_job_loop


def test_returns_result_and_cleans_up_the_thread_loop():
    async def main() -> tuple[int, str]:
        value = await asyncio.to_thread(lambda: 21 * 2)
        return value, threading.current_thread().name

    assert run_job_loop(main(), name="t") == (42, threading.current_thread().name)
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
    assert 1 <= default_thread_count() <= 32


def test_exceptions_propagate():
    async def main() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_job_loop(main())


def test_thread_work_is_abandoned_not_awaited():
    """asyncio.run would block here until time.sleep(3) returned."""
    finished = threading.Event()

    def slow() -> None:
        time.sleep(3)
        finished.set()

    async def main() -> str:
        work = asyncio.ensure_future(asyncio.to_thread(slow))
        await asyncio.sleep(0.05)
        work.cancel()
        try:
            await work
        except asyncio.CancelledError:
            return "cancelled"
        return "finished"  # pragma: no cover

    t0 = time.monotonic()
    assert run_job_loop(main(), name="t-abandon") == "cancelled"
    assert time.monotonic() - t0 < 1.5
    assert not finished.is_set()  # still running in the background (orphaned, not killed)


def test_leftover_tasks_are_cancelled_and_stubborn_ones_abandoned():
    cancelled = threading.Event()

    async def polite() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def stubborn() -> None:
        while True:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                continue

    async def main() -> str:
        asyncio.ensure_future(polite())
        asyncio.ensure_future(stubborn())
        await asyncio.sleep(0)
        return "done"

    t0 = time.monotonic()
    assert run_job_loop(main(), cleanup_timeout=0.2) == "done"
    assert cancelled.is_set() and time.monotonic() - t0 < 2.0


def test_base_exceptions_from_tasks_propagate_and_loop_still_closes():
    async def main() -> None:
        raise SystemExit(3)

    with pytest.raises(SystemExit):
        run_job_loop(main())
    assert run_job_loop(asyncio.sleep(0, result="again")) == "again"  # a new loop works afterwards
