"""Jobs: list/detail/events, SSE stream, cancel, retry."""

from __future__ import annotations

import json
import threading
import time

from sqlalchemy import func, select

from aadhi.models import Job, Render
from tests.api.factories import add_event, add_job, add_project


def setup_job(api, *, status: str = "running", kind: str = "build_assets", **kw):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        job = add_job(db, kind=kind, status=status, user=alice, project=project, version=version, **kw)
    return alice, c, project, version, job


def parse_sse(text: str) -> list[dict]:
    """[{event, id, data}] for every dispatched message (comments and retry lines dropped)."""
    out = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        msg: dict = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data":
                msg["data"] = msg.get("data", "") + value
            elif field in ("event", "id", "retry"):
                msg[field] = value
        if "data" in msg or "retry" in msg:
            out.append(msg)
    return out


def test_list_and_filter_jobs(api):
    alice, c, project, version, job = setup_job(api, status="running")
    with api.db() as db:
        add_job(db, kind="render_video", status="failed", user=alice, project=project, version=version)
        bob = api.user("bob")
        add_job(db, kind="cleanup", status="succeeded", user=bob)
    body = c.get("/api/jobs").json()
    assert body["total"] == 2
    assert [j["kind"] for j in body["items"]] == ["render_video", "build_assets"]
    assert c.get("/api/jobs", params={"status": "failed"}).json()["total"] == 1
    assert c.get("/api/jobs", params={"status": "running,failed"}).json()["total"] == 2
    assert c.get("/api/jobs", params={"status": "bogus"}).status_code == 422
    assert c.get("/api/jobs", params={"project_id": project.id}).json()["total"] == 2
    assert c.get("/api/jobs", params={"project_id": 9999}).status_code == 404


def test_job_detail_includes_result(api):
    _, c, project, version, job = setup_job(api, status="succeeded", result={"version_id": 1, "issue_counts": {}})
    body = c.get(f"/api/jobs/{job.id}").json()
    assert body["id"] == job.id and body["status"] == "succeeded"
    assert body["result"] == {"version_id": 1, "issue_counts": {}}
    assert c.get("/api/jobs/99999").status_code == 404


def test_job_events_after_cursor(api):
    _, c, project, version, job = setup_job(api)
    with api.db() as db:
        first = add_event(db, job, "Planning", progress=0.1)
        add_event(db, job, "Writing scene 1/3", progress=0.3)
    items = c.get(f"/api/jobs/{job.id}/events").json()["items"]
    messages = [e["message"] for e in items]
    assert "Planning" in messages and "Writing scene 1/3" in messages
    after = c.get(f"/api/jobs/{job.id}/events", params={"after": first.id}).json()["items"]
    assert "Planning" not in [e["message"] for e in after]
    assert c.get(f"/api/jobs/{job.id}/events", params={"limit": 0}).status_code == 422


