"""``/api/projects``: list, create (upload or pasted notes), import (JSON), detail, patch, delete, regenerate,
source report."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy import func, or_, select, update

from ...db import compare_and_set
from ...jobs.queue import job_summary, request_cancel
from ...legacy import convert_legacy, is_legacy
from ...models import ACTIVE_JOB_STATUSES, AssetRef, Job, Project, ProjectVersion, ShareLink, SourceDocument, User
from ...pipeline.base import SUPPORTED_LANGUAGES, GenerationOptions
from ...schemas.screenplay import Screenplay
from ...security.uploads import safe_display_name, validate_upload
from ...storage.assets import Produced, bytes_key
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, is_admin, load_project, load_version
from ..errors import ApiException, not_found
from ..generation import (
    MODEL_OVERRIDE_FIELDS,
    commit_and_notify,
    create_version,
    enqueue_job,
    ensure_budget,
    ensure_generation_allowed,
    parse_options,
    project_default_options,
    request_keys,
    sanitize_options,
    touch_project,
)
from ..screenplay_service import (
    accessible_project_ids,
    hidden_scene_ids,
    issues_payload,
    lint_screenplay,
    source_ingest,
    strip_unauthorized_asset_keys,
)
from ..serializers import project_summaries, project_summary, review_stages, source_dict, version_summary
from ..storable import StorableBody, ensure_storable
from ..upload_io import read_upload
from ..util import page_params, utcnow

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=RATE_LIMITED)

SOURCE_KINDS = {"pdf", "docx", "text", "markdown"}
IMPORT_MAX_BYTES = 20 * 1024 * 1024


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _title_from_filename(filename: str) -> str:
    stem = PurePath(filename or "").stem.replace("_", " ").strip()
    return (stem or "Untitled lecture")[:255]


@router.get("")
def list_projects(
    user: CurrentUser, db: DbSession, q: str = "", limit: int = 50, offset: int = 0, mine: bool = False
) -> dict[str, Any]:
    """Projects the user can see (admins: all unless ``mine``), newest first."""
    limit, offset = page_params(limit, offset)
    stmt = select(Project).where(Project.deleted_at.is_(None))
    if not is_admin(user) or mine:
        stmt = stmt.where(Project.owner_id == user.id)
    term = q.strip()[:200]
    if term:
        pattern = f"%{_escape_like(term)}%"
        stmt = stmt.where(
            or_(
                Project.title.ilike(pattern, escape="\\"),
                Project.subject_name.ilike(pattern, escape="\\"),
                Project.unit_name.ilike(pattern, escape="\\"),
                Project.session_title.ilike(pattern, escape="\\"),
            )
        )
    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = (
        db.execute(stmt.order_by(Project.updated_at.desc(), Project.id.desc()).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return {"items": project_summaries(db, rows), "total": int(total)}


@router.post("", status_code=201)
def create_project(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    store: Store,
    file: Annotated[UploadFile, File()],
    options: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form(max_length=255)] = None,
    review_source: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    """Upload a source document (a file, or notes the Studio pasted as a .txt file) and start generating
    version 1. ``review_source``: pause after the source was read so the teacher can check it first."""
    keys = request_keys(db, user, settings)
    opts = sanitize_options(
        parse_options(options, defaults={"language": _default_language(settings)}), user, settings, keys
    )
    max_bytes = settings.upload_max_mb * 1024 * 1024
    data = read_upload(file, max_bytes)
    filename = file.filename or "source"
    info = validate_upload(filename, data, allowed_kinds=SOURCE_KINDS, max_bytes=max_bytes)
    ensure_generation_allowed(db, user, settings, engine=opts.llm_provider, keys=keys, options=opts)
    db.commit()  # end any read transaction before the asset store writes in its own session
    key = bytes_key("source", data)
    asset = store.put(key, "source", Produced(data=data, mime=info.mime), created_by=user.id)
    opts_json = opts.model_dump(mode="json")
    project = Project(
        owner_id=user.id,
        title=(title or "").strip()[:255] or (opts.session_title or "")[:255] or _title_from_filename(filename),
        subject_name=(opts.subject_name or "")[:255],
        unit_name=(opts.unit_name or "")[:255],
        session_number=(opts.session_number or "")[:64],
        session_title=(opts.session_title or "")[:255],
        language=opts.language,
        settings=opts_json,
    )
    db.add(project)
    db.flush()
    source = SourceDocument(
        project_id=project.id,
        filename=safe_display_name(filename)[:255] or "source",
        mime=info.mime,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_key=asset.storage_key,
    )
    db.add(source)
    db.add(AssetRef(project_id=project.id, asset_key=key))
    db.flush()
    version = create_version(db, project, status="generating", language=opts.language, created_by=user.id)
    project.current_version_id = version.id
    job = enqueue_job(
        db,
        "generate_lecture",
        user=user,
        project_id=project.id,
        version_id=version.id,
        payload=_generation_payload(source.id, opts_json, version.revision, review_source=review_source),
    )
    commit_and_notify(db, settings)
    db.refresh(project)
    return {"project": project_summary(db, project), "version": version_summary(version), "job": job_summary(job)}


def _default_language(settings: AppSettings) -> str:
    return settings.default_language if settings.default_language in SUPPORTED_LANGUAGES else "en-IN"


def _generation_payload(
    source_id: int, opts_json: dict[str, Any], revision: int, *, review_source: bool
) -> dict[str, Any]:
    """``generate_lecture`` payload; ``review_source`` (pause for the teacher's source review) only when asked."""
    payload: dict[str, Any] = {"source_document_id": source_id, "options": opts_json, "base_revision": revision}
    if review_source:
        payload["review_source"] = True
    return payload


def _parse_import(data: bytes) -> tuple[Screenplay, list[str]]:
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApiException(
            422,
            "validation",
            [{"loc": ["file"], "msg": f"The file is not valid JSON ({type(exc).__name__}).", "type": "json_invalid"}],
        ) from None
    ensure_storable(parsed, ("file",))  # lone surrogates ("\ud800" escapes); numbers are checked by validate_upload
    if is_legacy(parsed):
        try:
            screenplay, warnings = convert_legacy(parsed)
        except (ValueError, ValidationError) as exc:
            raise ApiException(
                422,
                "validation",
                [{"loc": ["file"], "msg": f"Could not convert the v1 lecture: {exc}", "type": "legacy"}],
            ) from None
        return screenplay, list(warnings)
    if isinstance(parsed, dict) and "screenplay" in parsed and "scenes" not in parsed:
        parsed = parsed["screenplay"]
    try:
        return Screenplay.model_validate(parsed), []
    except ValidationError as exc:
        errors = [
            {"loc": ["file", *e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()[:50]
        ]
        raise ApiException(422, "validation", errors) from None


def _require_supported_language(screenplay: Screenplay) -> str:
    """The screenplay's narration language; 422 at ``file.language`` when it is not supported.

    Project, version and options must all agree on one supported code (TTS voices, translation and
    labels are looked up by it), so an unknown language is refused rather than silently replaced.
    """
    language = screenplay.language
    if language not in SUPPORTED_LANGUAGES:
        supported = ", ".join(sorted(SUPPORTED_LANGUAGES))
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["file", "language"],
                    "msg": f"Unsupported language {language!r}; supported: {supported}.",
                    "type": "value_error",
                }
            ],
        )
    return language


