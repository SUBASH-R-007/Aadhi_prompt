"""Preview vs export parity: does the MP4 show what the preview player shows at the same moment?

"One timeline, two renderers" (docs/ARCHITECTURE.md): the live/preview player draws the timeline in
the browser, the MP4 renderer screenshots the player in render mode and composites the mascot and
media with ffmpeg. This eval renders a small fixture lecture through the REAL stack (the app served
by uvicorn on a free loopback port, inline workers, the render page, ffmpeg), then opens the same
version in the preview player (Playwright, reduced motion, captions off), seeks to steady moments
of every scene and compares the screenshot with the MP4 frame at that time on a 32x18 grid
(``evals/video_qa.py``: mean absolute RGB difference of 255). The mascot's body is masked (the
preview's clip is paused at an arbitrary phase); the board region is also reported on its own,
because the preview draws frosted-glass panels over the moving mascot where the MP4 uses solid
surfaces (``glass-blur-parity``).

Offline: fake providers, narration and figures are synthesised locally, Chromium only talks to the
local server. Nothing outside the temporary workspace is written (the CLI overrides any .env
RENDER_WORK_DIR / RENDER_BASE_URL with the workspace and its own loopback server).

    .venv/Scripts/python.exe evals/render_parity.py                  # temp workspace, JSON report on stdout
    .venv/Scripts/python.exe evals/render_parity.py --out DIR --keep # keep the MP4, screenshots and report
    .venv/Scripts/python.exe evals/render_parity.py --threshold 8    # exit 1 when a sample differs more

Exit codes: 0 parity within the threshold, 1 over it, 2 missing tools (ffmpeg / Playwright).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import math
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_THRESHOLD = 10.0  # per-sample masked mean difference (of 255)
GRID = (32, 18)
STAGE = (1920, 1080)
CAPTION_BAND = (0, 900, 1920, 180)
# Mascot body + gesturing arms per position (stage px; measured in web/js/player/layout.js).
MASCOT_MASKS: dict[str, tuple[float, float, float, float]] = {
    "left": (0, 0, 720, 1080),
    "right": (1280, 0, 640, 1080),
    "center": (700, 0, 570, 1080),
    "popup_bottom_left": (520, 0, 1400, 1080),
    "popup_bottom_right": (520, 0, 1400, 1080),
}
BOARD_ZONES: dict[str, tuple[float, float, float, float]] = {  # (x, y, w, h) of the board card (no side panel)
    "left": (720, 56, 1140, 824),
    "right": (60, 56, 1190, 824),
    "center": (40, 56, 628, 824),
    "hidden": (160, 56, 1600, 824),
}

SCREENPLAY: dict[str, Any] = {
    "subject_name": "Basic Electrical Engineering",
    "unit_name": "Unit 2: DC Circuits",
    "session_number": "Session 3",
    "session_title": "Ohm's Law",
    "language": "en-IN",
    "concept_map": [{"id": "ohm", "title": "Ohm's Law"}],
    "scenes": [
        {"id": "p-title", "type": "title", "title": "Ohm's Law", "concept_id": "ohm", "mascot_position": "left",
         "board": [{"id": "h", "kind": "heading", "text": "Ohm's Law"},
                   {"id": "b1", "kind": "bullet", "text": "Voltage, current and resistance"}],
         "beats": [{"id": "p-title-1", "narration": "Welcome to this session on Ohm's law.", "board_item_id": "b1"},
                   {"id": "p-title-2", "narration": "We will connect voltage, current and resistance."}]},
        {"id": "p-law", "type": "content", "title": "The law", "concept_id": "ohm", "mascot_position": "right",
         "board": [{"id": "h2", "kind": "heading", "text": "V = I R"},
                   {"id": "f1", "kind": "formula", "latex": "V = I R"},
                   {"id": "b2", "kind": "bullet", "text": "Double the voltage, double the current"}],
         "beats": [{"id": "p-law-1", "narration": "The voltage equals current times resistance.", "board_item_id": "f1"},
                   {"id": "p-law-2", "narration": "Doubling the voltage doubles the current.", "board_item_id": "b2"}]},
        {"id": "p-sum", "type": "summary", "title": "Summary", "concept_id": "ohm", "mascot_position": "center",
         "board": [{"id": "t1", "kind": "takeaway", "text": "V = I R holds at constant temperature"}],
         "beats": [{"id": "p-sum-1", "narration": "To summarise, V equals I R.", "board_item_id": "t1"}]},
    ],
}


def load_video_qa() -> Any:
    spec = importlib.util.spec_from_file_location("aadhi_eval_video_qa", Path(__file__).resolve().parent / "video_qa.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def tools_missing() -> str | None:
    """Why this eval cannot run here (None when it can)."""
    if shutil.which(os.environ.get("FFMPEG", "ffmpeg")) is None or shutil.which(os.environ.get("FFPROBE", "ffprobe")) is None:
        return "ffmpeg/ffprobe not installed"
    if importlib.util.find_spec("playwright") is None:
        return "playwright not installed"
    return None


def configure_env(work: Path, port: int) -> None:
    """Environment for a self-contained run (CLI only): temp data dir and DB, fake providers, inline workers."""
    env = {
        "APP_ENV": "test", "DATA_DIR": str(work / "data"),
        "DATABASE_URL": f"sqlite:///{(work / 'parity.db').as_posix()}", "BASE_URL": f"http://127.0.0.1:{port}",
        "WORKER_MODE": "inline", "WORKER_CONCURRENCY": "1", "JOB_POLL_INTERVAL_SECONDS": "0.2",
        "STORAGE_BACKEND": "local", "STORAGE_LOCAL_DIR": str(work / "storage"),
        "LLM_PROVIDER": "fake", "TTS_PROVIDER": "fake", "IMAGE_PROVIDER": "fake", "VIDEO_PROVIDER": "fake",
        "GEMINI_API_KEY": "", "GEMINI_API_KEYS": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "ELEVENLABS_API_KEY": "", "GIPHY_API_KEY": "", "STORED_API_KEYS_ENABLED": "false",
        "JWT_SECRET": secrets.token_urlsafe(48), "ADMIN_USERNAME": "parity-admin",
        "ADMIN_PASSWORD": secrets.token_urlsafe(24), "MANIM_SANDBOX": "disabled",
        "RENDER_WIDTH": "640", "RENDER_HEIGHT": "360", "RENDER_FPS": "15", "RENDER_PRESET": "ultrafast",
        "RENDER_CRF": "23", "CORS_ORIGINS": "",
        # override any .env: the eval's render sweeps its work root (never a real one) and serves its own page
        "RENDER_WORK_DIR": str(work / "render-work"), "RENDER_BASE_URL": "",
    }
    os.environ.update(env)


# --- fixture data ---------------------------------------------------------------------------------


def _wav(seconds: float, freq: float, rate: int = 24000) -> bytes:
    n = int(seconds * rate)
    frames = bytearray()
    for i in range(n):
        v = int(0.25 * 32767 * math.sin(2 * math.pi * freq * i / rate))
        frames += v.to_bytes(2, "little", signed=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _manifest(sp: Any, store: Any) -> Any:
    """Narration for every scene: 1.6 s per beat, stored as real WAV assets."""
    from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS, AssetManifest, BeatAudio, SceneAudio
    from aadhi.schemas.timeline import TimedWord
    from aadhi.storage.assets import Produced

    speech = 1.6
    audio: dict[str, Any] = {}
    for n, scene in enumerate(sp.scenes):
        beats, t = [], 0.0
        for b in scene.beats:
            words = b.narration.split()
            step = speech / max(1, len(words))
            beats.append(BeatAudio(beat_id=b.id, offset=round(t, 3), speech_duration=speech, pause_after=0.0,
                                   spoken_text=b.narration,
                                   words=[TimedWord(text=w, start=round(t + k * step, 3), end=round(t + (k + 1) * step, 3))
                                          for k, w in enumerate(words)]))
            t += speech + INTER_BEAT_GAP_SECONDS
        duration = round(t - INTER_BEAT_GAP_SECONDS, 3)
        key = f"scene_audio-parity-{scene.id}"
        asset = store.put(key, "scene_audio", Produced(data=_wav(duration, 220 + 60 * n), mime="audio/wav",
                                                       duration_s=duration))
        audio[scene.id] = SceneAudio(scene_id=scene.id, asset_key=key, storage_key=asset.storage_key,
                                     mime="audio/wav", duration=duration, beats=beats, provider="fake", voice="fake")
    return AssetManifest(audio=audio, media={})


def seed(settings: Any) -> dict[str, Any]:
    """User + project + built version of the fixture lecture; returns ids and the login."""
    from aadhi.auth.passwords import hash_password
    from aadhi.compose.timeline import build_timeline
    from aadhi.db import get_sessionmaker
    from aadhi.models import Project, ProjectVersion, User
    from aadhi.schemas.screenplay import Screenplay
    from aadhi.storage import get_storage
    from aadhi.storage.assets import AssetStore

    store = AssetStore(get_storage(), get_sessionmaker())
    sp = Screenplay.model_validate(SCREENPLAY)
    manifest = _manifest(sp, store)
    password = secrets.token_urlsafe(18)
    with get_sessionmaker()() as db:
        user = User(username="parity-teacher", password_hash=hash_password(password), role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Parity check", language="en-IN", next_version_number=2)
        db.add(project)
        db.flush()
        version = ProjectVersion(project_id=project.id, number=1, status="ready", language="en-IN", revision=1)
        version.set_screenplay(sp)
        version.set_manifest(manifest)
        version.issues = []
        version.generation_meta = {}
        db.add(version)
        db.flush()
        version.set_timeline(build_timeline(sp, manifest, settings=settings, version_id=version.id, revision=1))
        version.built_revision = 1
        project.current_version_id = version.id
        db.commit()
        return {"username": user.username, "password": password, "version_id": version.id}


# --- live server ----------------------------------------------------------------------------------


@contextmanager
def live_server(settings: Any) -> Iterator[str]:
    """``create_app(settings)`` served by uvicorn on ``settings.base_url``'s port (inline workers)."""
    import uvicorn

    from aadhi.main import create_app

    port = int(settings.base_url.rsplit(":", 1)[1])
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, name="uvicorn-parity", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the parity server did not start")
        time.sleep(0.05)
    try:
        yield settings.base_url.rstrip("/")
    finally:
        server.should_exit = True
        thread.join(timeout=60)


