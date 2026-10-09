"""Durable job queue: enqueue, atomic claim, fenced state transitions, cancel/retry, summaries.

Every state change is ONE atomic SQL statement (``UPDATE ... WHERE <expected state> RETURNING``).
Functions that take a ``Session`` never commit: the caller owns the transaction (API request,
``session_scope`` in the worker). Transitions performed on behalf of a running job take a
``Lease`` and are fenced (``aadhi.jobs.lease.fence``); they return ``False`` when the lease was
lost, in which case nothing was written.

* A pending user cancellation wins over every non-final transition (retry, release, review).
* When a version-mutating job leaves the active statuses, ``settle_version`` (same transaction)
  moves its version out of generating/building/awaiting_review if nothing else will; likewise
  ``settle_render`` moves the Render row of a ``render_video`` job that failed / was cancelled
  outside its handler out of queued/running.
* JSON values written here are strict JSON (``util.jsonable``: no NaN/inf, no NUL characters).
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from collections.abc import Callable, Sequence
from typing import Any, NamedTuple

import psutil
from sqlalchemy import Select, case, exists, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import ACTIVE_JOB_STATUSES, VERSION_MUTATING_KINDS, Job, JobEvent, ProjectVersion, Render, utcnow
from .lease import BOOT_ID, HOSTNAME, Lease, fence, parse_worker_id
from .util import MAX_MESSAGE_CHARS, clean_text, iso, jsonable, redact_obj, redact_text, truncate

log = logging.getLogger(__name__)

TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
STREAM_END_STATUSES = (*TERMINAL_STATUSES, "awaiting_review")
EVENT_LEVELS = ("info", "warning", "error", "progress")
DEFAULT_BACKOFF_BASE_SECONDS = 15.0
DEFAULT_BACKOFF_CAP_SECONDS = 600.0
_NO_SYNC = {"synchronize_session": False}


# --- errors ---------------------------------------------------------------------------------


class JobInProgress(Exception):
    """Another version-mutating job is already active for this version (API: 409 job_in_progress)."""

    def __init__(self, job_id: int | None, message: str = "another job is already in progress for this version"):
        super().__init__(message)
        self.job_id = job_id


class JobNotFound(LookupError):
    """No job with that id."""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"job {job_id} not found")
        self.job_id = job_id


class InvalidJobState(Exception):
    """The requested transition is not allowed from the job's current status."""

    def __init__(self, job_id: int, status: str | None, message: str) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.status = status


# --- helpers ----------------------------------------------------------------------------------


def _active_version_predicate(dialect: str) -> Any:
    """The partial unique index predicate, verbatim (ON CONFLICT inference needs an exact match)."""
    for idx in Job.__table__.indexes:
        if idx.name == "uq_jobs_active_version":
            return idx.dialect_options[dialect]["where"]
    raise RuntimeError("uq_jobs_active_version index is missing from the Job model")


def _dialect_insert(dialect: str) -> Callable[..., Any] | None:
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        return sqlite_insert
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        return pg_insert
    return None


def add_event(
    db: Session,
    job_id: int,
    message: str,
    *,
    level: str = "info",
    stage: str = "",
    progress: float | None = None,
    data: dict[str, Any] | None = None,
    now: dt.datetime | None = None,
) -> None:
    """Insert one JobEvent (redacted). Not fenced: use only after a fenced transition matched or
    for API-side transitions."""
    db.execute(
        insert(JobEvent).values(
            job_id=job_id,
            created_at=now or utcnow(),
            level=level if level in EVENT_LEVELS else "info",
            stage=truncate(clean_text(stage or ""), 64),
            message=redact_text(message),
            progress=progress,
            data=redact_obj(jsonable(data or {})),
        )
    )


def retry_delay_seconds(
    attempt: int, *, base: float = DEFAULT_BACKOFF_BASE_SECONDS, cap: float = DEFAULT_BACKOFF_CAP_SECONDS
) -> float:
    """Exponential backoff before retry number ``attempt`` (1-based): base, 2*base, 4*base ... <= cap."""
    exponent = max(0, min(int(attempt) - 1, 30))
    return float(min(cap, base * (2**exponent)))


