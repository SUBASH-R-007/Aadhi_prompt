"""Job system contract.

* Jobs are rows in ``jobs`` (durable, survive restarts, visible to every replica). Workers
  (``python -m aadhi.worker`` or inline threads when ``WORKER_MODE=inline``) claim queued jobs
  of their ``WORKER_KINDS`` with ONE atomic statement:
  ``UPDATE jobs SET status='running', locked_by=:me, locked_at=:now, heartbeat_at=:now,
  attempts=attempts+1, started_at=coalesce(started_at,:now) WHERE id=(SELECT id FROM jobs WHERE
  status='queued' AND run_after<=:now AND kind IN (...) ORDER BY priority, id LIMIT 1
  [FOR UPDATE SKIP LOCKED on Postgres]) AND status='queued' RETURNING id, attempts``.
* Lease + fencing: ``locked_by`` = ``host:pid:boot_uuid:thread`` and ``attempts`` is the fencing
  token. Every job-owned write (heartbeat, progress, events, usage cost, finish, version
  compare-and-set) includes ``WHERE id=:id AND locked_by=:me AND attempts=:attempt``; if it
  matches no row the lease was lost and ``JobContext`` raises ``JobCancelled("lease lost")``.
  Heartbeats run on a dedicated OS thread (not the job's event loop). The reaper requeues
  running jobs with ``coalesce(heartbeat_at, locked_at) < now - JOB_STALE_SECONDS`` (or fails
  them when attempts are exhausted). A starting worker requeues jobs locked by its own host with a
  different boot uuid.
* ``JobContext`` methods never block the event loop: ``progress``/``log``/``record_usage`` append
  to an in-memory buffer flushed by a writer thread (<= 1 s, and immediately on stage change /
  finish). ``check_cancelled`` reads a flag refreshed by the heartbeat thread. Budget checks use
  an in-memory running total seeded from the DB at claim time.
* Handlers are async functions registered with ``@job_handler("kind")``. They must be idempotent
  under retries: expensive work goes through the content-addressed ``AssetStore``; version
  writes use compare-and-set on ``ProjectVersion.revision`` with the payload's ``base_revision``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from ..config import Settings
from ..providers.base import Usage
from ..storage.assets import AssetStore, register_owner_scoped
from ..storage.base import Storage


class JobCancelled(Exception):
    """Cancellation requested by the user, or the lease was lost (``reason='lease_lost'``)."""

    def __init__(self, message: str = "cancelled", *, reason: str = "cancelled") -> None:
        super().__init__(message)
        self.reason = reason


class BudgetExceeded(Exception):
    """A cost limit (job or user daily) was hit. The job fails with error_code='budget'."""


class RetryableJobError(Exception):
    """Transient failure: the worker requeues the job (exponential backoff) if attempts remain."""


class FatalJobError(Exception):
    """Permanent failure with a user-facing message; never retried."""

    def __init__(self, message: str, *, code: str = "failed") -> None:
        super().__init__(message)
        self.code = code


class AwaitingReview(Exception):
    """A stage needs human approval (e.g. plan review).

    The worker marks the job ``awaiting_review`` and stores ``state`` in ``job.result``; the API
    resumes by enqueuing a continuation job (same kind, payload ``resume_state``) and marking this
    job ``succeeded``.
    """

    def __init__(self, message: str, state: dict[str, Any]) -> None:
        super().__init__(message)
        self.state = state


# A job's own stop (cancel, lost lease, shutdown) or budget breach is never handed to other jobs that
# wait on its in-flight asset production: they run the producer themselves (``AssetStore.get_or_create``).
register_owner_scoped(JobCancelled, BudgetExceeded)


@runtime_checkable
class JobContext(Protocol):
    job_id: int
    kind: str
    user_id: int | None
    project_id: int | None
    version_id: int | None
    payload: dict[str, Any]
    attempt: int  # fencing token
    worker_id: str
    settings: Settings
    storage: Storage
    assets: AssetStore

    def session(self) -> AbstractContextManager[Session]:
        """Short transactional session (commit on success). Blocking: call from a thread
        (``await asyncio.to_thread(fn)``) or keep the block free of ``await`` and tiny."""
        ...

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        """Overall progress 0..1 (monotonic). Non-blocking; persisted <= 1/s and on stage change."""
        ...

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        """Append a JobEvent visible in the UI (level: info|warning|error). Non-blocking; redacted."""
        ...

    def check_cancelled(self) -> None:
        """Raise JobCancelled if cancellation was requested or the lease was lost. No DB hit."""
        ...

    def record_usage(self, usage: Usage) -> float:
        """Price the usage (aadhi.usage.pricing), buffer a UsageEvent, add to the in-memory job
        cost and return the cost. Raises BudgetExceeded when the job budget
        (payload ``budget_usd`` or MAX_COST_PER_LECTURE_USD) or the user's daily budget is exhausted.
        Thread-safe and non-blocking: safe as a provider ``on_usage`` callback."""
        ...

    def ensure_budget(self, estimated_cost_usd: float, provider: str | None = None) -> None:
        """Raise BudgetExceeded if spending ``estimated_cost_usd`` more would exceed a limit.
        ``provider``: the usage provider that would bill it (spend on the user's personal key is
        outside the daily budget)."""
        ...

    async def flush(self) -> None:
        """Persist buffered progress/events/usage now (called before version writes and at the end)."""
        ...

    def assert_lease(self, db: Session) -> None:
        """Fence the caller's transaction by the job lease (a fenced UPDATE in ``db``). Call it in the
        same transaction as a ProjectVersion compare-and-set. Raises JobCancelled(reason='lease_lost')."""
        ...


JobHandler = Callable[[JobContext], Awaitable[dict[str, Any] | None]]

_REGISTRY: dict[str, JobHandler] = {}


def job_handler(kind: str) -> Callable[[JobHandler], JobHandler]:
    def deco(fn: JobHandler) -> JobHandler:
        if kind in _REGISTRY and _REGISTRY[kind] is not fn:
            raise RuntimeError(f"duplicate job handler for {kind!r}")
        _REGISTRY[kind] = fn
        return fn

    return deco


def get_handler(kind: str) -> JobHandler:
    load_handlers()
    try:
        return _REGISTRY[kind]
    except KeyError as exc:
        raise KeyError(f"no handler registered for job kind {kind!r}") from exc


def registered_kinds() -> list[str]:
    load_handlers()
    return sorted(_REGISTRY)


_LOADED = False
HANDLER_MODULES = (
    "aadhi.pipeline.orchestrator",  # generate_lecture, regenerate_scene, build_assets, translate
    "aadhi.compose.video",  # render_video
    "aadhi.legacy.jobs",  # import_legacy
    "aadhi.jobs.cleanup",  # cleanup
)


def load_handlers() -> None:
    """Import modules that register handlers (side-effect imports)."""
    global _LOADED
    if _LOADED:
        return
    import importlib
    import logging

    log = logging.getLogger(__name__)
    for mod in HANDLER_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as exc:  # pragma: no cover - optional module absent
            if exc.name != mod:
                log.exception("failed to import job handler module %s", mod)
        except Exception:  # pragma: no cover - one broken module must not hide the others
            log.exception("failed to import job handler module %s", mod)
    _LOADED = True
