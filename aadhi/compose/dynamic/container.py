"""Post-render container checks of a motion (Dynamic) render, with their automatic remedies (DYNAMIC_VIDEO_DESIGN
§7.12), and the ``Render.options["qa"]`` document of a motion render.

Only these checks can be errors, and each has a remedy; the job fails only when the remedy does not fix it, which
means an engine bug (:class:`ContainerCheckFailed`, a ``FatalJobError`` with code ``render_invalid``):

=========================================================== =====================================================
Check                                                       Remedy
=========================================================== =====================================================
Every chunk has exactly its planned frames (packets)        ``rerender_chunks`` (only the chunks that differ)
The MP4 has exactly the timeline's frames                   ``remux`` (the chunks are right, the join was not)
First shown video frame at 0, also without the edit list    ``remux``
(``-ignore_editlist 1``, what players that ignore ``elst``
show), and the audio starting less than one frame from it
Colour tags BT.709 / TV range, the 1/fps frame grid, the    ``remux``
declared rate ``fps/1``, the caption track when expected
A sound track when expected, audible; with a loudness       ``reencode_audio`` (re-encode the audio and re-mux)
target: integrated loudness within 1 LU, true peak at or
below -1.0 dBTP (R-48)
=========================================================== =====================================================

Warnings keep the MP4: black edges on sampled frames (as the classic QA). Pixel contrast, static stretches and the
3-frame determinism spot check of §7.12 come with later batches.

:func:`verify_container` runs the checks, applies each needed remedy once (chunks first, then the audio, then the
re-mux; the engine supplies them as :class:`ContainerRemedies`), checks again after each, and raises when anything
is still wrong. :func:`motion_qa` builds the stored document (the classic keys the Studio reads, ``ok`` first, plus
``engine``, ``schema``, ``preflight``, ``chunks``, ``starts`` and ``remedies``); :func:`record_qa` writes it.
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select, update

from ...config import Settings
from ...jobs.base import FatalJobError
from ...models import Render
from ..ffmpeg import PROTOCOLS, FFmpegError, run_process
from ..loudness import LoudnessError, LoudnessTarget, ebur128
from ..qa import (
    SILENCE_DB,
    audio_levels,
    black_strips,
    edge_sample_size,
    gray_frame,
    grid_stats,
    packet_pts,
    parse_rate,
)

log = logging.getLogger(__name__)

QA_SCHEMA = 1
REMEDY_ORDER = ("rerender_chunks", "reencode_audio", "remux")
START_TOLERANCE = 1e-3  # s: "first video PTS = 0"
LOUDNESS_TOLERANCE_LU = 1.0  # R-48: integrated loudness +-1
TRUE_PEAK_CEILING_DBTP = -1.0  # R-48: true peak on the encoded file
BT709_TAGS = {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709", "color_range": "tv"}


@dataclass(frozen=True)
class ChunkFile:
    """One encoded chunk (MPEG-TS, §7.5) and the frames it must hold."""

    index: int
    path: Path
    frames: int


@dataclass(frozen=True)
class ContainerProblem:
    """A failed container check and the remedy that fixes it."""

    check: str
    message: str
    remedy: str  # rerender_chunks | reencode_audio | remux
    chunks: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"check": self.check, "message": self.message, "remedy": self.remedy}
        if self.chunks:
            out["chunks"] = list(self.chunks)
        return out


@dataclass
class ContainerQA:
    """Measurements of a motion render's MP4 (see the module docstring)."""

    expected_frames: int
    fps: int
    frames: int | None = None
    duration: float | None = None
    has_video: bool = False
    has_audio: bool = False
    width: int | None = None
    height: int | None = None
    video_start: float | None = None  # first shown frame (edit list applied)
    raw_video_start: float | None = None  # the same with -ignore_editlist 1
    audio_start: float | None = None
    r_frame_rate: str = ""
    avg_frame_rate: str = ""
    declared_cfr: bool = False
    on_grid: bool | None = None
    off_grid: int = 0
    color: dict[str, str] = field(default_factory=dict)
    subtitle_tracks: int = 0
    subtitle_default: bool | None = None
    audio_mean_db: float | None = None
    audio_max_db: float | None = None
    audible: bool | None = None
    loudness: dict[str, float | None] | None = None
    black_edges: list[str] = field(default_factory=list)
    chunks: list[dict[str, int]] = field(default_factory=list)  # [{index, expected, frames}]
    problems: list[ContainerProblem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    remedies: list[dict[str, Any]] = field(default_factory=list)  # applied: [{remedy, chunks?, problems}]

    @property
    def ok(self) -> bool:
        """No failed check."""
        return not self.problems

    @property
    def av_offset(self) -> float | None:
        if self.video_start is None or self.audio_start is None:
            return None
        return self.audio_start - self.video_start

    def remedies_needed(self) -> dict[str, list[ContainerProblem]]:
        out: dict[str, list[ContainerProblem]] = {}
        for p in self.problems:
            out.setdefault(p.remedy, []).append(p)
        return out

    def summary(self) -> dict[str, Any]:
        """The container part of ``Render.options["qa"]`` (JSON-serialisable)."""
        offset = self.av_offset
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
            "loudness": self.loudness,
            "black_edges": list(self.black_edges),
            "color": dict(self.color),
            "subtitle_tracks": self.subtitle_tracks,
            "starts": {"video": _round(self.video_start), "video_raw": _round(self.raw_video_start),
                       "audio": _round(self.audio_start), "av_offset": _round(offset)},
            "chunks": list(self.chunks),
            "problems": [p.message for p in self.problems],
            "warnings": list(self.warnings),
            "remedies": list(self.remedies),
        }


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 4)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


