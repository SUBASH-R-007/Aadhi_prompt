"""Request body size limits (pure ASGI).

* ``multipart/form-data`` bodies sent to an upload route (``upload_paths``, exact path match;
  default ``DEFAULT_UPLOAD_PATHS`` = the API's multipart endpoints) may be up to ``UPLOAD_MAX_MB``
  (+1 MiB multipart framing). Every other body -- JSON, form-urlencoded, raw, and multipart sent
  anywhere else -- is capped at ``MAX_JSON_BODY_MB``: merely claiming ``multipart/form-data`` must
  not lift the cap on routes that buffer the body before authenticating (e.g. login).
* ``Content-Length`` is checked before the app runs; bodies without one (chunked) are counted while
  streaming. Exceeding the cap yields ``413 {"detail": ..., "code": "too_large"}``.

When the limit is hit mid-stream, ``receive`` raises ``BodyTooLarge`` -- an ``HTTPException``
subclass, so FastAPI's body parsing re-raises it unchanged and the API's handler renders the
standard envelope; if it propagates up to this middleware instead, the middleware answers 413
itself (when the response has not started yet).
"""

from __future__ import annotations

from collections.abc import Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..auth.errors import AppHTTPException
from ..config import Settings, get_settings
from ._asgi import header, send_json_error

__all__ = ["DEFAULT_UPLOAD_PATHS", "BodySizeLimitMiddleware", "BodyTooLarge", "limit_for"]

MIB = 1024 * 1024
MULTIPART_OVERHEAD = MIB
# The API's multipart endpoints (docs/API.md): create project from a document, import a lecture JSON,
# upload scene media, add a picture or clip to the media library. Anything else gets the JSON cap even
# when it claims multipart/form-data.
DEFAULT_UPLOAD_PATHS: tuple[str, ...] = ("/api/projects", "/api/projects/import", "/api/uploads", "/api/library")
_METHODS_WITHOUT_BODY = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class BodyTooLarge(AppHTTPException):
    """413 ``too_large`` raised from the wrapped ``receive``."""

    def __init__(self, limit: int) -> None:
        super().__init__(413, f"Request body too large (limit {limit // MIB} MB)", code="too_large")
        self.limit = limit


def _normalize_path(path: str) -> str:
    path = (path or "/").split("?", 1)[0]
    return path.rstrip("/") or "/"


def limit_for(scope: Scope, settings: Settings, upload_paths: Iterable[str] = DEFAULT_UPLOAD_PATHS) -> int:
    """Byte limit for the request: the upload cap for multipart bodies sent to one of ``upload_paths``
    (exact match, trailing slash ignored), the JSON cap for everything else."""
    ctype = (header(scope, b"content-type") or "").lower()
    if ctype.startswith("multipart/form-data"):
        allowed = {_normalize_path(p) for p in upload_paths}
        if _normalize_path(str(scope.get("path", "/"))) in allowed:
            return int(settings.upload_max_mb) * MIB + MULTIPART_OVERHEAD
    return int(settings.max_json_body_mb) * MIB


class BodySizeLimitMiddleware:
    """Usage: ``app.add_middleware(BodySizeLimitMiddleware, settings=settings)``.

    ``upload_paths`` overrides ``DEFAULT_UPLOAD_PATHS`` (the only paths that get the upload cap).
    """

    def __init__(
        self, app: ASGIApp, settings: Settings | None = None, upload_paths: Iterable[str] | None = None
    ) -> None:
        self.app = app
        self.settings = settings or get_settings()
        paths = DEFAULT_UPLOAD_PATHS if upload_paths is None else tuple(upload_paths)
        self.upload_paths = frozenset(_normalize_path(p) for p in paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = limit_for(scope, self.settings, self.upload_paths)
        declared = header(scope, b"content-length")
        if declared is not None:
            try:
                length = int(declared.strip())
                if length < 0:
                    raise ValueError
            except ValueError:
                await send_json_error(send, 400, "Invalid Content-Length header", "bad_request")
                return
            if length > limit:
                await send_json_error(send, 413, f"Request body too large (limit {limit // MIB} MB)", "too_large")
                return
        elif scope.get("method", "GET").upper() in _METHODS_WITHOUT_BODY:
            await self.app(scope, receive, send)
            return

        received = 0
        response_started = False
        exceeded: BodyTooLarge | None = None
        replaced = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = BodyTooLarge(limit)
                    raise exceeded
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started, replaced
            if replaced:
                return  # swallow the body of the response we replaced
            if message["type"] == "http.response.start":
                response_started = True
                if exceeded is not None and int(message.get("status", 0)) == 413:
                    # Whatever handler rendered the 413, answer with the standard envelope.
                    replaced = True
                    await send_json_error(send, 413, str(exceeded.detail), "too_large")
                    return
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLarge as exc:
            if response_started:
                raise
            await send_json_error(send, 413, str(exc.detail), "too_large")
