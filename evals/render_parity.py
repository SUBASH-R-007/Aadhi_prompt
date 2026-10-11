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
    .venv/Scripts/python.exe evals/render_parity.py --dynamic portrait   # Dynamic: live stage vs motion MP4, SSIM

Dynamic videos (``--dynamic landscape|portrait``, DYNAMIC_VIDEO_DESIGN §7.4 (b)): the fixture with a synthetic
``presentation`` is rendered by the motion engine and compared with the live-mode stage at 20 seeded random
instants; every SSIM must be at least 0.98 (see the "Dynamic videos" section below).

Exit codes: 0 parity within the threshold, 1 over it, 2 missing tools (ffmpeg / Playwright; for ``--dynamic`` also a
build without the motion engine).
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


def configure_env(work: Path, port: int, *, dynamic: bool = False) -> None:
    """Environment for a self-contained run (CLI only): temp data dir and DB, fake providers, inline workers.
    ``dynamic``: the Dynamic render page on (``DYNAMIC_ENABLED``) and full-size 30 fps output, as in production."""
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
    if dynamic:
        env.update({"DYNAMIC_ENABLED": "true", "RENDER_WIDTH": "1920", "RENDER_HEIGHT": "1080", "RENDER_FPS": "30"})
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


# =================================================================================================
# Dynamic videos (motion engine): live stage vs MP4, SSIM at 20 instants (DYNAMIC_VIDEO_DESIGN §7.4 (b))
# =================================================================================================
#
# The live player and the render page share one compositor, ``web/js/dynamic/stage.js``
# (``createDynamicStage(root, timeline, {mode})``). This part renders a Dynamic fixture (the lecture above with a
# synthetic ``presentation`` and per-scene looks, 16:9 or 9:16) through the motion engine, opens the same timeline
# in a live-mode stage (``update(t)``), and compares the screenshot with the MP4 frame at 20 seeded random instants
# by SSIM on luma (Gaussian 11x11, sigma 1.5). Instants are whole frames (t = n / fps), so both sides draw the same t.
# Windows renders are compared only with Windows previews (§7.4 (c)). Each sample also reports where a difference
# comes from: the compositor (live vs the render page's lossless frame), the JPEG capture, or x264.

SSIM_THRESHOLD = 0.98
DYNAMIC_SAMPLES = 20
DYNAMIC_SEED = 20261010
DYNAMIC_FPS = 30
CONTENT_TOKENS = {  # §4.7, dark families
    "ink": "#F4F4FA", "ink2": "#C5C7D6", "entity:0": "#0FE3FE", "entity:1": "#C4A4FE", "entity:2": "#FEACE3",
    "warn": "#F8A87F", "ok": "#5DD8AA", "mark": "#F2D935", "focus": "#F2D935",
}
LOOK_TOKENS = {"struct.accent": "#F2A33A", "bg": "#1B1F3B", "bg-alt": "#232849", "bg-tint": "#2A2550"}

