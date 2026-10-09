"""Preview vs export parity (evals/render_parity.py) through the real stack: a live server with inline
workers renders a fixture lecture, the preview player is screenshotted at the same moments, and the
frames are compared on a 32x18 grid. Slow: Playwright Chromium + ffmpeg; offline."""

from __future__ import annotations

import importlib.util
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest

from aadhi.config import ROOT_DIR

pytestmark = pytest.mark.slow


def _load_parity() -> Any:
    spec = importlib.util.spec_from_file_location("aadhi_eval_render_parity", ROOT_DIR / "evals" / "render_parity.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def parity_env(monkeypatch: pytest.MonkeyPatch) -> str:
    pytest.importorskip("playwright.async_api")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {"BASE_URL": f"http://127.0.0.1:{port}", "WORKER_MODE": "inline", "WORKER_CONCURRENCY": "1",
           "JOB_POLL_INTERVAL_SECONDS": "0.2", "MANIM_SANDBOX": "disabled", "RENDER_WIDTH": "640",
           "RENDER_HEIGHT": "360", "RENDER_FPS": "15", "RENDER_PRESET": "ultrafast", "RENDER_CRF": "23"}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env["BASE_URL"]


def test_export_matches_the_preview(parity_env: str, app_env: Any, tmp_path: Path) -> None:
    from aadhi.security.ratelimit import reset_rate_limits

    assert app_env.base_url == parity_env and app_env.worker_mode == "inline"
    reset_rate_limits()
    parity = _load_parity()
    report = parity.run_parity(app_env, tmp_path / "p")
    reset_rate_limits()
    assert report["ok"], report
    assert report["worst_diff"] <= 6.0, report["samples"]  # mascot masked; glass panels without the blur
    assert all(s["diff_board"] is None or s["diff_board"] <= 8.0 for s in report["samples"]), report["samples"]
    assert len(report["samples"]) == 3 and report["cfr"]["ok"]
    assert report["render_qa"]["ok"] and report["render_qa"]["color"]["color_space"] == "bt709"
