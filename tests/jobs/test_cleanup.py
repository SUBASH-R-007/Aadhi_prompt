"""The ``cleanup`` job: deletes only eligible rows and blobs."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from sqlalchemy import select

from aadhi.db import session_scope
from aadhi.jobs.cleanup import GC_ASSET_KINDS, cleanup_job, ensure_cleanup_scheduled, run_cleanup
from aadhi.jobs.queue import add_event
from aadhi.models import AnalyticsEvent, Asset, AssetRef, Job, JobEvent, ProjectVersion, utcnow
from aadhi.storage import get_storage
from aadhi.storage.assets import storage_key_for

from ._helpers import add_job, get_job, make_project, make_user, make_version, update_job, wait_for

DAY = dt.timedelta(days=1)


def make_asset(kind: str, key: str, *, age_days: float, blob: bool = True) -> str:
    storage = get_storage()
    skey = storage_key_for(kind, key, "image/png")
    if blob:
        storage.put_bytes(skey, b"\x89PNG", "image/png")
    with session_scope() as db:
        db.add(Asset(key=key, kind=kind, storage_key=skey, mime="image/png", size_bytes=4,
                     created_at=utcnow() - age_days * DAY))
    return skey


def asset_keys() -> set[str]:
    with session_scope() as db:
        return set(db.execute(select(Asset.key)).scalars())


def event_jobs() -> set[int]:
    with session_scope() as db:
        return set(db.execute(select(JobEvent.job_id)).scalars())


@pytest.fixture()
def world(app_env):
    """Users/projects + a mix of eligible and protected rows."""
    uid = make_user()
    pid = make_project(uid)
    vid = make_version(pid)
    now = utcnow()
    jobs = {}
    for name, status, finished_days in [
        ("old_done", "succeeded", 40),
        ("old_failed", "failed", 31),
        ("recent_done", "succeeded", 5),
        ("old_running", "running", None),
    ]:
        jid = add_job()
        update_job(jid, status=status, finished_at=None if finished_days is None else now - finished_days * DAY,
                   created_at=now - 60 * DAY)
        with session_scope() as db:
            add_event(db, jid, "e", now=now - 60 * DAY)
        jobs[name] = jid
    with session_scope() as db:
        for days in (400, 366, 10):
            db.add(AnalyticsEvent(project_id=pid, viewer_id="v" * 16, event="session_start",
                                  created_at=now - days * DAY))
    blobs = {
        "tts-old-orphan": make_asset("tts", "tts-old-orphan", age_days=40),
        "manim-old-orphan": make_asset("manim", "manim-old-orphan", age_days=31),
        "intermediate-old-orphan": make_asset("intermediate", "intermediate-old-orphan", age_days=45),
        "image-old-no-blob": make_asset("image", "image-old-no-blob", age_days=45, blob=False),
        "tts-old-referenced": make_asset("tts", "tts-old-referenced", age_days=40),
        "tts-recent-orphan": make_asset("tts", "tts-recent-orphan", age_days=3),
        "upload-old-orphan": make_asset("upload", "upload-old-orphan", age_days=400),
        "source-old-orphan": make_asset("source", "source-old-orphan", age_days=400),
        "render-old-orphan": make_asset("render", "render-old-orphan", age_days=400),
        "figure-old-orphan": make_asset("figure", "figure-old-orphan", age_days=400),
        "tts-in-manifest": make_asset("tts", "tts-in-manifest", age_days=40),
        "video-in-timeline-url": make_asset("video", "video-in-timeline-url", age_days=40),
    }
    with session_scope() as db:
        db.add(AssetRef(project_id=pid, asset_key="tts-old-referenced"))
        version = db.get(ProjectVersion, vid)
        version.asset_manifest = {"scenes": {"s1": {"audio_key": "tts-in-manifest"}}}
        version.timeline = {"scenes": [{"media": {"url": f"/media/{blobs['video-in-timeline-url']}"}}]}
    return {"jobs": jobs, "blobs": blobs, "project_id": pid, "version_id": vid}


ELIGIBLE = {"tts-old-orphan", "manim-old-orphan", "intermediate-old-orphan", "image-old-no-blob"}


def test_cleanup_deletes_only_eligible_rows(world):
    storage = get_storage()
    before = asset_keys()
    out = run_cleanup()
    assert out["job_events"] == 2
    assert out["analytics_events"] == 2
    assert out["assets"] == len(ELIGIBLE)
    assert out["protected_by_documents"] == 2
    assert out["blobs"] == len(ELIGIBLE)  # deleting a missing blob is not an error
    jobs = world["jobs"]
    assert event_jobs() == {jobs["recent_done"], jobs["old_running"]}
    with session_scope() as db:
        assert len(db.execute(select(AnalyticsEvent)).all()) == 1
    assert asset_keys() == before - ELIGIBLE
    for key, skey in world["blobs"].items():
        if key in ELIGIBLE or key == "image-old-no-blob":
            assert not storage.exists(skey)
        else:
            assert storage.exists(skey), key
    # idempotent
    again = run_cleanup()
    assert (again["job_events"], again["analytics_events"], again["assets"]) == (0, 0, 0)


def test_dry_run_deletes_nothing(world):
    before = asset_keys()
    out = run_cleanup(dry_run=True)
    assert (out["job_events"], out["analytics_events"], out["assets"]) == (2, 2, len(ELIGIBLE))
    assert asset_keys() == before
    assert len(event_jobs()) == 4


def test_asset_gc_skipped_while_builds_are_running(world):
    jid = add_job("build_assets", version_id=world["version_id"])
    update_job(jid, status="running", locked_by="w:1:b:t", attempts=1)
    before = asset_keys()
    out = run_cleanup()
    assert out["assets_skipped"] == "active jobs" and asset_keys() == before
    assert out["job_events"] == 2  # retention still ran
    out = run_cleanup(force_assets=True)
    assert out["assets"] == len(ELIGIBLE)
    update_job(jid, status="succeeded")


def test_queued_and_awaiting_jobs_do_not_block_asset_gc(world):
    """Only RUNNING jobs touch assets: a plan review nobody approves must not disable GC forever."""
    review = add_job("generate_lecture", version_id=world["version_id"])
    update_job(review, status="awaiting_review", created_at=utcnow() - 90 * DAY)
    add_job("render_video", version_id=world["version_id"])  # queued, no render worker
    out = run_cleanup()
    assert "assets_skipped" not in out and out["assets"] == len(ELIGIBLE)


def test_running_guard_is_part_of_the_delete(world, monkeypatch):
    """A build that starts running after the candidate scan still blocks the delete (atomic guard)."""
    from aadhi.jobs import cleanup as cleanup_mod

    jid = add_job("build_assets", version_id=world["version_id"])
    real = cleanup_mod._referenced_in_documents

    def scan_then_build_starts(factory, candidates):
        found = real(factory, candidates)
        update_job(jid, status="running", locked_by="w:1:b:t", attempts=1)
        return found

    monkeypatch.setattr(cleanup_mod, "_referenced_in_documents", scan_then_build_starts)
    before = asset_keys()
    out = run_cleanup()
    assert out["assets_skipped"] == "active jobs" and out["assets"] == 0
    assert asset_keys() == before


def test_scan_documents_can_be_disabled(world):
    out = run_cleanup(scan_documents=False)
    assert out["assets"] == len(ELIGIBLE) + 2
    assert "tts-in-manifest" not in asset_keys()


def test_retention_settings_respected(world, app_env):
    settings = app_env.model_copy(update={"job_event_retention_days": 35, "analytics_retention_days": 5})
    out = run_cleanup(settings, dry_run=True)
    assert out["job_events"] == 1  # only the job finished 40 days ago
    assert out["analytics_events"] == 3


def test_media_in_a_library_is_kept_until_the_item_is_removed(world):
    """A generated picture in somebody's media library is not garbage, even when no lecture refers to it."""
    from aadhi.models import LibraryItem

    skey = make_asset("image", "image-in-library", age_days=60)
    owner = make_user("librarian")
    with session_scope() as db:
        db.add(LibraryItem(user_id=owner, asset_key="image-in-library", kind="image",
                           title="Kept", description="", keywords=[], source="generated", search_text=" kept "))
    assert run_cleanup(dry_run=True)["assets"] == len(ELIGIBLE)
    run_cleanup()
    assert "image-in-library" in asset_keys() and get_storage().exists(skey)
    with session_scope() as db:
        db.query(LibraryItem).filter(LibraryItem.asset_key == "image-in-library").delete()
    assert run_cleanup()["assets"] == 1
    assert "image-in-library" not in asset_keys() and not get_storage().exists(skey)


