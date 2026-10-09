"""Version actions that enqueue jobs or create versions: build, regenerate scene, plan review,
source review (corrections + approval), duplicate (with its Visual Review sign-offs), translate."""

from __future__ import annotations

import copy
from typing import Any

from fastapi import APIRouter
from pydantic import ConfigDict, Field
from sqlalchemy import select

from ... import review
from ...db import compare_and_set
from ...jobs.queue import complete_review, job_summary
from ...models import Job, Project, ProjectVersion
from ...pipeline.base import SUPPORTED_LANGUAGES, LecturePlan
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, load_version
from ..errors import ApiException, not_found
from ..generation import (
    charge_generation_rate,
    commit_and_notify,
    create_version,
    enqueue_job,
    ensure_budget,
    ensure_version_engine,
    request_keys,
    touch_project,
    version_options,
)
from ..screenplay_service import hidden_scene_ids, source_ingest
from ..serializers import version_summary
from ..storable import StorableBody
from ..util import utcnow
from .versions import safe_screenplay

router = APIRouter(prefix="/api/versions", tags=["versions"], dependencies=RATE_LIMITED)


def _require_screenplay_ids(version: ProjectVersion) -> list[str]:
    screenplay = safe_screenplay(version)
    if screenplay is None:
        raise ApiException(409, "no_screenplay", "This version has no screenplay yet.")
    return [s.id for s in screenplay.scenes]


class BuildBody(StorableBody):
    """Scenes to (re)build; null = every stale scene."""

    model_config = ConfigDict(extra="forbid")

    scene_ids: list[str] | None = Field(default=None, max_length=200)


@router.post("/{vid}/build", status_code=202)
def build_version(
    vid: int, user: CurrentUser, db: DbSession, settings: AppSettings, body: BuildBody | None = None
) -> dict[str, Any]:
    """(Re)build assets + timeline for the given scenes (null = every stale scene). 409 ``action_unavailable`` when
    every scene named is hidden (skipped in the video, so never built)."""
    version = load_version(db, user, vid)
    known = _require_screenplay_ids(version)
    scene_ids = None if body is None or body.scene_ids is None else list(dict.fromkeys(body.scene_ids))
    if scene_ids is not None:
        unknown = [s for s in scene_ids if s not in known]
        if unknown or not scene_ids:
            raise ApiException(
                422,
                "validation",
                [
                    {
                        "loc": ["body", "scene_ids"],
                        "msg": f"Unknown scene ids: {unknown}" if unknown else "No scenes given.",
                        "type": "value_error",
                    }
                ],
            )
        if set(scene_ids) <= hidden_scene_ids(version.screenplay):  # the build skips hidden scenes: nothing to make
            raise ApiException(409, "action_unavailable",
                               "These scenes are skipped in the video. Show them again in the editor to build them.")
    opts = version_options(version, db.get(Project, version.project_id))
    ensure_budget(db, user, settings, keys=request_keys(db, user, settings), options=opts)
    job = enqueue_job(
        db,
        "build_assets",
        user=user,
        project_id=version.project_id,
        version_id=version.id,
        payload={"scene_ids": scene_ids, "base_revision": version.revision},
    )
    commit_and_notify(db, settings)
    return {"job": job_summary(job)}


class RegenerateSceneBody(StorableBody):
    """Teacher instructions for rewriting one scene."""

    model_config = ConfigDict(extra="forbid")

    instructions: str = Field(default="", max_length=2000)


@router.post("/{vid}/scenes/{scene_id}/regenerate", status_code=202)
def regenerate_scene(
    vid: int,
    scene_id: str,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    body: RegenerateSceneBody | None = None,
) -> dict[str, Any]:
    """Rewrite one scene with optional teacher instructions."""
    version = load_version(db, user, vid)
    if scene_id not in _require_screenplay_ids(version):
        raise not_found("Scene")
    project = db.get(Project, version.project_id)
    keys = request_keys(db, user, settings)
    ensure_version_engine(version, project, settings, keys)
    ensure_budget(db, user, settings, keys=keys, options=version_options(version, project))
    # Enqueue before spending a generation token: a 409 (build running) must not use up the quota.
    job = enqueue_job(
        db,
        "regenerate_scene",
        user=user,
        project_id=version.project_id,
        version_id=version.id,
        payload={
            "scene_id": scene_id,
            "instructions": (body.instructions if body is not None else "").strip(),
            "base_revision": version.revision,
        },
    )
    charge_generation_rate(db, user, settings)
    commit_and_notify(db, settings)
    return {"job": job_summary(job)}


