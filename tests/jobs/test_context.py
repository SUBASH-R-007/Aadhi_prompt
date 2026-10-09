"""DBJobContext: buffering, throttling, fencing, cancellation flags, budgets."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from aadhi.db import session_scope
from aadhi.jobs import context as context_mod
from aadhi.jobs.base import BudgetExceeded, JobCancelled, JobContext
from aadhi.jobs.context import DBJobContext
from aadhi.jobs.lease import Lease
from aadhi.jobs.queue import claim_next, finish_job, request_cancel
from aadhi.models import Job, UsageEvent, utcnow
from aadhi.providers.base import Usage

from ._helpers import add_job, get_job, job_events, make_user, update_job, wait_for


def claimed_ctx(app_env, *, worker_id: str = "w1", flush_interval: float = 0.05, **job_kwargs) -> DBJobContext:
    add_job("t_job", **job_kwargs)
    with session_scope() as db:
        job_id, attempt = claim_next(db, worker_id, ["t_job"])
    return DBJobContext.open(Lease(job_id, worker_id, attempt), app_env, flush_interval=flush_interval)


def steal(job_id: int) -> None:
    with session_scope() as db:
        db.execute(update(Job).where(Job.id == job_id).values(locked_by="thief", attempts=Job.attempts + 1))


def usage(n: int = 1) -> Usage:
    return Usage(provider="fake", model="m", operation="llm", input_tokens=n, meta={"note": "key=SECRET9"})


def test_implements_protocol(app_env):
    ctx = claimed_ctx(app_env, payload={"x": 1})
    assert isinstance(ctx, JobContext)
    assert ctx.payload == {"x": 1} and ctx.kind == "t_job" and ctx.attempt == 1
    assert ctx.storage is ctx.assets.storage
    assert context_mod.shared_asset_store() is ctx.assets  # one AssetStore per process
    with ctx.session() as db:
        assert db.get(Job, ctx.job_id) is not None


def test_open_with_lost_lease_raises(app_env):
    jid = add_job()
    with session_scope() as db:
        _, attempt = claim_next(db, "w1", ["t_job"])
    with pytest.raises(JobCancelled) as info:
        DBJobContext.open(Lease(jid, "w1", attempt + 1), app_env)
    assert info.value.reason == "lease_lost"


def test_progress_is_monotonic_and_clamped(app_env):
    ctx = claimed_ctx(app_env)
    ctx.progress("plan", 0.5, "half")
    ctx.progress("plan", 0.3, "")  # lower -> stays 0.5, message kept
    assert ctx.flush_sync()
    row = get_job(ctx.job_id)
    assert (row.stage, row.progress, row.message) == ("plan", 0.5, "half")
    ctx.progress("script", float("nan"), "nan")
    ctx.progress("script", 7, "big")
    ctx.progress("script", -1, "neg")
    ctx.progress("script", "bogus", "bad type")  # type: ignore[arg-type]
    assert ctx.flush_sync()
    row = get_job(ctx.job_id)
    assert (row.stage, row.progress, row.message) == ("script", 1.0, "bad type")


def test_progress_events_are_throttled_and_logs_kept(app_env):
    ctx = claimed_ctx(app_env, flush_interval=0.3)
    ctx.start()
    try:
        ctx.progress("script", 0.0, "start")
        for i in range(200):
            ctx.progress("script", i / 400, f"Writing scene {i}/200")
        for i in range(5):
            ctx.log(f"log {i}", level="warning" if i == 2 else "info", n=i)
        wait_for(lambda: get_job(ctx.job_id).message == "Writing scene 199/200", timeout=5)
        wait_for(lambda: len([e for e in job_events(ctx.job_id) if e.level != "progress"]) == 5, timeout=5)
    finally:
        ctx.close()
    events = job_events(ctx.job_id)
    progress_events = [e for e in events if e.level == "progress"]
    assert 1 <= len(progress_events) <= 4  # coalesced, not 201 rows
    assert progress_events[-1].message == "Writing scene 199/200"
    logs = [e for e in events if e.level != "progress"]
    assert [e.message for e in logs] == [f"log {i}" for i in range(5)]
    assert logs[2].level == "warning" and logs[3].data == {"n": 3}
    assert get_job(ctx.job_id).progress == pytest.approx(199 / 400)


def test_stage_change_flushes_immediately(app_env):
    ctx = claimed_ctx(app_env, flush_interval=30.0)
    ctx.start()
    try:
        ctx.progress("ingest", 0.1, "Reading")
        wait_for(lambda: get_job(ctx.job_id).stage == "ingest", timeout=3)
        ctx.log("buffered only")  # no stage change: stays buffered for the 30 s window
        time.sleep(0.2)
        assert [e.message for e in job_events(ctx.job_id)] == ["Reading"]
        ctx.progress("plan", 0.2, "Planning")
        wait_for(lambda: get_job(ctx.job_id).stage == "plan", timeout=3)
        assert [e.message for e in job_events(ctx.job_id)] == ["Reading", "buffered only", "Planning"]
    finally:
        ctx.close()


def test_log_levels_and_redaction(app_env):
    ctx = claimed_ctx(app_env)
    ctx.log("calling https://x/y?key=SECRETKEY1 now", level="warn", url="https://x/?token=TOK123", obj=object())
    ctx.log("debug becomes info", level="debug")
    ctx.log("odd level", level="progress")
    ctx.progress("s", 0.1, "message with password=pw123456")
    assert ctx.flush_sync()
    events = job_events(ctx.job_id)
    assert [e.level for e in events] == ["warning", "info", "info", "progress"]
    assert "SECRETKEY1" not in events[0].message and "TOK123" not in events[0].data["url"]
    assert isinstance(events[0].data["obj"], str)
    assert "pw123456" not in get_job(ctx.job_id).message


def test_lease_stolen_writes_ignored(app_env):
    ctx = claimed_ctx(app_env)
    ctx.progress("plan", 0.2, "before steal")
    assert ctx.flush_sync()
    steal(ctx.job_id)
    ctx.progress("script", 0.9, "after steal")
    ctx.log("after steal")
    assert ctx.flush_sync() is False
    with pytest.raises(JobCancelled) as info:
        ctx.check_cancelled()
    assert info.value.reason == "lease_lost"
    assert ctx.lease_lost and not ctx.has_pending()
    row = get_job(ctx.job_id)
    assert (row.stage, row.message, row.locked_by) == ("plan", "before steal", "thief")
    assert [e.message for e in job_events(ctx.job_id)] == ["before steal"]
    with session_scope() as db:
        assert not finish_job(db, ctx.lease, {"done": True})
    assert get_job(ctx.job_id).status == "running"
    # further calls are no-ops; await flush raises lease_lost
    ctx.log("ignored")
    assert not ctx.has_pending()
    with pytest.raises(JobCancelled):
        asyncio.run(ctx.flush())


def test_assert_lease_fences_a_transaction(app_env):
    ctx = claimed_ctx(app_env)
    with session_scope() as db:
        ctx.assert_lease(db)
    steal(ctx.job_id)
    with pytest.raises(JobCancelled), session_scope() as db:
        ctx.assert_lease(db)
    assert ctx.cancel_reason == "lease_lost"


def test_cancel_flag_refreshed_by_flush(app_env):
    ctx = claimed_ctx(app_env)
    ctx.check_cancelled()
    with session_scope() as db:
        request_cancel(db, ctx.job_id)
    ctx.check_cancelled()  # no DB hit: not noticed yet
    ctx.progress("s", 0.1)
    assert ctx.flush_sync()
    with pytest.raises(JobCancelled) as info:
        ctx.check_cancelled()
    assert info.value.reason == "cancelled"


def test_shutdown_reason_priority(app_env):
    ctx = claimed_ctx(app_env)
    ctx.request_shutdown()
    assert ctx.cancel_reason == "shutdown"
    ctx.apply_heartbeat(True)
    assert ctx.cancel_reason == "cancelled"
    ctx.apply_heartbeat(None)
    assert ctx.cancel_reason == "lease_lost"


def test_record_usage_enforces_job_budget(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.6)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 1.0})
    assert ctx.record_usage(usage()) == 0.6
    with pytest.raises(BudgetExceeded):
        ctx.record_usage(usage())
    assert ctx.budget_error and "budget" in ctx.budget_error
    assert ctx.cost_usd == pytest.approx(1.2)
    assert ctx.flush_sync()
    assert get_job(ctx.job_id).cost_usd == pytest.approx(1.2)
    with session_scope() as db:
        rows = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).scalars().all()
    assert len(rows) == 2 and sum(r.cost_usd for r in rows) == pytest.approx(1.2)
    assert all("SECRET9" not in str(r.meta) for r in rows)  # provider meta is redacted before storage


def test_job_cost_seeded_from_previous_attempts(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.5)
    jid = add_job(payload={"budget_usd": 1.0})
    update_job(jid, cost_usd=0.75)
    with session_scope() as db:
        _, attempt = claim_next(db, "w1", ["t_job"])
    ctx = DBJobContext.open(Lease(jid, "w1", attempt), app_env)
    with pytest.raises(BudgetExceeded):
        ctx.record_usage(usage())
    ctx.flush_sync()
    assert get_job(jid).cost_usd == pytest.approx(1.25)


def test_default_job_budget_from_settings(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: app_env.max_cost_per_lecture_usd / 2 + 0.01)
    ctx = claimed_ctx(app_env)
    assert ctx.job_budget_usd == app_env.max_cost_per_lecture_usd
    ctx.record_usage(usage())
    with pytest.raises(BudgetExceeded):
        ctx.record_usage(usage())


def test_user_daily_budget_seeded_at_claim(app_env, monkeypatch):
    uid = make_user("dave", daily_budget_usd=1.0)
    with session_scope() as db:
        db.add(UsageEvent(user_id=uid, provider="fake", model="m", operation="llm", cost_usd=0.9, created_at=utcnow()))
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.2)
    ctx = claimed_ctx(app_env, user_id=uid, payload={"budget_usd": 100})
    assert ctx.user_budget_usd == pytest.approx(1.0)
    with pytest.raises(BudgetExceeded) as info:
        ctx.record_usage(usage())
    assert "Daily budget" in str(info.value)


def test_ensure_budget(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.4)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 1.0})
    ctx.ensure_budget(1.0)
    ctx.record_usage(usage())
    ctx.ensure_budget(0.6)
    with pytest.raises(BudgetExceeded):
        ctx.ensure_budget(0.61)
    assert ctx.budget_error is None  # a pre-check is not sticky


def test_record_usage_is_thread_safe(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.001)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 100})

    def spam() -> None:
        for _ in range(50):
            ctx.record_usage(usage())

    threads = [threading.Thread(target=spam) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ctx.flush_sync()
    with session_scope() as db:
        count = db.execute(select(func.count()).select_from(UsageEvent).where(UsageEvent.job_id == ctx.job_id))
        assert count.scalar_one() == 200
    assert get_job(ctx.job_id).cost_usd == pytest.approx(0.2)


def test_record_usage_from_another_event_loop_is_non_blocking(app_env, monkeypatch):
    """Providers call on_usage from their own loop/thread: it must return immediately."""
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.0)
    ctx = claimed_ctx(app_env)

    async def provider_like() -> float:
        start = time.perf_counter()
        for _ in range(100):
            ctx.record_usage(usage())
        return time.perf_counter() - start

    assert asyncio.run(provider_like()) < 0.5


def test_event_buffer_is_bounded(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "MAX_PENDING_EVENTS", 10)
    ctx = claimed_ctx(app_env)
    for i in range(25):
        ctx.log(f"e{i}")
    assert ctx.flush_sync()
    messages = [e.message for e in job_events(ctx.job_id)]
    assert messages[:10] == [f"e{i}" for i in range(15, 25)]
    assert messages[-1] == "15 log events were dropped (buffer full)"


def test_flush_failure_keeps_buffers(app_env, monkeypatch):
    """The database is unreachable: nothing can be written, so nothing is dropped."""
    ctx = claimed_ctx(app_env)
    ctx.log("survives")
    ctx.progress("s", 0.4, "p")
    real = context_mod.session_scope
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 3:  # batch, retry, row-by-row: all fail
            raise OperationalError("BEGIN", {}, Exception("database is locked"))
        return real()

    monkeypatch.setattr(context_mod, "session_scope", flaky)
    with pytest.raises(OperationalError):
        ctx.flush_sync()
    assert calls["n"] == 3 and ctx.has_pending()
    ctx.log("later")
    assert ctx.flush_sync()
    assert [e.message for e in job_events(ctx.job_id)] == ["survives", "p", "later"]
    assert get_job(ctx.job_id).progress == pytest.approx(0.4)


def test_single_transient_flush_error_is_retried_at_once(app_env, monkeypatch):
    ctx = claimed_ctx(app_env)
    ctx.log("first try fails")
    real = context_mod.session_scope
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OperationalError("BEGIN", {}, Exception("server closed the connection"))
        return real()

    monkeypatch.setattr(context_mod, "session_scope", flaky)
    assert ctx.flush_sync()
    assert [e.message for e in job_events(ctx.job_id)] == ["first try fails"] and ctx.rows_rejected == 0


@pytest.fixture()
def poison_rows(app_env):
    """Make the database reject any row whose parameters contain POISON (like Postgres rejecting a
    NaN in JSONB or a NUL in TEXT)."""
    import sqlite3

    from sqlalchemy import event

    from aadhi.db import get_engine

    engine = get_engine()

    def reject(conn, cursor, statement, parameters, context, executemany):
        if "POISON" in repr(parameters):
            raise sqlite3.IntegrityError("invalid input syntax for type json")

    event.listen(engine, "before_cursor_execute", reject)
    yield
    event.remove(engine, "before_cursor_execute", reject)


def test_poison_row_is_dropped_not_retried_forever(app_env, poison_rows, monkeypatch):
    """One rejected row must not block progress, other events, usage rows and the job cost."""
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.1)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 100})
    ctx.progress("plan", 0.3, "Planning")
    ctx.log("good 1")
    ctx.log("POISON event")
    for _ in range(6):
        ctx.record_usage(usage())
    ctx.log("good 2")
    assert ctx.flush_sync()
    assert ctx.rows_rejected == 1 and not ctx.has_pending()
    messages = [e.message for e in job_events(ctx.job_id)]
    assert messages[:3] == ["Planning", "good 1", "good 2"]
    assert "rejected by the database" in messages[-1]
    row = get_job(ctx.job_id)
    assert (row.stage, row.progress) == ("plan", pytest.approx(0.3))
    assert row.cost_usd == pytest.approx(0.6)
    with session_scope() as db:
        assert len(db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).all()) == 6
    ctx.log("after")  # later flushes are not blocked
    assert ctx.flush_sync()
    assert [e.message for e in job_events(ctx.job_id)][-1] == "after"


def test_rejected_usage_meta_still_records_the_billing_row(app_env, poison_rows, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.25)
    ctx = claimed_ctx(app_env)
    ctx.record_usage(Usage(provider="fake", model="m", operation="llm", input_tokens=7, meta={"x": "POISON"}))
    assert ctx.flush_sync()
    with session_scope() as db:
        row = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).scalar_one()
    assert (row.input_tokens, row.cost_usd, row.meta) == (7, 0.25, {})
    assert ctx.rows_rejected == 0


def test_values_the_database_would_reject_are_sanitised_when_buffered(app_env, monkeypatch):
    """NaN/inf (json.dumps emits them; Postgres JSONB rejects them) and NUL never reach the DB."""
    import sqlite3

    from sqlalchemy import event

    from aadhi.db import get_engine

    def reject(conn, cursor, statement, parameters, context, executemany):
        text = repr(parameters)
        if "NaN" in text or "Infinity" in text or "\\x00" in text:
            raise sqlite3.IntegrityError("rejected like Postgres would")

    event.listen(get_engine(), "before_cursor_execute", reject)
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.1)
    try:
        ctx = claimed_ctx(app_env)
        ctx.progress("st\x00age", 0.2, "mess\x00age")
        ctx.log("ratio\x00", ratio=float("nan"), nested={"inf": float("inf")})
        ctx.record_usage(Usage(provider="fa\x00ke", model="m", operation="llm", seconds=float("nan"),
                               input_tokens=float("inf"), meta={"ratio": float("nan")}))  # type: ignore[arg-type]
        assert ctx.flush_sync()
    finally:
        event.remove(get_engine(), "before_cursor_execute", reject)
    assert ctx.rows_rejected == 0
    row = get_job(ctx.job_id)
    assert (row.stage, row.message, row.cost_usd) == ("stage", "message", pytest.approx(0.1))
    log_event = [e for e in job_events(ctx.job_id) if e.level == "info"][0]
    assert log_event.message == "ratio" and log_event.data == {"ratio": None, "nested": {"inf": None}}
    with session_scope() as db:
        u = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).scalar_one()
    assert (u.provider, u.seconds, u.input_tokens, u.meta) == ("fake", 0.0, 0, {"ratio": None})


def test_usage_survives_lease_loss(app_env, monkeypatch):
    """Usage rows are billing facts: a lost lease must not under-count the user's daily spend."""
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 1.25)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 100})
    ctx.record_usage(usage())
    ctx.log("job-owned event")
    steal(ctx.job_id)
    assert ctx.flush_sync() is False
    assert ctx.cost_usd == pytest.approx(1.25)
    ctx.record_usage(usage())  # incurred after the loss (e.g. a provider call already in flight)
    assert ctx.has_pending()
    assert ctx.flush_sync() is False
    assert not ctx.has_pending()
    with session_scope() as db:
        rows = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).scalars().all()
    assert len(rows) == 2 and sum(r.cost_usd for r in rows) == pytest.approx(2.5)
    row = get_job(ctx.job_id)
    assert row.cost_usd == 0.0 and row.locked_by == "thief"  # job-owned (fenced) writes skipped
    assert job_events(ctx.job_id) == []