def render_mp4(base: str, login: dict[str, Any], out: Path, *, timeout: float = 900) -> dict[str, Any]:
    """Start a render through the API, wait for it and download the MP4; returns the RenderSummary."""
    import httpx

    headers = {"Origin": base, "X-Aadhi-CSRF": "1"}
    with httpx.Client(base_url=base, headers=headers, timeout=60) as c:
        r = c.post("/api/auth/login", json={"username": login["username"], "password": login["password"]})
        r.raise_for_status()
        vid = login["version_id"]
        started = c.post(f"/api/versions/{vid}/render", json={"include_intro": True})
        started.raise_for_status()
        job_id, rid = started.json()["job"]["id"], started.json()["render"]["id"]
        deadline = time.monotonic() + timeout
        while True:
            job = c.get(f"/api/jobs/{job_id}").json()
            job = job.get("job", job)
            if job["status"] in ("succeeded", "failed", "cancelled"):
                break
            if time.monotonic() > deadline:
                raise TimeoutError("the render did not finish in time")
            time.sleep(0.5)
        if job["status"] != "succeeded":
            raise RuntimeError(f"render {job['status']}: {job.get('error')}")
        summary = next(x for x in c.get(f"/api/versions/{vid}/renders").json()["items"] if x["id"] == rid)
        video = c.get(summary["downloads"]["video"])
        video.raise_for_status()
        out.write_bytes(video.content)
        return summary


