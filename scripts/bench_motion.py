"""Benchmark the motion engine (DYNAMIC_VIDEO_DESIGN §7.10, the B1 acceptance benchmark).

    python scripts/bench_motion.py                                # 1 minute of the showcase, 16:9, real stage
    python scripts/bench_motion.py --minutes 5 --json bench.json  # the acceptance run (5-minute 16:9 lesson)
    python scripts/bench_motion.py --format portrait --minutes 3.5
    python scripts/bench_motion.py --workers 4 --preset faster --crf 17
    python scripts/bench_motion.py --stage stub                   # engine + capture only (the tests' stub page)
    python scripts/bench_motion.py --disable-frame-rate-limit     # the open --disable-frame-rate-limit question
    python scripts/bench_motion.py --driver-per-worker            # one Playwright driver per worker (memory check)

What it renders: the showcase lecture (``tests/core/fixtures/showcase_ohms_law.json``) with a synthetic Dynamic
presentation, timed by ``preview_timeline`` (estimated timings, no TTS) and repeated until ``--minutes`` is
reached; every scene gets a narration tone and every AI-video / simulation scene a 1080p 30 fps test clip, so the
media extraction path runs. ``--timeline FILE`` renders a stored Dynamic timeline instead (its media keys must be
mapped with ``--media KEY=FILE``).

How: the production capture path. ``engine.render_motion`` with the configured workers and CPU slots, one headless
shell per worker with route interception (the timeline served by the worker, ``/__frames/...`` from its
workspace), the page's ``seek()`` with its ``requestAnimationFrame`` wait, CDP JPEG capture, the x264 pipe with
``MOTION_X264_PRESET`` / ``MOTION_X264_CRF``, MPEG-TS chunks, concat, the final mux and the container checks. The
page is served by a small static server from ``web/`` (no API, database or CSP; the same files).

What it records (printed, and written as JSON with ``--json``): fps per worker and aggregate, end-to-end seconds
with the phases (boot, capture, mux, checks), peak memory of the browsers, encoders and drivers, and a projection
of the 5-minute 16:9 lesson (9,000 frames) and the 3.5-minute reel (6,300 frames) at the measured rates. Run it in
the production render image under the ``motion`` compose profile for the numbers that set P1 and Q7::

    docker compose --profile motion run --rm --no-deps render-worker-motion \\
        python scripts/bench_motion.py --minutes 5 --json /data/app/bench-motion.json

Never touches ``data/`` (work files go to ``--out``, default a temporary directory) and never calls a provider.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import mimetypes
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allow `python scripts/bench_motion.py` from anywhere
    sys.path.insert(0, str(ROOT))

SHOWCASE = ROOT / "tests" / "core" / "fixtures" / "showcase_ohms_law.json"
STUB_DIR = ROOT / "tests" / "compose" / "motion" / "stub_stage"
TOKEN = "bench-motion-token"  # noqa: S105 - the stand-in render token (the worker serves the timeline)
NARRATION_KEY = "scene_audio-bench0000000000000000000000000000000000"
CLIP_SECONDS = 10.0
LESSON_FRAMES = 9000  # 5-minute 16:9 lesson
REEL_FRAMES = 6300  # 3.5-minute 9:16 reel


# ---------------------------------------------------------------------------
# The lecture
# ---------------------------------------------------------------------------


def bench_presentation(fmt: str, *, burn: bool) -> dict[str, Any]:
    """A synthetic ``Screenplay.presentation`` (every required choice made, deterministic)."""
    return {"style": "dynamic", "format": fmt, "look_seed": 20261010, "theme_family": "midnight_indigo",
            "personality": "crisp", "display_font": "outfit", "caption_style": "clean_pill",
            "cue_term_style": "marker", "hook_kind": "question",
            "asset_style": {"family": "midnight_indigo", "personality": "crisp"},
            "voices": {"narrator": "edge-en-in-neerja"}, "music": {"source": "off", "mode": "off"},
            "captions_burn": burn, "sfx": False}


def showcase_timeline(settings: Any, *, fmt: str, burn: bool) -> Any:
    """The showcase as a Dynamic timeline (estimated timings) with the looks the timeline builder resolves."""
    from aadhi.compose.timeline import preview_timeline
    from aadhi.legacy.convert import convert_legacy, is_legacy
    from aadhi.schemas.screenplay import Screenplay

    data = json.loads(SHOWCASE.read_text(encoding="utf-8"))
    sp = convert_legacy(data)[0] if is_legacy(data) else Screenplay.model_validate(data)
    dumped = sp.model_dump(mode="json")
    dumped["presentation"] = bench_presentation(fmt, burn=burn)
    sp = Screenplay.model_validate(dumped)
    tl = preview_timeline(sp, None, settings=settings.model_copy(update={"dynamic_enabled": True}), version_id=1,
                          include_intro=False)
    if tl.presentation is None:
        raise SystemExit("the timeline builder did not produce a Dynamic timeline (is the presentation step in "
                         "aadhi/compose/timeline.py?)")
    return tl


def repeat_to(timeline: Any, seconds: float) -> Any:
    """``timeline`` repeated scene by scene until ``seconds`` (the last repetition cut at a scene boundary)."""
    from aadhi.schemas.timeline import Timeline

    data = timeline.model_dump(mode="json")
    base = list(data["scenes"])
    period = float(data["total_duration"])
    scenes: list[dict[str, Any]] = []
    captions: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    t = 0.0
    rep = 0
    while t < seconds - 1e-6:
        offset = t
        for s in base:
            if t >= seconds - 1e-6:
                break
            c = copy.deepcopy(s)
            if rep:
                c["scene_id"] = f"{s['scene_id']}-r{rep}"
            c["index"] = len(scenes)
            c["start"] = t
            scenes.append(c)
            t += float(s["duration"])
        captions += [{**c, "start": c["start"] + offset, "end": c["end"] + offset} for c in data["captions"]]
        chapters += [{**c, "start": c["start"] + offset} for c in data["chapters"]]
        rep += 1
        if period <= 0:
            break
    data.update(scenes=scenes, total_duration=t, captions=[c for c in captions if c["end"] <= t + 1e-6],
                chapters=[c for c in chapters if c["start"] < t])
    data["presentation"]["generated_seconds"] = round(t, 3)
    return Timeline.model_validate(data)


def with_media(timeline: Any, *, narration: bool, clip: bool) -> tuple[Any, list[str]]:
    """Narration on every scene and the test clip on every video scene; returns the timeline and the clip keys."""
    data = timeline.model_dump(mode="json")
    clip_keys: set[str] = set()
    for s in data["scenes"]:
        if narration:
            s["audio_asset_key"] = NARRATION_KEY
            s["audio_offset"] = min(0.5, s["duration"] / 4)
            s["audio_duration"] = min(1.0, s["duration"] / 2)
        media = s.get("media")
        if media and media.get("kind") == "video" and media.get("asset_key"):
            if clip:
                media.update(duration=CLIP_SECONDS, width=1920, height=1080, src_fps=30.0, render_in_mp4=True)
                clip_keys.add(media["asset_key"])
            else:
                s["media"] = None
    return type(timeline).model_validate(data), sorted(clip_keys)


def make_narration(path: Path) -> Path:
    import math

    rate = 24_000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(int(8000 * math.sin(2 * math.pi * 220 * i / rate)).to_bytes(2, "little", signed=True)
                               for i in range(rate)))
    return path


def make_clip(ffmpeg: str, path: Path) -> Path:
    """A 1080p 30 fps H.264 test clip (the mezzanine shape of inserted clips, §8.4)."""
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s=1920x1080:r=30:d={CLIP_SECONDS}", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-g", "30", str(path)], check=True, timeout=600)
    return path


# ---------------------------------------------------------------------------
# The page server (static files; the worker serves the timeline and the media frames itself)
# ---------------------------------------------------------------------------


@contextmanager
def serve_stage(web_dir: Path, *, stub: bool) -> Iterator[str]:
    root = web_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - http.server API
            path = urlsplit(self.path).path
            if path == "/render-dynamic":
                page = STUB_DIR / "render-dynamic.html" if stub else root / "render-dynamic.html"
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            elif stub and path == "/stage-stub.js":
                self._send(200, (STUB_DIR / "stage-stub.js").read_bytes(), "text/javascript")
            elif stub and path == "/stub-config.js":
                self._send(200, b"window.STUB = {};\n", "text/javascript")
            elif path.startswith("/web/"):
                file = (root / unquote(path[len("/web/"):])).resolve()
                if root in file.parents and file.is_file():
                    ctype = "text/javascript" if file.suffix in (".js", ".mjs") else (
                        mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                    self._send(200, file.read_bytes(), ctype)
                else:
                    self._send(404, b"", "text/plain")
            else:
                self._send(404, b"", "text/plain")

    class Server(ThreadingHTTPServer):
        request_queue_size = 256  # every worker's page loads its modules at once (the default backlog is 5)

    server = Server(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class BenchContext:
    """The job-context surface the engine uses: progress on stderr, no database, no storage."""

    payload: dict[str, Any] = {}
    assets = None
    storage = None

    def __init__(self) -> None:
        self.last = 0.0

    def check_cancelled(self) -> None:
        return

    def progress(self, stage: str, fraction: float, message: str = "") -> None:
        now = time.monotonic()
        if now - self.last >= 5 or fraction >= 1.0 or stage != "capture":
            self.last = now
            print(f"  [{stage:8s} {fraction * 100:5.1f}%] {message}", file=sys.stderr, flush=True)

    def log(self, message: str, level: str = "info", **data: Any) -> None:
        print(f"  {level}: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


def host_info(settings: Any) -> dict[str, Any]:
    from aadhi.compose.motion.resources import CpuBudget

    budget = CpuBudget.detect()
    try:
        out = subprocess.run([settings.ffmpeg_path, "-hide_banner", "-version"], capture_output=True, text=True,
                             timeout=30, check=False).stdout.splitlines()
        ffmpeg = out[0] if out else "?"
    except OSError:
        ffmpeg = "?"
    import psutil

    return {"platform": platform.platform(), "processor": platform.processor() or platform.machine(),
            "python": platform.python_version(), "logical_cpus": os.cpu_count(), "physical_cores": budget.physical,
            "cpu_quota": budget.quota, "cpuset": budget.affinity, "auto_workers": budget.auto_workers(),
            "memory_gb": round(psutil.virtual_memory().total / 1e9, 1), "ffmpeg": ffmpeg,
            "in_container": Path("/.dockerenv").exists()}


def project(summary: dict[str, Any], frames: int) -> float | None:
    """Seconds a render of ``frames`` would take at the measured aggregate rate and fixed costs."""
    fps = summary.get("aggregate_fps")
    t = summary.get("timings") or {}
    if not fps:
        return None
    fixed = t.get("boot", 0.0) + t.get("mux", 0.0) * frames / max(1, summary["frames"]) + t.get("checks", 0.0)
    return round(fixed + frames / fps, 1)


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Settings, the timeline and its local media files (blocking)."""
    from aadhi.config import Settings

    out_dir = Path(args.out).resolve() if args.out else Path(tempfile.mkdtemp(prefix="aadhi-bench-motion-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    web_dir = Path(args.web_dir).resolve()
    if args.stage != "stub" and not (web_dir / "render-dynamic.html").is_file():
        raise SystemExit(f"{web_dir / 'render-dynamic.html'} is missing (use --stage stub for the engine alone)")
    base = Settings(_env_file=None)
    updates: dict[str, Any] = {"render_bgm": False, "web_dir": web_dir, "dynamic_enabled": True,
                               "render_slot_dir": str(out_dir / "cpu-slots"),
                               "motion_x264_preset": args.preset or base.motion_x264_preset,
                               "motion_x264_crf": base.motion_x264_crf if args.crf is None else args.crf}
    if args.ffmpeg:
        updates["ffmpeg_path"] = args.ffmpeg
    if args.ffprobe:
        updates["ffprobe_path"] = args.ffprobe
    settings = base.model_copy(update=updates)
    if args.timeline:
        from aadhi.schemas.timeline import Timeline

        tl = Timeline.model_validate(json.loads(Path(args.timeline).read_text(encoding="utf-8")))
        clip_keys: list[str] = []
    else:
        tl = repeat_to(showcase_timeline(settings, fmt=args.format, burn=args.burn_captions), args.minutes * 60)
        tl, clip_keys = with_media(tl, narration=not args.no_narration, clip=not args.no_media)
    files: dict[str, Path] = {}
    if any(s.audio_asset_key == NARRATION_KEY for s in tl.scenes):
        files[NARRATION_KEY] = make_narration(out_dir / "narration.wav")
    if clip_keys:
        clip = make_clip(settings.ffmpeg_path, out_dir / "clip_1080p30.mp4")
        files.update({k: clip for k in clip_keys})
    for item in args.media or []:
        key, _, path = item.partition("=")
        files[key] = Path(path).resolve()
    return {"out_dir": out_dir, "web_dir": web_dir, "settings": settings, "timeline": tl, "files": files,
            "host": host_info(settings)}


async def render(args: argparse.Namespace, prep: dict[str, Any]) -> Any:
    """The render itself (the engine, through the page server)."""
    from aadhi.compose.motion.engine import MotionConfig, render_motion
    from aadhi.compose.motion.stage import StageOptions

    tl, out_dir = prep["timeline"], prep["out_dir"]
    # an experiment only: production flags come from the page driver (CHROMIUM_ARGS)
    extra = ("--disable-frame-rate-limit",) if args.disable_frame_rate_limit else ()
    config = MotionConfig(workers=args.workers or None, sample_memory_seconds=0.5,
                          chunk_min=args.chunk_min, chunk_max=args.chunk_max, stage=StageOptions(extra_args=extra),
                          shared_driver=not args.driver_per_worker)
    with serve_stage(prep["web_dir"], stub=args.stage == "stub") as origin:
        settings = prep["settings"].model_copy(update={"base_url": origin})
        return await render_motion(BenchContext(), tl, out_dir / "bench.mp4", settings, token=TOKEN,
                                   served=tl.model_dump(mode="json"), work_dir=out_dir / "work",
                                   local_files=prep["files"], burn_captions=args.burn_captions,
                                   soft_subtitles=not args.burn_captions, config=config)


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Prepare, render and report (see the module docstring)."""
    prep = prepare(args)
    tl, out_dir, settings = prep["timeline"], prep["out_dir"], prep["settings"]
    print(f"Rendering {tl.total_duration:.1f} s ({tl.presentation.format if tl.presentation else '?'}, "
          f"{tl.width}x{tl.height}, {len(tl.scenes)} scenes) with the {args.stage} stage...", file=sys.stderr)
    started = time.monotonic()
    result = asyncio.run(render(args, prep))
    wall = time.monotonic() - started
    summary = result.summary()
    per_worker = summary["fps_per_worker"]
    report = {
        "bench": "motion", "version": 1, "host": prep["host"],
        "settings": {"workers": result.workers, "slots": result.slots, "preset": settings.motion_x264_preset,
                     "crf": settings.motion_x264_crf, "stage": args.stage,
                     "disable_frame_rate_limit": bool(args.disable_frame_rate_limit),
                     "shared_driver": not args.driver_per_worker,
                     "burn_captions": bool(args.burn_captions), "chunk": [args.chunk_min, args.chunk_max]},
        "video": {"seconds": round(tl.total_duration, 2), "frames": result.frames, "size": list(result.size),
                  "format": tl.presentation.format if tl.presentation else None, "scenes": len(tl.scenes),
                  "media_scenes": sum(1 for s in tl.scenes if s.media is not None and s.media.kind == "video")},
        "result": summary,
        "derived": {
            "wall_seconds": round(wall, 1),
            "realtime_factor": round(wall / max(1e-9, tl.total_duration), 3),
            "ms_per_frame_per_worker": [round(1000 / f, 1) for f in per_worker if f],
            "chunk_media_seconds": round(sum(c.media_seconds for c in result.chunks), 1),
            "memory_per_worker_mb": round((summary["memory_mb"].get("browsers", 0) + summary["memory_mb"].get(
                "encoders", 0)) / max(1, result.workers), 1),
            "projected_seconds": {"lesson_16x9_5min": project(summary, LESSON_FRAMES),
                                  "reel_9x16_3_5min": project(summary, REEL_FRAMES)},
        },
        "container_ok": result.container.ok,
        "output": str(out_dir / "bench.mp4"),
    }
    if not args.keep:
        shutil.rmtree(out_dir / "work", ignore_errors=True)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, default=1.0, help="length of the rendered video (default 1)")
    parser.add_argument("--format", choices=("landscape", "portrait"), default="landscape")
    parser.add_argument("--stage", choices=("real", "stub"), default="real",
                        help="real = web/render-dynamic.html; stub = the tests' stub page (engine + capture only)")
    parser.add_argument("--workers", type=int, default=0, help="capture workers (0 = RENDER_MOTION_WORKERS / auto)")
    parser.add_argument("--preset", default=None, help="x264 preset (default MOTION_X264_PRESET)")
    parser.add_argument("--crf", type=int, default=None, help="x264 CRF (default MOTION_X264_CRF)")
    parser.add_argument("--chunk-min", type=int, default=240)
    parser.add_argument("--chunk-max", type=int, default=600)
    parser.add_argument("--burn-captions", action="store_true", help="captions drawn by the stage")
    parser.add_argument("--no-media", action="store_true", help="drop the video scenes' clips")
    parser.add_argument("--no-narration", action="store_true", help="a silent lecture")
    parser.add_argument("--disable-frame-rate-limit", action="store_true",
                        help="add --disable-frame-rate-limit to Chromium (the open question of §7.5)")
    parser.add_argument("--driver-per-worker", action="store_true",
                        help="one Playwright driver per worker (the default shares one driver between the workers)")
    parser.add_argument("--timeline", help="render this stored Dynamic timeline JSON instead of the showcase")
    parser.add_argument("--media", action="append", metavar="KEY=FILE", help="a local file for a media key")
    parser.add_argument("--web-dir", default=str(ROOT / "web"))
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--ffprobe", default=None)
    parser.add_argument("--out", default=None, help="work directory (default: a new temporary directory)")
    parser.add_argument("--keep", action="store_true", help="keep the chunks and side files")
    parser.add_argument("--json", default=None, help="also write the report here")
    args = parser.parse_args(argv)
    report = run(args)
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text + "\n", encoding="utf-8")
    return 0 if report["container_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
