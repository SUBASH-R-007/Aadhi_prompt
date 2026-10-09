"""Post-render output QA: prove the MP4 is whole before it is stored.

``inspect_render`` measures the finished file with ffprobe/ffmpeg (no decoding of the whole
video): the exact video frame count (packets) against the timeline, the declared frame rate,
whether every frame sits on the ``n * (1/fps)`` grid, the audio stream and its loudness
(``volumedetect``, audio only), black edges (letter/pillarbox) on three sampled frames, the colour
tags and the soft caption track. ``RenderQA.problems`` are fatal (the job fails with
``render_invalid`` instead of storing a broken video): no video, a frame count off by more than
one, no audio stream, or silence while the lecture has narration. Everything else is a warning.
The summary is stored on the render (``Render.options["qa"]``) and shown in the Studio.

The pure parsers/statistics here are reused by ``evals/video_qa.py`` (slow tests, parity eval).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from .ffmpeg import PROTOCOLS, FFmpegError, run_process

SILENCE_DB = -60.0  # max_volume at or below this = no audible sound
BLACK_LUMA = 10.0  # mean full-range luma of an edge strip at or below this = black bar
EDGE_SAMPLE = (320, 180)  # sampled frames are scaled to this size (area average)
EDGE_STRIP = 2  # strip width in sampled pixels (~12 px of a 1080p frame)
_VOLUME_RE = re.compile(r"(mean|max)_volume:\s*(-?[\d.]+|-inf)\s*dB")


@dataclass
class RenderQA:
    """Measurements of a rendered MP4 (see the module docstring)."""

    expected_frames: int
    fps: int
    frames: int | None = None
    duration: float | None = None
    has_video: bool = False
    has_audio: bool = False
    r_frame_rate: str = ""
    avg_frame_rate: str = ""
    declared_cfr: bool = False
    on_grid: bool | None = None
    off_grid: int = 0
    audio_mean_db: float | None = None
    audio_max_db: float | None = None
    audible: bool | None = None
    black_edges: list[str] = field(default_factory=list)
    color: dict[str, str] = field(default_factory=dict)
    subtitle_tracks: int = 0
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """No fatal problem."""
        return not self.problems

    def summary(self) -> dict[str, Any]:
        """JSON summary stored on the render."""
        return {
            "ok": self.ok,
            "frames": self.frames,
            "expected_frames": self.expected_frames,
            "fps": self.fps,
            "duration": None if self.duration is None else round(self.duration, 3),
            "constant_frame_rate": self.declared_cfr and self.on_grid is not False,
            "on_grid": self.on_grid,
            "has_audio": self.has_audio,
            "audible": self.audible,
            "audio_mean_db": self.audio_mean_db,
            "audio_max_db": self.audio_max_db,
            "black_edges": list(self.black_edges),
            "color": dict(self.color),
            "subtitle_tracks": self.subtitle_tracks,
            "problems": list(self.problems),
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested; shared with evals/video_qa.py)
# ---------------------------------------------------------------------------


def parse_rate(value: str | None) -> float | None:
    """``"30/1"`` -> 30.0 (None for missing / ``0/0``)."""
    if not value or value in ("0/0", "N/A"):
        return None
    num, _, den = str(value).partition("/")
    try:
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None


def parse_volumedetect(text: str) -> tuple[float | None, float | None]:
    """``(mean_volume, max_volume)`` in dB from ``volumedetect`` output (``-inf`` -> -inf)."""
    found: dict[str, float] = {}
    for kind, value in _VOLUME_RE.findall(text or ""):
        found[kind] = -math.inf if value == "-inf" else float(value)
    return found.get("mean"), found.get("max")


def grid_stats(pts: Sequence[int], step: int) -> tuple[bool, int, int]:
    """``(on_grid, off_grid_count, irregular_intervals)`` of packet pts on a ``step`` grid.

    Times are taken relative to the smallest pts; ``on_grid`` = every pts is a multiple of
    ``step`` and consecutive (sorted) pts are exactly one step apart.
    """
    if not pts or step <= 0:
        return True, 0, 0
    ordered = sorted(pts)
    base = ordered[0]
    off = sum(1 for p in ordered if (p - base) % step)
    irregular = sum(1 for a, b in zip(ordered, ordered[1:], strict=False) if b - a != step)
    return off == 0 and irregular == 0, off, irregular


def black_strips(gray: Sequence[int] | bytes, width: int, height: int, *, strip: int = EDGE_STRIP,
                 threshold: float = BLACK_LUMA) -> list[str]:
    """Edges (``left``/``right``/``top``/``bottom``) whose ``strip``-pixel band is black in a gray frame."""
    data = bytes(gray)
    if len(data) < width * height or width <= 2 * strip or height <= 2 * strip:
        return []

    def mean(x0: int, x1: int, y0: int, y1: int) -> float:
        total = 0
        for y in range(y0, y1):
            row = data[y * width + x0: y * width + x1]
            total += sum(row)
        return total / max(1, (x1 - x0) * (y1 - y0))

    bands = {
        "left": (0, strip, 0, height),
        "right": (width - strip, width, 0, height),
        "top": (0, width, 0, strip),
        "bottom": (0, width, height - strip, height),
    }
    return [name for name, box in bands.items() if mean(*box) <= threshold]


def expected_frame_count(total_seconds: float, fps: int) -> int:
    """Frames of a constant-rate video of ``total_seconds`` (rounded)."""
    return max(0, math.floor(total_seconds * fps + 0.5))


# ---------------------------------------------------------------------------
# Measuring a file
# ---------------------------------------------------------------------------


async def _ffprobe_json(path: Path, entries: str, settings: Settings, *, extra: Sequence[str] = (),
                        timeout: float = 300) -> dict[str, Any]:
    cmd = [settings.ffprobe_path, "-hide_banner", "-v", "error", "-protocol_whitelist", PROTOCOLS, *extra,
           "-print_format", "json", "-show_entries", entries, "-i", str(path)]
    out, _ = await run_process(cmd, timeout=timeout, settings=settings, capture_stdout=True)
    try:
        return json.loads(out.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON") from exc


async def packet_pts(path: Path, settings: Settings, *, timeout: float = 600) -> list[int]:
    """Presentation timestamps (stream time base) of every packet of the first video stream."""
    cmd = [settings.ffprobe_path, "-hide_banner", "-v", "error", "-protocol_whitelist", PROTOCOLS,
           "-select_streams", "v:0", "-show_entries", "packet=pts", "-of", "csv=p=0", "-i", str(path)]
    out, _ = await run_process(cmd, timeout=timeout, settings=settings, capture_stdout=True)
    pts: list[int] = []
    for line in out.decode("ascii", "replace").split():
        token = line.strip().strip(",")
        if token.lstrip("-").isdigit():
            pts.append(int(token))
    return pts


async def audio_levels(path: Path, settings: Settings, *, timeout: float = 600,
                       check_cancelled: Callable[[], None] | None = None) -> tuple[float | None, float | None]:
    """``(mean_volume, max_volume)`` of the first audio stream (``volumedetect``; decodes audio only)."""
    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info",
           "-protocol_whitelist", PROTOCOLS, "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
           "-af", "volumedetect", "-f", "null", "-"]
    _, tail = await run_process(cmd, timeout=timeout, settings=settings, check_cancelled=check_cancelled)
    return parse_volumedetect(tail)


async def gray_frame(path: Path, t: float, settings: Settings, *, size: tuple[int, int] = EDGE_SAMPLE,
                     timeout: float = 60) -> bytes:
    """The frame at ``t`` seconds as ``size`` full-range 8-bit gray (area-averaged)."""
    w, h = size
    cmd = [settings.ffmpeg_path, "-hide_banner", "-nostdin", "-loglevel", "error", "-protocol_whitelist", PROTOCOLS,
           "-ss", f"{max(0.0, t):.3f}", "-i", str(path), "-map", "0:v:0", "-frames:v", "1",
           "-vf", f"scale={w}:{h}:flags=area:out_range=pc,format=gray", "-f", "rawvideo", "-"]
    out, _ = await run_process(cmd, timeout=timeout, settings=settings, capture_stdout=True,
                               stdout_limit=w * h * 2)
    return out[: w * h]


async def inspect_render(path: str | Path, *, settings: Settings, fps: int, expected_frames: int,
                         expect_audio: bool, expect_bt709: bool = False, expect_subtitles: bool = False,
                         check_cancelled: Callable[[], None] | None = None) -> RenderQA:
    """Measure ``path`` (see the module docstring). Raises :class:`FFmpegError` only when the file
    cannot be probed at all; later measurement failures become warnings."""
    path = Path(path)
    qa = RenderQA(expected_frames=expected_frames, fps=fps)
    data = await _ffprobe_json(path, "stream=index,codec_type,codec_name,r_frame_rate,avg_frame_rate,time_base,"
                                     "color_space,color_primaries,color_transfer,color_range:format=duration",
                               settings)
    try:
        qa.duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        qa.duration = None
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    qa.has_audio = any(s.get("codec_type") == "audio" for s in streams)
    qa.subtitle_tracks = sum(1 for s in streams if s.get("codec_type") == "subtitle")
    if video is None:
        qa.problems.append("the video has no picture stream")
        return qa
    qa.has_video = True
    qa.r_frame_rate, qa.avg_frame_rate = str(video.get("r_frame_rate") or ""), str(video.get("avg_frame_rate") or "")
    r, avg = parse_rate(qa.r_frame_rate), parse_rate(qa.avg_frame_rate)
    qa.declared_cfr = r is not None and avg is not None and abs(r - fps) < 1e-6 and abs(avg - fps) < 1e-3
    qa.color = {k: str(video.get(k) or "unknown") for k in ("color_space", "color_primaries", "color_transfer",
                                                              "color_range")}
    if check_cancelled:
        check_cancelled()
    pts = await packet_pts(path, settings)
    qa.frames = len(pts)
    if abs(qa.frames - expected_frames) > 1:
        qa.problems.append(f"the video has {qa.frames} frames, the lecture needs {expected_frames}")
    step = _grid_step(str(video.get("time_base") or ""), fps)
    if step is not None:
        qa.on_grid, qa.off_grid, irregular = grid_stats(pts, step)
        if not qa.on_grid:
            qa.warnings.append(f"{qa.off_grid + irregular} frame time(s) are off the 1/{fps} s grid")
    if not qa.declared_cfr:
        qa.warnings.append(f"the declared frame rate is {qa.avg_frame_rate or '?'} (expected {fps}/1)")
    if expect_bt709 and (qa.color.get("color_space") != "bt709" or qa.color.get("color_range") != "tv"):
        qa.warnings.append("the video is not tagged BT.709")
    if expect_subtitles and qa.subtitle_tracks == 0:
        qa.warnings.append("the selectable caption track is missing")

    if not qa.has_audio:
        if expect_audio:
            qa.problems.append("the video has no sound track")
    else:
        if check_cancelled:
            check_cancelled()
        try:
            qa.audio_mean_db, qa.audio_max_db = await audio_levels(path, settings, check_cancelled=check_cancelled)
        except FFmpegError as exc:
            qa.warnings.append(f"the sound level could not be measured ({exc})")
        if qa.audio_max_db is not None:
            qa.audible = qa.audio_max_db > SILENCE_DB
            if not math.isfinite(qa.audio_max_db):
                qa.audio_max_db = None
            if qa.audio_mean_db is not None and not math.isfinite(qa.audio_mean_db):
                qa.audio_mean_db = None
            if expect_audio and not qa.audible:
                qa.problems.append("the narration is silent in the video")

    total = qa.duration or expected_frames / max(1, fps)
    found: list[set[str]] = []
    for frac in (0.25, 0.5, 0.75):
        if check_cancelled:
            check_cancelled()
        try:
            gray = await gray_frame(path, total * frac, settings)
        except FFmpegError as exc:
            qa.warnings.append(f"a frame could not be sampled ({exc})")
            found = []
            break
        found.append(set(black_strips(gray, *EDGE_SAMPLE)))
    if found:
        qa.black_edges = sorted(set.intersection(*found))
        if qa.black_edges:
            qa.warnings.append(f"black bars at the {', '.join(qa.black_edges)} edge(s)")
    return qa


def _grid_step(time_base: str, fps: int) -> int | None:
    """Packet-time units per frame (``time_base`` = ``"1/15360"``), or None if not an integer."""
    num, _, den = time_base.partition("/")
    try:
        n, d = int(num), int(den)
    except ValueError:
        return None
    if n <= 0 or d <= 0 or fps <= 0:
        return None
    step = d / (n * fps)
    return int(step) if abs(step - round(step)) < 1e-9 and step >= 1 else None
