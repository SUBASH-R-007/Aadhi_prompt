"""Queue: enqueue / claim / fenced transitions / cancel / retry / review / summaries / recovery."""

from __future__ import annotations

import datetime as dt
import threading

import pytest
from sqlalchemy import select, update

from aadhi.db import session_scope
from aadhi.jobs import queue
from aadhi.jobs.lease import BOOT_ID, HOSTNAME, Lease, make_worker_id, parse_worker_id
from aadhi.jobs.queue import (
    InvalidJobState,
    JobInProgress,
    JobNotFound,
    active_job_for_project,
    active_job_for_version,
    add_event,
    claim_next,
    complete_review,
    enqueue,
    fail_job,
    finish_job,
    job_summary,
    reap_stale,
    recover_own_host,
    release_job,
    request_cancel,
    requeue_job,
    retry_delay_seconds,
    retry_job,
)
from aadhi.models import Job, utcnow

from ._helpers import add_job, ago, get_job, job_events, make_project, make_user, make_version, update_job


def claim(worker_id: str = "w1", kinds=("t_job",)) -> tuple[int, int] | None:
    with session_scope() as db:
        return claim_next(db, worker_id, list(kinds))


@pytest.fixture()
def version_id(app_env) -> int:
    uid = make_user()
    pid = make_project(uid)
    return make_version(pid)


# --- enqueue --------------------------------------------------------------------------------------


def test_enqueue_defaults(app_env):
    payload = {"a": [1, 2], "when": dt.date(2026, 1, 2)}
    with session_scope() as db:
        job = enqueue(db, "t_job", payload=payload)
        job_id = job.id
        assert job.id is not None  # flushed
    payload["a"].append(3)  # caller mutations after enqueue do not leak in
    row = get_job(job_id)
    assert row.status == "queued"
    assert row.max_attempts == app_env.job_max_attempts
    assert row.attempts == 0
    assert row.priority == 100
    assert row.payload == {"a": [1, 2], "when": "2026-01-02"}
    assert row.run_after is not None and row.locked_by is None


def test_enqueue_validation(app_env):
    with session_scope() as db:
        with pytest.raises(ValueError):
            enqueue(db, "")
        with pytest.raises(ValueError):
            enqueue(db, "x" * 33)
        with pytest.raises(ValueError):
            enqueue(db, "t_job", max_attempts=0)
        job = enqueue(db, "t_job", max_attempts=5, priority=7)
        assert (job.max_attempts, job.priority) == (5, 7)


def test_enqueue_caller_controls_commit(app_env):
    with session_scope() as db:
        job_id = enqueue(db, "t_job").id
        db.rollback()
    with session_scope() as db:
        assert db.get(Job, job_id) is None


def test_job_in_progress_for_same_version(app_env, version_id):
    with session_scope() as db:
        first = enqueue(db, "build_assets", version_id=version_id).id
    with session_scope() as db:
        with pytest.raises(JobInProgress) as info:
            enqueue(db, "generate_lecture", version_id=version_id)
        assert info.value.job_id == first
        # the caller's transaction is still usable after the conflict
        render = enqueue(db, "render_video", version_id=version_id).id  # non-mutating kinds are not exclusive
    assert get_job(render).status == "queued"
    with session_scope() as db:
        request_cancel(db, first)
    with session_scope() as db:
        assert enqueue(db, "translate", version_id=version_id).id > first


def test_job_in_progress_race_hits_unique_index(app_env, version_id, monkeypatch):
    """The pre-check can lose a race; the partial unique index (ON CONFLICT) still decides."""
    with session_scope() as db:
        first = enqueue(db, "build_assets", version_id=version_id).id
    real = queue.active_job_for_version
    calls = {"n": 0}

    def racy(db, vid, **kw):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(db, vid, **kw)

    monkeypatch.setattr(queue, "active_job_for_version", racy)
    with session_scope() as db:
        with pytest.raises(JobInProgress) as info:
            enqueue(db, "regenerate_scene", version_id=version_id)
        assert info.value.job_id == first
        assert db.execute(select(Job.id)).scalars().all() == [first]  # nothing inserted, session healthy
    assert calls["n"] == 2