PARITY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="/web/css/tokens.css"><link rel="stylesheet" href="/web/css/base.css">
<link rel="stylesheet" href="/web/css/player.css"><link rel="stylesheet" href="/web/css/panels.css">
<style>html,body{margin:0;width:1920px;height:1080px;overflow:hidden;background:#000}
#root{position:fixed;left:0;top:0;width:1920px;height:1080px}</style>
<script type="module">
import { Player } from '/web/js/player/player.js';
const tl = await (await fetch('/api/versions/__VID__/timeline', { credentials: 'same-origin' })).json();
const player = new Player(document.getElementById('root'), { mode: 'preview', captions: false, autoplay: false, analytics: null });
await player.load(tl);
window.__parity = { player, timeline: tl };
</script></head><body><div id="root"></div></body></html>
"""

_SETTLE_JS = """async () => {
  await document.fonts.ready;
  const imgs = [...document.images].filter((i) => i.getAttribute('src'));
  await Promise.all(imgs.map((i) => (i.decode ? i.decode().catch(() => null) : null)));
  const clips = [...document.querySelectorAll('video')].filter((v) => v.getAttribute('src'));
  const t0 = performance.now();
  while (clips.some((v) => v.readyState < 2) && performance.now() - t0 < 5000) await new Promise((r) => setTimeout(r, 50));
  if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) await window.MathJax.startup.promise;
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""


async def preview_shots(base: str, login: dict[str, Any], times: list[float], out_dir: Path) -> list[Path]:
    """Screenshots of the preview player paused at each time (1920x1080, reduced motion, captions off)."""
    from playwright.async_api import async_playwright

    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    page_url = f"{base}/__parity"
    shots: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--force-color-profile=srgb", "--hide-scrollbars",
                                                                "--font-render-hinting=none", "--disable-lcd-text"])
        try:
            context = await browser.new_context(viewport={"width": STAGE[0], "height": STAGE[1]},
                                                device_scale_factor=1, reduced_motion="reduce", color_scheme="dark")

            async def only_local(route: Any) -> None:
                if route.request.url.startswith(base):
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", only_local)
            await context.route(page_url, lambda route: route.fulfill(
                status=200, content_type="text/html", body=PARITY_PAGE.replace("__VID__", str(login["version_id"]))))
            r = await context.request.post(f"{base}/api/auth/login", headers={"Origin": base, "X-Aadhi-CSRF": "1"},
                                           data={"username": login["username"], "password": login["password"]})
            if not r.ok:
                raise RuntimeError(f"preview login failed: HTTP {r.status}")
            page = await context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded")
            await page.wait_for_function("() => !!window.__parity", timeout=60_000)
            for n, t in enumerate(times):
                await page.evaluate("(t) => window.__parity.player.seek(t)", t)
                await page.evaluate(_SETTLE_JS)
                await page.wait_for_timeout(400)
                path = out_dir / f"preview_{n:02d}.png"
                await page.screenshot(path=str(path), type="png")
                shots.append(path)
        finally:
            await browser.close()
    return shots


