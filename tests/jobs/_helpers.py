"""Plain helpers for the job-system tests (DB setup, polling)."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from typing import Any


def wait_for(predicate: Callable[[], Any], timeout: float = 10.0, interval: float = 0.02) -> Any:
    """Poll ``predicate`` until truthy; fail the test on timeout."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"condition not met within {timeout}s")
        time.sleep(interval)


def get_job(job_id: int):
    """Fresh Job row (payload/result undeferred)."""
    from sqlalchemy import select
    from sqlalchemy.orm import undefer

    from aadhi.db import get_sessionmaker
    from aadhi.models import Job

    with get_sessionmaker()() as db:
        return db.execute(
            select(Job).where(Job.id == job_id).options(undefer(Job.payload), undefer(Job.result))
        ).scalar_one()


def job_events(job_id: int) -> list[Any]:
    from sqlalchemy import select

    from aadhi.db import get_sessionmaker
    from aadhi.models import JobEvent

    with get_sessionmaker()() as db:
        return list(db.execute(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)).scalars())


def add_job(kind: str = "t_job", **kwargs: Any) -> int:
    """Enqueue + commit; returns the job id."""
    from aadhi.db import session_scope
    from aadhi.jobs.queue import enqueue

    with session_scope() as db:
        return enqueue(db, kind, **kwargs).id


def make_user(username: str = "alice", role: str = "editor", **kwargs: Any) -> int:
    from aadhi.db import session_scope
    from aadhi.models import User

    with session_scope() as db:
        user = User(username=username, password_hash="x", role=role, **kwargs)
        db.add(user)
        db.flush()
        return user.id


def make_project(owner_id: int) -> int:
    from aadhi.db import session_scope
    from aadhi.models import Project

    with session_scope() as db:
        project = Project(owner_id=owner_id, title="P")
        db.add(project)
        db.flush()
        return project.id


def make_version(project_id: int, number: int = 1) -> int:
    from aadhi.db import session_scope
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        version = ProjectVersion(project_id=project_id, number=number)
        db.add(version)
        db.flush()
        return version.id


def update_job(job_id: int, **values: Any) -> None:
    from sqlalchemy import update

    from aadhi.db import session_scope
    from aadhi.models import Job

    with session_scope() as db:
        db.execute(update(Job).where(Job.id == job_id).values(**values))


def ago(seconds: float) -> dt.datetime:
    from aadhi.models import utcnow

    return utcnow() - dt.timedelta(seconds=seconds)