# --- enqueue ----------------------------------------------------------------------------------


def enqueue(
    db: Session,
    kind: str,
    *,
    user_id: int | None = None,
    project_id: int | None = None,
    version_id: int | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
    max_attempts: int | None = None,
) -> Job:
    """Insert a queued job and flush (the caller commits).

    Raises ``JobInProgress(job_id=<active job>)`` when a version-mutating job is already active
    for ``version_id`` (enforced atomically by the ``uq_jobs_active_version`` partial unique index
    via ``INSERT ... ON CONFLICT DO NOTHING``, so the caller's transaction stays usable).
    """
    if not kind or len(kind) > 32:
        raise ValueError(f"invalid job kind {kind!r}")
    settings = get_settings()
    attempts_cap = settings.job_max_attempts if max_attempts is None else int(max_attempts)
    if attempts_cap < 1:
        raise ValueError("max_attempts must be >= 1")
    now = utcnow()
    values: dict[str, Any] = {
        "kind": kind,
        "status": "queued",
        "user_id": user_id,
        "project_id": project_id,
        "version_id": version_id,
        "payload": jsonable(payload or {}),
        "priority": int(priority),
        "max_attempts": attempts_cap,
        "run_after": now,
        "created_at": now,
    }
    if version_id is not None and kind in VERSION_MUTATING_KINDS:
        return _enqueue_exclusive(db, values, version_id)
    job = Job(**values)
    db.add(job)
    db.flush()
    return job


def _enqueue_exclusive(db: Session, values: dict[str, Any], version_id: int) -> Job:
    active = active_job_for_version(db, version_id)
    if active is not None:
        raise JobInProgress(active.id)
    dialect = db.get_bind().dialect.name
    dialect_insert = _dialect_insert(dialect)
    if dialect_insert is not None:
        stmt = (
            dialect_insert(Job)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Job.version_id], index_where=_active_version_predicate(dialect))
            .returning(Job.id)
        )
        new_id = db.execute(stmt).scalar_one_or_none()
        if new_id is None:
            active = active_job_for_version(db, version_id)
            raise JobInProgress(active.id if active is not None else None)
        job = db.get(Job, new_id)
        assert job is not None
        return job
    # Other dialects: savepoint + IntegrityError translation.
    job = Job(**values)
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
    except IntegrityError as exc:
        active = active_job_for_version(db, version_id)
        raise JobInProgress(active.id if active is not None else None) from exc
    return job


# --- claim ------------------------------------------------------------------------------------


def claim_next(
    db: Session, worker_id: str, kinds: Sequence[str], *, now: dt.datetime | None = None
) -> tuple[int, int] | None:
    """Atomically claim the next runnable job of ``kinds``; returns ``(job_id, attempt)`` or None.

    ONE statement: ``UPDATE jobs ... WHERE id=(SELECT ... ORDER BY priority, id LIMIT 1
    [FOR UPDATE SKIP LOCKED]) AND status='queued' RETURNING id, attempts``.
    """
    if not kinds:
        return None
    now = now or utcnow()
    cand = Job.__table__.alias("candidate")
    sub: Select[Any] = (
        select(cand.c.id)
        .where(cand.c.status == "queued", cand.c.run_after <= now, cand.c.kind.in_(list(kinds)))
        .order_by(cand.c.priority, cand.c.id)
        .limit(1)
    )
    if db.get_bind().dialect.name == "postgresql":
        sub = sub.with_for_update(skip_locked=True)
    stmt = (
        update(Job)
        .where(Job.id == sub.scalar_subquery(), Job.status == "queued")
        .values(
            status="running",
            locked_by=worker_id,
            locked_at=now,
            heartbeat_at=now,
            attempts=Job.attempts + 1,
            started_at=func.coalesce(Job.started_at, now),
        )
        .returning(Job.id, Job.attempts)
        .execution_options(**_NO_SYNC)
    )
    row = db.execute(stmt).first()
    return None if row is None else (int(row[0]), int(row[1]))


