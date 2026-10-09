"""ffmpeg/ffprobe helpers for Manim output (probe, exact-duration conform, frame grabs).

Every call uses an argv list (no shell), ``-protocol_whitelist`` and an explicit input format, and
runs in a worker thread so the event loop never blocks.
"""

from __future__ import annotations

import asyncio
import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings

PROBE_TIMEOUT = 60
CONFORM_TIMEOUT = 300
FRAME_TIMEOUT = 60
MP4_DEMUXER = "mp4"


class MediaError(RuntimeError):
    """ffmpeg/ffprobe failed."""


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)  # noqa: S603 - argv list
    except FileNotFoundError as exc:
        raise MediaError(f"{Path(cmd[0]).name} is not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).name} timed out") from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()[-5:]
        raise MediaError(f"{Path(cmd[0]).name} failed: {' | '.join(tail)}")
    return proc


def _parse_rate(rate: str) -> float:
    try:
        num, _, den = rate.partition("/")
        value = float(num) / float(den or 1)
        return value if math.isfinite(value) and value > 0 else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe_sync(path: Path, settings: Settings) -> VideoInfo:
    """Duration, size and frame rate of the first video stream."""
    cmd = [
        settings.ffprobe_path, "-v", "error", "-protocol_whitelist", "file", "-f", MP4_DEMUXER,
        "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate,duration:format=duration",
        "-of", "json", str(path),
    ]
    data = json.loads(_run(cmd, PROBE_TIMEOUT).stdout or b"{}")
    streams = data.get("streams") or []
    if not streams:
        raise MediaError("no video stream found")
    stream = streams[0]
    duration = float(stream.get("duration") or (data.get("format") or {}).get("duration") or 0.0)
    return VideoInfo(
        duration=duration,
        width=int(stream.get("width") or 0),
        height=int(stream.get("height") or 0),
        fps=_parse_rate(str(stream.get("r_frame_rate") or "0/1")),
    )


async def probe(path: Path, settings: Settings) -> VideoInfo:
    """Async :func:`probe_sync`."""
    return await asyncio.to_thread(probe_sync, path, settings)


def conform_sync(src: Path, dst: Path, total_duration: float, settings: Settings) -> VideoInfo:
    """Re-encode ``src`` to exactly ``total_duration`` seconds (freeze the last frame or trim)."""
    info = probe_sync(src, settings)
    fps = info.fps or 30.0
    frames = max(1, round(float(total_duration) * fps))
    pad = max(0.0, float(total_duration) - info.duration) + 2.0 / fps
    cmd = [
        settings.ffmpeg_path, "-v", "error", "-y", "-protocol_whitelist", "file", "-f", MP4_DEMUXER,
        "-i", str(src), "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.4f},format=yuv420p",
        "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-r", f"{fps:g}", "-movflags", "+faststart", "-f", "mp4", str(dst),
    ]
    _run(cmd, CONFORM_TIMEOUT)
    return probe_sync(dst, settings)


async def conform(src: Path, dst: Path, total_duration: float, settings: Settings) -> VideoInfo:
    """Async :func:`conform_sync`."""
    return await asyncio.to_thread(conform_sync, src, dst, total_duration, settings)


def _frame_cmd(src: Path, t: float, settings: Settings, max_width: int) -> list[str]:
    return [
        settings.ffmpeg_path, "-v", "error", "-protocol_whitelist", "file,pipe", "-ss", f"{max(t, 0.0):.3f}",
        "-f", MP4_DEMUXER, "-i", str(src), "-frames:v", "1", "-vf", f"scale='min({max_width},iw)':-2",
        "-f", "image2pipe", "-c:v", "png", "pipe:1",
    ]


def extract_frame_sync(src: Path, t: float, settings: Settings, max_width: int = 960) -> bytes:
    """One PNG frame at ``t`` seconds (scaled down to ``max_width``).

    A time past the last frame's timestamp yields no image from ffmpeg; it falls back to the last frame.
    """
    data = _run(_frame_cmd(src, t, settings, max_width), FRAME_TIMEOUT).stdout
    if not data.startswith(b"\x89PNG") and t > 0:
        info = probe_sync(src, settings)
        last = max(info.duration - 1.5 / (info.fps or 30.0), 0.0)
        if last < t:
            data = _run(_frame_cmd(src, last, settings, max_width), FRAME_TIMEOUT).stdout
    if not data.startswith(b"\x89PNG"):
        raise MediaError("frame extraction produced no image")
    return data


async def extract_frames(src: Path, times: list[float], settings: Settings, max_width: int = 960) -> list[bytes]:
    """PNG frames at ``times`` (sequential to bound CPU use)."""
    out: list[bytes] = []
    for t in times:
        out.append(await asyncio.to_thread(extract_frame_sync, src, t, settings, max_width))
    return out