@router.post("/import", status_code=201)
def import_project(
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    file: Annotated[UploadFile, File()],
    title: Annotated[str | None, Form(max_length=255)] = None,
) -> dict[str, Any]:
    """Create a project from a v2 Screenplay JSON or a v1 lecture JSON and build its assets."""
    ensure_budget(db, user, settings, keys=request_keys(db, user, settings))
    max_bytes = min(settings.upload_max_mb * 1024 * 1024, IMPORT_MAX_BYTES)
    data = read_upload(file, max_bytes)
    filename = file.filename or "lecture.json"
    validate_upload(filename, data, allowed_kinds={"json"}, max_bytes=max_bytes)
    screenplay, warnings = _parse_import(data)
    screenplay, strip_warnings, kept_keys = strip_unauthorized_asset_keys(
        db, screenplay, project_ids=accessible_project_ids(db, user.id, admin=is_admin(user))
    )
    language = _require_supported_language(screenplay)
    issues = lint_screenplay(screenplay, None)
    opts_json = GenerationOptions(language=language).model_dump(mode="json")
    project = Project(
        owner_id=user.id,
        title=(title or "").strip()[:255] or screenplay.session_title[:255] or _title_from_filename(filename),
        subject_name=screenplay.subject_name[:255],
        unit_name=screenplay.unit_name[:255],
        session_number=screenplay.session_number[:64],
        session_title=screenplay.session_title[:255],
        language=language,
        settings=opts_json,
    )
    db.add(project)
    db.flush()
    for key in sorted(kept_keys):
        db.add(AssetRef(project_id=project.id, asset_key=key))
    version = create_version(db, project, status="building", language=language, created_by=user.id, label="Imported")
    version.set_screenplay(screenplay)
    dumped, counts = issues_payload(issues, hidden_scene_ids(screenplay))
    version.issues = dumped
    version.issue_counts = counts
    project.current_version_id = version.id
    job = enqueue_job(
        db,
        "build_assets",
        user=user,
        project_id=project.id,
        version_id=version.id,
        payload={"scene_ids": None, "base_revision": version.revision},
    )
    commit_and_notify(db, settings)
    db.refresh(project)
    return {
        "project": project_summary(db, project),
        "version": version_summary(version),
        "job": job_summary(job),
        "warnings": warnings + strip_warnings,
    }