def has_claimable(db: Session, kinds: Sequence[str], *, now: dt.datetime | None = None) -> bool:
    """True when a queued job of ``kinds`` is runnable now (used by drain mode)."""
    if not kinds:
        return False
    now = now or utcnow()
    stmt = select(Job.id).where(Job.status == "queued", Job.run_after <= now, Job.kind.in_(list(kinds))).limit(1)
    return db.execute(stmt).first() is not None


# --- version status (defence in depth) -------------------------------------------------------------

TRANSIENT_VERSION_STATUSES = ("generating", "building", "awaiting_review")
RETRYABLE_STATUSES = ("failed", "cancelled")
RETRIED_ERROR_CODE = "retried"


class JobRef(NamedTuple):
    """The columns a transition needs to settle the job's version."""

    id: int
    kind: str
    version_id: int | None


_REF_COLUMNS = (Job.id, Job.kind, Job.version_id)


def _ref(row: Any) -> JobRef | None:
    return None if row is None else JobRef(int(row[0]), str(row[1]), row[2])


def settle_version(db: Session, job: JobRef, *, now: dt.datetime | None = None) -> str | None:
    """Move the version of a version-mutating job that just left the active statuses out of a
    transient status (generating / building / awaiting_review), unless another mutating job is
    active for it.

    The new status is ``ready`` when a build exists (``built_revision``) and ``failed`` otherwise —
    the orchestrator's own restore rule, so this is a no-op whenever the handler already settled the
    version. It covers every path where no handler could: cancelling a queued / awaiting-review job,
    reaper and restart recovery, jobs that could not start. ONE atomic UPDATE (same transaction as
    the job transition); returns the new status, or None when nothing changed.
    """
    if job.version_id is None or job.kind not in VERSION_MUTATING_KINDS:
        return None
    other_active = exists(
        select(Job.id).where(
            Job.version_id == job.version_id,
            Job.id != job.id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.kind.in_(VERSION_MUTATING_KINDS),
        )
    )
    row = db.execute(
        update(ProjectVersion)
        .where(
            ProjectVersion.id == job.version_id,
            ProjectVersion.status.in_(TRANSIENT_VERSION_STATUSES),
            ~other_active,
        )
        .values(
            status=case((ProjectVersion.built_revision.is_not(None), "ready"), else_="failed"),
            updated_at=now or utcnow(),
        )
        .returning(ProjectVersion.status)
        .execution_options(**_NO_SYNC)
    ).first()
    if row is None:
        return None
    log.info("job %s (%s) ended: version %s -> %s", job.id, job.kind, job.version_id, row[0])
    return str(row[0])


RENDER_JOB_KIND = "render_video"
SETTLED_RENDER_STATUSES = ("failed", "cancelled")


def settle_render(db: Session, job: JobRef, status: str) -> bool:
    """Move the ``Render`` row of a ``render_video`` job that ended as ``failed``/``cancelled``
    outside its handler (queued cancel, reaper / restart recovery, worker-side failure) out of
    ``queued``/``running``, so the Renders list never shows a dead render as in progress.

    ONE atomic UPDATE in the caller's transaction, matched on ``Render.job_id`` (a render retried
    as a newer job is left alone) and only from ``queued``/``running`` (a succeeded or already
    settled render is never touched). Returns True when a row changed.
    """
    if job.kind != RENDER_JOB_KIND or status not in SETTLED_RENDER_STATUSES:
        return False
    row = db.execute(
        update(Render)
        .where(Render.job_id == job.id, Render.status.in_(("queued", "running")))
        .values(status=status)
        .returning(Render.id)
        .execution_options(**_NO_SYNC)
    ).first()
    if row is None:
        return False
    log.info("job %s (%s) ended: render %s -> %s", job.id, job.kind, row[0], status)
    return True


