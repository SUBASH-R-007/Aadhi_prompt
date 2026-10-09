"""Stall detection (``_stream.StallGuard``) and job-log notices (``_notify``, ``call_with_retries``)."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.providers import _notify
from aadhi.providers._notify import (
    LIMIT_MESSAGE,
    NoticeThrottle,
    ProviderNotice,
    WaitingWatch,
    describe_duration,
    hold_waiting,
    provider_notices,
    release_waiting,
    retry_notice,
)
from aadhi.providers._retry import ErrorInfo, call_with_retries
from aadhi.providers._stream import (
    UPLOAD_ALLOWANCE_BYTES_PER_S,
    StallGuard,
    StreamStalled,
    first_response_limit,
    payload_size,
)

from .conftest import no_sleep

# --- StallGuard --------------------------------------------------------------------------------


async def _events(pauses: list[float]):
    for i, pause in enumerate(pauses):
        await asyncio.sleep(pause)
        yield i


async def _consume(guard: StallGuard, pauses: list[float], *, silent_after: set[int] = frozenset()) -> int:
    seen = 0
    async with guard:
        async for i in _events(pauses):
            guard.touch(expect_silence=i in silent_after)
            seen += 1
    return seen


@pytest.mark.asyncio
async def test_no_first_event_is_a_first_response_stall() -> None:
    with pytest.raises(StreamStalled) as info:
        await _consume(StallGuard(first_s=0.05, idle_s=10), [5.0])
    assert info.value.phase == "first" and info.value.seconds == pytest.approx(0.05)
    assert isinstance(info.value, TimeoutError)  # every provider classifies it as retryable


@pytest.mark.asyncio
async def test_silence_after_events_is_an_idle_stall() -> None:
    guard = StallGuard(first_s=1, idle_s=0.08)
    with pytest.raises(StreamStalled) as info:
        await _consume(guard, [0.0, 0.01, 0.01, 5.0])
    assert info.value.phase == "idle" and guard.events == 3


@pytest.mark.asyncio
async def test_slow_but_steady_stream_is_never_cut_off() -> None:
    # 12 events, 40 ms apart: the whole answer (~0.5 s) takes far longer than the idle limit (0.1 s)
    assert await _consume(StallGuard(first_s=0.1, idle_s=0.1), [0.04] * 12) == 12


@pytest.mark.asyncio
async def test_expected_silence_lifts_the_idle_limit_until_the_next_event() -> None:
    # event 1 says "busy" (e.g. reasoning): a 0.25 s pause is fine; afterwards the limit is back
    assert await _consume(StallGuard(first_s=1, idle_s=0.08), [0.0, 0.01, 0.25, 0.01], silent_after={1}) == 4
    with pytest.raises(StreamStalled):
        await _consume(StallGuard(first_s=1, idle_s=0.08), [0.0, 0.01, 0.25, 0.01, 0.3], silent_after={1})


@pytest.mark.asyncio
async def test_disabled_limits_and_outer_cap_and_own_errors() -> None:
    assert await _consume(StallGuard(first_s=None, idle_s=None), [0.1, 0.1]) == 2
    # the caller's total cap (wait_for) is not reported as a stall
    with pytest.raises(TimeoutError) as info:
        await asyncio.wait_for(_consume(StallGuard(first_s=5, idle_s=5), [5.0]), 0.05)
    assert not isinstance(info.value, StreamStalled)
    # errors raised inside the block pass through unchanged
    with pytest.raises(KeyError):
        async with StallGuard(first_s=1, idle_s=1):
            raise KeyError("x")


def test_first_response_limit_grows_with_the_request_size() -> None:
    kwargs = {"model": "m", "input": [{"content": [{"file_data": "x" * 640_000}, {"text": "hello"}]}],
              "max_output_tokens": 5, "blob": b"12345"}
    assert payload_size(kwargs) == 1 + 640_000 + 5 + 5
    assert first_response_limit(60, 0) == 60
    assert first_response_limit(60, payload_size(kwargs)) == pytest.approx(60 + 640_011 / UPLOAD_ALLOWANCE_BYTES_PER_S)
    assert first_response_limit(60, 27_000_000) > 400  # a 20 MB PDF (base64) on a slow uplink is not a stall


# --- wording -----------------------------------------------------------------------------------


def test_retry_notice_wording() -> None:
    def text(exc: BaseException | None, *, rate_limited: bool = False, status: int | None = None,
             delay: float = 1.2) -> str:
        return retry_notice(service="AI service (OpenAI)", provider="openai", exc=exc, rate_limited=rate_limited,
                            status=status, attempt=2, max_attempts=4, delay=delay).message

    assert text(TimeoutError()) == "The AI service (OpenAI) didn't respond; trying again (attempt 2 of 4)."
    assert text(StreamStalled("first", 60)) == (
        "The AI service (OpenAI) didn't respond within 1 min; trying again (attempt 2 of 4).")
    assert text(StreamStalled("idle", 240)) == (
        "The AI service (OpenAI) stopped sending its answer for 4 min; trying again (attempt 2 of 4).")
    assert text(None, rate_limited=True, status=429, delay=30) == (
        "The AI service (OpenAI) is busy (rate limit); trying again in 30 s (attempt 2 of 4).")
    assert text(RuntimeError("boom sk-secret"), status=503, delay=8) == (
        "The AI service (OpenAI) had a temporary problem (HTTP 503); trying again in 8 s (attempt 2 of 4).")
    assert text(ConnectionResetError()) == (
        "The connection to the AI service (OpenAI) was interrupted; trying again (attempt 2 of 4).")

    class ReadTimeout(Exception):  # SDK timeout classes that are not TimeoutError subclasses
        pass

    assert "didn't respond;" in text(ReadTimeout())
    notice = retry_notice(service="AI service (OpenAI)", provider="openai", exc=None, rate_limited=True, status=429,
                          attempt=3, max_attempts=4, delay=61.0)
    assert notice.kind == "rate_limited" and notice.level == "warning"
    assert notice.data() == {"notice": "rate_limited", "provider": "openai", "attempt": 3, "max_attempts": 4,
                             "delay_seconds": 61.0, "status": 429}
    assert [describe_duration(x) for x in (0.2, 44.6, 60, 150, 600)] == ["1 s", "45 s", "1 min", "2 min", "10 min"]


# --- still waiting -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_waiting_watch_reports_a_slow_call_then_repeats() -> None:
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=0.05, waiting_every_s=0.1):
        with WaitingWatch(service="AI service (Claude)", provider="anthropic", attempt=1, max_attempts=4):
            await asyncio.sleep(0.32)
        count_at_exit = len(notices)
        await asyncio.sleep(0.15)  # nothing after the block ended
    count = len(notices)
    assert count == count_at_exit
    assert 2 <= count <= 4  # ~0.05, 0.15, 0.25 s (timers may run late on a busy machine)
    first, second = notices[0], notices[1]
    assert first.kind == "waiting" and first.level == "info"
    assert first.message == ("Still waiting for the AI service (Claude) to answer (1 s so far). "
                             "Long answers can take a few minutes.")
    assert second.message == "Still waiting for the AI service (Claude) to answer (1 s so far)."
    assert first.data()["notice"] == "waiting" and "elapsed_seconds" in first.data()

    retry = WaitingWatch(service="AI service (Claude)", provider="anthropic", attempt=2, max_attempts=4)
    notices.clear()
    with provider_notices(notices.append, waiting_after_s=0.02, waiting_every_s=0):
        with retry:
            await asyncio.sleep(0.1)
    assert [n.message for n in notices] == [
        "Still waiting for the AI service (Claude) to answer (attempt 2 of 4, 1 s so far). "
        "Long answers can take a few minutes."]


@pytest.mark.asyncio
async def test_later_notices_of_a_long_call_come_at_growing_intervals() -> None:
    notices: list[ProviderNotice] = []
    loop = asyncio.get_running_loop()
    with provider_notices(notices.append):  # the defaults: after 1 min, then 2, 4, 8, 10, 10 ... min later
        with WaitingWatch(service="AI service (Claude)", provider="anthropic", attempt=1, max_attempts=4) as watch:
            assert watch._handle is not None and round(watch._handle.when() - loop.time()) == 60
            gaps = []
            for _ in range(6):
                watch._handle.cancel()
                watch._fire()  # the timer's callback, run now
                gaps.append(round(watch._handle.when() - loop.time()))
    assert gaps == [120, 240, 480, 600, 600, 600]  # a 30-minute call: 5 lines instead of 15
    assert len(notices) == 6


@pytest.mark.asyncio
async def test_notices_are_held_near_a_first_response_limit_until_the_answer_starts(monkeypatch) -> None:
    monkeypatch.setattr(_notify, "HOLD_MARGIN_S", 0.1)
    notices: list[ProviderNotice] = []
    service = {"service": "AI service (OpenAI)", "provider": "openai", "attempt": 1, "max_attempts": 4}
    with provider_notices(notices.append, waiting_after_s=0.05, waiting_every_s=10):
        with WaitingWatch(**service):
            hold_waiting(0.1)  # no answer by 0.1 s means a retry: the notice due at 0.05 s waits
            await asyncio.sleep(0.15)
            assert notices == []
            release_waiting()  # the answer started: the overdue notice comes at once
            await asyncio.sleep(0.03)
        assert [n.message for n in notices] == [
            "Still waiting for the AI service (OpenAI) to answer (1 s so far). Long answers can take a few minutes."]

        notices.clear()
        with WaitingWatch(**service):
            hold_waiting(1.0)  # a large upload: notices long before the limit still come (no "long answers")
            await asyncio.sleep(0.15)
        assert [n.message for n in notices] == ["Still waiting for the AI service (OpenAI) to answer (1 s so far)."]

        notices.clear()
        with WaitingWatch(**service):
            hold_waiting(None)  # no first-response limit (e.g. a reasoning model): nothing is held
            await asyncio.sleep(0.1)
        assert len(notices) == 1
    hold_waiting(5)  # outside an attempt: nothing to hold, never raises
    release_waiting()


@pytest.mark.asyncio
async def test_no_sink_means_no_watchdog_and_no_notices() -> None:
    watch = WaitingWatch(service="AI service (OpenAI)", provider="openai", attempt=1, max_attempts=4)
    with watch:
        assert watch._handle is None  # nothing scheduled outside a job
    with provider_notices(None):
        _notify.emit(ProviderNotice(kind="retry", provider="x", message="m"))  # silently dropped


# --- call_with_retries -------------------------------------------------------------------------


def _classify(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, PermissionError):
        return ErrorInfo(retry=True, rate_limited=True, status=429)
    return ErrorInfo(retry=isinstance(exc, TimeoutError | ConnectionError))


@pytest.mark.asyncio
async def test_every_retry_is_reported_with_attempt_numbers() -> None:
    script: list[BaseException | str] = [StreamStalled("first", 60), PermissionError(), ConnectionResetError(), "ok"]

    async def fn() -> str:
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        out = await call_with_retries(fn, classify=_classify, max_attempts=4, sleep=no_sleep, label="openai",
                                      service="AI service (OpenAI)")
    assert out == "ok"
    assert [(n.kind, n.attempt, n.max_attempts, n.provider) for n in notices] == [
        ("retry", 2, 4, "openai"), ("rate_limited", 3, 4, "openai"), ("retry", 4, 4, "openai")]
    assert notices[0].message.startswith("The AI service (OpenAI) didn't respond within 1 min; trying again")
    assert "busy (rate limit)" in notices[1].message
    assert notices[2].message.startswith("The connection to the AI service (OpenAI) was interrupted")


@pytest.mark.asyncio
async def test_no_notice_for_errors_that_are_not_retried_or_the_last_attempt() -> None:
    async def fail() -> None:
        raise TimeoutError

    notices: list[ProviderNotice] = []
    with provider_notices(notices.append), pytest.raises(TimeoutError):
        await call_with_retries(fail, classify=_classify, max_attempts=2, sleep=no_sleep, label="edge tts")
    assert len(notices) == 1 and notices[0].message.startswith("The edge tts didn't respond")  # default: the label

    async def bad() -> None:
        raise ValueError("not retryable")

    notices.clear()
    with provider_notices(notices.append), pytest.raises(ValueError):
        await call_with_retries(bad, classify=_classify, max_attempts=4, sleep=no_sleep)
    assert notices == []


@pytest.mark.asyncio
async def test_a_failing_sink_never_fails_the_call(caplog) -> None:
    calls = 0

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError
        await asyncio.sleep(0.05)  # long enough for a "still waiting" notice
        return "ok"

    def broken(_notice: ProviderNotice) -> None:
        raise RuntimeError("database is down")

    with provider_notices(broken, waiting_after_s=0.01, waiting_every_s=0.01):
        assert await call_with_retries(flaky, classify=_classify, sleep=no_sleep, label="openai") == "ok"
    assert calls == 2
    assert "provider notice could not be recorded (RuntimeError)" in caplog.text


@pytest.mark.asyncio
async def test_slow_attempts_get_still_waiting_notices_and_tasks_inherit_the_sink() -> None:
    async def slow() -> str:
        await asyncio.sleep(0.12)
        return "ok"

    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=0.05, waiting_every_s=10):
        results = await asyncio.gather(*[
            asyncio.create_task(call_with_retries(slow, classify=_classify, sleep=no_sleep, label="gemini llm",
                                                  service="AI service (Gemini)"))
            for _ in range(2)])
    assert results == ["ok", "ok"]
    assert [n.kind for n in notices] == ["waiting", "waiting"]
    assert all(n.provider == "gemini" and "AI service (Gemini)" in n.message for n in notices)


# --- throttling --------------------------------------------------------------------------------


def test_throttle_collapses_repeats_spaces_waiting_and_caps_the_total() -> None:
    now = [0.0]
    throttle = NoticeThrottle(repeat_s=60, waiting_s=60, limit=3, clock=lambda: now[0])

    def retry(msg: str) -> ProviderNotice:
        return ProviderNotice(kind="retry", provider="openai", message=msg)

    def waiting(msg: str) -> ProviderNotice:
        return ProviderNotice(kind="waiting", provider="openai", message=msg, level="info")

    assert throttle.admit(retry("A")) is not None
    assert throttle.admit(retry("A")) is None  # six parallel calls stalling together: one line
    assert throttle.admit(waiting("W 1 min")) is not None
    now[0] = 30
    assert throttle.admit(waiting("W 2 min")) is None  # another call's "still waiting" 30 s later
    now[0] = 61
    assert throttle.admit(retry("A")) is not None  # the same message again after a minute
    assert throttle.admit(waiting("W 3 min")) == waiting("W 3 min")  # "still waiting" does not use up the limit
    assert throttle.admit(retry("B")) is not None  # the third warning
    closing = throttle.admit(retry("C"))
    assert closing is not None and closing.message == LIMIT_MESSAGE and closing.level == "info"
    now[0] = 500
    assert throttle.admit(retry("D")) is None  # nothing after the closing line
    assert throttle.admit(waiting("W 9 min")) is None


def test_still_waiting_lines_have_their_own_cap_and_never_hide_later_warnings() -> None:
    now = [0.0]
    throttle = NoticeThrottle(limit=3, waiting_limit=2, clock=lambda: now[0])
    kept = []
    for i in range(5):  # a long, healthy job with slow calls: five "still waiting" lines a minute apart
        now[0] += 61
        kept.append(throttle.admit(ProviderNotice(kind="waiting", provider="anthropic", message=f"W {i}",
                                                  level="info")))
    assert [n.message if n else None for n in kept] == ["W 0", "W 1", None, None, None]
    warnings = [throttle.admit(ProviderNotice(kind="retry", provider="openai", message=f"R {i}")) for i in range(3)]
    assert [(n.message, n.level) for n in warnings if n] == [("R 0", "warning"), ("R 1", "warning"),
                                                             ("R 2", "warning")]
    closing = throttle.admit(ProviderNotice(kind="rate_limited", provider="openai", message="R 3"))
    assert closing is not None and closing.message == LIMIT_MESSAGE
