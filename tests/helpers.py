"""Shared test helpers."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from aadhi.config import Settings
from aadhi.db import session_scope
from aadhi.jobs.base import BudgetExceeded, JobCancelled
from aadhi.providers.base import Usage
from aadhi.storage.assets import AssetStore


@dataclass
class FakeJobContext:
    """Implements the ``aadhi.jobs.base.JobContext`` protocol in memory."""

    settings: Settings
    assets: AssetStore
    job_id: int = 0
    kind: str = "test"
    user_id: int | None = None
    project_id: int | None = None
    version_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    attempt: int = 1
    budget_usd: float | None = None
    cancelled: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)
    usages: list[Usage] = field(default_factory=list)
    cost_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def storage(self):  # type: ignore[override]
        return self.assets.storage

    def session(self) -> AbstractContextManager[Session]:
        return session_scope()

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        self.events.append({"type": "progress", "stage": stage, "progress": fraction, "message": message})

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.events.append({"type": "log", "level": level, "message": message, "data": data})

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def record_usage(self, usage: Usage) -> float:
        cost = 0.0
        try:  # price it if the usage module exists
            from aadhi.usage.pricing import estimate_cost

            cost = float(estimate_cost(usage, self.settings))
        except ImportError:
            cost = 0.0
        with self._lock:
            self.usages.append(usage)
            self.cost_usd += cost
            if self.budget_usd is not None and self.cost_usd > self.budget_usd:
                raise BudgetExceeded(f"job budget {self.budget_usd} exceeded")
        return cost

    def assert_lease(self, db: Session) -> None:  # no lease in tests
        return None

    async def flush(self) -> None:
        return None

    def ensure_budget(self, estimated_cost_usd: float, provider: str | None = None) -> None:
        if self.budget_usd is not None and self.cost_usd + estimated_cost_usd > self.budget_usd:
            raise BudgetExceeded("estimated cost exceeds budget")
