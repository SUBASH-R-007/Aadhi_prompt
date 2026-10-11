"""The motion engine: a Dynamic timeline -> MP4, every frame captured from the stage (DYNAMIC_VIDEO_DESIGN §7).

:func:`render_motion` (used by the ``render_video`` job for ``timeline.presentation.style == "dynamic"``, never for
a classic timeline):

1. plan scene-aligned chunks of 240-600 frames (``chunks.plan_chunks``) and build the audio master alongside the
   capture (the classic mix: narration, quiz ticks and ding per scene, ``encode.scene_audio_command``; the ducked
   branding BGM; encoded once to AAC, ``encode.master_audio_command``);
2. take ``W`` workers (``resources.motion_workers``: cgroup-aware, ``RENDER_MOTION_WORKERS``) limited by the
   host-wide CPU slots (``resources.CpuSlots``: ``RENDER_CPU_SLOTS`` lock files in ``RENDER_SLOT_DIR``), and start
   one :class:`~.stage.StageSession` per worker on ``/render-dynamic``; the first one's ``info()`` must agree with
   the timeline frame by frame;
3. workers take chunks longest first. A chunk whose content address (``chunks.chunk_key``) is in the workspace's
   chunk cache is reused (a retry); otherwise its media frames are extracted (``media``), then for every frame
   ``seek(frame)`` -> CDP JPEG capture -> the encoder's stdin (``encode.EncoderSpec``: image2pipe -> x264 ->
   MPEG-TS); the chunk's packet count must equal its frames (else it is captured again once, on a fresh page);
   a page is recycled after 1,800 frames or when its capture rate falls below half of its first chunk's;
4. the chunks are joined (concat demuxer, ``-c copy``) and muxed once with the master audio (``-c copy``), the
   chapters and an optional default-on ``mov_text`` caption track (``+faststart+negative_cts_offsets``);
5. the container checks run (``dynamic.container.verify_container``) with this engine's remedies: chunks with a
   wrong frame count are captured again, the audio is mixed again, the MP4 is muxed again; a problem its remedy does
   not fix fails the render (``render_invalid``).

Captions are drawn by the stage (``setLayers({captions})``), never burned by libass. Determinism is the stage's job
(§7.4): the engine asks for frames in ascending order within a chunk and chunks in any order. Cancellation
(``ctx.check_cancelled``) is honoured between frames and while any ffmpeg runs.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from ...config import Settings
from ...schemas.timeline import TimedScene, Timeline
from ..aio import gather_or_cancel
from ..base import output_size, timeline_stage
from ..captions import to_srt
from ..chapters import to_ffmetadata
from ..dynamic.container import ChunkFile, ContainerQA, inspect_container, verify_container
from ..ffmpeg import (
    AudioClip,
    FFmpegError,
    concat_list,
    ffmpeg_version,
    filter_script_flag,
    kill_tree,
    probe,
    run_ffmpeg,
    subprocess_env,
    subtitle_language_code,
)
from ..qa import packet_pts
from ..screenshot import media_origins_for, render_origin
from ..sounds import ding_wav, tick_wav
from ..video import DING_VOLUME, TICK_VOLUME  # the classic mix (§7.11: reused until B3's mix)
from ..workspace import stat_digest
from .cache import ChunkCache
from .chunks import (
    CHUNK_MAX_FRAMES,
    CHUNK_MIN_FRAMES,
    Chunk,
    assignment_order,
    bundle_digest,
    chunk_key,
    media_keys,
    plan_chunks,
    scene_frame_bounds,
    total_frames,
)
from .encode import (
    EncoderSpec,
    MasterAudioSpec,
    MotionFinalSpec,
    SceneAudioSpec,
    extract_args,
    ffmpeg_pipe_base,
    final_mux_command,
    frame_runs,
    master_audio_command,
    scene_audio_command,
)
from .media import (
    LOOKAHEAD_FRAMES,
    RUN_GAP_FRAMES,
    MediaPlanError,
    MediaRequest,
    file_rate,
    frame_files,
    native_frames,
    parse_media_plan,
    resolve_media_file,
)
from .resources import CpuBudget, CpuSlots, SlotLease, motion_workers
from .stage import StageError, StageOptions, StageSession, scene_mismatch

log = logging.getLogger(__name__)

ENGINE = "motion"
RECYCLE_FRAMES = 1800
RECYCLE_SLOWDOWN = 0.5
_STDERR_TAIL = 8192
_PROGRESS_SECONDS = 1.0


class MotionError(RuntimeError):
    """The motion render failed; ``retryable`` False = a retry cannot help (contract, deployment, input)."""

    def __init__(self, message: str, *, code: str = "render_failed", retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ChunkCountError(MotionError):
    """An encoded chunk does not have exactly its frames."""


def is_dynamic(timeline: Timeline) -> bool:
    """True for a Dynamic timeline (the motion engine's input); False for every classic timeline."""
    return timeline.presentation is not None and timeline.presentation.style == "dynamic"


@dataclass
class MotionConfig:
    """Engine knobs that are not settings (tests, the benchmark)."""

    workers: int | None = None  # None = RENDER_MOTION_WORKERS / auto
    chunk_min: int = CHUNK_MIN_FRAMES
    chunk_max: int = CHUNK_MAX_FRAMES
    recycle_frames: int = RECYCLE_FRAMES
    recycle_slowdown: float = RECYCLE_SLOWDOWN
    stage: StageOptions = field(default_factory=StageOptions)
    use_slots: bool = True
    sample_memory_seconds: float = 2.0  # 0 = off
    encoder_timeout: float = 600.0
    shared_driver: bool = True  # one Playwright driver (node) for every worker's browser instead of one per worker


@dataclass
class ChunkStat:
    """How one chunk was produced."""

    index: int
    f0: int
    f1: int
    worker: int
    reused: bool = False
    seconds: float = 0.0
    capture_seconds: float = 0.0
    media_seconds: float = 0.0
    media_frames: int = 0
    attempts: int = 1

    @property
    def fps(self) -> float:
        """Capture + encode rate of the chunk (frames per second of the capture loop)."""
        return (self.f1 - self.f0) / self.capture_seconds if self.capture_seconds > 0 else 0.0


@dataclass
class WorkerStat:
    """One worker's totals."""

    worker: int
    frames: int = 0
    chunks: int = 0
    capture_seconds: float = 0.0
    pages: int = 0
    recycles: int = 0

    @property
    def fps(self) -> float:
        """Frames per second while capturing."""
        return self.frames / self.capture_seconds if self.capture_seconds > 0 else 0.0


@dataclass
class MotionResult:
    """What :func:`render_motion` produced and measured."""

    output: Path
    frames: int
    fps: int
    size: tuple[int, int]
    workers: int
    slots: int
    chunks: list[ChunkStat]
    worker_stats: list[WorkerStat]
    container: ContainerQA
    timings: dict[str, float] = field(default_factory=dict)
    chromium: str = ""
    webgl: bool | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    memory_mb: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    encoder: dict[str, Any] = field(default_factory=dict)

    @property
    def reused(self) -> int:
        """Chunks taken from the cache."""
        return sum(1 for c in self.chunks if c.reused)

    def summary(self) -> dict[str, Any]:
        """JSON stored at ``Render.options["qa"]["motion"]`` (and printed by the benchmark)."""
        captured = [c for c in self.chunks if not c.reused]
        return {
            "engine": ENGINE,
            "frames": self.frames,
            "fps": self.fps,
            "size": list(self.size),
            "workers": self.workers,
            "slots": self.slots,
            "chunks": len(self.chunks),
            "reused_chunks": self.reused,
            "fps_per_worker": [round(w.fps, 2) for w in self.worker_stats],
            "aggregate_fps": round(sum(c.f1 - c.f0 for c in captured) / max(1e-9, self.timings.get("capture", 0.0)), 2)
            if captured else None,
            "page_recycles": sum(w.recycles for w in self.worker_stats),
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
            "chromium": self.chromium,
            "webgl": self.webgl,
            "budget": dict(self.budget),
            "memory_mb": {k: round(v, 1) for k, v in self.memory_mb.items()},
            "encoder": dict(self.encoder),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


class _PipeEncoder:
    """One ffmpeg reading JPEG frames on stdin (the chunk encoder)."""

    def __init__(self, args: list[str], *, cwd: Path, settings: Settings) -> None:
        self.args = args
        self.cwd = cwd
        self.settings = settings
        self.proc: asyncio.subprocess.Process | None = None
        self._err = bytearray()
        self._drain: asyncio.Future[None] | None = None

    async def start(self) -> None:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            import subprocess

            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, cwd=str(self.cwd), env=subprocess_env(), **kwargs)
        except FileNotFoundError as exc:
            raise FFmpegError("ffmpeg not found") from exc
        self._drain = asyncio.ensure_future(self._read_err())

    async def _read_err(self) -> None:
        assert self.proc is not None and self.proc.stderr is not None
        while True:
            chunk = await self.proc.stderr.read(65536)
            if not chunk:
                return
            self._err.extend(chunk)
            if len(self._err) > _STDERR_TAIL * 4:
                del self._err[: len(self._err) - _STDERR_TAIL]

    def tail(self) -> str:
        text = bytes(self._err[-_STDERR_TAIL:]).decode("utf-8", "replace").strip()
        return self.settings.redact(text)

    async def write(self, data: bytes) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        try:
            self.proc.stdin.write(data)
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            await self._settle(5)
            raise FFmpegError(f"the chunk encoder stopped reading frames: {self.tail()[-300:]}",
                              returncode=self.proc.returncode, stderr_tail=self.tail()) from exc

    async def _settle(self, timeout: float) -> None:
        assert self.proc is not None
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.proc.wait(), timeout)
        if self._drain is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self._drain, 5)

    async def finish(self, timeout: float) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        self.proc.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError, OSError):
            await self.proc.stdin.wait_closed()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout)
        except asyncio.TimeoutError:
            await self.kill()
            raise FFmpegError(f"the chunk encoder timed out after {timeout:.0f}s", stderr_tail=self.tail()) from None
        await self._settle(5)
        if self.proc.returncode != 0:
            tail = self.tail()
            last = tail.splitlines()[-1] if tail else ""
            raise FFmpegError(f"ffmpeg exited with {self.proc.returncode}: {last}", returncode=self.proc.returncode,
                              stderr_tail=tail)

    async def kill(self) -> None:
        if self.proc is not None and self.proc.returncode is None:
            await asyncio.to_thread(kill_tree, self.proc.pid)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.proc.wait(), 10)
        if self._drain is not None and not self._drain.done():
            self._drain.cancel()
            with contextlib.suppress(BaseException):
                await self._drain


