"""Provider notices in the job log: retries and "still waiting" become job events at the current
stage (``DBJobContext.provider_notice``), throttled, and the worker installs the sink for the job."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.db import session_scope
from aadhi.jobs.base import job_handler
from aadhi.jobs.context import DBJobContext
from aadhi.jobs.lease import Lease
from aadhi.jobs.queue import claim_next
from aadhi.providers import _notify
from aadhi.providers._notify import LIMIT_MESSAGE, NoticeThrottle, ProviderNotice
from aadhi.providers._retry import ErrorInfo, call_with_retries, no_sleep
from aadhi.providers._stream import StreamStalled

from ._helpers import add_job, get_job, job_events, wait_for


def claimed_ctx(app_env) -> DBJobContext:
    add_job("t_job")
    with session_scope() as db:
        job_id, attempt = claim_next(db, "w1", ["t_job"])
    return DBJobContext.open(Lease(job_id, "w1", attempt), app_env, flush_interval=0.05)


def notice(message: str, kind: str = "retry", **kw) -> ProviderNotice:
    level = "info" if kind == "waiting" else "warning"
    return ProviderNotice(kind=kind, provider="openai", message=message, level=level, **kw)  # type: ignore[arg-type]


def test_notices_become_job_events_at_the_current_stage(app_env):
    ctx = claimed_ctx(app_env)
    ctx.progress("plan", 0.05, "Extracting the concepts to teach")
    ctx.provider_notice(notice("The AI service (OpenAI) didn't respond; trying again (attempt 2 of 4).",
                               attempt=2, max_attempts=4, delay_s=1.4))
    ctx.provider_notice(notice("Still waiting for the AI service (OpenAI) to answer (1 min so far).", kind="waiting",
                               attempt=2, max_attempts=4, elapsed_s=60.2))
    assert ctx.flush_sync()
    rows = [e for e in job_events(ctx.job_id) if e.level != "progress"]
    assert [(e.level, e.stage, e.message) for e in rows] == [
        ("warning", "plan", "The AI service (OpenAI) didn't respond; trying again (attempt 2 of 4)."),
        ("info", "plan", "Still waiting for the AI service (OpenAI) to answer (1 min so far)."),
    ]
    assert rows[0].data == {"notice": "retry", "provider": "openai", "attempt": 2, "max_attempts": 4,
                            "delay_seconds": 1.4}
    assert rows[1].data == {"notice": "waiting", "provider": "openai", "attempt": 2, "max_attempts": 4,
                            "elapsed_seconds": 60}
    assert get_job(ctx.job_id).message == "Extracting the concepts to teach"  # the status line is unchanged


def test_notices_are_throttled_and_capped(app_env):
    ctx = claimed_ctx(app_env)
    now = [0.0]
    ctx.notice_throttle = NoticeThrottle(limit=3, clock=lambda: now[0])
    for _ in range(6):  # six parallel calls stall together
        ctx.provider_notice(notice("The AI service (OpenAI) didn't respond; trying again (attempt 2 of 4)."))
    ctx.provider_notice(notice("Still waiting (1 min so far).", kind="waiting"))
    ctx.provider_notice(notice("Still waiting (1 min so far, another call).", kind="waiting"))
    for i in range(5):
        now[0] += 61
        ctx.provider_notice(notice(f"The AI service (OpenAI) is busy (rate limit) #{i}", kind="rate_limited"))
    assert ctx.flush_sync()
    messages = [e.message for e in job_events(ctx.job_id)]
    assert messages == [
        "The AI service (OpenAI) didn't respond; trying again (attempt 2 of 4).",
        "Still waiting (1 min so far).",
        "The AI service (OpenAI) is busy (rate limit) #0",
        "The AI service (OpenAI) is busy (rate limit) #1",  # "still waiting" lines do not use up the limit
        LIMIT_MESSAGE,
    ]


def test_many_still_waiting_lines_never_hide_a_later_retry(app_env):
    ctx = claimed_ctx(app_env)
    now = [0.0]
    ctx.notice_throttle = NoticeThrottle(limit=3, waiting_limit=2, clock=lambda: now[0])
    ctx.progress("script", 0.3, "Writing the scenes")
    for i in range(6):  # a long job with slow (healthy) calls
        now[0] += 61
        ctx.provider_notice(notice(f"Still waiting for the AI service (Claude) to answer ({i + 1} min so far).",
                                   kind="waiting"))
    ctx.progress("tts", 0.7, "Recording the narration")
    for i in range(4):
        now[0] += 61
        ctx.provider_notice(notice(f"The voice service (ElevenLabs) didn't respond; trying again #{i}"))
    assert ctx.flush_sync()
    rows = [(e.level, e.stage, e.message) for e in job_events(ctx.job_id) if e.level != "progress"]
    assert rows == [
        ("info", "script", "Still waiting for the AI service (Claude) to answer (1 min so far)."),
        ("info", "script", "Still waiting for the AI service (Claude) to answer (2 min so far)."),
        ("warning", "tts", "The voice service (ElevenLabs) didn't respond; trying again #0"),
        ("warning", "tts", "The voice service (ElevenLabs) didn't respond; trying again #1"),
        ("warning", "tts", "The voice service (ElevenLabs) didn't respond; trying again #2"),
        ("info", "tts", LIMIT_MESSAGE),
    ]


def test_secrets_are_redacted_and_a_lost_lease_records_nothing(app_env):
    ctx = claimed_ctx(app_env)
    ctx.provider_notice(notice(f"odd text {app_env.jwt_secret.get_secret_value()}"))
    assert ctx.flush_sync()
    assert app_env.jwt_secret.get_secret_value() not in job_events(ctx.job_id)[-1].message
    ctx.mark_lease_lost()
    ctx.provider_notice(notice("after the lease was lost"))
    assert all(e.message != "after the lease was lost" for e in job_events(ctx.job_id))


def _classify(exc: BaseException) -> ErrorInfo:
    return ErrorInfo(retry=isinstance(exc, TimeoutError))


def test_worker_reports_retries_and_slow_calls_of_its_jobs(make_worker, monkeypatch):
    monkeypatch.setattr(_notify, "WAITING_AFTER_S", 0.05)
    calls = 0

    async def flaky_ai_call() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise StreamStalled("first", 60)  # nothing came back within a minute
        await asyncio.sleep(0.25)  # the retry is slow but answers
        return "plan"

    @job_handler("t_ai")
    async def handler(ctx):
        ctx.progress("plan", 0.05, "Extracting the concepts to teach")
        # a child task (asyncio.gather in the pipeline) inherits the job's notice sink
        result = await asyncio.gather(call_with_retries(flaky_ai_call, classify=_classify, sleep=no_sleep,
                                                        label="openai", service="AI service (OpenAI)"))
        return {"result": result[0]}

    jid = add_job("t_ai")
    make_worker(["t_ai"]).start()
    wait_for(lambda: get_job(jid).status == "succeeded")
    rows = [(e.level, e.stage, e.message) for e in job_events(jid) if (e.data or {}).get("notice")]
    assert rows[0] == ("warning", "plan",
                       "The AI service (OpenAI) didn't respond within 1 min; trying again (attempt 2 of 4).")
    assert rows[1][:2] == ("info", "plan")
    assert rows[1][2].startswith("Still waiting for the AI service (OpenAI) to answer (attempt 2 of 4, 1 s so far).")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_without_a_job_nothing_is_reported():
    async def fn() -> str:
        return "ok"

    assert _notify.PROVIDER_NOTICE.get() is None  # API requests, CLI: no sink
    assert await call_with_retries(fn, classify=_classify) == "ok"