# --- comparison -----------------------------------------------------------------------------------


def sample_times(timeline: Any) -> list[tuple[str, str, float]]:
    """(scene id, mascot position, absolute time) of a steady moment per scene: mid-speech of its last beat."""
    out = []
    for s in timeline.scenes:
        if s.beats:
            b = s.beats[-1]
            t = s.start + (b.start + b.speech_end) / 2
        else:
            t = s.start + s.duration / 2
        out.append((s.scene_id, s.layout.mascot_position, round(t, 3)))
    return out


def compare(vqa: Any, mp4: Path, shots: list[Path], samples: list[tuple[str, str, float]], fps: int) -> list[dict[str, Any]]:
    results = []
    for (sid, position, t), shot in zip(samples, shots, strict=True):
        preview = vqa.grid(vqa.image_rgb(shot), *GRID)
        export = vqa.grid(vqa.frame_rgb(mp4, round(t * fps), fps), *GRID)
        mask = [CAPTION_BAND, *([MASCOT_MASKS[position]] if position in MASCOT_MASKS else [])]
        board = BOARD_ZONES.get(position)
        outside_board = [*mask, board] if board else mask
        results.append({
            "scene_id": sid, "mascot_position": position, "t": t,
            "diff": round(vqa.mean_diff(preview, export, mask=mask, frame=STAGE), 2),
            "diff_outside_board": round(vqa.mean_diff(preview, export, mask=outside_board, frame=STAGE), 2),
            "diff_board": round(_region_diff(vqa, preview, export, board), 2) if board else None,
        })
    return results


