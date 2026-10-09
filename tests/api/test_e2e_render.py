"""Full system test: a live uvicorn server (ephemeral port) with inline workers renders an MP4
through the REAL render page (``/render-frame`` + ``web/js/render`` + the player) and the real
``GET /api/render/timeline`` scoped-token endpoint, then the files are downloaded over HTTP.

Slow: Playwright Chromium + ffmpeg. Small output (640x360 @ 15 fps, ultrafast) to keep it short.
Offline: fake LLM/TTS/images; Chromium only talks to the local server.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.api.e2e import FAST_OPTIONS, SOURCE_MARKDOWN, assert_succeeded, require_job_handlers, wait_job
from tests.api.factories import PASSWORD, add_user

pytestmark = pytest.mark.slow


def free_port() -> int:
    """An ephemeral TCP port on the loopback interface (never 8000)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def render_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Environment for the live server (read by ``app_env``); returns its base URL."""
    pytest.importorskip("playwright.async_api")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not installed")
    base = f"http://127.0.0.1:{free_port()}"
    env = {
        "BASE_URL": base,
        "WORKER_MODE": "inline",
        "WORKER_CONCURRENCY": "2",
        "JOB_POLL_INTERVAL_SECONDS": "0.2",
        "MANIM_SANDBOX": "disabled",
        "RENDER_WIDTH": "640",
        "RENDER_HEIGHT": "360",
        "RENDER_FPS": "15",
        "RENDER_PRESET": "ultrafast",
        "RENDER_CRF": "30",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return base


@pytest.fixture()
def live_server(render_env: str, app_env: Any) -> Iterator[str]:
    """Serve ``create_app(settings)`` with uvicorn in a thread; stop it (and its workers) afterwards."""
    import uvicorn

    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    assert app_env.base_url == render_env and app_env.worker_mode == "inline"
    reset_rate_limits()
    port = int(render_env.rsplit(":", 1)[1])
    config = uvicorn.Config(
        create_app(app_env), host="127.0.0.1", port=port, log_level="warning", access_log=False, lifespan="on"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="uvicorn-e2e", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("the live server did not start")
        time.sleep(0.05)
    try:
        yield render_env
    finally:
        server.should_exit = True
        thread.join(timeout=60)
        reset_rate_limits()


def ffprobe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        check=True,
        timeout=60,
    )
    return json.loads(out.stdout)


def test_upload_generate_render_and_download_mp4(live_server: str, app_env: Any, tmp_path: Path) -> None:
    import httpx

    from aadhi.db import get_sessionmaker

    require_job_handlers("generate_lecture", "render_video")  # fail fast instead of a 900 s timeout
    with get_sessionmaker()() as db:
        add_user(db, "alice")
    headers = {"Origin": live_server, "X-Aadhi-CSRF": "1"}
    with httpx.Client(base_url=live_server, headers=headers, timeout=60) as c:
        assert c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 200

        # The pages and modules the renderer loads are served with the right types and policies.
        frame = c.get("/render-frame")
        assert frame.status_code == 200 and frame.headers["content-type"].startswith("text/html")
        assert "script-src 'self'" in frame.headers["content-security-policy"]
        module = c.get("/web/js/render/render.js")
        assert module.status_code == 200 and module.headers["content-type"].startswith("text/javascript")

        created = c.post(
            "/api/projects",
            files={"file": ("ohm.md", SOURCE_MARKDOWN.encode("utf-8"), "text/markdown")},
            data={"options": json.dumps(FAST_OPTIONS), "title": "Ohm's Law"},
        )
        assert created.status_code == 201, created.text
        vid = created.json()["version"]["id"]
        assert_succeeded(c, wait_job(c, created.json()["job"]["id"]))
        timeline = c.get(f"/api/versions/{vid}/timeline").json()
        # The stored timeline includes the intro; this render leaves it out.
        intro = (timeline.get("intro") or {}).get("duration", 0.0)
        expected_duration = timeline["total_duration"] - intro

        started = c.post(f"/api/versions/{vid}/render", json={"include_intro": False, "burn_captions": False})
        assert started.status_code == 202, started.text
        render_id = started.json()["render"]["id"]
        assert_succeeded(c, wait_job(c, started.json()["job"]["id"], timeout=900))

        renders = c.get(f"/api/versions/{vid}/renders").json()["items"]
        summary = next(r for r in renders if r["id"] == render_id)
        assert summary["status"] == "succeeded" and summary["built_revision"] == timeline["screenplay_revision"]
        assert set(summary["downloads"]) >= {"video", "srt", "vtt"}
        assert summary["duration_s"] == pytest.approx(expected_duration, abs=1.0)

        video = c.get(summary["downloads"]["video"])
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"
        assert video.headers["content-disposition"] == 'attachment; filename="Ohm\'s Law.mp4"'
        mp4 = tmp_path / "lecture.mp4"
        mp4.write_bytes(video.content)
        info = ffprobe(mp4)
        streams = {s["codec_type"]: s for s in info["streams"]}
        assert {"video", "audio"} <= set(streams)
        assert (streams["video"]["width"], streams["video"]["height"]) == (640, 360)
        assert float(info["format"]["duration"]) == pytest.approx(expected_duration, abs=1.0)

        srt = c.get(summary["downloads"]["srt"])
        assert srt.status_code == 200 and "-->" in srt.text
        assert srt.headers["content-disposition"].endswith('.srt"')
