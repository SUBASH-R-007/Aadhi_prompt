"""Small asyncio helpers for the renderer: deadlines with cancel polling and fail-fast task groups.

* :func:`await_polling` waits for an awaitable with a hard deadline while calling a
  ``check_cancelled`` callback about once a second, so a user cancel (``JobCancelled``) takes
  effect even while a browser call or an ffmpeg process is still running.
* :func:`gather_or_cancel` runs coroutines concurrently; the first failure cancels the siblings
  and is re-raised as-is (no ``ExceptionGroup`` wrapping, unlike ``asyncio.TaskGroup``).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import Any, TypeVar

T = TypeVar("T")

CANCEL_POLL_SECONDS = 1.0


class DeadlineExceeded(TimeoutError):
    """The awaited operation did not finish within its deadline (the operation was cancelled)."""


def _consume(fut: asyncio.Future[Any]) -> None:
    """Mark a finished future's exception as retrieved (no 'never retrieved' warnings)."""
    if fut.done() and not fut.cancelled():
        fut.exception()


async def reap(fut: asyncio.Future[Any], *, grace: float = 5.0) -> None:
    """Cancel ``fut`` (if still running) and wait up to ``grace`` seconds for it to finish.

    Never raises the future's own exception; an outer cancellation still propagates.
    """
    if not fut.done():
        fut.cancel()
        await asyncio.wait({fut}, timeout=grace)
    _consume(fut)


async def await_polling(
    aw: Awaitable[T],
    *,
    timeout: float,
    check_cancelled: Callable[[], None] | None = None,
    poll: float = CANCEL_POLL_SECONDS,
) -> T:
    """Await ``aw`` for at most ``timeout`` seconds, calling ``check_cancelled`` every ``poll`` s.

    Raises :class:`DeadlineExceeded` on timeout and whatever ``check_cancelled`` raises; in both
    cases (and on outer cancellation) the pending operation is cancelled before returning.
    """
    fut = asyncio.ensure_future(aw)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise DeadlineExceeded(f"timed out after {timeout:.0f}s")
            step = min(poll, remaining) if check_cancelled is not None else remaining
            done, _ = await asyncio.wait({fut}, timeout=step)
            if done:
                return fut.result()
            if check_cancelled is not None:
                check_cancelled()
    finally:
        if not fut.done():
            await reap(fut)
        else:
            _consume(fut)


async def gather_or_cancel(coros: Sequence[Coroutine[Any, Any, T]]) -> list[T]:
    """Run ``coros`` concurrently; on the first failure cancel the rest and re-raise it."""
    tasks = [asyncio.ensure_future(c) for c in coros]
    if not tasks:
        return []
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in tasks:  # deterministic: the first failed task in submission order
            if task in done and not task.cancelled() and task.exception() is not None:
                raise task.exception()  # type: ignore[misc]
        return [t.result() for t in tasks]
    finally:
        pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
        if pending:
            with contextlib.suppress(Exception):
                await asyncio.wait(pending, timeout=30)
        for t in tasks:
            _consume(t)
