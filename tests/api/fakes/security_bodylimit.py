"""Fake aadhi.security.bodylimit: JSON cap and upload cap (declared and streamed)."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse


class _TooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    def __init__(self, app, settings) -> None:
        self.app = app
        self.settings = settings

    def _cap(self, headers: Headers) -> int:
        if headers.get("content-type", "").startswith("multipart/"):
            return (self.settings.upload_max_mb + 1) * 1024 * 1024
        return self.settings.max_json_body_mb * 1024 * 1024

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        cap = self._cap(headers)
        response = JSONResponse({"detail": "Request body too large.", "code": "too_large"}, status_code=413)
        declared = headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            await response(scope, receive, send)
            return
        seen = 0
        started = False

        async def _receive():
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > cap:
                    raise _TooLarge()
            return message

        async def _send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, _receive, _send)
        except _TooLarge:
            if not started:
                await response(scope, receive, send)
