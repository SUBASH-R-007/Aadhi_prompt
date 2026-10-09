"""The ``cleanup`` job: retention for job events / analytics and GC of orphaned generated assets.

* job_events of finished jobs (succeeded/failed/cancelled) that finished more than
  ``JOB_EVENT_RETENTION_DAYS`` ago;
* analytics_events older than ``ANALYTICS_RETENTION_DAYS``;
* generation claims (``asset_claims``) that expired more than an hour ago: leftovers of workers that
  stopped mid-generation (live holders renew theirs; finished ones delete them);
* this host's work files (not in a dry run): Manim temp dirs of dead processes and sandbox containers
  past their deadline (``manim.sandbox.sweep_orphans``), render workspaces that can no longer be
  resumed and old ``aadhi-render-*`` temp dirs (``compose.video.sweep_render_workspaces``). Worker
  processes also sweep their own host hourly (``Worker.sweep_host_once``);
* assets (row first, then blob) of kinds tts, scene_audio, manim, image, video, poster, screenshot,
  intermediate that are older than 30 days and have no ``asset_refs`` row and are in nobody's media
  library (``library_items``). Sources, uploads, figures, extracts, renders and captions are never
  touched. Defence in depth: keys still
  mentioned in any version document (screenplay, manifest, timeline, generation_meta) are kept,
  and no asset row is deleted while a version-mutating or render job is *running* (it could be
  re-using a cached asset that is not referenced yet) unless ``force_assets`` is set. That guard
  is part of every DELETE statement (``AND NOT EXISTS (running build/render job)``), so it is
  atomic with the delete; queued jobs and jobs awaiting review touch no assets and never block GC.

Payload (all optional): ``{"dry_run": bool, "force_assets": bool, "scan_documents": bool}``.
Scheduling (``ensure_cleanup_scheduled``) is one atomic ``INSERT ... SELECT ... WHERE NOT EXISTS``
(serialised by an advisory lock on Postgres), so concurrent worker processes enqueue one job.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Iterable
from typing import Any

from sqlalchemy import delete, exists, func, insert, literal, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..db import get_sessionmaker
from ..models import (
    ACTIVE_JOB_STATUSES,
    VERSION_MUTATING_KINDS,
    AnalyticsEvent,
    Asset,
    AssetClaim,
    AssetRef,
    Job,
    JobEvent,
    LibraryItem,
    ProjectVersion,
    utcnow,
)
from ..storage import get_storage
from ..storage.base import Storage
from .base import JobContext, job_handler
from .queue import TERMINAL_STATUSES

log = logging.getLogger(__name__)

GC_ASSET_KINDS = ("tts", "scene_audio", "manim", "image", "video", "poster", "screenshot", "intermediate")
GC_BLOCKING_KINDS = (*VERSION_MUTATING_KINDS, "render_video")
ORPHAN_ASSET_MIN_AGE_DAYS = 30
EXPIRED_CLAIM_GRACE_SECONDS = 3600  # claims expired longer than this are certainly abandoned
CLEANUP_INTERVAL_HOURS = 24
CLEANUP_PRIORITY = 1000
BATCH = 500
_DOCUMENT_COLUMNS = ("screenplay", "asset_manifest", "timeline", "generation_meta")
_SCHEDULE_LOCK_KEY = 0x0AAD_C1EA  # pg advisory lock: one scheduler at a time


def _batched_delete(
    factory: sessionmaker[Session], ids_query: Any, model: Any, *, dry_run: bool, check: Callable[[], None] | None
) -> int:
    """Delete rows selected by ``ids_query`` (a SELECT of ids with LIMIT) in short transactions."""
    total = 0
    if dry_run:
        with factory() as db:
            return len(db.execute(ids_query.limit(None)).all())
    while True:
        if check is not None:
            check()
        with factory() as db:
            ids = [r[0] for r in db.execute(ids_query).all()]
            if not ids:
                return total
            db.execute(delete(model).where(model.id.in_(ids)).execution_options(synchronize_session=False))
            db.commit()
        total += len(ids)


def _strings(value: Any) -> Iterable[str]:
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            yield item
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list | tuple):
            stack.extend(item)


def _referenced_in_documents(factory: sessionmaker[Session], candidates: set[str]) -> set[str]:
    """Candidate keys mentioned anywhere in version documents (exact string or path segment)."""
    found: set[str] = set()
    if not candidates:
        return found
    last_id = 0
    cols = [getattr(ProjectVersion, c) for c in _DOCUMENT_COLUMNS]
    while True:
        with factory() as db:
            rows = db.execute(
                select(ProjectVersion.id, *cols)
                .where(ProjectVersion.id > last_id)
                .order_by(ProjectVersion.id)
                .limit(50)
            ).all()
        if not rows:
            return found
        for row in rows:
            last_id = row[0]
            for doc in row[1:]:
                for s in _strings(doc):
                    if s in candidates:
                        found.add(s)
                    elif "/" in s:
                        found.update(seg for seg in s.split("/") if seg in candidates)


def _sweep_claims(factory: sessionmaker[Session], cutoff: dt.datetime, *, dry_run: bool) -> int:
    """Delete generation claims that expired before ``cutoff`` (one statement: claims are few). A
    database without the table (migration 0003 not applied yet) skips this step, not the others."""
    expired = AssetClaim.expires_at < cutoff
    try:
        with factory() as db:
            if dry_run:
                return int(db.execute(select(func.count()).select_from(AssetClaim).where(expired)).scalar_one())
            deleted = db.execute(delete(AssetClaim).where(expired).execution_options(synchronize_session=False))
            db.commit()
            return int(deleted.rowcount or 0)
    except (OperationalError, ProgrammingError) as exc:
        log.warning("cleanup: generation claims not swept (%s)", type(exc).__name__)
        return 0


def _sweep_host_files(settings: Settings, factory: sessionmaker[Session]) -> dict[str, int]:
    """Best effort: a failure here is logged and never stops the database retention steps."""
    out: dict[str, int] = {}
    try:
        from ..manim.sandbox import sweep_orphans

        out.update({f"manim_{k}": v for k, v in sweep_orphans(settings).items()})
    except Exception as exc:  # noqa: BLE001 - housekeeping only
        log.warning("cleanup: Manim leftovers not swept (%s)", type(exc).__name__)
    try:
        from ..compose.video import sweep_render_workspaces

        out["render_workspaces"] = sweep_render_workspaces(settings, factory)
    except Exception as exc:  # noqa: BLE001 - housekeeping only
        log.warning("cleanup: render workspaces not swept (%s)", type(exc).__name__)
    return out


def _running_blocker(exclude_job_id: int | None) -> Any:
    """``EXISTS`` a running build/render job (other than ``exclude_job_id``)."""
    stmt = select(Job.id).where(Job.status == "running", Job.kind.in_(GC_BLOCKING_KINDS))
    if exclude_job_id is not None:
        stmt = stmt.where(Job.id != exclude_job_id)
    return exists(stmt)


def _gc_blocked(factory: sessionmaker[Session], exclude_job_id: int | None) -> bool:
    with factory() as db:
        return bool(db.execute(select(_running_blocker(exclude_job_id))).scalar())


def _gc_assets(
    factory: sessionmaker[Session],
    storage: Storage,
    cutoff: dt.datetime,
    *,
    dry_run: bool,
    scan_documents: bool,
    guard: Any | None,
    check: Callable[[], None] | None,
) -> dict[str, Any]:
    # Kept while a lecture refers to it or it is in somebody's media library (aadhi.library).
    unreferenced = ~exists(select(AssetRef.id).where(AssetRef.asset_key == Asset.key)) & ~exists(
        select(LibraryItem.id).where(LibraryItem.asset_key == Asset.key)
    )
    candidates: dict[int, str] = {}
    last_id = 0
    while True:
        with factory() as db:
            rows = db.execute(
                select(Asset.id, Asset.key)
                .where(Asset.kind.in_(GC_ASSET_KINDS), Asset.created_at < cutoff, unreferenced, Asset.id > last_id)
                .order_by(Asset.id)
                .limit(BATCH)
            ).all()
        if not rows:
            break
        for asset_id, key in rows:
            candidates[asset_id] = key
            last_id = asset_id
    protected = _referenced_in_documents(factory, set(candidates.values())) if scan_documents else set()
    ids = [i for i, k in candidates.items() if k not in protected]
    out: dict[str, Any] = {"assets": 0, "blobs": 0, "blob_errors": 0, "protected_by_documents": len(protected)}
    if dry_run:
        out["assets"] = len(ids)
        return out
    conditions = [unreferenced, Asset.kind.in_(GC_ASSET_KINDS)]
    if guard is not None:
        conditions.append(~guard)
    for start in range(0, len(ids), BATCH):
        if check is not None:
            check()
        chunk = ids[start : start + BATCH]
        with factory() as db:
            # Re-check "unreferenced" and "no running build" atomically in the DELETE itself;
            # RETURNING gives the blobs to remove.
            deleted = db.execute(
                delete(Asset)
                .where(Asset.id.in_(chunk), *conditions)
                .returning(Asset.storage_key)
                .execution_options(synchronize_session=False)
            ).all()
            blocked = not deleted and guard is not None and bool(db.execute(select(guard)).scalar())
            db.commit()
        if blocked:
            out["assets_skipped"] = "active jobs"
            log.info("cleanup: asset GC stopped (a build/render job started running)")
            break
        out["assets"] += len(deleted)
        for (storage_key,) in deleted:  # rows are gone: blobs can no longer be handed out
            try:
                storage.delete(storage_key)
                out["blobs"] += 1
            except Exception as exc:  # noqa: BLE001
                out["blob_errors"] += 1
                log.warning("cleanup: could not delete blob %s: %s", storage_key, get_settings().redact(str(exc)))
    return out


def run_cleanup(
    settings: Settings | None = None,
    *,
    storage: Storage | None = None,
    session_factory: sessionmaker[Session] | None = None,
    now: dt.datetime | None = None,
    dry_run: bool = False,
    force_assets: bool = False,
    scan_documents: bool = True,
    exclude_job_id: int | None = None,
    check_cancelled: Callable[[], None] | None = None,
    progress: Callable[[str, float, str], None] | None = None,
) -> dict[str, Any]:
    """Run every retention step (blocking; call via ``asyncio.to_thread``). Returns counts."""
    settings = settings or get_settings()
    factory = session_factory or get_sessionmaker()
    storage = storage or get_storage()
    now = now or utcnow()

    def report(fraction: float, message: str) -> None:
        if progress is not None:
            progress("cleanup", fraction, message)

    out: dict[str, Any] = {"dry_run": dry_run}
    report(0.05, "Removing old job events")
    event_cutoff = now - dt.timedelta(days=settings.job_event_retention_days)
    finished_jobs = select(Job.id).where(
        Job.status.in_(TERMINAL_STATUSES), Job.finished_at.is_not(None), Job.finished_at < event_cutoff
    )
    if exclude_job_id is not None:
        finished_jobs = finished_jobs.where(Job.id != exclude_job_id)
    events_q = select(JobEvent.id).where(JobEvent.job_id.in_(finished_jobs)).order_by(JobEvent.id).limit(BATCH * 4)
    out["job_events"] = _batched_delete(factory, events_q, JobEvent, dry_run=dry_run, check=check_cancelled)

    report(0.3, "Removing old analytics")
    analytics_cutoff = now - dt.timedelta(days=settings.analytics_retention_days)
    analytics_q = (
        select(AnalyticsEvent.id)
        .where(AnalyticsEvent.created_at < analytics_cutoff)
        .order_by(AnalyticsEvent.id)
        .limit(BATCH * 4)
    )
    out["analytics_events"] = _batched_delete(
        factory, analytics_q, AnalyticsEvent, dry_run=dry_run, check=check_cancelled
    )

    report(0.45, "Removing abandoned generation claims")
    out["asset_claims"] = _sweep_claims(
        factory, now - dt.timedelta(seconds=EXPIRED_CLAIM_GRACE_SECONDS), dry_run=dry_run
    )

    if not dry_run:
        report(0.5, "Removing stale work files on this host")
        out.update(_sweep_host_files(settings, factory))

    report(0.55, "Collecting orphaned assets")
    guard = None if force_assets else _running_blocker(exclude_job_id)
    if guard is not None and _gc_blocked(factory, exclude_job_id):  # cheap early exit
        out.update(assets=0, blobs=0, blob_errors=0, assets_skipped="active jobs")
        log.info("cleanup: asset GC skipped (a build/render job is running)")
    else:
        asset_cutoff = now - dt.timedelta(days=ORPHAN_ASSET_MIN_AGE_DAYS)
        out.update(
            _gc_assets(
                factory,
                storage,
                asset_cutoff,
                dry_run=dry_run,
                scan_documents=scan_documents,
                guard=guard,
                check=check_cancelled,
            )
        )
    report(1.0, "Cleanup finished")
    return out


def _other_cleanup_running(factory: sessionmaker[Session], job_id: int) -> list[int]:
    with factory() as db:
        stmt = select(Job.id).where(Job.kind == "cleanup", Job.status == "running", Job.id != job_id).limit(10)
        return [int(i) for i in db.execute(stmt).scalars()]


@job_handler("cleanup")
async def cleanup_job(ctx: JobContext) -> dict[str, Any]:
    """Job handler for ``cleanup`` (payload: dry_run, force_assets, scan_documents)."""
    payload = ctx.payload or {}
    others = await asyncio.to_thread(_other_cleanup_running, get_sessionmaker(), ctx.job_id)
    if others:
        ctx.log("Another cleanup job is already running; nothing to do", running=others)
        return {"skipped": "another cleanup job is running", "running": others}
    deleted = await asyncio.to_thread(
        run_cleanup,
        ctx.settings,
        storage=ctx.storage,
        dry_run=bool(payload.get("dry_run", False)),
        force_assets=bool(payload.get("force_assets", False)),
        scan_documents=bool(payload.get("scan_documents", True)),
        exclude_job_id=ctx.job_id,
        check_cancelled=ctx.check_cancelled,
        progress=ctx.progress,
    )
    ctx.log("Cleanup finished", **{k: v for k, v in deleted.items() if isinstance(v, int | str | bool)})
    return {"deleted": deleted}


def ensure_cleanup_scheduled(db: Session, *, interval_hours: float = CLEANUP_INTERVAL_HOURS) -> Job | None:
    """Enqueue a low-priority ``cleanup`` job unless one is active or was created within
    ``interval_hours``. Returns the new job (the caller commits) or None.

    ONE atomic ``INSERT INTO jobs ... SELECT ... WHERE NOT EXISTS (recent or active cleanup)``; on
    Postgres the statement is additionally serialised with ``pg_try_advisory_xact_lock`` (READ
    COMMITTED would let two concurrent inserts both see "no cleanup yet").
    """
    now = utcnow()
    since = now - dt.timedelta(hours=interval_hours)
    if db.get_bind().dialect.name == "postgresql":
        if not db.execute(select(func.pg_try_advisory_xact_lock(_SCHEDULE_LOCK_KEY))).scalar():
            return None  # another process is scheduling right now
    recent_or_active = exists(
        select(Job.id).where(Job.kind == "cleanup", (Job.created_at >= since) | Job.status.in_(ACTIVE_JOB_STATUSES))
    )
    table = Job.__table__
    values: dict[str, Any] = {
        "kind": "cleanup",
        "status": "queued",
        "stage": "",
        "progress": 0.0,
        "message": "",
        "payload": {},
        "attempts": 0,
        "max_attempts": 1,
        "priority": CLEANUP_PRIORITY,
        "run_after": now,
        "cancel_requested": False,
        "cost_usd": 0.0,
        "created_at": now,
    }
    source = select(*[literal(v, type_=table.c[k].type) for k, v in values.items()]).where(~recent_or_active)
    stmt = insert(Job).from_select(list(values), source, include_defaults=False).returning(Job.id)
    new_id = db.execute(stmt).scalar_one_or_none()
    if new_id is None:
        return None
    return db.get(Job, int(new_id))
