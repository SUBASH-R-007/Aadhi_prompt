"""Security response headers (pure ASGI) and Content-Security-Policy strings (ARCHITECTURE §11).

Path-based CSP: ``/sandbox/p5`` gets the sandbox policy, ``<MEDIA_URL_PREFIX>/`` the media policy,
everything else the app policy. A CSP the endpoint already set (e.g. the companion sheet) is never
overridden; neither is any other header the endpoint set itself.
"""

from __future__ import annotations

from typing import Literal

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ..config import Settings, get_settings
from ._asgi import normalize_origin

__all__ = ["CspKind", "SecurityHeadersMiddleware", "build_csp", "csp_kind_for_path"]

CspKind = Literal["app", "sandbox", "media", "companion"]

SANDBOX_PATH = "/sandbox/p5"
PERMISSIONS_POLICY = (
    "accelerometer=(), autoplay=(self), bluetooth=(), browsing-topics=(), camera=(), display-capture=(), "
    "fullscreen=(self), geolocation=(), gyroscope=(), hid=(), magnetometer=(), microphone=(), midi=(), "
    "payment=(), serial=(), usb=(), xr-spatial-tracking=()"
)
HSTS = "max-age=63072000; includeSubDomains"
_MEDIA_TYPES = ("image/", "audio/", "video/")


def _media_sources(settings: Settings) -> list[str]:
    """Extra origins media may load from: the CDN, or (S3 without CDN) the bucket's presign origin."""
    if settings.cdn_base_url:
        origin = normalize_origin(settings.cdn_base_url)
        return [origin] if origin else []
    if settings.storage_backend == "s3":
        from ..storage.s3 import s3_media_origins  # pure helper; does not import boto3

        return s3_media_origins(settings)
    return []


def build_csp(settings: Settings, kind: CspKind = "app") -> str:
    """Return the Content-Security-Policy for ``kind`` (strings per ARCHITECTURE §11 / docs/API.md)."""
    if kind == "sandbox":
        return (
            "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
            "style-src 'unsafe-inline'; "
            "img-src data: blob:; connect-src 'none'; frame-ancestors 'self'"
        )
    if kind == "media":
        return "sandbox; default-src 'none'"
    if kind == "companion":
        return (
            "sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'"
        )
    if kind != "app":
        raise ValueError(f"unknown CSP kind {kind!r}")
    extra = "".join(f" {src}" for src in _media_sources(settings))
    return (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
        f"img-src 'self' data: blob: https://*.giphy.com{extra}; media-src 'self' blob:{extra}; "
        "connect-src 'self'; frame-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'self'"
    )


def csp_kind_for_path(path: str, settings: Settings) -> CspKind:
    """Which CSP applies to a request path."""
    if path == SANDBOX_PATH or path.startswith(SANDBOX_PATH + "/"):
        return "sandbox"
    media_prefix = (settings.media_url_prefix or "/media").rstrip("/") + "/"
    if path.startswith(media_prefix):
        return "media"
    return "app"


class SecurityHeadersMiddleware:
    """Adds CSP, nosniff, Referrer-Policy, Permissions-Policy, X-Frame-Options, COOP and (production)
    HSTS to every HTTP response without overriding headers the endpoint already set.

    Usage: ``app.add_middleware(SecurityHeadersMiddleware, settings=settings)``.
    """

    def __init__(self, app: ASGIApp, settings: Settings | None = None) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self._csp = {kind: build_csp(self.settings, kind) for kind in ("app", "sandbox", "media")}
        common = {
            "x-content-type-options": "nosniff",
            "referrer-policy": "strict-origin-when-cross-origin",
            "permissions-policy": PERMISSIONS_POLICY,
            "x-frame-options": "SAMEORIGIN",
            "cross-origin-opener-policy": "same-origin",
        }
        if self.settings.is_production:
            common["strict-transport-security"] = HSTS
        self._common = common

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        kind = csp_kind_for_path(scope.get("path") or "/", self.settings)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "content-security-policy" not in headers:
                    headers["content-security-policy"] = self._csp[kind]
                for name, value in self._common.items():
                    if name not in headers:
                        headers[name] = value
                status = int(message.get("status", 200))
                if kind == "media" and 200 <= status < 300 and "content-disposition" not in headers:
                    ctype = headers.get("content-type", "").lower()
                    if not ctype.startswith(_MEDIA_TYPES):
                        headers["content-disposition"] = "attachment"
            await send(message)

        await self.app(scope, receive, send_with_headers)
