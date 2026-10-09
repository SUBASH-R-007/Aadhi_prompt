"""Fake aadhi.security.csrf: cookie-authenticated mutations need the header AND same origin."""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


class CSRFMiddleware:
    def __init__(self, app, settings, exempt_paths=()) -> None:
        self.app = app
        self.settings = settings
        self.exempt = set(exempt_paths)
        self.allowed = {_origin(settings.base_url), *settings.cors_origins}

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] in MUTATING and scope["path"] not in self.exempt:
            headers = Headers(scope=scope)
            has_cookie = f"{self.settings.cookie_name}=" in headers.get("cookie", "")
            bearer = headers.get("authorization", "").lower().startswith("bearer ")
            if has_cookie and not bearer:
                origin = headers.get("origin")
                same = origin in self.allowed if origin else headers.get("sec-fetch-site") == "same-origin"
                if headers.get("x-aadhi-csrf") != "1" or not same:
                    resp = JSONResponse({"detail": "CSRF check failed.", "code": "csrf"}, status_code=403)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)