# --- fenced transitions (worker side) -------------------------------------------------------------


def touch(db: Session, lease: Lease, *, now: dt.datetime | None = None) -> bool | None:
    """Fenced heartbeat. Returns the job's ``cancel_requested`` flag, or None if the lease was lost."""
    stmt = (
        fence(update(Job), lease)
        .values(heartbeat_at=now or utcnow())
        .returning(Job.cancel_requested)
        .execution_options(**_NO_SYNC)
    )
    row = db.execute(stmt).first()
    return None if row is None else bool(row[0])


def _fenced_update(db: Session, lease: Lease, values: dict[str, Any], *where: Any) -> JobRef | None:
    stmt = (
        fence(update(Job), lease)
        .where(*where)
        .values(**values)
        .returning(*_REF_COLUMNS)
        .execution_options(**_NO_SYNC)
    )
    return _ref(db.execute(stmt).first())


def _cancel_if_requested(db: Session, lease: Lease, *, now: dt.datetime) -> bool:
    """Fenced running -> cancelled when the user asked for it. A pending cancel wins over a retry,
    a release or a review: the job must never run (or wait) again after the user cancelled it."""
    ref = _fenced_update(
        db,
        lease,
        {"status": "cancelled", "error_code": "cancelled", "finished_at": now, "locked_by": None},
        Job.cancel_requested.is_(True),
    )
    if ref is None:
        return False
    add_event(db, lease.job_id, "Cancelled", level="warning", now=now)
    settle_version(db, ref, now=now)
    settle_render(db, ref, "cancelled")
    return True


def finish_job(db: Session, lease: Lease, result: dict[str, Any] | None, *, now: dt.datetime | None = None) -> bool:
    """running -> succeeded with ``result`` (fenced; ``result`` is coerced to strict JSON)."""
    now = now or utcnow()
    ref = _fenced_update(
        db,
        lease,
        {
            "status": "succeeded",
            "result": jsonable(result) if result is not None else None,
            "progress": 1.0,
            "error": None,
            "error_code": None,
            "finished_at": now,
            "locked_by": None,
            "heartbeat_at": now,
        },
    )
    if ref is None:
        return False
    add_event(db, lease.job_id, "Finished", level="info", progress=1.0, now=now)
    settle_version(db, ref, now=now)
    return True


def fail_job(
    db: Session,
    lease: Lease,
    *,
    error: str,
    error_code: str = "error",
    traceback_text: str | None = None,
    now: dt.datetime | None = None,
) -> bool:
    """running -> failed (fenced). ``error`` is a short user-facing message; the traceback tail
    (if any) goes into a JobEvent."""
    now = now or utcnow()
    message = redact_text(error, MAX_MESSAGE_CHARS)
    ref = _fenced_update(
        db,
        lease,
        {
            "status": "failed",
            "error": message,
            "error_code": truncate(clean_text(error_code), 64),
            "finished_at": now,
            "locked_by": None,
        },
    )
    if ref is None:
        return False
    data: dict[str, Any] = {"error_code": error_code}
    if traceback_text:
        data["traceback"] = traceback_text
    add_event(db, lease.job_id, message, level="error", data=data, now=now)
    settle_version(db, ref, now=now)
    settle_render(db, ref, "failed")
    return True


