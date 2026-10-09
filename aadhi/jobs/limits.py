"""Process-wide concurrency limits usable from any thread and any event loop.

Inline workers run one event loop per job thread, so ``asyncio.Semaphore`` (bound to one loop)
cannot express "at most 6 LLM calls in this process". ``ProcessLimit`` is a FIFO counting
semaphore guarded by a ``threading.Lock``:

* async waiters park on a future of THEIR OWN loop, woken with ``call_soon_threadsafe`` — the
  loop is never blocked;
* sync waiters (plain threads) park on a ``threading.Event``;
* cancellation-safe: a waiter cancelled after being granted a permit hands it on, one cancelled
  before is removed from the queue — permits never leak.

Sizes come from settings: llm=LLM_MAX_PARALLEL, tts=TTS_MAX_PARALLEL, manim=MANIM_MAX_CONCURRENT,
render=RENDER_CONCURRENCY, image=4, video=2.
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Literal

from ..config import Settings, get_settings

LimitName = Literal["llm", "tts", "manim", "render", "image", "video"]
LIMIT_NAMES: tuple[str, ...] = ("llm", "tts", "manim", "render", "image", "video")


def limit_size(name: str, settings: Settings) -> int:
    """Configured size of a named limit (>= 1)."""
    sizes = {
        "llm": settings.llm_max_parallel,
        "tts": settings.tts_max_parallel,
        "manim": settings.manim_max_concurrent,
        "render": settings.render_concurrency,
        "image": 4,
        "video": 2,
    }
    if name not in sizes:
        raise KeyError(f"unknown limit {name!r} (expected one of {LIMIT_NAMES})")
    return max(1, int(sizes[name]))


@dataclass(eq=False)
class _Waiter:
    loop: asyncio.AbstractEventLoop | None = None
    future: asyncio.Future[None] | None = None
    event: threading.Event | None = None
    granted: bool = False


class ProcessLimit:
    """FIFO counting semaphore shared by every thread/loop of the process."""

    def __init__(self, name: str, size: int) -> None:
        if size < 1:
            raise ValueError("limit size must be >= 1")
        self.name = name
        self.size = size
        self._lock = threading.Lock()
        self._available = size
        self._waiters: deque[_Waiter] = deque()

    @property
    def in_use(self) -> int:
        with self._lock:
            return self.size - self._available

    @property
    def waiting(self) -> int:
        with self._lock:
            return len(self._waiters)

    # --- acquire -----------------------------------------------------------------------------------
    async def acquire_async(self) -> None:
        """Wait for a permit without blocking the running loop (cancellation-safe)."""
        loop = asyncio.get_running_loop()
        with self._lock:
            if self._available > 0 and not self._waiters:
                self._available -= 1
                return
            waiter = _Waiter(loop=loop, future=loop.create_future())
            self._waiters.append(waiter)
        assert waiter.future is not None
        try:
            await waiter.future
        except BaseException:
            self._abandon(waiter)
            raise

    def acquire_blocking(self, timeout: float | None = None) -> bool:
        """Blocking acquire for plain threads (never call from a running event loop)."""
        with self._lock:
            if self._available > 0 and not self._waiters:
                self._available -= 1
                return True
            waiter = _Waiter(event=threading.Event())
            self._waiters.append(waiter)
        assert waiter.event is not None
        if waiter.event.wait(timeout):
            return True
        self._abandon(waiter)
        # a grant may have raced with the timeout; _abandon passed it on in that case
        return False

    def _abandon(self, waiter: _Waiter) -> None:
        with self._lock:
            if waiter.granted:
                self._release_locked()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:  # pragma: no cover - already removed
                    pass

    # --- release ----------------------------------------------------------------------------------
    def release(self) -> None:
        """Return a permit (to the next waiter, FIFO)."""
        with self._lock:
            self._release_locked()

    def _release_locked(self) -> None:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.event is not None:
                waiter.granted = True
                waiter.event.set()
                return
            assert waiter.loop is not None and waiter.future is not None
            if waiter.loop.is_closed():
                continue
            waiter.granted = True
            try:
                waiter.loop.call_soon_threadsafe(_resolve, waiter.future)
            except RuntimeError:  # loop closed concurrently
                waiter.granted = False
                continue
            return
        if self._available >= self.size:
            raise ValueError(f"limit {self.name!r} released more times than acquired")
        self._available += 1


def _resolve(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


# --- registry -------------------------------------------------------------------------------------

_registry_lock = threading.Lock()
_registry: dict[str, ProcessLimit] = {}


def get_limit(name: str) -> ProcessLimit:
    """The process-wide limit ``name`` (created on first use from settings)."""
    with _registry_lock:
        lim = _registry.get(name)
        if lim is None:
            lim = _registry[name] = ProcessLimit(name, limit_size(name, get_settings()))
        return lim


def configure_limits(settings: Settings) -> None:
    """(Re)create every limit from ``settings``. Holders of old permits release on the old objects."""
    with _registry_lock:
        for name in LIMIT_NAMES:
            _registry[name] = ProcessLimit(name, limit_size(name, settings))


def reset_limits() -> None:
    """Tests: forget all limits (re-created lazily from current settings)."""
    with _registry_lock:
        _registry.clear()


def limit_stats() -> dict[str, dict[str, int]]:
    """``{name: {size, in_use, waiting}}`` for limits created so far."""
    with _registry_lock:
        items = list(_registry.items())
    return {n: {"size": lim.size, "in_use": lim.in_use, "waiting": lim.waiting} for n, lim in items}


@asynccontextmanager
async def acquire(name: LimitName) -> AsyncIterator[None]:
    """``async with acquire("llm"): ...`` — process-wide, any loop/thread, cancellation-safe."""
    lim = get_limit(name)
    await lim.acquire_async()
    try:
        yield
    finally:
        lim.release()


@contextmanager
def acquire_sync(name: LimitName, timeout: float | None = None) -> Iterator[None]:
    """Blocking variant for plain threads; raises ``TimeoutError`` after ``timeout`` seconds."""
    lim = get_limit(name)
    if not lim.acquire_blocking(timeout):
        raise TimeoutError(f"timed out waiting for the {name!r} limit")
    try:
        yield
    finally:
        lim.release()
