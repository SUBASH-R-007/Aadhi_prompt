"""Error envelope: ApiException, foreign HTTPExceptions, validation, domain exceptions, 500s."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from aadhi.api.errors import (
    ApiException,
    UnhandledErrorMiddleware,
    code_for_status,
    http_exception_parts,
    install_exception_handlers,
    seconds_until_utc_midnight,
)


class Body(BaseModel):
    n: int


@pytest.fixture()
def client(app_env):
    from aadhi.auth.errors import AppHTTPException
    from aadhi.jobs.base import BudgetExceeded
    from aadhi.jobs.queue import JobInProgress
    from aadhi.security.uploads import UploadRejected

    app = FastAPI()
    install_exception_handlers(app)
    app.add_middleware(UnhandledErrorMiddleware)

    @app.get("/api-exc")
    def api_exc():
        raise ApiException(409, "revision_conflict", "Changed elsewhere", current_revision=5, headers={"X-Test": "1"})

    @app.get("/plain-http")
    def plain_http():
        raise HTTPException(status_code=403, detail="nope")

    @app.get("/app-http")
    def app_http():
        raise AppHTTPException(429, "Slow down", code="rate_limited", headers={"Retry-After": "7"}, bucket="login")

    @app.get("/dict-detail")
    def dict_detail():
        raise HTTPException(status_code=400, detail={"code": "custom", "message": "Bad thing", "field": "x"})

    @app.post("/validate")
    def validate(body: Body):
        return body

    @app.get("/job-in-progress")
    def job_in_progress():
        raise JobInProgress(job_id=42)

    @app.get("/budget")
    def budget():
        raise BudgetExceeded("Daily budget reached")

    @app.get("/upload")
    def upload():
        raise UploadRejected(415, "unsupported_type", "No SVG")

    @app.get("/integrity")
    def integrity():
        from sqlalchemy.exc import IntegrityError

        raise IntegrityError("INSERT ...", {}, Exception("UNIQUE constraint failed: users.username"))

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals: password=hunter2")

    with TestClient(app) as c:
        yield c


def test_api_exception_envelope(client):
    r = client.get("/api-exc")
    assert r.status_code == 409
    assert r.json() == {"detail": "Changed elsewhere", "code": "revision_conflict", "current_revision": 5}
    assert r.headers["X-Test"] == "1"


def test_plain_http_exception_gets_code_from_status(client):
    r = client.get("/plain-http")
    assert r.json() == {"detail": "nope", "code": "forbidden"}


def test_core_app_http_exception_code_extra_and_headers(client):
    r = client.get("/app-http")
    assert r.status_code == 429
    assert r.json() == {"detail": "Slow down", "code": "rate_limited", "bucket": "login"}
    assert r.headers["Retry-After"] == "7"


def test_dict_detail_is_flattened(client):
    r = client.get("/dict-detail")
    assert r.json() == {"detail": "Bad thing", "code": "custom", "field": "x"}


def test_validation_errors_are_422_without_echoed_input(client):
    r = client.post("/validate", json={"n": "not-a-number"})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation"
    assert body["detail"][0]["loc"] == ["body", "n"]
    assert "input" not in body["detail"][0]


def test_domain_exceptions(client):
    jip = client.get("/job-in-progress")
    assert jip.status_code == 409 and jip.json()["code"] == "job_in_progress" and jip.json()["job_id"] == 42
    budget = client.get("/budget")
    assert budget.status_code == 429 and budget.json()["code"] == "budget"
    assert 0 < int(budget.headers["Retry-After"]) <= 86400
    upload = client.get("/upload")
    assert upload.status_code == 415 and upload.json() == {"detail": "No SVG", "code": "unsupported_type"}


def test_integrity_errors_are_conflicts_without_sql(client):
    r = client.get("/integrity")
    assert r.status_code == 409 and r.json()["code"] == "conflict"
    assert "UNIQUE" not in r.text and "INSERT" not in r.text


def test_unexpected_errors_are_generic_500s(client, caplog):
    r = client.get("/boom")
    assert r.status_code == 500
    assert r.json()["code"] == "internal"
    assert "hunter2" not in r.text and "secret" not in r.text
    assert any("Unhandled error on GET /boom" in rec.getMessage() for rec in caplog.records)


def test_helpers():
    assert code_for_status(404) == "not_found"
    assert code_for_status(418) == "error"
    noon = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    assert seconds_until_utc_midnight(noon) == 12 * 3600
    code, detail, extra = http_exception_parts(HTTPException(status_code=401, detail=None))
    assert (code, detail, extra) == ("unauthenticated", "Unauthorized", {})