def stored_options(project: Project, user: User, settings: AppSettings) -> dict[str, Any]:
    """The project's GenerationOptions as the Studio pre-fills "Regenerate lecture" with them.

    Validated like a regenerate (``project_default_options``); admin model overrides are shown to
    admins only (an editor's regenerate would drop them anyway).
    """
    opts = project_default_options(project, settings)
    if not is_admin(user):
        for field in MODEL_OVERRIDE_FIELDS:
            opts[field] = None
    return opts


@router.get("/{project_id}")
def get_project(project_id: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Project with all versions, sources, the latest 20 jobs and its stored generation options."""
    project = load_project(db, user, project_id)
    versions = db.execute(
        select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(ProjectVersion.number)
    ).scalars().all()
    stages = review_stages(db, versions)
    sources = db.execute(
        select(SourceDocument).where(SourceDocument.project_id == project.id).order_by(SourceDocument.id)
    ).scalars()
    jobs = db.execute(select(Job).where(Job.project_id == project.id).order_by(Job.id.desc()).limit(20)).scalars()
    return {
        "project": project_summary(db, project),
        # each awaiting review carries its review_stage ("source" | "plan"), read in one query
        "versions": [version_summary(v, stage=stages.get(v.id)) for v in versions],
        "sources": [source_dict(s) for s in sources],
        "jobs": [job_summary(j) for j in jobs],
        "options": stored_options(project, user, settings),
    }


class ProjectPatch(StorableBody):
    """Editable project metadata and the current version."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=255)
    subject_name: str | None = Field(default=None, max_length=255)
    unit_name: str | None = Field(default=None, max_length=255)
    session_number: str | None = Field(default=None, max_length=64)
    session_title: str | None = Field(default=None, max_length=255)
    current_version_id: int | None = None


@router.patch("/{project_id}")
def patch_project(project_id: int, body: ProjectPatch, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Edit metadata or switch the current version (must belong to the project)."""
    project = load_project(db, user, project_id)
    values: dict[str, Any] = {}
    for field in ("title", "subject_name", "unit_name", "session_number", "session_title"):
        value = getattr(body, field)
        if field in body.model_fields_set and value is not None:
            values[field] = value
    if "current_version_id" in body.model_fields_set and body.current_version_id is not None:
        owner = db.execute(
            select(ProjectVersion.project_id).where(ProjectVersion.id == body.current_version_id)
        ).scalar_one_or_none()
        if owner != project.id:
            raise ApiException(
                422,
                "validation",
                [
                    {
                        "loc": ["body", "current_version_id"],
                        "msg": "Version does not belong to this project.",
                        "type": "value_error",
                    }
                ],
            )
        values["current_version_id"] = body.current_version_id
    if values:
        values["updated_at"] = utcnow()
        db.execute(update(Project).where(Project.id == project.id).values(**values))
        db.commit()
        db.refresh(project)
    return {"project": project_summary(db, project)}


@router.delete("/{project_id}", status_code=204)
def delete_project(project_id: int, user: CurrentUser, db: DbSession) -> Response:
    """Soft delete; in the same transaction cancel active jobs and revoke share links."""
    project = load_project(db, user, project_id)
    now = utcnow()
    if not compare_and_set(db, Project, project.id, {"deleted_at": None}, {"deleted_at": now, "updated_at": now}):
        raise ApiException(404, "not_found", "Project not found")
    active = (
        db.execute(select(Job.id).where(Job.project_id == project.id, Job.status.in_(ACTIVE_JOB_STATUSES)))
        .scalars()
        .all()
    )
    for job_id in active:
        request_cancel(db, job_id)
    db.execute(
        update(ShareLink)
        .where(ShareLink.project_id == project.id, ShareLink.revoked_at.is_(None))
        .values(revoked_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return Response(status_code=204)


class RegenerateBody(StorableBody):
    """Optional GenerationOptions overrides for a new version (+ whether to pause for a source review)."""

    model_config = ConfigDict(extra="forbid")

    options: dict[str, Any] | None = None
    review_source: bool = False


@router.post("/{project_id}/regenerate", status_code=201)
def regenerate_project(
    project_id: int, user: CurrentUser, db: DbSession, settings: AppSettings, body: RegenerateBody | None = None
) -> dict[str, Any]:
    """New version generated from the latest source document."""
    project = load_project(db, user, project_id)
    source = db.execute(
        select(SourceDocument)
        .where(SourceDocument.project_id == project.id)
        .order_by(SourceDocument.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if source is None:
        raise ApiException(409, "no_source", "This project has no source document to generate from.")
    raw = body.options if body is not None else None
    keys = request_keys(db, user, settings)
    opts = sanitize_options(parse_options(raw, defaults=project_default_options(project, settings)), user, settings, keys)
    ensure_generation_allowed(db, user, settings, engine=opts.llm_provider, keys=keys, options=opts)
    opts_json = opts.model_dump(mode="json")
    version = create_version(db, project, status="generating", language=opts.language, created_by=user.id)
    project.settings = opts_json
    if project.current_version_id is None:
        project.current_version_id = version.id
    touch_project(project)
    job = enqueue_job(
        db,
        "generate_lecture",
        user=user,
        project_id=project.id,
        version_id=version.id,
        payload=_generation_payload(
            source.id, opts_json, version.revision, review_source=body is not None and body.review_source
        ),
    )
    commit_and_notify(db, settings)
    return {"version": version_summary(version), "job": job_summary(job)}


# --- source report --------------------------------------------------------------------------------------------

_ANALYSIS_CACHE_SIZE = 16
_analysis_cache: OrderedDict[tuple[str, str], Any] = OrderedDict()
_analysis_lock = threading.Lock()


def _source_analysis(key: str, ingest: Any) -> Any:
    """``source_review.analyze`` of an extract, cached per content-addressed extract key + ``REVIEW_VERSION``."""
    from ...pipeline.source_review import REVIEW_VERSION, analyze

    cache_key = (key, REVIEW_VERSION)
    with _analysis_lock:
        cached = _analysis_cache.get(cache_key)
        if cached is not None:
            _analysis_cache.move_to_end(cache_key)
            return cached
    analysis = analyze(ingest)
    with _analysis_lock:
        _analysis_cache[cache_key] = analysis
        while len(_analysis_cache) > _ANALYSIS_CACHE_SIZE:
            _analysis_cache.popitem(last=False)
    return analysis


def clear_source_analysis_cache() -> None:
    """Tests: forget cached source analyses."""
    with _analysis_lock:
        _analysis_cache.clear()


def _generated_from(db: Any, version: ProjectVersion) -> ProjectVersion | None:
    """The version whose extract ``version`` was generated from: itself, or for a translation (whose metadata has
    no extract key) the version it was translated from, in the same project and from the same source document."""
    doc = (version.generation_meta or {}).get("source_document_id")
    current: ProjectVersion | None = version
    seen: set[int] = set()
    while current is not None and current.id not in seen and len(seen) < 8:
        seen.add(current.id)
        meta = current.generation_meta or {}
        if isinstance(meta.get("ingest_key"), str) and meta["ingest_key"]:
            return current
        src = meta.get("source_version_id")
        if not isinstance(src, int) or isinstance(src, bool):
            return None
        current = db.get(ProjectVersion, src)
        if (
            current is None
            or current.project_id != version.project_id
            or (current.generation_meta or {}).get("source_document_id") != doc
        ):
            return None
    return None


@router.get("/{project_id}/versions/{vid}/source-report")
def source_report(project_id: int, vid: int, user: CurrentUser, db: DbSession, store: Store) -> dict[str, Any]:
    """How the source of a version was read (``pipeline.source_review.SourceReport``): outline with roles, content
    inventory, findings with an advisory readiness verdict, what the concept brief made of each part, what source
    scoping set aside (counts by category, never the values) and which parts each scene cites. ``available`` is
    false for imported lectures and extracts that can no longer be read; ``review.active`` while the generation
    waits for the teacher's source review."""
    from ...pipeline import plan_state, source_review
    from .versions import safe_screenplay

    project = load_project(db, user, project_id)
    version = load_version(db, user, vid)
    if version.project_id != project.id:
        raise not_found("Version")
    meta = dict(version.generation_meta or {})
    active = version.status == "awaiting_review" and (
        meta.get(source_review.REVIEW_STAGE_KEY) == source_review.REVIEW_STAGE_SOURCE
    )
    out: dict[str, Any] = {
        "version": version_summary(version),
        "available": False,
        "reason": "",
        "review": {"active": active, "editable": False},
        "report": None,
    }
    origin = _generated_from(db, version)
    if origin is None:
        out["reason"] = "no_source"
        return out
    origin_meta = dict(origin.generation_meta or {})
    key = str(origin_meta["ingest_key"])
    ingest = source_ingest(store, origin_meta)
    if ingest is None:
        out["reason"] = "unreadable"
        return out
    brief = plan_state.load_brief(version) or plan_state.load_brief(origin)
    doc_id = origin_meta.get("source_document_id")
    source = db.get(SourceDocument, doc_id) if isinstance(doc_id, int) and not isinstance(doc_id, bool) else None
    report = source_review.build_report(
        ingest,
        analysis=_source_analysis(key, ingest),
        brief=brief,
        overrides=source_review.overrides_for(origin_meta, key),
        screenplay=safe_screenplay(version),
        filename=source.filename if source is not None and source.project_id == project.id else "",
    )
    out.update(available=True, report=report.model_dump(mode="json"), review={"active": active, "editable": active})
    return out