def test_usage_buffered_before_heartbeat_detects_loss_is_written(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.5)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 100})
    ctx.record_usage(usage())
    ctx.apply_heartbeat(None)  # heartbeat thread noticed the loss before any flush
    assert ctx.lease_lost and ctx.has_pending()
    ctx.start()
    try:
        wait_for(lambda: not ctx.has_pending(), timeout=5)  # the writer thread inserts it
    finally:
        ctx.close()
    with session_scope() as db:
        assert db.execute(select(UsageEvent.cost_usd).where(UsageEvent.job_id == ctx.job_id)).scalar_one() == 0.5


def test_orphaned_usage_kept_when_db_is_down(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "price_usage", lambda u, s: 0.5)
    ctx = claimed_ctx(app_env, payload={"budget_usd": 100})
    ctx.mark_lease_lost()
    ctx.record_usage(usage())
    real = context_mod.session_scope

    def down():
        raise OperationalError("BEGIN", {}, Exception("connection refused"))

    monkeypatch.setattr(context_mod, "session_scope", down)
    with pytest.raises(OperationalError):
        ctx.flush_sync()
    assert ctx.has_pending()
    monkeypatch.setattr(context_mod, "session_scope", real)
    with pytest.raises(JobCancelled):
        asyncio.run(ctx.flush())  # writes the usage, then reports the lost lease
    assert not ctx.has_pending()