DYNAMIC_TOKEN = "parity.dynamic"  # noqa: S105 - a placeholder: the engine answers the page's timeline request
# The live side loads exactly the stylesheets of web/watch.html (and the Studio's player), in that order: the classic
# player's unscoped rules (e.g. .rt-k, .rt-code) are part of what the live stage must neutralise (§7.4.7b).
LIVE_STYLESHEETS = ("tokens.css", "base.css", "player.css", "panels.css", "dynamic.css")
DYNAMIC_PARITY_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
""" + "".join(f'<link rel="stylesheet" href="/web/css/{name}">' for name in LIVE_STYLESHEETS) + """
<style>html,body{margin:0;width:__W__px;height:__H__px;overflow:hidden;background:#000}
#root{position:fixed;left:0;top:0;width:__W__px;height:__H__px}</style>
<script type="module">
import { createDynamicStage } from '/web/js/dynamic/stage.js';
const tl = await (await fetch('/__parity-dynamic/timeline.json', { cache: 'no-store' })).json();
const stage = createDynamicStage(document.getElementById('root'), tl, { mode: 'live' });
stage.setLayers({ captions: __CAPTIONS__, stage: true });
window.__parity = { stage, timeline: tl };
</script></head><body><div id="root"></div></body></html>
"""

_LIVE_SETTLE_JS = """async (t) => {
  window.__parity.stage.update(t);
  await document.fonts.ready;
  const imgs = [...document.images].filter((i) => i.getAttribute('src'));
  await Promise.all(imgs.map((i) => (i.decode ? i.decode().catch(() => null) : null)));
  if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) await window.MathJax.startup.promise;
  window.__parity.stage.update(t);
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""


def _timing_manifest(sp: Any) -> Any:
    """Narration timings without stored audio (1.6 s per beat): enough to time a timeline for stage-only checks."""
    from aadhi.schemas.manifest import INTER_BEAT_GAP_SECONDS, AssetManifest, BeatAudio, SceneAudio
    from aadhi.schemas.timeline import TimedWord

    speech = 1.6
    audio: dict[str, Any] = {}
    for scene in sp.scenes:
        beats, t = [], 0.0
        for b in scene.beats:
            words = b.narration.split()
            step = speech / max(1, len(words))
            beats.append(BeatAudio(beat_id=b.id, offset=round(t, 3), speech_duration=speech, pause_after=0.0,
                                   spoken_text=b.narration,
                                   words=[TimedWord(text=w, start=round(t + k * step, 3),
                                                    end=round(t + (k + 1) * step, 3)) for k, w in enumerate(words)]))
            t += speech + INTER_BEAT_GAP_SECONDS
        duration = round(t - INTER_BEAT_GAP_SECONDS, 3)
        audio[scene.id] = SceneAudio(scene_id=scene.id, asset_key=f"scene_audio-parity-{scene.id}",
                                     storage_key=f"parity/{scene.id}.wav", mime="audio/wav", duration=duration,
                                     beats=beats, provider="fake", voice="fake")
    return AssetManifest(audio=audio, media={})


def _kinetic_chunks(scene: dict[str, Any], captions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phrase chunks (scene-relative) from the caption cues inside ``scene``, a stand-in for B2's ``kinetic.py``:
    words grouped into chunks of at most 32 characters (R-32, 9:16), each on at most 2 balanced lines, timed in
    proportion to their words."""
    start, end = float(scene["start"]), float(scene["start"]) + float(scene["duration"])
    out = []
    for cue in captions:
        a, b = max(start, float(cue["start"])), min(end, float(cue["end"]))
        words = str(cue["text"]).split()
        if b - a < 0.2 or not words:
            continue
        groups: list[list[str]] = [[]]
        for w in words:
            if groups[-1] and len(" ".join([*groups[-1], w])) > 32:
                groups.append([])
            groups[-1].append(w)
        step = (b - a) / len(words)
        k = 0
        for group in groups:
            text = " ".join(group)
            lines = [text]
            if len(text) > 16 and len(group) > 1:  # two balanced lines
                cut = min(range(1, len(group)), key=lambda i: abs(len(" ".join(group[:i])) - len(" ".join(group[i:]))))
                lines = [" ".join(group[:cut]), " ".join(group[cut:])]
            c0, c1 = a + k * step, a + (k + len(group)) * step
            out.append({"start": round(c0 - start, 3), "end": round(c1 - start, 3),
                        "words": [{"text": w, "start": round(c0 - start + j * step, 3),
                                   "end": round(c0 - start + (j + 1) * step, 3)} for j, w in enumerate(group)],
                        "lines": lines})
            k += len(group)
    return out


# Rich-lite markup on the Dynamic fixture's board (a [[keyword]], **bold** and `code`): the live player's classic CSS
# styles these classes, so parity must show the stage neutralises it. Classic parity keeps SCREENPLAY's plain text.
DYNAMIC_RICH_TEXT = {"b2": "Double the [[voltage]], **double** the current `I`"}
LIGHT_FAMILY = "paper_ink"  # §4.7: the light R1 family the light parity case uses


def _family_tokens(family: str) -> tuple[dict[str, str], dict[str, str]]:
    """``(content tokens, look tokens)`` of the fixture: the dark defaults, or a light R1 family's resolved tokens."""
    if family == "dark":
        return dict(CONTENT_TOKENS), dict(LOOK_TOKENS)
    from aadhi.compose.presentation import FAMILIES, content_tokens, structure_tokens

    fam = FAMILIES[family]
    return content_tokens(fam.mode), structure_tokens(fam, fam.accent[0])


def dynamic_timeline(fmt: str = "landscape", *, manifest: Any = None, settings: Any = None,
                     family: str = "dark") -> Any:
    """The fixture lecture as a Dynamic timeline: a synthetic ``presentation`` (§6.7) and per-scene looks
    (``board_generic``; backgrounds alternate ``mesh_drift`` / ``colour_block``; the first scene cuts in, the others
    crossfade 0.4 s); 9:16 scenes carry phrase chunks; one board item carries rich-lite markup
    (:data:`DYNAMIC_RICH_TEXT`). ``family`` is "dark" (the default tokens) or an R1 family id such as
    :data:`LIGHT_FAMILY`. ``manifest`` defaults to timings without stored audio."""
    from aadhi.compose.base import stage_size
    from aadhi.compose.timeline import build_timeline
    from aadhi.config import Settings
    from aadhi.schemas.screenplay import Screenplay
    from aadhi.schemas.timeline import Timeline

    sp = Screenplay.model_validate(SCREENPLAY)
    settings = settings or Settings(_env_file=None)
    tl = build_timeline(sp, manifest if manifest is not None else _timing_manifest(sp), settings=settings,
                        version_id=None, revision=1, include_intro=False)
    data = tl.model_dump(mode="json")
    width, height = stage_size(fmt)
    portrait = fmt == "portrait"
    data.update(width=width, height=height, fps=DYNAMIC_FPS)
    content, look_tokens = _family_tokens(family)
    for scene in data["scenes"]:
        for item in scene.get("board") or []:
            if item.get("id") in DYNAMIC_RICH_TEXT:
                item["text"] = DYNAMIC_RICH_TEXT[item["id"]]
    data["presentation"] = {
        "style": "dynamic", "format": fmt, "content": content,
        "fonts": {"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, "personality": "crisp",
        "caption_style": "bold_outline" if portrait else "clean_pill", "cue_term_style": "underline",
        "captions_burn": True, "sfx": False, "motion": "standard", "generated_seconds": data["total_duration"],
        "engine_version": 1,
    }
    for i, scene in enumerate(data["scenes"]):
        crossfade = i > 0
        scene["look"] = {
            "template": "board_generic", "tokens": dict(look_tokens),
            "background": {"preset": "mesh_drift" if i % 2 == 0 else "colour_block", "params": {}, "seed": 1000 + i,
                           "frozen_at": None},
            "transition_in": {"kind": "crossfade" if crossfade else "cut", "seconds": 0.4 if crossfade else 0.0},
        }
        if portrait:
            scene["kinetic_captions"] = _kinetic_chunks(scene, data.get("captions") or [])
    return Timeline.model_validate(data)


def ssim(a: Any, b: Any) -> float:
    """Mean SSIM (Wang et al. 2004) of two equal-size RGB (h, w, 3) or gray (h, w) uint8 images, on BT.601 luma,
    with an 11x11 Gaussian window (sigma 1.5) over the valid region."""
    import numpy as np
    from numpy.lib.stride_tricks import sliding_window_view

    def luma(img: Any) -> Any:
        arr = np.asarray(img, dtype=np.float64)
        return arr if arr.ndim == 2 else arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114

    x, y = luma(a), luma(b)
    if x.shape != y.shape:
        raise ValueError(f"image sizes differ: {x.shape} vs {y.shape}")
    radius, sigma = 5, 1.5
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()

    def blur(img: Any) -> Any:  # separable, "valid" region: (h - 10, w - 10)
        rows = sliding_window_view(img, 2 * radius + 1, axis=0) @ k
        return sliding_window_view(rows, 2 * radius + 1, axis=1) @ k

    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = blur(x), blur(y)
    sxx, syy, sxy = blur(x * x) - mx * mx, blur(y * y) - my * my, blur(x * y) - mx * my
    m = ((2 * mx * my + c1) * (2 * sxy + c2)) / ((mx * mx + my * my + c1) * (sxx + syy + c2))
    return float(m.mean())


def dynamic_sample_frames(frames: int, *, samples: int = DYNAMIC_SAMPLES, seed: int = DYNAMIC_SEED) -> list[int]:
    """``samples`` distinct frame indices in ``[0, frames)``, seeded (the same instants on every run)."""
    import random

    rng = random.Random(seed)
    return sorted(rng.sample(range(frames), min(samples, frames)))


async def live_stage_shots(base: str, timeline: dict[str, Any], frames: list[int], out_dir: Path, *,
                           captions: bool = True) -> list[Path]:
    """Screenshots of a live-mode stage (``createDynamicStage(..., {mode: 'live'})`` from ``base``) at each frame,
    with the caption layer on or off as in the MP4 (burned captions are drawn by the stage)."""
    from playwright.async_api import async_playwright

    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    width, height, fps = int(timeline["width"]), int(timeline["height"]), int(timeline.get("fps") or DYNAMIC_FPS)
    page_url = f"{base}/__parity-dynamic"
    html = (DYNAMIC_PARITY_PAGE.replace("__W__", str(width)).replace("__H__", str(height))
            .replace("__CAPTIONS__", "true" if captions else "false"))
    body = json.dumps(timeline).encode("utf-8")
    shots: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--force-color-profile=srgb", "--hide-scrollbars",
                                                                "--font-render-hinting=none", "--disable-lcd-text",
                                                                "--disable-gpu"])
        try:
            context = await browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1,
                                                color_scheme="dark")

            async def only_local(route: Any) -> None:
                if route.request.url.startswith(base):
                    await route.continue_()
                else:
                    await route.abort()

            await context.route("**/*", only_local)
            await context.route(page_url, lambda route: route.fulfill(status=200, content_type="text/html", body=html))
            await context.route(f"{page_url}/timeline.json", lambda route: route.fulfill(
                status=200, content_type="application/json", body=body))
            page = await context.new_page()
            await page.goto(page_url, wait_until="domcontentloaded")
            await page.wait_for_function("() => !!window.__parity", timeout=60_000)
            for n, frame in enumerate(frames):
                await page.evaluate(_LIVE_SETTLE_JS, frame / fps)
                path = out_dir / f"live_{n:02d}_f{frame:05d}.png"
                await page.screenshot(path=str(path), type="png")
                shots.append(path)
        finally:
            await browser.close()
    return shots


