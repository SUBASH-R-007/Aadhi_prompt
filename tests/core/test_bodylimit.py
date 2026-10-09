"""Body size limits (Content-Length precheck + streamed counting)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from aadhi.config import Settings
from aadhi.security.bodylimit import BodySizeLimitMiddleware, limit_for

MIB = 1024 * 1024


def _settings() -> Settings:
    return Settings(max_json_body_mb=1, upload_max_mb=2)


def _starlette_client() -> TestClient:
    seen: list[int] = []

    async def echo(request):
        body = await request.body()
        seen.append(len(body))
        return JSONResponse({"len": len(body)})

    app = Starlette(
        routes=[
            Route("/echo", echo, methods=["POST", "PUT", "GET"]),
            Route("/api/uploads", echo, methods=["POST"]),
            Route("/api/library", echo, methods=["POST"]),
            Route("/api/library/7/describe", echo, methods=["POST"]),
            Route("/api/projects/import", echo, methods=["POST"]),
            Route("/api/projects/importer", echo, methods=["POST"]),
        ]
    )
    app.add_middleware(BodySizeLimitMiddleware, settings=_settings())
    client = TestClient(app)
    client.seen = seen  # type: ignore[attr-defined]
    return client


def _chunks(total: int, size: int = 64 * 1024):
    sent = 0
    while sent < total:
        n = min(size, total - sent)
        sent += n
        yield b"x" * n


def test_small_json_ok():
    c = _starlette_client()
    r = c.post("/echo", content=b"{}" * 100, headers={"Content-Type": "application/json"})
    assert r.status_code == 200 and r.json() == {"len": 200}


def test_content_length_precheck_rejects_before_app_runs():
    c = _starlette_client()
    r = c.post("/echo", content=b"x" * (MIB + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    assert c.seen == []


def test_streamed_body_counted():
    c = _starlette_client()
    r = c.post("/echo", content=_chunks(MIB + 10), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    ok = c.post("/echo", content=_chunks(MIB - 10), headers={"Content-Type": "application/json"})
    assert ok.status_code == 200 and ok.json()["len"] == MIB - 10


def test_multipart_uses_upload_cap_on_upload_routes_only():
    c = _starlette_client()
    ctype = {"Content-Type": "multipart/form-data; boundary=xyz"}
    for path in ("/api/uploads", "/api/uploads/", "/api/projects/import", "/api/library"):
        assert c.post(path, content=b"x" * int(1.5 * MIB), headers=ctype).status_code == 200, path
        assert c.post(path, content=b"x" * (3 * MIB + 2), headers=ctype).status_code == 413, path
    # Claiming multipart elsewhere does not lift the JSON cap (Content-Length precheck and streamed).
    for path in ("/echo", "/api/projects/importer", "/api/library/7/describe"):
        r = c.post(path, content=b"x" * int(1.5 * MIB), headers=ctype)
        assert r.status_code == 413 and r.json()["code"] == "too_large", path
        assert c.post(path, content=_chunks(int(1.5 * MIB)), headers=ctype).status_code == 413, path
    assert (
        c.post("/api/uploads", content=b"x" * int(1.5 * MIB), headers={"Content-Type": "text/plain"}).status_code == 413
    )
    assert c.post("/echo", content=b"x" * 1000, headers=ctype).status_code == 200


def test_custom_upload_paths():
    seen: list[int] = []

    async def echo(request):
        seen.append(len(await request.body()))
        return JSONResponse({"len": seen[-1]})

    app = Starlette(routes=[Route("/files", echo, methods=["POST"]), Route("/api/uploads", echo, methods=["POST"])])
    app.add_middleware(BodySizeLimitMiddleware, settings=_settings(), upload_paths=["/files/"])
    c = TestClient(app)
    ctype = {"Content-Type": "multipart/form-data; boundary=xyz"}
    assert c.post("/files", content=b"x" * int(1.5 * MIB), headers=ctype).status_code == 200
    assert c.post("/api/uploads", content=b"x" * int(1.5 * MIB), headers=ctype).status_code == 413
    assert limit_for({"type": "http", "path": "/x", "headers": []}, _settings()) == MIB


def test_login_cannot_buffer_upload_sized_bodies():
    """The review scenario: an unauthenticated JSON route sent 2.5 MiB claiming multipart."""
    app = FastAPI()

    @app.post("/api/auth/login")
    async def login(request: Request):
        return {"n": len(await request.body())}

    app.add_middleware(BodySizeLimitMiddleware, settings=_settings())
    c = TestClient(app)
    big = b"x" * int(2.5 * MIB)
    r = c.post("/api/auth/login", content=big, headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_invalid_content_length():
    c = _starlette_client()
    for bad in ("abc", "-5"):
        r = c.post("/echo", content=b"{}", headers={"Content-Length": bad, "Content-Type": "application/json"})
        assert r.status_code == 400


def test_fastapi_body_parsing_maps_to_413():
    app = FastAPI()

    @app.post("/json")
    async def take(request: Request, payload: dict):
        return {"n": len(payload)}

    app.add_middleware(BodySizeLimitMiddleware, settings=_settings())
    c = TestClient(app)
    big = b'{"a": "' + b"x" * (MIB + 100) + b'"}'
    r = c.post("/json", content=_chunks(len(big)), headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "too_large"
    assert c.post("/json", json={"a": 1}).json() == {"n": 1}


def test_get_without_body_passes():
    c = _starlette_client()
    assert c.get("/echo").status_code == 200