def requeue_job(
    db: Session,
    lease: Lease,
    *,
    delay_seconds: float,
    error: str | None = None,
    error_code: str | None = None,
    traceback_text: str | None = None,
    only_if_attempts_left: bool = False,
    now: dt.datetime | None = None,
) -> bool:
    """running -> queued with ``run_after = now + delay`` (fenced retry with backoff).

    A pending cancellation cancels the job instead (returns True). With ``only_if_attempts_left``
    nothing is written (False) when ``attempts >= max_attempts``: the caller fails the job then.
    """
    now = now or utcnow()
    if _cancel_if_requested(db, lease, now=now):
        return True
    message = redact_text(error, MAX_MESSAGE_CHARS) if error else None
    values: dict[str, Any] = {
        "status": "queued",
        "locked_by": None,
        "locked_at": None,
        "heartbeat_at": None,
        "run_after": now + dt.timedelta(seconds=max(0.0, delay_seconds)),
    }
    if message is not None:
        values.update(error=message, error_code=truncate(clean_text(error_code or "error"), 64))
    extra = [Job.attempts < Job.max_attempts] if only_if_attempts_left else []
    if _fenced_update(db, lease, values, *extra) is None:
        return False
    data: dict[str, Any] = {"retry_in_seconds": round(delay_seconds, 3), "attempt": lease.attempt}
    if traceback_text:
        data["traceback"] = traceback_text
    text = f"Attempt {lease.attempt} failed; retrying in {delay_seconds:.0f}s"
    if message:
        text += f": {message}"
    add_event(db, lease.job_id, text, level="warning", data=data, now=now)
    return True


def cancel_running(
    db: Session,
    lease: Lease,
    *,
    error_code: str = "cancelled",
    error: str | None = None,
    now: dt.datetime | None = None,
) -> bool:
    """running -> cancelled (fenced), after the handler observed the cancellation.

    ``error_code``/``error`` describe handler-initiated stops (e.g. ``lease_lost`` raised by a
    handler whose own compare-and-set lost a race); user cancellations keep the defaults.
    """
    now = now or utcnow()
    message = redact_text(error, MAX_MESSAGE_CHARS) if error else None
    ref = _fenced_update(
        db,
        lease,
        {
            "status": "cancelled",
            "error": message,
            "error_code": truncate(clean_text(error_code), 64),
            "finished_at": now,
            "locked_by": None,
        },
    )
    if ref is None:
        return False
    add_event(db, lease.job_id, f"Cancelled: {message}" if message else "Cancelled", level="warning", now=now)
    settle_version(db, ref, now=now)
    settle_render(db, ref, "cancelled")
    return True


def mark_awaiting_review(
    db: Session, lease: Lease, *, state: dict[str, Any], message: str = "", now: dt.datetime | None = None
) -> bool:
    """running -> awaiting_review with ``result = state`` (fenced; a pending cancel cancels instead)."""
    now = now or utcnow()
    if _cancel_if_requested(db, lease, now=now):
        return True
    text = redact_text(message or "Waiting for review", MAX_MESSAGE_CHARS)
    ref = _fenced_update(
        db,
        lease,
        {
            "status": "awaiting_review",
            "result": jsonable(state),
            "message": text,
            "locked_by": None,
            "heartbeat_at": now,
        },
    )
    if ref is None:
        return False
    add_event(db, lease.job_id, text, level="info", now=now)
    return True


def release_job(db: Session, lease: Lease, *, reason: str = "worker shutdown", now: dt.datetime | None = None) -> bool:
    """running -> queued immediately without consuming an attempt (fenced). Only for jobs the
    worker stopped itself (shutdown) — never for handler failures.

    ``attempts`` is the fencing token and never decreases; the attempt is refunded by raising
    ``max_attempts`` instead. A job with a pending cancellation is cancelled instead.
    """
    now = now or utcnow()
    if _cancel_if_requested(db, lease, now=now):
        return True
    ref = _fenced_update(
        db,
        lease,
        {
            "status": "queued",
            "locked_by": None,
            "locked_at": None,
            "heartbeat_at": None,
            "run_after": now,
            "max_attempts": Job.max_attempts + 1,
        },
    )
    if ref is None:
        return False
    add_event(db, lease.job_id, f"Released ({reason}); the job will be picked up again", level="info", now=now)
    return True


# --- recovery (reaper / restart) ---------------------------------------------------------------


