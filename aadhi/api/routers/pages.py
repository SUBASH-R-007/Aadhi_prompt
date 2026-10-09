"""HTML pages served from ``web_dir`` (Studio, public watch, preview, render frame, p5 sandbox)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ...config import Settings
from ...security.headers import build_csp
from ..deps import AppSettings
from ..errors import not_found

router = APIRouter(include_in_schema=False)

CspKind = Literal["app", "sandbox", "media", "companion"]


def page_response(
    settings: Settings, relative: str, *, kind: CspKind = "app", extra: dict[str, str] | None = None
) -> FileResponse:
    """An HTML file from ``web_dir`` with no-cache and its CSP (404 if the file is missing)."""
    root = settings.web_dir.resolve()
    path = (root / relative).resolve()
    if root not in path.parents or not path.is_file():
        raise not_found("Page")
    headers = {
        "Cache-Control": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": build_csp(settings, kind=kind),
        **(extra or {}),
    }
    return FileResponse(path, media_type="text/html", headers=headers)


@router.api_route("/", methods=["GET", "HEAD"])
def studio(settings: AppSettings) -> FileResponse:
    """The Studio single-page app."""
    return page_response(settings, "index.html")


@router.api_route("/watch/{token}", methods=["GET", "HEAD"])
def watch(token: str, settings: AppSettings) -> FileResponse:
    """Public player (the token is read by the page; never leaked via Referer)."""
    return page_response(settings, "watch.html", extra={"Referrer-Policy": "no-referrer"})


@router.api_route("/preview/{version_id}", methods=["GET", "HEAD"])
def preview(version_id: str, settings: AppSettings) -> FileResponse:
    """Authenticated preview player (the page fetches the timeline with the session)."""
    if not version_id.isdigit():
        raise not_found("Page")
    return page_response(settings, "watch.html")


@router.api_route("/render-frame", methods=["GET", "HEAD"])
def render_frame(settings: AppSettings) -> FileResponse:
    """Render-mode page driven by the MP4 renderer (token in the URL fragment)."""
    return page_response(settings, "render.html", extra={"Referrer-Policy": "no-referrer"})


@router.api_route("/sandbox/p5", methods=["GET", "HEAD"])
def p5_sandbox(settings: AppSettings) -> FileResponse:
    """p5 sandbox page with its dedicated CSP (embedded with sandbox="allow-scripts")."""
    return page_response(settings, "sandbox/p5.html", kind="sandbox")
