"""``GET /api/videos``: the teacher's video history across their lectures, newest first.

Only the current user's own, not deleted projects (administrators too: other teachers' videos are reached through
their projects, never listed here). Each item: the render and its lecture (``project_id``, ``project_title``,
``version_id``, ``version_number``), ``status``, ``duration_s``, the MP4's ``size_bytes`` / ``width`` / ``height``,
``matches_current`` (made from the version's current revision and its timeline is not stale), ``download_url``
(the authenticated attachment, null without an MP4), ``preview_url`` (a streamable media URL for an in-browser
player, exactly as timeline media is served: a capability URL, or a short-lived presigned one with S3 and no CDN;
null unless the render succeeded) and ``qa_ok`` (the post-render check, null when it did not run). The lecture's
``subject_name``, ``unit_name``, ``session_number``, ``session_title``, ``project_language`` and
``project_created_at`` let the Studio tell lectures sharing a title apart exactly as the project list does. Three
queries per page (count, rows, assets).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from ...models import Project, ProjectVersion, Render
from ...stage import matches_current
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store
from ..timelines import media_url
from ..util import iso, page_params

router = APIRouter(tags=["renders"], dependencies=RATE_LIMITED)

DEFAULT_LIMIT = 24


def _qa_ok(options: Any) -> bool | None:
    qa = options.get("qa") if isinstance(options, dict) else None
    ok = qa.get("ok") if isinstance(qa, dict) else None
    return ok if isinstance(ok, bool) else None


@router.get("/api/videos")
def list_videos(
    user: CurrentUser, db: DbSession, settings: AppSettings, store: Store, limit: int = DEFAULT_LIMIT, offset: int = 0
) -> dict[str, Any]:
    """The current user's renders across their lectures (see the module docstring)."""
    limit, offset = page_params(limit, offset)
    mine = (Project.owner_id == user.id, Project.deleted_at.is_(None))
    joined = (
        select(Render.id)
        .join(ProjectVersion, ProjectVersion.id == Render.version_id)
        .join(Project, Project.id == ProjectVersion.project_id)
        .where(*mine)
    )
    total = db.execute(select(func.count()).select_from(joined.subquery())).scalar_one()
    rows = db.execute(
        select(
            Render,
            ProjectVersion.number,
            ProjectVersion.revision,
            ProjectVersion.built_revision,
            Project.id,
            Project.title,
            Project.subject_name,
            Project.unit_name,
            Project.session_number,
            Project.session_title,
            Project.language,
            Project.created_at,
        )
        .join(ProjectVersion, ProjectVersion.id == Render.version_id)
        .join(Project, Project.id == ProjectVersion.project_id)
        .where(*mine)
        .order_by(Render.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    assets = store.get_many(r[0].video_asset_key for r in rows if r[0].video_asset_key)
    items = []
    for (render, number, revision, built_revision, project_id, title, subject_name, unit_name, session_number,
         session_title, project_language, project_created_at) in rows:
        asset = assets.get(render.video_asset_key) if render.video_asset_key else None
        succeeded = render.status == "succeeded"
        items.append({
            "render_id": render.id,
            "project_id": project_id,
            "project_title": title,
            "version_id": render.version_id,
            "version_number": number,
            "created_at": iso(render.created_at),
            "status": render.status,
            "duration_s": render.duration_s,
            "size_bytes": asset.size_bytes if asset is not None else None,
            "width": asset.width if asset is not None else None,
            "height": asset.height if asset is not None else None,
            "matches_current": matches_current(render.built_revision, int(revision), built_revision),
            "download_url": f"/api/renders/{render.id}/download?file=video" if asset is not None else None,
            "preview_url": media_url(store, settings, asset.storage_key) if succeeded and asset is not None else None,
            "qa_ok": _qa_ok(render.options),
            "subject_name": subject_name,
            "unit_name": unit_name,
            "session_number": session_number,
            "session_title": session_title,
            "project_language": project_language,
            "project_created_at": iso(project_created_at),
        })
    return {"items": items, "total": int(total)}
