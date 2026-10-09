"""Host housekeeping: worker start-up capability log, hourly sweep of this host's leftovers (Manim
temp dirs / expired sandbox containers, stale render workspaces), and the cleanup job's host step."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from aadhi.jobs.worker import Worker

from ._helpers import wait_for


@pytest.fixture()
def sweeps(monkeypatch):
    """Stand-ins for the two sweepers (the real ones touch this machine's temp folder)."""
    from aadhi.compose import video
    from aadhi.manim import sandbox

    rec = SimpleNamespace(manim=0, render=0)

    def manim(settings, now=None):
        rec.manim += 1
        return {"temp_dirs": 2, "containers": 1}

    def render(settings, session):
        rec.render += 1
        with session() as db:  # gets a usable session (sessionmaker or session_scope)
            assert db is not None
        return 3

    monkeypatch.setattr(sandbox, "sweep_orphans", manim)
    monkeypatch.setattr(video, "sweep_render_workspaces", render)
    return rec


def test_sweep_host_once_follows_the_kinds_the_worker_runs(app_env, sweeps):
    worker = Worker(app_env, kinds=[])
    worker.kinds = ["build_assets", "render_video"]
    assert worker.sweep_host_once() == {"manim_temp_dirs": 2, "manim_containers": 1, "render_workspaces": 3}
    worker.kinds = ["cleanup"]
    assert worker.sweep_host_once() == {}
    worker.kinds = ["render_video"]
    assert worker.sweep_host_once() == {"render_workspaces": 3}
    assert (sweeps.manim, sweeps.render) == (1, 2)


def test_maintenance_sweeps_at_start_only_with_host_housekeeping(make_worker, sweeps):
    from aadhi.jobs.base import job_handler

    @job_handler("build_assets")
    async def handler(ctx):
        return None

    quiet = make_worker(["build_assets"])
    quiet.start()
    wait_for(lambda: quiet.kinds == ["build_assets"])
    quiet.stop()
    assert sweeps.manim == 0  # library / test workers never touch the host by default

    busy = make_worker(["build_assets"], host_housekeeping=True)
    busy.start()
    wait_for(lambda: sweeps.manim == 1)
    busy.stop()


def test_capability_log_only_for_render_workers(app_env, monkeypatch, caplog):
    from aadhi.compose import capabilities

    calls = []

    def fake(settings, *, browser=True, force=False):
        calls.append(browser)
        return SimpleNamespace(ok=False, reasons=["ffmpeg was not found ('ffmpeg')."], burn_captions=False)

    monkeypatch.setattr(capabilities, "render_capabilities", fake)
    worker = Worker(app_env, kinds=[])
    worker.kinds = ["build_assets"]
    worker.log_capabilities()
    assert calls == []
    worker.kinds = ["render_video"]
    with caplog.at_level(logging.WARNING, logger="aadhi.jobs.worker"):
        worker.log_capabilities()
    assert calls == [True] and "cannot render MP4s here" in caplog.text and "ffmpeg was not found" in caplog.text


def test_cleanup_runs_the_host_step_except_in_a_dry_run(app_env, host_sweeps):
    from aadhi.jobs.cleanup import run_cleanup

    run_cleanup(app_env, dry_run=True)
    assert host_sweeps.calls == []
    run_cleanup(app_env)
    assert len(host_sweeps.calls) == 1


def test_cleanup_host_step_reports_counts_and_never_fails(app_env, host_sweeps, sweeps, monkeypatch):
    from aadhi.db import get_sessionmaker
    from aadhi.manim import sandbox

    out = host_sweeps.real(app_env, get_sessionmaker())
    assert out == {"manim_temp_dirs": 2, "manim_containers": 1, "render_workspaces": 3}

    def broken(settings, now=None):
        raise OSError("disk gone")

    monkeypatch.setattr(sandbox, "sweep_orphans", broken)
    assert host_sweeps.real(app_env, get_sessionmaker()) == {"render_workspaces": 3}  # one failure skips one step
