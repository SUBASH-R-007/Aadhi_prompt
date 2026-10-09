"""Static file serving for ``/web`` and ``/branding`` and the HTML pages.

* Explicit MIME types (Windows' registry maps ``.js`` unpredictably; module scripts need
  ``text/javascript`` under ``nosniff``).
* ``/web``: ``no-cache`` (revalidate with ETag) for app code and for vendored files at unversioned
  paths; only content-addressed vendor URLs (a version/hash path segment, or a ``?v=`` query) get a
  long immutable cache. Frontend tests, ``node_modules`` and dot-files are never served; the
  deny-list compares case-insensitively because NTFS (and macOS APFS) paths are.
* ``/branding``: only known media types, long cache.
"""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs

import anyio
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope

MIME_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".wasm": "application/wasm",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".txt": "text/plain",
    ".riv": "application/octet-stream",
}

BRANDING_TYPES = frozenset({".mp4", ".webm", ".mp3", ".wav", ".ogg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".riv"})

NO_CACHE = "no-cache"
IMMUTABLE = "public, max-age=31536000, immutable"
BRANDING_CACHE = "public, max-age=604800"

WEB_DENIED_SEGMENTS = frozenset({"tests", "node_modules"})
# A path segment that pins content: ``name@1.2.3``, ``lib-1.2``, ``v1.2.3``, or a hex content hash
# (``chart.3f2a9b1c.js``). Unversioned vendor paths (``vendor/p5/p5.min.js``) revalidate instead.
_VERSIONED_SEGMENT = re.compile(
    r"(?:^|[@_.-])v?\d+\.\d+(?:\.\d+)?(?:[-+._][0-9a-z.]+)?(?:$|[._-])|(?:^|[._-])[0-9a-f]{8,64}(?:$|[._-])",
    re.IGNORECASE,
)


def media_type_for(path: str | os.PathLike[str]) -> str:
    """MIME type by extension (``application/octet-stream`` when unknown)."""
    return MIME_TYPES.get(Path(path).suffix.lower(), "application/octet-stream")


class _HeaderStaticFiles(StaticFiles):
    """StaticFiles with explicit MIME types and per-path cache headers."""

    def cache_control(self, rel_path: str, query: str = "") -> str:  # pragma: no cover - overridden
        """``Cache-Control`` for a path relative to the mount (``query``: the raw query string)."""
        return NO_CACHE

    def allowed(self, rel_path: str) -> bool:  # pragma: no cover - overridden
        """Whether a path relative to the mount may be served at all."""
        return True

    async def check_config(self) -> None:
        """A missing directory (e.g. no frontend build yet) serves 404s instead of 500s."""
        if self.directory is not None and not await anyio.to_thread.run_sync(os.path.isdir, self.directory):
            raise HTTPException(status_code=404)
        await super().check_config()

    async def get_response(self, path: str, scope: Scope) -> Response:
        """404 for disallowed paths, else Starlette's lookup (directory traversal is refused there)."""
        rel = PurePosixPath(path.replace("\\", "/")).as_posix()
        if not self.allowed(rel):
            raise HTTPException(status_code=404)
        return await super().get_response(path, scope)

    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        """FileResponse with an explicit MIME type, nosniff, the per-path cache policy and 304 support."""
        rel = self.get_path(scope).replace("\\", "/")
        query = bytes(scope.get("query_string") or b"").decode("latin-1")
        headers = {"Cache-Control": self.cache_control(rel, query), "X-Content-Type-Options": "nosniff"}
        response = FileResponse(
            full_path,
            status_code=status_code,
            stat_result=stat_result,
            media_type=media_type_for(full_path),
            headers=headers,
        )
        if self.is_not_modified(response.headers, Headers(scope=scope)):
            return NotModifiedResponse(response.headers)
        return response


def path_segments(rel_path: str) -> list[str]:
    """Non-empty, case-folded segments of a mount-relative path."""
    return [part.casefold() for part in rel_path.replace("\\", "/").split("/") if part]


def is_versioned_path(rel_path: str, query: str = "") -> bool:
    """True when the URL pins its content (version/hash path segment or a non-empty ``v`` query)."""
    if any(v.strip() for v in parse_qs(query).get("v", [])):
        return True
    return any(_VERSIONED_SEGMENT.search(part) for part in rel_path.replace("\\", "/").split("/") if part)


class WebStaticFiles(_HeaderStaticFiles):
    """``/web``: frontend code and vendored libraries (immutable only at content-addressed URLs)."""

    def allowed(self, rel_path: str) -> bool:
        """Everything except frontend tests, node_modules and dot-files/directories (any depth, any case)."""
        parts = path_segments(rel_path)
        if not parts or parts[0] in WEB_DENIED_SEGMENTS:
            return False
        return not any(part.startswith(".") for part in parts)

    def cache_control(self, rel_path: str, query: str = "") -> str:
        """Content-addressed vendor URLs are immutable; everything else revalidates (ETag)."""
        parts = path_segments(rel_path)
        if parts and parts[0] == "vendor" and is_versioned_path("/".join(parts[1:]), query):
            return IMMUTABLE
        return NO_CACHE


class BrandingStaticFiles(_HeaderStaticFiles):
    """``/branding``: mascot clips, background, logo, bgm; only known media types."""

    def allowed(self, rel_path: str) -> bool:
        """Known media types only (no HTML/SVG/JS from the branding directory)."""
        parts = path_segments(rel_path)
        return (
            bool(parts)
            and PurePosixPath(parts[-1]).suffix in BRANDING_TYPES
            and not any(part.startswith(".") for part in parts)
        )

    def cache_control(self, rel_path: str, query: str = "") -> str:
        """Branding changes rarely: cache for a week."""
        return BRANDING_CACHE
