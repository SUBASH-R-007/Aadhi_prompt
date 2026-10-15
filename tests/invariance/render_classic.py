"""Render a classic invariance fixture to MP4 with the code of one checkout, and fingerprint the result.

Half of the differential pixel test (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6): ``differential.py`` runs this
script once with the merge base's code and once with HEAD's, in the same container (or on the same machine), and
``pixeldiff.py`` compares the two outputs. It is a standalone script, never imported by the code under test: the
code root given by ``--code-root`` is put first on ``sys.path`` and ``aadhi`` is imported from there (checked).

The render goes through the production path: the fixture is seeded as a project version (synthetic narration and
media from ``classic_fixture.py``), the app is served by uvicorn on a free loopback port, and the real
``render_video`` job handler screenshots ``/render-frame`` with Chromium and composites with ffmpeg. Offline: fake
providers, every API key blank, the repository's ``.env`` is ignored; the database, storage and render workspace
live in a temporary work directory (``--work``), the results in ``--out``.

    python tests/invariance/render_classic.py --code-root . --out OUT
    python tests/invariance/render_classic.py --code-root ../base --media OUT/media --out OUT/base

Writes ``OUT/render.mp4``, ``OUT/report.json`` (decoded frame and audio hashes, the hash of every state
screenshot, settings, timings) and ``OUT/capture.json`` (the states the render page reported, see
``goldens/<fixture>/capture.json``); with ``--keep-states`` also the screenshots in ``OUT/states/``.

Exit codes: 0 rendered, 1 the render failed, 2 tools missing (ffmpeg / Playwright Chromium).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FRAME_HASH_CHARS = 32  # sha256 prefix per decoded frame (128 bits)


def _load_fixture_module() -> Any:
    try:
        from . import classic_fixture  # type: ignore[import-not-found]
    except ImportError:  # run as a script: this directory is sys.path[0]
        import classic_fixture  # type: ignore[import-not-found,no-redef]
    return classic_fixture


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def configure_env(work: Path, port: int, *, preset: str, width: int | None, height: int | None, fps: int | None,
                  crf: int | None) -> None:
    """Environment of a self-contained render (before ``aadhi`` is imported)."""
    tmp = work / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = {
        "APP_ENV": "test", "DATA_DIR": str(work / "data"),
        "DATABASE_URL": f"sqlite:///{(work / 'render.db').as_posix()}", "BASE_URL": f"http://127.0.0.1:{port}",
        "WORKER_MODE": "external", "STORAGE_BACKEND": "local", "STORAGE_LOCAL_DIR": str(work / "storage"),
        "LLM_PROVIDER": "fake", "TTS_PROVIDER": "fake", "IMAGE_PROVIDER": "fake", "VIDEO_PROVIDER": "fake",
        "IMAGE_FALLBACK_PROVIDERS": "", "VIDEO_FALLBACK_PROVIDERS": "", "VIDEO_PROVIDERS": "", "DYNAMIC_FEATURES": "",
        "GEMINI_API_KEY": "", "GEMINI_API_KEYS": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "ELEVENLABS_API_KEY": "", "GIPHY_API_KEY": "", "S3_ACCESS_KEY_ID": "", "S3_SECRET_ACCESS_KEY": "",
        "STORED_API_KEYS_ENABLED": "false", "CREDENTIALS_ENCRYPTION_KEY": "",
        "JWT_SECRET": secrets.token_urlsafe(48), "ADMIN_USERNAME": "invariance-admin",
        "ADMIN_PASSWORD": secrets.token_urlsafe(24), "MANIM_SANDBOX": "disabled", "CORS_ORIGINS": "",
        "RENDER_WORK_DIR": str(work / "render-work"), "RENDER_BASE_URL": "", "RENDER_MIN_FREE_GB": "0",
        "RENDER_PRESET": preset, "SCRATCH_DIR": str(work / "scratch"),
        "TEMP": str(tmp), "TMP": str(tmp), "TMPDIR": str(tmp),
    }
    for name, value in (("RENDER_WIDTH", width), ("RENDER_HEIGHT", height), ("RENDER_FPS", fps), ("RENDER_CRF", crf)):
        if value is not None:
            env[name] = str(value)
    os.environ.update(env)


def import_code(code_root: Path) -> Any:
    """Import ``aadhi`` from ``code_root`` (first on sys.path) with the repository's ``.env`` switched off."""
    sys.path.insert(0, str(code_root))
    for name in [m for m in sys.modules if m == "aadhi" or m.startswith("aadhi.")]:
        del sys.modules[name]
    import aadhi
    from aadhi import config

    where = Path(aadhi.__file__).resolve()
    if code_root not in where.parents:
        raise SystemExit(f"aadhi was imported from {where}, not from {code_root}")
    config.Settings.model_config["env_file"] = None  # a developer .env must not change either render
    return config


