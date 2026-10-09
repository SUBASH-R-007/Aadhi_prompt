"""Retry policy shared by provider adapters (tenacity based).

Retries transient failures (429, 5xx, timeouts, connection resets) with exponential backoff and
jitter, honouring a server-suggested delay when one is available. Rate limits can be routed to a
key pool: when another key is fresh, the next attempt starts almost immediately.

Every retry and every long-running attempt is reported to the running job's log in plain words
(``aadhi.providers._notify``: "The AI service (OpenAI) didn't respond; trying again (attempt 2 of
4).", "Still waiting for ..."); without a job the notices are skipped.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from tenacity import RetryCallState, retry_if_exception, stop_after_attempt
from tenacity.asyncio import AsyncRetrying

from ._notify import WaitingWatch, notify_retry

R = TypeVar("R")
log = logging.getLogger(__name__)

#: Hard ceiling for a server-suggested delay (``Retry-After`` / ``RetryInfo.retryDelay``).
MAX_RETRY_AFTER_S = 120.0

SleepFn = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ErrorInfo:
    """Classification of an exception raised by a provider call."""

    retry: bool = False
    rate_limited: bool = False
    status: int | None = None
    retry_after: float | None = None  # server-suggested delay (seconds)


NOT_RETRYABLE = ErrorInfo()


async def call_with_retries(
    fn: Callable[[], Awaitable[R]],
    *,
    classify: Callable[[BaseException], ErrorInfo],
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    has_fresh_key: Callable[[], bool] | None = None,
    sleep: SleepFn | None = None,
    label: str = "provider",
    max_retry_after: float = MAX_RETRY_AFTER_S,
    service: str | None = None,
) -> R:
    """Run ``fn`` and retry it while ``classify(exc).retry`` is true.

    The wait is exponential backoff with jitter, capped at ``max_delay``. A server-suggested delay
    (``ErrorInfo.retry_after``) is honoured even when it exceeds ``max_delay`` (retrying earlier
    would just hit the limit again); when it exceeds ``max_retry_after`` the call is not retried
    (unless ``has_fresh_key`` says another key can be used right away).
    The final exception is re-raised unchanged (callers convert it to a ``ProviderError``).
    ``has_fresh_key`` lets a key pool shorten the wait after a 429 when another key is usable.
    ``service`` names the service in the job-log notices (e.g. ``"AI service (OpenAI)"``; default
    ``label``); each attempt runs under a "still waiting" watchdog (``_notify.WaitingWatch``).
    """
    name = service or label
    provider = label.split(" ", 1)[0]
    attempts = 0

    def wait(state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        info = classify(exc) if exc is not None else NOT_RETRYABLE
        if info.rate_limited and has_fresh_key is not None and has_fresh_key():
            return 0.2
        backoff = base_delay * (2 ** max(0, state.attempt_number - 1)) + random.uniform(0, base_delay)  # not crypto
        backoff = min(backoff, max_delay)
        if info.retry_after is not None and info.retry_after > 0:
            backoff = max(backoff, min(float(info.retry_after), max_retry_after))
        return float(backoff)

    async def invoke() -> R:
        nonlocal attempts
        attempts += 1
        with WaitingWatch(service=name, provider=provider, attempt=attempts, max_attempts=max(1, max_attempts)):
            # ``fn`` may be a lambda returning a coroutine: tenacity only awaits coroutine *functions*.
            result = fn()
            if inspect.isawaitable(result):
                result = await result
            return result

    def before_sleep(state: RetryCallState) -> None:
        # Only the exception type and status are logged: messages may echo request data or keys.
        exc = state.outcome.exception() if state.outcome else None
        info = classify(exc) if exc is not None else NOT_RETRYABLE
        delay = state.next_action.sleep if state.next_action else 0.0
        log.info("%s: retrying after %s%s (attempt %d/%d, waiting %.1fs)", label, type(exc).__name__,
                 f" HTTP {info.status}" if info.status else "", state.attempt_number, max_attempts, delay)
        notify_retry(service=name, provider=provider, exc=exc, rate_limited=info.rate_limited, status=info.status,
                     attempt=state.attempt_number + 1, max_attempts=max(1, max_attempts), delay=float(delay or 0.0))

    def should_retry(exc: BaseException) -> bool:
        info = classify(exc)
        if not info.retry:
            return False
        if info.retry_after is not None and info.retry_after > max_retry_after:
            # The server asked for a longer pause than we are willing to wait: give up now (another
            # key of a pool may still be usable immediately).
            return bool(info.rate_limited and has_fresh_key is not None and has_fresh_key())
        return True

    retrying = AsyncRetrying(
        stop=stop_after_attempt(max(1, max_attempts)),
        wait=wait,
        retry=retry_if_exception(should_retry),
        reraise=True,
        sleep=sleep or asyncio.sleep,
        before_sleep=before_sleep,
    )
    return await retrying(invoke)


async def no_sleep(_seconds: float) -> None:
    """Sleep replacement for tests (still yields to the loop)."""
    await asyncio.sleep(0)
