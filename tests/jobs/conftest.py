"""Fixtures for the job-system tests.

* Every test gets an isolated handler registry (``aadhi.jobs.base._REGISTRY``) so test kinds never
  collide with the real pipeline/compose handlers and ``load_handlers`` imports nothing.
* Workers created through ``make_worker`` use short intervals and are always stopped.
* ``run_cleanup`` never sweeps this machine's real temp folder (Manim leftovers, render workspaces):
  its host step is recorded by the autouse ``host_sweeps`` fixture (``.real`` is the real function).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest

import aadhi.jobs.cleanup  # noqa: F401  (registers "cleanup" in the real registry before isolation)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from aadhi.jobs import base

    registry: dict[str, Any] = {}
    monkeypatch.setattr(base, "_REGISTRY", registry)
    monkeypatch.setattr(base, "_LOADED", True)
    monkeypatch.setattr(base, "HANDLER_MODULES", ())  # Worker.resolve_kinds imports these one by one
    return registry


@pytest.fixture(autouse=True)
def host_sweeps(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    from aadhi.jobs import cleanup

    rec = SimpleNamespace(calls=[], real=cleanup._sweep_host_files)

    def record(settings: Any, factory: Any) -> dict[str, int]:
        rec.calls.append(settings)
        return {}

    monkeypatch.setattr(cleanup, "_sweep_host_files", record)
    return rec


@pytest.fixture(autouse=True)
def clean_process_state() -> Iterator[None]:
    from aadhi.jobs import limits
    from aadhi.jobs.heartbeat import heartbeat_service

    limits.reset_limits()
    yield
    limits.reset_limits()
    hb = heartbeat_service()
    for holder in hb.holders():
        hb.unregister(holder)


@pytest.fixture()
def make_worker(app_env) -> Iterator[Callable[..., Any]]:
    """Factory for fast workers (stopped at teardown)."""
    from aadhi.jobs.worker import Worker

    created: list[Worker] = []

    def factory(kinds: list[str], concurrency: int = 1, **kwargs: Any) -> Worker:
        options: dict[str, Any] = {
            "poll_interval": 0.02,
            "heartbeat_interval": 0.2,
            "flag_interval": 0.05,
            "flush_interval": 0.05,
            "cancel_grace_seconds": 0.5,
            "retry_backoff_base": 0.1,
            "retry_backoff_cap": 1.0,
            "reaper_interval": 0.2,
        }
        options.update(kwargs)
        worker = Worker(app_env, kinds=kinds, concurrency=concurrency, **options)
        created.append(worker)
        return worker

    yield factory
    for w in created:
        w.stop(timeout=5)
