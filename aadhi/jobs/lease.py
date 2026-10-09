"""Lease identity and fencing.

A worker thread is identified by ``host:pid:boot_uuid:thread``. ``boot_uuid`` is random per
process, so a restarted process on the same host (even with a recycled pid) can recognise leases
held by its dead predecessor. ``Job.attempts`` is the fencing token: every job-owned write adds
``WHERE id=:id AND locked_by=:me AND attempts=:attempt AND status='running'`` (see ``fence``).
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from dataclasses import dataclass
from typing import Any, NamedTuple

from ..models import Job

BOOT_ID = uuid.uuid4().hex[:12]
HOSTNAME = (socket.gethostname() or "localhost").replace(":", "_")[:64]
MAX_WORKER_ID_LEN = 160


class WorkerIdParts(NamedTuple):
    host: str
    pid: int
    boot_id: str
    thread: str


@dataclass(frozen=True)
class Lease:
    """The (job, owner, fencing token) triple every job-owned write must match."""

    job_id: int
    worker_id: str
    attempt: int


def make_worker_id(thread_name: str | None = None) -> str:
    """``host:pid:boot_uuid:thread`` for the calling (or named) thread."""
    name = (thread_name or threading.current_thread().name).replace(":", "_")
    return f"{HOSTNAME}:{os.getpid()}:{BOOT_ID}:{name}"[:MAX_WORKER_ID_LEN]


def parse_worker_id(worker_id: str | None) -> WorkerIdParts | None:
    """Split a worker id; ``None`` when it does not have the expected shape."""
    if not worker_id:
        return None
    parts = worker_id.split(":", 3)
    if len(parts) != 4:
        return None
    host, pid, boot, thread = parts
    try:
        return WorkerIdParts(host, int(pid), boot, thread)
    except ValueError:
        return None


def fence(stmt: Any, lease: Lease) -> Any:
    """Add the lease-fencing predicate to an ``update(Job)``/``select`` statement."""
    return stmt.where(
        Job.id == lease.job_id,
        Job.locked_by == lease.worker_id,
        Job.attempts == lease.attempt,
        Job.status == "running",
    )