class _MemorySampler:
    """Peak resident memory (MB) of this process and its children, by kind (browser, encoder, driver, self)."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.peaks: dict[str, float] = {}
        self._task: asyncio.Future[None] | None = None

    @staticmethod
    def _kind(name: str) -> str:
        low = name.lower()
        if "ffmpeg" in low:
            return "encoders"
        if "chrom" in low or "headless" in low:
            return "browsers"
        if low.startswith("node"):
            return "drivers"
        return "other"

    def sample(self) -> None:
        try:
            me = psutil.Process(os.getpid())
            procs = [me, *me.children(recursive=True)]
        except psutil.Error:
            return
        totals: dict[str, float] = {"self": 0.0}
        for p in procs:
            try:
                rss = p.memory_info().rss / 1e6
                kind = "self" if p.pid == me.pid else self._kind(p.name())
            except psutil.Error:
                continue
            totals[kind] = totals.get(kind, 0.0) + rss
        totals["total"] = sum(v for k, v in totals.items())
        for k, v in totals.items():
            self.peaks[k] = max(self.peaks.get(k, 0.0), v)

    async def _run(self) -> None:
        while True:
            await asyncio.to_thread(self.sample)
            await asyncio.sleep(self.interval)

    def start(self) -> None:
        if self.interval > 0:
            self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
            self._task = None


async def _run_cmd(args: list[str], files: Mapping[str, str], *, work: Path, settings: Settings, timeout: float,
                   check_cancelled: Callable[[], None] | None) -> None:
    def write() -> None:
        for name, text in files.items():
            (work / name).write_text(text, encoding="utf-8")

    await asyncio.to_thread(write)
    await run_ffmpeg(args, settings=settings, cwd=work, timeout=timeout, check_cancelled=check_cancelled)


def _move(src: Path, dest: Path) -> None:
    if src.resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dest)


def _check(ctx: Any) -> Callable[[], None] | None:
    return getattr(ctx, "check_cancelled", None)


def _progress(ctx: Any, stage: str, fraction: float, message: str) -> None:
    fn = getattr(ctx, "progress", None)
    if fn is not None:
        fn(stage, min(1.0, max(0.0, fraction)), message)


def _log(ctx: Any, message: str, level: str = "info") -> None:
    fn = getattr(ctx, "log", None)
    if fn is not None:
        fn(message, level=level)
    else:
        log.log(logging.WARNING if level == "warning" else logging.INFO, message)


# ---------------------------------------------------------------------------
# Files the render needs
# ---------------------------------------------------------------------------


def video_media_keys(timeline: Timeline) -> list[str]:
    """Asset keys of the video media the stage may ask frames of (scene media, panel media, overlays)."""
    keys: set[str] = set()
    for s in timeline.scenes:
        refs = [s.media, s.side_panel.media if s.side_panel is not None else None, *(o.media for o in s.overlays)]
        for ref in refs:
            if ref is not None and ref.kind == "video" and ref.asset_key and ref.render_in_mp4:
                keys.add(ref.asset_key)
    return sorted(keys)


class _Files:
    """Local files of asset keys (copied into ``inputs`` on demand) and branding clips."""

    def __init__(self, ctx: Any, settings: Settings, inputs: Path, preset: Mapping[str, Path] | None) -> None:
        self.ctx = ctx
        self.settings = settings
        self.inputs = inputs
        self.local: dict[str, Path] = {k: Path(v) for k, v in (preset or {}).items()}
        self._lock = asyncio.Lock()

    async def get(self, key: str, *, required: bool) -> Path | None:
        if key in self.local:
            return self.local[key]
        async with self._lock:
            if key in self.local:
                return self.local[key]
            path = await asyncio.to_thread(self._fetch, key)
        if path is None:
            path = resolve_media_file(key, local={}, branding_dir=self.settings.branding_dir)
        if path is None and required:
            raise MotionError(f"asset {key[:48]} is missing from storage; rebuild the lecture before rendering",
                              code="asset_missing")
        if path is not None:
            self.local[key] = path
        return path

    def _fetch(self, key: str) -> Path | None:
        assets = getattr(self.ctx, "assets", None)
        if assets is None:
            return None
        self.inputs.mkdir(parents=True, exist_ok=True)
        try:
            return Path(assets.local_copy(key, self.inputs)).resolve()
        except (KeyError, FileNotFoundError):
            return None
        except OSError as exc:
            raise MotionError(f"asset {key[:48]} could not be read ({type(exc).__name__})", code="asset_unreadable",
                              retryable=True) from exc


# ---------------------------------------------------------------------------
# Audio master (the classic mix for now, §7.11)
# ---------------------------------------------------------------------------


def scene_audio_spec(scene: TimedScene, frames: tuple[int, int], *, fps: int, name: str, narration: str | None,
                     tick: str | None, ding: str | None) -> SceneAudioSpec:
    """The classic per-scene mix: narration at ``audio_offset``, a tick per countdown second, the reveal ding."""
    n = frames[1] - frames[0]
    duration = n / fps
    shift = scene.start - frames[0] / fps
    clips: list[tuple[str, AudioClip]] = []
    if narration is not None:
        clips.append((narration, AudioClip(0, delay=scene.audio_offset + shift)))
    quiz = scene.quiz
    if quiz is not None:
        if tick is not None:
            for k in range(quiz.countdown_seconds):
                t = quiz.countdown_start + k + shift
                if 0 <= t < duration:
                    clips.append((tick, AudioClip(0, delay=t, volume=TICK_VOLUME)))
        if ding is not None and 0 <= quiz.reveal_start + shift < duration:
            clips.append((ding, AudioClip(0, delay=quiz.reveal_start + shift, volume=DING_VOLUME)))
    return SceneAudioSpec(name=name, samples=round(n * 48_000 / fps), clips=tuple(clips))


async def _build_audio(timeline: Timeline, bounds: list[tuple[int, int]], files: _Files, *, work: Path,
                       settings: Settings, script_flag: str, check_cancelled: Callable[[], None] | None) -> list[str]:
    fps = timeline.fps
    tick = ding = None
    if any(s.quiz is not None for s in timeline.scenes):
        def write_sounds() -> tuple[str, str]:
            (work / "sfx_tick.wav").write_bytes(tick_wav())
            (work / "sfx_ding.wav").write_bytes(ding_wav())
            return str((work / "sfx_tick.wav").resolve()), str((work / "sfx_ding.wav").resolve())

        tick, ding = await asyncio.to_thread(write_sounds)
    names: list[str] = []
    for i, scene in enumerate(timeline.scenes):
        narration = None
        if scene.audio_asset_key:
            path = await files.get(scene.audio_asset_key, required=True)
            narration = str(path) if path is not None else None
        spec = scene_audio_spec(scene, bounds[i], fps=fps, name=f"aud_{i:04d}", narration=narration, tick=tick,
                                ding=ding)
        cmd = scene_audio_command(spec, script_flag=script_flag)
        await _run_cmd(cmd.args, cmd.files, work=work, settings=settings, timeout=600 + 5 * scene.duration,
                       check_cancelled=check_cancelled)
        names.append(f"{spec.name}.wav")
    return names


async def _build_master(timeline: Timeline, bounds: list[tuple[int, int]], files: _Files, *, work: Path,
                        settings: Settings, script_flag: str, master: MasterAudioSpec,
                        check_cancelled: Callable[[], None] | None) -> str:
    """The scenes' WAVs, then the master track (AAC, + the ducked BGM) the final mux copies; returns its file name.
    Runs while the frames are captured (§7.11), so the mux never waits for the AAC encode."""
    wavs = await _build_audio(timeline, bounds, files, work=work, settings=settings, script_flag=script_flag,
                              check_cancelled=check_cancelled)
    await asyncio.to_thread((work / master.audio_list).write_text, concat_list(wavs), "utf-8")
    cmd = master_audio_command(master, script_flag=script_flag)
    await _run_cmd(cmd.args, cmd.files, work=work, settings=settings, timeout=600 + 2 * master.total_seconds,
                   check_cancelled=check_cancelled)
    return master.output


# ---------------------------------------------------------------------------
# Media frames per chunk (§7.7)
# ---------------------------------------------------------------------------


async def _extract_media(plan: list[MediaRequest], files: _Files, directory: Path, *, settings: Settings,
                         rates: dict[str, float | None],
                         check_cancelled: Callable[[], None] | None) -> tuple[dict[tuple[str, int], Path], int, list[str]]:
    """Extract every requested frame; returns ``(registry entries, frames extracted, warnings)``."""
    out: dict[tuple[str, int], Path] = {}
    warnings: list[str] = []
    count = 0
    for req in plan:
        src = await files.get(req.asset, required=False)
        if src is None:
            raise MotionError(f"the stage asked for frames of an unknown media {req.asset[:48]!r}",
                              code="media_missing")
        if req.asset not in rates:
            try:
                rates[req.asset] = (await probe(src, settings=settings)).fps
            except FFmpegError:
                rates[req.asset] = None
        # A file at another rate than the page's grid (a 24 fps Veo clip on the 30 fps timeline grid) is extracted at
        # its own rate, frame-exact: page index n shows file frame floor(n * rate / srcFps) whatever the chunk plan.
        rate = file_rate(req.src_fps, rates[req.asset])
        native = native_frames(req.frames, src_fps=req.src_fps, rate=rate)
        target = directory / req.slug
        await asyncio.to_thread(target.mkdir, parents=True, exist_ok=True)
        for first, n in frame_runs(list(native.values()), max_gap=RUN_GAP_FRAMES):
            args = extract_args(src, first=first, count=n, src_fps=rate, size=req.size, out_dir=target)
            await run_ffmpeg(args, settings=settings, timeout=300 + n, check_cancelled=check_cancelled)
            count += n
        try:
            entries, missing = await asyncio.to_thread(frame_files, req, target, native=native)
        except MediaPlanError as exc:
            raise MotionError(str(exc), code="media_unreadable") from exc
        if missing:
            warnings.append(f"{len(missing)} frame(s) of {req.key[:40]} lie past its end; the last frame is held")
        out.update(entries)
    return out, count, warnings


# ---------------------------------------------------------------------------
# Stage sessions
# ---------------------------------------------------------------------------


def media_origins(ctx: Any, settings: Settings) -> list[str]:
    """The storage's media origins the stage may load images from (as the classic render page; blocking)."""
    storage = getattr(ctx, "storage", None)
    if storage is None:
        return []
    try:
        return media_origins_for(storage, settings)
    except Exception:  # noqa: BLE001 - a storage that cannot name its origins serves none
        return []


