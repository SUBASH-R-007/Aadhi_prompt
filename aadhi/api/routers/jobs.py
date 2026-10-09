"""``/api/jobs``: list, detail, events, SSE stream, cancel, retry.

Access: the job's user, the owner of its (non-deleted) project, or an admin. Otherwise 404.
A retry runs under the requester (their API keys and budget), so it is checked like a new request:
409 ``engine_not_configured`` when the requester cannot run the job's AI engine, then the budget. A
``render_video`` retry passes the render admission of ``POST /render``: 409 ``job_in_progress`` while
another render of the version is active, 429 ``render_busy`` / ``render_queue_full`` (``Retry-After``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from ...config import Settings
from ...jobs import events as job_events
from ...jobs import queue as job_queue
from ...jobs.events import list_events, stream_job
from ...jobs.queue import job_summary, request_cancel, retry_job
from ...models import ACTIVE_JOB_STATUSES, JOB_STATUSES, Job, Project, ProjectVersion, Render, User
from ...pipeline.base import GenerationOptions
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, is_admin, load_project, valid_id
from ..errors import ApiException, not_found
from ..generation import (
    charge_generation_rate,
    commit_and_notify,
    ensure_budget,
    ensure_engine_available,
    request_keys,
    version_options,
)
from ..sse import event_stream_response
from ..util import page_params
from .renders import active_render_job, check_admission

router = APIRouter(prefix="/api/jobs", tags=["jobs"], dependencies=RATE_LIMITED)

GENERATION_KINDS = ("generate_lecture", "regenerate_scene", "translate")
BUILD_KINDS = ("build_assets",)
ADMIN_KINDS = ("import_legacy", "cleanup")
RETRYABLE_STATUSES = ("failed", "cancelled")
# Raised by stream_job when a stream cap is reached (an empty tuple catches nothing if absent).
TOO_MANY_STREAMS: type[Exception] | tuple[()] = getattr(job_events, "TooManyStreams", ())
# Raised by retry_job for a job that is not (or no longer) retryable, e.g. already retried.
INVALID_JOB_STATE: type[Exception] | tuple[()] = getattr(job_queue, "InvalidJobState", ())
# error_code the queue stamps on a job once it has been retried (each job is retried at most once).
RETRIED_ERROR_CODE: str = getattr(job_queue, "RETRIED_ERROR_CODE", "retried")


def _not_retryable(message: str = "Only failed or cancelled jobs can be retried.") -> ApiException:
    return ApiException(409, "job_not_retryable", message)


def _admit_render_retry(db: Session, user: User, settings: Settings, job: Job) -> None:
    """The admission rules of ``POST /api/versions/{vid}/render`` for a retried render (raises)."""
    running = active_render_job(db, job.version_id) if job.version_id is not None else None
    if running is not None:
        raise ApiException(409, "job_in_progress", "A render of this version is already running.", job_id=running)
    check_admission(db, user.id, settings)


def load_job(db: Session, user: User, job_id: int) -> Job:
    """Job visible to the user or 404."""
    job = db.get(Job, job_id) if valid_id(job_id) else None
    if job is None:
        raise not_found("Job")
    if is_admin(user) or job.user_id == user.id:
        return job
    if job.project_id is not None:
        owner = db.execute(
            select(Project.owner_id).where(Project.id == job.project_id, Project.deleted_at.is_(None))
        ).scalar_one_or_none()
        if owner == user.id:
            return job
    raise not_found("Job")


def _job_options(db: Session, job: Job) -> GenerationOptions:
    """Options a retry of ``job`` runs with: its payload options, else its version's (for a translation
    its SOURCE version's, as ``orchestrator._translate`` uses), else the defaults."""
    payload = job.payload or {}
    raw = payload.get("options")
    if isinstance(raw, dict) and raw:
        try:
            return GenerationOptions.model_validate(raw)
        except ValidationError:
            pass
    version_id = job.version_id
    source_id = payload.get("source_version_id")
    if job.kind == "translate" and isinstance(source_id, int) and not isinstance(source_id, bool):
        version_id = source_id
    version = db.get(ProjectVersion, version_id) if version_id is not None else None
    if version is not None:
        return version_options(version, db.get(Project, version.project_id))
    return GenerationOptions()


def _statuses(raw: str | None) -> list[str]:
    if not raw:
        return []
    values = [s.strip() for s in raw.split(",") if s.strip()]
    bad = [s for s in values if s not in JOB_STATUSES]
    if bad:
        raise ApiException(
            422,
            "validation",
            [{"loc": ["query", "status"], "msg": f"Unknown status {bad[0]!r}", "type": "value_error"}],
        )
    return values


