"""Async helpers (deadlines, cancel polling, fail-fast groups) and state-PNG hygiene."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from PIL import Image

from aadhi.compose.aio import DeadlineExceeded, await_polling, gather_or_cancel, reap
from aadhi.compose.frames import ensure_rgba_png, normalize_pngs, transparent_png
from aadhi.jobs.base import JobCancelled

# --- aio --------------------------------------------------------------------------------------------


def test_await_polling_returns_result_and_raises_errors() -> None:
    async def ok() -> int:
        await asyncio.sleep(0.01)
        return 7

    async def bad() -> None:
        raise ValueError("x")

    assert asyncio.run(await_polling(ok(), timeout=5)) == 7
    with pytest.raises(ValueError):
        asyncio.run(await_polling(bad(), timeout=5, check_cancelled=lambda: None))


def test_await_polling_deadline_cancels_the_operation() -> None:
    cancelled: list[bool] = []

    async def forever() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    t0 = time.monotonic()
    with pytest.raises(DeadlineExceeded):
        asyncio.run(await_polling(forever(), timeout=0.3))
    assert time.monotonic() - t0 < 3 and cancelled == [True]
    assert issubclass(DeadlineExceeded, TimeoutError)


def test_await_polling_checks_cancellation_while_waiting() -> None:
    calls: list[float] = []
    t0 = time.monotonic()

    def check() -> None:
        calls.append(time.monotonic() - t0)
        if len(calls) >= 3:
            raise JobCancelled()

    async def forever() -> None:
        await asyncio.Event().wait()

    with pytest.raises(JobCancelled):
        asyncio.run(await_polling(forever(), timeout=60, check_cancelled=check, poll=0.1))
    assert len(calls) == 3 and time.monotonic() - t0 < 5


def test_gather_or_cancel_fails_fast_and_cancels_siblings() -> None:
    state: dict[str, str] = {}

    async def slow() -> None:
        try:
            await asyncio.sleep(30)
            state["slow"] = "finished"
        except asyncio.CancelledError:
            state["slow"] = "cancelled"
            raise

    async def failing() -> None:
        await asyncio.sleep(0.05)
        raise RuntimeError("first failure")

    async def ok(v: int) -> int:
        return v

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="first failure"):
        asyncio.run(gather_or_cancel([slow(), failing()]))
    assert state["slow"] == "cancelled" and time.monotonic() - t0 < 5
    assert asyncio.run(gather_or_cancel([ok(1), ok(2)])) == [1, 2]
    assert asyncio.run(gather_or_cancel([])) == []


def test_reap_is_quiet_for_failed_futures() -> None:
    async def main() -> None:
        async def boom() -> None:
            raise RuntimeError("x")

        task = asyncio.ensure_future(boom())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await reap(task)  # finished with an exception: retrieved, not raised
        assert task.done()

    asyncio.run(main())


# --- frames ----------------------------------------------------------------------------------------


def test_transparent_png(tmp_path: Path) -> None:
    path = transparent_png(tmp_path / "x" / "blank.png", (64, 36))
    with Image.open(path) as im:
        assert im.mode == "RGBA" and im.size == (64, 36) and im.getextrema()[3] == (0, 0)
    assert ensure_rgba_png(path, (64, 36)) is False  # already compliant: untouched


def test_ensure_rgba_png_normalises_mode_size_and_depth(tmp_path: Path) -> None:
    rgb = tmp_path / "rgb.png"
    Image.new("RGB", (32, 18), (10, 20, 30)).save(rgb)
    small = tmp_path / "small.png"
    Image.new("RGBA", (16, 9), (255, 0, 0, 128)).save(small)
    gray = tmp_path / "gray.png"
    Image.new("LA", (64, 36), (128, 255)).save(gray)
    deep = tmp_path / "deep.png"
    Image.new("I;16", (64, 36), 30000).save(deep)
    assert normalize_pngs([rgb, small, gray, deep, rgb], (64, 36)) == 4
    for p in (rgb, small, gray, deep):
        with Image.open(p) as im:
            assert im.mode == "RGBA" and im.size == (64, 36)
            assert im.tile[0][3] == "RGBA"  # 8-bit RGBA on disk
    assert not list(tmp_path.glob("*.tmp"))
    assert normalize_pngs([rgb, small], (64, 36)) == 0
