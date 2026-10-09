"""Worker: threads, claiming, outcome mapping, cancellation, fencing, recovery, shutdown."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import select, update

from aadhi.db import session_scope
from aadhi.jobs import context as context_mod
from aadhi.jobs.base import AwaitingReview, FatalJobError, JobCancelled, RetryableJobError, job_handler
from aadhi.jobs.heartbeat import heartbeat_service
from aadhi.jobs.lease import HOSTNAME
from aadhi.jobs.queue import complete_review, request_cancel
from aadhi.jobs.worker import Worker
from aadhi.models import Job, UsageEvent
from aadhi.providers.base import Usage

from ._helpers import add_job, ago, get_job, job_events, update_job, wait_for


def wait_status(job_id: int, *statuses: str, timeout: float = 10.0) -> Job:
    return wait_for(lambda: (j := get_job(job_id)).status in statuses and j, timeout=timeout)


def test_thirty_jobs_four_threads_each_claimed_once(make_worker):
    seen: list[tuple[int, str]] = []
    lock = threading.Lock()

    @job_handler("t_count")
    async def handler(ctx):
        with lock:
            seen.append((ctx.job_id, threading.current_thread().name))
        ctx.progress("work", 0.5, f"job {ctx.job_id}")
        await asyncio.sleep(0.01)
        return {"n": ctx.job_id}

    ids = [add_job("t_count") for _ in range(30)]
    worker = make_worker(["t_count"], concurrency=4)
    worker.start()
    for jid in ids:
        wait_status(jid, "succeeded", timeout=20)
    worker.stop()
    assert sorted(j for j, _ in seen) == sorted(ids)
    assert len({j for j, _ in seen}) == 30
    assert len({t for _, t in seen}) >= 2
    for jid in ids:
        row = get_job(jid)
        assert (row.attempts, row.result, row.progress) == (1, {"n": jid}, 1.0)


def test_kinds_filter_and_missing_handlers(make_worker):
    @job_handler("t_a")
    async def a(ctx):
        return None

    @job_handler("t_b")
    async def b(ctx):  # pragma: no cover - must never run
        raise AssertionError

    ja, jb, jc = add_job("t_a"), add_job("t_b"), add_job("t_unregistered")
    worker = make_worker(["t_a", "t_unregistered"])
    assert worker.resolve_kinds() == ["t_a"]
    worker.start()
    wait_status(ja, "succeeded")
    time.sleep(0.2)
    assert get_job(jb).status == "queued" and get_job(jc).status == "queued"


def test_priority_order_single_thread(make_worker):
    order: list[int] = []

    @job_handler("t_prio")
    async def handler(ctx):
        order.append(ctx.payload["p"])

    for p in (100, 5, 50, 5, 1):
        add_job("t_prio", priority=p, payload={"p": p})
    worker = make_worker(["t_prio"])
    assert worker.run_until_idle(timeout=10)
    assert order == [1, 5, 5, 50, 100]


def test_awaiting_review_stores_state(make_worker):
    @job_handler("t_review")
    async def handler(ctx):
        ctx.progress("plan", 0.3, "Planned")
        raise AwaitingReview("Plan ready for review", {"plan": {"scenes": 3}, "stage": "plan"})

    jid = add_job("t_review")
    make_worker(["t_review"]).start()
    row = wait_status(jid, "awaiting_review")
    assert row.result == {"plan": {"scenes": 3}, "stage": "plan"}
    assert row.message == "Plan ready for review" and row.locked_by is None
    with session_scope() as db:
        assert complete_review(db, jid).status == "succeeded"


def test_budget_exceeded_via_record_usage(make_worker, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.4)

    @job_handler("t_spend")
    async def handler(ctx):
        for _ in range(10):
            ctx.record_usage(Usage(provider="fake", model="m", operation="llm", input_tokens=10))
            await asyncio.sleep(0)
        return {"never": True}

    jid = add_job("t_spend", payload={"budget_usd": 1.0}, max_attempts=3)
    make_worker(["t_spend"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "budget" and row.attempts == 1  # never retried
    assert row.cost_usd == pytest.approx(1.2)
    with session_scope() as db:
        assert len(db.execute(select(UsageEvent).where(UsageEvent.job_id == jid)).all()) == 3


def test_budget_error_wrapped_by_provider_still_fails_with_budget(make_worker, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 2.0)

    @job_handler("t_wrap")
    async def handler(ctx):
        try:
            ctx.record_usage(Usage(provider="fake", model="m", operation="llm"))
        except Exception as exc:  # a provider that wraps every error
            raise RuntimeError("provider failed") from exc

    jid = add_job("t_wrap", payload={"budget_usd": 1.0}, max_attempts=3)
    make_worker(["t_wrap"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "budget" and row.attempts == 1


def test_fatal_error_is_not_retried(make_worker):
    @job_handler("t_fatal")
    async def handler(ctx):
        raise FatalJobError("The PDF has no text", code="invalid_source")

    jid = add_job("t_fatal", max_attempts=3)
    make_worker(["t_fatal"]).start()
    row = wait_status(jid, "failed")
    assert (row.error, row.error_code, row.attempts) == ("The PDF has no text", "invalid_source", 1)


def test_retryable_error_backs_off_then_succeeds(make_worker):
    attempts: list[float] = []

    @job_handler("t_flaky")
    async def handler(ctx):
        attempts.append(time.monotonic())
        if ctx.attempt == 1:
            raise RetryableJobError("upstream 503")
        return {"attempt": ctx.attempt}

    jid = add_job("t_flaky", max_attempts=3)
    make_worker(["t_flaky"], retry_backoff_base=0.4).start()
    row = wait_status(jid, "succeeded")
    assert row.attempts == 2 and row.result == {"attempt": 2} and row.error is None
    assert attempts[1] - attempts[0] >= 0.35  # waited for run_after
    retry_events = [e for e in job_events(jid) if e.level == "warning"]
    assert retry_events and retry_events[0].data["retry_in_seconds"] == pytest.approx(0.4)
    assert "upstream 503" in retry_events[0].message


def test_exhausted_attempts_fail_with_redacted_traceback(make_worker):
    @job_handler("t_boom")
    async def handler(ctx):
        raise ValueError("boom while calling https://api?key=SUPERSECRET42")

    jid = add_job("t_boom", max_attempts=2)
    make_worker(["t_boom"]).start()
    row = wait_status(jid, "failed")
    assert row.attempts == 2 and row.error_code == "error"
    assert row.error.startswith("ValueError: boom") and "SUPERSECRET42" not in row.error
    tracebacks = [e.data.get("traceback", "") for e in job_events(jid)]
    assert all("SUPERSECRET42" not in t for t in tracebacks)
    assert any("Traceback" in t and "ValueError" in t for t in tracebacks)


def test_retryable_error_exhausted_code(make_worker):
    @job_handler("t_retry_out")
    async def handler(ctx):
        raise RetryableJobError("still down")

    jid = add_job("t_retry_out", max_attempts=1)
    make_worker(["t_retry_out"]).start()
    assert wait_status(jid, "failed").error_code == "retries_exhausted"


def test_cancel_running_job_cooperatively(make_worker):
    started = threading.Event()

    @job_handler("t_loop")
    async def handler(ctx):
        started.set()
        while True:
            ctx.check_cancelled()
            await asyncio.sleep(0.01)

    jid = add_job("t_loop")
    make_worker(["t_loop"]).start()
    assert started.wait(5)
    with session_scope() as db:
        request_cancel(db, jid)
    row = wait_status(jid, "cancelled")
    assert row.error_code == "cancelled" and row.locked_by is None
    assert [e.message for e in job_events(jid)][-1] == "Cancelled"


def test_cancel_handler_that_ignores_it_is_interrupted(make_worker):
    started = threading.Event()

    @job_handler("t_deaf")
    async def handler(ctx):
        started.set()
        await asyncio.sleep(60)

    jid = add_job("t_deaf")
    make_worker(["t_deaf"], cancel_grace_seconds=0.2).start()
    assert started.wait(5)
    with session_scope() as db:
        request_cancel(db, jid)
    wait_status(jid, "cancelled", timeout=5)


def test_lease_lost_worker_leaves_job_to_new_owner(make_worker):
    started = threading.Event()
    stolen = threading.Event()
    outcome: dict[str, str] = {}

    @job_handler("t_steal")
    async def handler(ctx):
        ctx.progress("a", 0.1, "before")
        await ctx.flush()
        started.set()
        await asyncio.to_thread(stolen.wait, 5)
        try:
            while True:
                ctx.progress("b", 0.9, "after steal")
                ctx.check_cancelled()
                await asyncio.sleep(0.01)
        except JobCancelled as exc:
            outcome["reason"] = exc.reason
            raise

    jid = add_job("t_steal")
    worker = make_worker(["t_steal"])
    worker.start()
    assert started.wait(5)
    with session_scope() as db:
        db.execute(update(Job).where(Job.id == jid).values(locked_by="thief:1:x:y", attempts=Job.attempts + 1))
    stolen.set()
    wait_for(lambda: not worker.active_job_ids(), timeout=5)
    assert outcome == {"reason": "lease_lost"}
    row = get_job(jid)
    assert (row.status, row.locked_by, row.stage, row.message) == ("running", "thief:1:x:y", "a", "before")
    assert [e.message for e in job_events(jid)] == ["before"]


def test_restart_recovery_requeues_and_runs_abandoned_job(make_worker):
    import psutil

    dead_pid = next(p for p in range(4_000_000, 4_100_000, 4) if not psutil.pid_exists(p))

    @job_handler("t_recover")
    async def handler(ctx):
        return {"attempt": ctx.attempt}

    jid = add_job("t_recover", max_attempts=2)
    update_job(jid, status="running", attempts=1, locked_by=f"{HOSTNAME}:{dead_pid}:0123456789ab:old-thread",
               locked_at=ago(1), heartbeat_at=ago(1))
    make_worker(["t_recover"]).start()
    row = wait_status(jid, "succeeded")
    assert row.result == {"attempt": 2}
    assert any("worker restarted" in e.message for e in job_events(jid))


def test_reaper_requeues_stale_job(make_worker, monkeypatch):
    @job_handler("t_stale")
    async def handler(ctx):
        return {"ok": True}

    jid = add_job("t_stale", max_attempts=3)
    update_job(jid, status="running", attempts=1, locked_by="elsewhere:1:b:t", locked_at=ago(9999), heartbeat_at=None)
    make_worker(["t_stale"]).start()
    row = wait_status(jid, "succeeded")
    assert row.attempts == 2


def test_heartbeat_thread_keeps_lease_fresh(make_worker):
    release = threading.Event()

    @job_handler("t_long")
    async def handler(ctx):
        await asyncio.to_thread(release.wait, 5)

    jid = add_job("t_long")
    worker = make_worker(["t_long"], heartbeat_interval=0.1, flush_interval=10.0)
    worker.start()
    first = wait_for(lambda: get_job(jid).heartbeat_at, timeout=5)
    wait_for(lambda: get_job(jid).heartbeat_at > first, timeout=5)
    assert heartbeat_service().running
    release.set()
    wait_status(jid, "succeeded")


def test_graceful_stop_lets_running_jobs_finish(make_worker):
    started = threading.Event()

    @job_handler("t_slow")
    async def handler(ctx):
        started.set()
        await asyncio.sleep(0.3)
        return {"done": True}

    jid = add_job("t_slow")
    worker = make_worker(["t_slow"])
    worker.start()
    assert started.wait(5)
    assert worker.stop(timeout=5)
    assert get_job(jid).status == "succeeded"
    assert not worker.is_running


def test_stop_timeout_releases_running_job(make_worker):
    started = threading.Event()

    @job_handler("t_stuck")
    async def handler(ctx):
        started.set()
        await asyncio.sleep(60)

    jid = add_job("t_stuck", max_attempts=1)
    worker = make_worker(["t_stuck"], cancel_grace_seconds=0.2)
    worker.start()
    assert started.wait(5)
    assert worker.stop(timeout=0.2)
    row = get_job(jid)
    assert (row.status, row.max_attempts, row.locked_by) == ("queued", 2, None)  # attempt refunded


def test_claimed_but_not_started_job_is_released(app_env):
    from aadhi.jobs.lease import Lease
    from aadhi.jobs.queue import claim_next

    @job_handler("t_never")
    async def handler(ctx):  # pragma: no cover - must not run
        raise AssertionError

    jid = add_job("t_never")
    worker = Worker(app_env, kinds=["t_never"], concurrency=1)
    worker.kinds = worker.resolve_kinds()
    worker._stopping.set()
    with session_scope() as db:
        _, attempt = claim_next(db, "w-test", ["t_never"])
    worker.process(Lease(jid, "w-test", attempt))
    row = get_job(jid)
    assert row.status == "queued" and row.max_attempts == 3


def test_non_dict_result_is_wrapped(make_worker):
    @job_handler("t_scalar")
    async def handler(ctx):
        return 42

    jid = add_job("t_scalar")
    make_worker(["t_scalar"]).start()
    assert wait_status(jid, "succeeded").result == {"value": 42}


def test_each_job_runs_in_a_fresh_event_loop(make_worker):
    loops: list[int] = []

    @job_handler("t_loopid")
    async def handler(ctx):
        loops.append(id(asyncio.get_running_loop()))
        assert threading.current_thread() is not threading.main_thread()

    for _ in range(3):
        add_job("t_loopid")
    assert make_worker(["t_loopid"]).run_until_idle(timeout=10)
    assert len(loops) == 3


def test_broken_handler_module_does_not_hide_the_others(app_env, monkeypatch, caplog):
    """base.load_handlers stops at the first failing module; the worker imports each on its own."""
    import sys

    from aadhi.jobs import base

    monkeypatch.delitem(sys.modules, "tests.jobs._good_handlers", raising=False)
    monkeypatch.setattr(
        base,
        "HANDLER_MODULES",
        ("tests.jobs._broken_handlers", "aadhi.not_installed_handlers", "tests.jobs._good_handlers"),
    )
    with caplog.at_level("ERROR", logger="aadhi.jobs.worker"):
        kinds = Worker(app_env, kinds=["t_good", "cleanup"]).resolve_kinds()
    assert kinds == ["t_good"]
    assert "tests.jobs._broken_handlers" in caplog.text
    assert "not_installed_handlers" not in caplog.text  # absent modules are not errors


def test_run_until_idle_times_out_when_queue_never_drains(make_worker):
    release = threading.Event()

    @job_handler("t_block")
    async def handler(ctx):
        await asyncio.to_thread(release.wait, 3)

    add_job("t_block")
    worker = make_worker(["t_block"])
    timer = threading.Timer(0.8, release.set)
    timer.start()
    try:
        assert worker.run_until_idle(timeout=0.3) is False
    finally:
        timer.cancel()
        release.set()


def test_maintenance_schedules_cleanup(make_worker, isolated_registry):
    from aadhi.jobs.cleanup import cleanup_job

    isolated_registry["cleanup"] = cleanup_job
    worker = make_worker(["cleanup"], schedule_cleanup=True)
    worker.start()

    def cleanup_jobs():
        with session_scope() as db:
            return db.execute(select(Job).where(Job.kind == "cleanup")).scalars().all()

    jobs = wait_for(cleanup_jobs, timeout=5)
    row = wait_status(jobs[0].id, "succeeded")
    assert "deleted" in row.result
    assert len(cleanup_jobs()) == 1  # scheduled once per interval


def test_handler_raised_lease_lost_with_valid_lease_is_finalised(make_worker):
    """e.g. render_video's own compare-and-set lost a race: the job must not linger as running."""

    @job_handler("t_superseded")
    async def handler(ctx):
        raise JobCancelled("render row changed while rendering", reason="lease_lost")

    jid = add_job("t_superseded")
    make_worker(["t_superseded"]).start()
    row = wait_status(jid, "cancelled")
    assert (row.error_code, row.error) == ("lease_lost", "render row changed while rendering")


