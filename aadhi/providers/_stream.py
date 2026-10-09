"""Stall detection for streamed provider requests.

A request sent into a dead connection (a pooled keep-alive connection after a network or DNS drop)
gets no answer at all, and without a shorter limit it waits for the whole per-attempt timeout. A
healthy streamed request shows a sign of life within seconds (response headers, the provider's
first event) and then keeps streaming. ``StallGuard`` bounds both phases in the current task (no
helper task per event):

* until the first sign of life (``touch()``): ``first_s``, which callers grow with
  ``first_response_limit`` so that uploading a large document on a slow connection is not mistaken
  for a stall;
* between later events: ``idle_s``, lifted until the next event when the provider reports that it
  is legitimately busy and silent (``touch(expect_silence=True)``: an OpenAI reasoning item, a
  queued response). ``None`` disables a limit.

A stall raises ``StreamStalled``, a ``TimeoutError``: every provider classifies it as retryable,
so ``call_with_retries`` starts a new attempt. The total cap of one attempt stays with the caller
(``asyncio.wait_for``). ``on_alive`` is called once, at the first sign of life (the providers use it
to put the attempt's "still waiting" notices back on schedule, ``_notify.hold_waiting``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, Literal

#: Upload rate assumed when the first-response limit is extended for a large request body
#: (64 kB/s, about 0.5 Mbit/s: a slow but working uplink).
UPLOAD_ALLOWANCE_BYTES_PER_S = 64_000

StallPhase = Literal["first", "idle"]


class StreamStalled(TimeoutError):
    """No sign of life from the provider within the first-response or idle limit (retryable)."""

    def __init__(self, phase: StallPhase, seconds: float) -> None:
        what = "no response" if phase == "first" else "no data"
        super().__init__(f"{what} from the provider for {seconds:.0f} s")
        self.phase: StallPhase = phase
        self.seconds = float(seconds)


def payload_size(value: Any) -> int:
    """Approximate size in bytes of a request's keyword arguments (text and binary leaves; inline
    files and images dominate). Counts characters, never copies the strings."""
    if isinstance(value, str | bytes | bytearray):
        return len(value)
    if isinstance(value, Mapping):
        return sum(payload_size(v) for v in value.values())
    if isinstance(value, list | tuple):
        return sum(payload_size(v) for v in value)
    return 0


def first_response_limit(base_s: float, request_bytes: int = 0) -> float:
    """``base_s`` plus the time a slow uplink needs to send ``request_bytes`` (the first sign of
    life can only come after the whole request was sent)."""
    return float(base_s) + max(0, int(request_bytes)) / UPLOAD_ALLOWANCE_BYTES_PER_S


class StallGuard:
    """``async with StallGuard(first_s=60, idle_s=240) as guard: ... guard.touch() per event``."""

    def __init__(self, *, first_s: float | None, idle_s: float | None,
                 on_alive: Callable[[], None] | None = None) -> None:
        self.first_s = first_s
        self.idle_s = idle_s
        self.on_alive = on_alive
        self.events = 0
        self._phase: StallPhase = "first"
        self._limit = first_s
        self._loop: asyncio.AbstractEventLoop | None = None
        self._timeout: asyncio.Timeout | None = None

    def _deadline(self, limit: float | None) -> float | None:
        assert self._loop is not None
        return None if limit is None else self._loop.time() + max(0.0, float(limit))

    async def __aenter__(self) -> StallGuard:
        self._loop = asyncio.get_running_loop()
        self._timeout = asyncio.timeout_at(self._deadline(self.first_s))
        await self._timeout.__aenter__()
        return self

    def touch(self, *, expect_silence: bool = False) -> None:
        """A sign of life arrived: the idle limit starts again (lifted until the next event when
        ``expect_silence``: the provider said it is busy and will be quiet)."""
        assert self._timeout is not None, "StallGuard.touch outside 'async with'"
        if self.events == 0 and self.on_alive is not None:
            self.on_alive()
        self.events += 1
        if self._timeout.expired():  # the stall already fired: let it surface at the next await
            return
        self._phase = "idle"
        self._limit = None if expect_silence else self.idle_s
        self._timeout.reschedule(self._deadline(self._limit))

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> bool | None:
        assert self._timeout is not None
        try:
            return await self._timeout.__aexit__(exc_type, exc, tb)
        except TimeoutError:
            if self._timeout.expired() and self._limit is not None:
                raise StreamStalled(self._phase, self._limit) from None
            raise
