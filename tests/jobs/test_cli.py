"""``python -m aadhi.worker``: argument parsing, signals, drain mode, real subprocess."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from aadhi import worker as cli
from aadhi.jobs.base import job_handler

from ._helpers import add_job, get_job

ROOT = Path(__file__).resolve().parents[2]


def test_parse_kinds(app_env):
    assert cli.parse_kinds(None, app_env) == list(app_env.worker_kinds)
    assert cli.parse_kinds("render_video, cleanup", app_env) == ["render_video", "cleanup"]
    with pytest.raises(ValueError):
        cli.parse_kinds("render_video,nope", app_env)
    with pytest.raises(ValueError):
        cli.parse_kinds(" , ", app_env)


def test_invalid_arguments_exit_2(app_env):
    assert cli.main(["--kinds", "bogus"]) == 2
    assert cli.main(["--concurrency", "0", "--drain"]) == 2


def test_shutdown_signal_handler():
    stop = threading.Event()
    exits: list[int] = []
    sig = cli.ShutdownSignals(stop, force_exit=exits.append)
    sig.handler(signal.SIGINT, None)
    assert stop.is_set() and exits == []
    sig.handler(signal.SIGTERM, None)
    assert exits == [130]


def test_signal_install_and_restore():
    stop = threading.Event()
    sig = cli.ShutdownSignals(stop)
    before = signal.getsignal(signal.SIGINT)
    sig.install()
    try:
        assert signal.getsignal(signal.SIGINT) == sig.handler
    finally:
        sig.restore()
    assert signal.getsignal(signal.SIGINT) == before


def test_drain_mode_in_process(app_env):
    @job_handler("cleanup")
    async def fake_cleanup(ctx):
        return {"deleted": {}}

    ids = [add_job("cleanup") for _ in range(3)]
    assert cli.main(["--drain", "--kinds", "cleanup", "--concurrency", "2"]) == 0
    assert all(get_job(i).status == "succeeded" for i in ids)


def _env(app_env) -> dict[str, str]:
    env = dict(os.environ)
    env.update(DATA_DIR=str(app_env.data_dir), DATABASE_URL=app_env.resolved_database_url,
               PYTHONPATH=str(ROOT), JOB_POLL_INTERVAL_SECONDS="0.05", LOG_LEVEL="INFO")
    return env


def test_module_entrypoint_drains_real_cleanup_job(app_env):
    jid = add_job("cleanup", payload={"dry_run": True})
    proc = subprocess.run(
        [sys.executable, "-m", "aadhi.worker", "--drain", "--kinds", "cleanup", "--drain-timeout", "60"],
        cwd=ROOT, env=_env(app_env), capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    row = get_job(jid)
    assert row.status == "succeeded" and row.result["deleted"]["dry_run"] is True


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break is Windows-specific")
def test_ctrl_break_stops_worker_gracefully(app_env):
    proc = subprocess.Popen(
        [sys.executable, "-m", "aadhi.worker", "--kinds", "cleanup", "--no-schedule-cleanup",
         "--shutdown-timeout", "5"],
        cwd=ROOT, env=_env(app_env), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,  # type: ignore[attr-defined]
    )
    watchdog = threading.Timer(90, proc.kill)
    watchdog.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:  # wait until the worker is up
            if "started" in line:
                break
        proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        out, _ = proc.communicate(timeout=60)
        assert proc.returncode == 0, out[-2000:]
        assert "stopped" in out
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait(10)