def test_open_seeds_cancel_flag(app_env):
    """A requeued job whose cancel landed meanwhile must stop before doing any work."""
    jid = add_job()
    update_job(jid, cancel_requested=True)
    with session_scope() as db:
        _, attempt = claim_next(db, "w1", ["t_job"])
    ctx = DBJobContext.open(Lease(jid, "w1", attempt), app_env)
    with pytest.raises(JobCancelled) as info:
        ctx.check_cancelled()
    assert info.value.reason == "cancelled"


def test_writer_thread_survives_db_errors(app_env, monkeypatch):
    ctx = claimed_ctx(app_env, flush_interval=0.05)
    real = context_mod.session_scope
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("transient")
        return real()

    monkeypatch.setattr(context_mod, "session_scope", flaky)
    ctx.start()
    try:
        ctx.log("eventually written")
        wait_for(lambda: [e.message for e in job_events(ctx.job_id)] == ["eventually written"], timeout=5)
    finally:
        ctx.close()


def test_usage_rows_fallback_without_usage_module(app_env, monkeypatch):
    monkeypatch.setattr(context_mod, "_usage_api", lambda: None)
    assert context_mod.price_usage(usage(), app_env) == 0.0
    ctx = claimed_ctx(app_env)
    ctx.record_usage(usage(5))
    assert ctx.flush_sync()
    with session_scope() as db:
        row = db.execute(select(UsageEvent).where(UsageEvent.job_id == ctx.job_id)).scalar_one()
    assert row.input_tokens == 5 and row.cost_usd == 0.0 and "SECRET9" not in str(row.meta)


def test_pricing_errors_degrade_to_zero(app_env, monkeypatch):
    class Boom:
        @staticmethod
        def estimate_cost(u, s):
            raise ValueError("bad table")

    monkeypatch.setattr(context_mod, "_usage_api", lambda: type("A", (), {"pricing": Boom, "service": None})())
    assert context_mod.price_usage(usage(), app_env) == 0.0


def test_writer_flushes_within_interval(app_env):
    ctx = claimed_ctx(app_env, flush_interval=0.2)
    ctx.start()
    try:
        ctx.progress("", 0.3)  # fraction-only change: no event, row updated by the periodic flush
        wait_for(lambda: get_job(ctx.job_id).progress == pytest.approx(0.3), timeout=3)
        assert job_events(ctx.job_id) == []
    finally:
        ctx.close()
