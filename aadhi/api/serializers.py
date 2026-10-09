"""JSON shapes of docs/API.md "Schemas" (User, VersionSummary, ProjectSummary, RenderSummary ...).

List helpers batch their lookups (one query per related table) so list endpoints never N+1.

Lesson stage (``aadhi.stage``): ProjectSummary and the version detail carry ``stage`` and ``next_step`` derived
from stored state only; ProjectSummary also has ``latest_render`` (``{id, status, built_revision,
matches_current}`` or null) and the version detail ``checkpoints`` (the teacher's reviews: source, plan, visuals).
RenderSummary carries ``matches_current`` when its version is known.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from ..config import Settings
from ..jobs.queue import job_summary
from ..models import (
    ACTIVE_JOB_STATUSES,
    VERSION_MUTATING_KINDS,
    Job,
    Project,
    ProjectVersion,
    Render,
    ShareLink,
    SourceDocument,
    User,
)
from ..stage import RenderFacts, StageFacts, derive, matches_current
from .util import iso


def user_dict(user: User) -> dict[str, Any]:
    """User schema (never includes the password hash or token version)."""
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "must_change_password": bool(user.must_change_password),
        "is_active": bool(user.is_active),
        "daily_budget_usd": user.daily_budget_usd,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }


def issue_counts(raw: dict[str, int] | None) -> dict[str, int]:
    """Always report the three severities."""
    counts = {"error": 0, "warning": 0, "info": 0}
    for key, value in (raw or {}).items():
        counts[str(key)] = int(value or 0)
    return counts


REVIEW_STAGE_PLAN = "plan"  # VersionSummary.review_stage of a plan review (the source review's is "source")


def review_stage(v: ProjectVersion) -> str | None:
    """What a version awaiting review waits for: ``"source"`` (the teacher's source check) or ``"plan"``; None for
    any other status. Reads the deferred ``generation_meta`` only for a version awaiting review."""
    if v.status != "awaiting_review":
        return None
    from ..pipeline.source_review import REVIEW_STAGE_KEY, REVIEW_STAGE_SOURCE

    waiting_for_source = (v.generation_meta or {}).get(REVIEW_STAGE_KEY) == REVIEW_STAGE_SOURCE
    return REVIEW_STAGE_SOURCE if waiting_for_source else REVIEW_STAGE_PLAN


def review_stages(db: Session, versions: Iterable[ProjectVersion]) -> dict[int, str]:
    """``review_stage`` of the versions awaiting review, with one query that reads a single key of their
    ``generation_meta`` (never the whole document)."""
    from ..pipeline.source_review import REVIEW_STAGE_KEY, REVIEW_STAGE_SOURCE

    waiting = [v.id for v in versions if v.status == "awaiting_review"]
    if not waiting:
        return {}
    stage = ProjectVersion.generation_meta[REVIEW_STAGE_KEY].as_string()
    query = select(ProjectVersion.id, stage).where(ProjectVersion.id.in_(waiting))
    found = {vid: value for vid, value in db.execute(query)}
    return {vid: REVIEW_STAGE_SOURCE if found.get(vid) == REVIEW_STAGE_SOURCE else REVIEW_STAGE_PLAN for vid in waiting}


def version_summary(v: ProjectVersion, *, stage: str | None = None) -> dict[str, Any]:
    """VersionSummary schema (cheap columns only, never the deferred documents).

    ``review_stage`` is present only while the version awaits review (``review_stage``; pass ``stage`` when it
    was looked up in a batch, see ``review_stages``)."""
    if stage is None:
        stage = review_stage(v)
    out = {
        "id": v.id,
        "project_id": v.project_id,
        "number": v.number,
        "label": v.label or "",
        "status": v.status,
        "language": v.language,
        "revision": v.revision,
        "built_revision": v.built_revision,
        "timeline_stale": v.built_revision != v.revision,
        "has_timeline": bool(v.has_timeline),
        "source_version_id": v.source_version_id,
        "issue_counts": issue_counts(v.issue_counts),
        "created_at": iso(v.created_at),
        "updated_at": iso(v.updated_at),
    }
    if stage is not None and v.status == "awaiting_review":
        out["review_stage"] = stage
    return out


def jobs_summary(job: Job | None) -> dict[str, Any] | None:
    """JobSummary or None."""
    return None if job is None else job_summary(job)


# --- lesson stage ---------------------------------------------------------------------------------------------


def render_facts(db: Session, version_ids: Iterable[int]) -> dict[int, RenderFacts]:
    """``stage.RenderFacts`` of each version that has renders, in two queries (an aggregate, then the latest rows)."""
    ids = sorted({int(v) for v in version_ids if v})
    if not ids:
        return {}
    ok = Render.status == "succeeded"
    agg = (
        select(
            Render.version_id,
            func.max(Render.id),
            func.max(case((ok, Render.built_revision), else_=None)),
            func.sum(case((ok, 1), else_=0)),
        )
        .where(Render.version_id.in_(ids))
        .group_by(Render.version_id)
    )
    rows = db.execute(agg).all()
    latest_ids = [int(r[1]) for r in rows if r[1] is not None]
    latest = {
        rid: (status, built)
        for rid, status, built in db.execute(
            select(Render.id, Render.status, Render.built_revision).where(Render.id.in_(latest_ids))
        )
    } if latest_ids else {}
    out: dict[int, RenderFacts] = {}
    for vid, latest_id, best, succeeded in rows:
        status, built = latest.get(latest_id, (None, None))
        out[int(vid)] = RenderFacts(latest_id=latest_id, latest_status=status, latest_built_revision=built,
                                    best_succeeded_revision=best, succeeded=int(succeeded or 0))
    return out


def stage_job(jobs: Iterable[Job]) -> Job | None:
    """The job that decides a version's stage among its active jobs: a version-mutating one first, else a render."""
    jobs = list(jobs)
    return next((j for j in jobs if j.kind in VERSION_MUTATING_KINDS), None) or next(
        (j for j in jobs if j.kind == "render_video"), None
    )


def stage_fields(project_id: int, v: ProjectVersion | None, *, review: str | None, job: Job | None,
                 renders: RenderFacts | None) -> dict[str, Any]:
    """``{"stage", "next_step"}`` of a project's version (``aadhi.stage.derive``)."""
    facts = StageFacts(
        project_id=project_id,
        version_id=v.id if v is not None else None,
        status=v.status if v is not None else None,
        review_stage=review,
        revision=int(v.revision) if v is not None else 1,
        built_revision=v.built_revision if v is not None else None,
        has_timeline=bool(v.has_timeline) if v is not None else False,
        job_kind=job.kind if job is not None else None,
        job_status=job.status if job is not None else None,
        job_stage=(job.stage or "") if job is not None else "",
        renders=renders or RenderFacts(),
    )
    stage, step = derive(facts)
    return {"stage": stage, "next_step": step}


def latest_render_dict(v: ProjectVersion | None, renders: RenderFacts | None) -> dict[str, Any] | None:
    """``{id, status, built_revision, matches_current}`` of the version's newest render, or None."""
    if v is None or renders is None or renders.latest_id is None:
        return None
    return {
        "id": renders.latest_id,
        "status": renders.latest_status,
        "built_revision": renders.latest_built_revision,
        "matches_current": matches_current(renders.latest_built_revision, v.revision, v.built_revision),
    }


def version_stage(db: Session, v: ProjectVersion) -> dict[str, Any]:
    """``{"stage", "next_step"}`` of one version (its active jobs and renders read in three small queries)."""
    jobs = db.execute(
        select(Job).where(Job.version_id == v.id, Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.id.desc())
    ).scalars()
    return stage_fields(v.project_id, v, review=review_stage(v), job=stage_job(jobs),
                        renders=render_facts(db, [v.id]).get(v.id))


def review_checkpoints(db: Session, v: ProjectVersion, screenplay: Any | None) -> dict[str, Any]:
    """The teacher's review checkpoints of a version, from stored state only.

    ``source`` / ``plan``: ``waiting`` (the generation is paused for it), ``approved`` (a review pause of this
    version was approved: its job ended with that stage) or ``not_requested``; ``source.corrected`` when the
    teacher's corrections were applied. ``visuals``: the Visual Review sign-offs of the scenes with a visual
    (hidden scenes left out), where an approval made before the visual changed counts as ``pending`` and
    ``stale``."""
    from .. import review as visual_review
    from ..pipeline.source_review import OVERRIDES_KEY, REVIEW_STAGE_SOURCE

    meta: Mapping[str, Any] = v.generation_meta or {}
    stage_col = Job.result["stage"].as_string()
    done = set(
        db.execute(
            select(stage_col).where(
                Job.version_id == v.id, Job.kind == "generate_lecture", Job.status == "succeeded",
                stage_col.in_(("source_review", "plan_review")),
            )
        ).scalars()
    )
    waiting = review_stage(v)
    overrides = meta.get(OVERRIDES_KEY)
    corrected = isinstance(overrides, Mapping) and overrides.get("ingest_key") == meta.get("ingest_key") and bool(
        overrides.get("excluded_chunk_ids") or overrides.get("restored_chunk_ids") or overrides.get("concept_names")
    )

    def state(name: str, paused: bool) -> str:
        return "waiting" if paused else "approved" if f"{name}_review" in done else "not_requested"

    visuals = {"total": 0, "approved": 0, "pending": 0, "changed": 0, "removed": 0, "stale": 0}
    if screenplay is not None:
        rows = visual_review.reviews_of(db, v.id)
        for scene in screenplay.scenes:
            if getattr(scene, "hidden", False):
                continue
            slot = visual_review.visual_slot(screenplay, scene)
            view = visual_review.review_view(rows.get(scene.id), visual_review.fingerprint(slot))
            if slot.kind == "none" and view["state"] != "removed":
                continue
            visuals["total"] += 1
            visuals[view["state"]] += 1
            visuals["stale"] += 1 if view["stale"] else 0
    return {
        "source": {"state": state("source", waiting == REVIEW_STAGE_SOURCE), "corrected": corrected},
        "plan": {"state": state("plan", waiting == REVIEW_STAGE_PLAN)},
        "visuals": visuals,
    }


def project_summaries(db: Session, projects: Sequence[Project]) -> list[dict[str, Any]]:
    """ProjectSummary for many projects with batched lookups (owners, current versions and the review stage of
    those awaiting review, active jobs, a summary of the current versions' renders)."""
    if not projects:
        return []
    owner_ids = {p.owner_id for p in projects}
    version_ids = {p.current_version_id for p in projects if p.current_version_id}
    project_ids = [p.id for p in projects]
    owners = {row.id: row.username for row in db.execute(select(User.id, User.username).where(User.id.in_(owner_ids)))}
    versions: dict[int, ProjectVersion] = {}
    if version_ids:
        versions = {
            v.id: v for v in db.execute(select(ProjectVersion).where(ProjectVersion.id.in_(version_ids))).scalars()
        }
    stages = review_stages(db, versions.values())
    active: dict[int, Job] = {}
    version_jobs: dict[int, list[Job]] = {}
    stmt = (
        select(Job).where(Job.project_id.in_(project_ids), Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.id.desc())
    )
    for job in db.execute(stmt).scalars():
        if job.project_id is not None:
            active.setdefault(job.project_id, job)
        if job.version_id is not None and job.version_id in versions:
            version_jobs.setdefault(job.version_id, []).append(job)
    facts = render_facts(db, versions)
    out = []
    for p in projects:
        cv = versions.get(p.current_version_id) if p.current_version_id else None
        renders = facts.get(cv.id) if cv is not None else None
        out.append(
            {
                "id": p.id,
                "title": p.title,
                "subject_name": p.subject_name,
                "unit_name": p.unit_name,
                "session_number": p.session_number,
                "session_title": p.session_title,
                "language": p.language,
                "owner": {"id": p.owner_id, "username": owners.get(p.owner_id, "")},
                "current_version": version_summary(cv, stage=stages.get(cv.id)) if cv is not None else None,
                "active_job": jobs_summary(active.get(p.id)),
                **stage_fields(
                    p.id,
                    cv,
                    review=stages.get(cv.id) if cv is not None else None,
                    job=stage_job(version_jobs.get(cv.id, ())) if cv is not None else None,
                    renders=renders,
                ),
                "latest_render": latest_render_dict(cv, renders),
                "created_at": iso(p.created_at),
                "updated_at": iso(p.updated_at),
            }
        )
    return out


def project_summary(db: Session, project: Project) -> dict[str, Any]:
    """ProjectSummary for one project."""
    return project_summaries(db, [project])[0]


def source_dict(src: SourceDocument) -> dict[str, Any]:
    """Source document listing entry (never the storage key)."""
    return {
        "id": src.id,
        "filename": src.filename,
        "mime": src.mime,
        "size_bytes": src.size_bytes,
        "page_count": src.page_count,
        "created_at": iso(src.created_at),
    }


def render_downloads(render: Render) -> dict[str, str]:
    """Download links for the files a render produced."""
    out: dict[str, str] = {}
    for name, key in (("video", render.video_asset_key), ("srt", render.srt_asset_key), ("vtt", render.vtt_asset_key)):
        if key:
            out[name] = f"/api/renders/{render.id}/download?file={name}"
    return out


def render_summary(render: Render, job: Job | None, version: ProjectVersion | None = None) -> dict[str, Any]:
    """RenderSummary schema. With its ``version``: ``matches_current`` (made from the version's current revision and
    the timeline is not stale)."""
    out = {
        "id": render.id,
        "version_id": render.version_id,
        "status": render.status,
        "language": render.language,
        "built_revision": render.built_revision,
        "duration_s": render.duration_s,
        "chapters_text": render.chapters_text or "",
        "downloads": render_downloads(render),
        "options": dict(render.options or {}),
        "job": jobs_summary(job),
        "created_at": iso(render.created_at),
    }
    if version is not None and version.id == render.version_id:
        out["matches_current"] = matches_current(render.built_revision, version.revision, version.built_revision)
    return out


def render_summaries(db: Session, renders: Iterable[Render], version: ProjectVersion | None = None) -> list[dict[str, Any]]:
    """RenderSummary for many renders (jobs loaded in one query); ``version``: see ``render_summary``."""
    renders = list(renders)
    job_ids = {r.job_id for r in renders if r.job_id}
    jobs: dict[int, Job] = {}
    if job_ids:
        jobs = {j.id: j for j in db.execute(select(Job).where(Job.id.in_(job_ids))).scalars()}
    return [render_summary(r, jobs.get(r.job_id) if r.job_id else None, version) for r in renders]


def share_url(settings: Settings, token: str) -> str:
    """Public watch URL for a share token."""
    return f"{settings.base_url.rstrip('/')}/watch/{token}"


def share_dict(share: ShareLink, settings: Settings) -> dict[str, Any]:
    """Share link listing entry."""
    return {
        "token": share.token,
        "url": share_url(settings, share.token),
        "version_id": share.version_id,
        "created_at": iso(share.created_at),
        "expires_at": iso(share.expires_at),
        "revoked_at": iso(share.revoked_at),
        "view_count": share.view_count,
    }