# --- job context ------------------------------------------------------------------------------------------------


@dataclass
class RenderContext:
    """The ``JobContext`` protocol, in memory (one render, no lease, no budget)."""

    settings: Any
    assets: Any
    version_id: int
    project_id: int
    payload: dict[str, Any]
    job_id: int = 0
    kind: str = "render_video"
    user_id: int | None = None
    attempt: int = 1
    worker_id: str = "invariance"
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def storage(self) -> Any:
        return self.assets.storage

    def session(self) -> Any:
        from aadhi.db import session_scope

        return session_scope()

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        self.events.append({"type": "progress", "stage": stage, "progress": fraction, "message": message})

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        self.events.append({"type": "log", "level": level, "message": message})

    def check_cancelled(self) -> None:
        return None

    def record_usage(self, usage: Any) -> float:
        return 0.0

    def ensure_budget(self, estimated_cost_usd: float, provider: str | None = None) -> None:
        return None

    async def flush(self) -> None:
        return None

    def assert_lease(self, db: Any) -> None:
        return None


# --- seeding ----------------------------------------------------------------------------------------------------


def seed(settings: Any, fx: Any, fixture: str, media_dir: Path, ffmpeg: str, max_scenes: int | None,
         burn_captions: bool) -> dict[str, Any]:
    """User, project, built version and a queued render of the fixture; returns ids and the timeline."""
    from aadhi.compose.timeline import build_timeline
    from aadhi.db import get_sessionmaker
    from aadhi.models import Project, ProjectVersion, Render, User
    from aadhi.storage import get_storage
    from aadhi.storage.assets import AssetStore

    store = AssetStore(get_storage(), get_sessionmaker())
    sp = fx.load_screenplay(fixture, max_scenes=max_scenes)
    files = fx.write_media(sp, media_dir, ffmpeg=ffmpeg, fixture=fixture)
    stored = fx.store_assets(sp, store, files, fixture=fixture)
    manifest = fx.synthetic_manifest(sp, stored, fixture=fixture)
    with get_sessionmaker()() as db:
        user = User(username="invariance-teacher", password_hash="x", role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title=sp.session_title or "Invariance", language=sp.language,
                          subject_name=sp.subject_name, next_version_number=2)
        db.add(project)
        db.flush()
        version = ProjectVersion(project_id=project.id, number=1, status="ready", language=sp.language, revision=1,
                                 built_revision=1)
        version.set_screenplay(sp)
        version.set_manifest(manifest)
        version.issues = []
        version.generation_meta = {}
        db.add(version)
        db.flush()
        timeline = build_timeline(sp, manifest, settings=settings, version_id=version.id, revision=1)
        version.set_timeline(timeline)
        project.current_version_id = version.id
        render = Render(version_id=version.id, status="queued", options={"burn_captions": burn_captions})
        db.add(render)
        db.commit()
        return {"store": store, "project_id": project.id, "version_id": version.id, "render_id": render.id,
                "timeline": timeline, "scenes": len(sp.scenes), "screenplay": sp}


@contextlib.contextmanager
def live_server(settings: Any) -> Iterator[str]:
    """``create_app(settings)`` served by uvicorn on ``settings.base_url``'s port (no workers)."""
    import uvicorn

    from aadhi.main import create_app

    port = int(settings.base_url.rsplit(":", 1)[1])
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=port, log_level="warning",
                                           access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, name="uvicorn-invariance", daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("the invariance app server did not start")
        time.sleep(0.05)
    try:
        yield settings.base_url.rstrip("/")
    finally:
        server.should_exit = True
        thread.join(timeout=60)


# --- capture recording -------------------------------------------------------------------------------------------


