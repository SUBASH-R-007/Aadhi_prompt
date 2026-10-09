"""Run one job's coroutine in a fresh event loop whose thread work can be abandoned.

``asyncio.run`` ends by awaiting ``loop.shutdown_default_executor()``, which on Python 3.11 waits
— without any timeout — for every ``asyncio.to_thread`` / ``run_in_executor`` call to return. A
handler cancelled while it awaits a 10-minute ffmpeg call in a thread would therefore keep its job
thread (and the job's outcome) hostage until the thread returns.

``run_job_loop`` gives every job its own ``ThreadPoolExecutor`` as the loop's default executor and,
when the main coroutine is done (or cancelled), shuts it down with ``wait=False,
cancel_futures=True``: queued thread work is dropped, running thread work is *orphaned* — it may
still finish in the background (it is not killed; Python threads cannot be), but the job's outcome
is recorded immediately. Writes that matter (job rows, version compare-and-set) are lease-fenced,
so orphaned work of a job that moved on cannot clobber the new owner's state.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from ..config import get_settings

log = logging.getLogger(__name__)
T = TypeVar("T")


def default_thread_count() -> int:
    """Same sizing as asyncio's default executor."""
    return min(32, (os.cpu_count() or 1) + 4)


def run_job_loop(
    main: Coroutine[Any, Any, T],
    *,
    name: str = "aadhi-job",
    cleanup_timeout: float = 5.0,
    max_threads: int | None = None,
) -> T:
    """Run ``main`` to completion in a new event loop (like ``asyncio.run``) without ever waiting
    for executor threads. Leftover tasks get ``cleanup_timeout`` seconds to honour cancellation."""
    loop = asyncio.new_event_loop()
    executor = ThreadPoolExecutor(max_workers=max_threads or default_thread_count(), thread_name_prefix=f"{name}-io")
    loop.set_default_executor(executor)
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(main)
    finally:
        try:
            _cancel_leftovers(loop, cleanup_timeout)
            loop.run_until_complete(asyncio.wait_for(loop.shutdown_asyncgens(), max(0.1, cleanup_timeout)))
        except Exception as exc:  # noqa: BLE001 - best-effort cleanup; the outcome matters more
            log.warning("%s: event loop cleanup failed: %s", name, get_settings().redact(repr(exc)))
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            asyncio.set_event_loop(None)
            loop.close()


def _cancel_leftovers(loop: asyncio.AbstractEventLoop, timeout: float) -> None:
    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    done, still_pending = loop.run_until_complete(asyncio.wait(pending, timeout=max(0.1, timeout)))
    for task in done:
        if not task.cancelled() and task.exception() is not None:
            error = get_settings().redact(repr(task.exception()))
            log.warning("background task %s failed during job shutdown: %s", task.get_name(), error)
    if still_pending:
        log.warning("%d background tasks ignored cancellation; abandoning them", len(still_pending))