async def _probe(path: Path, settings: Settings, *args: str, timeout: float = 300) -> dict[str, Any]:
    cmd = [settings.ffprobe_path, "-hide_banner", "-v", "error", "-protocol_whitelist", PROTOCOLS, *args,
           "-print_format", "json", "-i", str(path)]
    out, _ = await run_process(cmd, timeout=timeout, settings=settings, capture_stdout=True)
    try:
        return json.loads(out.decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError as exc:
        raise FFmpegError("ffprobe returned invalid JSON") from exc


def _float(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def grid_step(time_base: str, fps: int) -> int | None:
    """Packet-time units per frame for ``time_base`` (``"1/15360"``) at ``fps``; None when not an integer."""
    num, _, den = str(time_base).partition("/")
    try:
        n, d = int(num), int(den)
    except ValueError:
        return None
    if n <= 0 or d <= 0 or fps <= 0:
        return None
    step = d / (n * fps)
    return int(round(step)) if abs(step - round(step)) < 1e-9 and step >= 1 else None


async def first_frame_time(path: Path, settings: Settings, stream: str = "v:0", *,
                           ignore_editlist: bool = False) -> float | None:
    """Presentation time (s) of the first frame of ``stream`` as a player shows it (``ignore_editlist``: as a
    player that ignores the MP4 edit list shows it)."""
    extra = ["-ignore_editlist", "1"] if ignore_editlist else []
    data = await _probe(path, settings, *extra, "-select_streams", stream, "-show_entries",
                        "frame=pts_time,best_effort_timestamp_time", "-read_intervals", "%+#5")
    times = []
    for frame in data.get("frames") or []:
        t = _float(frame.get("pts_time"))
        times.append(t if t is not None else _float(frame.get("best_effort_timestamp_time")))
    known = [t for t in times if t is not None]
    return min(known) if known else None  # the decoder returns frames in display order; reordering aside


async def chunk_frame_counts(chunks: Sequence[ChunkFile], settings: Settings) -> list[dict[str, int]]:
    """``[{index, expected, frames}]`` of every chunk (video packets; -1 when the chunk cannot be read)."""
    out = []
    for c in chunks:
        try:
            frames = len(await packet_pts(c.path, settings))
        except FFmpegError:
            frames = -1
        out.append({"index": c.index, "expected": c.frames, "frames": frames})
    return out


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------


async def inspect_container(
    path: str | Path,
    *,
    settings: Settings,
    fps: int,
    expected_frames: int,
    expect_audio: bool,
    expect_subtitles: bool = False,
    chunks: Sequence[ChunkFile] = (),
    loudness: LoudnessTarget | None = None,
    size: tuple[int, int] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> ContainerQA:
    """Measure the MP4 at ``path`` (and the chunks it was joined from). Raises :class:`FFmpegError` only when the
    file cannot be probed at all."""
    path = Path(path)
    qa = ContainerQA(expected_frames=expected_frames, fps=fps)

    def tick() -> None:
        if check_cancelled:
            check_cancelled()

    if chunks:
        qa.chunks = await chunk_frame_counts(chunks, settings)
        wrong = tuple(c["index"] for c in qa.chunks if c["frames"] != c["expected"])
        if wrong:
            detail = ", ".join(f"chunk {c['index']}: {c['frames']} of {c['expected']}" for c in qa.chunks
                               if c["index"] in wrong)
            qa.problems.append(ContainerProblem("chunk_frames", f"chunks with the wrong frame count ({detail})",
                                                "rerender_chunks", wrong))
    tick()
    data = await _probe(path, settings, "-show_entries",
                        "stream=index,codec_type,codec_name,r_frame_rate,avg_frame_rate,time_base,start_time,width,"
                        "height,color_space,color_primaries,color_transfer,color_range:stream_disposition=default"
                        ":format=duration")
    qa.duration = _float((data.get("format") or {}).get("duration"))
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    qa.has_audio = audio is not None
    qa.subtitle_tracks = len(subs)
    qa.subtitle_default = bool((subs[0].get("disposition") or {}).get("default")) if subs else None
    if video is None:
        qa.problems.append(ContainerProblem("video", "the video has no picture stream", "remux"))
        return qa
    qa.has_video = True
    qa.width, qa.height = _int(video.get("width")), _int(video.get("height"))
    qa.r_frame_rate, qa.avg_frame_rate = str(video.get("r_frame_rate") or ""), str(video.get("avg_frame_rate") or "")
    r, avg = parse_rate(qa.r_frame_rate), parse_rate(qa.avg_frame_rate)
    qa.declared_cfr = r is not None and avg is not None and abs(r - fps) < 1e-6 and abs(avg - fps) < 1e-3
    qa.color = {k: str(video.get(k) or "unknown") for k in BT709_TAGS}

    tick()
    pts = await packet_pts(path, settings)
    qa.frames = len(pts)
    if qa.frames != expected_frames and not any(p.remedy == "rerender_chunks" for p in qa.problems):
        qa.problems.append(ContainerProblem(
            "frame_count", f"the video has {qa.frames} frames, the timeline needs exactly {expected_frames}", "remux"))
    step = grid_step(str(video.get("time_base") or ""), fps)
    if step is not None:
        qa.on_grid, qa.off_grid, irregular = grid_stats(pts, step)
        if not qa.on_grid:
            qa.problems.append(ContainerProblem(
                "frame_grid", f"{qa.off_grid + irregular} frame time(s) are off the 1/{fps} s grid", "remux"))
    if not qa.declared_cfr:
        qa.problems.append(ContainerProblem(
            "frame_rate", f"the declared frame rate is {qa.avg_frame_rate or '?'} (expected {fps}/1)", "remux"))
    wrong_tags = sorted(k for k, v in BT709_TAGS.items() if qa.color.get(k) != v)
    if wrong_tags:
        qa.problems.append(ContainerProblem(
            "colour_tags", "the video is not tagged BT.709 / TV range (" + ", ".join(
                f"{k}={qa.color.get(k)}" for k in wrong_tags) + ")", "remux"))
    if expect_subtitles and (not subs or not qa.subtitle_default):
        qa.problems.append(ContainerProblem(
            "caption_track", "the selectable caption track is missing or not on by default", "remux"))

    tick()
    qa.video_start = await first_frame_time(path, settings, "v:0")
    qa.raw_video_start = await first_frame_time(path, settings, "v:0", ignore_editlist=True)
    if qa.video_start is None or abs(qa.video_start) > START_TOLERANCE:
        qa.problems.append(ContainerProblem(
            "video_start", f"the first video frame is shown at {_fmt_s(qa.video_start)}, not at 0", "remux"))
    elif qa.raw_video_start is None or abs(qa.raw_video_start) > START_TOLERANCE:
        qa.problems.append(ContainerProblem(
            "video_start", f"without the edit list the first video frame is at {_fmt_s(qa.raw_video_start)}, "
                           "not at 0", "remux"))
    if audio is not None:
        qa.audio_start = _float(audio.get("start_time"))
        if qa.audio_start is None:
            qa.audio_start = await first_frame_time(path, settings, "a:0")
        offset = qa.av_offset
        if offset is not None and abs(offset) >= 1.0 / fps:
            qa.problems.append(ContainerProblem(
                "av_start", f"the sound starts {offset * 1000:+.0f} ms from the picture (limit: one frame)", "remux"))

    tick()
    if audio is None:
        if expect_audio:
            qa.problems.append(ContainerProblem("audio", "the video has no sound track", "reencode_audio"))
    elif loudness is not None:
        try:
            r128 = await ebur128(path, settings, check_cancelled=check_cancelled)
        except LoudnessError as exc:
            qa.warnings.append(f"the loudness could not be measured ({exc})")
        else:
            qa.loudness = {"integrated_lufs": r128.as_dict()["integrated_lufs"],
                           "true_peak_dbtp": r128.as_dict()["true_peak_dbtp"],
                           "target_lufs": loudness.integrated}
            qa.audible = not r128.silent
            if expect_audio and r128.silent:
                qa.problems.append(ContainerProblem("audio", "the narration is silent in the video", "reencode_audio"))
            elif not r128.silent and abs(r128.integrated - loudness.integrated) > LOUDNESS_TOLERANCE_LU + 1e-9:
                qa.problems.append(ContainerProblem(
                    "loudness", f"the loudness is {r128.integrated:.1f} LUFS (target {loudness.integrated:.1f} "
                                f"+-{LOUDNESS_TOLERANCE_LU:.0f})", "reencode_audio"))
            if math.isfinite(r128.true_peak) and r128.true_peak > TRUE_PEAK_CEILING_DBTP + 1e-9:
                qa.problems.append(ContainerProblem(
                    "true_peak", f"the true peak is {r128.true_peak:.1f} dBTP (ceiling "
                                 f"{TRUE_PEAK_CEILING_DBTP:.1f})", "reencode_audio"))
    else:
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
                qa.problems.append(ContainerProblem("audio", "the narration is silent in the video", "reencode_audio"))

    total = qa.duration or expected_frames / max(1, fps)
    sample = edge_sample_size(*(size or (qa.width, qa.height)))
    found: list[set[str]] = []
    for frac in (0.25, 0.5, 0.75):
        tick()
        try:
            gray = await gray_frame(path, total * frac, settings, size=sample)
        except FFmpegError as exc:
            qa.warnings.append(f"a frame could not be sampled ({exc})")
            found = []
            break
        found.append(set(black_strips(gray, *sample)))
    if found:
        qa.black_edges = sorted(set.intersection(*found))
        if qa.black_edges:
            qa.warnings.append(f"black bars at the {', '.join(qa.black_edges)} edge(s)")
    return qa


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fmt_s(v: float | None) -> str:
    return "?" if v is None else f"{v:.4f} s"


# ---------------------------------------------------------------------------
# Remedies
# ---------------------------------------------------------------------------


class ContainerRemedies(Protocol):
    """What the motion engine does when a container check fails (each called at most once per render)."""

    async def rerender_chunks(self, indices: Sequence[int]) -> None:
        """Capture and encode these chunks again, join all chunks and mux the MP4 again."""

    async def reencode_audio(self) -> None:
        """Encode the master audio again (from the mix) and mux the MP4 again."""

    async def remux(self) -> None:
        """Join the chunks and mux the MP4 again (tags, timestamps, caption track)."""


class ContainerCheckFailed(FatalJobError):
    """A container check still fails after its remedy: an engine bug (the job fails with ``render_invalid``)."""

    def __init__(self, qa: ContainerQA) -> None:
        super().__init__("The rendered video failed its checks: " + "; ".join(p.message for p in qa.problems) + ".",
                         code="render_invalid")
        self.qa = qa


async def verify_container(
    inspect: Callable[[], Awaitable[ContainerQA]],
    remedies: ContainerRemedies,
    *,
    log_line: Callable[[str], None] | None = None,
) -> ContainerQA:
    """Check the MP4 (``inspect``), apply each needed remedy once in :data:`REMEDY_ORDER`, checking again after
    each; return the final measurements (``remedies`` lists what was applied) or raise
    :class:`ContainerCheckFailed` when a problem survives its remedy."""
    qa = await inspect()
    applied: list[dict[str, Any]] = []
    for kind in REMEDY_ORDER:
        problems = qa.remedies_needed().get(kind)
        if not problems:
            continue
        entry: dict[str, Any] = {"remedy": kind, "problems": [p.message for p in problems]}
        if kind == "rerender_chunks":
            indices = sorted({i for p in problems for i in p.chunks})
            entry["chunks"] = indices
            if log_line:
                log_line(f"Video check: re-rendering chunk(s) {', '.join(map(str, indices))}")
            await remedies.rerender_chunks(indices)
        elif kind == "reencode_audio":
            if log_line:
                log_line("Video check: encoding the sound again")
            await remedies.reencode_audio()
        else:
            if log_line:
                log_line("Video check: joining the video again")
            await remedies.remux()
        applied.append(entry)
        qa = await inspect()
    qa.remedies = applied
    if qa.problems:
        raise ContainerCheckFailed(qa)
    return qa


# ---------------------------------------------------------------------------
# The stored QA document
# ---------------------------------------------------------------------------


def motion_qa(container: ContainerQA | None, preflight: Any = None) -> dict[str, Any]:
    """``Render.options["qa"]`` of a motion render: the container summary (the classic keys, ``ok`` included, which
    the Studio's video list reads) plus ``engine``, ``schema`` and the preflight summary (``PreflightResult`` or its
    ``summary()`` dict). ``ok`` is False when either part failed."""
    pre = preflight.summary() if hasattr(preflight, "summary") else preflight if isinstance(preflight, dict) else None
    doc: dict[str, Any] = container.summary() if container is not None else {
        "ok": True, "problems": [], "warnings": []}
    doc = {"ok": bool(doc.get("ok")) and (pre is None or bool(pre.get("ok"))), "engine": "motion",
           "schema": QA_SCHEMA, **{k: v for k, v in doc.items() if k != "ok"}}
    if pre is not None:
        doc["preflight"] = pre
        doc["warnings"] = [*pre.get("warnings", []), *doc.get("warnings", [])]
        doc["problems"] = [*pre.get("problems", []), *doc.get("problems", [])]
    return doc


def record_qa(ctx: Any, render_id: int, qa: dict[str, Any]) -> None:
    """Store ``qa`` as ``Render.options["qa"]`` (other options kept). Blocking; the job's ``ctx.session()``."""
    with ctx.session() as db:
        current = db.execute(select(Render.options).where(Render.id == render_id)).scalar_one_or_none()
        options = {**dict(current or {}), "qa": qa}
        db.execute(update(Render).where(Render.id == render_id).values(options=options))