def test_gc_kinds_are_the_documented_ones():
    assert set(GC_ASSET_KINDS) == {"tts", "scene_audio", "manim", "image", "video", "poster", "screenshot",
                                   "intermediate"}


def test_cleanup_job_end_to_end(world, make_worker, isolated_registry):
    isolated_registry["cleanup"] = cleanup_job
    jid = add_job("cleanup", payload={"dry_run": False})
    make_worker(["cleanup"]).start()
    row = wait_for(lambda: (j := get_job(jid)).status == "succeeded" and j, timeout=10)
    assert row.result["deleted"]["assets"] == len(ELIGIBLE)
    assert row.result["deleted"]["job_events"] == 2
    assert row.progress == 1.0


def test_cleanup_job_respects_cancellation(world):
    from aadhi.jobs.base import JobCancelled

    def cancelled() -> None:
        raise JobCancelled()

    with pytest.raises(JobCancelled):
        run_cleanup(check_cancelled=cancelled)


def test_ensure_cleanup_scheduled(app_env):
    with session_scope() as db:
        job = ensure_cleanup_scheduled(db)
        assert job is not None and job.priority == 1000 and job.max_attempts == 1
    with session_scope() as db:
        assert ensure_cleanup_scheduled(db) is None
    update_job(job.id, status="succeeded", created_at=utcnow() - 2 * DAY)
    with session_scope() as db:
        assert ensure_cleanup_scheduled(db) is not None
        assert len(db.execute(select(Job).where(Job.kind == "cleanup")).all()) == 2


