"""MP4 renders: start (``/render``), preflight, list, download, render capabilities.

Admission (``start_render``): one active render per version (409 ``job_in_progress``), at most
``RENDER_MAX_PER_USER`` active renders per user (429 ``render_busy``) and ``RENDER_MAX_QUEUED`` on
the server (429 ``render_queue_full``), both with ``Retry-After``; 0 disables a limit. With
``allow_degraded: false`` a lecture whose MP4 would have silent scenes is refused with 409
``render_preflight`` (the items are in the body); the default renders anyway. A lecture whose every scene is
skipped in the video (``hidden``) is refused with 409 ``all_scenes_hidden`` before a job is queued. In
``WORKER_MODE=inline`` the API host is the render host, so a host that cannot render (no
ffmpeg/encoders/Chromium) answers 503 ``render_unavailable`` (the reasons only for admins); with external
workers the API never refuses on its own host's tools.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...config import Settings
from ...jobs.queue import job_summary
from ...models import ACTIVE_JOB_STATUSES, Job, Project, ProjectVersion, Render
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, is_admin, load_version, valid_id
from ..errors import ApiException, not_found
from ..generation import commit_and_notify, enqueue_job
from ..screenplay_service import hidden_scene_ids
from ..serializers import render_summaries, render_summary
from ..timelines import media_url
from ..util import content_disposition, download_name

router = APIRouter(tags=["renders"], dependencies=RATE_LIMITED)

FILE_EXT = {"video": "mp4", "srt": "srt", "vtt": "vtt"}
RETRY_AFTER_SECONDS = 60
# pg advisory transaction lock: render admission (count, then insert) one request at a time. Differs from the
# cleanup scheduler's key (aadhi.jobs.cleanup).
RENDER_ADMISSION_LOCK_KEY = 0x0AAD_4E0D
UNAVAILABLE_MESSAGE = "Video rendering is not set up on this server; ask an administrator."


class RenderBody(BaseModel):
    """MP4 render options."""

    model_config = ConfigDict(extra="forbid")

    burn_captions: bool = False
    include_intro: bool = True
    soft_subtitles: bool | None = None  # selectable caption track in the MP4; None = RENDER_SOFT_SUBTITLES
    allow_degraded: bool = True  # False: refuse (409 render_preflight) when some scene would be silent

    def job_options(self) -> dict[str, Any]:
        """Options stored on the Render row and in the job payload (extras only when set)."""
        out: dict[str, Any] = {"burn_captions": self.burn_captions, "include_intro": self.include_intro}
        if self.soft_subtitles is not None:
            out["soft_subtitles"] = self.soft_subtitles
        return out


def lock_version_for_render(db: Session, version_id: int) -> tuple[int, int | None, bool, str] | None:
    """Take the version's row write lock (held until commit/rollback) and re-read its build state.

    A no-op ``UPDATE`` (``updated_at`` set to itself, so no ``onupdate`` bump) is the first write of
    the transaction: Postgres row-locks the version, SQLite takes its single writer lock. Concurrent
    ``POST /render`` calls for one version therefore run their check-then-insert one at a time.
    Returns ``(revision, built_revision, has_timeline, language)`` or None if the row vanished.
    """
    row = db.execute(
        update(ProjectVersion)
        .where(ProjectVersion.id == version_id)
        .values(updated_at=ProjectVersion.updated_at)
        .returning(
            ProjectVersion.revision,
            ProjectVersion.built_revision,
            ProjectVersion.has_timeline,
            ProjectVersion.language,
        )
        .execution_options(synchronize_session=False)
    ).first()
    return None if row is None else (int(row[0]), row[1], bool(row[2]), str(row[3]))


def active_render_job(db: Session, version_id: int) -> int | None:
    """Id of a queued/running ``render_video`` job of the version, if any."""
    return db.execute(
        select(Job.id)
        .where(Job.version_id == version_id, Job.kind == "render_video", Job.status.in_(ACTIVE_JOB_STATUSES))
        .limit(1)
    ).scalar_one_or_none()


def active_render_counts(db: Session, user_id: int) -> tuple[int, int]:
    """``(the user's, everyone's)`` queued/running ``render_video`` jobs."""
    active = (Job.kind == "render_video", Job.status.in_(ACTIVE_JOB_STATUSES))
    total = db.execute(select(func.count()).select_from(Job).where(*active)).scalar_one()
    mine = db.execute(select(func.count()).select_from(Job).where(*active, Job.user_id == user_id)).scalar_one()
    return int(mine), int(total)


def admission_lock(db: Session) -> None:
    """Serialise render admission on PostgreSQL until the caller's commit / rollback: under READ COMMITTED,
    concurrent requests (other versions, retries) would all count the same committed rows and all insert.
    SQLite's single writer lock already serialises this."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(func.pg_advisory_xact_lock(RENDER_ADMISSION_LOCK_KEY)))


