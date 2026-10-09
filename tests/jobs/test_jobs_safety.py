"""Jobs safety: a running job generates paid media under a claim that names its lease, a waiting job
honours its own cancellation, and the cleanup job sweeps claims abandoned by crashed workers."""

from __future__ import annotations

import asyncio
import datetime as dt
import threading

from sqlalchemy import select

from aadhi.db import session_scope
from aadhi.jobs.base import job_handler
from aadhi.jobs.cleanup import EXPIRED_CLAIM_GRACE_SECONDS, run_cleanup
from aadhi.jobs.context import DBJobContext
from aadhi.jobs.lease import Lease
from aadhi.jobs.queue import request_cancel
from aadhi.models import AssetClaim, utcnow
from aadhi.storage import get_asset_store
from aadhi.storage.assets import ClaimOwner, Produced

from ._helpers import add_job, get_job, wait_for


def claim_rows() -> list[tuple[str, int | None, str | None, int | None]]:
    with session_scope() as db:
        return [(c.key, c.job_id, c.worker_id, c.attempt) for c in db.execute(select(AssetClaim)).scalars()]


def add_claim(key: str, *, expired_seconds_ago: float, job_id: int | None = None) -> None:
    now = utcnow()
    with session_scope() as db:
        db.add(AssetClaim(key=key, holder="h-" + key, job_id=job_id, worker_id="host:1:boot:t", attempt=1,
                          created_at=now - dt.timedelta(hours=3),
                          expires_at=now - dt.timedelta(seconds=expired_seconds_ago)))


def test_db_context_claim_owner_is_the_lease(app_env):
    ctx = DBJobContext(lease=Lease(5, "host:9:boot:thread", 2), kind="build_assets", settings=app_env)
    owner = ctx.claim_owner()
    assert owner == ClaimOwner(worker_id="host:9:boot:thread", job_id=5, attempt=2, check_cancelled=ctx.check_cancelled)


def test_worker_runs_handlers_as_the_claim_owner(make_worker):
    seen: list[tuple[str, int | None, str | None, int | None]] = []

    @job_handler("t_paid")
    async def handler(ctx):
        async def produce() -> Produced:
            seen.extend(claim_rows())  # inside the producer: the claim is held
            return Produced(data=b"\x00\x00\x00\x18ftypmp42", mime="video/mp4")

        async def in_subtask():  # tasks created by the handler inherit the owner
            return await ctx.assets.get_or_create(f"video-job-{ctx.job_id}", "video", produce)

        asset, created = await asyncio.ensure_future(in_subtask())
        return {"created": created}

    job_id = add_job("t_paid")
    worker = make_worker(["t_paid"])
    worker.start()
    row = wait_for(lambda: (j := get_job(job_id)).status in ("succeeded", "failed") and j)
    worker.stop()
    assert row.status == "succeeded" and row.result == {"created": True}
    assert len(seen) == 1
    key, claim_job, worker_id, attempt = seen[0]
    assert (key, claim_job, attempt) == (f"video-job-{job_id}", job_id, 1)
    assert worker_id and worker_id.endswith(":aadhi-worker-0")  # the job thread's lease identity
    assert claim_rows() == []


def test_a_job_waiting_for_another_workers_clip_can_be_cancelled(make_worker):
    """The holder is another live worker (claim not expired, no job lease to check): the waiting job
    stops promptly when the user cancels it, and leaves the holder's claim alone."""
    now = utcnow()
    with session_scope() as db:
        db.add(AssetClaim(key="video-busy", holder="other-process", job_id=None, worker_id="otherhost:1:x:t",
                          attempt=None, created_at=now, expires_at=now + dt.timedelta(minutes=10)))
    waiting = threading.Event()

    @job_handler("t_wait")
    async def handler(ctx):
        async def produce() -> Produced:  # pragma: no cover - must never run
            raise AssertionError("paid twice")

        waiting.set()
        await get_asset_store().get_or_create("video-busy", "video", produce)

    job_id = add_job("t_wait")
    worker = make_worker(["t_wait"])
    worker.start()
    assert waiting.wait(10)
    with session_scope() as db:
        request_cancel(db, job_id)
    row = wait_for(lambda: (j := get_job(job_id)).status in ("cancelled", "failed", "succeeded") and j, timeout=15)
    worker.stop()
    assert row.status == "cancelled"
    assert [r[0] for r in claim_rows()] == ["video-busy"]


def test_cleanup_sweeps_abandoned_claims_only(app_env):
    add_claim("video-abandoned", expired_seconds_ago=EXPIRED_CLAIM_GRACE_SECONDS + 60)
    add_claim("video-just-expired", expired_seconds_ago=30)  # a waiter takes these over itself
    now = utcnow()
    with session_scope() as db:
        db.add(AssetClaim(key="video-live", holder="h-live", job_id=None, worker_id="w", attempt=None,
                          created_at=now, expires_at=now + dt.timedelta(minutes=2)))
    dry = run_cleanup(app_env, dry_run=True)
    assert dry["asset_claims"] == 1 and len(claim_rows()) == 3
    out = run_cleanup(app_env)
    assert out["asset_claims"] == 1
    assert sorted(r[0] for r in claim_rows()) == ["video-just-expired", "video-live"]


def test_cleanup_without_the_claim_table_still_runs_the_other_steps(app_env):
    from sqlalchemy import text

    with session_scope() as db:
        db.execute(text("DROP TABLE asset_claims"))
    out = run_cleanup(app_env)
    assert out["asset_claims"] == 0 and "job_events" in out and "assets" in out
