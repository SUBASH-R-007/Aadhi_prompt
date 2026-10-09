"""Fake aadhi.jobs.queue (DB rows only; no workers)."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from aadhi.config import get_settings
from aadhi.models import ACTIVE_JOB_STATUSES, VERSION_MUTATING_KINDS, Job, utcnow

RETRIED_ERROR_CODE = "retried"


class InvalidJobState(Exception):
    def __init__(self, job_id: int, status: str | None, message: str) -> None:
        super().__init__(message)
        self.job_id = job_id
        self.status = status


class JobInProgress(Exception):
    def __init__(self, message: str = "job in progress", job_id: int | None = None) -> None:
        super().__init__(message)
        self.job_id = job_id


def _iso(value):
    if value is None:
        return None
    return value.isoformat()


def job_summary(job: Job) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "project_id": job.project_id,
        "version_id": job.version_id,
        "error": job.error,
        "error_code": job.error_code,
        "cost_usd": job.cost_usd,
        "attempts": job.attempts,
        "created_at": _iso(job.created_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def active_job_for_version(db, version_id: int):
    return (
        db.execute(
            select(Job).where(
                Job.version_id == version_id, Job.status.in_(ACTIVE_JOB_STATUSES), Job.kind.in_(VERSION_MUTATING_KINDS)
            )
        )
        .scalars()
        .first()
    )


def active_job_for_project(db, project_id: int):
    return (
        db.execute(
            select(Job).where(Job.project_id == project_id, Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.id.desc())
        )
        .scalars()
        .first()
    )


def enqueue(
    db, kind, *, user_id=None, project_id=None, version_id=None, payload=None, priority=100, max_attempts=None
) -> Job:
    if version_id is not None and kind in VERSION_MUTATING_KINDS:
        existing = active_job_for_version(db, version_id)
        if existing is not None:
            raise JobInProgress(job_id=existing.id)
    job = Job(
        kind=kind,
        status="queued",
        user_id=user_id,
        project_id=project_id,
        version_id=version_id,
        payload=dict(payload or {}),
        priority=priority,
        max_attempts=max_attempts or get_settings().job_max_attempts,
    )
    db.add(job)
    try:
        db.flush()
    except IntegrityError:  # pragma: no cover - race
        raise JobInProgress() from None
    return job


def request_cancel(db, job_id: int) -> Job:
    now = utcnow()
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status.in_(("queued", "awaiting_review")))
        .values(status="cancelled", finished_at=now, error_code="cancelled")
        .execution_options(synchronize_session=False)
    )
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "running")
        .values(cancel_requested=True)
        .execution_options(synchronize_session=False)
    )
    db.flush()
    job = db.get(Job, job_id)
    db.refresh(job)
    return job


def retry_job(db, job_id: int, *, user_id: int | None) -> Job:
    """Each failed/cancelled job is retried at most once (claimed via error_code, undone on 409)."""
    old = db.get(Job, job_id)
    previous = old.error_code
    claimed = db.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status.in_(("failed", "cancelled")),
            Job.error_code.is_distinct_from(RETRIED_ERROR_CODE),
        )
        .values(error_code=RETRIED_ERROR_CODE)
        .execution_options(synchronize_session=False)
    ).rowcount
    if not claimed:
        raise InvalidJobState(job_id, old.status, "this job was already retried")
    try:
        return enqueue(
            db,
            old.kind,
            user_id=user_id,
            project_id=old.project_id,
            version_id=old.version_id,
            payload=dict(old.payload or {}),
            priority=old.priority,
        )
    except JobInProgress:
        db.execute(
            update(Job).where(Job.id == job_id).values(error_code=previous).execution_options(synchronize_session=False)
        )
        raise


def complete_review(db, job_id: int) -> Job:
    db.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "awaiting_review")
        .values(status="succeeded", finished_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.flush()
    job = db.get(Job, job_id)
    db.refresh(job)
    return job