def test_handler_raised_cancel_without_request(make_worker):
    @job_handler("t_selfcancel")
    async def handler(ctx):
        raise JobCancelled("nothing to do")

    jid = add_job("t_selfcancel")
    make_worker(["t_selfcancel"]).start()
    row = wait_status(jid, "cancelled")
    assert row.error_code == "cancelled" and row.error is None


# --- review round 2: failure modes ----------------------------------------------------------------


@pytest.mark.parametrize("exc_type", [SystemExit, KeyboardInterrupt])
def test_base_exception_in_handler_is_a_failure_not_a_release(make_worker, exc_type):
    """A handler-raised SystemExit must never refund attempts (that was a hot retry loop)."""
    runs: list[int] = []

    @job_handler("t_exit")
    async def handler(ctx):
        runs.append(ctx.attempt)
        raise exc_type(2)

    jid = add_job("t_exit", max_attempts=2)
    make_worker(["t_exit"]).start()
    row = wait_status(jid, "failed")
    time.sleep(0.2)
    assert runs == [1, 2]
    assert (row.attempts, row.max_attempts, row.error_code) == (2, 2, "error")


def test_handler_claimed_shutdown_without_stop_is_not_released(make_worker):
    runs: list[int] = []

    @job_handler("t_fake_shutdown")
    async def handler(ctx):
        runs.append(ctx.attempt)
        raise JobCancelled("worker shutting down", reason="shutdown")

    jid = add_job("t_fake_shutdown", max_attempts=2)
    make_worker(["t_fake_shutdown"]).start()
    row = wait_status(jid, "failed")
    assert runs == [1, 2] and row.max_attempts == 2 and row.error_code == "unexpected_cancel"