@router.get("")
def list_jobs(
    user: CurrentUser,
    db: DbSession,
    project_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Jobs visible to the user, newest first."""
    limit, offset = page_params(limit, offset)
    stmt = select(Job)
    if project_id is not None:
        project = load_project(db, user, project_id)
        stmt = stmt.where(Job.project_id == project.id)
    elif not is_admin(user):
        own_projects = select(Project.id).where(Project.owner_id == user.id, Project.deleted_at.is_(None))
        stmt = stmt.where(or_(Job.user_id == user.id, Job.project_id.in_(own_projects)))
    statuses = _statuses(status)
    if statuses:
        stmt = stmt.where(Job.status.in_(statuses))
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(stmt.order_by(Job.id.desc()).limit(limit).offset(offset)).scalars()
    return {"items": [job_summary(j) for j in rows], "total": int(total)}


@router.get("/{job_id}")
def get_job(job_id: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """JobSummary + result."""
    job = load_job(db, user, job_id)
    return {**job_summary(job), "result": job.result}


@router.get("/{job_id}/events")
def get_events(job_id: int, user: CurrentUser, db: DbSession, after: int = 0, limit: int = 200) -> dict[str, Any]:
    """Events after ``after`` (event id), oldest first."""
    job = load_job(db, user, job_id)
    if limit < 1 or limit > 500:
        raise ApiException(
            422, "validation", [{"loc": ["query", "limit"], "msg": "limit must be 1..500", "type": "value_error"}]
        )
    return {"items": list_events(db, job.id, after_id=max(0, after), limit=limit)}


def _last_event_id(request: Request) -> int:
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id") or "0"
    try:
        return max(0, int(raw.strip()))
    except ValueError:
        return 0


@router.get("/{job_id}/stream")
def stream(job_id: int, request: Request, user: CurrentUser, db: DbSession, settings: AppSettings) -> StreamingResponse:
    """Server-sent events until the job ends (per-user/global stream caps -> 429).

    The request-scoped session is closed before the body streams; ``stream_job`` opens a short
    session per poll and re-checks access (job owner, project owner, admin) periodically.
    """
    job = load_job(db, user, job_id)
    try:
        events = stream_job(job.id, last_event_id=_last_event_id(request), user_id=user.id, settings=settings)
    except TOO_MANY_STREAMS as exc:
        retry = str(int(getattr(exc, "retry_after", 5) or 5))
        raise ApiException(
            429, "rate_limited", "Too many open progress streams.", headers={"Retry-After": retry}
        ) from None
    return event_stream_response(events)


@router.post("/{job_id}/cancel", status_code=202)
def cancel(job_id: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Request cancellation (idempotent: finished jobs are returned unchanged)."""
    job = load_job(db, user, job_id)
    if job.status in ACTIVE_JOB_STATUSES:
        request_cancel(db, job.id)
        db.commit()
        db.refresh(job)
    return {"job": job_summary(job)}


@router.post("/{job_id}/retry", status_code=202)
def retry(job_id: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """New job with the same payload (failed/cancelled jobs only)."""
    job = load_job(db, user, job_id)
    if job.status not in RETRYABLE_STATUSES:
        raise _not_retryable()
    if job.error_code == RETRIED_ERROR_CODE:
        raise _not_retryable("This job was already retried; retry the newest attempt instead.")
    if job.kind in ADMIN_KINDS and not is_admin(user):
        raise ApiException(403, "forbidden", "Only administrators can retry this job.")
    if job.project_id is not None:
        load_project(db, user, job.project_id)
    if job.kind in GENERATION_KINDS or job.kind in BUILD_KINDS:
        # Checked before retry_job: a refused retry neither claims the old job nor touches the version.
        keys = request_keys(db, user, settings)
        opts = _job_options(db, job)
        engine = opts.llm_provider or settings.llm_provider
        if job.kind in GENERATION_KINDS:
            ensure_engine_available(engine, settings, keys)  # the new job runs on the requester's keys
        ensure_budget(db, user, settings, engine=engine, keys=keys, options=opts)
    if job.kind == "render_video":
        _admit_render_retry(db, user, settings, job)
    try:
        new_job = retry_job(db, job.id, user_id=user.id)
    except INVALID_JOB_STATE:  # lost a race with a concurrent retry / state change
        db.rollback()
        raise _not_retryable("This job was already retried; retry the newest attempt instead.") from None
    if job.kind in GENERATION_KINDS:
        # Only now: a retry refused above (409, e.g. a build still running) costs no generation token.
        charge_generation_rate(db, user, settings)
    if new_job.kind == "render_video":
        render_id = (job.payload or {}).get("render_id")
        if isinstance(render_id, int):
            db.execute(
                update(Render)
                .where(Render.id == render_id)
                .values(job_id=new_job.id, status="queued")
                .execution_options(synchronize_session=False)
            )
    commit_and_notify(db, settings)
    db.refresh(new_job)
    return {"job": job_summary(new_job)}