def _mint_token(ctx: Any, settings: Settings, timeline: Timeline) -> str:
    from ..video import RENDER_TOKEN_TTL_SECONDS, _mint_token

    if timeline.version_id is None:
        raise MotionError("the timeline has no version id (needed for the render token)", code="bad_payload")
    payload = getattr(ctx, "payload", None) or {}
    rid = payload.get("render_id") if isinstance(payload, dict) else None
    return _mint_token(settings, int(timeline.version_id), RENDER_TOKEN_TTL_SECONDS,
                       render_id=int(rid) if isinstance(rid, int) else None)


def hashable_timeline(data: Mapping[str, Any]) -> dict[str, Any]:
    """``data`` without its URLs (filled per request: presigned links change on every retry; asset keys stay)."""

    def strip(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if not (k == "url" or k.endswith("_url"))}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value

    return strip(dict(data))


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass
class _Run:
    """Shared state of one render's workers."""

    timeline: Timeline
    settings: Settings
    ctx: Any
    work: Path
    enc: EncoderSpec
    capture_size: tuple[int, int]
    total: int
    keys: dict[int, str]
    cache: ChunkCache | None
    files: _Files
    cfg: MotionConfig
    queue: list[Chunk]
    stats: dict[int, ChunkStat] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    done_frames: int = 0
    last_progress: float = 0.0
    rates: dict[str, float | None] = field(default_factory=dict)
    media_allowed: frozenset[str] = frozenset()  # the media keys the timeline names (nothing else is ever extracted)
    driver: Any = None  # the Playwright driver the workers share (MotionConfig.shared_driver), else None
    refresh_served: Callable[[], Mapping[str, Any]] | None = None  # re-signs ``served`` before every page load

    def frame_done(self, n: int = 1) -> None:
        self.done_frames += n
        now = time.monotonic()
        if now - self.last_progress >= _PROGRESS_SECONDS or self.done_frames >= self.total:
            self.last_progress = now
            _progress(self.ctx, "capture", 0.08 + 0.77 * self.done_frames / max(1, self.total),
                      f"Captured {self.done_frames}/{self.total} frames")


