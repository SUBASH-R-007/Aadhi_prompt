"""Fake aadhi.jobs.events: list + SSE stream with short sessions per poll."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from aadhi.db import get_sessionmaker
from aadhi.models import Job, JobEvent

TERMINAL = {"succeeded", "failed", "cancelled", "awaiting_review"}
POLL_SECONDS = 0.05
_OPEN: dict[int, int] = {}


class TooManyStreams(Exception):
    retry_after = 5


def event_dict(ev: JobEvent) -> dict:
    return {
        "id": ev.id,
        "job_id": ev.job_id,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
        "level": ev.level,
        "stage": ev.stage,
        "message": ev.message,
        "progress": ev.progress,
        "data": ev.data or {},
    }


def list_events(db, job_id: int, after_id: int = 0, limit: int = 200) -> list[dict]:
    rows = db.execute(
        select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > after_id).order_by(JobEvent.id).limit(limit)
    ).scalars()
    return [event_dict(e) for e in rows]


def _poll(job_id: int, after: int):
    from aadhi.jobs.queue import job_summary

    with get_sessionmaker()() as db:
        events = list_events(db, job_id, after)
        job = db.get(Job, job_id)
        return events, (job_summary(job) if job else None)


def stream_job(job_id: int, *, last_event_id: int = 0, user_id: int, settings=None, **_tuning):
    from aadhi.config import get_settings

    settings = settings or get_settings()
    if _OPEN.get(user_id, 0) >= settings.sse_max_streams_per_user:
        raise TooManyStreams()
    _OPEN[user_id] = _OPEN.get(user_id, 0) + 1
    return _stream(job_id, last_event_id, user_id)


async def _stream(job_id: int, last_event_id: int, user_id: int):
    try:
        async for chunk in _chunks(job_id, last_event_id):
            yield chunk
    finally:
        _OPEN[user_id] = max(0, _OPEN.get(user_id, 1) - 1)


async def _chunks(job_id: int, last_event_id: int):
    yield "retry: 5000\n\n"
    last = last_event_id
    previous = None
    while True:
        events, summary = await asyncio.to_thread(_poll, job_id, last)
        for ev in events:
            last = ev["id"]
            yield f"id: {ev['id']}\nevent: job_event\ndata: {json.dumps(ev)}\n\n"
        if summary is None:
            yield "event: end\ndata: null\n\n"
            return
        if summary != previous:
            previous = summary
            yield f"event: job\ndata: {json.dumps(summary)}\n\n"
        if summary["status"] in TERMINAL:
            yield f"event: end\ndata: {json.dumps(summary)}\n\n"
            return
        await asyncio.sleep(POLL_SECONDS)
