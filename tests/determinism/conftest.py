"""Determinism and parity suites of the motion engine (DYNAMIC_VIDEO_DESIGN §7.4 tests (a) and (b), §10.3 B1).

Every test here is ``slow`` (real Chromium headless shell, real ffmpeg) and the frame-hash tests are also
``container``: the Docker ``test`` image runs them in the blocking CI job (``pytest -m container``), where Linux
FreeType rasterises the text; Windows renders are compared only with Windows captures (§7.4 (c)), which is what a
local run does.

``real_app`` serves the real application (``create_app`` under uvicorn on a free loopback port, a temporary data dir
and database, ``DYNAMIC_ENABLED`` on) for the tests that drive the real render page ``/render-dynamic``; they skip
while the stage bundle (``web/render-dynamic.html``, ``web/js/dynamic/stage.js``) is absent. In the Docker test image
(``AADHI_CONTAINER_TESTS=1``) a missing bundle, Playwright or Chromium fails these tests instead of skipping them
(``tests.compose.dynamic.helpers.missing``).
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from aadhi.config import ROOT_DIR

STAGE_FILES = (ROOT_DIR / "web" / "render-dynamic.html", ROOT_DIR / "web" / "js" / "dynamic" / "stage.js")


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "container: suites the Docker test image runs (pytest -m container)")


@dataclass
class RealApp:
    settings: Any
    base: str
    work: Path


def need_real_stage() -> None:
    """The real stage bundle, else skip (fail in the Docker test image, ``AADHI_CONTAINER_TESTS=1``: there a missing
    bundle is a broken image and the blocking CI job must not pass by skipping the determinism suite)."""
    from tests.compose.dynamic.helpers import missing

    absent = [p.name for p in STAGE_FILES if not p.is_file()]
    if absent:
        missing(f"the Dynamic stage bundle is not in this build ({', '.join(absent)})")


@pytest.fixture(scope="module")
def real_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[RealApp]:
    """The real app on 127.0.0.1:<free port> with its own data dir and database (module scope)."""
    from tests.compose.dynamic.helpers import need_chromium, parity_eval

    need_real_stage()
    need_chromium()
    from aadhi import config, db, models  # noqa: F401  (models registers the tables)
    from aadhi.storage import reset_storage_cache

    work = tmp_path_factory.mktemp("real-app")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    mp = pytest.MonkeyPatch()
    for key, value in {
        "DATA_DIR": str(work / "data"), "DATABASE_URL": f"sqlite:///{(work / 'app.db').as_posix()}",
        "BASE_URL": f"http://127.0.0.1:{port}", "DYNAMIC_ENABLED": "true", "WORKER_MODE": "external",
        "RENDER_WORK_DIR": str(work / "render-work"), "RENDER_WIDTH": "1920", "RENDER_HEIGHT": "1080",
        "RENDER_FPS": "30", "RENDER_BGM": "false",
    }.items():
        mp.setenv(key, value)
    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()
    try:
        settings = config.get_settings()
        db.Base.metadata.create_all(db.get_engine())
        with parity_eval().live_server(settings) as base:
            yield RealApp(settings=settings, base=base, work=work)
    finally:
        db.get_engine().dispose()
        mp.undo()
        config.get_settings.cache_clear()
        db.reset_engine_cache()
        reset_storage_cache()