async def _frames_ok(path: Path, frames: int, settings: Settings) -> bool:
    try:
        return len(await packet_pts(path, settings)) == frames
    except FFmpegError:
        return False


def _unlink(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


async def _capture_chunk(run: _Run, session: StageSession, chunk: Chunk, worker: int) -> ChunkStat:
    """Media extraction + capture + encode of one chunk into ``<work>/<chunk>.ts`` (verified, then cached)."""
    settings, work = run.settings, run.work
    check = _check(run.ctx)
    started = time.monotonic()
    stat = ChunkStat(chunk.index, chunk.f0, chunk.f1, worker)
    media_dir = work / "media" / chunk.name
    raw = await session.media_plan(chunk.f0, min(run.total, chunk.f1 + LOOKAHEAD_FRAMES))
    try:
        plan = parse_media_plan(raw, stage=run.capture_size)
    except MediaPlanError as exc:
        raise MotionError(f"the render stage broke its contract: {exc}", code="stage_contract") from exc
    unknown = sorted(r.asset for r in plan if r.asset not in run.media_allowed)
    if unknown:  # never fetch an asset (or branding file) the lecture does not name
        raise MotionError(f"the render stage asked for media the lecture does not name ({unknown[0][:48]})",
                          code="stage_contract")
    entries, stat.media_frames, warnings = await _extract_media(plan, run.files, media_dir, settings=settings,
                                                                rates=run.rates, check_cancelled=check)
    run.warnings.extend(w for w in warnings if w not in run.warnings)
    session.frames.set(entries)
    stat.media_seconds = time.monotonic() - started
    part = work / f"{chunk.name}.part.ts"
    await asyncio.to_thread(_unlink, part)  # a fresh file: never write through a hard link into the cache
    args = [*ffmpeg_pipe_base(settings), *run.enc.chunk_args(frames=chunk.frames, output=part.name,
                                                               capture=run.capture_size)]
    encoder = _PipeEncoder(args, cwd=work, settings=settings)
    await encoder.start()
    keys = hashlib.sha256()
    t0 = time.monotonic()
    done = 0
    try:
        for frame in range(chunk.f0, chunk.f1):
            if check is not None:
                check()
            key = await session.seek(frame)
            keys.update(key.encode("utf-8", "replace") + b"\n")
            await encoder.write(await session.capture_jpeg())
            done += 1
            run.frame_done()
        await encoder.finish(run.cfg.encoder_timeout)
    except BaseException:
        run.done_frames = max(0, run.done_frames - done)  # this attempt's frames are captured again
        await encoder.kill()
        raise
    finally:
        session.frames.clear()
        await asyncio.to_thread(shutil.rmtree, media_dir, True)
    stat.capture_seconds = time.monotonic() - t0
    if not await _frames_ok(part, chunk.frames, settings):
        raise ChunkCountError(f"chunk {chunk.index} (frames {chunk.f0}-{chunk.f1 - 1}) was encoded with the wrong "
                              "number of frames", code="chunk_frames", retryable=True)
    dest = work / f"{chunk.name}.ts"
    await asyncio.to_thread(_unlink, dest)
    await asyncio.to_thread(os.replace, part, dest)
    if run.cache is not None:
        await asyncio.to_thread(run.cache.store, run.keys[chunk.index], dest, frames=chunk.frames,
                                keys_digest=keys.hexdigest())
    stat.seconds = time.monotonic() - started
    return stat


def _retryable(exc: BaseException) -> bool:
    """A chunk failure worth one more attempt on a fresh page: a crashed or stuck page, a failed call inside the page
    (a deterministic bug fails again), a wrong frame count, an encoder failure; never a contract violation."""
    if isinstance(exc, ChunkCountError | FFmpegError):
        return True
    if isinstance(exc, StageError):
        return exc.code != "stage_contract" and (exc.retryable or exc.code == "stage_call_failed")
    return False


async def _worker(run: _Run, session: StageSession, index: int, wstat: WorkerStat) -> None:
    """Take chunks until none is left (see the module docstring for reuse, retry and recycling)."""
    first_fps: float | None = None
    page_frames = 0
    check = _check(run.ctx)
    while run.queue:
        chunk = run.queue.pop(0)
        if check is not None:
            check()
        key = run.keys[chunk.index]
        dest = run.work / f"{chunk.name}.ts"
        if run.cache is not None:
            cached = await asyncio.to_thread(run.cache.cached, key, frames=chunk.frames)
            if cached is not None and await _frames_ok(cached, chunk.frames, run.settings):
                await asyncio.to_thread(run.cache.adopt, key, dest)
                run.stats[chunk.index] = ChunkStat(chunk.index, chunk.f0, chunk.f1, index, reused=True)
                run.frame_done(chunk.frames)
                continue
            if cached is not None:
                await asyncio.to_thread(run.cache.discard, key)
        attempt = 1
        while True:
            try:
                stat = await _capture_chunk(run, session, chunk, index)
                break
            except (StageError, MotionError, FFmpegError) as exc:
                if attempt >= 2 or not _retryable(exc):
                    raise
                attempt += 1
                _log(run.ctx, f"Chunk {chunk.index + 1} is captured again on a fresh page "
                              f"({run.settings.redact(str(exc))[:200]})", level="warning")
                await session.recycle()
                wstat.recycles += 1
                first_fps, page_frames = None, 0
        stat.attempts = attempt
        run.stats[chunk.index] = stat
        wstat.frames += chunk.frames
        wstat.chunks += 1
        wstat.capture_seconds += stat.capture_seconds
        page_frames += chunk.frames
        slowed = first_fps is not None and stat.fps < run.cfg.recycle_slowdown * first_fps
        if first_fps is None:
            first_fps = stat.fps
        if run.queue and (page_frames >= run.cfg.recycle_frames or slowed):
            await session.recycle()
            wstat.recycles += 1
            first_fps, page_frames = None, 0


def _playwright() -> Any:
    """``async_playwright()`` (an async context manager yielding the started driver)."""
    from playwright.async_api import async_playwright

    return async_playwright()


def _session(run: _Run, *, token: str, served: Mapping[str, Any] | None, origins: list[str], options: StageOptions,
             label: str) -> StageSession:
    return StageSession(settings=run.settings, size=run.capture_size, token=token, served=served,
                        base_url=render_origin(run.settings), media_origins=origins,
                        options=dataclasses.replace(options, layers=dict(options.layers)),
                        check_cancelled=_check(run.ctx), label=label, playwright=run.driver,
                        refresh_served=run.refresh_served)


async def _audio_guard(task: asyncio.Future[str]) -> None:
    """Re-raise the audio master's failure while the frames are captured (a corrupt narration, a failed encode), so
    the workers stop and release their CPU slots at once. Cancelling the guard (a worker failed) leaves the task."""
    await asyncio.shield(task)


async def _start_sessions(sessions: list[StageSession]) -> None:
    try:
        await gather_or_cancel([s.start() for s in sessions])
    except BaseException:
        await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)
        raise