def png_pixels_digest(path: Path) -> str:
    """sha256 of the decoded RGBA pixels (+ size): PNG encoder differences never count, pixels do."""
    from PIL import Image

    with Image.open(path) as im:
        rgba = im.convert("RGBA")
        h = hashlib.sha256(f"{rgba.width}x{rgba.height}|".encode())
        h.update(rgba.tobytes())
    return h.hexdigest()


def _rect(r: Any) -> Any:
    return {k: round(float(v), 2) for k, v in r.items()} if isinstance(r, dict) else None


def capture_document(result: Any) -> dict[str, Any]:
    """The logical part of a ``CaptureResult``: states, keys, reported rects and notices (no pixels)."""
    scenes = {}
    for index, shots in sorted(result.scenes.items()):
        scenes[str(index)] = [{"t": round(float(s.t), 6), "key": s.key, "png": Path(s.png_path).name,
                               "media_rect": _rect(s.media_rect), "panel_media_rect": _rect(s.panel_media_rect),
                               "media_fit": s.media_fit, "notices": list(s.notices)} for s in shots]
    return {"scenes": scenes,
            "intro": [{"t": round(float(s.t), 6), "png": Path(s.png_path).name} for s in result.intro],
            "blocked_media": sorted(set(result.blocked_media)),
            "websocket_attempts": sorted(set(result.websocket_attempts))}


def dump_capture(doc: dict[str, Any]) -> str:
    """The capture document as JSON with one state per line (readable golden diffs)."""
    if not doc:
        return "{}\n"
    lines = ["{", ' "scenes": {']
    scenes = list(doc["scenes"].items())
    for n, (index, states) in enumerate(scenes):
        lines.append(f"  {json.dumps(index)}: [")
        lines += [f"   {json.dumps(s, sort_keys=True)}{',' if k < len(states) - 1 else ''}"
                  for k, s in enumerate(states)]
        lines.append("  ]" + ("," if n < len(scenes) - 1 else ""))
    lines.append(" },")
    rest = [k for k in doc if k != "scenes"]
    for n, key in enumerate(rest):
        lines.append(f" {json.dumps(key)}: {json.dumps(doc[key], sort_keys=True)}{',' if n < len(rest) - 1 else ''}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def install_capture_recorder(video_mod: Any, sink: dict[str, Any], states_dir: Path | None, *,
                             stop: bool = False) -> None:
    """Wrap the render job's screenshot step: keep the capture document, the pixel digest of every screenshot
    and (``states_dir``) a copy of the screenshots, before the job normalises and deletes them. ``stop`` ends
    the job there (:class:`CaptureDone`)."""
    original = video_mod.capture_render_frames

    async def recording(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        paths = sorted({Path(s.png_path) for shots in result.scenes.values() for s in shots}
                       | {Path(s.png_path) for s in result.intro})
        sink["capture"] = capture_document(result)
        sink["states"] = {p.name: png_pixels_digest(p) for p in paths}
        if states_dir is not None:
            states_dir.mkdir(parents=True, exist_ok=True)
            for p in paths:
                shutil.copyfile(p, states_dir / p.name)
        if stop:
            raise CaptureDone()
        return result

    video_mod.capture_render_frames = recording


# --- fingerprints ------------------------------------------------------------------------------------------------


def probe_size(ffprobe: str, mp4: Path) -> tuple[int, int, str]:
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,r_frame_rate", "-of", "json", str(mp4)],
                         capture_output=True, check=True, timeout=120, stdin=subprocess.DEVNULL).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"]), str(s["r_frame_rate"])