def _recover_one(
    db: Session, job_id: int, locked_by: str, attempts: int, extra: list[Any], *, code: str, why: str, now: dt.datetime
) -> str | None:
    """Requeue / fail / cancel one abandoned running job, fenced on its old lease + ``extra``."""
    lease = Lease(job_id, locked_by, attempts)

    def run(where_extra: list[Any], values: dict[str, Any]) -> JobRef | None:
        return _fenced_update(db, lease, values, *extra, *where_extra)

    ref = run(
        [Job.cancel_requested.is_(True)],
        {"status": "cancelled", "error_code": "cancelled", "finished_at": now, "locked_by": None},
    )
    if ref is not None:
        add_event(db, job_id, f"Cancelled ({why})", level="warning", now=now)
        settle_version(db, ref, now=now)
        settle_render(db, ref, "cancelled")
        return "cancelled"
    message = f"The worker running this job stopped ({why})."
    ref = run(
        [Job.attempts >= Job.max_attempts],
        {"status": "failed", "error": message, "error_code": code, "finished_at": now, "locked_by": None},
    )
    if ref is not None:
        add_event(db, job_id, message + " No attempts left.", level="error", data={"error_code": code}, now=now)
        settle_version(db, ref, now=now)
        settle_render(db, ref, "failed")
        return "failed"
    requeue = {"status": "queued", "locked_by": None, "locked_at": None, "heartbeat_at": None, "run_after": now}
    if run([], requeue) is not None:
        add_event(db, job_id, message + " Requeued.", level="warning", data={"error_code": code}, now=now)
        return "requeued"
    return None


def reap_stale(
    db: Session, *, stale_seconds: float, now: dt.datetime | None = None, limit: int = 100
) -> dict[str, list[int]]:
    """Requeue (or fail when attempts are exhausted, or cancel when requested) running jobs whose
    ``coalesce(heartbeat_at, locked_at)`` is older than ``stale_seconds``. Safe to run concurrently
    in every worker: each row update re-checks the lease and the staleness atomically."""
    now = now or utcnow()
    cutoff = now - dt.timedelta(seconds=stale_seconds)
    last_seen = func.coalesce(Job.heartbeat_at, Job.locked_at, Job.created_at)
    rows = db.execute(
        select(Job.id, Job.locked_by, Job.attempts)
        .where(Job.status == "running", last_seen < cutoff)
        .order_by(Job.id)
        .limit(limit)
    ).all()
    out: dict[str, list[int]] = {"requeued": [], "failed": [], "cancelled": []}
    for job_id, locked_by, attempts in rows:
        extra = [last_seen < cutoff]
        if locked_by is None:  # malformed running row: match NULL owner explicitly
            outcome = _recover_unowned(db, job_id, attempts, cutoff, now)
        else:
            outcome = _recover_one(db, job_id, locked_by, attempts, extra, code="stale", why="no heartbeat", now=now)
        if outcome:
            out[outcome].append(job_id)
    if any(out.values()):
        log.warning("reaper recovered stale jobs: %s", out)
    return out


def _recover_unowned(db: Session, job_id: int, attempts: int, cutoff: dt.datetime, now: dt.datetime) -> str | None:
    last_seen = func.coalesce(Job.heartbeat_at, Job.locked_at, Job.created_at)
    base = [
        Job.id == job_id,
        Job.status == "running",
        Job.locked_by.is_(None),
        Job.attempts == attempts,
        last_seen < cutoff,
    ]

    def run(where_extra: list[Any], values: dict[str, Any]) -> JobRef | None:
        stmt = update(Job).where(*base, *where_extra).values(**values).returning(*_REF_COLUMNS)
        return _ref(db.execute(stmt.execution_options(**_NO_SYNC)).first())

    ref = run([Job.cancel_requested.is_(True)], {"status": "cancelled", "error_code": "cancelled", "finished_at": now})
    if ref is not None:
        add_event(db, job_id, "Cancelled (the job lost its worker)", level="warning", now=now)
        settle_version(db, ref, now=now)
        settle_render(db, ref, "cancelled")
        return "cancelled"
    ref = run(
        [Job.attempts >= Job.max_attempts],
        {"status": "failed", "error": "The job lost its worker.", "error_code": "stale", "finished_at": now},
    )
    if ref is not None:
        add_event(db, job_id, "The job lost its worker. No attempts left.", level="error", now=now)
        settle_version(db, ref, now=now)
        settle_render(db, ref, "failed")
        return "failed"
    if run([], {"status": "queued", "locked_at": None, "heartbeat_at": None, "run_after": now}) is not None:
        add_event(db, job_id, "The job lost its worker. Requeued.", level="warning", now=now)
        return "requeued"
    return None


