"""JobEvent serialisation, list_events and the SSE stream."""

from __future__ import annotations

import asyncio
import gc
import json
import threading

import pytest
from sqlalchemy import delete

from aadhi.db import session_scope
from aadhi.jobs.base import AwaitingReview, job_handler
from aadhi.jobs.events import (
    TooManyStreams,
    event_dict,
    list_events,
    open_stream_counts,
    sse_message,
    stream_job,
)
from aadhi.jobs.queue import add_event
from aadhi.models import Job, JobEvent

from ._helpers import add_job, make_project, make_user, update_job


def parse(chunks: list[str]) -> list[dict]:
    """Split SSE chunks into messages: {'event', 'id', 'data', 'comment', 'retry'}."""
    out = []
    for chunk in chunks:
        assert chunk.endswith("\n\n")
        msg: dict = {}
        for line in chunk.strip("\n").split("\n"):
            key, _, value = line.partition(": ")
            if line.startswith(":"):
                msg["comment"] = line[1:].strip()
            elif key == "data":
                msg["data"] = json.loads(value)
            else:
                msg[key] = value
        out.append(msg)
    return out


async def collect(stream, limit: int = 500, timeout: float = 10.0) -> list[str]:
    chunks: list[str] = []

    async def run() -> None:
        async for chunk in stream:
            chunks.append(chunk)
            if len(chunks) >= limit:
                break

    try:
        await asyncio.wait_for(run(), timeout)
    finally:
        await stream.aclose()
    return chunks


def fast(**kw):
    return {"poll_interval": 0.02, **kw}


def add_events(job_id: int, n: int) -> None:
    with session_scope() as db:
        for i in range(n):
            add_event(db, job_id, f"m{i}", stage="s", progress=i / 10, data={"i": i})


def test_event_dict_and_list_events(app_env):
    jid = add_job()
    other = add_job()
    add_events(jid, 5)
    add_events(other, 2)
    with session_scope() as db:
        items = list_events(db, jid)
        assert [e["message"] for e in items] == [f"m{i}" for i in range(5)]
        assert set(items[0]) == {"id", "job_id", "created_at", "level", "stage", "message", "progress", "data"}
        assert items[0]["created_at"].endswith("+00:00") and items[1]["data"] == {"i": 1}
        assert [e["message"] for e in list_events(db, jid, after_id=items[2]["id"])] == ["m3", "m4"]
        assert len(list_events(db, jid, limit=2)) == 2
        assert len(list_events(db, jid, limit=0)) == 1  # clamped
        ev = db.get(JobEvent, items[0]["id"])
        assert event_dict(ev) == items[0]


def test_sse_message_format():
    assert sse_message({"a": "x\ny"}, event="job", id=7) == 'id: 7\nevent: job\ndata: {"a":"x\\ny"}\n\n'
    assert sse_message([1]) == "data: [1]\n\n"


def test_stream_emits_events_then_end(make_worker, app_env):
    uid = make_user()
    gate = threading.Event()

    @job_handler("t_sse")
    async def handler(ctx):
        await asyncio.to_thread(gate.wait, 5)
        for i in range(3):
            ctx.log(f"step {i}")
            ctx.progress("work", (i + 1) / 3, f"Step {i + 1}/3")
            await ctx.flush()
            await asyncio.sleep(0.05)
        return {"ok": True}

    jid = add_job("t_sse", user_id=uid)
    make_worker(["t_sse"]).start()

    async def main() -> list[str]:
        stream = stream_job(jid, user_id=uid, **fast())
        task = asyncio.ensure_future(collect(stream))
        await asyncio.sleep(0.1)
        gate.set()
        return await task

    messages = parse(asyncio.run(main()))
    assert messages[0] == {"retry": "5000"}
    events = [m for m in messages if m.get("event") == "job_event"]
    assert [m["data"]["message"] for m in events if m["data"]["level"] == "info"][:3] == ["step 0", "step 1", "step 2"]
    ids = [int(m["id"]) for m in events]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert all(int(m["id"]) == m["data"]["id"] for m in events)
    jobs = [m for m in messages if m.get("event") == "job"]
    assert jobs and jobs[0]["data"]["id"] == jid
    assert messages[-1]["event"] == "end" and messages[-1]["data"]["status"] == "succeeded"
    assert "id" not in messages[-1]
    assert open_stream_counts() == (0, {})