def decoded_frames(ffmpeg: str, mp4: Path, width: int, height: int) -> Iterator[bytes]:
    """Every decoded video frame as packed RGB24 bytes (ffmpeg's default conversion, the same on both sides)."""
    size = width * height * 3
    proc = subprocess.Popen([ffmpeg, "-v", "error", "-nostdin", "-i", str(mp4), "-map", "0:v:0", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    assert proc.stdout is not None
    complete = False
    try:
        while True:
            buf = proc.stdout.read(size)
            if not buf:
                complete = True
                break
            if len(buf) != size:
                raise RuntimeError(f"truncated frame from {mp4.name}")
            yield buf
    finally:
        if not complete:  # the caller stopped early (or failed): no more frames are wanted
            proc.kill()
        proc.stdout.close()
        if proc.wait(timeout=120) != 0 and complete:
            raise RuntimeError(f"ffmpeg could not decode {mp4.name}")


def decoded_audio(ffmpeg: str, mp4: Path) -> bytes:
    """The first audio stream as 48 kHz stereo s16le (empty without audio)."""
    proc = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(mp4), "-map", "0:a:0?", "-f", "s16le",
                           "-ac", "2", "-ar", "48000", "-"], capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode the audio of {mp4.name}")
    return proc.stdout


def frame_hashes(ffmpeg: str, mp4: Path) -> list[str]:
    """sha256 of every decoded video frame (its native planes), computed by ffmpeg's ``framehash`` muxer."""
    out = subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-i", str(mp4), "-map", "0:v:0", "-f", "framehash",
                          "-hash", "sha256", "-"], capture_output=True, text=True, timeout=1800,
                         stdin=subprocess.DEVNULL)
    if out.returncode != 0:
        raise RuntimeError(f"ffmpeg could not hash the frames of {mp4.name}: {out.stderr[-300:]}")
    return [line.rsplit(",", 1)[1].strip()[:FRAME_HASH_CHARS] for line in out.stdout.splitlines()
            if line.strip() and not line.startswith("#")]