def check_admission(db: Session, user_id: int, settings: Settings) -> None:
    """429 ``render_busy`` / ``render_queue_full`` over ``RENDER_MAX_PER_USER`` / ``RENDER_MAX_QUEUED``.

    Takes the admission lock first (``admission_lock``), held until the caller commits the new render job
    or rolls back, so the counts and the insert are one step for concurrent requests."""
    per_user, queued = int(settings.render_max_per_user), int(settings.render_max_queued)
    if per_user <= 0 and queued <= 0:
        return
    admission_lock(db)
    mine, total = active_render_counts(db, user_id)
    retry = {"Retry-After": str(RETRY_AFTER_SECONDS)}
    if per_user > 0 and mine >= per_user:
        raise ApiException(429, "render_busy",
                           f"You already have {mine} video render(s) in progress; wait for one to finish.",
                           headers=retry, limit=per_user)
    if queued > 0 and total >= queued:
        raise ApiException(429, "render_queue_full", "The server is busy rendering other videos; try again shortly.",
                           headers=retry, limit=queued)


def every_scene_hidden(version: ProjectVersion) -> bool:
    """The stored screenplay has scenes and every one is skipped in the video: the render job would fail
    (``empty``), so the request is refused up front."""
    document = version.screenplay
    scenes = document.get("scenes") if isinstance(document, dict) else None
    return isinstance(scenes, list) and bool(scenes) and all(
        isinstance(s, dict) and s.get("hidden") is True for s in scenes)


def scene_numbers(version: ProjectVersion) -> dict[str, int]:
    """Each scene's 1-based position in the stored screenplay, hidden scenes counted, as the editor numbers them
    (``TimedScene.index`` counts only the scenes that play)."""
    document = version.screenplay
    scenes = document.get("scenes") if isinstance(document, dict) else None
    return {str(s["id"]): i + 1 for i, s in enumerate(scenes or []) if isinstance(s, dict) and s.get("id")}


def numbered(items: list[dict[str, Any]], numbers: dict[str, int]) -> list[dict[str, Any]]:
    """Preflight items with ``scene_number`` (the screenplay position, ``scene_numbers``; null without a scene)."""
    for it in items:
        it["scene_number"] = numbers.get(str(it.get("scene_id") or "")) if it.get("scene_id") else None
    return items


def version_preflight(version: ProjectVersion) -> list[dict[str, Any]]:
    """Preflight items of the version's stored timeline (empty when it has none), with ``scene_number``."""
    from ...compose.preflight import render_preflight as preflight_items

    timeline = version.get_timeline() if version.timeline is not None else None
    return numbered(preflight_items(timeline) if timeline is not None else [], scene_numbers(version))


def host_render_capabilities(settings: Settings, *, browser: bool = True) -> Any:
    """Cached ``aadhi.compose.capabilities.render_capabilities`` of this host."""
    from ...compose.capabilities import render_capabilities as check

    return check(settings, browser=browser)


@router.post("/api/versions/{vid}/render", status_code=202)
def start_render(
    vid: int, user: CurrentUser, db: DbSession, settings: AppSettings, body: RenderBody | None = None
) -> dict[str, Any]:
    """Render the current timeline to MP4 (409 ``timeline_stale`` unless built from this revision).

    One render per version at a time (409 ``job_in_progress``), enforced under the version row lock
    because the jobs table's partial unique index leaves ``render_video`` out; per-user / server
    limits (429), the optional preflight refusal (409 ``render_preflight``) and, in inline worker
    mode, the host's render capability (503) are described in the module docstring.
    """
    version = load_version(db, user, vid)
    opts = body or RenderBody()
    if settings.worker_mode == "inline":
        caps = host_render_capabilities(settings)
        if not caps.ok:  # the reasons (tool names, configured paths) only for admins, as /api/renders/capabilities
            detail = ("This server cannot render videos: " + " ".join(caps.reasons) if is_admin(user)
                      else UNAVAILABLE_MESSAGE)
            raise ApiException(503, "render_unavailable", detail)
    state = lock_version_for_render(db, version.id)
    if state is None:
        db.rollback()
        raise not_found("Version")
    revision, built_revision, has_timeline, language = state
    if not has_timeline or built_revision is None or built_revision != revision:
        db.rollback()
        raise ApiException(409, "timeline_stale", "Build the latest edits before rendering.")
    if every_scene_hidden(version):
        db.rollback()
        raise ApiException(409, "all_scenes_hidden",
                           "Every scene is skipped in the video. Show at least one scene, build, then make the video.")
    running = active_render_job(db, version.id)
    if running is not None:
        db.rollback()
        raise ApiException(409, "job_in_progress", "A render of this version is already running.", job_id=running)
    try:
        check_admission(db, user.id, settings)
    except ApiException:
        db.rollback()
        raise
    if not opts.allow_degraded:
        items = version_preflight(version)
        blocking = [it for it in items if it.get("blocking")]
        if blocking:
            db.rollback()
            raise ApiException(409, "render_preflight", f"{len(blocking)} scene(s) would be silent in the video.",
                               items=items)
    render = Render(
        version_id=version.id,
        status="queued",
        language=language,
        built_revision=built_revision,
        options=opts.job_options(),
    )
    db.add(render)
    db.flush()
    job = enqueue_job(
        db,
        "render_video",
        user=user,
        project_id=version.project_id,
        version_id=version.id,
        payload={"render_id": render.id, **opts.job_options()},
    )
    render.job_id = job.id
    commit_and_notify(db, settings)
    return {"render": render_summary(render, job, version), "job": job_summary(job)}