def test_last_event_id_resumes(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)
    add_events(jid, 4)
    update_job(jid, status="succeeded")
    with session_scope() as db:
        second = list_events(db, jid)[1]["id"]
    messages = parse(asyncio.run(collect(stream_job(jid, user_id=uid, last_event_id=second, **fast()))))
    assert [m["data"]["message"] for m in messages if m.get("event") == "job_event"] == ["m2", "m3"]
    assert messages[-1]["event"] == "end"


def test_large_backlog_is_drained_before_end(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)
    add_events(jid, 450)
    update_job(jid, status="failed", error="x")
    messages = parse(asyncio.run(collect(stream_job(jid, user_id=uid, **fast()), limit=1000)))
    assert len([m for m in messages if m.get("event") == "job_event"]) == 450
    assert messages[-1]["event"] == "end" and messages[-1]["data"]["status"] == "failed"


def test_awaiting_review_ends_stream(make_worker):
    uid = make_user()

    @job_handler("t_rev")
    async def handler(ctx):
        raise AwaitingReview("Review the plan", {"plan": 1})

    jid = add_job("t_rev", user_id=uid)
    make_worker(["t_rev"]).start()
    messages = parse(asyncio.run(collect(stream_job(jid, user_id=uid, **fast()))))
    assert messages[-1]["event"] == "end" and messages[-1]["data"]["status"] == "awaiting_review"


def test_access_rules(app_env):
    owner = make_user("owner")
    project_owner = make_user("powner")
    admin = make_user("root", role="admin")
    stranger = make_user("stranger")
    inactive_admin = make_user("old", role="admin", is_active=False)
    pid = make_project(project_owner)
    jid = add_job(user_id=owner, project_id=pid)
    add_events(jid, 1)
    update_job(jid, status="succeeded")

    def run(user_id: int, **kw) -> list[dict]:
        return parse(asyncio.run(collect(stream_job(jid, user_id=user_id, **fast(**kw)))))

    for allowed in (owner, project_owner, admin):
        assert run(allowed)[-1]["event"] == "end"
    for denied in (stranger, inactive_admin):
        assert run(denied) == [{"retry": "5000"}]
    assert run(stranger, can_access=lambda db, job: True)[-1]["event"] == "end"


def test_access_rechecked_periodically(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)
    allowed = {"value": True}

    async def main() -> list[str]:
        stream = stream_job(jid, user_id=uid, can_access=lambda db, job: allowed["value"], access_interval=0.1,
                            heartbeat_interval=0.05, **fast())
        task = asyncio.ensure_future(collect(stream))
        await asyncio.sleep(0.3)
        allowed["value"] = False
        return await task

    messages = parse(asyncio.run(main()))
    assert messages[0] == {"retry": "5000"}
    assert all(m.get("event") != "end" for m in messages)  # closed without `end`


def test_keepalive_comments_and_max_duration(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)
    stream = stream_job(jid, user_id=uid, heartbeat_interval=0.1, max_duration=0.5, **fast())
    messages = parse(asyncio.run(collect(stream)))
    assert any(m.get("comment") == "keepalive" for m in messages)
    assert [m["event"] for m in messages if "event" in m] == ["job"]  # one summary, no end


def test_job_deleted_mid_stream_closes(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)

    async def main() -> list[str]:
        task = asyncio.ensure_future(collect(stream_job(jid, user_id=uid, **fast())))
        await asyncio.sleep(0.15)
        await asyncio.to_thread(_delete_job, jid)
        return await task

    messages = parse(asyncio.run(main()))
    assert messages[-1].get("event") == "job"


def _delete_job(job_id: int) -> None:
    with session_scope() as db:
        db.execute(delete(JobEvent).where(JobEvent.job_id == job_id))
        db.execute(delete(Job).where(Job.id == job_id))