def test_unexpected_cancelled_error_is_retried_then_failed(make_worker):
    """A library cancelling its own inner task is not a user cancellation."""
    runs: list[int] = []

    @job_handler("t_inner_cancel")
    async def handler(ctx):
        runs.append(ctx.attempt)
        inner = asyncio.ensure_future(asyncio.sleep(10))
        asyncio.get_running_loop().call_later(0.01, inner.cancel)
        await inner

    jid = add_job("t_inner_cancel", max_attempts=2)
    make_worker(["t_inner_cancel"]).start()
    row = wait_status(jid, "failed")
    assert runs == [1, 2]
    assert (row.error_code, row.cancel_requested) == ("unexpected_cancel", False)


def test_outcome_write_is_retried_while_the_lease_keeps_heartbeating(make_worker, monkeypatch):
    """A DB blip while recording 'succeeded' must not leave a finished job running without heartbeat."""
    from sqlalchemy.exc import OperationalError

    from aadhi.jobs import worker as worker_mod

    runs: list[int] = []
    failing_until = {"t": 0.0}
    first_failure = threading.Event()
    real_finish = worker_mod.finish_job

    def flaky_finish(db, lease, result, **kw):
        if time.monotonic() < failing_until["t"]:
            first_failure.set()
            raise OperationalError("UPDATE jobs", {}, Exception("server closed the connection unexpectedly"))
        return real_finish(db, lease, result, **kw)

    monkeypatch.setattr(worker_mod, "finish_job", flaky_finish)

    @job_handler("t_blip")
    async def handler(ctx):
        runs.append(ctx.attempt)
        failing_until["t"] = time.monotonic() + 2.0
        return {"ok": True}

    jid = add_job("t_blip")
    make_worker(["t_blip"], heartbeat_interval=0.1).start()
    assert first_failure.wait(5)
    seen = get_job(jid).heartbeat_at
    time.sleep(0.6)
    mid = get_job(jid)
    assert mid.status == "running" and mid.heartbeat_at > seen  # still heartbeating
    row = wait_status(jid, "succeeded", timeout=10)
    assert row.result == {"ok": True} and runs == [1]


