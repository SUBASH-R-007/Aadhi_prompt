"""CSRF on cookie-authenticated mutations; CORS only for configured exact origins."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.api.factories import PASSWORD


def _session_client(api, headers: dict[str, str]) -> TestClient:
    api.user("alice")
    c = api.client(browser=False)
    r = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200
    c.headers.clear()
    c.headers.update(headers)
    return c


def test_cookie_mutation_without_csrf_header_is_refused(api):
    c = _session_client(api, {"Origin": "http://testserver"})
    r = c.post("/api/projects/1/shares", json={})
    assert r.status_code == 403
    assert r.json()["code"] == "csrf"


def test_cookie_mutation_from_foreign_origin_is_refused(api):
    c = _session_client(api, {"Origin": "https://evil.example", "X-Aadhi-CSRF": "1"})
    r = c.post("/api/projects/1/shares", json={})
    assert r.status_code == 403
    assert r.json()["code"] == "csrf"


def test_cookie_mutation_same_origin_with_header_passes_csrf(api):
    c = _session_client(api, {"Origin": "http://testserver", "X-Aadhi-CSRF": "1"})
    r = c.post("/api/projects/1/shares", json={})
    assert r.status_code == 404  # reached the endpoint: project 1 does not exist


def test_safe_methods_need_no_csrf_header(api):
    c = _session_client(api, {})
    assert c.get("/api/projects").status_code == 200


def test_csrf_failures_still_carry_security_headers(api):
    c = _session_client(api, {"Origin": "http://testserver"})
    r = c.delete("/api/shares/" + "x" * 32)
    assert r.status_code == 403
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert "content-security-policy" in r.headers


def test_no_cors_headers_without_configured_origins(api):
    r = api.client(browser=False).get("/healthz", headers={"Origin": "https://app.example.com"})
    assert "access-control-allow-origin" not in r.headers


def test_cors_allows_only_configured_origins_with_credentials(app_env):
    from aadhi.main import create_app

    settings = app_env.model_copy(update={"cors_origins": ["https://app.example.com"]})
    with TestClient(create_app(settings), base_url="http://testserver") as c:
        pre = c.options(
            "/api/projects",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Aadhi-CSRF, Content-Type",
            },
        )
        assert pre.status_code == 200
        assert pre.headers["access-control-allow-origin"] == "https://app.example.com"
        assert pre.headers["access-control-allow-credentials"] == "true"
        assert "x-aadhi-csrf" in pre.headers["access-control-allow-headers"].lower()
        denied = c.options(
            "/api/projects",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        assert denied.headers.get("access-control-allow-origin") != "https://evil.example"
        simple = c.get("/api/projects", headers={"Origin": "https://app.example.com"})
        assert simple.status_code == 401
        assert simple.headers["access-control-allow-origin"] == "https://app.example.com"
        assert simple.headers.get("access-control-allow-origin") != "*"
