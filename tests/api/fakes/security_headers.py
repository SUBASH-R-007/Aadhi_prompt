"""Fake aadhi.security.headers (CSP strings from docs/ARCHITECTURE.md section 11)."""

from __future__ import annotations

from starlette.datastructures import MutableHeaders

APP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data: blob: https://*.giphy.com; media-src 'self' blob:; connect-src 'self'; frame-src 'self'; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
)
SANDBOX = (
    "default-src 'none'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; style-src 'unsafe-inline'; "
    "img-src data: blob:; connect-src 'none'; frame-ancestors 'self'"
)
MEDIA = "sandbox; default-src 'none'"
COMPANION = (
    "sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'"
)


def build_csp(settings, kind: str = "app") -> str:
    return {"app": APP, "sandbox": SANDBOX, "media": MEDIA, "companion": COMPANION}[kind]


class SecurityHeadersMiddleware:
    def __init__(self, app, settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault("X-Frame-Options", "SAMEORIGIN")
                headers.setdefault("Content-Security-Policy", APP)
            await send(message)

        await self.app(scope, receive, _send)
