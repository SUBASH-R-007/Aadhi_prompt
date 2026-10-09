"""``/api/versions/{vid}/changes``: what the teacher changed since Aadhi wrote the lecture, and putting a scene back
(``aadhi.changes``).

* ``GET .../changes``: ``{"available", "scenes": [{scene_id, status, generated_index, current_index,
  fields_changed, history}]}``. ``available`` is false for lectures without a generated snapshot (imports, and
  versions generated before snapshots were kept).
* ``GET .../changes/{scene_id}``: one scene's generated and current documents with its regeneration history
  (404 when the scene is in neither).
* ``POST .../scenes/{scene_id}/revert`` ``{"revision", "to": "generated" | "history", "history_index"?,
  "position"?}``: puts the scene back as generated or as one of its history entries (a removed scene is restored at
  ``position``). Saved like an editor save: the revision compare-and-set (409 ``revision_conflict``), refused while
  a job writes the version (409 ``version_busy``), lint and issues merged, the timeline is stale afterwards. 404
  ``not_found`` when there is nothing to revert to; 409 ``revert_invalid`` when the lecture would no longer validate.

Owner or admin of the project, like every version route (``load_version``); mutations go through the CSRF
middleware and the per-user rate limit.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import ConfigDict, Field
from sqlalchemy import exists, select, update

from ... import changes as changes_service
from ...models import ACTIVE_JOB_STATUSES, VERSION_MUTATING_KINDS, Job, Project, ProjectVersion
from ..deps import RATE_LIMITED, CurrentUser, DbSession, Store, load_version
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
from ..serializers import version_summary
from ..storable import StorableBody, ensure_storable
from ..util import utcnow
from .versions import safe_manifest, safe_screenplay

router = APIRouter(prefix="/api/versions", tags=["versions"], dependencies=RATE_LIMITED)


class RevertBody(StorableBody):
    """Which version of the scene to put back, based on ``revision`` (optimistic concurrency)."""

    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    to: Literal["generated", "history"] = "generated"
    history_index: int | None = Field(default=None, ge=0, le=99)
    position: int | None = Field(default=None, ge=0, le=1000)


@router.get("/{vid}/changes")
def version_changes(vid: int, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """Every scene compared with the screenplay as Aadhi wrote it."""
    version = load_version(db, user, vid)
    meta = dict(version.generation_meta or {})
    return changes_service.changes(changes_service.load_generated(store, meta), safe_screenplay(version), meta)


@router.get("/{vid}/changes/{scene_id}")
def scene_changes(vid: int, scene_id: str, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """One scene as generated, as it is now, and its earlier versions (scene regeneration)."""
    version = load_version(db, user, vid)
    meta = dict(version.generation_meta or {})
    detail = changes_service.scene_detail(changes_service.load_generated(store, meta), safe_screenplay(version), meta,
                                          scene_id[:64])
    if detail is None:
        raise not_found("Scene")
    return detail


def _busy(job: Job) -> ApiException:
    return ApiException(409, "version_busy", "A job is currently modifying this version.", job_id=job.id)


@router.post("/{vid}/scenes/{scene_id}/revert")
def revert_scene(
    vid: int, scene_id: str, body: RevertBody, user: CurrentUser, db: DbSession, store: Store
) -> dict[str, Any]:
    """Put one scene back as Aadhi wrote it (or as an earlier regenerated version), as one saved edit."""
    version = load_version(db, user, vid)
    busy = active_mutating_job(db, version.id)
    if busy is not None:
        raise _busy(busy)
    if body.revision != version.revision:
        raise ApiException(
            409, "revision_conflict", "The screenplay was changed elsewhere.", current_revision=version.revision
        )
    current = safe_screenplay(version)
    if current is None:
        raise ApiException(409, "no_screenplay", "This version has no screenplay yet.")
    meta = dict(version.generation_meta or {})
    generated = changes_service.load_generated(store, meta)
    try:
        scene = changes_service.target_scene(generated, meta, scene_id, body.to, body.history_index)
        new_sp = changes_service.reverted(current, generated, scene, body.position)
    except changes_service.RevertError as exc:
        if exc.code == "nothing_to_revert":
            raise ApiException(404, "not_found", str(exc)) from None
        raise ApiException(409, exc.code, str(exc)) from None
    authorize_asset_keys(db, version.project_id, new_sp)
    document = new_sp.model_dump(mode="json")
    ensure_storable(document, ("body",))
    project = db.get(Project, version.project_id)
    lint_issues = lint_screenplay(
        new_sp,
        options_for(project) if project is not None else None,
        chunk_ids=source_chunk_ids(store, version.generation_meta),
        ingest=source_ingest(store, version.generation_meta),
    )
    hidden = hidden_scene_ids(new_sp)  # their issues stored as they are, served and counted as notes
    dumped, counts = issues_payload(merge_issues(current, new_sp, version.issues, lint_issues), hidden)
    now = utcnow()
    job_active = exists().where(
        Job.version_id == version.id, Job.status.in_(ACTIVE_JOB_STATUSES), Job.kind.in_(VERSION_MUTATING_KINDS)
    )
    result = db.execute(
        update(ProjectVersion)
        .where(ProjectVersion.id == version.id, ProjectVersion.revision == body.revision, ~job_active)
        .values(revision=body.revision + 1, screenplay=document, issues=dumped, issue_counts=counts, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if (result.rowcount or 0) == 0:
        db.rollback()
        busy = active_mutating_job(db, version.id)
        if busy is not None:
            raise _busy(busy)
        current_rev = db.execute(select(ProjectVersion.revision).where(ProjectVersion.id == version.id)).scalar_one_or_none()
        raise ApiException(409, "revision_conflict", "The screenplay was changed elsewhere.", current_revision=current_rev)
    db.execute(
        update(Project)
        .where(Project.id == version.project_id)
        .values(updated_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.refresh(version)
    return {
        "revision": version.revision,
        "scene_id": scene_id,
        "version": version_summary(version),
        "issues": served_issues(dumped, hidden),
        "stale_scenes": stale_scenes(new_sp, safe_manifest(version)),
    }
