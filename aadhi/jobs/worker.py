"""Job worker: N threads, each running claimed jobs in a fresh event loop (``runner.run_job_loop``).

* Claims with ``queue.claim_next`` (one atomic statement) for the worker's kinds that have a
  registered handler; idle threads poll every ``JOB_POLL_INTERVAL_SECONDS``. Handler modules are
  imported one by one, so a broken module only disables its own kinds.
* Leases are kept alive by the process-wide heartbeat thread (``aadhi.jobs.heartbeat``) from claim
  until the outcome is recorded; the heartbeat interval is clamped to ``JOB_STALE_SECONDS / 3``.
* A handler runs as the owner of the paid media it generates (``storage.assets.claim_owner``): its
  cross-process generation claims name the job's lease, so they die with it. Its provider calls
  report retries and slow answers to the job log (``providers._notify``, ``ctx.provider_notice``).
* A maintenance thread recovers jobs abandoned by a previous process on this host (at start),
  runs the stale-lease reaper every 30 s and (optionally) schedules the daily ``cleanup`` job.
  With ``host_housekeeping`` (the worker entry points) it also logs at start whether this host can
  render MP4s (when it claims ``render_video``) and, at start and hourly, removes this host's
  leftovers: Manim temp dirs / expired sandbox containers (``manim.sandbox.sweep_orphans``, when it
  claims a version-building kind) and stale render workspaces (``compose.video.sweep_render_workspaces``).
* Cancellation is cooperative (``ctx.check_cancelled``); if a handler ignores a cancel / lost lease
  / shutdown for ``cancel_grace_seconds`` its task is cancelled. Thread work it was awaiting
  (``asyncio.to_thread``) is abandoned, not awaited, so the outcome is recorded at once.
* Outcome mapping: success -> succeeded; JobCancelled -> cancelled (lease_lost: nothing — the new
  owner owns the job; shutdown: released, attempt refunded — only when this worker really is
  stopping); AwaitingReview -> awaiting_review; BudgetExceeded -> failed/budget; FatalJobError ->
  failed/<code>; anything else (including SystemExit and other BaseExceptions, and a CancelledError
  nobody asked for) -> retry with exponential backoff while attempts remain, else failed (redacted
  traceback tail stored in a JobEvent). A pending user cancel always wins over a retry. A provider
  refusing the job owner's *personal* API key (HTTP 401/403, possibly wrapped in a FatalJobError)
  fails the job at once with ``error_code='personal_key_rejected'`` and a message saying so: retrying
  cannot help and the job never falls back to the server key.
* Outcomes are written with retries (exponential backoff up to ``JOB_STALE_SECONDS / 2``) while the
  lease keeps heartbeating; a result the database refuses fails the job with
  ``error_code='result_not_persisted'`` instead of leaving it running.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ..config import Settings, get_settings
from ..credentials import personal_key_rejection
from ..db import session_scope
from ..models import VERSION_MUTATING_KINDS
from ..providers._notify import provider_notices
from ..storage.assets import claim_owner
from . import base
from .base import AwaitingReview, BudgetExceeded, FatalJobError, JobCancelled, JobHandler, RetryableJobError
from .context import DBJobContext
from .heartbeat import heartbeat_service
from .lease import Lease, make_worker_id
from .queue import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_CAP_SECONDS,
    cancel_running,
    claim_next,
    fail_job,
    finish_job,
    has_claimable,
    mark_awaiting_review,
    reap_stale,
    recover_own_host,
    release_job,
    requeue_job,
    retry_delay_seconds,
)
from .runner import run_job_loop
from .util import MAX_MESSAGE_CHARS, is_transient_db_error, jsonable, redact_text, short_error, traceback_tail

log = logging.getLogger(__name__)
T = TypeVar("T")

MIN_SAFE_STALE_SECONDS = 30
HOUSEKEEPING_INTERVAL_SECONDS = 3600.0
RESULT_NOT_PERSISTED = "result_not_persisted"
UNEXPECTED_CANCEL = "unexpected_cancel"
PERSONAL_KEY_REJECTED = "personal_key_rejected"


@dataclass
class _Outcome:
    kind: str  # succeeded | cancelled | awaiting_review | failed | retry | retry_if_left | release | lost
    result: dict[str, Any] | None = None
    error: str = ""
    error_code: str = "error"
    traceback: str | None = None
    message: str = ""
    state: dict[str, Any] = field(default_factory=dict)


def import_handler_modules(modules: Sequence[str] | None = None) -> list[str]:
    """Import every handler module on its own (``base.HANDLER_MODULES`` by default) so one broken
    module cannot hide the handlers of the others. Returns the modules that failed to import."""
    failed: list[str] = []
    for name in base.HANDLER_MODULES if modules is None else modules:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:  # not part of this install (partial deployments / development)
                log.info("job handler module %s is not installed", name)
                continue
            failed.append(name)
            log.error("importing job handler module %s failed: %s", name, traceback_tail(exc, 1500))
        except Exception as exc:  # noqa: BLE001 - keep loading the other modules
            failed.append(name)
            log.error("importing job handler module %s failed: %s", name, traceback_tail(exc, 1500))
    return failed


class Worker:
    """A pool of job threads plus a maintenance thread (see module docstring)."""

    def __init__(
        self,
        settings: Settings | None = None,
        kinds: Sequence[str] | None = None,
        concurrency: int | None = None,
        *,
        name: str = "worker",
        poll_interval: float | None = None,
        heartbeat_interval: float = 10.0,
        flag_interval: float = 2.0,
        reaper_interval: float = 30.0,
        flush_interval: float = 1.0,
        cancel_grace_seconds: float = 10.0,
        retry_backoff_base: float = DEFAULT_BACKOFF_BASE_SECONDS,
        retry_backoff_cap: float = DEFAULT_BACKOFF_CAP_SECONDS,
        persist_timeout: float | None = None,
        run_reaper: bool = True,
        recover_on_start: bool = True,
        schedule_cleanup: bool = False,
        cleanup_check_interval: float = 3600.0,
        host_housekeeping: bool = False,
        housekeeping_interval: float = HOUSEKEEPING_INTERVAL_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.name = name
        self.requested_kinds = list(kinds) if kinds is not None else list(self.settings.worker_kinds)
        self.concurrency = max(1, int(concurrency if concurrency is not None else self.settings.worker_concurrency))
        self.poll_interval = max(
            0.01, float(poll_interval if poll_interval is not None else self.settings.job_poll_interval_seconds)
        )
        stale = float(self.settings.job_stale_seconds)
        if stale < MIN_SAFE_STALE_SECONDS:
            log.warning(
                "JOB_STALE_SECONDS=%s is below %ss: slow heartbeats (e.g. a busy database) may get live jobs "
                "requeued and run twice",
                self.settings.job_stale_seconds,
                MIN_SAFE_STALE_SECONDS,
            )
        # The reaper requeues jobs without a heartbeat for JOB_STALE_SECONDS: beat at least 3x per window.
        self.heartbeat_interval = max(0.05, min(float(heartbeat_interval), stale / 3))
        self.flag_interval = min(float(flag_interval), self.heartbeat_interval)
        self.reaper_interval = reaper_interval
        self.flush_interval = flush_interval
        self.cancel_grace_seconds = cancel_grace_seconds
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_cap = retry_backoff_cap
        self.persist_timeout = float(persist_timeout) if persist_timeout is not None else max(5.0, stale / 2)
        self.run_reaper = run_reaper
        self.recover_on_start = recover_on_start
        self.schedule_cleanup = schedule_cleanup
        self.cleanup_check_interval = cleanup_check_interval
        self.host_housekeeping = host_housekeeping
        self.housekeeping_interval = housekeeping_interval

        self.kinds: list[str] = []
        self._handlers: dict[str, JobHandler] = {}
        self._threads: list[threading.Thread] = []
        self._maintenance: threading.Thread | None = None
        self._stopping = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._active: dict[int, DBJobContext] = {}
        self._started = False
        self._heartbeat_acquired = False

    # --- lifecycle ---------------------------------------------------------------------------------
    def resolve_kinds(self) -> list[str]:
        """Requested kinds that have a registered handler (others stay queued for another worker)."""
        import_handler_modules()
        registry = dict(base._REGISTRY)
        missing = [k for k in self.requested_kinds if k not in registry]
        if missing:
            log.warning("worker %s: no handler registered for kinds %s (not claimed)", self.name, missing)
        self._handlers = {k: registry[k] for k in self.requested_kinds if k in registry}
        return [k for k in dict.fromkeys(self.requested_kinds) if k in registry]

    def start(self) -> None:
        """Start job threads, the maintenance thread and (shared) heartbeats. Idempotent."""
        with self._lock:
            if self._started:
                return
            self._started = True
        self._stopping.clear()
        self.kinds = self.resolve_kinds()
        heartbeat_service().acquire(heartbeat_interval=self.heartbeat_interval, flag_interval=self.flag_interval)
        self._heartbeat_acquired = True
        self._maintenance = threading.Thread(
            target=self._maintenance_main, name=f"aadhi-{self.name}-maint", daemon=True
        )
        self._maintenance.start()
        for i in range(self.concurrency):
            t = threading.Thread(target=self._thread_main, args=(i,), name=f"aadhi-{self.name}-{i}", daemon=True)
            self._threads.append(t)
            t.start()
        log.info("worker %s started: %d threads, kinds=%s", self.name, self.concurrency, self.kinds)

    @property
    def is_running(self) -> bool:
        return self._started and not self._stopping.is_set()

    def notify(self) -> None:
        """Wake idle threads (e.g. right after enqueueing) instead of waiting for the next poll."""
        self._wake.set()

    def active_job_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._active)

    def stop(self, timeout: float = 30.0) -> bool:
        """Graceful stop: no new claims; running jobs get ``timeout`` seconds to finish, then are
        asked to stop and released to another worker. Returns True if every thread exited."""
        if not self._started:
            return True
        self._stopping.set()
        self._wake.set()
        deadline = time.monotonic() + max(0.0, timeout)
        for t in self._threads:
            t.join(max(0.0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self._threads):
            with self._lock:
                contexts = list(self._active.values())
            for ctx in contexts:
                log.warning("worker %s: job %s still running at shutdown; releasing it", self.name, ctx.job_id)
                ctx.request_shutdown()
            grace = deadline + self.cancel_grace_seconds + 5.0
            for t in self._threads:
                t.join(max(0.0, grace - time.monotonic()))
        if self._maintenance is not None:
            self._maintenance.join(5.0)
        alive = [t.name for t in self._threads if t.is_alive()]
        if self._heartbeat_acquired:
            heartbeat_service().release()
            self._heartbeat_acquired = False
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()]
            self._started = bool(alive)
        if alive:
            log.error("worker %s: threads did not stop: %s", self.name, alive)
        else:
            log.info("worker %s stopped", self.name)
        return not alive

    def run_until_idle(self, *, idle_checks: int = 2, timeout: float | None = None) -> bool:
        """Start (if needed), process until nothing is claimable and nothing runs, then stop.

        Returns True when the queue drained before ``timeout``.
        """
        self.start()
        deadline = None if timeout is None else time.monotonic() + timeout
        idle = 0
        drained = False
        try:
            while deadline is None or time.monotonic() < deadline:
                time.sleep(max(self.poll_interval, 0.05))
                with session_scope() as db:
                    claimable = has_claimable(db, self.kinds)
                if not claimable and not self.active_job_ids():
                    idle += 1
                    if idle >= idle_checks:
                        drained = True
                        break
                else:
                    idle = 0
        finally:
            self.stop()
        return drained

    # --- job threads ---------------------------------------------------------------------------------
    def _thread_main(self, slot: int) -> None:
        worker_id = make_worker_id(threading.current_thread().name)
        while not self._stopping.is_set():
            try:
                claimed = self._claim(worker_id)
            except Exception as exc:  # noqa: BLE001
                log.warning("worker %s: claim failed: %s", self.name, self.settings.redact(str(exc)))
                claimed = None
            if claimed is None:
                self._wake.wait(self.poll_interval)
                self._wake.clear()
                continue
            job_id, attempt = claimed
            try:
                self.process(Lease(job_id, worker_id, attempt))
            except Exception:  # pragma: no cover - process() handles its own errors
                log.exception("worker %s: unexpected error processing job %s", self.name, job_id)

    def _claim(self, worker_id: str) -> tuple[int, int] | None:
        if not self.kinds:
            return None
        with session_scope() as db:
            return claim_next(db, worker_id, self.kinds)

    def process(self, lease: Lease) -> None:
        """Run one claimed job to an outcome and persist it (fenced). Blocking."""
        try:
            ctx = DBJobContext.open(lease, self.settings, flush_interval=self.flush_interval)
        except JobCancelled:
            log.warning("job %s: lease lost before start", lease.job_id)
            return
        except Exception as exc:  # noqa: BLE001 - mapped to retry (transient) or failure (permanent)
            self._persist(lease, self._open_failure(lease, exc), ctx=None)
            return
        if self._stopping.is_set():
            self._persist(lease, _Outcome("release"), ctx=None)
            return
        handler = self._handlers.get(ctx.kind)
        if handler is None:
            self._persist(
                lease,
                _Outcome("failed", error=f"No handler for job kind {ctx.kind!r}", error_code="no_handler"),
                ctx=None,
            )
            return
        with self._lock:
            self._active[lease.job_id] = ctx
        hb = heartbeat_service()
        hb.register(ctx)  # heartbeats continue until the outcome is recorded
        outcome: _Outcome | None = None
        try:
            ctx.start()
            outcome = self._execute(handler, ctx)
        except Exception as exc:  # noqa: BLE001 - e.g. the writer thread could not start
            outcome = self._error_outcome(ctx, exc)
        finally:
            ctx.close()
            try:
                if outcome is not None:
                    self._persist(lease, outcome, ctx=ctx)
            finally:
                hb.unregister(ctx)
                ctx.finalize()
                with self._lock:
                    self._active.pop(lease.job_id, None)

    def _open_failure(self, lease: Lease, exc: Exception) -> _Outcome:
        tb = traceback_tail(exc)
        log.error("job %s: could not load its context: %s", lease.job_id, tb)
        if is_transient_db_error(exc):
            return _Outcome("retry_if_left", error=short_error(exc), error_code="error", traceback=tb)
        return _Outcome("failed", error=short_error(exc), error_code="invalid_job", traceback=tb)

    # --- running a handler ---------------------------------------------------------------------------
    def _execute(self, handler: JobHandler, ctx: DBJobContext) -> _Outcome:
        try:
            result = run_job_loop(
                self._run_handler(handler, ctx),
                name=f"aadhi-job-{ctx.job_id}",
                cleanup_timeout=max(0.5, min(self.cancel_grace_seconds, 10.0)),
            )
        except JobCancelled as exc:
            return self._cancel_outcome(ctx, exc.reason, str(exc))
        except asyncio.CancelledError:
            if ctx.cancel_reason is None:
                # e.g. a library cancelled its own inner task and let the CancelledError escape
                return self._error_outcome(
                    ctx, RuntimeError("the job's task was cancelled unexpectedly"), code=UNEXPECTED_CANCEL
                )
            return self._cancel_outcome(ctx, ctx.cancel_reason)
        except AwaitingReview as exc:
            state = jsonable(exc.state)
            return _Outcome(
                "awaiting_review", state=state if isinstance(state, dict) else {"value": state}, message=str(exc)
            )
        except BudgetExceeded as exc:
            return _Outcome("failed", error=str(exc) or "Budget exceeded", error_code="budget")
        except FatalJobError as exc:
            rejected = personal_key_rejection(exc, ctx.key_sources)
            if rejected:
                return _Outcome("failed", error=rejected, error_code=PERSONAL_KEY_REJECTED)
            # Scrub with the job's own resolved keys: the shared registry may already have dropped a
            # personal key the user replaced or deleted while this job ran.
            error = redact_text(str(exc) or "Job failed", MAX_MESSAGE_CHARS, ctx.settings.redact)
            return _Outcome("failed", error=error, error_code=exc.code or "failed")
        except Exception as exc:  # noqa: BLE001
            return self._error_outcome(ctx, exc)
        except BaseException as exc:  # SystemExit, KeyboardInterrupt, GeneratorExit raised by handler code
            if self._stopping.is_set() or ctx.cancel_reason == "shutdown":
                log.warning("job %s: %s while the worker is stopping; releasing it", ctx.job_id, type(exc).__name__)
                return _Outcome("release")
            log.error("job %s: handler raised %s; treated as a failure", ctx.job_id, type(exc).__name__)
            return self._error_outcome(ctx, exc)
        return self._success_outcome(result)

    @staticmethod
    def _success_outcome(result: Any) -> _Outcome:
        if result is not None and not isinstance(result, dict):
            result = {"value": result}
        try:
            value = jsonable(result) if result is not None else None
        except Exception as exc:  # noqa: BLE001 - e.g. nesting deeper than the recursion limit
            return _Outcome(
                "failed",
                error="The job finished but its result could not be saved.",
                error_code=RESULT_NOT_PERSISTED,
                traceback=traceback_tail(exc),
            )
        return _Outcome("succeeded", result=value)

    def _cancel_outcome(self, ctx: DBJobContext, reason: str, message: str = "") -> _Outcome:
        if ctx.lease_lost:
            return _Outcome("lost")
        if ctx.cancel_requested:  # the user's cancel wins over every other stop reason
            return _Outcome("cancelled")
        effective = ctx.cancel_reason or reason
        if effective == "shutdown":
            if ctx.cancel_reason == "shutdown" or self._stopping.is_set():
                return _Outcome("release")
            # A handler claimed a shutdown that is not happening: never refund attempts for that.
            return self._error_outcome(
                ctx, RuntimeError(f"the handler stopped for a shutdown that was not requested ({message})"),
                code=UNEXPECTED_CANCEL,
            )
        if effective == "lease_lost":
            # Raised by the handler itself (e.g. its own compare-and-set lost a race) while our job
            # lease still looked valid: the fenced final flush/transition decides who owns the job.
            return _Outcome("cancelled", error_code="lease_lost", error=message or "lease lost")
        return _Outcome("cancelled")

    def _error_outcome(self, ctx: DBJobContext, exc: BaseException, *, code: str | None = None) -> _Outcome:
        if ctx.lease_lost:
            return _Outcome("lost")
        if ctx.budget_error:  # a provider wrapped our BudgetExceeded: retrying cannot help
            return _Outcome("failed", error=ctx.budget_error, error_code="budget")
        if ctx.cancel_requested:
            return _Outcome("cancelled")
        redact = ctx.settings.redact  # the job's own resolved keys are always scrubbed
        rejected = personal_key_rejection(exc, ctx.key_sources)
        if rejected:  # the user's own key was refused: retrying cannot help
            return _Outcome(
                "failed", error=rejected, error_code=PERSONAL_KEY_REJECTED, traceback=traceback_tail(exc, redact=redact)
            )
        tb = traceback_tail(exc, redact=redact)
        error = short_error(exc, redact=redact)
        if ctx.attempt < ctx.max_attempts:
            return _Outcome("retry", error=error, error_code=code or "retry", traceback=tb)
        final = code or ("retries_exhausted" if isinstance(exc, RetryableJobError) else "error")
        return _Outcome("failed", error=error, error_code=final, traceback=tb)

    async def _run_handler(self, handler: JobHandler, ctx: DBJobContext) -> Any:
        ctx.check_cancelled()  # seeded from the row: a cancelled job never starts work
        # Paid media the handler generates is claimed across processes under this job's lease, and
        # provider retries / slow calls are reported in its log (the task copies the context, so
        # both stay set for all of its work).
        owner = getattr(ctx, "claim_owner", None)
        notice = getattr(ctx, "provider_notice", None)
        with claim_owner(owner() if callable(owner) else None), provider_notices(notice if callable(notice) else None):
            task = asyncio.ensure_future(handler(ctx))
        watcher = asyncio.ensure_future(self._watch(ctx, task))
        try:
            return await task
        except asyncio.CancelledError:
            if task.cancelled() and ctx.cancel_reason is not None:
                raise JobCancelled("stopped", reason=ctx.cancel_reason) from None
            raise
        finally:
            watcher.cancel()

    async def _watch(self, ctx: DBJobContext, task: asyncio.Future[Any]) -> None:
        """Cancel the handler task if it ignores a cancel/lease-loss/shutdown for the grace period."""
        loop = asyncio.get_running_loop()
        noticed: float | None = None
        tick = min(0.2, max(0.02, self.cancel_grace_seconds / 4))
        while not task.done():
            await asyncio.sleep(tick)
            if ctx.cancel_reason is None:
                noticed = None
                continue
            if noticed is None:
                noticed = loop.time()
            elif loop.time() - noticed >= self.cancel_grace_seconds:
                log.warning("job %s: handler ignored %s; cancelling its task", ctx.job_id, ctx.cancel_reason)
                task.cancel()
                return

    # --- persisting outcomes -------------------------------------------------------------------------
    def _persist(self, lease: Lease, outcome: _Outcome, *, ctx: DBJobContext | None) -> None:
        deadline = time.monotonic() + self.persist_timeout
        if ctx is not None:
            try:
                owned = self._retry_db(ctx.flush_sync, deadline=deadline)
            except Exception as exc:  # noqa: BLE001 - buffers are lost; still record the outcome
                log.error("job %s: final flush failed: %s", lease.job_id, self.settings.redact(str(exc)))
                owned = not ctx.lease_lost
            if not owned:
                log.warning("job %s: lease lost; leaving the job to its new owner", lease.job_id)
                return
        if outcome.kind == "lost":
            log.warning("job %s: lease lost; leaving the job to its new owner", lease.job_id)
            return
        candidates = [outcome, self._fallback(outcome)]
        for i, current in enumerate(candidates):

            def write(current: _Outcome = current) -> bool:
                with session_scope() as db:
                    return self._transition(db, lease, current)

            try:
                ok = self._retry_db(write, deadline=deadline)
            except Exception as exc:  # noqa: BLE001
                error = self.settings.redact(f"{type(exc).__name__}: {exc}")[:500]
                if i + 1 < len(candidates) and not is_transient_db_error(exc):
                    log.error("job %s: could not store outcome %s (%s); storing a simpler one", lease.job_id,
                              current.kind, error)
                    continue
                log.error("job %s: could not record outcome %s: %s (the reaper will recover it)", lease.job_id,
                          current.kind, error)
                return
            if ok:
                log.info("job %s: %s", lease.job_id, current.kind)
            else:
                log.warning("job %s: lease lost before recording outcome %s", lease.job_id, current.kind)
            return

    @staticmethod
    def _fallback(outcome: _Outcome) -> _Outcome:
        """A simpler outcome for when the database refuses the real one (e.g. an unstorable result)."""
        if outcome.kind in ("succeeded", "awaiting_review"):
            return _Outcome(
                "failed", error="The job finished but its result could not be saved.", error_code=RESULT_NOT_PERSISTED
            )
        return dataclasses.replace(
            outcome,
            error="The job failed; the details could not be saved." if outcome.error else "",
            traceback=None,
            message="",
        )

    def _transition(self, db: Any, lease: Lease, outcome: _Outcome) -> bool:
        if outcome.kind == "succeeded":
            return finish_job(db, lease, outcome.result)
        if outcome.kind == "cancelled":
            if outcome.error_code == "lease_lost":
                return cancel_running(db, lease, error_code="lease_lost", error=outcome.error)
            return cancel_running(db, lease)
        if outcome.kind == "awaiting_review":
            return mark_awaiting_review(db, lease, state=outcome.state, message=outcome.message)
        if outcome.kind == "release":
            return release_job(db, lease)
        if outcome.kind in ("retry", "retry_if_left"):
            delay = retry_delay_seconds(lease.attempt, base=self.retry_backoff_base, cap=self.retry_backoff_cap)
            requeued = requeue_job(
                db,
                lease,
                delay_seconds=delay,
                error=outcome.error,
                error_code=outcome.error_code,
                traceback_text=outcome.traceback,
                only_if_attempts_left=outcome.kind == "retry_if_left",
            )
            if requeued or outcome.kind == "retry":
                return requeued
        return fail_job(db, lease, error=outcome.error, error_code=outcome.error_code, traceback_text=outcome.traceback)

    def _retry_db(self, fn: Callable[[], T], *, deadline: float) -> T:
        """Call ``fn``; retry transient (connection-level / unknown) errors with exponential backoff
        until ``deadline`` (monotonic). Data errors are raised at once."""
        delay = 0.1
        while True:
            try:
                return fn()
            except Exception as exc:
                remaining = deadline - time.monotonic()
                if not is_transient_db_error(exc) or remaining <= 0:
                    raise
                log.warning(
                    "worker %s: database write failed (%s); retrying in %.1fs",
                    self.name,
                    self.settings.redact(f"{type(exc).__name__}: {exc}")[:300],
                    min(delay, remaining),
                )
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 5.0)

    # --- maintenance ---------------------------------------------------------------------------------
    def _maintenance_main(self) -> None:
        if self.recover_on_start:
            self._safe("restart recovery", self.recover_once)
        next_reap = time.monotonic() + (self.reaper_interval if self.run_reaper else float("inf"))
        next_cleanup = time.monotonic() if self.schedule_cleanup else float("inf")
        next_housekeeping = time.monotonic() if self.host_housekeeping else float("inf")
        if self.run_reaper:
            self._safe("reaper", self.reap_once)
        if self.host_housekeeping:
            self._safe("capability check", self.log_capabilities)
        while not self._stopping.is_set():
            wait = max(0.01, min(next_reap, next_cleanup, next_housekeeping) - time.monotonic())
            if self._stopping.wait(min(wait, 3600.0)):
                break
            now = time.monotonic()
            if now >= next_reap:
                self._safe("reaper", self.reap_once)
                next_reap = now + self.reaper_interval
            if now >= next_cleanup:
                self._safe("cleanup scheduling", self.schedule_cleanup_once)
                next_cleanup = now + self.cleanup_check_interval
            if now >= next_housekeeping:
                self._safe("host housekeeping", self.sweep_host_once)
                next_housekeeping = time.monotonic() + self.housekeeping_interval

    def _safe(self, what: str, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            log.warning("worker %s: %s failed: %s", self.name, what, self.settings.redact(str(exc)))

    def recover_once(self) -> dict[str, list[int]]:
        """Recover jobs left running by a dead process on this host (different boot uuid)."""
        with session_scope() as db:
            out = recover_own_host(db)
        if any(out.values()):
            self.notify()
        return out

    def reap_once(self) -> dict[str, list[int]]:
        """Requeue/fail jobs whose lease went stale (no heartbeat for JOB_STALE_SECONDS)."""
        with session_scope() as db:
            out = reap_stale(db, stale_seconds=self.settings.job_stale_seconds)
        if any(out.values()):
            self.notify()
        return out

    def log_capabilities(self) -> None:
        """Say at start whether this host can do the work it claims (MP4 rendering needs ffmpeg with
        libx264/aac and Playwright's Chromium); a render on a host that cannot fails ``render_unavailable``."""
        if "render_video" not in self.kinds:
            return
        from ..compose.capabilities import render_capabilities

        caps = render_capabilities(self.settings, browser=True)
        if caps.ok:
            log.info("worker %s: MP4 rendering available (caption burn-in: %s)", self.name,
                     "yes" if caps.burn_captions else "no, ffmpeg has no libass")
        else:
            log.warning("worker %s claims render_video but cannot render MP4s here: %s", self.name,
                        " ".join(caps.reasons))

    def sweep_host_once(self) -> dict[str, int]:
        """Remove this host's leftovers of the kinds it runs (idempotent, safe with many workers):
        Manim temp dirs of dead processes and sandbox containers past their deadline, and render
        workspaces that can no longer be resumed. Returns counts."""
        out: dict[str, int] = {}
        if any(k in self.kinds for k in VERSION_MUTATING_KINDS):
            from ..manim.sandbox import sweep_orphans

            out.update({f"manim_{k}": v for k, v in sweep_orphans(self.settings).items()})
        if "render_video" in self.kinds:
            from ..compose.video import sweep_render_workspaces

            out["render_workspaces"] = sweep_render_workspaces(self.settings, session_scope)
        if any(out.values()):
            log.info("worker %s: removed leftovers %s", self.name, out)
        return out

    def schedule_cleanup_once(self) -> int | None:
        """Enqueue the daily ``cleanup`` job if this worker runs it and none ran recently."""
        if "cleanup" not in self.kinds:
            return None
        from .cleanup import ensure_cleanup_scheduled

        with session_scope() as db:
            job = ensure_cleanup_scheduled(db)
            return None if job is None else job.id