def _media_identities(timeline_data: Mapping[str, Any], settings: Settings) -> dict[str, str]:
    """Identity of media keys that are branding file names (not content addresses): name, size, mtime."""
    out: dict[str, str] = {}
    for key in media_keys({"scenes": timeline_data.get("scenes", [])}):
        path = resolve_media_file(key, local={}, branding_dir=settings.branding_dir)
        if path is not None:
            with contextlib.suppress(OSError):
                out[key] = stat_digest(path)
    return out


@dataclass
class _Assembly:
    """The join + mux of a render, and the container remedies (``dynamic.container.ContainerRemedies``)."""

    run: _Run
    chunks: list[Chunk]
    bounds: list[tuple[int, int]]
    script_flag: str
    master: MasterAudioSpec
    mux_args: list[str]
    side: dict[str, str]
    reopen: Callable[[], StageSession]
    applied: list[str] = field(default_factory=list)

    async def mux(self) -> None:
        run = self.run
        await _run_cmd(self.mux_args, self.side, work=run.work, settings=run.settings,
                       timeout=900 + 2 * run.total / max(1, run.timeline.fps), check_cancelled=_check(run.ctx))

    async def rerender_chunks(self, indices: Sequence[int]) -> None:
        """Capture these chunks again on a fresh page (bypassing the cache), then join and mux again."""
        self.applied.append("rerender_chunks")
        run = self.run
        by_index = {c.index: c for c in self.chunks}
        session = self.reopen()
        await session.start()
        try:
            for i in indices:
                chunk = by_index[i]
                if run.cache is not None:
                    await asyncio.to_thread(run.cache.discard, run.keys[i])
                run.done_frames = max(0, run.done_frames - chunk.frames)
                run.stats[i] = await _capture_chunk(run, session, chunk, -1)
        finally:
            await session.close()
        await self.mux()

    async def reencode_audio(self) -> None:
        """Mix and encode the master audio again, then mux again."""
        self.applied.append("reencode_audio")
        run = self.run
        await _build_master(run.timeline, self.bounds, run.files, work=run.work, settings=run.settings,
                            script_flag=self.script_flag, master=self.master, check_cancelled=_check(run.ctx))
        await self.mux()

    async def remux(self) -> None:
        """Join the chunks and mux the MP4 again."""
        self.applied.append("remux")
        await self.mux()


