"""``DBJobContext``: the non-blocking, lease-fenced ``JobContext`` used by real workers.

* ``progress`` / ``log`` / ``record_usage`` only touch in-memory buffers (a ``threading.Lock``,
  never the DB) and are safe from any thread or event loop. Everything is sanitised when it is
  buffered (redacted, strict JSON without NaN/inf, no NUL characters), so a value the database
  would reject never enters the buffers.
* A writer thread flushes the buffers at most ``flush_interval`` seconds after they change,
  immediately on a stage change, and on ``await ctx.flush()``. Every flush is ONE transaction
  whose first statement is the fenced ``UPDATE jobs ... WHERE id AND locked_by AND attempts AND
  status='running' RETURNING cancel_requested``; if it matches nothing the lease was lost and
  ``check_cancelled`` raises ``JobCancelled(reason='lease_lost')``.
* Poison protection: when the batched flush fails it is retried once (connection errors), then
  written row by row (the fenced job update first, then each event / usage row in its own
  savepoint). Rows the database still rejects are dropped and counted — a failing batch is never
  put back as a whole, so one bad row cannot block later progress, events or usage.
* Usage rows are billing facts, not job-owned state: after a lease loss the job-owned buffers
  (progress, events, cost delta) are discarded, but usage already incurred stays buffered and is
  inserted unfenced (attributed to this job) so the user's daily spend stays correct.
* The cancel flag is seeded from the job row, refreshed by flushes and by the heartbeat thread.
* Budgets are enforced in memory: job cost seeded from ``Job.cost_usd`` (earlier attempts) and the
  user's spend today seeded at claim time.
* API keys: ``open`` resolves the job owner's keys (``aadhi.credentials.resolve_keys``: personal key >
  server key saved in the Studio > environment) and runs the job with those settings
  (``ctx.settings``, ``ctx.key_sources``). Usage paid with a personal key is stored with
  ``billed_to='user'``: it counts toward the job budget but not toward the user's daily budget, and
  neither it nor free usage ever trips the daily limit (only a paid, server-billed increment does).
  Events are redacted with ``ctx.settings.redact``, so the job's own resolved keys are always scrubbed.
* Provider notices (``provider_notice``: retries, rate-limit waits, "still waiting" for a slow AI
  service) become job events at the current stage, throttled by ``NoticeThrottle``; the worker
  installs the method as the job task's notice sink (``aadhi.providers._notify``).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import functools
import logging
import math
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session, undefer

from ..config import Settings, get_settings
from ..credentials import KEY_PROVIDER_LABELS, billed_to, resolve_keys
from ..db import session_scope
from ..models import Job, JobEvent, UsageEvent, User, utcnow
from ..providers._notify import NoticeThrottle, ProviderNotice
from ..providers.base import Usage
from ..storage.assets import AssetStore, ClaimOwner
from ..storage.base import Storage
from .base import BudgetExceeded, JobCancelled
from .lease import Lease, fence
from .util import (
    MAX_MESSAGE_CHARS,
    clean_text,
    finite_or_none,
    is_connection_error,
    jsonable,
    redact_obj,
    redact_text,
    truncate,
)

log = logging.getLogger(__name__)

MAX_PENDING_EVENTS = 1000
_LEVEL_ALIASES = {"warn": "warning", "debug": "info", "critical": "error", "fatal": "error", "exception": "error"}
_LOG_LEVELS = ("info", "warning", "error")
_EPS = 1e-9
_MAX_INT = 2**31 - 1


# --- optional usage module (owned by another area; degrade gracefully while absent) -------------


@functools.cache
def _usage_api() -> SimpleNamespace | None:
    try:
        from ..usage import pricing, service  # type: ignore[import-not-found,unused-ignore]
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == "aadhi.usage" or exc.name.startswith("aadhi.usage.")):
            log.info("aadhi.usage is not available: usage is priced at $0 and recorded directly")
            return None
        raise
    return SimpleNamespace(pricing=pricing, service=service)


def price_usage(usage: Usage, settings: Settings) -> float:
    """Estimated USD cost of ``usage`` (0.0 when pricing is unavailable or fails)."""
    api = _usage_api()
    if api is None:
        return 0.0
    try:
        cost = float(api.pricing.estimate_cost(usage, settings))
    except Exception as exc:  # noqa: BLE001 - pricing must never break a job
        log.warning("pricing failed for %s/%s: %s", usage.provider, usage.model, settings.redact(str(exc)))
        return 0.0
    return cost if math.isfinite(cost) and cost > 0 else 0.0


def spent_today(db: Session, user_id: int) -> float:
    """User spend today billed to the server (``aadhi.usage.service.spent_today``; UTC-day fallback)."""
    api = _usage_api()
    if api is not None:
        return float(api.service.spent_today(db, user_id))
    start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    total = db.execute(
        select(func.coalesce(func.sum(UsageEvent.cost_usd), 0.0)).where(
            UsageEvent.user_id == user_id, UsageEvent.created_at >= start, UsageEvent.billed_to == "server"
        )
    ).scalar_one()
    return float(total or 0.0)


def user_daily_budget(user: User, settings: Settings) -> float:
    """User daily budget (``aadhi.usage.service.user_daily_budget``; settings fallback)."""
    api = _usage_api()
    if api is not None:
        return float(api.service.user_daily_budget(user, settings))
    return float(user.daily_budget_usd if user.daily_budget_usd is not None else settings.daily_budget_usd_per_user)


def _count(value: Any) -> int:
    number = finite_or_none(value)
    return 0 if number is None else max(0, min(_MAX_INT, int(number)))


def sanitize_usage(usage: Usage, redact: Callable[[str], str] | None = None) -> Usage:
    """The copy of ``usage`` that is buffered and stored: redacted strict-JSON ``meta``, NUL-free
    bounded strings, non-negative finite numbers (a NaN would be rejected by Postgres JSONB/floats)."""
    fn = redact or get_settings().redact
    try:
        meta = redact_obj(jsonable(usage.meta or {}), fn)
    except Exception:  # noqa: BLE001 - pathological meta (e.g. too deep): keep the billing fact
        meta = {}
    if not isinstance(meta, dict):
        meta = {"value": meta}
    seconds = finite_or_none(usage.seconds)
    return Usage(
        provider=truncate(clean_text(str(usage.provider or "")), 32) or "unknown",
        model=truncate(clean_text(str(usage.model or "")), 128),
        operation=truncate(clean_text(str(usage.operation or "")), 32) or "unknown",
        input_tokens=_count(usage.input_tokens),
        output_tokens=_count(usage.output_tokens),
        characters=_count(usage.characters),
        seconds=max(0.0, seconds or 0.0),
        units=_count(usage.units),
        meta=meta,
    )


def _insert_usage(
    db: Session,
    usage: Usage,
    cost: float,
    *,
    user_id: int | None,
    project_id: int | None,
    job_id: int,
    billed: str = "server",
) -> None:
    """Insert one UsageEvent via ``aadhi.usage.service`` (direct insert while it is absent)."""
    api = _usage_api()
    if api is not None:
        api.service.record_usage(
            db, usage, user_id=user_id, project_id=project_id, job_id=job_id, cost_usd=cost, billed_to=billed
        )
        return
    _insert_usage_direct(db, usage, cost, user_id=user_id, project_id=project_id, job_id=job_id, billed=billed)


def _insert_usage_direct(
    db: Session,
    usage: Usage,
    cost: float,
    *,
    user_id: int | None,
    project_id: int | None,
    job_id: int,
    with_meta: bool = True,
    billed: str = "server",
) -> None:
    db.execute(
        insert(UsageEvent).values(
            user_id=user_id,
            project_id=project_id,
            job_id=job_id,
            provider=usage.provider or "unknown",
            model=usage.model or "",
            operation=usage.operation or "unknown",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            characters=usage.characters,
            seconds=usage.seconds,
            units=usage.units,
            cost_usd=cost,
            billed_to=billed,
            meta=usage.meta if with_meta else {},
            created_at=utcnow(),
        )
    )


# --- shared asset store ----------------------------------------------------------------------------

def shared_asset_store() -> AssetStore:
    """One ``AssetStore`` per process (its in-flight de-duplication only works when shared)."""
    from ..storage import get_asset_store

    return get_asset_store()


# --- buffers -----------------------------------------------------------------------------------------


class _LeaseLost(Exception):
    pass


# A buffered usage row: (sanitised usage, cost in USD, billed_to "server" | "user").
_UsageRow = tuple[Usage, float, str]


@dataclass
class _Snapshot:
    progress: tuple[str, float, str] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    usage: list[_UsageRow] = field(default_factory=list)
    cost_delta: float = 0.0
    dropped: int = 0

    @property
    def empty(self) -> bool:
        return self.progress is None and not self.events and not self.usage and not self.cost_delta and not self.dropped


class DBJobContext:
    """Implements ``aadhi.jobs.base.JobContext`` for a claimed job (see module docstring)."""

    def __init__(
        self,
        *,
        lease: Lease,
        kind: str,
        settings: Settings | None = None,
        payload: dict[str, Any] | None = None,
        user_id: int | None = None,
        project_id: int | None = None,
        version_id: int | None = None,
        max_attempts: int = 1,
        job_cost_usd: float = 0.0,
        user_spent_usd: float = 0.0,
        user_budget_usd: float | None = None,
        cancel_requested: bool = False,
        storage: Storage | None = None,
        assets: AssetStore | None = None,
        flush_interval: float = 1.0,
        key_sources: dict[str, str | None] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # provider -> "personal" | "server" | "env" | None (aadhi.credentials.resolve_keys for the job owner)
        self.key_sources: dict[str, str | None] = dict(key_sources or {})
        self._lease = lease
        self.job_id = lease.job_id
        self.attempt = lease.attempt
        self.worker_id = lease.worker_id
        self.kind = kind
        self.user_id = user_id
        self.project_id = project_id
        self.version_id = version_id
        self.payload: dict[str, Any] = dict(payload or {})
        self.max_attempts = max_attempts
        self.assets = assets if assets is not None else shared_asset_store()
        self.storage = storage if storage is not None else self.assets.storage
        raw_budget = self.payload.get("budget_usd")
        self.job_budget_usd = float(self.settings.max_cost_per_lecture_usd if raw_budget is None else raw_budget)
        self.user_budget_usd = user_budget_usd
        self._flush_interval = max(0.01, float(flush_interval))

        self._lock = threading.Lock()  # guards buffers + flags (never held during I/O)
        self._flush_lock = threading.Lock()  # one flush at a time
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None

        self._stage = ""
        self._fraction = 0.0
        self._message = ""
        self._progress_dirty = False
        self._pending_progress: dict[str, Any] | None = None
        self._events: list[dict[str, Any]] = []
        self._dropped = 0
        self._usage: list[_UsageRow] = []
        self._cost_delta = 0.0
        self._job_cost = float(job_cost_usd or 0.0)
        self._user_spent = float(user_spent_usd or 0.0)

        self._cancel_requested = bool(cancel_requested)
        self._lease_lost = False
        self._shutdown = False
        self._finalized = False
        self._late_flush_running = False
        self.budget_error: str | None = None
        self.rows_rejected = 0  # event/usage rows the database refused (dropped, never retried)
        self.notice_throttle = NoticeThrottle()

    # --- construction ----------------------------------------------------------------------------
    @classmethod
    def open(cls, lease: Lease, settings: Settings | None = None, **kwargs: Any) -> DBJobContext:
        """Load a claimed job, resolve its owner's API keys and seed budgets and the cancel flag
        (blocking: call from a worker thread).

        Raises ``JobCancelled(reason='lease_lost')`` if the job is no longer owned by ``lease``.
        """
        settings = settings or get_settings()
        with session_scope() as db:
            job = db.execute(fence(select(Job), lease).options(undefer(Job.payload))).scalar_one_or_none()
            if job is None:
                raise JobCancelled("lease lost before start", reason="lease_lost")
            user = db.get(User, job.user_id) if job.user_id is not None else None
            spent = spent_today(db, user.id) if user is not None else 0.0
            budget = user_daily_budget(user, settings) if user is not None else None
            keys = resolve_keys(db, settings, user.id if user is not None else None)
            ctx = cls(
                lease=lease,
                kind=job.kind,
                settings=keys.settings,
                payload=dict(job.payload or {}),
                user_id=job.user_id,
                project_id=job.project_id,
                version_id=job.version_id,
                max_attempts=job.max_attempts,
                job_cost_usd=job.cost_usd,
                user_spent_usd=spent,
                user_budget_usd=budget,
                cancel_requested=bool(job.cancel_requested),
                key_sources=keys.sources,
                **kwargs,
            )
        personal = sorted(p for p, source in keys.sources.items() if source == "personal")
        if personal:
            labels = ", ".join(KEY_PROVIDER_LABELS[p] for p in personal)
            ctx.log(f"Using your own API key for: {labels}.", personal_keys=personal)
        return ctx

    # --- lifecycle ---------------------------------------------------------------------------------
    @property
    def lease(self) -> Lease:
        return self._lease

    def start(self) -> None:
        """Start the writer thread (idempotent)."""
        if self._writer is not None:
            return
        self._writer = threading.Thread(target=self._writer_main, name=f"aadhi-job-{self.job_id}-writer", daemon=True)
        self._writer.start()

    def close(self, timeout: float = 5.0) -> None:
        """Stop the writer thread. Buffered data is kept for a final ``flush_sync()``."""
        self._stop.set()
        self._wake.set()
        writer = self._writer
        if writer is not None and writer is not threading.current_thread():
            writer.join(timeout)

    def finalize(self) -> None:
        """Called by the worker once the job's outcome is recorded. Usage reported later — by
        thread work the job abandoned on cancel/shutdown (see ``runner``) — is still written, by a
        short-lived background thread (the job row is final, so it goes in unfenced)."""
        self._finalized = True
        if self.has_pending():
            self._spawn_late_flush()

    def _spawn_late_flush(self) -> None:
        with self._lock:
            if self._late_flush_running:
                return
            self._late_flush_running = True
        threading.Thread(target=self._late_flush_main, name=f"aadhi-job-{self.job_id}-late", daemon=True).start()

    def _late_flush_main(self) -> None:
        flushed = False
        try:
            for attempt in range(5):
                try:
                    self.flush_sync()
                    flushed = True
                    break
                except Exception as exc:  # noqa: BLE001 - retried a few times, then given up
                    log.warning("job %s: late usage flush failed: %s", self.job_id, self.settings.redact(str(exc)))
                    time.sleep(min(30.0, 0.5 * 2**attempt))
        finally:
            with self._lock:
                self._late_flush_running = False
        if flushed and self.has_pending():  # recorded while we were writing
            self._spawn_late_flush()

    def _writer_main(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._flush_interval)
            self._wake.clear()
            if self._stop.is_set():
                break
            if not self.has_pending():
                continue
            try:
                self.flush_sync()
            except Exception as exc:  # noqa: BLE001 - keep (sanitised) buffers; retry on the next tick
                log.warning("job %s: flush failed: %s", self.job_id, self.settings.redact(str(exc)))

    # --- flags -----------------------------------------------------------------------------------------
    @property
    def lease_lost(self) -> bool:
        return self._lease_lost

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested

    @property
    def cancel_reason(self) -> str | None:
        """``lease_lost`` > ``cancelled`` > ``shutdown`` > None."""
        if self._lease_lost:
            return "lease_lost"
        if self._cancel_requested:
            return "cancelled"
        if self._shutdown:
            return "shutdown"
        return None

    def apply_heartbeat(self, cancel_requested: bool | None) -> None:
        """Heartbeat result: ``None`` = lease lost, otherwise the DB ``cancel_requested`` flag.

        Ignored once the context is closed: the job may already be finalised by this worker (the
        final flush and transition are fenced on their own).
        """
        if self._stop.is_set():
            return
        if cancel_requested is None:
            self.mark_lease_lost()
        elif cancel_requested:
            self._cancel_requested = True

    def mark_lease_lost(self) -> None:
        """Another worker owns the job now: drop job-owned buffers (progress, events, cost delta) and
        stop writing them. Buffered usage is kept: it is inserted unfenced by the next flush."""
        with self._lock:
            if not self._lease_lost and not self._finalized:
                log.warning("job %s: lease lost (worker %s, attempt %s)", self.job_id, self.worker_id, self.attempt)
            self._lease_lost = True
            self._discard_locked()
        self._wake.set()

    def request_shutdown(self) -> None:
        """Ask the handler to stop so the job can be released to another worker."""
        self._shutdown = True

    # --- JobContext protocol ---------------------------------------------------------------------------
    def session(self) -> AbstractContextManager[Session]:
        """Short transactional session (``aadhi.db.session_scope``). Blocking."""
        return session_scope()

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        """Monotonic, clamped overall progress; stage changes are flushed immediately."""
        if self._lease_lost:
            return
        stage = truncate(clean_text(str(stage or "")), 64)
        value = finite_or_none(fraction)
        value = min(1.0, max(0.0, value or 0.0))
        text = redact_text(message, MAX_MESSAGE_CHARS, self.settings.redact) if message else ""
        now = utcnow()
        with self._lock:
            value = max(self._fraction, value)
            stage_changed = stage != self._stage
            message_changed = bool(text) and text != self._message
            if stage_changed or text:
                self._message = text
            if not (stage_changed or message_changed or value != self._fraction):
                return
            self._stage, self._fraction = stage, value
            self._progress_dirty = True
            event = {
                "level": "progress",
                "stage": stage,
                "message": self._message or stage,
                "progress": value,
                "data": {},
                "created_at": now,
            }
            if stage_changed:
                self._pending_progress = None
                self._append_locked(event)
            elif message_changed:
                self._replace_pending_locked(event)
        if stage_changed:
            self._wake.set()

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        """Buffer a JobEvent (redacted, strict JSON; ``level`` info|warning|error)."""
        if self._lease_lost:
            return
        lvl = _LEVEL_ALIASES.get(str(level).lower(), str(level).lower())
        if lvl not in _LOG_LEVELS:
            lvl = "info"
        event = {
            "level": lvl,
            "stage": self._stage,
            "message": redact_text(message, redact=self.settings.redact),
            "progress": None,
            "data": _safe_data(data, self.settings.redact),
            "created_at": utcnow(),
        }
        with self._lock:
            self._append_locked(event)

    def provider_notice(self, notice: ProviderNotice) -> None:
        """Record a provider notice (retry, rate-limit wait, still waiting) as a job event at the
        current stage, unless the throttle drops it. Non-blocking, like ``log``."""
        if self._lease_lost:
            return
        kept = self.notice_throttle.admit(notice)
        if kept is not None:
            self.log(kept.message, kept.level, **kept.data())

    def check_cancelled(self) -> None:
        """Raise ``JobCancelled`` (reason ``lease_lost`` | ``cancelled`` | ``shutdown``). No DB hit."""
        reason = self.cancel_reason
        if reason is None:
            return
        messages = {"lease_lost": "lease lost", "cancelled": "cancelled by user", "shutdown": "worker shutting down"}
        raise JobCancelled(messages[reason], reason=reason)

    def record_usage(self, usage: Usage) -> float:
        """Price + buffer a usage event and enforce budgets (thread-safe, non-blocking).

        Usage is buffered even after a lease loss: it was incurred and is billed to the user. Usage
        paid with a personal API key, or costing nothing, is checked against the job budget only: once
        the user's server spend is over their daily budget, their own key (and free voices) still work.
        """
        cost = price_usage(usage, self.settings)
        clean = sanitize_usage(usage, self.settings.redact)
        billed = billed_to(self.key_sources, clean.provider)
        with self._lock:
            self._usage.append((clean, cost, billed))
            if not self._lease_lost:
                self._cost_delta += cost
            self._job_cost += cost
            if billed == "server":  # spend on the user's own key never uses up their daily budget
                self._user_spent += cost
            job_cost, user_spent = self._job_cost, self._user_spent
        if self._finalized:  # abandoned thread work reporting after the job ended
            self._spawn_late_flush()
        problem = self._budget_problem(job_cost, user_spent, check_daily=billed == "server" and cost > 0)
        if problem:
            self.budget_error = problem
            raise BudgetExceeded(problem)
        return cost

    def ensure_budget(self, estimated_cost_usd: float, provider: str | None = None) -> None:
        """Raise ``BudgetExceeded`` if spending ``estimated_cost_usd`` more would exceed a limit.

        ``provider`` is the usage provider that would bill the estimate (e.g. ``"veo"``): when the
        user's personal key pays for it (or it costs nothing), it is checked against the job budget
        only, like ``record_usage``.
        """
        extra = max(0.0, finite_or_none(estimated_cost_usd) or 0.0)
        own_key = provider is not None and billed_to(self.key_sources, provider) == "user"
        with self._lock:
            job_cost, user_spent = self._job_cost + extra, self._user_spent + (0.0 if own_key else extra)
        problem = self._budget_problem(job_cost, user_spent, check_daily=not own_key and extra > 0)
        if problem:
            raise BudgetExceeded(problem)

    async def flush(self) -> None:
        """Persist buffers now; raises ``JobCancelled(reason='lease_lost')`` if the lease is gone."""
        try:
            await asyncio.to_thread(self.flush_sync)
        except Exception:
            if self._lease_lost:
                raise JobCancelled("lease lost", reason="lease_lost") from None
            raise
        if self._lease_lost:
            raise JobCancelled("lease lost", reason="lease_lost")

    # --- extras (not in the protocol) ---------------------------------------------------------------
    def claim_owner(self) -> ClaimOwner:
        """This job's identity for cross-process generation claims (``aadhi.storage.assets``): a claim is
        live while the job runs under this lease; waiting for another holder honours ``check_cancelled``."""
        return ClaimOwner(
            worker_id=self.worker_id, job_id=self.job_id, attempt=self.attempt, check_cancelled=self.check_cancelled
        )

    @property
    def cost_usd(self) -> float:
        """In-memory job cost (all attempts)."""
        with self._lock:
            return self._job_cost

    def assert_lease(self, db: Session) -> None:
        """Fence a caller transaction: run a fenced heartbeat in ``db`` (row stays locked until
        commit) so a following version compare-and-set in the same transaction is lease-fenced.
        Raises ``JobCancelled(reason='lease_lost')``."""
        row = db.execute(
            fence(update(Job), self._lease)
            .values(heartbeat_at=utcnow())
            .returning(Job.cancel_requested)
            .execution_options(synchronize_session=False)
        ).first()
        if row is None:
            self.mark_lease_lost()
            raise JobCancelled("lease lost", reason="lease_lost")
        if row[0]:
            self._cancel_requested = True

    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._progress_dirty or self._events or self._usage or self._cost_delta or self._dropped)

    def flush_sync(self) -> bool:
        """Blocking flush (writer thread / worker). Returns False when the lease was lost (usage
        already incurred is still written, unfenced).

        Raises only when nothing could be written (e.g. the database is unreachable); the buffers
        are then kept — minus any row the database explicitly rejected — for the next flush.
        """
        with self._flush_lock:
            if self._lease_lost:
                self._flush_orphaned_usage()
                return False
            snap = self._take()
            now = utcnow()
            try:
                cancel = self._write_resilient(snap, now)
            except _LeaseLost:
                self._lost_during_flush(snap)
                return False
            if cancel:
                self._cancel_requested = True
            return True

    # --- internals: budgets / buffers ----------------------------------------------------------------
    def _budget_problem(self, job_cost: float, user_spent: float, *, check_daily: bool = True) -> str | None:
        """The exceeded limit, or None. ``check_daily=False`` (an increment on the user's own key, or
        free) checks the job budget only: such spend never uses up, nor is blocked by, the daily budget."""
        if job_cost > self.job_budget_usd + _EPS:
            return f"Job cost ${job_cost:.2f} exceeds the budget of ${self.job_budget_usd:.2f} for this job."
        if check_daily and self.user_budget_usd is not None and user_spent > self.user_budget_usd + _EPS:
            return f"Daily budget of ${self.user_budget_usd:.2f} for this user is exhausted (${user_spent:.2f} spent)."
        return None

    def _append_locked(self, event: dict[str, Any]) -> None:
        if len(self._events) >= MAX_PENDING_EVENTS:
            dropped = self._events.pop(0)
            if dropped is self._pending_progress:
                self._pending_progress = None
            self._dropped += 1
        self._events.append(event)

    def _replace_pending_locked(self, event: dict[str, Any]) -> None:
        pending = self._pending_progress
        if pending is not None:
            for i in range(len(self._events) - 1, -1, -1):
                if self._events[i] is pending:
                    del self._events[i]
                    break
        self._append_locked(event)
        self._pending_progress = event

    def _discard_locked(self) -> None:
        """Drop job-owned buffers (never usage)."""
        self._events.clear()
        self._cost_delta = 0.0
        self._dropped = 0
        self._progress_dirty = False
        self._pending_progress = None

    def _take(self) -> _Snapshot:
        with self._lock:
            snap = _Snapshot(
                progress=(self._stage, self._fraction, self._message) if self._progress_dirty else None,
                events=self._events,
                usage=self._usage,
                cost_delta=self._cost_delta,
                dropped=self._dropped,
            )
            self._events, self._usage = [], []
            self._cost_delta = 0.0
            self._dropped = 0
            self._progress_dirty = False
            self._pending_progress = None
        return snap

    def _restore(self, snap: _Snapshot) -> None:
        """Put an unwritten snapshot back in front of newer data (usage survives a lease loss)."""
        with self._lock:
            self._usage[:0] = snap.usage
            if self._lease_lost:
                return
            self._events[:0] = snap.events
            del self._events[: max(0, len(self._events) - MAX_PENDING_EVENTS)]
            self._cost_delta += snap.cost_delta
            self._dropped += snap.dropped
            if snap.progress is not None:
                self._progress_dirty = True

    # --- internals: writing ----------------------------------------------------------------------------
    def _write_resilient(self, snap: _Snapshot, now: dt.datetime) -> bool:
        """Batched write; on failure retry once (connection errors), then fall back to row by row."""
        try:
            return self._write_batch(snap, now)
        except _LeaseLost:
            raise
        except Exception as exc:  # noqa: BLE001 - classified below
            error: Exception = exc
        if is_connection_error(error):
            try:
                return self._write_batch(snap, now)
            except _LeaseLost:
                raise
            except Exception as exc:  # noqa: BLE001
                error = exc
        log.warning(
            "job %s: batched flush failed (%s); writing rows one by one",
            self.job_id,
            self.settings.redact(f"{type(error).__name__}: {error}")[:300],
        )
        return self._write_rows(snap, now)

    def _write_batch(self, snap: _Snapshot, now: dt.datetime) -> bool:
        with session_scope() as db:
            cancel = self._update_job(db, snap, now)
            rows = self._event_rows(snap, now)
            if rows:
                db.execute(insert(JobEvent), rows)
            for usage, cost, billed in snap.usage:
                _insert_usage(
                    db, usage, cost, user_id=self.user_id, project_id=self.project_id, job_id=self.job_id, billed=billed
                )
        return cancel

    def _write_rows(self, snap: _Snapshot, now: dt.datetime) -> bool:
        """Fenced job update, then every row in its own savepoint; rejected rows are dropped."""
        rows = self._event_rows(snap, now)
        bad_rows: set[int] = set()
        bad_usage: set[int] = set()
        try:
            with session_scope() as db:
                cancel = self._update_job(db, snap, now)
                for i, row in enumerate(rows):
                    if not self._savepoint(db, functools.partial(_insert_event, db, row)):
                        bad_rows.add(i)
                for i, (usage, cost, billed) in enumerate(snap.usage):
                    if not self._insert_usage_row(db, usage, cost, billed):
                        bad_usage.add(i)
                rejected = len(bad_rows) + len(bad_usage)
                if rejected:
                    notice = self._row(
                        now, "warning", f"{rejected} log/usage rows were rejected by the database and dropped"
                    )
                    self._savepoint(db, functools.partial(_insert_event, db, notice))
        except _LeaseLost:
            raise
        except Exception:
            self._restore(_without(snap, bad_rows, bad_usage))
            raise
        if bad_rows or bad_usage:
            self.rows_rejected += len(bad_rows) + len(bad_usage)
        return cancel

    def _update_job(self, db: Session, snap: _Snapshot, now: dt.datetime) -> bool:
        values: dict[str, Any] = {"heartbeat_at": now}
        if snap.progress is not None:
            stage, fraction, message = snap.progress
            values.update(stage=stage, progress=fraction, message=message)
        if snap.cost_delta:
            values["cost_usd"] = Job.cost_usd + snap.cost_delta
        row = db.execute(
            fence(update(Job), self._lease)
            .values(**values)
            .returning(Job.cancel_requested)
            .execution_options(synchronize_session=False)
        ).first()
        if row is None:
            raise _LeaseLost()
        return bool(row[0])

    def _row(self, created_at: dt.datetime, level: str, message: str, **extra: Any) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "created_at": created_at,
            "level": level,
            "stage": extra.get("stage", self._stage),
            "message": message,
            "progress": extra.get("progress"),
            "data": extra.get("data", {}),
        }

    def _event_rows(self, snap: _Snapshot, now: dt.datetime) -> list[dict[str, Any]]:
        """One row per buffered event (same order/index), plus the 'dropped' notice last."""
        rows = [
            self._row(ev["created_at"], ev["level"], ev["message"], stage=ev["stage"], progress=ev["progress"],
                      data=ev["data"])
            for ev in snap.events
        ]
        if snap.dropped:
            rows.append(self._row(now, "warning", f"{snap.dropped} log events were dropped (buffer full)"))
        return rows

    def _savepoint(self, db: Session, write: Callable[[], Any]) -> bool:
        """Run ``write`` in a savepoint. False when the database rejected the row (the outer
        transaction stays usable); connection-level errors propagate (nothing is dropped for them)."""
        try:
            with db.begin_nested():
                write()
            return True
        except Exception as exc:
            if is_connection_error(exc):
                raise
            log.warning(
                "job %s: the database rejected a row; dropping it: %s",
                self.job_id,
                self.settings.redact(f"{type(exc).__name__}: {exc}")[:300],
            )
            return False

    def _insert_usage_row(self, db: Session, usage: Usage, cost: float, billed: str = "server") -> bool:
        """Insert a usage row; if rejected retry without ``meta`` (the billing numbers matter)."""
        ids: dict[str, Any] = {
            "user_id": self.user_id, "project_id": self.project_id, "job_id": self.job_id, "billed": billed
        }
        if self._savepoint(db, lambda: _insert_usage(db, usage, cost, **ids)):
            return True
        return self._savepoint(db, lambda: _insert_usage_direct(db, usage, cost, with_meta=False, **ids))

    def _lost_during_flush(self, snap: _Snapshot) -> None:
        self.mark_lease_lost()
        with self._lock:
            self._usage[:0] = snap.usage
        self._flush_orphaned_usage()

    def _flush_orphaned_usage(self) -> None:
        """Lease lost: insert buffered usage unfenced (never touches the job row). Raises (keeping
        the usage buffered) only when nothing could be written."""
        with self._lock:
            pending, self._usage = self._usage, []
        if not pending:
            return
        bad: set[int] = set()
        try:
            with session_scope() as db:
                for i, (usage, cost, billed) in enumerate(pending):
                    if not self._insert_usage_row(db, usage, cost, billed):
                        bad.add(i)
        except Exception:
            with self._lock:
                self._usage[:0] = [p for i, p in enumerate(pending) if i not in bad]
            raise
        self.rows_rejected += len(bad)
        log.info("job %s: recorded %d usage rows after the lease was lost", self.job_id, len(pending) - len(bad))


def _insert_event(db: Session, row: dict[str, Any]) -> None:
    db.execute(insert(JobEvent).values(**row))


def _without(snap: _Snapshot, bad_rows: set[int], bad_usage: set[int]) -> _Snapshot:
    """``snap`` minus the rows the database rejected (row indexes as produced by ``_event_rows``)."""
    n = len(snap.events)
    return _Snapshot(
        progress=snap.progress,
        events=[ev for i, ev in enumerate(snap.events) if i not in bad_rows],
        usage=[u for i, u in enumerate(snap.usage) if i not in bad_usage],
        cost_delta=snap.cost_delta,
        dropped=0 if n in bad_rows else snap.dropped,
    )


def _safe_data(data: dict[str, Any], redact: Callable[[str], str] | None = None) -> dict[str, Any]:
    try:
        value = jsonable(data) or {}
    except Exception:  # noqa: BLE001 - pathological values (e.g. nesting deeper than the recursion limit)
        value = {"repr": redact_text(repr(data), redact=redact)}
    return redact_obj(value, redact) if isinstance(value, dict) else {"value": redact_obj(value, redact)}