def test_stream_emits_events_and_ends_for_finished_job(api):
    _, c, project, version, job = setup_job(api, status="succeeded")
    with api.db() as db:
        e1 = add_event(db, job, "Planning", progress=0.1)
        e2 = add_event(db, job, "Done", progress=1.0)
    with c.stream("GET", f"/api/jobs/{job.id}/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "no-cache" in r.headers["cache-control"]
        text = r.read().decode()
    messages = parse_sse(text)
    assert messages[0].get("retry") == "5000"
    job_events = [m for m in messages if m.get("event") == "job_event"]
    assert [int(m["id"]) for m in job_events][-2:] == [e1.id, e2.id]
    assert json.loads(job_events[-1]["data"])["message"] == "Done"
    assert messages[-1]["event"] == "end"
    assert json.loads(messages[-1]["data"])["status"] == "succeeded"


def test_stream_resumes_after_last_event_id(api):
    _, c, project, version, job = setup_job(api, status="failed")
    with api.db() as db:
        e1 = add_event(db, job, "first")
        e2 = add_event(db, job, "second")
    with c.stream("GET", f"/api/jobs/{job.id}/stream", headers={"Last-Event-ID": str(e1.id)}) as r:
        messages = parse_sse(r.read().decode())
    ids = [int(m["id"]) for m in messages if m.get("event") == "job_event"]
    assert e1.id not in ids and e2.id in ids


def test_stream_follows_a_running_job_until_it_ends(api):
    _, c, project, version, job = setup_job(api, status="running")

    def finish_later() -> None:
        time.sleep(0.6)
        with api.db() as db:
            add_event(db, db.get(Job, job.id), "Finishing")
            db.get(Job, job.id).status = "succeeded"
            db.commit()

    t = threading.Thread(target=finish_later)
    t.start()
    try:
        with c.stream("GET", f"/api/jobs/{job.id}/stream") as r:
            messages = parse_sse(r.read().decode())
    finally:
        t.join()
    kinds = [m.get("event") for m in messages]
    assert "job" in kinds  # running summary first
    assert kinds[-1] == "end"
    assert any(m.get("event") == "job_event" and "Finishing" in m["data"] for m in messages)


def test_stream_holds_no_request_scoped_session_while_streaming(api):
    from aadhi.db import get_db, get_sessionmaker

    open_sessions: list[int] = []

    def tracking_db():
        db = get_sessionmaker()()
        open_sessions.append(1)
        try:
            yield db
        finally:
            db.close()
            open_sessions.pop()

    _, c, project, version, job = setup_job(api, status="running")
    api.app.dependency_overrides[get_db] = tracking_db

    def finish_later() -> None:
        time.sleep(0.8)
        with api.db() as db:
            db.get(Job, job.id).status = "succeeded"
            db.commit()

    t = threading.Thread(target=finish_later)
    t.start()
    try:
        with c.stream("GET", f"/api/jobs/{job.id}/stream") as r:
            lines = r.iter_lines()
            assert next(lines).startswith("retry:")
            assert open_sessions == []  # auth + endpoint sessions closed before the body streams
            rest = "\n".join(lines)
    finally:
        t.join()
        api.app.dependency_overrides.pop(get_db, None)
    assert "event: end" in rest


def test_stream_cap_per_user_returns_429(api):
    _, c, project, version, job = setup_job(api, status="succeeded")
    api.settings.sse_max_streams_per_user = 0
    r = c.get(f"/api/jobs/{job.id}/stream")
    assert r.status_code == 429
    assert r.json()["code"] == "rate_limited"
    assert "Retry-After" in r.headers


def test_cancel_queued_job_and_idempotent_on_finished(api):
    _, c, project, version, job = setup_job(api, status="queued")
    r = c.post(f"/api/jobs/{job.id}/cancel")
    assert r.status_code == 202
    assert r.json()["job"]["status"] == "cancelled"
    again = c.post(f"/api/jobs/{job.id}/cancel")
    assert again.status_code == 202 and again.json()["job"]["status"] == "cancelled"


def test_cancel_running_job_requests_cancellation(api):
    _, c, project, version, job = setup_job(api, status="running")
    assert c.post(f"/api/jobs/{job.id}/cancel").status_code == 202
    with api.db() as db:
        row = db.get(Job, job.id)
        assert row.cancel_requested is True and row.status == "running"


def test_retry_failed_job_creates_new_job_with_same_payload(api):
    _, c, project, version, job = setup_job(api, status="failed", payload={"scene_ids": None, "base_revision": 1})
    r = c.post(f"/api/jobs/{job.id}/retry")
    assert r.status_code == 202, r.text
    new = r.json()["job"]
    assert new["id"] != job.id and new["kind"] == "build_assets" and new["status"] == "queued"
    with api.db() as db:
        assert db.get(Job, new["id"]).payload == {"scene_ids": None, "base_revision": 1}
    twice = c.post(f"/api/jobs/{job.id}/retry")  # each job is retried at most once
    assert twice.status_code == 409 and twice.json()["code"] == "job_not_retryable", twice.text
    with api.db() as db:
        active = db.execute(select(func.count()).select_from(Job).where(Job.status == "queued")).scalar_one()
    assert active == 1


def test_retry_while_version_is_busy_is_job_in_progress(api):
    alice, c, project, version, failed = setup_job(api, status="failed", payload={"scene_ids": None})
    with api.db() as db:
        running = add_job(db, kind="regenerate_scene", status="running", user=alice, project=project, version=version)
    r = c.post(f"/api/jobs/{failed.id}/retry")
    assert r.status_code == 409 and r.json() == {
        "detail": r.json()["detail"],
        "code": "job_in_progress",
        "job_id": running.id,
    }
    # The refused retry did not consume the job's single retry.
    with api.db() as db:
        db.get(Job, running.id).status = "succeeded"
        db.commit()
    assert c.post(f"/api/jobs/{failed.id}/retry").status_code == 202


def test_retry_only_failed_or_cancelled(api):
    _, c, project, version, job = setup_job(api, status="running")
    r = c.post(f"/api/jobs/{job.id}/retry")
    assert r.status_code == 409 and r.json()["code"] == "job_not_retryable"


def test_retry_render_job_relinks_the_render(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        render = Render(version_id=version.id, status="failed")
        db.add(render)
        db.commit()
        job = add_job(
            db,
            kind="render_video",
            status="failed",
            user=alice,
            project=project,
            version=version,
            payload={"render_id": render.id, "burn_captions": False, "include_intro": True},
        )
    new = c.post(f"/api/jobs/{job.id}/retry").json()["job"]
    with api.db() as db:
        row = db.get(Render, render.id)
        assert row.job_id == new["id"] and row.status == "queued"


def test_retry_admin_only_kinds(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        job = add_job(db, kind="cleanup", status="failed", user=alice)
    r = c.post(f"/api/jobs/{job.id}/retry")
    assert r.status_code == 403


def test_closing_wrapper_always_closes_the_stream():
    import asyncio

    from aadhi.api.sse import closing

    closed: list[bool] = []

    class Stream:
        def __init__(self) -> None:
            self.items = iter(["a", "b", "c"])

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            try:
                return next(self.items)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self) -> None:
            closed.append(True)

    async def consume_two() -> list[str]:
        gen = closing(Stream())
        out = [await gen.__anext__(), await gen.__anext__()]
        await gen.aclose()  # client went away mid-stream
        return out

    assert asyncio.run(consume_two()) == ["a", "b"]
    assert closed == [True]


def test_refused_retry_does_not_use_up_the_generation_quota(api):
    api.settings.generation_rate_limit_per_hour = 1
    payload = {"scene_id": "s1", "instructions": "", "base_revision": 1}
    alice, c, project, version, failed = setup_job(api, kind="regenerate_scene", status="failed", payload=payload)
    with api.db() as db:
        running = add_job(db, kind="build_assets", status="running", user=alice, project=project, version=version)
        other = add_job(
            db, kind="regenerate_scene", status="failed", user=alice, project=project, version=version, payload=payload
        )
    for _ in range(3):
        busy = c.post(f"/api/jobs/{failed.id}/retry")
        assert busy.status_code == 409 and busy.json()["code"] == "job_in_progress", busy.text
    with api.db() as db:
        db.get(Job, running.id).status = "succeeded"
        db.commit()
    retried = c.post(f"/api/jobs/{failed.id}/retry")
    assert retried.status_code == 202, retried.text  # the 409s spent nothing
    with api.db() as db:
        db.get(Job, retried.json()["job"]["id"]).status = "succeeded"
        db.commit()
    limited = c.post(f"/api/jobs/{other.id}/retry")
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    # The 429 rolled back retry_job: no new job, and the job keeps its single retry.
    with api.db() as db:
        assert db.execute(select(func.count()).select_from(Job).where(Job.status == "queued")).scalar_one() == 0
    api.settings.generation_rate_limit_per_hour = 10
    assert c.post(f"/api/jobs/{other.id}/retry").status_code == 202