class PlanBody(StorableBody):
    """An edited lecture plan (validated like the generated one)."""

    model_config = ConfigDict(extra="ignore")

    plan: LecturePlan


def _template_problems(plan: LecturePlan) -> list[dict[str, Any]]:
    try:
        from ...manim.templates import registry
    except ImportError:  # manim area not installed: the pipeline validates again
        return []
    problems = []
    for ci, chapter in enumerate(plan.chapters):
        for si, scene in enumerate(chapter.scenes):
            if scene.manim_template and scene.manim_template not in registry:
                problems.append(
                    {
                        "loc": ["body", "plan", "chapters", ci, "scenes", si, "manim_template"],
                        "msg": f"Unknown Manim template {scene.manim_template!r}",
                        "type": "value_error",
                    }
                )
    return problems


def _not_awaiting() -> ApiException:
    return ApiException(409, "not_awaiting_review", "This version is not waiting for plan approval.")


# --- source review (generate_lecture with review_source: pauses before planning) ---------------------------


def _awaiting_source_review(version: ProjectVersion) -> bool:
    from ...pipeline.source_review import REVIEW_STAGE_KEY, REVIEW_STAGE_SOURCE

    return version.status == "awaiting_review" and (version.generation_meta or {}).get(REVIEW_STAGE_KEY) == (
        REVIEW_STAGE_SOURCE
    )


def _source_review_first() -> ApiException:
    return ApiException(
        409,
        "not_awaiting_review",
        "This version is waiting for you to check how the source was read, not for plan approval.",
    )


def _not_awaiting_source() -> ApiException:
    return ApiException(409, "not_awaiting_review", "This version is not waiting for a source review.")