def test_awaiting_review_still_blocks_version(app_env, version_id):
    jid = add_job("generate_lecture", version_id=version_id)
    update_job(jid, status="awaiting_review")
    with session_scope() as db, pytest.raises(JobInProgress):
        enqueue(db, "build_assets", version_id=version_id)


# --- claim ----------------------------------------------------------------------------------------


def test_claim_sets_lease_fields(app_env):
    jid = add_job()
    worker_id = make_worker_id("t")
    assert claim(worker_id) == (jid, 1)
    row = get_job(jid)
    assert row.status == "running" and row.locked_by == worker_id and row.attempts == 1
    assert row.started_at is not None and row.heartbeat_at is not None and row.locked_at is not None
    assert claim(worker_id) is None


def test_claim_priority_then_id(app_env):
    ids = [add_job(priority=p) for p in (100, 10, 50, 10)]
    order = [claim()[0] for _ in ids]
    assert order == [ids[1], ids[3], ids[2], ids[0]]


def test_claim_kinds_filter_and_run_after(app_env):
    a = add_job("t_a")
    b = add_job("t_b")
    later = add_job("t_c")
    update_job(later, run_after=utcnow() + dt.timedelta(hours=1))
    assert claim(kinds=()) is None
    assert claim(kinds=("t_b",)) == (b, 1)
    assert claim(kinds=("t_c",)) is None
    assert claim(kinds=("t_a", "t_b", "t_c")) == (a, 1)