async def render_motion(ctx: Any, timeline: Timeline, out_path: Path, settings: Settings, *,
                        burn_captions: bool = False, soft_subtitles: bool = False, token: str | None = None,
                        served: Mapping[str, Any] | None = None, work_dir: Path | None = None,
                        cache: ChunkCache | None = None, local_files: Mapping[str, Path] | None = None,
                        title: str | None = None, config: MotionConfig | None = None,
                        refresh_served: Callable[[], Mapping[str, Any]] | None = None) -> MotionResult:
    """Render a Dynamic ``timeline`` to ``out_path`` (see the module docstring).

    ``ctx`` is the job context (``check_cancelled``, ``progress``, ``log``, ``assets``, ``storage``, ``payload``);
    ``token`` is the scoped render token for the stage page (minted from the timeline's version when None);
    ``served`` is the timeline as the page receives it (resolved URLs; the preflight's ``PreflightResult.timeline``
    with its remedies), else the page asks the API; ``work_dir`` holds the chunks and side files (ffmpeg's cwd;
    default: next to ``out_path``); ``cache`` reuses chunks of an earlier attempt; ``local_files`` maps asset keys
    to files already on disk; ``refresh_served`` (blocking) returns ``served`` with its presigned URLs signed again
    and is called before every page load (start, recycling, a retried chunk, a remedy), so a page opened an hour into
    a render never loads expired links (chunk keys ignore URLs). Raises :class:`MotionError` (``retryable`` for transient failures), ``StageError``,
    ``FFmpegError``, ``ContainerCheckFailed`` (a check its remedy did not fix) and whatever ``ctx.check_cancelled``
    raises.
    """
    cfg = config or MotionConfig()
    if not is_dynamic(timeline):
        raise MotionError("the motion engine renders Dynamic timelines only", code="not_dynamic")
    if not timeline.scenes:
        raise MotionError("the lecture has no scenes", code="empty")
    started = time.monotonic()
    timings: dict[str, float] = {}
    check = _check(ctx)
    fps = int(timeline.fps)
    capture_size = timeline_stage(timeline)
    out_size = output_size(timeline, settings)
    bounds = scene_frame_bounds(timeline, fps)
    total = total_frames(bounds)
    chunks = plan_chunks(bounds, min_frames=cfg.chunk_min, max_frames=cfg.chunk_max)
    work = Path(work_dir) if work_dir is not None else Path(out_path).parent / "motion-work"
    work = work.resolve()
    await asyncio.to_thread(work.mkdir, parents=True, exist_ok=True)
    files = _Files(ctx, settings, work / "inputs", local_files)
    version = await ffmpeg_version(settings)
    script_flag = filter_script_flag(version)
    enc = EncoderSpec.from_settings(settings, fps, out_size)
    options = dataclasses.replace(cfg.stage, layers={"captions": bool(burn_captions), "stage": True})
    budget = await asyncio.to_thread(CpuBudget.detect)
    want = max(1, min(cfg.workers or motion_workers(settings, budget), len(chunks)))
    sampler = _MemorySampler(cfg.sample_memory_seconds)
    sampler.start()
    lease: SlotLease | None = None
    n_slots = 0
    sessions: list[StageSession] = []
    audio_task: asyncio.Future[str] | None = None
    drivers = contextlib.AsyncExitStack()
    _progress(ctx, "capture", 0.02, "Preparing the motion render")
    try:
        from ..video import branding_file

        bgm = branding_file(settings, timeline.branding.bgm_url) if settings.render_bgm else None
        master = MasterAudioSpec(audio_list="audio.ffconcat", total_seconds=total / fps,
                                 bgm=str(bgm) if bgm else None, bgm_volume=timeline.branding.bgm_volume)
        audio_task = asyncio.ensure_future(_build_master(timeline, bounds, files, work=work, settings=settings,
                                                         script_flag=script_flag, master=master,
                                                         check_cancelled=check))
        narration = {sc.audio_asset_key for sc in timeline.scenes if sc.audio_asset_key}
        for key in sorted({*video_media_keys(timeline), *narration}):  # fail before the capture: a missing clip or
            await files.get(key, required=True)  # narration (cached: the audio task gets the same files)
        if cfg.use_slots:
            slots = CpuSlots.from_settings(settings, budget)

            def waiting() -> None:
                _progress(ctx, "capture", 0.03, "Waiting for a free render slot on this server")

            lease = await slots.acquire(want, check_cancelled=check, on_wait=waiting)
            n_workers = n_slots = lease.count
        else:
            n_workers = want
        token = token or _mint_token(ctx, settings, timeline)
        origins = await asyncio.to_thread(media_origins, ctx, settings)
        data = hashable_timeline(served if served is not None else timeline.model_dump(mode="json"))
        run = _Run(timeline=timeline, settings=settings, ctx=ctx, work=work, enc=enc, capture_size=capture_size,
                   total=total, keys={}, cache=cache, files=files, cfg=cfg, queue=assignment_order(chunks),
                   media_allowed=frozenset(media_keys({"scenes": data.get("scenes", [])})),
                   refresh_served=refresh_served)
        t0 = time.monotonic()
        if cfg.shared_driver:  # ~150 MB per driver saved for every worker after the first (B1 measurement)
            with contextlib.suppress(ImportError):  # no Playwright: each session reports it as a StageError
                run.driver = await drivers.enter_async_context(_playwright())
        _progress(ctx, "capture", 0.04, f"Opening {n_workers} render page(s)")
        sessions = [_session(run, token=token, served=served, origins=origins, options=options, label=f"worker-{i}")
                    for i in range(n_workers)]
        await _start_sessions(sessions)
        timings["boot"] = time.monotonic() - t0
        info = await sessions[0].info()
        info.check(size=capture_size, fps=fps, frames=total)
        problem = scene_mismatch(info, bounds)
        if problem:
            raise MotionError(f"the render stage does not match the timeline: {problem}", code="stage_contract")
        chromium = sessions[0].chromium_version
        bundle = await asyncio.to_thread(bundle_digest, Path(settings.web_dir))
        identities = await asyncio.to_thread(_media_identities, data, settings)
        encoder_fp = {**enc.fingerprint(capture_size), "ffmpeg": list(version) if version else None}
        capture_fp = options.capture_fingerprint()
        run.keys = {c.index: chunk_key(timeline, c, bundle=bundle, encoder=encoder_fp, capture=capture_fp,
                                       chromium=chromium, media=identities, data=data) for c in chunks}
        wstats = [WorkerStat(i) for i in range(len(sessions))]
        t0 = time.monotonic()
        _progress(ctx, "capture", 0.08, f"Capturing {total} frames with {len(sessions)} worker(s)")
        await gather_or_cancel([*(_worker(run, s, i, wstats[i]) for i, s in enumerate(sessions)),
                                _audio_guard(audio_task)])  # a failed audio master stops the capture at once
        timings["capture"] = time.monotonic() - t0
        webgl = sessions[0].webgl
        for s, w in zip(sessions, wstats, strict=False):
            w.pages = s.pages_opened
        closing, sessions = sessions, []
        await asyncio.gather(*(s.close() for s in closing))  # a redirect off the allowed origins fails here
        trips = [t for s in closing for t in s.messages()[1]]
        if trips:
            run.warnings.append(f"the stage's timer guard tripped {len(trips)} time(s): {trips[0][:200]}")
        if lease is not None:
            lease.release()

        # --- join, mux, check ------------------------------------------------------------------
        t0 = time.monotonic()
        audio_file = await audio_task  # the master track, encoded while the frames were captured
        timings["audio_wait"] = time.monotonic() - t0
        _progress(ctx, "mux", 0.87, "Joining the chunks")
        total_seconds = total / fps
        meta_title = str(title or timeline.meta.get("session_title") or "Lecture")
        side = {
            "chunks.ffconcat": concat_list([f"{c.name}.ts" for c in chunks]),
            "chapters.ffmeta": to_ffmetadata(timeline.chapters, total_seconds, title=meta_title,
                                             extra={"artist": "REC - Aadhi EduEngine", "album": meta_title}),
        }
        soft = bool(soft_subtitles) and bool(timeline.captions) and not burn_captions
        if soft:
            side["captions.soft.srt"] = to_srt(timeline.captions)
        spec = MotionFinalSpec(video_list="chunks.ffconcat", audio=audio_file, metadata="chapters.ffmeta",
                               total_seconds=total_seconds, output="motion.mp4",
                               soft_subtitles="captions.soft.srt" if soft else None,
                               subtitle_language=subtitle_language_code(timeline.language))
        mux = final_mux_command(spec)
        assembly = _Assembly(run=run, chunks=chunks, bounds=bounds, script_flag=script_flag, master=master,
                             mux_args=mux.args, side={**side, **mux.files},
                             reopen=lambda: _session(run, token=str(token), served=served, origins=origins,
                                                     options=options, label="remedy"))
        t0 = time.monotonic()
        await assembly.mux()
        timings["mux"] = time.monotonic() - t0
        _progress(ctx, "check", 0.9, "Checking the video")
        t0 = time.monotonic()
        produced = work / "motion.mp4"
        chunk_files = [ChunkFile(c.index, work / f"{c.name}.ts", c.frames) for c in chunks]
        narrated = any(s.audio_asset_key for s in timeline.scenes)  # a silent lecture still gets a (silent) track

        async def inspect() -> ContainerQA:
            return await inspect_container(produced, settings=settings, fps=fps, expected_frames=total,
                                           expect_audio=narrated, expect_subtitles=soft, chunks=chunk_files,
                                           size=out_size, check_cancelled=check)

        report = await verify_container(inspect, assembly, log_line=lambda line: _log(ctx, line, level="warning"))
        timings["checks"] = time.monotonic() - t0
        out = Path(out_path)
        await asyncio.to_thread(_move, produced, out)
        timings["total"] = time.monotonic() - started
        sampler.sample()
        return MotionResult(
            output=out, frames=total, fps=fps, size=out_size, workers=len(wstats), slots=n_slots,
            chunks=[run.stats[c.index] for c in chunks if c.index in run.stats], worker_stats=wstats,
            container=report, timings=timings, chromium=chromium, webgl=webgl,
            budget={"physical": budget.physical, "quota": budget.quota, "affinity": budget.affinity,
                    "auto_workers": budget.auto_workers(), "wanted": want},
            memory_mb=dict(sampler.peaks), warnings=run.warnings,
            encoder={"preset": enc.preset, "crf": enc.crf, "ffmpeg": ".".join(map(str, version)) if version else None})
    finally:
        await sampler.stop()
        if sessions:
            await asyncio.gather(*(s.close() for s in sessions), return_exceptions=True)
        with contextlib.suppress(Exception):
            await drivers.aclose()  # after every browser it launched is closed
        if lease is not None:
            lease.release()
        if audio_task is not None:
            if not audio_task.done():
                audio_task.cancel()
            with contextlib.suppress(BaseException):
                await audio_task  # also reads a failed task's exception (no "never retrieved" log)


__all__ = [
    "ENGINE",
    "ChunkStat",
    "MotionConfig",
    "MotionError",
    "MotionResult",
    "WorkerStat",
    "hashable_timeline",
    "is_dynamic",
    "render_motion",
    "scene_audio_spec",
    "video_media_keys",
]
