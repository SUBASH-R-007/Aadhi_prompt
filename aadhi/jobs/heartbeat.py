"""ONE heartbeat thread per process for every lease held by this process.

* Every ``heartbeat_interval`` (10 s) it runs a fenced ``UPDATE jobs SET heartbeat_at=now ...
  RETURNING cancel_requested`` per lease (one transaction) — a lost lease marks the context.
* Every ``flag_interval`` (2 s) it refreshes cancel flags / lease ownership with one cheap SELECT,
  so cancellation is noticed quickly without writing.

Workers share the service through ``acquire()`` / ``release()`` (reference counted); contexts
``register`` while their job runs. The thread never touches a job's event loop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Protocol

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Job, utcnow
from .lease import Lease
from .queue import touch

log = logging.getLogger(__name__)


class LeaseHolder(Protocol):
    """What the heartbeat thread needs from a running job's context."""

    @property
    def lease(self) -> Lease: ...

    def apply_heartbeat(self, cancel_requested: bool | None) -> None: ...


class HeartbeatService:
    """Process-wide lease heartbeats (see module docstring)."""

    def __init__(self, heartbeat_interval: float = 10.0, flag_interval: float = 2.0) -> None:
        self.heartbeat_interval = heartbeat_interval
        self.flag_interval = flag_interval
        self._lock = threading.Lock()
        self._holders: dict[int, LeaseHolder] = {}
        self._users = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # --- registration ----------------------------------------------------------------------------
    def register(self, holder: LeaseHolder) -> None:
        with self._lock:
            self._holders[id(holder)] = holder

    def unregister(self, holder: LeaseHolder) -> None:
        with self._lock:
            self._holders.pop(id(holder), None)

    def holders(self) -> list[LeaseHolder]:
        with self._lock:
            return list(self._holders.values())

    # --- thread lifecycle ------------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def acquire(self, *, heartbeat_interval: float | None = None, flag_interval: float | None = None) -> None:
        """Start the thread on first use; the smallest requested intervals win."""
        with self._lock:
            self._users += 1
            if heartbeat_interval is not None:
                self.heartbeat_interval = (
                    min(self.heartbeat_interval, heartbeat_interval) if self._users > 1 else heartbeat_interval
                )
            if flag_interval is not None:
                self.flag_interval = min(self.flag_interval, flag_interval) if self._users > 1 else flag_interval
            if self._thread is None or not self._thread.is_alive():
                self._stop = threading.Event()
                self._thread = threading.Thread(
                    target=self._run, args=(self._stop,), name="aadhi-heartbeat", daemon=True
                )
                self._thread.start()

    def release(self, timeout: float = 5.0) -> None:
        """Drop one user; the thread stops when nobody uses it."""
        with self._lock:
            self._users = max(0, self._users - 1)
            if self._users:
                return
            thread, stop = self._thread, self._stop
            self._thread = None
        stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _run(self, stop: threading.Event) -> None:
        now = time.monotonic()
        next_beat = now + self.heartbeat_interval
        next_flags = now + self.flag_interval
        while not stop.is_set():
            stop.wait(max(0.01, min(next_beat, next_flags) - time.monotonic()))
            if stop.is_set():
                break
            now = time.monotonic()
            try:
                if now >= next_beat:
                    self.beat_once()
                    next_beat = now + self.heartbeat_interval
                    next_flags = now + self.flag_interval
                elif now >= next_flags:
                    self.refresh_flags_once()
                    next_flags = now + self.flag_interval
            except Exception as exc:  # noqa: BLE001 - never let the heartbeat thread die
                log.warning("heartbeat failed: %s", get_settings().redact(str(exc)))
                next_flags = now + self.flag_interval
                next_beat = min(next_beat, now + self.flag_interval)

    # --- work --------------------------------------------------------------------------------------------
    def beat_once(self) -> int:
        """Fenced heartbeat for every registered lease (one transaction). Returns leases kept."""
        holders = self.holders()
        if not holders:
            return 0
        now = utcnow()
        results: list[tuple[LeaseHolder, bool | None]] = []
        with session_scope() as db:
            for holder in holders:
                results.append((holder, touch(db, holder.lease, now=now)))
        kept = 0
        for holder, flag in results:
            # Jobs stay registered until their outcome is recorded (the beat keeps the lease fresh
            # while the worker retries that write); a closed context ignores the result.
            if self._still_registered(holder):
                holder.apply_heartbeat(flag)
            kept += flag is not None
        return kept

    def _still_registered(self, holder: LeaseHolder) -> bool:
        with self._lock:
            return self._holders.get(id(holder)) is holder

    def refresh_flags_once(self) -> None:
        """Read ownership + cancel flags of every registered lease (one SELECT, no writes)."""
        holders = self.holders()
        if not holders:
            return
        ids = sorted({h.lease.job_id for h in holders})
        with session_scope() as db:
            rows = db.execute(
                select(Job.id, Job.locked_by, Job.attempts, Job.status, Job.cancel_requested).where(Job.id.in_(ids))
            ).all()
        by_id = {r[0]: r for r in rows}
        for holder in holders:
            if not self._still_registered(holder):
                continue
            lease = holder.lease
            row = by_id.get(lease.job_id)
            owned = row is not None and row[1] == lease.worker_id and row[2] == lease.attempt and row[3] == "running"
            holder.apply_heartbeat(bool(row[4]) if owned and row is not None else None)


_SERVICE = HeartbeatService()


def heartbeat_service() -> HeartbeatService:
    """The process-wide heartbeat service."""
    return _SERVICE
