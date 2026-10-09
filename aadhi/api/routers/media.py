"""``GET /media/{storage_key}``: public, content-addressed blobs of the local storage backend.

* Only the ``assets/`` namespace (``private/`` and anything invalid -> 404).
* Immutable caching, ``nosniff``, ``Content-Security-Policy: sandbox; default-src 'none'``, and
  ``Content-Disposition: attachment`` for anything that is not audio/video/image.
* Range requests (206) are served by Starlette's ``FileResponse``.
* Remote backends (S3): 302 to a presigned URL generated at serve time.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse, Response

from ...security.headers import build_csp
from ...storage.assets import MIME_BY_EXT
from ...storage.base import is_public_key, validate_key
from ..deps import AppSettings, Store
from ..errors import not_found
from ..util import content_disposition

INLINE_PREFIXES = ("audio/", "video/", "image/")
IMMUTABLE = "public, max-age=31536000, immutable"


def media_mime(storage_key: str) -> str:
    """MIME type from the (app-generated) extension."""
    ext = storage_key.rsplit(".", 1)[-1].lower() if "." in storage_key.rsplit("/", 1)[-1] else ""
    return MIME_BY_EXT.get(ext, "application/octet-stream")


def media_headers(storage_key: str, mime: str, csp: str) -> dict[str, str]:
    """Headers for a public media object."""
    headers = {
        "Cache-Control": IMMUTABLE,
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": csp,
        "Cross-Origin-Resource-Policy": "same-site",
    }
    if not mime.startswith(INLINE_PREFIXES):
        headers["Content-Disposition"] = content_disposition(storage_key.rsplit("/", 1)[-1])
    return headers


def build_media_router(prefix: str = "/media") -> APIRouter:
    """Router serving ``{prefix}/{storage_key}`` (prefix = ``Settings.media_url_prefix``)."""
    router = APIRouter(tags=["media"])
    path = prefix.rstrip("/") or "/media"

    @router.api_route(path + "/{storage_key:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def get_media(storage_key: str, settings: AppSettings, store: Store) -> Response:
        """Serve one public blob."""
        try:
            validate_key(storage_key)
        except ValueError:
            raise not_found("File") from None
        if not is_public_key(storage_key):
            raise not_found("File")
        storage = store.storage
        local = storage.local_path(storage_key)
        if local is None:
            if not storage.exists(storage_key):
                raise not_found("File")
            url = storage.signed_url(storage_key, settings.s3_presign_ttl_seconds)
            return RedirectResponse(url, status_code=302, headers={"Cache-Control": "public, max-age=300"})
        if not local.is_file():
            raise not_found("File")
        mime = media_mime(storage_key)
        return FileResponse(
            local, media_type=mime, headers=media_headers(storage_key, mime, build_csp(settings, kind="media"))
        )

    return router
