"""``ingest.run_pdf_worker``: a PDF worker killed after a timeout (or a cancelled job) is reaped before the call
returns, and its temp dir is gone."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from aadhi.pipeline import ingest as ingest_mod
from aadhi.pipeline.ingest import IngestError, run_pdf_worker
from tests.pipeline.sources import make_pdf


@pytest.fixture()
def spawned(monkeypatch) -> list[tuple[Any, Path]]:
    """Every PDF worker started (its process and work dir)."""
    real = asyncio.create_subprocess_exec
    out: list[tuple[Any, Path]] = []

    async def spy(*args: Any, **kwargs: Any) -> Any:
        proc = await real(*args, **kwargs)
        out.append((proc, Path(kwargs["cwd"])))
        return proc

    monkeypatch.setattr(ingest_mod.asyncio, "create_subprocess_exec", spy)
    return out


async def test_a_timed_out_worker_is_killed_and_reaped(spawned):
    with pytest.raises(IngestError, match="longer than"):
        await run_pdf_worker(make_pdf(), 5, 0.01)
    (proc, work), = spawned
    assert proc.returncode is not None  # reaped before the call returned: no zombie, no open pipes
    assert not work.exists()


async def test_the_worker_is_reaped_even_when_the_kill_does_not_wait(spawned, monkeypatch):
    import psutil

    monkeypatch.setattr(ingest_mod, "_kill_tree", lambda pid: psutil.Process(pid).kill())  # signal only, no wait
    with pytest.raises(IngestError, match="longer than"):
        await run_pdf_worker(make_pdf(), 5, 0.01)
    (proc, work), = spawned
    assert proc.returncode is not None and not work.exists()


async def test_a_cancelled_read_kills_and_reaps_the_worker(spawned, monkeypatch):
    task = asyncio.ensure_future(run_pdf_worker(make_pdf(), 5, 120))
    for _ in range(500):  # wait until the worker runs
        if spawned:
            break
        await asyncio.sleep(0.01)
    assert spawned
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    (proc, work), = spawned
    assert proc.returncode is not None and not work.exists()


async def test_a_worker_that_will_not_exit_is_not_waited_on_forever(spawned, monkeypatch, caplog):
    monkeypatch.setattr(ingest_mod, "PDF_REAP_SECONDS", 0.05)
    monkeypatch.setattr(ingest_mod, "_kill_tree", lambda pid: None)  # the kill does not take effect
    with pytest.raises(IngestError, match="longer than"):
        await run_pdf_worker(make_pdf(), 5, 0.01)
    assert any("did not exit" in r.message for r in caplog.records)
    (proc, _), = spawned
    proc.kill()  # the test's own clean-up
    await proc.wait()
