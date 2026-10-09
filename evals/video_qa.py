"""Frame-accurate checks of exported lecture videos (slow tests, ``evals/render_parity.py``).

ffmpeg / ffprobe only (``FFMPEG`` / ``FFPROBE`` env vars, else PATH); nothing is played and no
network is used. Every input is opened with ``-protocol_whitelist file,pipe`` (as the renderer
does), commands run without a shell. Pure statistics are shared with ``aadhi.compose.qa``.

    probe(path)                       streams, codecs, frame rates, exact frame count, colour, chapters, captions
    frame_times(path)                 every video frame's time (s), sorted (packet times: no decoding)
    check_cfr(path, fps)              constant frame rate: declared rates, n/fps grid, frame count vs duration
    frame_rgb(path, index, fps)       one decoded frame (RGB, uint8) by its index
    grid(rgb, w, h) / mean_diff(...)  area-averaged w x h grid and the mean absolute RGB difference (of 255)
    black_edges(path, times)          letter/pillarbox strips found on sampled frames
    frozen_run(frames, region)        the longest stretch of identical consecutive frames
    audio_pcm(path) / audio_onset()   mono PCM and the first sound after a moment
    loudness(path)                    volumedetect mean/max (dB)
    parse_vtt / parse_srt             caption cues as (start, end, text)

Import it by path (``evals`` is not a package), e.g. ``importlib.util.spec_from_file_location``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aadhi.compose.qa import black_strips, grid_stats, parse_rate, parse_volumedetect  # noqa: E402

PROTOCOLS = "file,pipe"
_TIME_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{3})")


def ffmpeg_bin() -> str:
    return os.environ.get("FFMPEG", "ffmpeg")


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE", "ffprobe")


def _run(cmd: Sequence[str], *, timeout: float = 600) -> subprocess.CompletedProcess[bytes]:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.run(list(cmd), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, check=False,
                          **kwargs)
    if proc.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {proc.stderr.decode('utf-8', 'replace')[-600:]}")
    return proc


def _ffprobe_json(path: Path, *args: str) -> dict[str, Any]:
    out = _run([ffprobe_bin(), "-v", "error", "-protocol_whitelist", PROTOCOLS, *args, "-of", "json", "-i", str(path)])
    return json.loads(out.stdout.decode("utf-8", "replace") or "{}")


# ---- what the file is --------------------------------------------------------------------------


def probe(path: str | Path) -> dict[str, Any]:
    """Streams, codecs, rates, exact video frame count (packets), duration, colour, chapters, captions."""
    path = Path(path)
    data = _ffprobe_json(path, "-count_packets", "-show_chapters", "-show_entries",
                         "format=duration:stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,"
                         "avg_frame_rate,nb_read_packets,time_base,color_space,color_primaries,color_transfer,"
                         "color_range:stream_tags=language:stream_disposition=default")
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    return {
        "duration": float((data.get("format") or {}).get("duration") or 0.0),
        "video": video,
        "audio": audio,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "codec": video.get("codec_name") if video else None,
        "frames": int(video.get("nb_read_packets") or 0) if video else 0,
        "r_frame_rate": video.get("r_frame_rate") if video else None,
        "avg_frame_rate": video.get("avg_frame_rate") if video else None,
        "color": {k: (video or {}).get(k, "unknown") for k in ("color_space", "color_primaries", "color_transfer",
                                                               "color_range")},
        "subtitles": [{"codec": s.get("codec_name"), "language": (s.get("tags") or {}).get("language"),
                       "default": (s.get("disposition") or {}).get("default")} for s in subs],
        "chapters": [{"start": float(c.get("start_time", 0)), "end": float(c.get("end_time", 0)),
                      "title": (c.get("tags") or {}).get("title", "")} for c in data.get("chapters") or []],
    }


# ---- frame timing ------------------------------------------------------------------------------


def packet_pts(path: str | Path) -> tuple[list[int], str]:
    """Video packet pts (stream time base units) and the time base (``"1/15360"``)."""
    path = Path(path)
    meta = _ffprobe_json(path, "-select_streams", "v:0", "-show_entries", "stream=time_base")
    tb = str(((meta.get("streams") or [{}])[0]).get("time_base") or "1/1")
    out = _run([ffprobe_bin(), "-v", "error", "-protocol_whitelist", PROTOCOLS, "-select_streams", "v:0",
                "-show_entries", "packet=pts", "-of", "csv=p=0", "-i", str(path)])
    pts = [int(x.strip(",")) for x in out.stdout.decode("ascii", "replace").split() if x.strip(",").lstrip("-").isdigit()]
    return pts, tb


def frame_times(path: str | Path) -> list[float]:
    """Every video frame's presentation time in seconds, sorted."""
    pts, tb = packet_pts(path)
    num, _, den = tb.partition("/")
    scale = int(num) / int(den)
    return sorted(p * scale for p in pts)


def check_cfr(path: str | Path, fps: int) -> dict[str, Any]:
    """Constant-rate contract: declared rates == fps, every frame on the n/fps grid, frames == round(duration*fps)."""
    info = probe(path)
    pts, tb = packet_pts(path)
    num, _, den = tb.partition("/")
    step_f = int(den) / (int(num) * fps)
    step = round(step_f) if abs(step_f - round(step_f)) < 1e-9 else 0
    on_grid, off, irregular = grid_stats(pts, step) if step else (False, len(pts), 0)
    declared = parse_rate(info["r_frame_rate"]) == fps and parse_rate(info["avg_frame_rate"]) == fps
    expected = round(info["duration"] * fps)
    return {"ok": on_grid and declared and abs(len(pts) - expected) <= 1, "frames": len(pts),
            "expected_frames": expected, "declared": declared, "on_grid": on_grid, "off_grid": off,
            "irregular": irregular, "step": step}