def test_ensure_cleanup_scheduled_is_atomic_across_threads(app_env):
    """Every worker process schedules at start: concurrent calls must enqueue exactly one job."""
    barrier = threading.Barrier(6)
    created: list[int | None] = []
    lock = threading.Lock()

    def schedule() -> None:
        barrier.wait(5)
        with session_scope() as db:
            job = ensure_cleanup_scheduled(db)
            job_id = None if job is None else job.id
        with lock:
            created.append(job_id)

    threads = [threading.Thread(target=schedule) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert len(created) == 6 and len([c for c in created if c is not None]) == 1
    with session_scope() as db:
        rows = db.execute(select(Job).where(Job.kind == "cleanup")).scalars().all()
    assert len(rows) == 1
    row = get_job(rows[0].id)
    assert (row.status, row.priority, row.max_attempts, row.payload, row.attempts) == ("queued", 1000, 1, {}, 0)
    assert (row.stage, row.progress, row.cost_usd, row.cancel_requested) == ("", 0.0, 0.0, False)


def test_cleanup_job_exits_when_another_cleanup_is_running(app_env, make_worker, isolated_registry):
    isolated_registry["cleanup"] = cleanup_job
    other = add_job("cleanup")
    update_job(other, status="running", locked_by="elsewhere:1:b:t", attempts=1, heartbeat_at=utcnow())
    jid = add_job("cleanup")
    make_worker(["cleanup"], run_reaper=False).start()
    row = wait_for(lambda: (j := get_job(jid)).status == "succeeded" and j, timeout=10)
    assert row.result == {"skipped": "another cleanup job is running", "running": [other]}
