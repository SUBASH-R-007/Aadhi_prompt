"""Process-wide heartbeat service and inline-worker helpers."""

from __future__ import annotations

import asyncio
import time

import pytest

from aadhi.db import session_scope
from aadhi.jobs.base import job_handler
from aadhi.jobs.context import DBJobContext
from aadhi.jobs.heartbeat import HeartbeatService
from aadhi.jobs.inline import notify_inline_workers, start_inline_workers, stop_inline_workers
from aadhi.jobs.lease import Lease
from aadhi.jobs.queue import claim_next, request_cancel

from ._helpers import add_job, ago, get_job, update_job, wait_for


def claimed(app_env, worker_id: str = "w1") -> DBJobContext:
    add_job()
    with session_scope() as db:
        job_id, attempt = claim_next(db, worker_id, ["t_job"])
    return DBJobContext.open(Lease(job_id, worker_id, attempt), app_env)


def test_beat_once_refreshes_heartbeat_and_cancel_flag(app_env):
    hb = HeartbeatService()
    ctx = claimed(app_env)
    update_job(ctx.job_id, heartbeat_at=ago(500))
    hb.register(ctx)
    assert hb.beat_once() == 1
    assert get_job(ctx.job_id).heartbeat_at > ago(5).replace(tzinfo=None)
    assert ctx.cancel_reason is None
    with session_scope() as db:
        request_cancel(db, ctx.job_id)
    hb.beat_once()
    assert ctx.cancel_reason == "cancelled"


def test_lost_lease_detected_by_heartbeat_and_flags(app_env):
    hb = HeartbeatService()
    a = claimed(app_env, "w1")
    b = claimed(app_env, "w2")
    hb.register(a)
    hb.register(b)
    update_job(a.job_id, locked_by="thief")
    hb.refresh_flags_once()
    assert a.cancel_reason == "lease_lost" and b.cancel_reason is None
    update_job(b.job_id, attempts=99)
    assert hb.beat_once() == 0
    assert b.cancel_reason == "lease_lost"
    hb.unregister(a)
    hb.unregister(b)
    assert hb.beat_once() == 0 and hb.holders() == []


def test_thread_lifecycle_is_reference_counted(app_env):
    hb = HeartbeatService()
    hb.acquire(heartbeat_interval=0.05, flag_interval=0.02)
    hb.acquire(heartbeat_interval=1.0)
    assert hb.running and hb.heartbeat_interval == 0.05
    ctx = claimed(app_env)
    update_job(ctx.job_id, heartbeat_at=ago(500))
    hb.register(ctx)
    wait_for(lambda: get_job(ctx.job_id).heartbeat_at > ago(5).replace(tzinfo=None), timeout=3)
    hb.release()
    assert hb.running
    hb.release()
    assert not hb.running
    hb.release()  # extra release is harmless
    hb.acquire()
    assert hb.running
    hb.release()


def test_heartbeat_thread_survives_db_errors(app_env, monkeypatch):
    from aadhi.jobs import heartbeat as hb_mod

    hb = HeartbeatService()
    ctx = claimed(app_env)
    hb.register(ctx)
    calls = {"n": 0}
    real = hb_mod.session_scope

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("db down")
        return real()

    monkeypatch.setattr(hb_mod, "session_scope", flaky)
    hb.acquire(heartbeat_interval=0.05, flag_interval=0.02)
    try:
        wait_for(lambda: calls["n"] >= 4, timeout=3)
        assert hb.running and ctx.cancel_reason is None
    finally:
        hb.release()


def test_inline_workers(app_env):
    assert start_inline_workers(app_env) is None  # WORKER_MODE=external in tests
    stop_inline_workers(None)
    with pytest.raises(TypeError):
        stop_inline_workers(object())

    @job_handler("t_inline")
    async def handler(ctx):
        await asyncio.sleep(0)
        return {"inline": True}

    settings = app_env.model_copy(
        update={"worker_mode": "inline", "worker_concurrency": 2, "worker_kinds": ["t_inline"],
                "job_poll_interval_seconds": 0.02}
    )
    handle = start_inline_workers(settings)
    try:
        assert handle.concurrency == 2 and handle.kinds == ["t_inline"]
        jid = add_job("t_inline")
        notify_inline_workers()
        wait_for(lambda: get_job(jid).status == "succeeded", timeout=10)
        assert get_job(jid).result == {"inline": True}
    finally:
        started = time.monotonic()
        stop_inline_workers(handle)
        stop_inline_workers(handle)  # idempotent
    assert time.monotonic() - started < 10
    assert not handle.is_running