def test_unstorable_result_fails_instead_of_staying_running(make_worker, monkeypatch):
    from sqlalchemy.exc import DataError

    from aadhi.jobs import worker as worker_mod

    def rejecting_finish(db, lease, result, **kw):
        raise DataError("UPDATE jobs", {}, Exception("invalid input syntax for type json"))

    monkeypatch.setattr(worker_mod, "finish_job", rejecting_finish)

    @job_handler("t_unstorable")
    async def handler(ctx):
        return {"big": "result"}

    jid = add_job("t_unstorable", max_attempts=3)
    make_worker(["t_unstorable"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "result_not_persisted" and row.attempts == 1 and row.locked_by is None


def test_non_finite_result_values_are_stored_as_null(make_worker):
    @job_handler("t_nan")
    async def handler(ctx):
        return {"ratio": float("nan"), "list": [float("inf"), 1]}

    jid = add_job("t_nan")
    make_worker(["t_nan"]).start()
    assert wait_status(jid, "succeeded").result == {"ratio": None, "list": [None, 1]}


def _api_cancel(job_id: int) -> None:
    with session_scope() as db:
        request_cancel(db, job_id)


def test_cancel_landing_before_a_handler_error_is_not_retried(make_worker):
    """The cancel flag was not refreshed yet when the handler failed: the retry must not happen."""
    runs: list[int] = []

    @job_handler("t_cancel_then_fail")
    async def handler(ctx):
        runs.append(ctx.attempt)
        await asyncio.to_thread(_api_cancel, ctx.job_id)
        raise RuntimeError("provider failed")

    jid = add_job("t_cancel_then_fail", max_attempts=3)
    make_worker(["t_cancel_then_fail"], heartbeat_interval=30, flag_interval=30, flush_interval=30).start()
    row = wait_status(jid, "cancelled")
    time.sleep(0.3)
    assert runs == [1] and get_job(jid).status == "cancelled" and row.error_code == "cancelled"


def test_requeued_job_with_pending_cancel_never_runs(make_worker):
    runs: list[int] = []

    @job_handler("t_flagged")
    async def handler(ctx):  # pragma: no cover - must not run
        runs.append(ctx.attempt)

    jid = add_job("t_flagged", max_attempts=3)
    update_job(jid, cancel_requested=True)  # e.g. requeued by the reaper just as the user cancelled
    make_worker(["t_flagged"]).start()
    wait_status(jid, "cancelled")
    assert runs == []


def _version(status: str) -> int:
    from ._helpers import make_project, make_user, make_version

    vid = make_version(make_project(make_user()))
    _set_version_status(vid, status)
    return vid


def _set_version_status(vid: int, status: str) -> None:
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        db.execute(update(ProjectVersion).where(ProjectVersion.id == vid).values(status=status))


def _version_status(vid: int) -> str:
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        return db.execute(select(ProjectVersion.status).where(ProjectVersion.id == vid)).scalar_one()


def test_cancel_during_planning_ends_cancelled_not_awaiting_review(make_worker):
    @job_handler("generate_lecture")
    async def handler(ctx):
        await asyncio.to_thread(_set_version_status, ctx.version_id, "awaiting_review")  # like the orchestrator
        await asyncio.to_thread(_api_cancel, ctx.job_id)
        raise AwaitingReview("Plan ready", {"plan": 1})

    vid = _version("generating")
    jid = add_job("generate_lecture", version_id=vid)
    make_worker(["generate_lecture"], heartbeat_interval=30, flag_interval=30, flush_interval=30).start()
    wait_status(jid, "cancelled")
    assert _version_status(vid) == "failed"


def test_job_without_handler_settles_its_version(app_env):
    from aadhi.jobs.lease import Lease
    from aadhi.jobs.queue import claim_next

    vid = _version("building")
    jid = add_job("build_assets", version_id=vid)
    worker = Worker(app_env, kinds=["build_assets"], concurrency=1)
    with session_scope() as db:
        _, attempt = claim_next(db, "w-test", ["build_assets"])
    worker.process(Lease(jid, "w-test", attempt))
    row = get_job(jid)
    assert (row.status, row.error_code) == ("failed", "no_handler")
    assert _version_status(vid) == "failed"


def test_transient_open_failure_is_retried(make_worker, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from aadhi.jobs import worker as worker_mod

    real_open = worker_mod.DBJobContext.open
    calls = {"n": 0}

    def flaky_open(lease, settings=None, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("SELECT", {}, Exception("database is locked"))
        return real_open(lease, settings, **kwargs)

    monkeypatch.setattr(worker_mod.DBJobContext, "open", staticmethod(flaky_open))

    @job_handler("t_open_retry")
    async def handler(ctx):
        return {"attempt": ctx.attempt}

    jid = add_job("t_open_retry", max_attempts=3)
    make_worker(["t_open_retry"]).start()
    row = wait_status(jid, "succeeded")
    assert row.result == {"attempt": 2}
    assert any("database is locked" in e.message for e in job_events(jid))


def test_transient_open_failure_without_attempts_left_fails(make_worker, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from aadhi.jobs import worker as worker_mod

    def broken_open(lease, settings=None, **kwargs):
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(worker_mod.DBJobContext, "open", staticmethod(broken_open))

    @job_handler("t_open_dead")
    async def handler(ctx):  # pragma: no cover - never opened
        return None

    jid = add_job("t_open_dead", max_attempts=1)
    make_worker(["t_open_dead"]).start()
    row = wait_status(jid, "failed")
    assert row.error_code == "error" and row.attempts == 1


def test_permanent_open_failure_fails_at_once(make_worker):
    @job_handler("t_bad_payload")
    async def handler(ctx):  # pragma: no cover - never opened
        return None

    jid = add_job("t_bad_payload", max_attempts=3, payload={"budget_usd": "lots"})
    make_worker(["t_bad_payload"]).start()
    row = wait_status(jid, "failed")
    assert (row.error_code, row.attempts) == ("invalid_job", 1)


def test_cancel_of_handler_awaiting_thread_work_is_prompt(make_worker):
    """Thread work (asyncio.to_thread) is abandoned on cancel, not awaited by the loop shutdown."""
    started = threading.Event()

    @job_handler("t_thread_wait")
    async def handler(ctx):
        started.set()
        await asyncio.to_thread(time.sleep, 6)

    jid = add_job("t_thread_wait")
    make_worker(["t_thread_wait"], cancel_grace_seconds=0.3).start()
    assert started.wait(5)
    t0 = time.monotonic()
    _api_cancel(jid)
    wait_status(jid, "cancelled", timeout=4)
    assert time.monotonic() - t0 < 3.0


def test_stop_is_bounded_when_handler_awaits_thread_work(make_worker):
    started = threading.Event()

    @job_handler("t_thread_stop")
    async def handler(ctx):
        started.set()
        await asyncio.to_thread(time.sleep, 6)

    jid = add_job("t_thread_stop", max_attempts=1)
    worker = make_worker(["t_thread_stop"], cancel_grace_seconds=0.3)
    worker.start()
    assert started.wait(5)
    t0 = time.monotonic()
    assert worker.stop(timeout=0.2)
    assert time.monotonic() - t0 < 3.0
    row = get_job(jid)
    assert (row.status, row.max_attempts) == ("queued", 2)  # released, attempt refunded


def test_heartbeat_interval_is_clamped_to_the_stale_window(app_env, caplog):
    settings = app_env.model_copy(update={"job_stale_seconds": 6})
    with caplog.at_level("WARNING", logger="aadhi.jobs.worker"):
        worker = Worker(settings, kinds=[], heartbeat_interval=10.0, flag_interval=5.0)
    assert worker.heartbeat_interval == pytest.approx(2.0) and worker.flag_interval == pytest.approx(2.0)
    assert worker.persist_timeout == pytest.approx(5.0)
    assert "JOB_STALE_SECONDS" in caplog.text
    normal = Worker(app_env, kinds=[], heartbeat_interval=10.0)
    assert normal.heartbeat_interval == 10.0 and normal.persist_timeout == pytest.approx(app_env.job_stale_seconds / 2)


def test_usage_reported_by_abandoned_thread_work_is_recorded(make_worker, monkeypatch):
    """Thread work abandoned on cancel may still report provider usage: it is billed, not lost."""
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.75)
    started, reported = threading.Event(), threading.Event()

    def slow_provider_call(ctx) -> None:
        started.set()
        time.sleep(1.0)
        ctx.record_usage(Usage(provider="fake", model="m", operation="llm", input_tokens=3))
        reported.set()

    @job_handler("t_orphan_usage")
    async def handler(ctx):
        await asyncio.to_thread(slow_provider_call, ctx)

    jid = add_job("t_orphan_usage")
    make_worker(["t_orphan_usage"], cancel_grace_seconds=0.1).start()
    assert started.wait(5)
    _api_cancel(jid)
    wait_status(jid, "cancelled", timeout=5)
    assert not reported.is_set()  # the outcome did not wait for the thread
    assert reported.wait(5)

    def usage_rows():
        with session_scope() as db:
            return db.execute(select(UsageEvent).where(UsageEvent.job_id == jid)).scalars().all()

    rows = wait_for(usage_rows, timeout=10)
    assert len(rows) == 1 and rows[0].cost_usd == 0.75 and rows[0].input_tokens == 3
    assert get_job(jid).status == "cancelled"