# ---- frames ------------------------------------------------------------------------------------


def frame_rgb(path: str | Path, index: int, fps: int, *, size: tuple[int, int] | None = None) -> np.ndarray:
    """Decoded frame ``index`` as an (h, w, 3) uint8 array (accurate seek; the stream's colour tags apply)."""
    path = Path(path)
    info = probe(path)
    w, h = size or (int(info["width"]), int(info["height"]))
    t = max(0.0, (index - 0.5) / fps)
    vf = f"scale={w}:{h}:flags=area" if size else "null"
    out = _run([ffmpeg_bin(), "-v", "error", "-nostdin", "-protocol_whitelist", PROTOCOLS, "-ss", f"{t:.6f}",
                "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-"])
    return np.frombuffer(out.stdout[: w * h * 3], dtype=np.uint8).reshape(h, w, 3)


def image_rgb(path: str | Path) -> np.ndarray:
    """An image file (PNG screenshot) as an (h, w, 3) uint8 array (alpha dropped)."""
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB")).copy()


def grid(rgb: np.ndarray, w: int = 32, h: int = 18) -> np.ndarray:
    """Area-averaged ``w x h`` RGB grid (float)."""
    from PIL import Image

    im = Image.fromarray(np.ascontiguousarray(rgb, dtype=np.uint8)).resize((w, h), Image.Resampling.BOX)
    return np.asarray(im, dtype=float)


def mean_diff(a: np.ndarray, b: np.ndarray, *, mask: Sequence[tuple[float, float, float, float]] = (),
              frame: tuple[int, int] = (1920, 1080)) -> float:
    """Mean absolute RGB difference (of 255) of two equal-size grids; ``mask`` = (x, y, w, h) areas in
    ``frame`` pixels whose cells (by centre) are left out."""
    gh, gw = a.shape[:2]
    keep = np.ones((gh, gw), dtype=bool)
    for y in range(gh):
        for x in range(gw):
            cx, cy = (x + 0.5) * frame[0] / gw, (y + 0.5) * frame[1] / gh
            if any(mx <= cx < mx + mw and my <= cy < my + mh for mx, my, mw, mh in mask):
                keep[y, x] = False
    if not keep.any():
        return 0.0
    return float(np.abs(a - b)[keep].mean())


def black_edges(path: str | Path, times: Sequence[float]) -> list[str]:
    """Edges that are black on every sampled frame (``aadhi.compose.qa.black_strips`` on 320x180 gray)."""
    found: list[set[str]] = []
    for t in times:
        out = _run([ffmpeg_bin(), "-v", "error", "-nostdin", "-protocol_whitelist", PROTOCOLS, "-ss", f"{t:.3f}",
                    "-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-vf",
                    "scale=320:180:flags=area:out_range=pc,format=gray", "-f", "rawvideo", "-"])
        found.append(set(black_strips(out.stdout[: 320 * 180], 320, 180)))
    return sorted(set.intersection(*found)) if found else []


def frozen_run(frames: Sequence[np.ndarray], *, tolerance: float = 0.5) -> int:
    """Length of the longest run of consecutive frames whose mean difference is <= ``tolerance``."""
    best = run = 1 if frames else 0
    for a, b in zip(frames, frames[1:], strict=False):
        run = run + 1 if float(np.abs(a.astype(float) - b.astype(float)).mean()) <= tolerance else 1
        best = max(best, run)
    return best


# ---- sound -------------------------------------------------------------------------------------


def audio_pcm(path: str | Path, rate: int = 8000) -> np.ndarray:
    """The first audio stream as mono int16 samples at ``rate``."""
    out = _run([ffmpeg_bin(), "-v", "error", "-nostdin", "-protocol_whitelist", PROTOCOLS, "-i", str(path),
                "-map", "0:a:0", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"])
    return np.frombuffer(out.stdout, dtype="<i2").astype(float)


def audio_onset(pcm: np.ndarray, rate: int, start: float, *, threshold: float = 1500.0, window: float = 0.01,
                limit: float = 5.0) -> float | None:
    """Time (s) of the first ``window`` after ``start`` whose RMS exceeds ``threshold`` (None within ``limit``)."""
    n = max(1, int(window * rate))
    i = int(start * rate)
    end = min(len(pcm), int((start + limit) * rate))
    while i + n <= end:
        if float(np.sqrt(np.mean(pcm[i:i + n] ** 2))) > threshold:
            return i / rate
        i += n
    return None


def loudness(path: str | Path) -> tuple[float | None, float | None]:
    """``(mean_volume, max_volume)`` dB of the first audio stream."""
    out = _run([ffmpeg_bin(), "-hide_banner", "-nostdin", "-nostats", "-protocol_whitelist", PROTOCOLS,
                "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"])
    return parse_volumedetect(out.stderr.decode("utf-8", "replace"))


# ---- text outputs ------------------------------------------------------------------------------


def _seconds(stamp: str) -> float:
    m = _TIME_RE.fullmatch(stamp.strip())
    if not m:
        raise ValueError(f"bad timestamp {stamp!r}")
    h, mi, s, ms = (int(g) if g else 0 for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def _cues(text: str) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for k, line in enumerate(lines):
            if "-->" in line:
                a, _, b = line.partition("-->")
                cues.append((_seconds(a), _seconds(b.split()[0]), "\n".join(lines[k + 1:])))
                break
    return cues


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """WebVTT cues (the header and NOTE blocks are skipped)."""
    return _cues(text)


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    """SubRip cues."""
    return _cues(text)
