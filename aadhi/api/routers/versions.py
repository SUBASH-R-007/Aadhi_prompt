"""``/api/versions/{vid}``: detail, timeline (+ preview), screenplay edits, lint."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy import exists, select, update

from ...models import ACTIVE_JOB_STATUSES, VERSION_MUTATING_KINDS, Job, Project, ProjectVersion
from ...schemas.jsonsafe import json_safe, validate_stored
from ...schemas.manifest import AssetManifest
from ...schemas.screenplay import Screenplay
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, load_version
from ..errors import ApiException, not_found
from ..generation import active_mutating_job
from ..screenplay_service import (
    authorize_asset_keys,
    hidden_scene_ids,
    issues_payload,
    lint_screenplay,
    merge_issues,
    options_for,
    served_issues,
    source_chunk_ids,
    source_ingest,
    stale_scenes,
)
from ..serializers import review_checkpoints, version_stage, version_summary
from ..storable import StorableBody, ensure_storable
from ..timelines import etag_matches, resolve_timeline, timeline_etag
from ..util import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/versions", tags=["versions"], dependencies=RATE_LIMITED)


class ScreenplayBody(StorableBody):
    """A (possibly unsaved) screenplay draft (no NaN/Infinity or unstorable text: 422)."""

    model_config = ConfigDict(extra="ignore")

    screenplay: Screenplay


class ScreenplayPut(StorableBody):
    """A screenplay edit based on ``revision`` (optimistic concurrency; no NaN/Infinity or unstorable text)."""

    model_config = ConfigDict(extra="ignore")

    screenplay: Screenplay
    revision: int = Field(ge=1)


def safe_screenplay(version: ProjectVersion) -> Screenplay | None:
    """Stored screenplay, or None when absent/unreadable (logged). Non-finite numbers and lone surrogates
    stored before writes refused them are read as 0 / U+FFFD (``validate_stored``)."""
    try:
        return None if version.screenplay is None else validate_stored(Screenplay, version.screenplay)
    except ValidationError:
        logger.warning("version %s has a screenplay that no longer validates", version.id)
        return None


def safe_manifest(version: ProjectVersion) -> AssetManifest | None:
    """Stored asset manifest, or None when absent/unreadable (logged; legacy unstorable values sanitised)."""
    try:
        return None if version.asset_manifest is None else validate_stored(AssetManifest, version.asset_manifest)
    except ValidationError:
        logger.warning("version %s has an asset manifest that no longer validates", version.id)
        return None


def require_version_language(version: ProjectVersion, screenplay: Screenplay) -> None:
    """422 when an edited screenplay's narration language differs from the version's.

    The version (its timeline, TTS voices, translation checks) is keyed by ``version.language``;
    changing the language is what ``POST /api/versions/{vid}/translate`` is for.
    """
    if screenplay.language != version.language:
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["body", "screenplay", "language"],
                    "msg": (
                        f"The screenplay language {screenplay.language!r} does not match this version "
                        f"({version.language!r}); use translate to create a version in another language."
                    ),
                    "type": "value_error",
                }
            ],
        )


def _plan_dict(version: ProjectVersion) -> dict[str, Any] | None:
    if version.status != "awaiting_review":
        return None
    from ...pipeline.plan_state import load_plan

    plan = load_plan(version)
    return None if plan is None else plan.model_dump(mode="json")


@router.get("/{vid}")
def get_version(vid: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """VersionSummary + screenplay, issues, stale scenes, generation meta and (in review) the plan.

    Stored documents are served through ``json_safe``: a version saved before NaN/Infinity and lone
    surrogates were refused still loads (those values as ``null`` / U+FFFD) instead of failing with a 500.
    ``stage`` / ``next_step`` (``aadhi.stage``) and ``checkpoints`` (the teacher's reviews) are derived from
    stored state.
    """
    version = load_version(db, user, vid)
    screenplay = safe_screenplay(version)
    meta = dict(version.generation_meta or {})
    meta.pop("plan", None)
    return {
        **version_summary(version),
        **version_stage(db, version),
        "checkpoints": review_checkpoints(db, version, screenplay),
        "screenplay": json_safe(version.screenplay),
        # a hidden scene's issues as notes (stored at their real severity: showing the scene brings it back)
        "issues": json_safe(served_issues(list(version.issues or []), hidden_scene_ids(version.screenplay))),
        "stale_scenes": stale_scenes(screenplay, safe_manifest(version)),
        "generation_meta": json_safe(meta),
        "plan": json_safe(_plan_dict(version)),
    }


@router.get("/{vid}/timeline")
def get_timeline(
    vid: int, request: Request, user: CurrentUser, db: DbSession, settings: AppSettings, store: Store
) -> Response:
    """Timeline with URLs resolved at serve time; ETag + If-None-Match."""
    version = load_version(db, user, vid)
    etag = timeline_etag(version, settings)
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    # 304 without loading the deferred (large) timeline column; has_timeline is set with it.
    if version.has_timeline and etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=headers)
    stored = version.timeline
    if stored is None:
        raise not_found("Timeline")
    return JSONResponse(resolve_timeline(stored, store, settings), headers=headers)


@router.post("/{vid}/timeline/preview")
def preview_timeline(
    vid: int, body: ScreenplayBody, user: CurrentUser, db: DbSession, settings: AppSettings, store: Store
) -> dict[str, Any]:
    """Editor preview of an unsaved screenplay (estimated timings where no clip matches)."""
    from ...compose.timeline import preview_timeline as build_preview

    version = load_version(db, user, vid)
    require_version_language(version, body.screenplay)
    authorize_asset_keys(db, version.project_id, body.screenplay)
    try:
        timeline = build_preview(body.screenplay, safe_manifest(version), settings=settings, version_id=version.id)
    except ValueError as exc:
        raise ApiException(
            422, "validation", [{"loc": ["body", "screenplay"], "msg": settings.redact(str(exc)), "type": "timeline"}]
        ) from None
    return resolve_timeline(timeline, store, settings)


@router.put("/{vid}/screenplay")
def put_screenplay(vid: int, body: ScreenplayPut, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """Save an edited screenplay (optimistic concurrency on ``revision``; refused while a job mutates it)."""
    version = load_version(db, user, vid)
    busy = active_mutating_job(db, version.id)
    if busy is not None:
        raise ApiException(409, "version_busy", "A job is currently modifying this version.", job_id=busy.id)
    if body.revision != version.revision:
        raise ApiException(
            409, "revision_conflict", "The screenplay was changed elsewhere.", current_revision=version.revision
        )
    require_version_language(version, body.screenplay)
    authorize_asset_keys(db, version.project_id, body.screenplay)
    document = body.screenplay.model_dump(mode="json")
    ensure_storable(document, ("body", "screenplay"))  # never write a row that later reads cannot serve
    project = db.get(Project, version.project_id)
    old = safe_screenplay(version)
    lint_issues = lint_screenplay(
        body.screenplay,
        options_for(project) if project is not None else None,
        chunk_ids=source_chunk_ids(store, version.generation_meta),
        ingest=source_ingest(store, version.generation_meta),
    )
    hidden = hidden_scene_ids(body.screenplay)  # their issues are stored as they are, served and counted as notes
    dumped, counts = issues_payload(merge_issues(old, body.screenplay, version.issues, lint_issues), hidden)
    now = utcnow()
    job_active = exists().where(
        Job.version_id == version.id, Job.status.in_(ACTIVE_JOB_STATUSES), Job.kind.in_(VERSION_MUTATING_KINDS)
    )
    result = db.execute(
        update(ProjectVersion)
        .where(ProjectVersion.id == version.id, ProjectVersion.revision == body.revision, ~job_active)
        .values(
            revision=body.revision + 1,
            screenplay=document,
            issues=dumped,
            issue_counts=counts,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if (result.rowcount or 0) == 0:
        db.rollback()
        busy = active_mutating_job(db, version.id)
        if busy is not None:
            raise ApiException(409, "version_busy", "A job is currently modifying this version.", job_id=busy.id)
        current = db.execute(
            select(ProjectVersion.revision).where(ProjectVersion.id == version.id)
        ).scalar_one_or_none()
        raise ApiException(409, "revision_conflict", "The screenplay was changed elsewhere.", current_revision=current)
    db.execute(
        update(Project)
        .where(Project.id == version.project_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(version)
    return {
        "version": version_summary(version),
        "issues": served_issues(dumped, hidden),
        "stale_scenes": stale_scenes(body.screenplay, safe_manifest(version)),
    }


@router.post("/{vid}/lint")
def lint_version(vid: int, body: ScreenplayBody, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """Lint a (possibly unsaved) screenplay with the project's generation options.

    ``quality`` (additive): safe repairs for some of the issues (exact field edits the editor applies as an
    undoable change) and the lesson's consistency registry (``aadhi.pipeline.quality.quality_report``). The quality
    analysis runs once and serves both the issues and the report.
    """
    from ...pipeline.quality import analysis_of, quality_report
    from ...pipeline.validate import lint

    version = load_version(db, user, vid)
    project = db.get(Project, version.project_id)
    analysis = analysis_of(body.screenplay)
    issues = lint(
        body.screenplay,
        options_for(project) if project is not None else None,
        chunk_ids=source_chunk_ids(store, version.generation_meta),
        ingest=source_ingest(store, version.generation_meta),
        quality=analysis,
    )
    return {"issues": issues_payload(issues)[0], "quality": quality_report(body.screenplay, analysis)}