@router.post("/{vid}/plan")
def save_plan(vid: int, body: PlanBody, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Replace the lecture plan while the version awaits review."""
    from ...pipeline.plan_state import save_plan as store_plan

    version = load_version(db, user, vid)
    if version.status != "awaiting_review":
        raise _not_awaiting()
    if _awaiting_source_review(version):
        raise _source_review_first()
    problems = _template_problems(body.plan)
    if problems:
        raise ApiException(422, "validation", problems)
    # save_plan replaces generation_meta on a detached stand-in; the write itself is a single
    # compare-and-set on the status so a concurrent approval cannot be overwritten.
    holder = ProjectVersion(
        id=version.id,
        project_id=version.project_id,
        status=version.status,
        generation_meta=copy.deepcopy(dict(version.generation_meta or {})),
    )
    store_plan(holder, body.plan)
    if not compare_and_set(
        db,
        ProjectVersion,
        version.id,
        {"status": "awaiting_review"},
        {"generation_meta": holder.generation_meta, "updated_at": utcnow()},
    ):
        db.rollback()
        raise _not_awaiting()
    db.commit()
    db.refresh(version)
    return {"version": version_summary(version)}


@router.post("/{vid}/approve-plan", status_code=202)
def approve_plan(vid: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Approve the plan: the awaiting job succeeds and a continuation job resumes generation."""
    version = load_version(db, user, vid)
    if version.status != "awaiting_review":
        raise _not_awaiting()
    waiting = db.execute(
        select(Job)
        .where(Job.version_id == version.id, Job.status == "awaiting_review", Job.kind == "generate_lecture")
        .order_by(Job.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if waiting is None:
        raise ApiException(409, "not_awaiting_review", "No job is waiting for plan approval.")
    if _awaiting_source_review(version) or (waiting.result or {}).get("stage") == "source_review":
        raise _source_review_first()
    project = db.get(Project, version.project_id)
    keys = request_keys(db, user, settings)
    ensure_version_engine(version, project, settings, keys)
    ensure_budget(db, user, settings, keys=keys, options=version_options(version, project))
    if not compare_and_set(
        db, ProjectVersion, version.id, {"status": "awaiting_review"}, {"status": "generating", "updated_at": utcnow()}
    ):
        db.rollback()
        raise _not_awaiting()
    payload = dict(waiting.payload or {})
    payload["resume_state"] = dict(waiting.result or {})
    payload["base_revision"] = version.revision
    complete_review(db, waiting.id)
    job = enqueue_job(
        db, "generate_lecture", user=user, project_id=version.project_id, version_id=version.id, payload=payload
    )
    commit_and_notify(db, settings)
    return {"job": job_summary(job)}


class SourceReviewBody(StorableBody):
    """The teacher's corrections to how the source was read (``pipeline.source_review.SourceOverrides``)."""

    model_config = ConfigDict(extra="forbid")

    excluded_chunk_ids: list[str] = Field(default_factory=list, max_length=500)
    restored_chunk_ids: list[str] = Field(default_factory=list, max_length=500)
    concept_names: dict[str, str] = Field(default_factory=dict, max_length=40)


@router.put("/{vid}/source-review")
def save_source_review(
    vid: int, body: SourceReviewBody, user: CurrentUser, db: DbSession, store: Store
) -> dict[str, Any]:
    """Store the teacher's corrections while the version waits for its source review (409 otherwise)."""
    from pydantic import ValidationError

    from ...pipeline import plan_state, source_review

    version = load_version(db, user, vid)
    if not _awaiting_source_review(version):
        raise _not_awaiting_source()
    meta = copy.deepcopy(dict(version.generation_meta or {}))
    ingest = source_ingest(store, meta)
    if ingest is None:
        raise ApiException(
            409, "source_unavailable", "The source document can no longer be read; continue without changes."
        )
    try:
        overrides = source_review.SourceOverrides(
            ingest_key=str(meta.get("ingest_key") or ""),
            excluded_chunk_ids=body.excluded_chunk_ids,
            restored_chunk_ids=body.restored_chunk_ids,
            concept_names=body.concept_names,
            updated_at=utcnow().isoformat(),
        )
    except ValidationError as exc:
        raise ApiException(
            422,
            "validation",
            [
                {"loc": ["body", *e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
                for e in exc.errors()[:20]
            ],
        ) from None
    problems = source_review.override_problems(overrides, ingest, plan_state.load_brief(version))
    if problems:
        raise ApiException(422, "validation", problems)
    meta[source_review.OVERRIDES_KEY] = overrides.model_dump(mode="json")
    # one compare-and-set on the status: a concurrent approval is never overwritten
    if not compare_and_set(
        db, ProjectVersion, version.id, {"status": "awaiting_review"}, {"generation_meta": meta, "updated_at": utcnow()}
    ):
        db.rollback()
        raise _not_awaiting_source()
    db.commit()
    db.refresh(version)
    return {"version": version_summary(version), "source_overrides": overrides.model_dump(mode="json")}


@router.post("/{vid}/approve-source", status_code=202)
def approve_source(vid: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Approve how the source was read: the waiting job succeeds and a continuation job plans the lecture
    with the teacher's corrections (it may pause again for plan review)."""
    from ...pipeline.source_review import RESUME_STAGE

    version = load_version(db, user, vid)
    if not _awaiting_source_review(version):
        raise _not_awaiting_source()
    waiting = db.execute(
        select(Job)
        .where(Job.version_id == version.id, Job.status == "awaiting_review", Job.kind == "generate_lecture")
        .order_by(Job.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if waiting is None or (waiting.result or {}).get("stage") != RESUME_STAGE:
        raise ApiException(409, "not_awaiting_review", "No job is waiting for the source review.")
    project = db.get(Project, version.project_id)
    keys = request_keys(db, user, settings)
    ensure_version_engine(version, project, settings, keys)
    ensure_budget(db, user, settings, keys=keys, options=version_options(version, project))
    if not compare_and_set(
        db, ProjectVersion, version.id, {"status": "awaiting_review"}, {"status": "generating", "updated_at": utcnow()}
    ):
        db.rollback()
        raise _not_awaiting_source()
    payload = dict(waiting.payload or {})
    payload["resume_state"] = dict(waiting.result or {})
    payload["base_revision"] = version.revision
    complete_review(db, waiting.id)
    job = enqueue_job(
        db, "generate_lecture", user=user, project_id=version.project_id, version_id=version.id, payload=payload
    )
    commit_and_notify(db, settings)
    return {"job": job_summary(job)}


class DuplicateBody(StorableBody):
    """Label of the copy."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(default="", max_length=255)


@router.post("/{vid}/duplicate", status_code=201)
def duplicate_version(vid: int, user: CurrentUser, db: DbSession, body: DuplicateBody | None = None) -> dict[str, Any]:
    """Copy screenplay, manifest and timeline into a new version (new number, revision 1)."""
    version = load_version(db, user, vid)
    if version.screenplay is None:
        raise ApiException(409, "no_screenplay", "This version has no screenplay yet.")
    project = db.get(Project, version.project_id)
    assert project is not None
    fresh = version.has_timeline and version.timeline is not None and version.built_revision == version.revision
    label = (body.label if body is not None else "") or f"Copy of v{version.number}"
    new = create_version(
        db,
        project,
        status="ready" if fresh else "draft",
        language=version.language,
        created_by=user.id,
        label=label,
        source_version_id=version.source_version_id,
    )
    meta = copy.deepcopy(dict(version.generation_meta or {}))
    meta.pop("plan", None)
    meta["duplicated_from"] = version.id
    new.screenplay = copy.deepcopy(version.screenplay)
    new.asset_manifest = copy.deepcopy(version.asset_manifest)
    new.issues = copy.deepcopy(list(version.issues or []))
    new.issue_counts = dict(version.issue_counts or {})
    new.generation_meta = meta
    if version.timeline is not None:
        timeline = copy.deepcopy(version.timeline)
        timeline["version_id"] = new.id
        timeline["screenplay_revision"] = 1 if fresh else None
        new.timeline = timeline
        new.has_timeline = True
    new.built_revision = 1 if fresh else None
    review.copy_reviews(db, version.id, new.id)  # the Visual Review sign-offs go with the copied screenplay
    touch_project(project)
    db.commit()
    db.refresh(new)
    return {"version": version_summary(new)}


class TranslateBody(StorableBody):
    """Target language of the new version and whether the board is translated too."""

    model_config = ConfigDict(extra="forbid")

    target_language: str
    translate_board: bool = False
    tts_voice: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_\-]{1,64}$")


@router.post("/{vid}/translate", status_code=202)
def translate_version(
    vid: int, body: TranslateBody, user: CurrentUser, db: DbSession, settings: AppSettings
) -> dict[str, Any]:
    """Create the target-language version (status generating) and enqueue ``translate``."""
    version = load_version(db, user, vid)
    if body.target_language not in SUPPORTED_LANGUAGES:
        raise ApiException(
            422,
            "validation",
            [{"loc": ["body", "target_language"], "msg": "Unsupported language.", "type": "value_error"}],
        )
    if body.target_language == version.language:
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["body", "target_language"],
                    "msg": "The version is already in this language.",
                    "type": "value_error",
                }
            ],
        )
    if version.screenplay is None:
        raise ApiException(409, "no_screenplay", "This version has no screenplay yet.")
    project = db.get(Project, version.project_id)
    assert project is not None
    keys = request_keys(db, user, settings)
    ensure_version_engine(version, project, settings, keys)  # a translation uses its source version's engine
    ensure_budget(db, user, settings, keys=keys, options=version_options(version, project))
    target = create_version(
        db,
        project,
        status="generating",
        language=body.target_language,
        created_by=user.id,
        label=f"{SUPPORTED_LANGUAGES[body.target_language]} (from v{version.number})",
        source_version_id=version.id,
    )
    touch_project(project)
    job = enqueue_job(
        db,
        "translate",
        user=user,
        project_id=project.id,
        version_id=target.id,
        payload={
            "source_version_id": version.id,
            "target_language": body.target_language,
            "translate_board": body.translate_board,
            "tts_voice": body.tts_voice,
        },
    )
    charge_generation_rate(db, user, settings)  # 429 rolls back the new version and its job
    commit_and_notify(db, settings)
    db.refresh(target)
    return {"version": version_summary(target), "job": job_summary(job)}