async def render_mode_shots(settings: Any, base: str, timeline: dict[str, Any], frames: list[int], out_dir: Path, *,
                            captions: bool = True, token: str = DYNAMIC_TOKEN) -> list[tuple[Path, Path]]:
    """The render page's own frames (``/render-dynamic`` through ``aadhi.compose.dynamic.stage_page``, the worker's
    capture path) at each frame: ``(lossless PNG, JPEG q92 as captured for the MP4)`` per frame."""
    from aadhi.compose.dynamic.stage_page import open_stage

    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    out: list[tuple[Path, Path]] = []
    size = (int(timeline["width"]), int(timeline["height"]))
    async with open_stage(settings, size=size, token=token, base_url=base, served_timeline=timeline) as page:
        await page.set_layers(captions=captions, stage=True)
        for frame in frames:
            await page.seek(frame)
            png, jpg = out_dir / f"render_f{frame:05d}.png", out_dir / f"render_f{frame:05d}.jpg"
            png.write_bytes(await page.capture_png())
            jpg.write_bytes(await page.capture_jpeg())
            out.append((png, jpg))
    return out


def compare_dynamic(vqa: Any, mp4: Path, shots: list[Path], frames: list[int], fps: int,
                    render_shots: list[tuple[Path, Path]] | None = None) -> list[dict[str, Any]]:
    """SSIM of each live screenshot against the MP4 frame of the same index. With ``render_shots`` (the render
    page's lossless PNG and captured JPEG of the same frames) each sample also splits the difference:
    ``stage_ssim`` = live vs render-mode PNG (the compositor's own parity, no codec), ``capture_ssim`` = render PNG vs
    its JPEG q92 capture, ``encode_ssim`` = that JPEG vs the MP4 frame (x264 alone)."""
    import numpy as np
    from PIL import Image

    def fit(img: Any, like: Any) -> Any:
        if img.shape == like.shape:
            return img
        return np.asarray(Image.fromarray(img).resize((like.shape[1], like.shape[0]), Image.Resampling.LANCZOS))

    out = []
    for n, (frame, shot) in enumerate(zip(frames, shots, strict=True)):
        live = vqa.image_rgb(shot)
        export = vqa.frame_rgb(mp4, frame, fps)
        row: dict[str, Any] = {"frame": frame, "t": round(frame / fps, 4), "ssim": round(ssim(fit(live, export),
                                                                                            export), 4)}
        if render_shots is not None:
            png, jpg = (vqa.image_rgb(p) for p in render_shots[n])
            row["stage_ssim"] = round(ssim(live, png), 4)
            row["capture_ssim"] = round(ssim(png, jpg), 4)
            row["encode_ssim"] = round(ssim(fit(jpg, export), export), 4)
        out.append(row)
    return out


