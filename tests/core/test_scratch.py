"""The scratch root (``SCRATCH_DIR``, ``aadhi.scratch``): host sweeps never touch folders outside it.

The real system temp dir is never used here. A directory of the test plays "the system temp dir"
(``tempfile.tempdir``), so the proof that a stale ``aadhi-render-old`` folder there survives every sweep
does not depend on, or touch, the developer's real temp folder.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
from pathlib import Path

import pytest

from aadhi import scratch
from aadhi.compose import workspace
from aadhi.manim import sandbox
from aadhi.scratch import UnsafeScratchDir, ensure_scratch_root, make_temp_dir, scratch_root

STALE_NAMES = ("aadhi-render-old", "aadhi-manim-old", "aadhi-m-old", "aadhi-d-old")


def _stale_dirs(base: Path) -> list[Path]:
    """Folders every sweep would remove inside the scratch root: our prefixes, no owner marker, two days old."""
    old = time.time() - 48 * 3600
    out = []
    for name in STALE_NAMES:
        d = base / name
        d.mkdir(parents=True)
        (d / "leftover.bin").write_bytes(b"x")
        os.utime(d, (old, old))
        out.append(d)
    return out


def test_the_test_session_never_uses_the_real_temp_dir(app_env):
    from tests import conftest

    session = Path(conftest.SESSION_TEMP)
    assert Path(tempfile.gettempdir()) == session != Path(conftest._REAL_TEMP)
    assert all(Path(os.environ[name]) == session for name in ("TEMP", "TMP", "TMPDIR"))
    assert scratch_root(app_env).parent == session.parent  # SCRATCH_DIR: <session>/scratch
    assert Path(conftest._REAL_TEMP) not in {Path(p) for p in (tempfile.gettempdir(), scratch_root(app_env))}


def test_sweeps_never_touch_stale_folders_next_to_the_scratch_root(app_env, tmp_path, monkeypatch):
    """A stale ``aadhi-render-old`` (and Manim-looking) folder in the system temp dir is never removed by any
    sweep (render start, worker housekeeping, cleanup job); the same folders inside the scratch root are."""
    from aadhi.db import get_sessionmaker
    from aadhi.jobs.cleanup import _sweep_host_files
    from aadhi.jobs.worker import Worker

    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    app_env.scratch_dir = ""  # the default root: <system temp>/aadhi
    root = ensure_scratch_root(app_env)
    assert root.parent == system_temp and root.name.startswith("aadhi")
    outside = _stale_dirs(system_temp)
    inside = _stale_dirs(root)

    listed: list[Path] = []
    real_iterdir = pathlib.Path.iterdir

    def spy(self):
        listed.append(Path(os.path.abspath(self)))
        return real_iterdir(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", spy)
    workspace.sweep_stale(app_env, lambda ids: {})
    worker = Worker(app_env, kinds=[])
    worker.kinds = ["build_assets", "render_video"]
    worker.sweep_host_once()
    _sweep_host_files(app_env, get_sessionmaker())
    sandbox.sweep_orphans(app_env)

    assert all(d.is_dir() and (d / "leftover.bin").is_file() for d in outside)
    assert not any(d.exists() for d in inside)
    assert system_temp not in listed  # the system temp dir is never even listed
    assert root in listed


def test_explicit_scratch_dir_is_the_only_swept_place(app_env, tmp_path, monkeypatch):
    system_temp = tmp_path / "system-temp"
    system_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(system_temp))
    app_env.scratch_dir = str(tmp_path / "scratch")
    outside = _stale_dirs(system_temp) + _stale_dirs(system_temp / "aadhi")  # even the default root is left alone
    inside = _stale_dirs(ensure_scratch_root(app_env))
    workspace.sweep_stale(app_env, lambda ids: {})
    sandbox.sweep_orphans(app_env)
    assert all(d.is_dir() for d in outside) and not any(d.exists() for d in inside)


def test_manim_work_dirs_are_created_inside_the_scratch_root(app_env):
    from aadhi.manim import render

    root = scratch_root(app_env)
    media = make_temp_dir(app_env, "aadhi-m-")
    work = render._new_workdir(app_env)
    try:
        assert media.parent == root and work.parent == root
        assert work.name.startswith("aadhi-manim-") and media.name.startswith("aadhi-m-")
    finally:
        media.rmdir()
        work.rmdir()
    runner = sandbox.get_runner(app_env)
    assert Path(runner.scratch_dir) == root
    assert sandbox._temp_roots(app_env) == [root]
    app_env.manim_sandbox = "docker"
    assert Path(sandbox.get_runner(app_env).scratch_dir) == root


def test_default_root_is_a_private_folder_inside_the_temp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    root = scratch_root()
    assert root.parent == tmp_path and root.name == scratch._default_name()
    assert not root.exists()  # computing the root never creates it (sweeps have no side effects)
    assert ensure_scratch_root() == root and root.is_dir()
    assert ensure_scratch_root() == root  # idempotent
    if os.name != "nt":
        assert root.name == f"aadhi-{os.getuid()}" and (root.stat().st_mode & 0o777) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="the shared-/tmp checks are POSIX-only (Windows temp dirs are per user)")
def test_default_root_refuses_a_symlink_planted_by_someone_else(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    scratch_root().symlink_to(elsewhere)
    with pytest.raises(UnsafeScratchDir):
        ensure_scratch_root()


def test_sweep_of_a_missing_root_does_nothing(app_env, tmp_path):
    app_env.scratch_dir = str(tmp_path / "never-created")
    assert sandbox.sweep_orphans(app_env)["temp_dirs"] == 0
    assert workspace.sweep_stale(app_env, lambda ids: {}) == 0
    assert not (tmp_path / "never-created").exists()