@router.get("/api/versions/{vid}/render/preflight")
def render_preflight(vid: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """What the MP4 of the built timeline would show differently from the editor (silent scenes,
    boards replacing missing media, panels shown as a title only), in scene order.

    ``quality``: the version's open errors and warnings (lint, the reviewer and generation/translation
    failures, at most a dozen; ``compose.preflight.quality_items``), listed before a render and never
    blocking it. They are kept out of ``items``, which says how the video itself differs from the editor. Issues of
    hidden scenes (skipped in the video) are not listed.

    Each item's ``scene_index`` is its position in the timeline (hidden scenes left out); ``scene_number`` is its
    1-based position in the screenplay, as the editor numbers scenes (null for lecture-wide items)."""
    from ...compose.preflight import quality_items
    from ...compose.preflight import render_preflight as preflight_items

    version = load_version(db, user, vid)
    timeline = version.get_timeline() if version.timeline is not None else None
    numbers = scene_numbers(version)
    items = numbered(preflight_items(timeline) if timeline is not None else [], numbers)
    stale = not version.has_timeline or version.built_revision is None or version.built_revision != version.revision
    return {
        "version_id": version.id,
        "has_timeline": bool(version.has_timeline),
        "timeline_stale": stale,
        "blocking": any(it.get("blocking") for it in items),
        "items": items,
        "quality": numbered(quality_items(version.issues or [], timeline, hidden_scene_ids(version.screenplay)), numbers),
    }


@router.get("/api/renders/capabilities")
def render_capabilities(user: CurrentUser, settings: AppSettings) -> dict[str, Any]:
    """Whether MP4 rendering works. With inline workers this host is the render host (full check:
    ffmpeg, encoders, Chromium); with external workers the answer is advisory (this API host's
    tools only, no Chromium probe). Reasons and individual checks are shown to administrators."""
    inline = settings.worker_mode == "inline"
    caps = host_render_capabilities(settings, browser=inline)
    out: dict[str, Any] = {"available": bool(caps.ok), "advisory": not inline, "burn_captions": caps.burn_captions}
    if is_admin(user):
        out["reasons"] = list(caps.reasons)
        out["checks"] = dict(caps.checks)
    elif not caps.ok:
        out["reasons"] = [UNAVAILABLE_MESSAGE]
    return out


@router.get("/api/versions/{vid}/renders")
def list_renders(vid: int, user: CurrentUser, db: DbSession, settings: AppSettings, store: Store) -> dict[str, Any]:
    """Renders of a version, newest first; ``matches_current`` says whether each was made from the current script and
    ``preview_url`` is a streamable URL of a finished MP4 for an in-browser player (null otherwise), served like the
    video history's (``GET /api/videos``)."""
    version = load_version(db, user, vid)
    renders = list(
        db.execute(select(Render).where(Render.version_id == version.id).order_by(Render.id.desc())).scalars()
    )
    items = render_summaries(db, renders, version)
    assets = store.get_many(r.video_asset_key for r in renders if r.status == "succeeded" and r.video_asset_key)
    for render, item in zip(renders, items, strict=True):
        asset = assets.get(render.video_asset_key) if render.status == "succeeded" and render.video_asset_key else None
        item["preview_url"] = media_url(store, settings, asset.storage_key) if asset is not None else None
    return {"items": items}


@router.get("/api/renders/{rid}/download")
def download_render(
    rid: int,
    user: CurrentUser,
    db: DbSession,
    settings: AppSettings,
    store: Store,
    file: Literal["video", "srt", "vtt"] = "video",
) -> Response:
    """Attachment named after the project title (local file, or 302 to a presigned URL)."""
    render = db.get(Render, rid) if valid_id(rid) else None
    if render is None:
        raise not_found("Render")
    version = load_version(db, user, render.version_id)
    key = {"video": render.video_asset_key, "srt": render.srt_asset_key, "vtt": render.vtt_asset_key}[file]
    asset = store.get(key) if key else None
    if asset is None:
        raise not_found("File")
    project = db.get(Project, version.project_id)
    name = download_name(project.title if project else "", FILE_EXT[file])
    storage = store.storage
    local = storage.local_path(asset.storage_key)
    if local is None:
        url = storage.signed_url(asset.storage_key, settings.s3_presign_ttl_seconds, download_name=name)
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
    if not local.is_file():
        raise not_found("File")
    return FileResponse(
        local,
        media_type=asset.mime,
        headers={
            "Content-Disposition": content_disposition(name),
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