def test_stream_caps(app_env):
    uid, other = make_user("u1"), make_user("u2")
    jid = add_job(user_id=uid)
    settings = app_env.model_copy(update={"sse_max_streams_per_user": 2, "sse_max_streams_total": 3})
    s1 = stream_job(jid, user_id=uid, settings=settings)
    s2 = stream_job(jid, user_id=uid, settings=settings)
    with pytest.raises(TooManyStreams):
        stream_job(jid, user_id=uid, settings=settings)
    s3 = stream_job(jid, user_id=other, settings=settings)
    with pytest.raises(TooManyStreams) as info:
        stream_job(jid, user_id=other, settings=settings)
    assert info.value.retry_after > 0
    assert open_stream_counts() == (3, {uid: 2, other: 1})
    asyncio.run(s1.aclose())
    asyncio.run(s1.aclose())  # idempotent
    s4 = stream_job(jid, user_id=uid, settings=settings)
    for s in (s2, s3, s4):
        asyncio.run(s.aclose())
    assert open_stream_counts() == (0, {})


def test_unstarted_stream_releases_slot_when_dropped_without_gc(app_env):
    """No reference cycle: a stream that never started (client gone before the response began)
    frees its slot by refcount, not at some later cyclic GC pass."""
    uid = make_user()
    jid = add_job(user_id=uid)
    settings = app_env.model_copy(update={"sse_max_streams_per_user": 4})
    gc.disable()
    try:
        for _ in range(4):
            stream = stream_job(jid, user_id=uid, settings=settings)
            assert open_stream_counts()[0] == 1
            del stream
            assert open_stream_counts() == (0, {})
        streams = [stream_job(jid, user_id=uid, settings=settings) for _ in range(4)]
        assert open_stream_counts() == (4, {uid: 4})
        del streams
        assert open_stream_counts() == (0, {})
        fifth = stream_job(jid, user_id=uid, settings=settings)  # no 429
        del fifth
    finally:
        gc.enable()
    assert open_stream_counts() == (0, {})


def test_late_committed_lower_id_is_delivered_once(app_env):
    """Postgres: an event whose (lower) id commits after a higher id was streamed is not skipped."""
    from sqlalchemy import insert

    uid = make_user()
    jid = add_job(user_id=uid)
    other = add_job(user_id=uid)
    add_events(jid, 1)  # id 1
    add_events(other, 1)  # id 2: stands in for an uncommitted id of this job
    add_events(jid, 1)  # id 3

    async def main() -> list[str]:
        stream = stream_job(jid, user_id=uid, gap_window=100, **fast())
        chunks: list[str] = []

        async def run() -> None:
            async for chunk in stream:
                chunks.append(chunk)

        task = asyncio.ensure_future(run())
        await asyncio.sleep(0.2)

        def late_commit() -> None:
            with session_scope() as db:
                db.execute(delete(JobEvent).where(JobEvent.job_id == other))
                db.execute(insert(JobEvent).values(id=2, job_id=jid, message="late", level="info", stage="", data={}))

        await asyncio.to_thread(late_commit)
        await asyncio.sleep(0.2)
        await asyncio.to_thread(update_job, jid, status="succeeded")
        await asyncio.wait_for(task, 5)
        await stream.aclose()
        return chunks

    messages = parse(asyncio.run(main()))
    events = [m for m in messages if m.get("event") == "job_event"]
    assert [m["data"]["id"] for m in events] == [1, 3, 2]
    assert [m["data"]["message"] for m in events][-1] == "late"
    assert [int(m["id"]) for m in events] == [1, 3, 3]  # Last-Event-ID never moves backwards
    assert messages[-1]["event"] == "end"


def test_resumed_stream_does_not_resend_the_window(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)
    add_events(jid, 5)
    with session_scope() as db:
        third = list_events(db, jid)[2]["id"]
    update_job(jid, status="succeeded")
    stream = stream_job(jid, user_id=uid, last_event_id=third, gap_window=100, **fast())
    messages = parse(asyncio.run(collect(stream)))
    assert [m["data"]["message"] for m in messages if m.get("event") == "job_event"] == ["m3", "m4"]


def test_gap_window_defaults_off_on_sqlite(app_env):
    from aadhi.jobs.events import default_gap_window

    assert default_gap_window() == 0  # SQLite has one writer at a time: ids become visible in order


def test_cancelled_consumer_releases_slot(app_env):
    uid = make_user()
    jid = add_job(user_id=uid)

    async def main() -> None:
        stream = stream_job(jid, user_id=uid, **fast())

        async def consume() -> None:
            async for _ in stream:
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
    assert open_stream_counts() == (0, {})
