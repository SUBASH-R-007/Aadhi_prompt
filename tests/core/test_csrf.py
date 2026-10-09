"""CSRF middleware matrix."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from aadhi.config import Settings
from aadhi.security.csrf import CSRFMiddleware, allowed_origins

BASE = "https://aadhi.rec.edu"


def _client(**settings_kw) -> TestClient:
    calls: list[str] = []

    async def endpoint(request):
        calls.append(request.method)
        return JSONResponse({"ok": True})

    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
    app = Starlette(
        routes=[Route("/api/thing", endpoint, methods=methods), Route("/api/auth/login", endpoint, methods=methods)]
    )
    settings = Settings(base_url=BASE, **settings_kw)
    app.add_middleware(CSRFMiddleware, settings=settings, exempt_paths=("/api/auth/login",))
    client = TestClient(app, base_url=BASE)
    client.calls = calls  # type: ignore[attr-defined]
    return client


SESSION = {"Cookie": "aadhi_session=tok"}
HOST_SESSION = {"Cookie": "other=1; __Host-aadhi_session=tok"}
GOOD = {"X-Aadhi-CSRF": "1", "Origin": BASE}


def _post(c, headers, method="POST", path="/api/thing"):
    return c.request(method, path, headers=headers)


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({}, 200),  # no cookie: not a CSRF target
        ({"Authorization": "Bearer t"}, 200),  # bearer-only API client
        ({**SESSION}, 403),  # cookie, no header
        ({**SESSION, "X-Aadhi-CSRF": "0", "Origin": BASE}, 403),  # wrong header value
        ({**SESSION, **GOOD}, 200),
        ({**HOST_SESSION, **GOOD}, 200),
        ({**HOST_SESSION, "Origin": BASE}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "https://evil.example"}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "https://aadhi.rec.edu.evil.example"}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "http://aadhi.rec.edu"}, 403),  # scheme differs
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "https://AADHI.rec.edu:443"}, 200),  # normalised
        ({**SESSION, "X-Aadhi-CSRF": "1", "Sec-Fetch-Site": "same-origin"}, 200),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Sec-Fetch-Site": "none"}, 200),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Sec-Fetch-Site": "cross-site"}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Sec-Fetch-Site": "same-site"}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1"}, 403),  # no origin information at all
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "null"}, 403),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "null", "Sec-Fetch-Site": "same-origin"}, 200),
        ({**SESSION, "X-Aadhi-CSRF": "1", "Origin": "https://evil.example", "Sec-Fetch-Site": "same-origin"}, 403),
        ({**SESSION, "Authorization": "Bearer t"}, 403),  # cookie present -> still CSRF-checked
    ],
)
def test_matrix_post(headers, status):
    c = _client()
    r = _post(c, headers)
    assert r.status_code == status
    if status == 403:
        assert r.json()["code"] == "csrf"
        assert c.calls == []  # the app never ran


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_all_unsafe_methods(method):
    c = _client()
    assert _post(c, SESSION, method).status_code == 403
    assert _post(c, {**SESSION, **GOOD}, method).status_code == 200


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_safe_methods_pass(method):
    c = _client()
    assert c.request(method, "/api/thing", headers={**SESSION, "Origin": "https://evil.example"}).status_code == 200


def test_cors_origins_allowed():
    c = _client(cors_origins="https://studio.rec.edu")
    ok = {**SESSION, "X-Aadhi-CSRF": "1", "Origin": "https://studio.rec.edu"}
    assert _post(c, ok).status_code == 200
    assert allowed_origins(Settings(base_url=BASE, cors_origins="https://studio.rec.edu")) == {
        "https://aadhi.rec.edu",
        "https://studio.rec.edu",
    }


def test_exempt_login_path():
    c = _client()
    assert _post(c, SESSION, path="/api/auth/login").status_code == 200  # stale cookie, no header
    assert _post(c, {"Origin": BASE}, path="/api/auth/login").status_code == 200
    r = _post(c, {"Origin": "https://evil.example"}, path="/api/auth/login")  # login CSRF
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    assert _post(c, {}, path="/api/auth/login/").status_code == 200


def test_cookie_name_substring_is_not_a_session():
    c = _client()
    assert _post(c, {"Cookie": "not_aadhi_session_x=1"}).status_code == 200


def test_loopback_aliases_are_same_origin_in_dev():
    """localhost and 127.0.0.1 are the same machine: a dev opened via either name must work."""
    from aadhi.config import Settings
    from aadhi.security.csrf import allowed_origins

    dev = allowed_origins(Settings(base_url="http://127.0.0.1:8010", _env_file=None))
    assert "http://localhost:8010" in dev and "http://127.0.0.1:8010" in dev
    assert "http://localhost:8011" not in dev  # other ports are other origins
    prod = allowed_origins(Settings(base_url="https://lectures.example.edu", _env_file=None))
    assert prod == frozenset({"https://lectures.example.edu"})
