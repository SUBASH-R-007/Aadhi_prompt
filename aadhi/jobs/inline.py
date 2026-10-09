"""``WORKER_MODE=inline``: worker threads inside the API process (single-box dev).

The API calls ``start_inline_workers(settings)`` on startup and registers
``stop_inline_workers(handle)`` on shutdown. Threads are daemons, so a hard exit never hangs;
jobs interrupted that way are recovered on the next start (boot-uuid recovery) or by the reaper.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import Settings
from .worker import Worker

log = logging.getLogger(__name__)

_lock = threading.Lock()
_handles: list[Worker] = []


def start_inline_workers(settings: Settings) -> Any:
    """Start ``WORKER_CONCURRENCY`` worker threads for ``WORKER_KINDS`` when ``WORKER_MODE=inline``.

    Returns an opaque handle (``None`` when the mode is ``external``: nothing is started).
    """
    if settings.worker_mode != "inline":
        log.info("WORKER_MODE=%s: no inline workers started", settings.worker_mode)
        return None
    worker = Worker(
        settings,
        kinds=settings.worker_kinds,
        concurrency=settings.worker_concurrency,
        name="inline",
        schedule_cleanup=True,
        host_housekeeping=True,
    )
    worker.start()
    with _lock:
        _handles.append(worker)
    return worker


def stop_inline_workers(handle: Any, timeout: float = 10.0) -> None:
    """Gracefully stop workers started by ``start_inline_workers`` (``None`` is a no-op; idempotent)."""
    if handle is None:
        return
    if not isinstance(handle, Worker):
        raise TypeError("not an inline worker handle")
    try:
        handle.stop(timeout=timeout)
    finally:
        with _lock:
            if handle in _handles:
                _handles.remove(handle)


def notify_inline_workers() -> None:
    """Wake idle inline worker threads (call after enqueueing for lower latency; optional)."""
    with _lock:
        handles = list(_handles)
    for worker in handles:
        worker.notify()