def recover_own_host(
    db: Session,
    *,
    host: str = HOSTNAME,
    boot_id: str = BOOT_ID,
    now: dt.datetime | None = None,
    pid_alive: Callable[[int], bool] | None = None,
) -> dict[str, list[int]]:
    """On worker start: recover running jobs locked by THIS host under a different boot uuid whose
    process is gone (pid not alive, or our own pid recycled). Live sibling processes on the same
    host are left alone (the reaper handles them if they stop heartbeating)."""
    now = now or utcnow()
    alive = pid_alive or psutil.pid_exists
    me = os.getpid()
    rows = db.execute(
        select(Job.id, Job.locked_by, Job.attempts).where(
            Job.status == "running", Job.locked_by.startswith(f"{host}:", autoescape=True)
        )
    ).all()
    out: dict[str, list[int]] = {"requeued": [], "failed": [], "cancelled": []}
    for job_id, locked_by, attempts in rows:
        parts = parse_worker_id(locked_by)
        if parts is None or parts.host != host or parts.boot_id == boot_id:
            continue
        if parts.pid != me and _safe_alive(alive, parts.pid):
            continue
        outcome = _recover_one(db, job_id, locked_by, attempts, [], code="worker_lost", why="worker restarted", now=now)
        if outcome:
            out[outcome].append(job_id)
    if any(out.values()):
        log.warning("recovered jobs abandoned by a previous worker process on this host: %s", out)
    return out


def _safe_alive(alive: Callable[[int], bool], pid: int) -> bool:
    try:
        return bool(alive(pid))
    except Exception:  # noqa: BLE001 - pragma: no cover - psutil edge cases: assume alive (never steal)
        return True


# --- API-side operations ------------------------------------------------------------------------


def _get_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id, populate_existing=True)
    if job is None:
        raise JobNotFound(job_id)
    return job


def request_cancel(db: Session, job_id: int) -> Job:
    """Cancel a job: queued/awaiting_review -> cancelled immediately (and its version leaves the
    generating/building/awaiting_review status); running -> ``cancel_requested`` (the worker stops
    it at the next ``check_cancelled``); terminal jobs are returned unchanged."""
    now = utcnow()
    done = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(("queued", "awaiting_review")))
        .values(status="cancelled", cancel_requested=True, error_code="cancelled", finished_at=now, locked_by=None)
        .returning(*_REF_COLUMNS)
        .execution_options(**_NO_SYNC)
    ).first()
    if done is not None:
        add_event(db, job_id, "Cancelled", level="warning", now=now)
        ref = _ref(done)
        assert ref is not None
        settle_version(db, ref, now=now)
        settle_render(db, ref, "cancelled")
        return _get_job(db, job_id)
    flagged = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running", Job.cancel_requested.is_(False))
        .values(cancel_requested=True)
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).first()
    if flagged is not None:
        add_event(db, job_id, "Cancellation requested", level="warning", now=now)
    return _get_job(db, job_id)


# Payload keys that hold a teacher's consent for one job only (``aadhi.pipeline.assets.CONFIRM_PAID_KEY``: a possibly
# billed AI video may be submitted again): a retry never carries them over, so it never pays twice unasked.
ONE_JOB_PAYLOAD_KEYS = frozenset({"confirm_paid_scene_ids"})


