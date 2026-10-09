"""``GET /api/render/timeline``: timeline for the MP4 render page (scoped render token only).

``render.js`` reads the token from the URL fragment of ``/render-frame`` and sends it as
``Authorization: Bearer``. Claims: ``scope=render``, ``vid`` (required), optional ``rid``
(render id, must belong to ``vid``) and ``include_intro``. With ``include_intro`` (minted by the
``render_video`` job) and a current timeline, the timeline is rebuilt exactly like the render job
does (``build_timeline(screenplay, manifest, revision=version.revision, include_intro=...)``, which
is pure), so the page and the MP4 compositor use identical timings.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ...auth.tokens import InvalidToken, decode_scoped_token
from ...models import Project, ProjectVersion, Render
from ..deps import AppSettings, DbSession, Store, valid_id
from ..errors import ApiException, not_found
from ..timelines import resolve_timeline
from .versions import safe_manifest, safe_screenplay

router = APIRouter(tags=["render"])


def _unauthenticated() -> ApiException:
    return ApiException(
        401, "unauthenticated", "A valid render token is required.", headers={"WWW-Authenticate": "Bearer"}
    )


def _render_timeline(version: ProjectVersion, stored: dict[str, Any], include_intro: Any, settings: AppSettings) -> Any:
    """The render job's timeline (rebuilt with its ``include_intro``), else the stored one."""
    if not isinstance(include_intro, bool) or version.built_revision != version.revision:
        return stored
    screenplay = safe_screenplay(version)
    if screenplay is None:
        return stored
    from ...compose.timeline import build_timeline

    return build_timeline(
        screenplay,
        safe_manifest(version),
        settings=settings,
        version_id=version.id,
        revision=version.revision,
        include_intro=include_intro,
    )


@router.get("/api/render/timeline")
def render_timeline(
    request: Request, db: DbSession, settings: AppSettings, store: Store, vid: int | None = None
) -> dict[str, Any]:
    """Resolved timeline of the version named by the scoped token."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthenticated()
    try:
        claims = decode_scoped_token(token.strip(), "render", settings)
    except InvalidToken:
        raise _unauthenticated() from None
    claim_vid = claims.get("vid")
    if not isinstance(claim_vid, int) or isinstance(claim_vid, bool) or not valid_id(claim_vid):
        raise _unauthenticated()
    if vid is not None and vid != claim_vid:
        raise ApiException(403, "forbidden", "The render token is for another version.")
    version = db.get(ProjectVersion, claim_vid)
    project = db.get(Project, version.project_id) if version is not None else None
    if version is None or project is None or project.deleted_at is not None or version.timeline is None:
        raise not_found("Timeline")
    rid = claims.get("rid")
    if rid is not None:
        render = db.get(Render, rid) if isinstance(rid, int) and valid_id(rid) else None
        if render is None or render.version_id != version.id:
            raise ApiException(403, "forbidden", "The render token does not match this version.")
    timeline = _render_timeline(version, version.timeline, claims.get("include_intro"), settings)
    return resolve_timeline(timeline, store, settings)
