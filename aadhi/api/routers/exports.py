"""Version exports: companion sheet (md/html), screenplay JSON, YouTube chapters."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from ...models import Project, ProjectVersion
from ...schemas.jsonsafe import json_safe, validate_stored
from ...schemas.screenplay import Screenplay
from ...schemas.timeline import Timeline
from ...security.headers import build_csp
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, load_version
from ..errors import not_found
from ..util import content_disposition, download_name
from .versions import safe_screenplay

router = APIRouter(prefix="/api/versions", tags=["exports"], dependencies=RATE_LIMITED)

PRIVATE = {"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff"}


def _title(db: Session, version: ProjectVersion) -> str:
    project = db.get(Project, version.project_id)
    return project.title if project is not None else ""


def _screenplay(version: ProjectVersion) -> Screenplay:
    sp = safe_screenplay(version)
    if sp is None:
        raise not_found("Screenplay")
    return sp


@router.get("/{vid}/companion.md")
def companion_markdown(vid: int, user: CurrentUser, db: DbSession) -> Response:
    """Companion sheet as a Markdown attachment."""
    from ...pipeline.companion import render_markdown

    version = load_version(db, user, vid)
    text = json_safe(render_markdown(_screenplay(version)))  # text stored before lone surrogates were refused
    name = download_name(_title(db, version), "md", suffix=" - companion")
    return Response(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={**PRIVATE, "Content-Disposition": content_disposition(name)},
    )


@router.get("/{vid}/companion.html")
def companion_html(vid: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> Response:
    """Printable companion sheet under the strict companion CSP."""
    from ...pipeline.companion import render_html

    version = load_version(db, user, vid)
    html = json_safe(render_html(_screenplay(version)))
    return HTMLResponse(html, headers={**PRIVATE, "Content-Security-Policy": build_csp(settings, kind="companion")})


@router.get("/{vid}/export.json")
def export_json(vid: int, user: CurrentUser, db: DbSession) -> Response:
    """The stored screenplay as a JSON attachment (re-importable; values stored before NaN/Infinity and lone
    surrogates were refused are exported as ``null`` / U+FFFD)."""
    version = load_version(db, user, vid)
    if version.screenplay is None:
        raise not_found("Screenplay")
    name = download_name(_title(db, version), "json")
    return JSONResponse(
        json_safe(version.screenplay), headers={**PRIVATE, "Content-Disposition": content_disposition(name)}
    )


@router.get("/{vid}/chapters.txt")
def chapters_txt(vid: int, user: CurrentUser, db: DbSession) -> Response:
    """YouTube chapter list derived from the timeline."""
    from ...compose.chapters import youtube_chapters

    version = load_version(db, user, vid)
    timeline = validate_stored(Timeline, version.timeline) if version.timeline is not None else None
    if timeline is None:
        raise not_found("Timeline")
    return PlainTextResponse(json_safe(youtube_chapters(timeline)), headers=PRIVATE)
