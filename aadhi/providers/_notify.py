"""Plain-language notices about retried or slow provider calls, for the job log.

* ``call_with_retries`` reports every retry before it waits (``notify_retry``: "The AI service
  (OpenAI) didn't respond; trying again (attempt 2 of 4).", rate-limit waits) and runs each
  attempt under ``WaitingWatch``: after ``waiting_after_s`` without an answer, then after
  ``waiting_every_s`` and growing intervals, a "Still waiting for the AI service ..." notice is sent.
* A streamed request holds those notices back near its first-response limit (``hold_waiting``)
  until it shows a sign of life (``release_waiting``): a request that gets no answer is reported
  once, as a retry, not as "still waiting" a moment before the retry.
* The job runtime installs a sink for a job's task with ``provider_notices(sink)`` (the worker
  uses ``DBJobContext.provider_notice``, which throttles them with ``NoticeThrottle`` and writes
  job events); tasks created inside inherit it. Without a sink (API requests, CLI, tests) nothing
  is reported and no watchdog runs.
* A failing sink never reaches the provider call. Notices name the service, the attempt, the HTTP
  status and the wait: never provider error text, request data or keys.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, replace
from typing import Any, Literal

from ._stream import StreamStalled

log = logging.getLogger(__name__)

#: First "still waiting" notice of one attempt, then after ``WAITING_EVERY_S``; each later interval
#: is twice the one before, up to ``WAITING_EVERY_MAX_S`` (a 30-minute call adds about 5 lines).
WAITING_AFTER_S = 60.0
WAITING_EVERY_S = 120.0
WAITING_EVERY_MAX_S = 600.0
#: While a streamed request waits for its first sign of life, no "still waiting" notice is sent in
#: the last ``HOLD_MARGIN_S`` before its first-response limit; a held notice comes ``HOLD_GRACE_S``
#: after the limit (in practice never: the request is retried as stalled, or it answered).
HOLD_MARGIN_S = 30.0
HOLD_GRACE_S = 15.0
#: Retry waits shorter than this are not mentioned ("trying again" instead of "trying again in 4 s").
MENTION_WAIT_S = 5.0

NoticeKind = Literal["retry", "rate_limited", "waiting"]
NoticeLevel = Literal["info", "warning"]


@dataclass(frozen=True)
class ProviderNotice:
    """One notice for the job log."""

    kind: NoticeKind
    provider: str  # internal id of the provider ("openai", "anthropic", "gemini", "edge", ...)
    message: str  # plain words for the teacher
    level: NoticeLevel = "warning"
    attempt: int | None = None  # retry: the attempt about to start; waiting: the running attempt
    max_attempts: int | None = None
    delay_s: float | None = None  # retry: the wait before the next attempt
    elapsed_s: float | None = None  # waiting: time since the attempt started
    status: int | None = None  # HTTP status of the failure that is retried

    def data(self) -> dict[str, Any]:
        """Job-event data (``notice`` marks provider notices for the Studio)."""
        values: dict[str, Any] = {
            "notice": self.kind,
            "provider": self.provider,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "delay_seconds": round(self.delay_s, 1) if self.delay_s is not None else None,
            "elapsed_seconds": round(self.elapsed_s) if self.elapsed_s is not None else None,
            "status": self.status,
        }
        return {k: v for k, v in values.items() if v is not None}


NoticeSink = Callable[[ProviderNotice], None]


@dataclass(frozen=True)
class NoticeConfig:
    sink: NoticeSink
    waiting_after_s: float = WAITING_AFTER_S
    waiting_every_s: float = WAITING_EVERY_S


#: Where notices of the running task go (``None``: nowhere).
PROVIDER_NOTICE: ContextVar[NoticeConfig | None] = ContextVar("aadhi_provider_notice", default=None)


@contextmanager
def provider_notices(
    sink: NoticeSink | None,
    *,
    waiting_after_s: float | None = None,
    waiting_every_s: float | None = None,
) -> Iterator[None]:
    """Send the notices of provider calls made inside the block (and the tasks it creates) to
    ``sink`` (``None``: none are sent). The "still waiting" timing defaults to ``WAITING_AFTER_S`` /
    ``WAITING_EVERY_S``; ``waiting_after_s <= 0`` turns it off."""
    config = None
    if sink is not None:
        config = NoticeConfig(sink, WAITING_AFTER_S if waiting_after_s is None else float(waiting_after_s),
                              WAITING_EVERY_S if waiting_every_s is None else float(waiting_every_s))
    token = PROVIDER_NOTICE.set(config)
    try:
        yield
    finally:
        PROVIDER_NOTICE.reset(token)


def _deliver(config: NoticeConfig | None, notice: ProviderNotice) -> None:
    if config is None:
        return
    try:
        config.sink(notice)
    except Exception as exc:  # noqa: BLE001 - a notice must never fail the provider call
        log.warning("provider notice could not be recorded (%s)", type(exc).__name__)


def emit(notice: ProviderNotice) -> None:
    """Send ``notice`` to the sink of the running task, if any."""
    _deliver(PROVIDER_NOTICE.get(), notice)


# --- wording ----------------------------------------------------------------------------------


def describe_duration(seconds: float) -> str:
    """``"45 s"`` / ``"2 min"`` (whole units, at least 1 s)."""
    if seconds < 60:
        return f"{max(1, round(seconds))} s"
    return f"{round(seconds / 60)} min"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _is_timeout(exc: BaseException | None) -> bool:
    # SDK timeout classes (APITimeoutError, httpx ReadTimeout, aiohttp ServerTimeoutError) do not all
    # derive from TimeoutError; their names say what they are.
    return isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()


def retry_notice(
    *,
    service: str,
    provider: str,
    exc: BaseException | None,
    rate_limited: bool,
    status: int | None,
    attempt: int,
    max_attempts: int,
    delay: float,
) -> ProviderNotice:
    """Notice for a retry of ``service`` (e.g. ``"AI service (OpenAI)"``); ``attempt`` is the next one."""
    where = f"(attempt {attempt} of {max_attempts})"
    then = "trying again" if delay < MENTION_WAIT_S else f"trying again in {describe_duration(delay)}"
    kind: NoticeKind = "retry"
    if rate_limited:
        kind = "rate_limited"
        text = f"the {service} is busy (rate limit); {then} {where}."
    elif isinstance(exc, StreamStalled):
        waited = describe_duration(exc.seconds)
        text = (f"the {service} didn't respond within {waited}; {then} {where}." if exc.phase == "first"
                else f"the {service} stopped sending its answer for {waited}; {then} {where}.")
    elif status:
        text = f"the {service} had a temporary problem (HTTP {status}); {then} {where}."
    elif _is_timeout(exc):
        text = f"the {service} didn't respond; {then} {where}."
    else:
        text = f"the connection to the {service} was interrupted; {then} {where}."
    return ProviderNotice(kind=kind, provider=provider, message=_sentence(text), level="warning", attempt=attempt,
                          max_attempts=max_attempts, delay_s=float(delay), status=status)


def notify_retry(**fields: Any) -> None:
    """Build and emit a ``retry_notice`` (never raises)."""
    if PROVIDER_NOTICE.get() is None:
        return
    try:
        notice = retry_notice(**fields)
    except Exception as exc:  # noqa: BLE001 - wording problems must not fail the call
        log.warning("provider retry notice failed (%s)", type(exc).__name__)
        return
    emit(notice)


def waiting_notice(*, service: str, provider: str, elapsed: float, attempt: int, max_attempts: int,
                   first: bool) -> ProviderNotice:
    """"Still waiting" notice ``elapsed`` seconds into ``attempt``."""
    so_far = describe_duration(elapsed) + " so far"
    if attempt > 1:
        so_far = f"attempt {attempt} of {max_attempts}, {so_far}"
    text = f"Still waiting for the {service} to answer ({so_far})."
    if first:
        text += " Long answers can take a few minutes."
    return ProviderNotice(kind="waiting", provider=provider, message=text, level="info", attempt=attempt,
                          max_attempts=max_attempts, elapsed_s=float(elapsed))


class WaitingWatch:
    """Within a running event loop: send "still waiting" notices while the block runs (timer
    callbacks on the loop, no task). Does nothing when no sink is installed.

    ``hold(first_s)`` / ``release()`` (via ``hold_waiting`` / ``release_waiting``, for the watch of
    the running attempt): no notice is sent in the last ``HOLD_MARGIN_S`` before a streamed
    request's first-response limit, so that a request that gets no answer is reported once, as a
    retry. During a long upload (a large document) earlier notices still come.
    """

    def __init__(self, *, service: str, provider: str, attempt: int, max_attempts: int) -> None:
        self.service = service
        self.provider = provider
        self.attempt = attempt
        self.max_attempts = max_attempts
        self._config: NoticeConfig | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._handle: asyncio.TimerHandle | None = None
        self._token: Token[WaitingWatch | None] | None = None
        self._started = 0.0
        self._next = 0.0  # loop time the next notice is due (before any hold)
        self._every = 0.0  # interval after the next notice
        self._hold_until: float | None = None  # first-response deadline of the running request
        self._sent = 0

    def __enter__(self) -> WaitingWatch:
        config = PROVIDER_NOTICE.get()
        if config is None or config.waiting_after_s <= 0:
            return self
        self._config = config
        self._loop = asyncio.get_running_loop()
        self._started = self._loop.time()
        self._next = self._started + config.waiting_after_s
        self._every = config.waiting_every_s
        self._schedule()
        self._token = _CURRENT_WATCH.set(self)
        return self

    def _schedule(self) -> None:
        """(Re)arm the timer for the next notice, past a hold that it would fall into."""
        assert self._loop is not None
        due = self._next
        if self._hold_until is not None and due > self._hold_until - HOLD_MARGIN_S:
            due = max(due, self._hold_until + HOLD_GRACE_S)
        if self._handle is not None:
            self._handle.cancel()
        self._handle = self._loop.call_at(due, self._fire)

    def hold(self, first_s: float) -> None:
        """A streamed request started that must show a sign of life within ``first_s``."""
        if self._config is None or self._loop is None or self._handle is None:
            return
        self._hold_until = self._loop.time() + max(0.0, float(first_s))
        self._schedule()

    def release(self) -> None:
        """The request showed a sign of life (or is no longer bounded): notices are due as usual."""
        if self._hold_until is None:
            return
        self._hold_until = None
        if self._config is not None and self._handle is not None:
            self._schedule()

    def _fire(self) -> None:
        if self._loop is None or self._config is None:  # the block already ended
            return
        self._handle = None
        try:
            # "Long answers can take ..." only once an answer may be coming (not during an upload)
            notice = waiting_notice(service=self.service, provider=self.provider,
                                    elapsed=self._loop.time() - self._started, attempt=self.attempt,
                                    max_attempts=self.max_attempts,
                                    first=self._sent == 0 and self._hold_until is None)
        except Exception as exc:  # noqa: BLE001 - never disturb the loop
            log.warning("provider waiting notice failed (%s)", type(exc).__name__)
            return
        self._sent += 1
        _deliver(self._config, notice)
        if self._every > 0 and not self._loop.is_closed():
            self._next = self._loop.time() + self._every
            self._every = min(self._every * 2, max(self._config.waiting_every_s, WAITING_EVERY_MAX_S))
            self._schedule()

    def __exit__(self, *exc: object) -> None:
        if self._handle is not None:
            self._handle.cancel()
            self._handle = None
        self._config = None  # a timer that already fired cannot schedule another one
        if self._token is not None:
            try:
                _CURRENT_WATCH.reset(self._token)
            except ValueError:  # exited in another context than it was entered in: nothing to restore
                pass
            self._token = None


#: The watch of the running attempt (``hold_waiting`` / ``release_waiting``).
_CURRENT_WATCH: ContextVar[WaitingWatch | None] = ContextVar("aadhi_waiting_watch", default=None)


def hold_waiting(first_s: float | None) -> None:
    """A streamed request of the running attempt must show a sign of life within ``first_s`` (its
    first-response limit; ``None``: no limit, nothing is held). Its "still waiting" notices are held
    back near that limit until ``release_waiting``. Never raises."""
    watch = _CURRENT_WATCH.get()
    if watch is None or first_s is None:
        return
    try:
        watch.hold(first_s)
    except Exception as exc:  # noqa: BLE001 - notices must never fail the call
        log.warning("provider waiting notice could not be held (%s)", type(exc).__name__)


def release_waiting() -> None:
    """The running attempt's request showed a sign of life (``StallGuard(on_alive=...)``), or it is
    sent without stall detection: its "still waiting" notices are due as usual. Never raises."""
    watch = _CURRENT_WATCH.get()
    if watch is None:
        return
    try:
        watch.release()
    except Exception as exc:  # noqa: BLE001 - notices must never fail the call
        log.warning("provider waiting notice could not be released (%s)", type(exc).__name__)


# --- throttling (used by the job runtime) ------------------------------------------------------

LIMIT_MESSAGE = "More notices about slow or retried service calls are not shown for this job."


class NoticeThrottle:
    """Keeps a long job's log readable when many calls are slow at once.

    * the same message within ``repeat_s`` is dropped (parallel calls that stall together);
    * "still waiting" notices are at least ``waiting_s`` apart, whichever call sends them, and at
      most ``waiting_limit`` of them are kept per job (they never use up the budget of warnings);
    * after ``limit`` retry and rate-limit notices one closing line is kept, then nothing.

    Thread-safe.
    """

    def __init__(self, *, repeat_s: float = 60.0, waiting_s: float = 60.0, limit: int = 40,
                 waiting_limit: int = 12, clock: Callable[[], float] = time.monotonic) -> None:
        self.repeat_s = float(repeat_s)
        self.waiting_s = float(waiting_s)
        self.limit = int(limit)
        self.waiting_limit = int(waiting_limit)
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._last_waiting = float("-inf")
        self._waiting_count = 0
        self._count = 0

    def admit(self, notice: ProviderNotice) -> ProviderNotice | None:
        """The notice to record (``notice`` or the closing line), or ``None`` to drop it."""
        with self._lock:
            if self._count > self.limit:  # the closing line was written: nothing more
                return None
            now = self._clock()
            waiting = notice.kind == "waiting"
            if waiting and (self._waiting_count >= self.waiting_limit or now - self._last_waiting < self.waiting_s):
                return None
            last = self._seen.get(notice.message)
            if last is not None and now - last < self.repeat_s:
                return None
            if len(self._seen) > 64:  # forget messages that can no longer collapse anything
                self._seen = {m: t for m, t in self._seen.items() if now - t < self.repeat_s}
            self._seen[notice.message] = now
            if waiting:  # info lines never use up the budget of warnings
                self._last_waiting = now
                self._waiting_count += 1
                return notice
            self._count += 1
            if self._count > self.limit:
                return replace(notice, level="info", message=LIMIT_MESSAGE)
            return notice
