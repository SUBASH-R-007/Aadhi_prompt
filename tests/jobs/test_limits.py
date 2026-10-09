"""Process-wide limits across threads and event loops; cancellation safety."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from aadhi.jobs import limits
from aadhi.jobs.limits import ProcessLimit, acquire, acquire_sync, get_limit, limit_size, limit_stats


def test_sizes_from_settings(app_env):
    assert limit_size("llm", app_env) == app_env.llm_max_parallel
    assert limit_size("tts", app_env) == app_env.tts_max_parallel
    assert limit_size("manim", app_env) == app_env.manim_max_concurrent
    assert limit_size("render", app_env) == app_env.render_concurrency
    assert (limit_size("image", app_env), limit_size("video", app_env)) == (4, 2)
    with pytest.raises(KeyError):
        limit_size("gpu", app_env)
    assert get_limit("llm") is get_limit("llm")
    zero = app_env.model_copy(update={"manim_max_concurrent": 0})
    assert limit_size("manim", zero) == 1


def test_limit_across_three_threads_with_separate_loops(app_env):
    lock = threading.Lock()
    state = {"current": 0, "peak": 0, "done": 0}
    loops: set[int] = set()

    async def task() -> None:
        async with acquire("video"):  # size 2
            with lock:
                state["current"] += 1
                state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.03)
            with lock:
                state["current"] -= 1
                state["done"] += 1

    def thread_main() -> None:
        async def main() -> None:
            loops.add(id(asyncio.get_running_loop()))
            await asyncio.gather(*(task() for _ in range(4)))

        asyncio.run(main())

    threads = [threading.Thread(target=thread_main) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert state["done"] == 12
    assert state["peak"] == 2
    assert len(loops) == 3
    assert get_limit("video").in_use == 0


def test_waiting_does_not_block_the_event_loop(app_env):
    lim = get_limit("video")

    async def main() -> int:
        ticks = 0
        async with acquire("video"), acquire("video"):
            waiter = asyncio.ensure_future(_hold(lim))
            for _ in range(5):  # the loop keeps running while the waiter is parked
                await asyncio.sleep(0.01)
                ticks += 1
            assert not waiter.done()
        await waiter
        return ticks

    assert asyncio.run(main()) == 5
    assert lim.in_use == 0


async def _hold(lim: ProcessLimit) -> None:
    await lim.acquire_async()
    lim.release()


def test_cancelled_waiter_does_not_leak(app_env):
    lim = ProcessLimit("t", 1)

    async def main() -> None:
        await lim.acquire_async()
        waiter = asyncio.ensure_future(lim.acquire_async())
        await asyncio.sleep(0.01)
        assert lim.waiting == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert lim.waiting == 0
        lim.release()
        assert lim.in_use == 0
        await asyncio.wait_for(lim.acquire_async(), 1)
        lim.release()

    asyncio.run(main())


def test_cancel_after_grant_hands_permit_on(app_env):
    lim = ProcessLimit("t", 1)

    async def main() -> None:
        await lim.acquire_async()
        waiter = asyncio.ensure_future(lim.acquire_async())
        await asyncio.sleep(0.01)
        lim.release()  # grants to the waiter (resolution scheduled on the loop) ...
        waiter.cancel()  # ... which is cancelled before it ever runs
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert lim.in_use == 0 and lim.waiting == 0

    asyncio.run(main())


def test_grant_crosses_threads(app_env):
    lim = ProcessLimit("t", 1)
    assert lim.acquire_blocking()
    got = threading.Event()

    def other_loop() -> None:
        async def main() -> None:
            await lim.acquire_async()
            got.set()
            lim.release()

        asyncio.run(main())

    t = threading.Thread(target=other_loop)
    t.start()
    time.sleep(0.05)
    assert not got.is_set()
    lim.release()  # from this (non-loop) thread
    t.join(5)
    assert got.is_set() and lim.in_use == 0


def test_blocking_acquire_timeout_and_fifo(app_env):
    lim = ProcessLimit("t", 1)
    assert lim.acquire_blocking(0)
    assert lim.acquire_blocking(0.05) is False
    assert lim.waiting == 0
    order: list[int] = []

    def waiter(n: int) -> None:
        assert lim.acquire_blocking(5)
        order.append(n)
        lim.release()

    threads = []
    for n in range(3):
        t = threading.Thread(target=waiter, args=(n,))
        t.start()
        threads.append(t)
        time.sleep(0.03)
    lim.release()
    for t in threads:
        t.join(5)
    assert order == [0, 1, 2]


def test_acquire_sync_context_manager(app_env):
    with acquire_sync("render"):
        assert get_limit("render").in_use == 1
    assert get_limit("render").in_use == 0
    lim = get_limit("video")
    lim.acquire_blocking()
    lim.acquire_blocking()
    with pytest.raises(TimeoutError), acquire_sync("video", timeout=0.05):
        pass  # pragma: no cover
    lim.release()
    lim.release()


def test_over_release_is_an_error(app_env):
    lim = ProcessLimit("t", 2)
    with pytest.raises(ValueError):
        lim.release()
    with pytest.raises(ValueError):
        ProcessLimit("bad", 0)


def test_configure_reset_and_stats(app_env):
    limits.configure_limits(app_env.model_copy(update={"llm_max_parallel": 9}))
    assert get_limit("llm").size == 9
    stats = limit_stats()
    assert set(stats) == set(limits.LIMIT_NAMES)
    assert stats["llm"] == {"size": 9, "in_use": 0, "waiting": 0}
    limits.reset_limits()
    assert limit_stats() == {}
    assert get_limit("llm").size == app_env.llm_max_parallel


def test_exception_inside_block_releases(app_env):
    async def main() -> None:
        with pytest.raises(RuntimeError):
            async with acquire("image"):
                raise RuntimeError("x")

    asyncio.run(main())
    assert get_limit("image").in_use == 0