def fingerprint(ffmpeg: str, ffprobe: str, mp4: Path) -> dict[str, Any]:
    width, height, rate = probe_size(ffprobe, mp4)
    frames = frame_hashes(ffmpeg, mp4)
    pcm = decoded_audio(ffmpeg, mp4)
    return {"width": width, "height": height, "rate": rate, "frames": len(frames), "frame_sha256": frames,
            "audio_samples": len(pcm) // 4, "audio_sha256": hashlib.sha256(pcm).hexdigest()}


# --- main --------------------------------------------------------------------------------------------------------


def git_describe(code_root: Path) -> str | None:
    """HEAD commit of ``code_root`` when it is a git checkout (read-only), else None."""
    try:
        out = subprocess.run(["git", "-C", str(code_root), "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=30, stdin=subprocess.DEVNULL)
    except OSError:
        return None
    return out.stdout.strip() or None if out.returncode == 0 else None


class CaptureDone(Exception):
    """Ends a ``--capture-only`` run right after the screenshot step."""


def render(args: argparse.Namespace) -> dict[str, Any]:
    out: Path = args.out
    # A short path of its own (storage keys are long; Windows paths stop at 260 characters).
    work = args.work or Path(tempfile.mkdtemp(prefix="aadhi-inv-"))
    work.mkdir(parents=True, exist_ok=True)
    configure_env(work, free_port(), preset=args.preset, width=args.width, height=args.height, fps=args.fps,
                  crf=args.crf)
    config = import_code(args.code_root)
    fx = _load_fixture_module()
    from aadhi import db, models  # noqa: F401  (models registers the tables)
    from aadhi.compose import video as video_mod
    from aadhi.compose.capabilities import render_capabilities, resolve_tool
    from aadhi.compose.timeline import build_timeline
    from aadhi.storage import reset_storage_cache

    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()
    settings = config.get_settings()
    caps = render_capabilities(settings, browser=True, force=True)
    if not caps.ok:
        print(json.dumps({"ok": False, "skipped": " ".join(caps.reasons)}))
        raise SystemExit(2)
    ffmpeg = resolve_tool(settings.ffmpeg_path) or settings.ffmpeg_path
    ffprobe = resolve_tool(settings.ffprobe_path) or settings.ffprobe_path
    vendor = Path(settings.web_dir) / "vendor" / "manifest.json"
    if not vendor.is_file():
        raise SystemExit(f"{vendor} is missing: run `npm ci` in {args.code_root} (or copy web/vendor)")
    db.Base.metadata.create_all(db.get_engine())
    t0 = time.monotonic()
    rows = seed(settings, fx, args.fixture, args.media or (work / "media"), ffmpeg, args.max_scenes,
                args.burn_captions)
    sink: dict[str, Any] = {}
    install_capture_recorder(video_mod, sink, out / "states" if args.keep_states else None,
                             stop=args.capture_only)
    ctx = RenderContext(settings=settings, assets=rows["store"], version_id=rows["version_id"],
                        project_id=rows["project_id"],
                        payload={"render_id": rows["render_id"], "burn_captions": args.burn_captions,
                                 "include_intro": True})
    fp: dict[str, Any] = {}
    with live_server(settings):
        t1 = time.monotonic()
        try:
            result = asyncio.run(video_mod.render_video(ctx))
        except CaptureDone:
            result = None
        t2 = time.monotonic()
    if result is not None:
        asset = rows["store"].get(result["video_asset_key"])
        mp4 = out / "render.mp4"
        shutil.copyfile(rows["store"].storage.local_path(asset.storage_key), mp4)
        fp = fingerprint(ffmpeg, ffprobe, mp4)
    # The timeline as the goldens build it (synthetic storage keys: the store's names are random per run).
    sp = rows["screenplay"]
    golden_tl = build_timeline(sp, fx.synthetic_manifest(sp, fixture=args.fixture), settings=settings, version_id=1,
                               revision=1)
    timeline = rows["timeline"]
    capture_text = dump_capture(sink.get("capture", {}))
    report = {
        "ok": True, "fixture": args.fixture, "code_root": str(args.code_root), "commit": git_describe(args.code_root),
        "capture_only": bool(args.capture_only), "max_scenes": args.max_scenes, "scenes": rows["scenes"],
        "settings": {"width": settings.render_width, "height": settings.render_height, "fps": settings.render_fps,
                     "preset": settings.render_preset, "crf": settings.render_crf,
                     "burn_captions": args.burn_captions},
        "timeline_sha256": hashlib.sha256(golden_tl.model_dump_json().encode("utf-8")).hexdigest(),
        "capture_sha256": hashlib.sha256(capture_text.encode("utf-8")).hexdigest(),
        "total_duration": timeline.total_duration,
        "intro_duration": timeline.intro.duration if timeline.intro is not None else 0.0,
        "scene_starts": [{"scene_id": s.scene_id, "start": s.start, "duration": s.duration} for s in timeline.scenes],
        "states": sink.get("states", {}),
        "warnings": [e["message"] for e in ctx.events if e["type"] == "log" and e["level"] == "warning"],
        "seconds": {"seed": round(t1 - t0, 1), "render": round(t2 - t1, 1),
                    "fingerprint": round(time.monotonic() - t2, 1)},
        **fp,
    }
    (out / "capture.json").write_text(capture_text, encoding="utf-8", newline="\n")  # committed as a golden: LF
    db.get_engine().dispose()
    if args.keep_work:
        report["work"] = str(work)
    else:
        shutil.rmtree(work, ignore_errors=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return report


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    fx = _load_fixture_module()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--code-root", type=Path, required=True, help="checkout whose aadhi/ and web/ render")
    p.add_argument("--out", type=Path, required=True, help="output directory (created)")
    p.add_argument("--fixture", default=fx.RENDER_FIXTURE, choices=sorted(fx.FIXTURES))
    p.add_argument("--media", type=Path, help="shared directory for the synthetic media (default: in --work)")
    p.add_argument("--work", type=Path, help="work directory: database, storage, render workspace "
                                             "(default: a new temporary directory, removed afterwards)")
    p.add_argument("--max-scenes", type=int, help="render only the first N scenes (quick local checks)")
    p.add_argument("--preset", default="ultrafast", help="x264 preset of both renders (default ultrafast)")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--fps", type=int)
    p.add_argument("--crf", type=int)
    p.add_argument("--burn-captions", action="store_true", help="burn the captions in (needs libass)")
    p.add_argument("--keep-states", action="store_true", help="keep the state screenshots in OUT/states")
    p.add_argument("--keep-work", action="store_true", help="keep the work directory")
    p.add_argument("--capture-only", action="store_true",
                   help="stop after the screenshot step: capture.json and the state digests, no MP4")
    args = p.parse_args(argv)
    args.code_root = args.code_root.resolve()
    args.out = args.out.resolve()
    for name in ("media", "work"):
        if getattr(args, name) is not None:
            setattr(args, name, getattr(args, name).resolve())
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    for name in ("report.json", "capture.json", "render.mp4"):  # never leave an earlier run's results behind
        (args.out / name).unlink(missing_ok=True)
    try:
        report = render(args)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - reported as JSON for the orchestrator
        (args.out / "report.json").write_text(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                                                         indent=1) + "\n", encoding="utf-8")
        raise
    print(json.dumps({k: report.get(k) for k in ("ok", "fixture", "frames", "audio_sha256", "capture_sha256",
                                                 "seconds")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