def test_concurrent_claims_claim_each_job_once(app_env):
    ids = {add_job() for _ in range(30)}
    claimed: list[int] = []
    lock = threading.Lock()

    def run(n: int) -> None:
        while True:
            got = claim(f"w{n}")
            if got is None:
                return
            with lock:
                claimed.append(got[0])

    threads = [threading.Thread(target=run, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(claimed) == sorted(ids)
    assert len(claimed) == len(set(claimed)) == 30


# --- fenced transitions ---------------------------------------------------------------------------


def test_fenced_finish_requires_matching_lease(app_env):
    jid = add_job()
    _, attempt = claim("w1")
    with session_scope() as db:
        assert not finish_job(db, Lease(jid, "w1", attempt + 1), {"x": 1})
        assert not finish_job(db, Lease(jid, "other", attempt), {"x": 1})
        assert finish_job(db, Lease(jid, "w1", attempt), {"x": 1})
        assert not finish_job(db, Lease(jid, "w1", attempt), {"x": 2})  # no longer running
    row = get_job(jid)
    assert (row.status, row.result, row.progress, row.locked_by) == ("succeeded", {"x": 1}, 1.0, None)
    assert row.finished_at is not None
    assert [e.message for e in job_events(jid)] == ["Finished"]


def test_fail_job_records_redacted_error_and_traceback(app_env):
    jid = add_job()
    _, attempt = claim("w1")
    with session_scope() as db:
        assert fail_job(db, Lease(jid, "w1", attempt), error="bad password=hunter22", error_code="x",
                        traceback_text="Traceback ... token=abc")
    row = get_job(jid)
    assert row.status == "failed" and row.error_code == "x"
    assert "hunter22" not in row.error and "[REDACTED]" in row.error
    ev = job_events(jid)[-1]
    assert ev.level == "error" and "abc" not in ev.data["traceback"]


def test_requeue_with_backoff(app_env):
    jid = add_job(max_attempts=3)
    _, attempt = claim("w1")
    before = utcnow()
    with session_scope() as db:
        assert requeue_job(db, Lease(jid, "w1", attempt), delay_seconds=30, error="flaky", error_code="retry")
    row = get_job(jid)
    assert row.status == "queued" and row.locked_by is None and row.error == "flaky"
    run_after = row.run_after.replace(tzinfo=dt.timezone.utc)
    assert before + dt.timedelta(seconds=29) <= run_after <= utcnow() + dt.timedelta(seconds=31)
    assert claim("w1") is None  # not runnable before run_after
    ev = job_events(jid)[-1]
    assert ev.level == "warning" and ev.data["retry_in_seconds"] == 30


def test_retry_delay_seconds():
    assert [retry_delay_seconds(n, base=15, cap=100) for n in (1, 2, 3, 4)] == [15, 30, 60, 100]
    assert retry_delay_seconds(10_000) == queue.DEFAULT_BACKOFF_CAP_SECONDS


def test_release_refunds_the_attempt(app_env):
    jid = add_job(max_attempts=2)
    _, attempt = claim("w1")
    with session_scope() as db:
        assert release_job(db, Lease(jid, "w1", attempt))
    row = get_job(jid)
    assert (row.status, row.attempts, row.max_attempts, row.locked_by) == ("queued", 1, 3, None)
    # next claim gets a NEW fencing token
    assert claim("w1") == (jid, 2)


def test_release_with_pending_cancel_cancels(app_env):
    jid = add_job()
    _, attempt = claim("w1")
    update_job(jid, cancel_requested=True)
    with session_scope() as db:
        assert release_job(db, Lease(jid, "w1", attempt))
    assert get_job(jid).status == "cancelled"


# --- cancel / retry / review ----------------------------------------------------------------------


def test_request_cancel_queued_running_terminal(app_env):
    queued = add_job()
    with session_scope() as db:
        job = request_cancel(db, queued)
        assert job.status == "cancelled" and job.error_code == "cancelled"
    assert get_job(queued).finished_at is not None

    running = add_job()
    claim("w1")
    with session_scope() as db:
        job = request_cancel(db, running)
        assert job.status == "running" and job.cancel_requested
    with session_scope() as db:
        request_cancel(db, running)  # idempotent, no duplicate event
    assert [e.message for e in job_events(running)] == ["Cancellation requested"]

    with session_scope() as db:
        assert request_cancel(db, queued).status == "cancelled"
        with pytest.raises(JobNotFound):
            request_cancel(db, 9999)


def test_cancel_awaiting_review_frees_the_version(app_env, version_id):
    jid = add_job("generate_lecture", version_id=version_id)
    update_job(jid, status="awaiting_review")
    with session_scope() as db:
        assert request_cancel(db, jid).status == "cancelled"
        assert enqueue(db, "build_assets", version_id=version_id).id


def test_retry_job(app_env, version_id):
    uid = make_user("bob")
    jid = add_job("build_assets", version_id=version_id, payload={"scene_ids": ["s1"]}, priority=5)
    with session_scope() as db:
        with pytest.raises(InvalidJobState):
            retry_job(db, jid, user_id=uid)
        with pytest.raises(JobNotFound):
            retry_job(db, 4242, user_id=uid)
    update_job(jid, status="failed")
    with session_scope() as db:
        new = retry_job(db, jid, user_id=uid)
        new_id = new.id
    row = get_job(new_id)
    assert (row.kind, row.payload, row.user_id, row.version_id, row.priority, row.status) == (
        "build_assets", {"scene_ids": ["s1"]}, uid, version_id, 5, "queued"
    )
    assert job_events(new_id)[0].data == {"retry_of": jid}
    old = get_job(jid)
    assert old.status == "failed" and old.error_code == "retried"
    assert job_events(jid)[-1].data == {"retried_by": new_id, "previous_error_code": None}
    with session_scope() as db, pytest.raises(InvalidJobState):  # already retried: exactly one retry
        retry_job(db, jid, user_id=None)


def test_retry_job_is_atomic_under_concurrency(app_env, version_id):
    """Two clicks / concurrent requests on a failed render must create ONE new job."""
    jid = add_job("render_video", version_id=version_id, payload={"render_id": 1})
    update_job(jid, status="failed", error_code="error")
    barrier = threading.Barrier(4)
    results: list[object] = []
    lock = threading.Lock()

    def attempt() -> None:
        barrier.wait(5)
        try:
            with session_scope() as db:
                outcome: object = retry_job(db, jid, user_id=None).id
        except InvalidJobState as exc:
            outcome = exc
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    created = [r for r in results if isinstance(r, int)]
    assert len(results) == 4 and len(created) == 1
    with session_scope() as db:
        renders = db.execute(select(Job.id).where(Job.kind == "render_video", Job.id != jid)).scalars().all()
    assert renders == created


def test_retry_job_conflict_undoes_the_claim(app_env, version_id):
    jid = add_job("build_assets", version_id=version_id)
    update_job(jid, status="failed", error_code="budget")
    blocker = add_job("generate_lecture", version_id=version_id)
    with session_scope() as db, pytest.raises(JobInProgress):
        retry_job(db, jid, user_id=None)
    assert get_job(jid).error_code == "budget"  # not marked as retried
    update_job(blocker, status="succeeded")
    with session_scope() as db:
        assert retry_job(db, jid, user_id=None).kind == "build_assets"
    assert job_events(jid)[-1].data["previous_error_code"] == "budget"


def test_complete_review_allows_continuation(app_env, version_id):
    jid = add_job("generate_lecture", version_id=version_id)
    with session_scope() as db:
        with pytest.raises(InvalidJobState):
            complete_review(db, jid)
        with pytest.raises(JobNotFound):
            complete_review(db, 777)
    update_job(jid, status="awaiting_review")
    with session_scope() as db:
        job = complete_review(db, jid)
        assert job.status == "succeeded"
        cont = enqueue(db, "generate_lecture", version_id=version_id, payload={"resume_state": {"a": 1}})
        assert cont.id != jid


# --- summaries / lookups ------------------------------------------------------------------------------


def test_job_summary_shape(app_env):
    jid = add_job()
    claim("w1")
    summary = job_summary(get_job(jid))
    assert set(summary) == {
        "id", "kind", "status", "stage", "progress", "message", "project_id", "version_id", "error",
        "error_code", "cost_usd", "attempts", "created_at", "started_at", "finished_at",
    }
    assert summary["status"] == "running" and summary["finished_at"] is None
    assert summary["created_at"].endswith("+00:00") and summary["started_at"].endswith("+00:00")


def test_active_job_lookups(app_env, version_id):
    uid = make_user("carol")
    pid = make_project(uid)
    with session_scope() as db:
        assert active_job_for_project(db, pid) is None
    render = add_job("render_video", version_id=version_id, project_id=pid)
    with session_scope() as db:
        assert active_job_for_version(db, version_id) is None  # mutating kinds only by default
        assert active_job_for_version(db, version_id, kinds=None).id == render
        assert active_job_for_project(db, pid).id == render
    build = add_job("build_assets", version_id=version_id, project_id=pid)
    with session_scope() as db:
        assert active_job_for_version(db, version_id).id == build
        assert active_job_for_project(db, pid).id == build
    update_job(build, status="succeeded")
    update_job(render, status="failed")
    with session_scope() as db:
        assert active_job_for_version(db, version_id, kinds=None) is None
        assert active_job_for_project(db, pid) is None


# --- reaper / restart recovery --------------------------------------------------------------------------


def test_reap_stale_requeues_fails_and_cancels(app_env):
    fresh = add_job()
    stale = add_job(max_attempts=3)
    null_hb = add_job(max_attempts=3)
    exhausted = add_job(max_attempts=1)
    cancelled = add_job(max_attempts=3)
    for jid in (fresh, stale, null_hb, exhausted, cancelled):
        with session_scope() as db:
            db.execute(update(Job).where(Job.id == jid).values(status="running", locked_by=f"h:1:b:{jid}",
                                                               attempts=1, locked_at=ago(5), heartbeat_at=ago(5)))
    update_job(stale, heartbeat_at=ago(1000), locked_at=ago(2000))
    update_job(null_hb, heartbeat_at=None, locked_at=ago(1000))
    update_job(exhausted, heartbeat_at=ago(1000))
    update_job(cancelled, heartbeat_at=ago(1000), cancel_requested=True)
    with session_scope() as db:
        out = reap_stale(db, stale_seconds=300)
    assert out == {"requeued": [stale, null_hb], "failed": [exhausted], "cancelled": [cancelled]}
    assert get_job(fresh).status == "running"
    for jid in (stale, null_hb):
        row = get_job(jid)
        assert row.status == "queued" and row.locked_by is None and row.heartbeat_at is None
    assert get_job(exhausted).error_code == "stale"
    assert get_job(cancelled).status == "cancelled"
    with session_scope() as db:  # idempotent
        assert reap_stale(db, stale_seconds=300) == {"requeued": [], "failed": [], "cancelled": []}


def test_reap_stale_handles_unowned_running_rows(app_env):
    jid = add_job(max_attempts=2)
    update_job(jid, status="running", attempts=1, locked_by=None, locked_at=None, heartbeat_at=None,
               created_at=ago(10_000))
    with session_scope() as db:
        assert reap_stale(db, stale_seconds=60)["requeued"] == [jid]


def test_worker_id_roundtrip():
    wid = make_worker_id("thread:with:colons")
    parts = parse_worker_id(wid)
    assert parts is not None and parts.host == HOSTNAME and parts.boot_id == BOOT_ID
    assert ":" not in parts.thread
    assert parse_worker_id("garbage") is None and parse_worker_id(None) is None
    assert parse_worker_id("h:notapid:b:t") is None


def test_recover_own_host(app_env):
    def running(locked_by: str, **extra) -> int:
        jid = add_job(max_attempts=3)
        update_job(jid, status="running", attempts=1, locked_by=locked_by, locked_at=ago(1), heartbeat_at=ago(1),
                   **extra)
        return jid

    dead = running(f"{HOSTNAME}:424242:deadbeef0000:t")
    alive_sibling = running(f"{HOSTNAME}:31337:cafebabe0000:t")
    mine = running(make_worker_id("me"))
    other_host = running("some-other-host:424242:deadbeef0000:t")
    malformed = running(f"{HOSTNAME}:oops")
    with session_scope() as db:
        out = recover_own_host(db, pid_alive=lambda pid: pid == 31337)
    assert out["requeued"] == [dead]
    assert get_job(dead).status == "queued"
    for jid in (alive_sibling, mine, other_host, malformed):
        assert get_job(jid).status == "running"
    assert job_events(dead)[-1].data == {"error_code": "worker_lost"}


# --- pending cancel wins over retry / review / recovery --------------------------------------------


def test_requeue_with_pending_cancel_cancels_instead(app_env):
    jid = add_job(max_attempts=3)
    _, attempt = claim("w1")
    with session_scope() as db:
        request_cancel(db, jid)
    with session_scope() as db:
        assert requeue_job(db, Lease(jid, "w1", attempt), delay_seconds=0, error="boom")
    row = get_job(jid)
    assert (row.status, row.error_code, row.locked_by) == ("cancelled", "cancelled", None)
    assert claim("w1") is None  # never runs again


def test_mark_awaiting_review_with_pending_cancel_cancels(app_env, version_id):
    jid = add_job("generate_lecture", version_id=version_id)
    _, attempt = claim("w1", kinds=("generate_lecture",))
    set_version(version_id, status="awaiting_review")
    update_job(jid, cancel_requested=True)
    with session_scope() as db:
        assert queue.mark_awaiting_review(db, Lease(jid, "w1", attempt), state={"plan": 1})
    assert get_job(jid).status == "cancelled"
    assert version_status(version_id) == "failed"


def test_requeue_only_if_attempts_left(app_env):
    jid = add_job(max_attempts=1)
    _, attempt = claim("w1")
    with session_scope() as db:
        assert not requeue_job(db, Lease(jid, "w1", attempt), delay_seconds=0, error="x", only_if_attempts_left=True)
    assert get_job(jid).status == "running"  # nothing written: the caller fails it
    jid2 = add_job(max_attempts=2)
    _, attempt2 = claim("w1")
    with session_scope() as db:
        assert requeue_job(db, Lease(jid2, "w1", attempt2), delay_seconds=0, error="x", only_if_attempts_left=True)
    assert get_job(jid2).status == "queued"


def test_reaper_cancels_unowned_running_row_with_pending_cancel(app_env):
    jid = add_job(max_attempts=3)
    update_job(jid, status="running", attempts=1, locked_by=None, locked_at=None, heartbeat_at=None,
               created_at=ago(10_000), cancel_requested=True)
    with session_scope() as db:
        assert reap_stale(db, stale_seconds=60)["cancelled"] == [jid]
    assert get_job(jid).status == "cancelled"


# --- version status is never left transient -----------------------------------------------------------


def set_version(version_id: int, **values) -> None:
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        db.execute(update(ProjectVersion).where(ProjectVersion.id == version_id).values(**values))


def version_status(version_id: int) -> str:
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        return db.execute(select(ProjectVersion.status).where(ProjectVersion.id == version_id)).scalar_one()


def test_cancel_queued_job_settles_version(app_env, version_id):
    set_version(version_id, status="generating")
    jid = add_job("generate_lecture", version_id=version_id)
    with session_scope() as db:
        request_cancel(db, jid)
    assert version_status(version_id) == "failed"


def test_cancel_awaiting_review_settles_version_to_ready_when_built(app_env, version_id):
    set_version(version_id, status="awaiting_review", built_revision=1)
    jid = add_job("generate_lecture", version_id=version_id)
    update_job(jid, status="awaiting_review")
    with session_scope() as db:
        request_cancel(db, jid)
    assert version_status(version_id) == "ready"


def test_reaper_failure_and_cancel_settle_version(app_env):
    uid = make_user("vera")
    pid = make_project(uid)
    v_failed, v_cancel, v_requeue = make_version(pid, 1), make_version(pid, 2), make_version(pid, 3)
    for vid in (v_failed, v_cancel, v_requeue):
        set_version(vid, status="building")
    failed = add_job("build_assets", version_id=v_failed, max_attempts=1)
    cancelled = add_job("build_assets", version_id=v_cancel, max_attempts=3)
    requeued = add_job("build_assets", version_id=v_requeue, max_attempts=3)
    for jid in (failed, cancelled, requeued):
        update_job(jid, status="running", attempts=1, locked_by=f"dead:1:b:{jid}", heartbeat_at=ago(1000))
    update_job(cancelled, cancel_requested=True)
    with session_scope() as db:
        out = reap_stale(db, stale_seconds=60)
    assert out == {"requeued": [requeued], "failed": [failed], "cancelled": [cancelled]}
    assert version_status(v_failed) == "failed"
    assert version_status(v_cancel) == "failed"
    assert version_status(v_requeue) == "building"  # will run again


def test_restart_recovery_settles_version(app_env, version_id):
    set_version(version_id, status="generating")
    jid = add_job("generate_lecture", version_id=version_id, max_attempts=1)
    update_job(jid, status="running", attempts=1, locked_by=f"{HOSTNAME}:424242:deadbeef0000:t", heartbeat_at=ago(1))
    with session_scope() as db:
        assert recover_own_host(db, pid_alive=lambda pid: False)["failed"] == [jid]
    assert version_status(version_id) == "failed"


def test_fenced_terminal_transitions_settle_version(app_env):
    uid = make_user("tess")
    pid = make_project(uid)
    results = {}
    for n, transition in enumerate(("fail", "cancel", "finish"), start=1):
        vid = make_version(pid, n)
        set_version(vid, status="building", built_revision=1 if transition == "finish" else None)
        jid = add_job("build_assets", version_id=vid)
        _, attempt = claim("w1", kinds=("build_assets",))
        lease = Lease(jid, "w1", attempt)
        with session_scope() as db:
            if transition == "fail":
                assert fail_job(db, lease, error="x")
            elif transition == "cancel":
                assert queue.cancel_running(db, lease)
            else:  # e.g. the handler skipped after a revision conflict and left the status behind
                assert finish_job(db, lease, {"skipped": "revision_conflict"})
        results[transition] = version_status(vid)
    assert results == {"fail": "failed", "cancel": "failed", "finish": "ready"}


def test_settle_version_leaves_other_cases_alone(app_env, version_id):
    from aadhi.jobs.queue import JobRef, settle_version

    set_version(version_id, status="ready")
    with session_scope() as db:
        assert settle_version(db, JobRef(1, "build_assets", version_id)) is None  # not transient
    set_version(version_id, status="building")
    with session_scope() as db:
        assert settle_version(db, JobRef(1, "render_video", version_id)) is None  # not a mutating kind
        assert settle_version(db, JobRef(1, "build_assets", None)) is None
    old = add_job("build_assets", version_id=version_id)
    update_job(old, status="failed")
    add_job("regenerate_scene", version_id=version_id)  # another mutating job is active
    with session_scope() as db:
        assert settle_version(db, JobRef(old, "build_assets", version_id)) is None
    assert version_status(version_id) == "building"


def test_transitions_store_strict_json(app_env):
    jid = add_job()
    _, attempt = claim("w1")
    with session_scope() as db:
        assert finish_job(db, Lease(jid, "w1", attempt), {"ratio": float("nan"), "inf": float("inf"), "t": "a\x00b"})
    assert get_job(jid).result == {"ratio": None, "inf": None, "t": "ab"}
    with session_scope() as db:
        add_event(db, jid, "nul\x00here", stage="s\x00", data={"x": float("-inf")})
    ev = job_events(jid)[-1]
    assert (ev.message, ev.stage, ev.data) == ("nulhere", "s", {"x": None})
