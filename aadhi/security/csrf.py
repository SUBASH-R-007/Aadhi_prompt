"""CSRF protection for cookie-authenticated requests (pure ASGI).

A state-changing request (POST/PUT/PATCH/DELETE) that carries the session cookie must

1. send ``X-Aadhi-CSRF: 1`` (a custom header forces a CORS preflight cross-origin), AND
2. come from an allowed origin: ``Origin`` equal to the ``BASE_URL`` origin or one of
   ``CORS_ORIGINS``, or -- when the browser sent no usable ``Origin`` -- ``Sec-Fetch-Site`` of
   ``same-origin`` / ``none``.

Requests without the session cookie (Bearer-only API clients, anonymous share-link analytics) are
not subject to CSRF and pass. ``exempt_paths`` (e.g. ``/api/auth/login``) skip the cookie rules but
a *present* foreign ``Origin`` is still refused there (login CSRF). Failures: ``403
{"detail": ..., "code": "csrf"}``.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from starlette.requests import cookie_parser
from starlette.types import ASGIApp, Receive, Scope, Send

from ..config import Settings, get_settings
from ._asgi import header, headers_all, normalize_origin, send_json_error

__all__ = ["CSRF_HEADER", "SESSION_COOKIE_NAMES", "CSRFMiddleware", "allowed_origins"]

CSRF_HEADER = b"x-aadhi-csrf"
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SESSION_COOKIE_NAMES = ("__Host-aadhi_session", "aadhi_session")
_SAFE_FETCH_SITES = frozenset({"same-origin", "none"})


_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def allowed_origins(settings: Settings) -> frozenset[str]:
    """Normalised origins accepted for cookie-authenticated mutations.

    When BASE_URL is a loopback address (local development), the other loopback aliases on the
    same scheme and port are accepted too: ``localhost`` and ``127.0.0.1`` are the same machine,
    and no remote site can be served from them.
    """
    out = {normalize_origin(settings.base_url)}
    out.update(normalize_origin(o) for o in settings.cors_origins)
    try:
        parts = urlsplit(settings.base_url)
        host, port, scheme = (parts.hostname or "").lower(), parts.port, (parts.scheme or "http").lower()
    except ValueError:
        host, port, scheme = "", None, "http"
    if host in _LOOPBACK_HOSTS:
        for alias in _LOOPBACK_HOSTS:
            netloc = f"[{alias}]" if ":" in alias else alias
            out.add(normalize_origin(f"{scheme}://{netloc}" + (f":{port}" if port else "")))
    return frozenset(o for o in out if o)


def _has_session_cookie(scope: Scope) -> bool:
    """True if any Cookie header carries a session cookie (parsed exactly like ``Request.cookies``)."""
    for raw in headers_all(scope, b"cookie"):
        parsed = cookie_parser(raw)  # same lenient parser Starlette uses; it never raises
        if any(name in parsed for name in SESSION_COOKIE_NAMES):
            return True
    return False


class CSRFMiddleware:
    """Usage: ``app.add_middleware(CSRFMiddleware, settings=settings, exempt_paths=("/api/auth/login",))``."""

    def __init__(self, app: ASGIApp, settings: Settings | None = None, exempt_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.settings = settings or get_settings()
        self.exempt_paths = frozenset(p.rstrip("/") or "/" for p in exempt_paths)
        self.origins = allowed_origins(self.settings)

    def _origin_ok(self, scope: Scope) -> bool:
        origin_raw = header(scope, b"origin")
        if origin_raw is not None and origin_raw.strip() and origin_raw.strip() != "null":
            return normalize_origin(origin_raw) in self.origins
        site = (header(scope, b"sec-fetch-site") or "").strip().lower()
        return site in _SAFE_FETCH_SITES

    def _foreign_origin(self, scope: Scope) -> bool:
        origin_raw = header(scope, b"origin")
        if origin_raw is None or not origin_raw.strip():
            return False
        return normalize_origin(origin_raw) not in self.origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method", "GET").upper() not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        path = (scope.get("path") or "/").rstrip("/") or "/"
        if path in self.exempt_paths:
            if self._foreign_origin(scope):
                await send_json_error(send, 403, "Cross-origin request refused", "csrf")
                return
            await self.app(scope, receive, send)
            return
        if not _has_session_cookie(scope):
            await self.app(scope, receive, send)
            return
        if (header(scope, CSRF_HEADER) or "").strip() != "1":
            await send_json_error(send, 403, "Missing CSRF header", "csrf")
            return
        if not self._origin_ok(scope):
            await send_json_error(send, 403, "Cross-origin request refused", "csrf")
            return
        await self.app(scope, receive, send)
