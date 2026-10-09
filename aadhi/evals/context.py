"""In-process ``JobContext`` for the eval harness.

The production context (``aadhi.jobs.context.DBJobContext``) needs a claimed ``jobs`` row, a lease
and writer/heartbeat threads. Evals run pipeline stages directly, so this implementation keeps
events and usage in memory, prices usage with ``aadhi.usage.pricing`` when that module is
available, enforces a per-lecture budget (protects real-provider runs) and supports a wall-clock
deadline. It never imports ``tests/``.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import Usage
from ..storage.assets import AssetStore
from ..storage.base import Storage
from .metrics import UsageRecord

logger = logging.getLogger(__name__)

PriceFn = Callable[[Usage, Settings], float]
_MAX_DEPTH = 6


def jsonable(value: Any, settings: Settings, _depth: int = 0) -> Any:
    """JSON-safe, redacted copy of free-form event data (sets/tuples -> lists, other objects -> str)."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return settings.redact(value)
    if _depth >= _MAX_DEPTH:
        return settings.redact(repr(value))[:500]
    if isinstance(value, dict):
        return {str(k): jsonable(v, settings, _depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=repr) if isinstance(value, set | frozenset) else value
        return [jsonable(v, settings, _depth + 1) for v in items]
    return settings.redact(str(value))[:500]


def load_pricing() -> PriceFn | None:
    """``aadhi.usage.pricing.estimate_cost`` if the usage module exists, else ``None``."""
    try:
        from ..usage.pricing import estimate_cost
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("aadhi.usage"):
            return None
        raise
    return estimate_cost


class CostMeter:
    """Thread-safe usage accumulator with an optional budget (safe as a provider ``on_usage`` sink)."""

    def __init__(self, settings: Settings, *, budget_usd: float | None = None, price: PriceFn | None = None) -> None:
        self.settings = settings
        self.budget_usd = budget_usd
        self._price = price if price is not None else load_pricing()
        self.pricing_available = self._price is not None
        self.records: list[UsageRecord] = []
        self.total_usd = 0.0
        self.stage = ""
        self._lock = threading.Lock()

    def estimate(self, usage: Usage) -> float:
        """Price one usage record (0.0 when no pricing module or the price table lacks the model)."""
        if self._price is None:
            return 0.0
        try:
            return max(0.0, float(self._price(usage, self.settings)))
        except Exception as exc:  # pricing must never break an eval run
            logger.warning("pricing failed for %s/%s: %s", usage.provider, usage.model, self.settings.redact(str(exc)))
            return 0.0

    def record(self, usage: Usage) -> float:
        """Add a usage record; raises ``BudgetExceeded`` once the budget is exhausted."""
        cost = self.estimate(usage)
        with self._lock:
            self.records.append(UsageRecord(usage=usage, cost_usd=cost, stage=self.stage))
            self.total_usd += cost
            over = self.budget_usd is not None and self.total_usd > self.budget_usd
            total = self.total_usd
        if over:
            raise BudgetExceeded(f"eval budget of ${self.budget_usd:.2f} exceeded (spent ${total:.4f})")
        return cost

    def ensure(self, estimated_cost_usd: float) -> None:
        """Raise ``BudgetExceeded`` if spending ``estimated_cost_usd`` more would exceed the budget."""
        if self.budget_usd is None:
            return
        with self._lock:
            total = self.total_usd
        if total + max(0.0, estimated_cost_usd) > self.budget_usd:
            raise BudgetExceeded(
                f"estimated ${estimated_cost_usd:.4f} would exceed the eval budget of ${self.budget_usd:.2f}"
            )

    def __call__(self, usage: Usage) -> float:
        return self.record(usage)


@dataclass
class EvalJobContext:
    """Implements ``aadhi.jobs.base.JobContext`` without a worker or a ``jobs`` row."""

    settings: Settings
    assets: AssetStore
    session_factory: sessionmaker[Session]
    job_id: int = 0
    kind: str = "generate_lecture"
    user_id: int | None = None
    project_id: int | None = None
    version_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    worker_id: str = field(default_factory=lambda: f"eval:{socket.gethostname()}:{os.getpid()}")
    budget_usd: float | None = None
    deadline: float | None = None  # time.monotonic() value after which check_cancelled raises
    echo: Callable[[dict[str, Any]], None] | None = None  # live progress printer (CLI)
    events: list[dict[str, Any]] = field(default_factory=list)
    meter: CostMeter | None = None
    _cancelled: threading.Event = field(default_factory=threading.Event)
    _progress: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _t0: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.meter is None:
            self.meter = CostMeter(self.settings, budget_usd=self.budget_usd)

    # --- JobContext protocol ---------------------------------------------------------------
    @property
    def storage(self) -> Storage:
        return self.assets.storage

    def session(self) -> AbstractContextManager[Session]:
        """Short transactional session on the eval database (commit on success)."""
        return self._session()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        db = self.session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        """Record monotonic overall progress (never blocks)."""
        with self._lock:
            self._progress = max(self._progress, min(1.0, max(0.0, float(fraction))))
            event = {
                "type": "progress",
                "t": self._elapsed(),
                "stage": stage,
                "progress": round(self._progress, 4),
                "message": self.settings.redact(message),
            }
            self.events.append(event)
        self._emit(event)

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        """Record a (redacted) log event."""
        event = {
            "type": "log",
            "t": self._elapsed(),
            "level": level if level in ("info", "warning", "error") else "info",
            "stage": self._cost.stage,
            "message": self.settings.redact(str(message)),
            "data": jsonable(data, self.settings),
        }
        with self._lock:
            self.events.append(event)
        self._emit(event)

    def check_cancelled(self) -> None:
        """Raise ``JobCancelled`` after ``cancel()`` or once the deadline has passed."""
        if self._cancelled.is_set():
            raise JobCancelled("eval cancelled")
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise JobCancelled("eval fixture timed out", reason="timeout")

    def record_usage(self, usage: Usage) -> float:
        """Price and buffer usage; raises ``BudgetExceeded`` past the eval budget."""
        return self._cost.record(usage)

    def ensure_budget(self, estimated_cost_usd: float, provider: str | None = None) -> None:
        """Raise ``BudgetExceeded`` if ``estimated_cost_usd`` more would exceed the budget (any provider)."""
        self._cost.ensure(estimated_cost_usd)

    def assert_lease(self, db: Session) -> None:
        """Evals run without a job lease; nothing to fence."""
        self.check_cancelled()

    async def flush(self) -> None:
        """Nothing to persist: events and usage live in memory."""
        return None

    # --- eval helpers --------------------------------------------------------------------------
    def cancel(self) -> None:
        """Request cancellation (observed by the next ``check_cancelled``)."""
        self._cancelled.set()

    def set_stage(self, stage: str) -> None:
        """Attribute subsequent usage records to ``stage``."""
        self._cost.stage = stage

    @property
    def cost_usd(self) -> float:
        return self._cost.total_usd

    @property
    def usage_records(self) -> list[UsageRecord]:
        return list(self._cost.records)

    @property
    def _cost(self) -> CostMeter:
        if self.meter is None:  # pragma: no cover - set in __post_init__
            raise RuntimeError("EvalJobContext has no cost meter")
        return self.meter

    def _elapsed(self) -> float:
        return round(time.monotonic() - self._t0, 3)

    def _emit(self, event: dict[str, Any]) -> None:
        if self.echo is None:
            return
        try:
            self.echo(event)
        except Exception:  # a broken printer must not fail the pipeline
            logger.debug("eval echo failed", exc_info=True)
