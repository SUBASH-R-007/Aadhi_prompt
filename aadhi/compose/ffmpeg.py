"""ffmpeg / ffprobe: async runner, probing and pure filter-graph builders for the MP4 renderer.

Safety: every input is preceded by ``-protocol_whitelist file,pipe`` (the option is per input),
images and concat/metadata lists are opened with an explicit ``-f`` (concat lists with
``-safe 1``), processes run without a shell, with an allow-listed environment, a timeout, an
optional cancel callback and a process-tree kill. Errors carry only a redacted tail of stderr.

Builders return a :class:`Command` (argument list + the side files it references: filtergraph
script, concat lists) and never touch the filesystem, so they are unit-testable.

Compatibility: ffmpeg 5.1 (Debian bookworm, the Docker runtime) through 8.x. Filtergraph scripts
are passed with ``-filter_complex_script`` (accepted by every supported version; deprecated since
7.0) unless :func:`ffmpeg_version` reports >= 7.0, where ``-/filter_complex <file>`` is used.

State PNGs: a scene's render-mode screenshots are NOT opened as one input each (memory and argv
would grow with the number of states). They are fed as ONE concat stream that shows every state back to
back; each frame of a cross-fade window is its own PNG, the previous and the new state mixed in
premultiplied alpha (:class:`StateMix`, written by ``frames.write_state_mixes`` before the encode). A
translucent ("glass") panel present in both states therefore keeps its opacity through the change, which
two stacked translucent layers (the old state under the new one fading in) would darken. Memory and
command length are constant in the number of states.

The concat demuxer's stream time base is the nested PNG demuxer's default frame rate (1/25 s), so
list durations are written on a virtual 25-units-per-frame clock (exact) and re-timed with
``settb``/``setpts`` to the real frame rate before ``fps`` duplicates frames.

Colour (``bt709=True``, ``RENDER_COLOR_BT709``): RGB sources (state PNGs, stills, the colour
clock) are converted to YUV with the BT.709 matrix (limited range) by an explicit ``scale``, YUV
sources are only relabelled (the mascot clip, passed through unconverted, and untagged HD media videos
such as the intro logo, AI clips and Manim videos: browsers show untagged HD as BT.709, so they are
decoded as BT.709 instead of swscale's BT.601 default; tagged media keep their own matrix), every layer is
tagged BT.709 (``setparams``) so filters never auto-convert between them, and the encoder writes
BT.709 VUI / ``colr`` tags: players then show the colours the browser showed. Off = untagged
BT.601 conversion (the original output, bit for bit).

Frame grid: every segment has an exact frame count, the same encoder parameters and the track
timescale ``fps * 512``, so after the concat copy frame ``n`` sits at pts ``n * 512`` (checked by
the slow tests and by ``aadhi.compose.qa`` on every render).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import math
import os
import re
import subprocess
import sys
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import psutil

from ..config import Settings
from ..schemas.timeline import KenBurns
from .aio import DeadlineExceeded, await_polling, reap

log = logging.getLogger(__name__)

AUDIO_RATE = 48_000
PROTOCOLS = "file,pipe"
STATE_FADE_SECONDS = 0.15  # cross-fade between render-mode state PNGs
MASCOT_CROSSFADE_SECONDS = 0.6  # mascot clip crossfade on a position change (web/js/player/mascot.js CROSSFADE_MS)
BT709_CONVERT = "scale=out_color_matrix=bt709:out_range=tv"
BT709_TAG = "setparams=range=tv:colorspace=bt709:color_primaries=bt709:color_trc=bt709"
BT709_TAG_KEEP_RANGE = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"  # range tagged already
BT709_CODEC_TAGS = ("-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709")
VIRTUAL_RATE = 25  # time base (1/25 s) of the concat stream: the PNG demuxer's default frame rate
SCRIPT_FLAG_LEGACY = "-filter_complex_script"  # ffmpeg 2.x .. 8.x (deprecated since 7.0)
SCRIPT_FLAG_MODERN = "-/filter_complex"  # ffmpeg >= 7.0: "-/<option> <file>" reads the value from a file
_STDERR_TAIL_BYTES = 8192
_ENV_ALLOW = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "LANG", "LC_ALL",
              "FONTCONFIG_FILE", "FONTCONFIG_PATH", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "COMSPEC")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class FFmpegError(RuntimeError):
    """ffmpeg/ffprobe failed (non-zero exit, timeout, or missing binary)."""

    def __init__(self, message: str, *, returncode: int | None = None, stderr_tail: str = "") -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr_tail = stderr_tail


# ---------------------------------------------------------------------------
# Process handling
# ---------------------------------------------------------------------------


def subprocess_env() -> dict[str, str]:
    """Allow-listed environment for media subprocesses (no secrets)."""
    return {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOW}


def kill_tree(pid: int) -> None:
    """Kill a process and all its children (best effort, blocking up to 5 s)."""
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return
    procs = []
    with contextlib.suppress(psutil.Error):
        procs = parent.children(recursive=True)
    procs.append(parent)
    for p in procs:
        with contextlib.suppress(psutil.Error):
            p.kill()
    psutil.wait_procs(procs, timeout=5)


async def _drain(stream: asyncio.StreamReader | None, sink: deque[bytes] | bytearray, limit: int | None) -> None:
    if stream is None:
        return
    size = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return
        if isinstance(sink, bytearray):
            if limit is None or len(sink) < limit:
                sink.extend(chunk)
            continue
        sink.append(chunk)
        size += len(chunk)
        while limit is not None and size > limit and len(sink) > 1:
            size -= len(sink.popleft())


def _tail(chunks: deque[bytes], settings: Settings | None) -> str:
    text = b"".join(chunks)[-_STDERR_TAIL_BYTES:].decode("utf-8", "replace").strip()
    return settings.redact(text) if settings else text


async def run_process(
    cmd: Sequence[str],
    *,
    timeout: float,
    cwd: Path | None = None,
    settings: Settings | None = None,
    capture_stdout: bool = False,
    stdout_limit: int = 64 * 1024 * 1024,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[bytes, str]:
    """Run ``cmd`` (no shell); return ``(stdout, stderr_tail)``. Raises :class:`FFmpegError`.

    ``check_cancelled`` is polled about once a second while the process runs; whatever it raises
    (``JobCancelled``) propagates after the process tree was killed.
    """
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    name = Path(str(cmd[0])).name
    try:
        proc = await asyncio.create_subprocess_exec(
            *[str(c) for c in cmd],
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE if capture_stdout else asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd) if cwd else None,
            env=subprocess_env(),
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise FFmpegError(f"{name} not found") from exc
    err: deque[bytes] = deque()
    out = bytearray()
    work = asyncio.gather(_drain(proc.stderr, err, _STDERR_TAIL_BYTES * 4), _drain(proc.stdout, out, stdout_limit),
                          proc.wait())
    try:
        await await_polling(work, timeout=timeout, check_cancelled=check_cancelled)
    except DeadlineExceeded:
        await asyncio.to_thread(kill_tree, proc.pid)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 10)
        raise FFmpegError(f"{name} timed out after {timeout:.0f}s", stderr_tail=_tail(err, settings)) from None
    except BaseException:
        await asyncio.to_thread(kill_tree, proc.pid)
        await reap(work)
        raise
    tail = _tail(err, settings)
    if proc.returncode != 0:
        last = tail.splitlines()[-1] if tail else ""
        raise FFmpegError(f"{name} exited with {proc.returncode}: {last}", returncode=proc.returncode,
                          stderr_tail=tail)
    return bytes(out), tail


def ffmpeg_base(settings: Settings) -> list[str]:
    """Global ffmpeg flags: no banner, no stdin, overwrite, errors only."""
    return [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-nostats"]


async def run_ffmpeg(args: Sequence[str], *, settings: Settings, timeout: float = 1800, cwd: Path | None = None,
                     check_cancelled: Callable[[], None] | None = None) -> str:
    """Run ffmpeg with the safe global flags prepended; returns the stderr tail."""
    _, tail = await run_process([*ffmpeg_base(settings), *args], timeout=timeout, cwd=cwd, settings=settings,
                                check_cancelled=check_cancelled)
    return tail


# ---------------------------------------------------------------------------
# Version detection (filtergraph script flag)
# ---------------------------------------------------------------------------

_VERSION_RE = re.compile(r"ffmpeg version\s+n?(\d+)\.(\d+)")
_LAVFI_RE = re.compile(r"libavfilter\s+(\d+)\.\s*(\d+)")
_VERSION_CACHE: dict[str, tuple[int, int] | None] = {}


def parse_ffmpeg_version(text: str) -> tuple[int, int] | None:
    """``(major, minor)`` from ``ffmpeg -version`` output; git builds map libavfilter's major."""
    m = _VERSION_RE.search(text or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    m = _LAVFI_RE.search(text or "")
    if m and int(m.group(1)) >= 7:  # libavfilter 7 = ffmpeg 4, 8 = 5, 9 = 6, 10 = 7, 11 = 8
        return int(m.group(1)) - 3, 0
    return None


def filter_script_flag(version: tuple[int, int] | None) -> str:
    """``-/filter_complex`` on ffmpeg >= 7, else ``-filter_complex_script`` (also for unknown versions)."""
    return SCRIPT_FLAG_MODERN if version is not None and version[0] >= 7 else SCRIPT_FLAG_LEGACY


async def ffmpeg_version(settings: Settings, *, timeout: float = 30) -> tuple[int, int] | None:
    """Installed ffmpeg version (cached per binary; None if it cannot be determined)."""
    key = str(settings.ffmpeg_path)
    if key in _VERSION_CACHE:
        return _VERSION_CACHE[key]
    try:
        out, _ = await run_process([settings.ffmpeg_path, "-hide_banner", "-version"], timeout=timeout,
                                   settings=settings, capture_stdout=True, stdout_limit=1 << 16)
    except FFmpegError as exc:
        log.warning("could not determine the ffmpeg version: %s", exc)
        return None
    version = parse_ffmpeg_version(out.decode("utf-8", "replace"))
    _VERSION_CACHE[key] = version
    return version


# ---------------------------------------------------------------------------
# Inputs and probing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Input:
    """One ``-i`` input with its per-input options."""

    path: str
    fmt: str | None = None
    options: tuple[str, ...] = ()

    def args(self) -> list[str]:
        """``-protocol_whitelist file,pipe [-f fmt] [options] -i path``."""
        out = ["-protocol_whitelist", PROTOCOLS]
        if self.fmt:
            out += ["-f", self.fmt]
        return [*out, *self.options, "-i", self.path]


def image_input(path: str | Path, fps: int) -> Input:
    """Single still image (explicit image2 demuxer, no filename pattern expansion)."""
    return Input(str(path), "image2", ("-pattern_type", "none", "-framerate", str(fps)))


def video_input(path: str | Path, *, loop: bool = False, audio: bool = True, gif: bool = False) -> Input:
    """Video (or GIF) input; ``loop`` repeats it forever (the filtergraph trims it)."""
    opts: list[str] = []
    if gif:
        opts += ["-ignore_loop", "0"] if loop else []
        fmt: str | None = "gif"
    else:
        fmt = None
        if loop:
            opts += ["-stream_loop", "-1"]
    if not audio:
        opts.append("-an")
    return Input(str(path), fmt, tuple(opts))


def concat_input(path: str | Path) -> Input:
    """ffconcat list (``-f concat -safe 1``: only simple relative file names)."""
    return Input(str(path), "concat", ("-safe", "1"))


def state_list_input(path: str | Path) -> Input:
    """ffconcat list of state PNGs, decoded single-threaded.

    A frame-threaded PNG decoder keeps one decoded 1080p RGBA frame per thread in flight; one
    thread keeps the segment's memory flat (PNG entries are rare: one per state change).
    """
    return Input(str(path), "concat", ("-safe", "1", "-threads", "1"))


def is_image_path(path: str | Path) -> bool:
    """True for still-image extensions (opened with the image2 demuxer)."""
    return Path(str(path)).suffix.lower() in IMAGE_EXTS


@dataclass
class MediaProbe:
    """Subset of ffprobe output used by the renderer."""

    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    has_video: bool = False
    has_audio: bool = False
    chapters: list[dict] = field(default_factory=list)
    format_name: str = ""
    pix_fmt: str | None = None
    color_space: str | None = None  # the video's matrix tag (None: untagged / "unknown")
    color_range: str | None = None  # "tv" / "pc" (None: untagged)


def _rate(v: str | None) -> float | None:
    if not v or v in ("0/0", "N/A"):
        return None
    try:
        num, _, den = v.partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def parse_probe(data: dict) -> MediaProbe:
    """Parse ``ffprobe -print_format json -show_format -show_streams -show_chapters`` output."""
    p = MediaProbe(format_name=str(data.get("format", {}).get("format_name", "")))
    try:
        p.duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        p.duration = None
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not p.has_video and s.get("disposition", {}).get("attached_pic") != 1:
            p.has_video = True
            p.video_codec = s.get("codec_name")
            p.width, p.height = s.get("width"), s.get("height")
            p.fps = _rate(s.get("avg_frame_rate")) or _rate(s.get("r_frame_rate"))
            p.pix_fmt, p.color_space, p.color_range = (
                v if isinstance(v, str) and v and v != "unknown" else None
                for v in (s.get("pix_fmt"), s.get("color_space"), s.get("color_range")))
            if p.duration is None:
                with contextlib.suppress(TypeError, ValueError):
                    p.duration = float(s.get("duration"))
        elif s.get("codec_type") == "audio" and not p.has_audio:
            p.has_audio = True
            p.audio_codec = s.get("codec_name")
    p.chapters = [{"start": float(c.get("start_time", 0)), "end": float(c.get("end_time", 0)),
                   "title": (c.get("tags") or {}).get("title", "")} for c in data.get("chapters", [])]
    return p


async def probe(path: str | Path, *, settings: Settings, timeout: float = 60) -> MediaProbe:
    """Probe a local media file (explicit image demuxer for stills)."""
    cmd = [settings.ffprobe_path, "-hide_banner", "-v", "error", "-protocol_whitelist", PROTOCOLS]
    if is_image_path(path):
        cmd += ["-f", "image2", "-pattern_type", "none"]
    cmd += ["-print_format", "json", "-show_format", "-show_streams", "-show_chapters", "-i", str(path)]
    out, _ = await run_process(cmd, timeout=timeout, settings=settings, capture_stdout=True)
    try:
        return parse_probe(json.loads(out.decode("utf-8", "replace") or "{}"))
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON") from exc


# ---------------------------------------------------------------------------
# Filter-graph builders (pure)
# ---------------------------------------------------------------------------


def ft(x: float, digits: int = 3) -> str:
    """Compact non-negative decimal for filter arguments (millisecond precision by default)."""
    s = f"{max(0.0, float(x)) + 0.0:.{digits}f}".rstrip("0").rstrip(".")
    return s or "0"


def even(n: float) -> int:
    """Nearest even integer >= 2 (yuv420p friendly sizes)."""
    v = round(n / 2.0) * 2
    return max(2, v)


def to_yuv(fmt: str, *, bt709: bool) -> str:
    """Filters converting an RGB(A) (or any) chain to ``fmt``.

    ``bt709=False``: ``format=<fmt>`` (swscale's default BT.601 matrix, untagged: the original
    output). ``bt709=True``: through RGBA, then BT.709 matrix / limited range, tagged BT.709.
    """
    if not bt709:
        return f"format={fmt}"
    return f"format=rgba,{BT709_CONVERT},format={fmt},{BT709_TAG}"


def relabel_bt709(bt709: bool) -> str:
    """``,setparams=...`` tagging an already-YUV chain BT.709 without touching its pixels ('' when off)."""
    return f",{BT709_TAG}" if bt709 else ""


def alpha_fade_in(seconds: float, fps: int) -> str:
    """``,fade`` ramping a layer's alpha from 0 to 1 over the first ``seconds`` ('' for 0).

    Enabled on the ramp only, so later (possibly shared, duplicated) frames pass untouched.
    """
    if seconds <= 0:
        return ""
    d = ft(seconds, 6)
    return f",fade=t=in:st=0:d={d}:alpha=1:enable='lte(t,{ft(seconds + 0.5 / fps, 6)})'"


@dataclass(frozen=True)
class Rect:
    """Pixel rectangle in output coordinates."""

    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_stage(cls, rect: dict | None, *, stage: tuple[int, int], out: tuple[int, int]) -> Rect | None:
        """Scale a render-mode rect (stage px; keys x/left, y/top, width/w, height/h) to the output."""
        if not rect or not isinstance(rect, dict):
            return None
        try:
            x = float(rect.get("x", rect.get("left", 0)))
            y = float(rect.get("y", rect.get("top", 0)))
            w = float(rect.get("width", rect.get("w", 0)))
            h = float(rect.get("height", rect.get("h", 0)))
        except (TypeError, ValueError):
            return None
        sx, sy = out[0] / stage[0], out[1] / stage[1]
        x0, y0 = max(0.0, x * sx), max(0.0, y * sy)
        x1, y1 = min(float(out[0]), (x + w) * sx), min(float(out[1]), (y + h) * sy)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return None
        return cls(round(x0), round(y0), even(x1 - x0), even(y1 - y0))


def fit_filter(w: int, h: int, fit: Literal["contain", "cover"]) -> str:
    """Scale into a ``w x h`` box: contain = letterbox with transparent bars, cover = crop."""
    if fit == "cover":
        return f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,crop={w}:{h},setsar=1"
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,format=rgba,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x00000000,setsar=1")


def still_loop_filter(fps: int, frames: int) -> str:
    """Repeat a single decoded frame ``frames`` times at ``fps`` (decodes the image once)."""
    return f"loop=loop={max(0, frames - 1)}:size=1:start=0,setpts=N/{fps}/TB"


def ken_burns_filter(kb: KenBurns, w: int, h: int, *, fps: int, frames: int, supersample: int = 2) -> str:
    """Linear pan/zoom from ``kb.start`` to ``kb.end`` over ``frames`` frames via ``zoompan``.

    The still is first fitted (cover) at ``supersample`` x the target size so zoompan's integer
    crop offsets do not visibly jitter.
    """
    sw, sh = even(w * supersample), even(h * supersample)
    n = max(1, frames - 1)
    s0, s1 = max(1.0, kb.start.scale), max(1.0, kb.end.scale)

    def lerp(a: float, b: float) -> str:
        return f"({a:.4f}+({b - a:.4f})*on/{n})"

    z = lerp(s0, s1)
    cx, cy = lerp(kb.start.cx, kb.end.cx), lerp(kb.start.cy, kb.end.cy)
    x = f"max(0,min(iw-iw/zoom,{cx}*iw-iw/zoom/2))"
    y = f"max(0,min(ih-ih/zoom,{cy}*ih-ih/zoom/2))"
    return (f"scale={sw}:{sh}:force_original_aspect_ratio=increase:flags=lanczos,crop={sw}:{sh},setsar=1,"
            f"{still_loop_filter(fps, frames)},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={w}x{h}:fps={fps},setsar=1")


def clock_input(*, width: int, height: int, fps: int, frames: int, color: str = "#1A0B2E") -> Input:
    """Solid-colour frames as a lavfi INPUT (not an in-graph source).

    Input frames are paced by ffmpeg's scheduler together with the outputs; an in-graph source
    (``color``, a looped still) would let the overlays race ahead and pile up blended frames while
    a state stream waits for its next list entry.
    """
    c = color.replace("#", "0x")
    return Input(f"color=c={c}:s={width}x{height}:r={fps}:d={ft(frames / fps + 1)}", "lavfi")


def loop_phase_frames(start_frame: int, *, fps: int, clip_seconds: float | None) -> int:
    """Output frames to skip in a looped clip so its phase follows the absolute time.

    A segment starting at output frame ``start_frame`` shows the clip at
    ``(start_frame / fps) mod clip_seconds``: consecutive segments using the same clip continue
    its loop seamlessly (as the live player's ever-playing mascot clip). 0 for unknown durations.
    """
    if not clip_seconds or clip_seconds <= 0 or start_frame <= 0:
        return 0
    period = max(1, round(clip_seconds * fps))
    return round(((start_frame / fps) % clip_seconds) * fps) % period


def background_filter(kind: Literal["video", "image", "color"], *, width: int, height: int, fps: int, frames: int,
                      in_label: str = "0:v", clock_label: str = "0:v", out_label: str = "bg",
                      offset_frames: int = 0, bt709: bool = False) -> str:
    """Base layer: looped mascot clip (input opened with ``-stream_loop -1 -an``), still, or colour.

    ``image`` and ``color`` are timed by the :func:`clock_input` at ``clock_label``; the still is
    decoded and converted once, then looped as references over the clock frames. A looped clip
    starts ``offset_frames`` output frames into its loop (:func:`loop_phase_frames`; the skipped
    frames are dropped before scaling).
    """
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if kind == "color":  # the clock is the visible background: convert its RGB colour per frame
        clock = f"[{clock_label}]trim=end_frame={frames},setpts=PTS-STARTPTS,{to_yuv('yuv420p', bt709=bt709)}"
    else:  # covered by the still / clip: only its tags matter
        clock = f"[{clock_label}]trim=end_frame={frames},setpts=PTS-STARTPTS,format=yuv420p{relabel_bt709(bt709)}"
    if kind == "video":
        skip = f"trim=start_frame={offset_frames},setpts=PTS-STARTPTS," if offset_frames > 0 else ""
        return (f"[{in_label}]setpts=PTS-STARTPTS,fps={fps},{skip}{cover},trim=end_frame={frames},"
                f"setpts=PTS-STARTPTS,format=yuv420p{relabel_bt709(bt709)}[{out_label}]")
    if kind == "image":
        return ";".join([
            f"{clock}[{out_label}_clk]",
            f"[{in_label}]{cover},{to_yuv('yuv420p', bt709=bt709)},{still_loop_filter(fps, frames)}[{out_label}_img]",
            f"[{out_label}_clk][{out_label}_img]overlay=x=0:y=0:eof_action=pass:repeatlast=0[{out_label}]",
        ])
    return f"{clock}[{out_label}]"


def crossfade_clip_filter(*, width: int, height: int, fps: int, frames: int, in_label: str, out_label: str,
                          offset_frames: int = 0, bt709: bool = False) -> str:
    """The previous scene's mascot clip fading out over ``frames`` frames (alpha), continuing its loop.

    Overlaid on the new clip at the start of a scene whose mascot position changed: the live
    player's 600 ms crossfade between clips. YUV in, YUV(A) out (no matrix conversion).
    """
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    skip = f"trim=start_frame={offset_frames},setpts=PTS-STARTPTS," if offset_frames > 0 else ""
    return (f"[{in_label}]setpts=PTS-STARTPTS,fps={fps},{skip}{cover},trim=end_frame={frames},"
            f"setpts=PTS-STARTPTS,format=yuva420p{relabel_bt709(bt709)},"
            f"fade=t=out:st=0:d={ft(frames / fps, 6)}:alpha=1[{out_label}]")


@dataclass(frozen=True)
class MediaLayer:
    """A media element composited under the render-mode PNGs (segment-relative times).

    A video/GIF starts playing at ``visible_from`` (its frame 0 is shown there; loops wrap relative
    to it), like the player's panels (``currentTime = t - show_at``).
    """

    input_index: int
    kind: Literal["video", "image", "gif"]
    rect: Rect
    fit: Literal["contain", "cover"] = "contain"
    end_behavior: Literal["loop", "freeze"] = "loop"
    ken_burns: KenBurns | None = None
    visible_from: float = 0.0
    visible_to: float | None = None  # None = until the end of the segment
    # YUV video without a matrix tag (``untagged_hd``): browsers show untagged HD as BT.709, so with bt709 it
    # is relabelled (decoded as BT.709), not converted from swscale's BT.601 default. ``range_tagged``: its
    # range tag is kept.
    untagged_yuv: bool = False
    range_tagged: bool = False


def untagged_hd(p: MediaProbe) -> bool:
    """A YUV video of at least 720 lines with no colour matrix tag (shown as BT.709 by browsers)."""
    return (p.has_video and not p.color_space and (p.height or 0) >= 720
            and (p.pix_fmt or "yuv").startswith("yuv"))


def media_filter(layer: MediaLayer, *, fps: int, frames: int, out_label: str, fade_in: float = 0.0,
                 bt709: bool = False) -> str:
    """Media scaled into its rect: video (delayed to ``visible_from``, loop/freeze), Ken-Burns or still.

    ``fade_in`` ramps the layer's alpha in over the segment's first seconds (scene-layer fade);
    ``bt709`` converts it with the BT.709 matrix (see the module docstring).
    """
    duration = frames / fps
    w, h = layer.rect.w, layer.rect.h
    src = f"[{layer.input_index}:v]"
    fade = alpha_fade_in(fade_in, fps)
    if layer.kind == "image":
        if layer.ken_burns is not None:
            body = ken_burns_filter(layer.ken_burns, w, h, fps=fps, frames=frames)
            return f"{src}{body},{to_yuv('yuva420p', bt709=bt709)}{fade}[{out_label}]"
        # converted once to the overlay's input format, then looped (references, not copies)
        return (f"{src}{fit_filter(w, h, layer.fit)},{to_yuv('yuva420p', bt709=bt709)},"
                f"{still_loop_filter(fps, frames)}{fade}[{out_label}]")
    tag = ""
    if bt709 and layer.kind == "video" and layer.untagged_yuv:  # the RGBA conversion below follows the tag
        tag = f"{BT709_TAG_KEEP_RANGE if layer.range_tagged else BT709_TAG},"
    chain = f"{tag}setpts=PTS-STARTPTS,fps={fps},{fit_filter(w, h, layer.fit)},format=rgba"
    pad: list[str] = []
    delay = round(max(0.0, layer.visible_from) * fps)
    if delay > 0:  # transparent frames until the media starts: frame 0 of the clip lands on visible_from
        pad += [f"start={delay}", "start_mode=add", "color=0x00000000"]
    if layer.end_behavior == "freeze":
        pad += ["stop_mode=clone", f"stop_duration={ft(duration + 1)}"]
    if pad:
        chain += ",tpad=" + ":".join(pad)
    out_fmt = to_yuv("yuva420p", bt709=True) if bt709 else "format=rgba"
    return f"{src}{chain},trim=end_frame={frames},setpts=PTS-STARTPTS,{out_fmt}{fade}[{out_label}]"


def overlay_filter(base: str, top: str, out: str, *, x: int = 0, y: int = 0, start: float | None = None,
                   end: float | None = None) -> str:
    """``overlay`` of ``top`` on ``base`` (optionally enabled on ``[start, end]`` only)."""
    enable = ""
    if start is not None:
        enable = f":enable='between(t,{ft(start)},{ft(end if end is not None else 1e9)})'"
    return f"[{base}][{top}]overlay=x={x}:y={y}:eof_action=pass:repeatlast=0{enable}[{out}]"


# --- render-mode state PNGs ---------------------------------------------------------------------

_CONCAT_SAFE_PART = re.compile(r"[A-Za-z0-9_\-][A-Za-z0-9_.\-]*")


def concat_safe(path: str) -> bool:
    """True if ffmpeg's concat demuxer accepts ``path`` with ``-safe 1`` (relative, simple names)."""
    return bool(path) and all(_CONCAT_SAFE_PART.fullmatch(part) for part in path.split("/"))


def _virtual_duration(frames: int) -> str:
    """``frames`` output frames on the concat stream's virtual 1/25 s clock, as exact decimal seconds."""
    us = frames * (1_000_000 // VIRTUAL_RATE)
    return f"{us // 1_000_000}.{us % 1_000_000:06d}"


def concat_document(entries: Sequence[tuple[str, int | None]]) -> str:
    """ffconcat list of ``(file, frames)``; ``frames`` are written on the virtual 1/25 s clock.

    When durations are given the last file is listed once more (without a duration) so its
    display time is honoured by every ffmpeg version. Raises ValueError for unsafe names.
    """
    lines = ["ffconcat version 1.0"]
    for path, frames in entries:
        if not concat_safe(path):
            raise ValueError(f"unsafe concat entry {path!r}")
        lines.append(f"file '{path}'")
        if frames is not None:
            lines.append(f"duration {_virtual_duration(frames)}")
    if entries and entries[-1][1] is not None:
        lines.append(f"file '{entries[-1][0]}'")
    return "\n".join(lines) + "\n"


def concat_list(files: Sequence[str]) -> str:
    """ffconcat document listing ``files`` (simple relative names generated by the renderer)."""
    return concat_document([(f, None) for f in files])


@dataclass(frozen=True)
class StateFrame:
    """A render-mode PNG (path relative to the work dir) shown from ``start`` (segment seconds)."""

    path: str
    start: float


@dataclass(frozen=True)
class StateMix:
    """One cross-fade frame: ``old`` and ``new`` (state PNGs) mixed in premultiplied alpha, ``weight`` of
    ``new``, written to ``out`` (work-dir relative, next to ``new``) by ``frames.write_state_mixes``."""

    old: str
    new: str
    weight: float
    out: str


@dataclass(frozen=True)
class StatePlan:
    """Frame-exact layout of the state stream of a segment.

    ``base``: (png, frames) back to back covering the segment: each state, and before it (from its start)
    its cross-fade window as one mixed PNG per frame (``mixes`` lists the distinct ones to write).
    """

    base: tuple[tuple[str, int], ...] = ()
    mixes: tuple[StateMix, ...] = ()
    fade_frames: int = 0


def _mix(old: str, new: str, j: int, fade_frames: int) -> StateMix:
    """The ``j``-th frame of the cross-fade from ``old`` to ``new``: weight ``(j + 1) / (fade_frames + 1)``."""
    digest = hashlib.sha1(f"{old}|{new}|{j}|{fade_frames}".encode(), usedforsecurity=False).hexdigest()[:16]
    folder = new.rsplit("/", 1)[0] + "/" if "/" in new else ""
    return StateMix(old, new, (j + 1) / (fade_frames + 1), f"{folder}mix-{digest}.png")


def plan_states(states: Sequence[StateFrame], *, fps: int, frames: int, fade: float = STATE_FADE_SECONDS,
                blank: str | None = None) -> StatePlan:
    """Place states on frames (a state is visible from the first frame at or after its start).

    States landing on the same frame keep the later one; states at or after the segment end are
    dropped; before the first state the transparent ``blank`` is shown (e.g. the intro logo).
    A new state fades in over ``round(fade * fps)`` frames (cut short when the next state arrives
    earlier), mixed with the previous one frame by frame (:class:`StateMix`). ``blank`` is required for
    cross-fades and for a first state that starts after frame 0 (ValueError otherwise).
    """
    if not states or frames <= 0:
        return StatePlan()
    starts: list[int] = []
    paths: list[str] = []
    for _, state in sorted(enumerate(states), key=lambda p: (p[1].start, p[0])):
        k = max(0, math.ceil(state.start * fps - 1e-6))
        if k >= frames:
            break
        if starts and k <= starts[-1]:
            paths[-1] = state.path  # same frame: the later state wins
            continue
        starts.append(k)
        paths.append(state.path)
    if not starts:
        return StatePlan()
    if starts[0] > 0:
        if blank is None:
            raise ValueError("a transparent blank state PNG is required before the first state")
        starts.insert(0, 0)
        paths.insert(0, blank)
    fade_frames = max(1, round(fade * fps)) if fade > 0 and len(starts) > 1 else 0
    if fade_frames and blank is None:
        raise ValueError("a transparent blank state PNG is required for cross-fades")
    count = len(starts)
    nexts = [*starts[1:], frames]
    # state i is shown in full once its cross-fade window [starts[i], switch[i]) is over (the window is
    # cut short when state i+1 arrives earlier); during the window the previous state is mixed with it
    switch = [0] + [min(starts[i] + fade_frames, nexts[i]) for i in range(1, count)]
    base: list[tuple[str, int]] = []
    mixes: dict[str, StateMix] = {}
    for i in range(count):
        for j in range(switch[i] - starts[i]):  # one mixed PNG per window frame (none without fades)
            mix = _mix(paths[i - 1], paths[i], j, fade_frames)
            mixes.setdefault(mix.out, mix)
            base.append((mix.out, 1))
        if nexts[i] > switch[i]:  # (only the last state can be left with none: it starts in the last window)
            base.append((paths[i], nexts[i] - switch[i]))
    return StatePlan(base=tuple(base), mixes=tuple(mixes.values()), fade_frames=fade_frames)


def state_mixes(spec: SegmentSpec) -> tuple[StateMix, ...]:
    """The cross-fade PNGs ``build_segment(spec)`` reads (write them with ``frames.write_state_mixes``)."""
    return plan_states(spec.states, fps=spec.fps, frames=spec.frames, fade=spec.state_fade,
                       blank=spec.blank_state).mixes


def state_stream_filter(input_index: int, *, fps: int, frames: int, size: tuple[int, int] | None, out_label: str,
                        fade_in: float = 0.0, bt709: bool = False) -> str:
    """A concat stream of state PNGs re-timed to ``fps`` (scaled/converted once per list entry).

    Cross-fades are already in the stream (mixed PNGs, :func:`plan_states`). ``fade_in`` (the scene-layer
    fade at the segment start) runs after ``fps`` but is enabled on its first frames only, so it copies
    just those duplicated frames.
    """
    scale = f",scale={size[0]}:{size[1]}:flags=lanczos" if size else ""
    convert = f"{BT709_CONVERT},format=yuva420p,{BT709_TAG}" if bt709 else "format=yuva420p"
    chain = (f"[{input_index}:v]settb=1/{VIRTUAL_RATE * fps},setpts=PTS*{VIRTUAL_RATE}/{fps},format=rgba{scale},"
             f"{convert}")
    return f"{chain},fps={fps},trim=end_frame={frames}{alpha_fade_in(fade_in, fps)}[{out_label}]"


# --- audio ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AudioClip:
    """An audio input placed at ``delay`` seconds (optionally trimmed / faded out)."""

    input_index: int
    delay: float = 0.0
    duration: float | None = None
    fade_out: float = 0.0
    volume: float = 1.0


def audio_mix_filter(clips: Sequence[AudioClip], *, total_samples: int, out_label: str = "aout") -> str:
    """Delay + mix clips (no normalisation) into exactly ``total_samples`` of 48 kHz stereo."""
    fmt = f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    exact = f"apad=whole_len={total_samples},atrim=end_sample={total_samples},asetpts=PTS-STARTPTS"
    if not clips:
        return f"anullsrc=r={AUDIO_RATE}:cl=stereo,{exact}[{out_label}]"
    parts: list[str] = []
    labels: list[str] = []
    # One asplit per shared input (ticks are the same file at many times).
    by_input: dict[int, list[AudioClip]] = {}
    for c in clips:
        by_input.setdefault(c.input_index, []).append(c)
    for idx, group in by_input.items():
        srcs = [f"s{idx}_{k}" for k in range(len(group))]
        if len(group) > 1:
            parts.append(f"[{idx}:a]{fmt},asplit={len(group)}" + "".join(f"[{s}]" for s in srcs))
            heads = [f"[{s}]" for s in srcs]
        else:
            heads = [f"[{idx}:a]{fmt},"]
        for k, c in enumerate(group):
            chain: list[str] = []
            if c.duration is not None:
                chain.append(f"atrim=duration={ft(c.duration)}")
                if c.fade_out > 0:
                    chain.append(f"afade=t=out:st={ft(max(0.0, c.duration - c.fade_out))}:d={ft(c.fade_out)}")
            if abs(c.volume - 1.0) > 1e-6:
                chain.append(f"volume={c.volume:.4f}")
            ms = round(max(0.0, c.delay) * 1000)
            if ms > 0:
                chain.append(f"adelay=delays={ms}:all=1")
            label = f"a{idx}_{k}"
            parts.append(f"{heads[k]}{','.join(chain) or 'anull'}[{label}]")
            labels.append(label)
    if len(labels) == 1:
        parts.append(f"[{labels[0]}]{exact}[{out_label}]")
    else:
        parts.append("".join(f"[{lb}]" for lb in labels)
                     + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0:duration=longest,{exact}[{out_label}]")
    return ";\n".join(parts)


# ---------------------------------------------------------------------------
# Segment command
# ---------------------------------------------------------------------------


@dataclass
class Command:
    """ffmpeg arguments plus the side files they reference (work-dir-relative name -> text)."""

    args: list[str]
    files: dict[str, str] = field(default_factory=dict)


@dataclass
class SegmentSpec:
    """Everything needed to encode one scene (or the intro) as video + PCM audio of exact length."""

    width: int
    height: int
    fps: int
    frames: int
    background: Input | None = None
    background_kind: Literal["video", "image", "color"] = "color"
    background_color: str = "#1A0B2E"
    media: list[tuple[Input, MediaLayer]] = field(default_factory=list)
    states: list[StateFrame] = field(default_factory=list)
    state_size: tuple[int, int] = (1920, 1080)
    blank_state: str | None = None  # transparent PNG (work-dir relative) for the cross-fade stream
    state_fade: float = STATE_FADE_SECONDS
    audio: list[tuple[Input, AudioClip]] = field(default_factory=list)
    fade_in: float = 0.0  # whole-picture fade from black (the intro: the start of the video)
    crf: int = 20
    preset: str = "medium"
    layer_fade_in: float = 0.0  # scene-layer fade: media + state layers fade in over the running background
    background_offset_frames: int = 0  # looped clip phase (loop_phase_frames)
    previous_background: Input | None = None  # previous scene's clip, crossfaded out (mascot position change)
    previous_background_offset_frames: int = 0
    crossfade_seconds: float = MASCOT_CROSSFADE_SECONDS
    bt709: bool = False  # BT.709 conversion + tags (module docstring)

    @property
    def duration(self) -> float:
        """Exact segment duration (frames / fps)."""
        return self.frames / self.fps

    @property
    def total_samples(self) -> int:
        """Audio samples (48 kHz) matching the segment duration."""
        return round(self.frames * AUDIO_RATE / self.fps)


def video_codec_args(*, fps: int, crf: int, preset: str, bt709: bool = False) -> list[str]:
    """Identical H.264 parameters for every segment (required by the concat demuxer copy).

    ``bt709`` adds the BT.709 / limited-range colour tags (VUI + ``colr``).
    """
    args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-r", str(fps), "-g", str(fps * 2), "-video_track_timescale", str(fps * 512)]
    return [*args, *BT709_CODEC_TAGS] if bt709 else args


def build_segment(spec: SegmentSpec, *, name: str, script_flag: str = SCRIPT_FLAG_LEGACY) -> Command:
    """Command encoding ``{name}.mp4`` (video) and ``{name}.wav`` (PCM) for one segment.

    Layers back to front: background (looped clip at ``background_offset_frames``), the previous
    clip fading out (``previous_background``: mascot crossfade), media (in rects), the state stream
    (cross-fades mixed in: the PNGs of :func:`state_mixes` must exist in the work dir), then the
    whole-picture fade-in (``fade_in``). ``layer_fade_in`` instead fades only the media and state layers
    in, over the running background (the live player's scene transition). Audio: clips delayed and mixed
    to exactly ``frames / fps`` seconds. Side files: ``{name}.filtergraph`` and the state list
    ``{name}.states.ffconcat`` (only when there are states).
    """
    if not concat_safe(name) or "/" in name:
        raise ValueError(f"unsafe segment name {name!r}")
    inputs: list[Input] = []
    graph: list[str] = []
    files: dict[str, str] = {}
    kind = spec.background_kind if spec.background is not None else "color"
    if kind != "video":  # stills and plain colour are timed by a lavfi clock input
        inputs.append(clock_input(width=spec.width, height=spec.height, fps=spec.fps, frames=spec.frames,
                                  color=spec.background_color))
    if spec.background is not None and kind != "color":
        inputs.append(spec.background)
    graph.append(background_filter(kind, width=spec.width, height=spec.height, fps=spec.fps, frames=spec.frames,
                                   in_label=f"{len(inputs) - 1}:v", clock_label="0:v", out_label="bg",
                                   offset_frames=spec.background_offset_frames if kind == "video" else 0,
                                   bt709=spec.bt709))
    cur = "bg"
    xf_frames = min(spec.frames, round(max(0.0, spec.crossfade_seconds) * spec.fps))
    if spec.previous_background is not None and xf_frames > 0:
        idx = len(inputs)
        inputs.append(spec.previous_background)
        graph.append(crossfade_clip_filter(width=spec.width, height=spec.height, fps=spec.fps, frames=xf_frames,
                                           in_label=f"{idx}:v", out_label="xf",
                                           offset_frames=spec.previous_background_offset_frames, bt709=spec.bt709))
        graph.append(overlay_filter(cur, "xf", "vxf"))
        cur = "vxf"
    for n, (inp, layer) in enumerate(spec.media):
        idx = len(inputs)
        inputs.append(inp)
        layer = dataclasses.replace(layer, input_index=idx)
        graph.append(media_filter(layer, fps=spec.fps, frames=spec.frames, out_label=f"m{n}",
                                  fade_in=spec.layer_fade_in, bt709=spec.bt709))
        start = layer.visible_from if layer.visible_from > 0 or layer.visible_to is not None else None
        graph.append(overlay_filter(cur, f"m{n}", f"vm{n}", x=layer.rect.x, y=layer.rect.y,
                                    start=start, end=layer.visible_to))
        cur = f"vm{n}"
    plan = plan_states(spec.states, fps=spec.fps, frames=spec.frames, fade=spec.state_fade, blank=spec.blank_state)
    size = (spec.width, spec.height) if (spec.width, spec.height) != tuple(spec.state_size) else None
    if plan.base:
        list_name = f"{name}.states.ffconcat"
        files[list_name] = concat_document(plan.base)
        idx = len(inputs)
        inputs.append(state_list_input(list_name))
        graph.append(state_stream_filter(idx, fps=spec.fps, frames=spec.frames, size=size, out_label="sb",
                                         fade_in=spec.layer_fade_in, bt709=spec.bt709))
        graph.append(overlay_filter(cur, "sb", "vsb"))
        cur = "vsb"
    tail = f"trim=end_frame={spec.frames},setpts=PTS-STARTPTS"
    if spec.fade_in > 0:
        tail += f",fade=t=in:st=0:d={ft(spec.fade_in)}"
    graph.append(f"[{cur}]{tail},format=yuv420p[vout]")
    clips: list[AudioClip] = []
    audio_index: dict[tuple[str, str | None, tuple[str, ...]], int] = {}
    for inp, clip in spec.audio:  # each audio file is opened once; repeated clips share it (asplit)
        ident = (inp.path, inp.fmt, inp.options)
        if ident not in audio_index:
            audio_index[ident] = len(inputs)
            inputs.append(inp)
        clips.append(dataclasses.replace(clip, input_index=audio_index[ident]))
    graph.append(audio_mix_filter(clips, total_samples=spec.total_samples, out_label="aout"))
    script = f"{name}.filtergraph"
    files[script] = ";\n".join(graph) + "\n"
    args: list[str] = []
    for inp in inputs:
        args += inp.args()
    args += [script_flag, script,
             "-map", "[vout]", *video_codec_args(fps=spec.fps, crf=spec.crf, preset=spec.preset, bt709=spec.bt709),
             "-frames:v", str(spec.frames), "-an", f"{name}.mp4",
             "-map", "[aout]", "-c:a", "pcm_s16le", "-ar", str(AUDIO_RATE), "-ac", "2", "-vn", f"{name}.wav"]
    return Command(args, files)


# ---------------------------------------------------------------------------
# Final mux
# ---------------------------------------------------------------------------


def filter_escape(value: str) -> str:
    """Escape a value for use inside a filter option (``\\``, ``:``, ``'``, ``,``, ``;``, ``[``, ``]``)."""
    out = value.replace("\\", "/")
    for ch in (":", "'", ",", ";", "[", "]"):
        out = out.replace(ch, "\\" + ch)
    return out


@dataclass
class FinalSpec:
    """Inputs and options of the final mux (paths relative to the work dir)."""

    video_list: str
    audio_list: str
    metadata: str
    total_seconds: float
    output: str
    bgm: str | None = None
    bgm_volume: float = 0.06
    bgm_delay: float = 0.0
    burn_subtitles: str | None = None  # relative .srt path (cwd = work dir)
    fonts_dir: str | None = None  # relative directory with fonts for libass
    font_name: str = "Inter"
    width: int = 1920
    height: int = 1080
    fps: int = 30
    crf: int = 20
    preset: str = "medium"
    audio_bitrate: str = "192k"
    soft_subtitles: str | None = None  # relative .srt embedded as a selectable mov_text caption track
    subtitle_language: str = "und"  # ISO 639-2 tag of the soft track (subtitle_language_code)
    bt709: bool = False  # BT.709 tags on a re-encode (burn-in); copies keep the segments' tags


_ISO639_2 = {"en": "eng", "ta": "tam", "hi": "hin", "te": "tel", "kn": "kan", "ml": "mal", "bn": "ben",
             "mr": "mar", "gu": "guj", "pa": "pan", "or": "ori", "ur": "urd", "fr": "fra", "de": "deu",
             "es": "spa"}


def subtitle_language_code(language: str) -> str:
    """ISO 639-2 code for a BCP-47 tag (``ta-IN`` -> ``tam``); ``und`` when unknown."""
    return _ISO639_2.get((language or "").split("-")[0].strip().lower(), "und")


def bgm_filter(*, narration: str, bgm: str, volume: float, delay: float, total: float, out_label: str = "aout") -> str:
    """Looping BGM at ``volume``, ducked by the narration (sidechaincompress), mixed under it."""
    fmt = f"aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo"
    ms = round(max(0.0, delay) * 1000)
    fade_st = max(0.0, total - delay - 2.0)
    music = f"[{bgm}]{fmt},volume={volume:.4f},atrim=duration={ft(max(0.1, total - delay))}"
    music += f",afade=t=in:st=0:d=1,afade=t=out:st={ft(fade_st)}:d=2"
    if ms > 0:
        music += f",adelay=delays={ms}:all=1"
    return ";\n".join([
        f"[{narration}]{fmt},asplit=2[narr][key]",
        f"{music}[music]",
        "[music][key]sidechaincompress=threshold=0.015:ratio=8:attack=15:release=450:makeup=1[ducked]",
        f"[narr][ducked]amix=inputs=2:normalize=0:duration=first:dropout_transition=0[{out_label}]",
    ])


def subtitles_filter(srt: str, *, fonts_dir: str | None, font_name: str, height: int) -> str:
    """libass caption burn-in with the vendored fonts and a fixed style."""
    size = max(12, round(height / 1080 * 22))
    style = (f"FontName={font_name},FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,"
             "BorderStyle=1,Outline=2,Shadow=0,MarginV=36")
    opts = f"filename={filter_escape(srt)}"
    if fonts_dir:
        opts += f":fontsdir={filter_escape(fonts_dir)}"
    return f"subtitles={opts}:force_style='{style}'"


def build_final(spec: FinalSpec, *, filter_script: str = "final.filtergraph",
                script_flag: str = SCRIPT_FLAG_LEGACY) -> Command:
    """Command muxing the concatenated segments + BGM + chapters (+ optional caption burn-in, and an
    optional soft ``mov_text`` caption track: requested non-default, but the MP4 muxer enables the only caption
    track of a file, so some players show it at once)."""
    inputs = [concat_input(spec.video_list), concat_input(spec.audio_list)]
    graph: list[str] = []
    if spec.bgm:
        inputs.append(video_input(spec.bgm, loop=True))
        graph.append(bgm_filter(narration="1:a", bgm="2:a", volume=spec.bgm_volume, delay=spec.bgm_delay,
                                total=spec.total_seconds))
    else:
        graph.append(f"[1:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=stereo[aout]")
    meta_index = len(inputs)
    inputs.append(Input(spec.metadata, "ffmetadata"))
    subtitle_args: list[str] = []
    if spec.soft_subtitles:
        sub_index = len(inputs)
        inputs.append(Input(spec.soft_subtitles, "srt"))
        subtitle_args = ["-map", f"{sub_index}:s:0", "-c:s", "mov_text",
                         "-metadata:s:s:0", f"language={spec.subtitle_language or 'und'}", "-disposition:s:0", "0"]
    video_args: list[str]
    if spec.burn_subtitles:
        graph.append("[0:v]" + subtitles_filter(spec.burn_subtitles, fonts_dir=spec.fonts_dir,
                                                font_name=spec.font_name, height=spec.height)
                     + ",format=yuv420p[vout]")
        video_args = ["-map", "[vout]", *video_codec_args(fps=spec.fps, crf=spec.crf, preset=spec.preset,
                                                          bt709=spec.bt709)]
    else:
        video_args = ["-map", "0:v", "-c:v", "copy"]
    args: list[str] = []
    for inp in inputs:
        args += inp.args()
    args += [script_flag, filter_script, *video_args, "-map", "[aout]",
             "-c:a", "aac", "-b:a", spec.audio_bitrate, "-ar", str(AUDIO_RATE), "-ac", "2", *subtitle_args,
             "-map_metadata", str(meta_index), "-map_chapters", str(meta_index),
             "-t", ft(spec.total_seconds), "-movflags", "+faststart", spec.output]
    return Command(args, {filter_script: ";\n".join(graph) + "\n"})