class EvalJobContext:
    """The ``JobContext`` the motion engine gets in this eval: no queue, no lease, no database (narration comes from
    ``local_files``); warnings go to stderr."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.assets = None
        self.storage = None
        self.job_id, self.kind, self.user_id, self.project_id, self.version_id = 0, "render_video", None, None, None
        self.payload: dict[str, Any] = {}
        self.attempt, self.worker_id = 1, "parity-eval"
        self.events: list[dict[str, Any]] = []

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        self.events.append({"type": "progress", "stage": stage, "progress": fraction, "message": message})

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.events.append({"type": "log", "level": level, "message": message})
        if level in ("warning", "error"):
            print(f"[{level}] {message}", file=sys.stderr)

    def check_cancelled(self) -> None:
        return None

    async def flush(self) -> None:
        return None


def dynamic_narration(timeline: Any, out_dir: Path) -> dict[str, Path]:
    """A WAV (a tone as long as the scene's narration) for every scene's narration key: the ``local_files`` of an
    offline motion render."""
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Path] = {}
    for n, scene in enumerate(timeline.scenes):
        key = scene.audio_asset_key
        if key and key not in files:
            path = out_dir / f"narration_{n:03d}.wav"
            path.write_bytes(_wav(max(0.1, scene.audio_duration or scene.duration), 220 + 60 * n))
            files[key] = path
    return files


def render_with_motion_engine(settings: Any, timeline: Any, out: Path, *, work: Path, burn_captions: bool = True,
                              config: Any = None) -> Any:
    """Render ``timeline`` with the motion engine (``aadhi.compose.motion.render_motion``) into ``out``; returns its
    ``MotionResult``. The page is ``/render-dynamic`` at the settings' render origin (the caller serves the app);
    the engine answers the page's timeline request with ``timeline`` itself. ``config``: a ``MotionConfig`` (tests
    use small chunks to get chunk joins)."""
    from aadhi.compose.motion import render_motion  # the engine (B1); ImportError in a build without it

    ctx = EvalJobContext(settings)
    return asyncio.run(render_motion(
        ctx, timeline, out, settings, burn_captions=burn_captions, token=DYNAMIC_TOKEN,
        served=timeline.model_dump(mode="json"), work_dir=work / "motion-work",
        local_files=dynamic_narration(timeline, work / "narration"), config=config))


def run_dynamic_parity(settings: Any, work: Path, *, fmt: str = "landscape", threshold: float = SSIM_THRESHOLD,
                       samples: int = DYNAMIC_SAMPLES, seed: int = DYNAMIC_SEED, timeline: Any = None,
                       render: Any = None, base: str | None = None) -> dict[str, Any]:
    """Render the Dynamic fixture (or ``timeline``) and compare the live stage with the MP4 (see above).

    ``render(timeline, out_path)`` and ``base`` (a server for ``/web/js/dynamic/stage.js``) replace the motion engine
    and the app server (tests); by default the app is served on ``settings.base_url`` (``live_server``) and the
    timeline is rendered by :func:`render_with_motion_engine`. Captions are burned as the presentation says, and the
    live stage draws them the same way."""
    vqa = load_video_qa()
    work.mkdir(parents=True, exist_ok=True)
    tl = timeline if timeline is not None else dynamic_timeline(fmt, settings=settings)
    fps = int(tl.fps)
    from aadhi.compose.qa import expected_frame_count

    total = expected_frame_count(tl.total_duration, fps)
    frames = dynamic_sample_frames(total, samples=samples, seed=seed)
    burn = bool(tl.presentation.captions_burn) if tl.presentation is not None else False
    mp4 = work / "dynamic.mp4"
    served = tl.model_dump(mode="json")
    motion: dict[str, Any] | None = None
    if render is not None and base is not None:
        render(tl, mp4)
        shots = asyncio.run(live_stage_shots(base, served, frames, work / "live", captions=burn))
        renders = asyncio.run(render_mode_shots(settings, base, served, frames, work / "render", captions=burn))
    else:
        with live_server(settings) as app_base:
            if render is not None:
                render(tl, mp4)
            else:
                result = render_with_motion_engine(settings, tl, mp4, work=work, burn_captions=burn)
                motion = {"qa": result.container.summary(), "engine": result.summary()}
            shots = asyncio.run(live_stage_shots(app_base, served, frames, work / "live", captions=burn))
            renders = asyncio.run(render_mode_shots(settings, app_base, served, frames, work / "render",
                                                    captions=burn))
    results = compare_dynamic(vqa, mp4, shots, frames, fps, renders)
    worst = min((r["ssim"] for r in results), default=1.0)

    def lowest(key: str) -> float | None:
        values = [r[key] for r in results if key in r]
        return min(values) if values else None

    report = {"format": tl.presentation.format if tl.presentation else fmt, "threshold": threshold,
              "ok": worst >= threshold, "worst_ssim": worst,
              "mean_ssim": round(sum(r["ssim"] for r in results) / max(1, len(results)), 4),
              "worst_stage_ssim": lowest("stage_ssim"), "worst_capture_ssim": lowest("capture_ssim"),
              "worst_encode_ssim": lowest("encode_ssim"),
              "samples": results, "frames": total, "mp4": str(mp4), "cfr": vqa.check_cfr(mp4, fps)}
    if motion is not None:
        report["motion"] = motion
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="workspace directory (default: a temporary one)")
    parser.add_argument("--keep", action="store_true", help="keep the workspace (MP4, screenshots, report)")
    parser.add_argument("--threshold", type=float, default=None,
                        help=f"classic: max mean difference (default {DEFAULT_THRESHOLD}); "
                             f"--dynamic: min SSIM (default {SSIM_THRESHOLD})")
    parser.add_argument("--dynamic", choices=("landscape", "portrait"),
                        help="compare a Dynamic (motion engine) render with the live stage instead")
    args = parser.parse_args(argv)
    missing = tools_missing()
    if missing:
        print(json.dumps({"ok": False, "skipped": missing}))
        return 2
    work = (args.out or Path(tempfile.mkdtemp(prefix="aadhi-parity-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    configure_env(work, free_port(), dynamic=bool(args.dynamic))
    from aadhi import config, db, models  # noqa: F401  (models registers the tables)
    from aadhi.storage import reset_storage_cache

    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()
    settings = config.get_settings()
    db.Base.metadata.create_all(db.get_engine())
    try:
        if args.dynamic:
            try:
                report = run_dynamic_parity(settings, work, fmt=args.dynamic,
                                            threshold=SSIM_THRESHOLD if args.threshold is None else args.threshold)
            except ImportError as exc:  # the motion engine is not installed in this build
                print(json.dumps({"ok": False, "skipped": f"motion engine unavailable: {exc}"}))
                return 2
            (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2))
            return 0 if report["ok"] else 1
        report = run_parity(settings, work, threshold=DEFAULT_THRESHOLD if args.threshold is None else args.threshold)
        (work / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    finally:
        db.get_engine().dispose()
        if not args.keep and args.out is None:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