def _region_diff(vqa: Any, a: Any, b: Any, region: tuple[float, float, float, float]) -> float:
    """Mean difference inside ``region`` only (everything else masked)."""
    x, y, w, h = region
    outside = [(0, 0, STAGE[0], y), (0, y + h, STAGE[0], STAGE[1] - y - h), (0, y, x, h), (x + w, y, STAGE[0] - x - w, h)]
    return vqa.mean_diff(a, b, mask=outside, frame=STAGE)


def run_parity(settings: Any, work: Path, *, threshold: float = DEFAULT_THRESHOLD) -> dict[str, Any]:
    """Seed, render, screenshot the preview and compare (``settings`` must point at a prepared temp DB/data
    dir, ``WORKER_MODE=inline`` and a free ``BASE_URL`` port)."""
    vqa = load_video_qa()
    from aadhi.compose.timeline import build_timeline
    from aadhi.schemas.screenplay import Screenplay

    work.mkdir(parents=True, exist_ok=True)
    login = seed(settings)
    mp4 = work / "export.mp4"
    with live_server(settings) as base:
        summary = render_mp4(base, login, mp4)
        timeline = build_timeline(Screenplay.model_validate(SCREENPLAY), None, settings=settings)  # times only
        from aadhi.db import get_sessionmaker
        from aadhi.models import ProjectVersion

        with get_sessionmaker()() as db:
            stored = db.get(ProjectVersion, login["version_id"]).get_timeline()
        timeline = stored or timeline
        samples = sample_times(timeline)
        shots = asyncio.run(preview_shots(base, login, [t for _, _, t in samples], work / "preview"))
    fps = int(settings.render_fps)
    results = compare(vqa, mp4, shots, samples, fps)
    worst = max((r["diff"] for r in results), default=0.0)
    return {
        "threshold": threshold, "ok": worst <= threshold, "worst_diff": worst,
        "mean_diff": round(sum(r["diff"] for r in results) / max(1, len(results)), 2),
        "samples": results, "render_qa": (summary.get("options") or {}).get("qa"),
        "mp4": str(mp4), "cfr": vqa.check_cfr(mp4, fps),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="workspace directory (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="keep the workspace (MP4, screenshots, report)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    args = parser.parse_args(argv)
    missing = tools_missing()
    if missing:
        print(json.dumps({"ok": False, "skipped": missing}))
        return 2
    work = (args.out or Path(tempfile.mkdtemp(prefix="aadhi-parity-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    configure_env(work, free_port())
    from aadhi import config, db, models  # noqa: F401  (models registers the tables)
    from aadhi.storage import reset_storage_cache

    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()
    settings = config.get_settings()
    db.Base.metadata.create_all(db.get_engine())
    try:
        report = run_parity(settings, work, threshold=args.threshold)
        (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    finally:
        db.get_engine().dispose()
        if not args.keep and args.out is None:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
