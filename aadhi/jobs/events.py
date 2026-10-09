"""JobEvent serialisation and the SSE job stream (``GET /api/jobs/{id}/stream``).

Stream protocol (docs/API.md): ``retry: 5000`` first; ``event: job_event`` (JobEvent JSON, with an
``id:`` line = event id so ``Last-Event-ID`` resumes); ``event: job`` (JobSummary whenever it
changes); ``event: end`` (final JobSummary) when the job is terminal or awaiting review, then
close. A ``: keepalive`` comment is sent after 15 s of silence. Each poll (every 0.5 s) opens a
short DB session in a worker thread — the stream never holds a request-scoped session.

``stream_job`` reserves a stream slot *when called* and raises ``TooManyStreams`` (API: 429)
before any byte is sent; the slot is released when the stream ends, is closed, or is dropped
(no reference cycle: an unstarted stream frees its slot by refcount, without waiting for the GC).
On databases with concurrent writers (Postgres) the stream also re-checks a window of ids behind its
cursor, so an event whose lower id committed late is still delivered (exactly once). Access is re-checked on the first poll and every 30 s (job owner, project owner or
admin by default; pass ``can_access`` to override). Streams stop after 30 minutes without an
``end`` event so the browser's EventSource reconnects (and re-authenticates).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_sessionmaker
from ..models import Job, JobEvent, Project, User
from .queue import STREAM_END_STATUSES, job_summary
from .util import iso

log = logging.getLogger(__name__)

AccessCheck = Callable[[Session, Job], bool]


class TooManyStreams(Exception):
    """Per-user or global SSE stream cap reached (API: 429 rate_limited + Retry-After)."""

    def __init__(self, message: str = "too many open job streams", *, retry_after: int = 5) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# --- serialisation -------------------------------------------------------------------------------


def event_dict(ev: JobEvent) -> dict[str, Any]:
    """``JobEvent`` (docs/API.md) as a JSON-ready dict."""
    return {
        "id": ev.id,
        "job_id": ev.job_id,
        "created_at": iso(ev.created_at),
        "level": ev.level,
        "stage": ev.stage or "",
        "message": ev.message or "",
        "progress": ev.progress,
        "data": ev.data or {},
    }


def list_events(db: Session, job_id: int, after_id: int = 0, limit: int = 200) -> list[dict[str, Any]]:
    """Events of a job with ``id > after_id`` in id order (``limit`` clamped to 1..1000)."""
    limit = max(1, min(int(limit), 1000))
    rows = db.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.id > int(after_id or 0))
        .order_by(JobEvent.id)
        .limit(limit)
    ).scalars()
    return [event_dict(ev) for ev in rows]


def sse_message(data: Any, *, event: str | None = None, id: int | None = None) -> str:
    """Format one SSE message (JSON data never contains raw newlines)."""
    lines = []
    if id is not None:
        lines.append(f"id: {int(id)}")
    if event:
        lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(data, separators=(",", ":"), ensure_ascii=False, default=str))
    return "\n".join(lines) + "\n\n"


def default_can_access(db: Session, job: Job, user_id: int) -> bool:
    """Job owner, project owner, or an active admin."""
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return False
    if user.role == "admin" or job.user_id == user_id:
        return True
    if job.project_id is not None:
        project = db.get(Project, job.project_id)
        return project is not None and project.owner_id == user_id and project.deleted_at is None
    return False


# --- process-wide stream caps ------------------------------------------------------------------------


class _StreamSlots:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_user: dict[int, int] = {}
        self._total = 0

    def acquire(self, user_id: int, settings: Settings) -> None:
        with self._lock:
            if self._total >= settings.sse_max_streams_total:
                raise TooManyStreams("the server has too many open job streams")
            if self._per_user.get(user_id, 0) >= settings.sse_max_streams_per_user:
                raise TooManyStreams("too many open job streams for this user")
            self._total += 1
            self._per_user[user_id] = self._per_user.get(user_id, 0) + 1

    def release(self, user_id: int) -> None:
        with self._lock:
            n = self._per_user.get(user_id, 0) - 1
            if n > 0:
                self._per_user[user_id] = n
            else:
                self._per_user.pop(user_id, None)
            self._total = max(0, self._total - 1)

    def counts(self) -> tuple[int, dict[int, int]]:
        with self._lock:
            return self._total, dict(self._per_user)


_SLOTS = _StreamSlots()


def open_stream_counts() -> tuple[int, dict[int, int]]:
    """(total open streams, per-user counts) in this process."""
    return _SLOTS.counts()


class _Slot:
    """Idempotent release of one reserved stream slot."""

    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        _SLOTS.release(self.user_id)


# --- the stream ---------------------------------------------------------------------------------------

DEFAULT_GAP_WINDOW = 1000  # event ids re-checked behind the cursor (databases with concurrent writers)


@dataclass
class _Poll:
    exists: bool
    allowed: bool = True
    summary: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    late: list[dict[str, Any]] = field(default_factory=list)
    window_ids: list[int] | None = None


@dataclass(frozen=True)
class _GapCheck:
    """Re-check ``(floor, after]`` for events that committed after a higher id was streamed.

    ``known`` = ids already streamed (or present when a resumed stream started); ``None`` asks for
    the window ids only (priming a stream resumed with ``Last-Event-ID``)."""

    floor: int
    known: frozenset[int] | None


def _poll(
    job_id: int, after_id: int, limit: int, check_access: bool, access: AccessCheck, gap: _GapCheck | None = None
) -> _Poll:
    # Job first, then events: a terminal status read before the events query guarantees every
    # event written before the job finished is included (workers flush before finalising).
    with get_sessionmaker()() as db:
        job = db.get(Job, job_id)
        if job is None:
            return _Poll(exists=False)
        if check_access and not access(db, job):
            return _Poll(exists=True, allowed=False)
        summary = job_summary(job)
        events = list_events(db, job_id, after_id, limit)
        late: list[dict[str, Any]] = []
        window_ids: list[int] | None = None
        if gap is not None:
            ids = list(
                db.execute(
                    select(JobEvent.id).where(
                        JobEvent.job_id == job_id, JobEvent.id > gap.floor, JobEvent.id <= after_id
                    )
                ).scalars()
            )
            if gap.known is None:
                window_ids = ids
            else:
                missing = [i for i in ids if i not in gap.known]
                if missing:
                    rows = db.execute(select(JobEvent).where(JobEvent.id.in_(missing)).order_by(JobEvent.id)).scalars()
                    late = [event_dict(ev) for ev in rows]
    return _Poll(exists=True, summary=summary, events=events, late=late, window_ids=window_ids)


class _Cursor:
    """Stream position: ``after`` (highest id streamed) plus the recently streamed ids, so events
    whose lower id committed late (Postgres assigns serial ids at INSERT, visibility comes at
    COMMIT; the writer thread and API transactions write concurrently) are still delivered once."""

    def __init__(self, last_event_id: int, window: int) -> None:
        self.after = max(0, int(last_event_id or 0))
        self.window = max(0, int(window))
        self._known: set[int] = set()
        self._primed = self.after == 0  # a resumed stream first learns what the client already has

    def gap_check(self) -> _GapCheck | None:
        if self.window <= 0 or self.after <= 0:
            return None
        floor = max(0, self.after - self.window)
        return _GapCheck(floor, frozenset(self._known) if self._primed else None)

    def accept(self, result: _Poll) -> list[tuple[dict[str, Any], int]]:
        """Events to send, each with the SSE ``id`` to use (late events repeat the current cursor so
        the browser's Last-Event-ID never moves backwards)."""
        if result.window_ids is not None:
            self._known.update(result.window_ids)
            self._primed = True
        out: list[tuple[dict[str, Any], int]] = []
        for ev in result.late:
            self._known.add(int(ev["id"]))
            out.append((ev, self.after))
        for ev in result.events:
            ev_id = int(ev["id"])
            self._known.add(ev_id)
            self.after = max(self.after, ev_id)
            out.append((ev, ev_id))
        if self.window > 0:
            floor = self.after - self.window
            self._known = {i for i in self._known if i > floor}
        else:
            self._known.clear()
        return out


@dataclass(frozen=True)
class _StreamOptions:
    last_event_id: int
    access: AccessCheck
    poll_interval: float
    heartbeat_interval: float
    max_duration: float
    access_interval: float
    batch: int
    gap_window: int


async def _stream_body(job_id: int, slot: _Slot, opts: _StreamOptions) -> AsyncIterator[str]:
    """The SSE chunks of one stream. Module-level on purpose: it must not reference the
    ``JobStream`` wrapper, so dropping an unstarted stream frees it (and its slot) by refcount."""
    try:
        yield "retry: 5000\n\n"
        started = time.monotonic()
        last_sent = started
        next_access = started  # check on the first poll
        last_summary: dict[str, Any] | None = None
        cursor = _Cursor(opts.last_event_id, opts.gap_window)
        while True:
            now = time.monotonic()
            if now - started >= opts.max_duration:
                return  # no `end`: the EventSource reconnects with Last-Event-ID
            check = now >= next_access
            if check:
                next_access = now + opts.access_interval
            result = await asyncio.to_thread(
                _poll, job_id, cursor.after, opts.batch, check, opts.access, cursor.gap_check()
            )
            if not result.exists or not result.allowed:
                return
            for ev, sse_id in cursor.accept(result):
                yield sse_message(ev, event="job_event", id=sse_id)
                last_sent = time.monotonic()
            summary = result.summary
            assert summary is not None
            if summary != last_summary:
                last_summary = summary
                if summary["status"] not in STREAM_END_STATUSES:
                    yield sse_message(summary, event="job")
                    last_sent = time.monotonic()
            if summary["status"] in STREAM_END_STATUSES and len(result.events) < opts.batch:
                yield sse_message(summary, event="end")
                return
            if len(result.events) >= opts.batch:
                continue  # drain the backlog without sleeping
            if time.monotonic() - last_sent >= opts.heartbeat_interval:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(opts.poll_interval)
    finally:
        slot.release()


class JobStream:
    """Async iterator of SSE chunks for one job (returned by ``stream_job``).

    Holds no reference cycle: when it is dropped without being iterated (e.g. the client vanished
    before the response started) its slot is released immediately by ``weakref.finalize``.
    """

    def __init__(
        self,
        job_id: int,
        *,
        last_event_id: int,
        user_id: int,
        access: AccessCheck,
        slot: _Slot,
        poll_interval: float,
        heartbeat_interval: float,
        max_duration: float,
        access_interval: float,
        batch: int = 200,
        gap_window: int = 0,
    ) -> None:
        self.job_id = job_id
        self.user_id = user_id
        self._slot = slot
        self._finalizer = weakref.finalize(self, slot.release)
        options = _StreamOptions(
            last_event_id=max(0, int(last_event_id or 0)),
            access=access,
            poll_interval=poll_interval,
            heartbeat_interval=heartbeat_interval,
            max_duration=max_duration,
            access_interval=access_interval,
            batch=batch,
            gap_window=gap_window,
        )
        self._gen = _stream_body(job_id, slot, options)

    def __aiter__(self) -> JobStream:
        return self

    async def __anext__(self) -> str:
        return await self._gen.__anext__()

    async def aclose(self) -> None:
        """Stop the stream and release its slot."""
        try:
            await self._gen.aclose()
        finally:
            self._slot.release()


def default_gap_window() -> int:
    """0 on SQLite (one writer at a time: ids become visible in order), else ``DEFAULT_GAP_WINDOW``."""
    try:
        dialect = get_sessionmaker().kw["bind"].dialect.name
    except Exception:  # noqa: BLE001 - unknown setup: be safe
        return DEFAULT_GAP_WINDOW
    return 0 if dialect == "sqlite" else DEFAULT_GAP_WINDOW


def stream_job(
    job_id: int,
    *,
    last_event_id: int = 0,
    user_id: int,
    can_access: AccessCheck | None = None,
    settings: Settings | None = None,
    poll_interval: float = 0.5,
    heartbeat_interval: float = 15.0,
    max_duration: float = 1800.0,
    access_interval: float = 30.0,
    gap_window: int | None = None,
) -> JobStream:
    """Reserve a stream slot (raises ``TooManyStreams``) and return the SSE chunk iterator.

    ``can_access(db, job) -> bool`` overrides the default rule (job owner, project owner, admin).
    ``gap_window`` (event ids re-checked behind the cursor) defaults to ``default_gap_window()``.
    Use it as ``StreamingResponse(stream_job(...), media_type="text/event-stream")``.
    """
    settings = settings or get_settings()
    window = default_gap_window() if gap_window is None else max(0, int(gap_window))
    _SLOTS.acquire(user_id, settings)
    slot = _Slot(user_id)

    def default(db: Session, job: Job) -> bool:
        return default_can_access(db, job, user_id)

    try:
        return JobStream(
            job_id,
            last_event_id=last_event_id,
            user_id=user_id,
            access=can_access or default,
            slot=slot,
            poll_interval=poll_interval,
            heartbeat_interval=heartbeat_interval,
            max_duration=max_duration,
            access_interval=access_interval,
            gap_window=window,
        )
    except BaseException:
        slot.release()
        raise