def retry_job(db: Session, job_id: int, *, user_id: int | None) -> Job:
    """Enqueue a new job with the same kind/payload as a failed or cancelled one (without the one-job consent
    keys, ``ONE_JOB_PAYLOAD_KEYS``).

    The retry is claimed atomically first (``error_code := 'retried'`` on the old job, ONE
    conditional UPDATE), so double clicks / concurrent requests create exactly one new job; a job
    that was already retried raises ``InvalidJobState``. Authorisation is the caller's job.
    ``user_id`` (the requester) owns the new job; ``None`` keeps the original owner. Raises
    ``JobNotFound``, ``InvalidJobState`` or ``JobInProgress`` (the claim is undone then).
    """
    old = db.execute(select(Job).where(Job.id == job_id).execution_options(populate_existing=True)).scalar_one_or_none()
    if old is None:
        raise JobNotFound(job_id)
    if old.status not in RETRYABLE_STATUSES:
        raise InvalidJobState(
            job_id, old.status, f"only failed or cancelled jobs can be retried (status: {old.status})"
        )
    previous_code = old.error_code
    claimed = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_(RETRYABLE_STATUSES),
            func.coalesce(Job.error_code, "") != RETRIED_ERROR_CODE,
        )
        .values(error_code=RETRIED_ERROR_CODE)
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).first()
    if claimed is None:
        raise InvalidJobState(job_id, old.status, "this job was already retried")
    try:
        new = enqueue(
            db,
            old.kind,
            user_id=user_id if user_id is not None else old.user_id,
            project_id=old.project_id,
            version_id=old.version_id,
            payload={k: v for k, v in (old.payload or {}).items() if k not in ONE_JOB_PAYLOAD_KEYS},
            priority=old.priority,
        )
    except JobInProgress:
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.error_code == RETRIED_ERROR_CODE)
            .values(error_code=previous_code)
            .execution_options(**_NO_SYNC)
        )
        raise
    add_event(db, new.id, f"Retry of job #{old.id}", data={"retry_of": old.id})
    add_event(
        db, old.id, f"Retried as job #{new.id}", data={"retried_by": new.id, "previous_error_code": previous_code}
    )
    return new


def complete_review(db: Session, job_id: int) -> Job:
    """awaiting_review -> succeeded (the API then enqueues the continuation job in the same
    transaction; the partial unique index no longer counts this job as active)."""
    now = utcnow()
    row = db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "awaiting_review")
        .values(status="succeeded", finished_at=now)
        .returning(Job.id)
        .execution_options(**_NO_SYNC)
    ).first()
    if row is None:
        job = db.get(Job, job_id)
        if job is None:
            raise JobNotFound(job_id)
        raise InvalidJobState(job_id, job.status, f"job is not awaiting review (status: {job.status})")
    add_event(db, job_id, "Review approved", level="info", now=now)
    return _get_job(db, job_id)


def job_summary(job: Job) -> dict[str, Any]:
    """``JobSummary`` (docs/API.md). Never loads the deferred payload/result columns."""
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage or "",
        "progress": round(float(job.progress or 0.0), 4),
        "message": job.message or "",
        "project_id": job.project_id,
        "version_id": job.version_id,
        "error": job.error,
        "error_code": job.error_code,
        "cost_usd": round(float(job.cost_usd or 0.0), 6),
        "attempts": job.attempts,
        "created_at": iso(job.created_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
    }


def active_job_for_version(
    db: Session, version_id: int, *, kinds: Sequence[str] | None = VERSION_MUTATING_KINDS
) -> Job | None:
    """The active (queued/running/awaiting_review) job for a version — by default only
    version-mutating kinds (the ones the partial unique index guards); ``kinds=None`` = any kind."""
    stmt = select(Job).where(Job.version_id == version_id, Job.status.in_(ACTIVE_JOB_STATUSES))
    if kinds is not None:
        stmt = stmt.where(Job.kind.in_(list(kinds)))
    return db.execute(stmt.order_by(Job.id.desc()).limit(1)).scalar_one_or_none()


def active_job_for_project(db: Session, project_id: int) -> Job | None:
    """The most recent active job (any kind) of a project."""
    stmt = (
        select(Job)
        .where(Job.project_id == project_id, Job.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(Job.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()
