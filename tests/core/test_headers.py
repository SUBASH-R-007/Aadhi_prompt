"""Security headers and path-based CSP."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from aadhi.config import Settings
from aadhi.security.headers import SecurityHeadersMiddleware, build_csp, csp_kind_for_path

APP_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data: blob: https://*.giphy.com; media-src 'self' blob:; connect-src 'self'; "
    "frame-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'self'"
)
SANDBOX_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
    "style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none'; frame-ancestors 'self'"
)
MEDIA_CSP = "sandbox; default-src 'none'"
COMPANION_CSP = (
    "sandbox allow-scripts; default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'"
)


def _settings(**kw) -> Settings:
    return Settings(**kw)


def _client(settings: Settings) -> TestClient:
    async def page(request):
        return PlainTextResponse("ok")

    async def companion(request):
        return PlainTextResponse("sheet", headers={"Content-Security-Policy": build_csp(settings, "companion")})

    async def media_json(request):
        return Response(b"{}", media_type="application/json")

    async def media_png(request):
        return Response(b"\x89PNG", media_type="image/png")

    async def custom_frame(request):
        return PlainTextResponse("x", headers={"X-Frame-Options": "DENY"})

    app = Starlette(
        routes=[
            Route("/", page),
            Route("/sandbox/p5", page),
            Route("/media/assets/x/y.json", media_json),
            Route("/media/assets/x/y.png", media_png),
            Route("/api/versions/1/companion.html", companion),
            Route("/deny", custom_frame),
        ]
    )
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    return TestClient(app)


def test_csp_strings_exact():
    s = _settings(cdn_base_url="", storage_backend="local")
    assert build_csp(s) == APP_CSP
    assert build_csp(s, "sandbox") == SANDBOX_CSP
    assert build_csp(s, "media") == MEDIA_CSP
    assert build_csp(s, "companion") == COMPANION_CSP
    with pytest.raises(ValueError):
        build_csp(s, "bogus")  # type: ignore[arg-type]


def test_cdn_host_added():
    s = _settings(cdn_base_url="https://cdn.example.edu/aadhi/")
    csp = build_csp(s)
    assert "img-src 'self' data: blob: https://*.giphy.com https://cdn.example.edu;" in csp
    assert "media-src 'self' blob: https://cdn.example.edu;" in csp
    assert "script-src 'self';" in csp  # scripts never from the CDN


def test_s3_presign_origins_added_without_cdn():
    s = _settings(storage_backend="s3", s3_bucket="Lectures", s3_region="ap-south-1", cdn_base_url="")
    csp = build_csp(s)
    assert "https://lectures.s3.amazonaws.com https://lectures.s3.ap-south-1.amazonaws.com" in csp
    r2 = _settings(storage_backend="s3", s3_bucket="b", s3_endpoint_url="https://acct.r2.cloudflarestorage.com/")
    assert "media-src 'self' blob: https://acct.r2.cloudflarestorage.com;" in build_csp(r2)


def test_kind_for_path():
    s = _settings(media_url_prefix="/media")
    assert csp_kind_for_path("/sandbox/p5", s) == "sandbox"
    assert csp_kind_for_path("/sandbox/p5/", s) == "sandbox"
    assert csp_kind_for_path("/sandbox/p5x", s) == "app"
    assert csp_kind_for_path("/media/assets/a.png", s) == "media"
    assert csp_kind_for_path("/mediax", s) == "app"
    assert csp_kind_for_path("/web/sandbox/p5.html", s) == "app"


def test_headers_per_path():
    c = _client(_settings(app_env="development"))
    r = c.get("/")
    assert r.headers["content-security-policy"] == APP_CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["cross-origin-opener-policy"] == "same-origin"
    assert "camera=()" in r.headers["permissions-policy"] and "fullscreen=(self)" in r.headers["permissions-policy"]
    assert "strict-transport-security" not in r.headers
    assert c.get("/sandbox/p5").headers["content-security-policy"] == SANDBOX_CSP
    media = c.get("/media/assets/x/y.png")
    assert media.headers["content-security-policy"] == MEDIA_CSP
    assert "content-disposition" not in media.headers
    assert c.get("/media/assets/x/y.json").headers["content-disposition"] == "attachment"


def test_endpoint_headers_not_overridden():
    c = _client(_settings())
    r = c.get("/api/versions/1/companion.html")
    assert r.headers["content-security-policy"] == COMPANION_CSP
    assert len(r.headers.get_list("content-security-policy")) == 1
    assert c.get("/deny").headers["x-frame-options"] == "DENY"


def test_hsts_in_production():
    c = _client(_settings(app_env="production"))
    assert c.get("/").headers["strict-transport-security"] == "max-age=63072000; includeSubDomains"


def test_error_responses_get_headers():
    c = _client(_settings())
    r = c.get("/does-not-exist")
    assert r.status_code == 404
    assert r.headers["content-security-policy"] == APP_CSP
