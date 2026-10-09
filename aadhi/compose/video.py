"""``render_video`` job: Timeline -> deterministic MP4 (+ SRT/VTT captions + chapters).

Pipeline (see ARCHITECTURE §9):

1. mark the ``Render`` row running; load the version (timeline must be built and not stale) and
   rebuild the timeline with ``include_intro`` from the payload (so intro/captions/chapters
   follow the option); check the host's tools (``capabilities``) and free disk space; log the
   preflight warnings (silent scenes, boards replacing missing media: ``preflight``);
2. mint a scoped ``render`` token and screenshot every visual state of the player in render mode
   (network limited to the app origin + the storage's media origins; side panels showing a
   placeholder are rendered title-only and reported);
3. per scene (in parallel, under the process-wide ``render`` limit) encode a segment of an exact
   frame count: mascot clip (its loop phase continuing across scenes, crossfading from the
   previous clip on a position change) / static background -> media in rects (fit, Ken-Burns,
   freeze/loop, panel clips starting at ``show_at``) -> state PNGs as one concat stream
   (cross-fades as premultiplied mixed PNGs, written first) -> scene-layer fade-in (``RENDER_MASCOT_CONTINUITY``; else a fade from black);
   audio = narration + countdown ticks + reveal ding, as PCM of exactly the same length (no A/V
   drift across segments). Segments are cached in the render's durable workspace, so a retry
   reuses them (``workspace``);
4. concat (demuxer, stream copy), BGM ducked under the narration, chapters via ffmetadata,
   ``+faststart``, optional caption burn-in (libass-escaped SRT) or a selectable ``mov_text``
   caption track (``RENDER_SOFT_SUBTITLES``), BT.709 colour tags (``RENDER_COLOR_BT709``);
5. verify the MP4 (frame count, frame grid, sound, black edges: ``qa``; fatal problems fail the
   job with ``render_invalid``);
6. store MP4/SRT/VTT as content-addressed assets (+ ``AssetRef`` rows) and finish the Render row
   with the QA summary and the warnings in ``Render.options`` (``qa``, ``warnings``).

A user cancel is honoured within about a second, also while ffmpeg or the browser is working.
Retryable failures leave the Render row untouched (the worker requeues the job) unless this was
the last attempt.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import math
import re
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import undefer

from ..config import Settings
from ..jobs.base import BudgetExceeded, FatalJobError, JobCancelled, JobContext, RetryableJobError, job_handler
from ..models import AssetRef, Project, ProjectVersion, Render
from ..schemas.timeline import MediaRef, TimedScene, Timeline
from ..storage.assets import Produced, compute_key
from .aio import gather_or_cancel
from .base import STAGE_HEIGHT, STAGE_WIDTH
from .capabilities import render_capabilities
from .captions import to_srt, to_vtt
from .chapters import to_ffmetadata, youtube_chapters
from .ffmpeg import (
    AudioClip,
    Command,
    FFmpegError,
    FinalSpec,
    Input,
    MediaLayer,
    MediaProbe,
    Rect,
    SegmentSpec,
    StateFrame,
    build_final,
    build_segment,
    concat_list,
    ffmpeg_version,
    filter_script_flag,
    image_input,
    loop_phase_frames,
    probe,
    run_ffmpeg,
    state_mixes,
    subtitle_language_code,
    untagged_hd,
    video_input,
)
from .frames import normalize_pngs, transparent_png, write_state_mixes
from .preflight import render_preflight
from .qa import inspect_render, packet_pts
from .screenshot import (
    CaptureResult,
    IntroShot,
    ScreenshotError,
    StateShot,
    capture_render_frames,
    media_origins_for,
)
from .sounds import ensure_sounds_async
from .timeline import BRANDING_PREFIX, build_timeline
from .workspace import (
    RenderWorkspace,
    file_digest,
    free_gb,
    segment_key,
    stat_digest,
    sweep_stale,
)

log = logging.getLogger(__name__)

RENDER_TOKEN_TTL_SECONDS = 4 * 3600
TICK_VOLUME = 0.8
DING_VOLUME = 0.9
BLANK_STATE = "frames/blank.png"  # transparent frame before a late first state / the intro cards (work-dir relative)
_SAFE_BRANDING_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,127}$")
_FONT_BY_LANG = {
    "ta": "Noto Sans Tamil",
    "hi": "Noto Sans Devanagari",
    "te": "Noto Sans Telugu",
    "kn": "Noto Sans Kannada",
    "ml": "Noto Sans Malayalam",
}
_FONT_EXTS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}


@dataclass(frozen=True)
class RenderPayload:
    """Validated ``render_video`` job payload."""

    render_id: int
    burn_captions: bool = False
    include_intro: bool = True
    soft_subtitles: bool = False

    @classmethod
    def from_dict(cls, payload: dict[str, Any], settings: Settings) -> RenderPayload:
        """Parse the payload; options default to the settings (``RENDER_*``)."""
        try:
            rid = int(payload["render_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FatalJobError("render_video payload needs an integer render_id", code="bad_payload") from exc
        burn = payload.get("burn_captions")
        intro = payload.get("include_intro")
        soft = payload.get("soft_subtitles")
        return cls(
            render_id=rid,
            burn_captions=settings.render_burn_captions if burn is None else bool(burn),
            include_intro=settings.render_include_intro if intro is None else bool(intro),
            soft_subtitles=settings.render_soft_subtitles if soft is None else bool(soft),
        )


@dataclass
class _Job:
    render_id: int
    version_id: int
    project_id: int
    revision: int
    title: str
    timeline: Timeline
    # scene id -> 1-based position in the screenplay, hidden scenes counted (the editor's numbering)
    numbers: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def branding_file(settings: Settings, url: str | None) -> Path | None:
    """Absolute local file for a stable ``/branding/<file>`` URL (None if unsafe or missing).

    The path is absolute because ffmpeg runs with the render work dir as its cwd.
    """
    if not url or not url.startswith(BRANDING_PREFIX):
        return None
    name = url[len(BRANDING_PREFIX):]
    if not _SAFE_BRANDING_NAME.match(name) or ".." in name:
        return None
    path = Path(settings.branding_dir).resolve() / name
    return path if path.is_file() else None


def segment_frames(timeline: Timeline, fps: int) -> list[tuple[int, int]]:
    """Frame ranges ``[start, end)`` of the intro (if any) and every scene, from absolute times.

    Boundaries are rounded from absolute times, so per-segment rounding never accumulates drift.
    """
    edges: list[float] = []
    if timeline.intro is not None and timeline.intro.duration > 0:
        edges.append(0.0)
    edges += [s.start for s in timeline.scenes]
    edges.append(timeline.total_duration)
    frames = [math.floor(e * fps + 0.5) for e in edges]  # one rounding per shared boundary
    out: list[tuple[int, int]] = []
    for a, b in zip(frames, frames[1:], strict=False):
        start = out[-1][1] if out else a
        out.append((start, max(start + 1, b)))
    return out


def font_for_language(language: str) -> str:
    """Caption font family for a BCP-47 language (vendored Noto faces for Indic scripts)."""
    return _FONT_BY_LANG.get((language or "").split("-")[0].lower(), "Inter")


def _first_rect(shots: list[StateShot], attr: str, out_size: tuple[int, int]) -> tuple[Rect | None, str | None]:
    """First reported rect for ``attr`` and the page's fit for it.

    The render page reports ``media_fit`` for the MAIN media element only, so the side-panel rect
    never takes it (the panel uses its own ``MediaRef.fit``, as the live player does).
    """
    for shot in shots:
        rect = Rect.from_stage(getattr(shot, attr), stage=(STAGE_WIDTH, STAGE_HEIGHT), out=out_size)
        if rect is not None:
            return rect, shot.media_fit if attr == "media_rect" else None
    return None, None


def _media_input(ref: MediaRef, path: Path) -> Input | None:
    if ref.kind == "image":
        return None  # images are opened with image_input (needs fps)
    return video_input(path, loop=ref.end_behavior == "loop", audio=False, gif=ref.kind == "gif")


@dataclass
class SceneInputs:
    """Local files available to a scene segment."""

    background: Path | None = None
    background_is_image: bool = False
    narration: Path | None = None
    media: dict[str, Path] = field(default_factory=dict)  # asset key -> local file
    tick: Path | None = None
    ding: Path | None = None
    # asset key -> its range is tagged, for the untagged HD YUV videos among ``media`` (``ffmpeg.untagged_hd``)
    untagged_media: dict[str, bool] = field(default_factory=dict)
    background_offset_frames: int = 0  # looped clip phase at the segment start (mascot continuity)
    previous_background: Path | None = None  # the previous scene's clip when the mascot position changed
    previous_background_offset_frames: int = 0


def _state_frames(paths_times: list[tuple[str, float]], duration: float) -> list[StateFrame]:
    frames = [StateFrame(p, min(max(0.0, t), duration)) for p, t in paths_times]
    if frames:
        frames[0] = StateFrame(frames[0].path, 0.0)  # the first state covers the segment from its first frame
    return frames


def scene_segment_spec(
    scene: TimedScene,
    shots: list[StateShot],
    *,
    timeline: Timeline,
    inputs: SceneInputs,
    frames: tuple[int, int],
    settings: Settings,
    shot_path: Callable[[Path], str] = Path.as_posix,
    blank_state: str | None = BLANK_STATE,
) -> SegmentSpec:
    """Segment description for one scene (pure: all files already local).

    ``blank_state`` (work-dir path of a transparent PNG) enables the cross-fades between state
    PNGs; None renders hard cuts. With ``RENDER_MASCOT_CONTINUITY`` the scene transition fades
    only the scene layer (media + states) in over the mascot clip, which continues its loop at
    ``inputs.background_offset_frames`` (and crossfades from ``inputs.previous_background`` when
    the mascot position changed), as in the live player; otherwise the whole picture fades in
    from black and the clip restarts at every scene.
    """
    fps = settings.render_fps
    size = (settings.render_width, settings.render_height)
    n_frames = frames[1] - frames[0]
    duration = n_frames / fps
    shift = scene.start - frames[0] / fps  # scene-relative t -> segment-relative t + shift
    continuity = bool(settings.render_mascot_continuity)
    transition = timeline.transition_seconds
    spec = SegmentSpec(width=size[0], height=size[1], fps=fps, frames=n_frames,
                       fade_in=0.0 if continuity else transition, layer_fade_in=transition if continuity else 0.0,
                       crf=settings.render_crf, preset=settings.render_preset, state_size=(STAGE_WIDTH, STAGE_HEIGHT),
                       background_color=settings.manim_background, blank_state=blank_state,
                       bt709=bool(settings.render_color_bt709))
    if blank_state is None:
        spec.state_fade = 0.0
    if inputs.background is not None:
        spec.background_kind = "image" if inputs.background_is_image else "video"
        spec.background = (image_input(inputs.background, fps) if inputs.background_is_image
                           else video_input(inputs.background, loop=True, audio=False))
        if continuity and not inputs.background_is_image:
            spec.background_offset_frames = max(0, int(inputs.background_offset_frames))
    if continuity and inputs.previous_background is not None and inputs.previous_background != inputs.background:
        spec.previous_background = video_input(inputs.previous_background, loop=True, audio=False)
        spec.previous_background_offset_frames = max(0, int(inputs.previous_background_offset_frames))

    def add_media(ref: MediaRef | None, attr: str, visible_from: float, *, ken_burns: bool) -> None:
        if ref is None or not ref.render_in_mp4 or not ref.asset_key or ref.asset_key not in inputs.media:
            return
        rect, fit = _first_rect(shots, attr, size)
        if rect is None:
            return
        path = inputs.media[ref.asset_key]
        inp = image_input(path, fps) if ref.kind == "image" else _media_input(ref, path)
        if inp is None:
            return
        untagged = ref.kind == "video" and ref.asset_key in inputs.untagged_media
        layer = MediaLayer(input_index=0, kind=ref.kind, rect=rect, fit=fit or ref.fit,  # type: ignore[arg-type]
                           end_behavior=ref.end_behavior,
                           ken_burns=ref.ken_burns if ken_burns and ref.kind == "image" else None,
                           visible_from=max(0.0, visible_from), untagged_yuv=untagged,
                           range_tagged=untagged and inputs.untagged_media[ref.asset_key])
        spec.media.append((inp, layer))

    main = scene.media if scene.media is not None else scene.poster
    add_media(main, "media_rect", 0.0, ken_burns=True)
    if scene.side_panel is not None:  # the panel clip starts playing at show_at (videoTimeAt = t - show_at)
        add_media(scene.side_panel.media, "panel_media_rect", scene.side_panel.show_at + shift, ken_burns=False)

    spec.states = _state_frames([(shot_path(s.png_path), s.t + shift) for s in shots], duration)

    if inputs.narration is not None and scene.audio_asset_key:
        spec.audio.append((Input(str(inputs.narration)), AudioClip(0, delay=scene.audio_offset + shift)))
    quiz = scene.quiz
    if quiz is not None:
        if inputs.tick is not None:
            for k in range(quiz.countdown_seconds):
                t = quiz.countdown_start + k + shift
                if 0 <= t < duration:
                    spec.audio.append((Input(str(inputs.tick)), AudioClip(0, delay=t, volume=TICK_VOLUME)))
        if inputs.ding is not None and 0 <= quiz.reveal_start + shift < duration:
            spec.audio.append((Input(str(inputs.ding)),
                               AudioClip(0, delay=quiz.reveal_start + shift, volume=DING_VOLUME)))
    return spec


def intro_segment_spec(
    timeline: Timeline,
    shots: list[IntroShot],
    *,
    frames: int,
    settings: Settings,
    logo: Path | None,
    logo_has_audio: bool,
    background: Path | None,
    shot_path: Callable[[Path], str] = Path.as_posix,
    blank_state: str = BLANK_STATE,
    logo_probe: MediaProbe | None = None,
) -> SegmentSpec:
    """Intro: static background, logo animation full-frame, then the title-card PNGs (cross-faded).
    ``logo_probe`` tells whether the logo is an untagged HD video (relabelled BT.709, see ``ffmpeg``)."""
    intro = timeline.intro
    assert intro is not None
    fps = settings.render_fps
    size = (settings.render_width, settings.render_height)
    duration = frames / fps
    spec = SegmentSpec(width=size[0], height=size[1], fps=fps, frames=frames, crf=settings.render_crf,
                       preset=settings.render_preset, state_size=(STAGE_WIDTH, STAGE_HEIGHT),
                       background_color=settings.manim_background, fade_in=timeline.transition_seconds,
                       blank_state=blank_state, bt709=bool(settings.render_color_bt709))
    if background is not None:
        spec.background_kind = "image"
        spec.background = image_input(background, fps)
    logo_seconds = min(intro.logo_duration, duration)
    if logo is not None and logo_seconds > 0:
        spec.media.append((video_input(logo, loop=False, audio=False),
                           MediaLayer(0, "video", Rect(0, 0, size[0], size[1]), fit="cover", end_behavior="freeze",
                                      visible_from=0.0, visible_to=logo_seconds,
                                      untagged_yuv=logo_probe is not None and untagged_hd(logo_probe),
                                      range_tagged=logo_probe is not None and bool(logo_probe.color_range))))
        if logo_has_audio:
            spec.audio.append((video_input(logo), AudioClip(0, delay=0.0, duration=logo_seconds,
                                                            fade_out=min(0.6, logo_seconds))))
    cards = sorted(intro.cards, key=lambda c: c.start)
    # each card's PNG from the card's start (the blank state covers the logo part before it)
    spec.states = [StateFrame(shot_path(shot.png_path), min(max(0.0, card.start), duration))
                   for shot, card in zip(shots, cards, strict=False)]
    return spec


# ---------------------------------------------------------------------------
# DB helpers (blocking: always called through asyncio.to_thread)
# ---------------------------------------------------------------------------


def _start_render(ctx: JobContext, payload: RenderPayload) -> tuple[int, dict[str, Any] | None]:
    """Atomically mark the render running. Returns (version_id, previous result if already succeeded)."""
    with ctx.session() as db:
        render = db.get(Render, payload.render_id)
        if render is None:
            raise FatalJobError(f"render {payload.render_id} not found", code="not_found")
        if ctx.version_id is not None and render.version_id != ctx.version_id:
            raise FatalJobError("render does not belong to this job's version", code="bad_payload")
        if render.status == "succeeded" and render.video_asset_key:
            return render.version_id, {
                "render_id": render.id, "video_asset_key": render.video_asset_key,
                "srt_asset_key": render.srt_asset_key, "vtt_asset_key": render.vtt_asset_key,
                "duration_s": render.duration_s}
        values: dict[str, Any] = {"status": "running"}
        if ctx.job_id:
            values["job_id"] = ctx.job_id
        changed = db.execute(
            update(Render)
            .where(Render.id == payload.render_id, Render.status.in_(("queued", "running", "failed")))
            .values(**values)
            .returning(Render.id)
        ).first()
        if changed is None:
            raise FatalJobError(f"render {payload.render_id} cannot start (status {render.status})", code="conflict")
        return render.version_id, None


def _load_job(ctx: JobContext, payload: RenderPayload, version_id: int) -> _Job:
    with ctx.session() as db:
        version = db.execute(
            select(ProjectVersion)
            .options(undefer(ProjectVersion.screenplay), undefer(ProjectVersion.asset_manifest))
            .where(ProjectVersion.id == version_id)
        ).scalar_one_or_none()
        if version is None:
            raise FatalJobError("version not found", code="not_found")
        if not version.has_timeline:
            raise FatalJobError("build the lecture before rendering (no timeline)", code="timeline_missing")
        if version.built_revision != version.revision:
            raise FatalJobError("the screenplay changed since the last build; rebuild before rendering",
                                code="timeline_stale")
        screenplay = version.get_screenplay()
        if screenplay is None or not screenplay.scenes:
            raise FatalJobError("the lecture has no scenes", code="empty")
        if all(s.hidden for s in screenplay.scenes):
            raise FatalJobError("every scene of the lecture is hidden; show at least one scene", code="empty")
        manifest = version.get_manifest()
        project = db.get(Project, version.project_id)
        title = (project.title if project else "") or screenplay.session_title or "Lecture"
        project_id, revision = version.project_id, version.revision
    timeline = build_timeline(screenplay, manifest, settings=ctx.settings, version_id=version_id, revision=revision,
                              include_intro=payload.include_intro)
    numbers = {s.id: i + 1 for i, s in enumerate(screenplay.scenes)}
    return _Job(payload.render_id, version_id, project_id, revision, title, timeline, numbers)


def _finish_render(ctx: JobContext, job: _Job, values: dict[str, Any], keys: list[str],
                   options: dict[str, Any] | None = None) -> None:
    """Mark the render succeeded; ``options`` (QA summary, warnings) are merged into ``Render.options``."""
    with ctx.session() as db:
        _add_asset_refs(db, job.project_id, keys)
        if options:
            current = db.execute(select(Render.options).where(Render.id == job.render_id)).scalar_one_or_none()
            values = {**values, "options": {**dict(current or {}), **options}}
        cond = [Render.id == job.render_id, Render.status == "running"]
        if ctx.job_id:
            cond.append(Render.job_id == ctx.job_id)
        changed = db.execute(update(Render).where(*cond).values(status="succeeded", **values)
                             .returning(Render.id)).first()
        if changed is None:
            raise JobCancelled("render row changed while rendering", reason="lease_lost")


def _add_asset_refs(db: Any, project_id: int, keys: list[str]) -> None:
    """Insert missing ``AssetRef`` rows atomically (ON CONFLICT DO NOTHING)."""
    keys = sorted({k for k in keys if k})
    if not keys:
        return
    rows = [{"project_id": project_id, "asset_key": k} for k in keys]
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        db.execute(pg_insert(AssetRef).values(rows).on_conflict_do_nothing())
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        db.execute(sqlite_insert(AssetRef).values(rows).on_conflict_do_nothing())
    else:  # pragma: no cover - other dialects
        existing = set(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project_id,
                                                                   AssetRef.asset_key.in_(keys))).scalars())
        db.add_all(AssetRef(project_id=project_id, asset_key=k) for k in keys if k not in existing)


def _mark_render(ctx: JobContext, render_id: int, status: str) -> None:
    try:
        with ctx.session() as db:
            db.execute(update(Render).where(Render.id == render_id, Render.status.in_(("queued", "running")))
                       .values(status=status))
    except Exception as exc:  # noqa: BLE001 - best effort while already failing
        log.warning("could not mark render %s %s: %s", render_id, status, type(exc).__name__)


def _local_copies(ctx: JobContext, keys: set[str], dest: Path, *, required: set[str]) -> dict[str, Path]:
    """Absolute local files for ``keys`` (blocking).

    A missing ``required`` asset (narration, composited media) fails the render: missing in the
    store -> FatalJobError (rebuild needed), unreadable -> RetryableJobError. Optional assets
    (sound effects) are skipped with a warning.
    """
    out: dict[str, Path] = {}
    for key in sorted(keys):
        try:
            out[key] = Path(ctx.assets.local_copy(key, dest)).resolve()
        except (KeyError, FileNotFoundError) as exc:
            if key in required:
                raise FatalJobError(f"asset {key[:48]} is missing from storage; rebuild the lecture before rendering",
                                    code="asset_missing") from exc
            ctx.log(f"asset {key[:24]}… unavailable for the render ({type(exc).__name__}); skipped", level="warning")
        except OSError as exc:
            if key in required:
                raise RetryableJobError(f"asset {key[:48]} could not be read ({type(exc).__name__})") from exc
            ctx.log(f"asset {key[:24]}… unavailable for the render ({type(exc).__name__}); skipped", level="warning")
    return out


def _final_attempt(ctx: JobContext) -> bool:
    """True when the worker will not retry this job after a retryable failure."""
    limit = getattr(ctx, "max_attempts", None)
    if not isinstance(limit, int) or isinstance(limit, bool):
        limit = ctx.settings.job_max_attempts
    return int(getattr(ctx, "attempt", 1) or 1) >= max(1, limit)


# ---------------------------------------------------------------------------
# Token / limits (optional modules owned by other areas)
# ---------------------------------------------------------------------------


def _mint_token(settings: Settings, version_id: int, ttl_seconds: int, *, render_id: int | None = None,
                include_intro: bool | None = None) -> str:
    """Scoped ``render`` token for the render page (``vid`` + optional ``rid``/``include_intro`` claims)."""
    try:
        from ..auth.tokens import create_scoped_token
    except ImportError as exc:
        raise FatalJobError("render tokens are unavailable (aadhi.auth.tokens missing)", code="config") from exc
    claims: dict[str, Any] = {"vid": version_id}
    if render_id is not None:
        claims["rid"] = render_id
    if include_intro is not None:
        claims["include_intro"] = include_intro
    return create_scoped_token("render", claims, ttl_seconds, settings)


def _render_slot_factory(settings: Settings) -> Callable[[], contextlib.AbstractAsyncContextManager[Any]]:
    try:
        from ..jobs.limits import acquire

        return lambda: acquire("render")
    except ImportError:
        sem = asyncio.Semaphore(max(1, settings.render_concurrency))

        @contextlib.asynccontextmanager
        async def slot() -> AsyncIterator[None]:
            async with sem:
                yield

        return slot


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _scene_required_keys(scene: TimedScene) -> set[str]:
    """Assets the MP4 cannot do without: narration, the main media (or poster) and panel media."""
    keys: set[str] = set()
    main = scene.media if scene.media is not None else scene.poster
    for ref in (main, scene.side_panel.media if scene.side_panel else None):
        if ref is not None and ref.asset_key and ref.render_in_mp4:
            keys.add(ref.asset_key)
    if scene.audio_asset_key:
        keys.add(scene.audio_asset_key)
    return keys


def _copy_fonts(settings: Settings, dest: Path) -> Path | None:
    src = Path(settings.web_dir).resolve() / "vendor" / "fonts"
    if not src.is_dir():
        return None
    dest.mkdir(parents=True, exist_ok=True)
    for f in src.iterdir():
        if f.is_file() and f.suffix.lower() in _FONT_EXTS:
            shutil.copyfile(f, dest / f.name)
    return dest


def _prepare_frames(capture: CaptureResult, work: Path) -> int:
    """Transparent gap frame + RGBA/size normalisation of every screenshot (blocking)."""
    transparent_png(work / BLANK_STATE, (STAGE_WIDTH, STAGE_HEIGHT))
    paths = [s.png_path for shots in capture.scenes.values() for s in shots] + [s.png_path for s in capture.intro]
    return normalize_pngs(paths, (STAGE_WIDTH, STAGE_HEIGHT))


async def _write_files(work: Path, files: dict[str, str]) -> None:
    def write() -> None:
        for name, text in files.items():
            (work / name).write_text(text, encoding="utf-8")

    await asyncio.to_thread(write)


async def _run(cmd: Command, ctx: JobContext, work: Path, *, timeout: float) -> None:
    await _write_files(work, cmd.files)
    await run_ffmpeg(cmd.args, settings=ctx.settings, cwd=work, timeout=timeout, check_cancelled=ctx.check_cancelled)


async def _capture(ctx: JobContext, job: _Job, payload: RenderPayload, frames_dir: Path) -> CaptureResult:
    settings = ctx.settings
    tl = job.timeline
    token = _mint_token(settings, job.version_id, RENDER_TOKEN_TTL_SECONDS, render_id=job.render_id,
                        include_intro=payload.include_intro)
    intro_times = [c.start + c.duration / 2 for c in tl.intro.cards] if tl.intro else []
    origins = await asyncio.to_thread(media_origins_for, ctx.storage, settings)

    def on_scene(done: int, total: int) -> None:
        ctx.progress("screenshots", 0.02 + 0.38 * done / max(1, total), f"Captured scene {done}/{total}")

    ctx.progress("screenshots", 0.02, "Opening the render page")
    try:
        capture = await capture_render_frames(
            settings=settings, token=token, timeline=tl, out_dir=frames_dir, intro_times=intro_times, pages=2,
            media_origins=origins, on_scene_done=on_scene, check_cancelled=ctx.check_cancelled)
    except ScreenshotError as exc:
        message = settings.redact(str(exc))
        if exc.retryable:
            raise RetryableJobError(message) from exc
        raise FatalJobError(message, code=exc.code) from exc
    if capture.blocked_media:
        ctx.log("Some images could not be loaded by the render page (blocked origins: "
                f"{', '.join(capture.blocked_media[:5])}); the video may show placeholders.", level="warning")
    if capture.websocket_attempts:
        ctx.log(f"The render page tried to open {len(capture.websocket_attempts)} WebSocket(s); they were blocked.",
                level="warning")
    return capture


# --- preflight / warnings ----------------------------------------------------------------------


def _warning(item: dict[str, Any]) -> dict[str, Any]:
    return {k: item.get(k) for k in ("scene_index", "scene_id", "title", "reason", "blocking", "message")}


def render_warnings(timeline: Timeline, capture: CaptureResult | None = None,
                    numbers: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """Preflight items of the rendered timeline plus side panels the render page showed title-only. With ``numbers``
    (scene id -> 1-based screenplay position), each item also carries ``scene_number``, as the editor numbers scenes
    (``scene_index`` counts only the scenes that play)."""
    items = [_warning(it) for it in render_preflight(timeline)]
    known = {(it["scene_id"], it["reason"]) for it in items}
    for index, shots in sorted((capture.scenes if capture else {}).items()):
        notices = sorted({n for s in shots for n in s.notices})
        if not notices or index >= len(timeline.scenes):
            continue
        scene = timeline.scenes[index]
        if (scene.scene_id, "panel_media_missing") in known:
            continue
        items.append({"scene_index": index, "scene_id": scene.scene_id, "title": scene.title or scene.scene_id,
                      "reason": "panel_notice", "blocking": False,
                      "message": f"A side panel could not show its media ({notices[0]}); the video shows its title only."})
    if numbers is not None:
        for it in items:
            it["scene_number"] = numbers.get(it["scene_id"]) if it.get("scene_id") else None
    return items


def _log_warnings(ctx: JobContext, items: list[dict[str, Any]]) -> None:
    for it in items[:50]:
        number = it.get("scene_number")  # the editor's numbering; older items: the timeline position
        if not isinstance(number, int):
            number = int(it["scene_index"]) + 1 if isinstance(it.get("scene_index"), int) else None
        where = f"Scene {number}" if number is not None else "A scene"
        ctx.log(f"{where} ({it.get('title') or it.get('scene_id')}): {it.get('message')}",
                level="warning" if it.get("blocking") else "info", reason=it.get("reason"))


# --- workspace helpers (blocking) -----------------------------------------------------------------


def _render_statuses(ctx: JobContext, ids: list[int]) -> dict[int, str | None]:
    if not ids:
        return {}
    with ctx.session() as db:
        rows = db.execute(select(Render.id, Render.status).where(Render.id.in_(ids))).all()
    found: dict[int, str | None] = {int(r[0]): str(r[1]) for r in rows}
    return {i: found.get(i) for i in ids}


def _sweep_workspaces(ctx: JobContext, current_id: int) -> int:
    """Best-effort cleanup of finished / expired render workspaces (never fails the render)."""
    return sweep_stale(ctx.settings, lambda ids: _render_statuses(ctx, ids), exclude=[current_id])


def sweep_render_workspaces(settings: Settings, session: Callable[[], Any]) -> int:
    """Host housekeeping outside a render (worker maintenance, ``cleanup`` job): stale workspaces and
    old temp dirs. ``session`` opens a database session (a context manager). Never raises."""

    def statuses(ids: list[int]) -> dict[int, str | None]:
        with session() as db:
            rows = db.execute(select(Render.id, Render.status).where(Render.id.in_(ids))).all()
        found: dict[int, str | None] = {int(r[0]): str(r[1]) for r in rows}
        return {i: found.get(i) for i in ids}

    return sweep_stale(settings, statuses)


def _input_digests(segments: list[tuple[str, SegmentSpec]], local: dict[str, Path], work: Path) -> dict[str, str]:
    """Content digest of every input path used by the segment commands (see ``workspace.segment_key``)."""
    by_path = {str(p): f"asset:{k}" for k, p in local.items()}
    out: dict[str, str] = {}
    for _, spec in segments:
        inputs = [spec.background, spec.previous_background, *(i for i, _ in spec.media), *(i for i, _ in spec.audio)]
        for inp in inputs:
            if inp is None or inp.fmt == "lavfi" or inp.path in out:
                continue
            if inp.path in by_path:
                out[inp.path] = by_path[inp.path]
                continue
            path = Path(inp.path)
            if path.is_file():
                out[inp.path] = stat_digest(path)
        pngs = [s.path for s in spec.states] + ([spec.blank_state] if spec.blank_state else [])
        for png in [*pngs, *(m.out for m in state_mixes(spec))]:
            if png not in out and (work / png).is_file():
                out[png] = "png:" + file_digest(work / png)
    return out


async def _untagged_media(tl: Timeline, local: dict[str, Path], settings: Settings) -> dict[str, bool]:
    """Asset key -> range tagged, for the scenes' video media that are untagged HD YUV (probed once each; a
    file that cannot be probed counts as tagged, i.e. left as it was)."""
    keys = {ref.asset_key for s in tl.scenes
            for ref in (s.media, s.poster, s.side_panel.media if s.side_panel is not None else None)
            if ref is not None and ref.kind == "video" and ref.asset_key and ref.asset_key in local}
    out: dict[str, bool] = {}
    for key in sorted(keys):
        with contextlib.suppress(FFmpegError):
            info = await probe(local[key], settings=settings)
            if untagged_hd(info):
                out[key] = bool(info.color_range)
    return out


async def _segment_frames_ok(path: Path, frames: int, settings: Settings) -> bool:
    try:
        return len(await packet_pts(path, settings)) == frames
    except FFmpegError:
        return False


def _clip_phases(tl: Timeline, bounds: list[tuple[int, int]], offset: int, fps: int,
                 clip_for: Callable[[TimedScene], Path | None],
                 durations: dict[Path, float | None]) -> list[tuple[int, Path | None, int]]:
    """Per scene: (clip phase frames, previous scene's clip when it differs, its phase)."""
    out: list[tuple[int, Path | None, int]] = []
    prev_clip: Path | None = None
    for i, scene in enumerate(tl.scenes):
        clip = clip_for(scene)
        start = bounds[i + offset][0]
        phase = loop_phase_frames(start, fps=fps, clip_seconds=durations.get(clip)) if clip else 0
        prev = prev_clip if i > 0 and prev_clip is not None and prev_clip != clip else None
        prev_phase = loop_phase_frames(start, fps=fps, clip_seconds=durations.get(prev)) if prev else 0
        out.append((phase, prev, prev_phase))
        prev_clip = clip
    return out


async def _render(ctx: JobContext, job: _Job, payload: RenderPayload, ws: RenderWorkspace) -> dict[str, Any]:
    settings = ctx.settings
    tl = job.timeline
    fps = settings.render_fps
    work = ws.attempt_dir
    frames_dir = work / "frames"
    inputs_dir = work / "inputs"
    await asyncio.to_thread(inputs_dir.mkdir, parents=True, exist_ok=True)

    caps = await asyncio.to_thread(render_capabilities, settings, browser=False)
    if not caps.ok:
        raise FatalJobError("This server cannot render videos: " + " ".join(caps.reasons), code="render_unavailable")
    burn = payload.burn_captions and bool(tl.captions)
    if burn and not caps.burn_captions:
        raise FatalJobError("Burning captions in needs an ffmpeg build with libass (the subtitles filter); "
                            "render without burned-in captions or install a full ffmpeg.", code="render_unavailable")
    min_free = float(settings.render_min_free_gb or 0)
    if min_free > 0:
        free = await asyncio.to_thread(free_gb, ws.root)
        if free < min_free:
            raise FatalJobError(f"Not enough free disk space to render ({free:.1f} GB free, {min_free:g} GB needed).",
                                code="disk_full")
    version = await ffmpeg_version(settings)
    script_flag = filter_script_flag(version)
    tl_hash = hashlib.sha256(tl.model_dump_json().encode("utf-8")).hexdigest()
    resumable = await asyncio.to_thread(ws.prepare, {
        "timeline": tl_hash, "ffmpeg": version, "w": settings.render_width, "h": settings.render_height,
        "fps": fps, "crf": settings.render_crf, "preset": settings.render_preset})
    preflight = render_warnings(tl, numbers=job.numbers)
    _log_warnings(ctx, preflight)

    sounds = await ensure_sounds_async(ctx.assets)
    ctx.check_cancelled()
    capture = await _capture(ctx, job, payload, frames_dir)
    await asyncio.to_thread(_prepare_frames, capture, work)
    warnings = render_warnings(tl, capture, numbers=job.numbers)
    _log_warnings(ctx, warnings[len(preflight):])

    required: set[str] = set()
    for s in tl.scenes:
        required |= _scene_required_keys(s)
    keys = required | {sounds["tick"].asset_key, sounds["ding"].asset_key}
    local = await asyncio.to_thread(_local_copies, ctx, keys, inputs_dir, required=required)
    branding_urls = {tl.branding.static_background_url, tl.branding.bgm_url, *tl.branding.mascot_clips.values()}
    if tl.intro is not None:
        branding_urls |= {tl.intro.logo_video_url, tl.intro.background_url}
    branding_paths = await asyncio.to_thread(
        lambda: {u: branding_file(settings, u) for u in branding_urls if u})

    def branding(url: str | None) -> Path | None:
        return branding_paths.get(url or "")

    def clip_for(scene: TimedScene) -> Path | None:
        return branding(tl.branding.mascot_clips.get(scene.layout.mascot_position))

    bounds = segment_frames(tl, fps)
    segments: list[tuple[str, SegmentSpec]] = []

    def rel(p: Path) -> str:
        return p.relative_to(work).as_posix()

    offset = 0
    if tl.intro is not None and tl.intro.duration > 0:
        logo = branding(tl.intro.logo_video_url)
        logo_probe: MediaProbe | None = None
        if logo is not None:
            with contextlib.suppress(FFmpegError):
                logo_probe = await probe(logo, settings=settings)
        segments.append(("intro", intro_segment_spec(
            tl, capture.intro, frames=bounds[0][1] - bounds[0][0], settings=settings, logo=logo,
            logo_has_audio=logo_probe is not None and logo_probe.has_audio,
            background=branding(tl.intro.background_url), shot_path=rel, blank_state=BLANK_STATE,
            logo_probe=logo_probe)))
        offset = 1
    durations: dict[Path, float | None] = {}
    if settings.render_mascot_continuity:
        for clip in {c for c in map(clip_for, tl.scenes) if c is not None}:
            try:
                durations[clip] = (await probe(clip, settings=settings)).duration
            except FFmpegError:
                durations[clip] = None
    phases = _clip_phases(tl, bounds, offset, fps, clip_for, durations)
    untagged = await _untagged_media(tl, local, settings) if settings.render_color_bt709 else {}
    for i, scene in enumerate(tl.scenes):
        clip = clip_for(scene)
        bg = clip or branding(tl.branding.static_background_url)
        phase, prev, prev_phase = phases[i]
        inp = SceneInputs(
            background=bg, background_is_image=bg is not None and clip is None,
            narration=local.get(scene.audio_asset_key or ""),
            media=dict(local),
            tick=local.get(sounds["tick"].asset_key), ding=local.get(sounds["ding"].asset_key),
            background_offset_frames=phase, previous_background=prev, previous_background_offset_frames=prev_phase,
            untagged_media=untagged)
        segments.append((f"s{i:03d}", scene_segment_spec(scene, capture.scenes.get(i, []), timeline=tl, inputs=inp,
                                                         frames=bounds[i + offset], settings=settings,
                                                         shot_path=rel, blank_state=BLANK_STATE)))

    mixes = {m.out: m for _, spec in segments for m in state_mixes(spec)}  # cross-fade frames, premultiplied
    await asyncio.to_thread(write_state_mixes, mixes.values(), work)
    digests = await asyncio.to_thread(_input_digests, segments, local, work)
    slot = _render_slot_factory(settings)
    done = 0
    reused = 0

    async def encode(name: str, spec: SegmentSpec) -> None:
        nonlocal done, reused
        cmd = build_segment(spec, name=name, script_flag=script_flag)
        key = segment_key(cmd, name, digests, {"ffmpeg": version})
        async with slot():
            ctx.check_cancelled()
            cached = await asyncio.to_thread(ws.cached, key, frames=spec.frames, samples=spec.total_samples)
            if cached is not None and await _segment_frames_ok(cached, spec.frames, settings):
                await asyncio.to_thread(ws.adopt, key, name)
                reused += 1
            else:
                await _run(cmd, ctx, work, timeout=600 + 30 * spec.duration)
                await asyncio.to_thread(ws.store, key, name, frames=spec.frames, samples=spec.total_samples)
        done += 1
        ctx.progress("segments", 0.40 + 0.45 * done / len(segments), f"Encoded {done}/{len(segments)} segments")

    ctx.progress("segments", 0.40, f"Encoding {len(tl.scenes)} scenes")
    await gather_or_cancel([encode(name, spec) for name, spec in segments])  # first failure stops the siblings
    if reused:
        ctx.log(f"Reused {reused} of {len(segments)} segment(s) encoded by an earlier attempt"
                + ("" if resumable else " (workspace reset)"), level="info")

    # --- final mux -----------------------------------------------------------
    ctx.check_cancelled()
    ctx.progress("mux", 0.86, "Joining segments")
    total_frames = bounds[-1][1]
    total_seconds = total_frames / fps
    names = [n for n, _ in segments]
    srt_text, vtt_text = to_srt(tl.captions), to_vtt(tl.captions)
    soft = payload.soft_subtitles and bool(tl.captions) and not burn
    bt709 = bool(settings.render_color_bt709)
    meta_title = tl.meta.get("session_title") or job.title
    files = {
        "video.ffconcat": concat_list([f"{n}.mp4" for n in names]),
        "audio.ffconcat": concat_list([f"{n}.wav" for n in names]),
        "chapters.ffmeta": to_ffmetadata(tl.chapters, total_seconds, title=str(meta_title),
                                         extra={"artist": "REC - Aadhi EduEngine", "album": job.title}),
    }
    if burn:
        files["captions.burn.srt"] = to_srt(tl.captions, burn_in=True)
    if soft:
        files["captions.soft.srt"] = srt_text
    await _write_files(work, files)
    fonts_rel: str | None = None
    if burn:
        fonts = await asyncio.to_thread(_copy_fonts, settings, work / "fonts")
        fonts_rel = "fonts" if fonts is not None else None
    bgm = branding(tl.branding.bgm_url) if settings.render_bgm else None
    final = FinalSpec(
        video_list="video.ffconcat", audio_list="audio.ffconcat", metadata="chapters.ffmeta",
        total_seconds=total_seconds, output="output.mp4", bgm=str(bgm) if bgm else None,
        bgm_volume=tl.branding.bgm_volume, bgm_delay=tl.intro.logo_duration if tl.intro else 0.0,
        burn_subtitles="captions.burn.srt" if burn else None, fonts_dir=fonts_rel,
        font_name=font_for_language(tl.language), width=settings.render_width, height=settings.render_height,
        fps=fps, crf=settings.render_crf, preset=settings.render_preset,
        soft_subtitles="captions.soft.srt" if soft else None, subtitle_language=subtitle_language_code(tl.language),
        bt709=bt709)
    await _run(build_final(final, filter_script="final.filtergraph", script_flag=script_flag), ctx, work,
               timeout=900 + (10 * total_seconds if burn else 0))
    output = work / "output.mp4"
    info = await probe(output, settings=settings)
    duration = round(info.duration or total_seconds, 3)

    # --- output QA -------------------------------------------------------------
    options: dict[str, Any] = {}
    if warnings:
        options["warnings"] = warnings
    if settings.render_output_qa:
        ctx.check_cancelled()
        ctx.progress("check", 0.92, "Checking the video")
        expect_audio = any(s.audio_asset_key and s.audio_asset_key in local for s in tl.scenes)
        qa = await inspect_render(output, settings=settings, fps=fps, expected_frames=total_frames,
                                  expect_audio=expect_audio, expect_bt709=bt709, expect_subtitles=soft,
                                  check_cancelled=ctx.check_cancelled)
        options["qa"] = qa.summary()
        for text in qa.warnings:
            ctx.log(f"Video check: {text}", level="warning")
        if qa.problems:
            raise FatalJobError("The rendered video failed its checks: " + "; ".join(qa.problems) + ".",
                                code="render_invalid")
        level = f", sound peak {qa.audio_max_db:.1f} dB" if qa.audio_max_db is not None else ""
        ctx.log(f"Video check passed: {qa.frames} frames at {fps} fps{level}.", level="info")

    # --- store -----------------------------------------------------------------
    ctx.check_cancelled()
    ctx.progress("store", 0.95, "Saving the video")
    base_inputs = {"render_id": job.render_id, "version_id": job.version_id, "revision": job.revision,
                   "timeline": tl_hash, "burn": payload.burn_captions, "intro": payload.include_intro,
                   "w": settings.render_width, "h": settings.render_height, "fps": fps, "crf": settings.render_crf,
                   "preset": settings.render_preset, "bgm": bool(bgm)}
    for flag, on in (("soft", soft), ("bt709", bt709), ("continuity", bool(settings.render_mascot_continuity))):
        if on:
            base_inputs[flag] = True
    video_key = compute_key("render", base_inputs)
    srt_bytes, vtt_bytes = srt_text.encode("utf-8"), vtt_text.encode("utf-8")
    srt_key = compute_key("captions", {"render_id": job.render_id, "fmt": "srt",
                                       "sha": hashlib.sha256(srt_bytes).hexdigest()})
    vtt_key = compute_key("captions", {"render_id": job.render_id, "fmt": "vtt",
                                       "sha": hashlib.sha256(vtt_bytes).hexdigest()})
    await asyncio.to_thread(ctx.assets.put, video_key, "render", Produced(
        path=output, mime="video/mp4", duration_s=duration, width=settings.render_width,
        height=settings.render_height, meta={"render_id": job.render_id, "version_id": job.version_id}))
    await asyncio.to_thread(ctx.assets.put, srt_key, "captions",
                            Produced(data=srt_bytes, mime="application/x-subrip", meta={"render_id": job.render_id}))
    await asyncio.to_thread(ctx.assets.put, vtt_key, "captions",
                            Produced(data=vtt_bytes, mime="text/vtt", meta={"render_id": job.render_id}))
    chapters_text = youtube_chapters(tl)
    flush = getattr(ctx, "flush", None)
    if flush is not None:
        await flush()
    await asyncio.to_thread(_finish_render, ctx, job, {
        "video_asset_key": video_key, "srt_asset_key": srt_key, "vtt_asset_key": vtt_key, "duration_s": duration,
        "chapters_text": chapters_text, "built_revision": job.revision,
    }, [video_key, srt_key, vtt_key], options)
    ctx.progress("done", 1.0, "Video ready")
    return {"render_id": job.render_id, "video_asset_key": video_key, "srt_asset_key": srt_key,
            "vtt_asset_key": vtt_key, "duration_s": duration, "chapters": len(info.chapters)}


@job_handler("render_video")
async def render_video(ctx: JobContext) -> dict[str, Any]:
    """Job handler: payload ``{render_id, burn_captions, include_intro, soft_subtitles?}`` -> MP4 + captions + chapters.

    Works in a durable workspace (``aadhi.compose.workspace``): the attempt directory is always
    removed; the workspace is removed on success and on a user cancel, and kept after a failure so
    a retry reuses the segments already encoded.
    """
    payload = RenderPayload.from_dict(dict(ctx.payload or {}), ctx.settings)
    version_id, finished = await asyncio.to_thread(_start_render, ctx, payload)
    if finished is not None:  # idempotent retry of a finished render
        return finished
    await asyncio.to_thread(_sweep_workspaces, ctx, payload.render_id)
    attempt = int(getattr(ctx, "attempt", 1) or 1)
    try:
        ws = await asyncio.to_thread(RenderWorkspace.open, ctx.settings, payload.render_id, attempt)
    except OSError as exc:
        await asyncio.to_thread(_mark_render, ctx, payload.render_id, "failed")
        raise FatalJobError(f"the render workspace could not be created ({type(exc).__name__})",
                            code="workspace") from exc
    keep = True
    try:
        job = await asyncio.to_thread(_load_job, ctx, payload, version_id)
        result = await _render(ctx, job, payload, ws)
        keep = False
        return result
    except JobCancelled as exc:
        # Only a user cancel ends the render; on lease loss / shutdown the job is retried (possibly by
        # another worker already), so the Render row is left restartable and untouched.
        if exc.reason == "cancelled":
            keep = False
            await asyncio.to_thread(_mark_render, ctx, payload.render_id, "cancelled")
        raise
    except asyncio.CancelledError:
        raise  # worker shutdown: the job is requeued and the render restarts from "running"
    except (FatalJobError, BudgetExceeded):
        await asyncio.to_thread(_mark_render, ctx, payload.render_id, "failed")
        raise
    except FFmpegError as exc:
        await asyncio.to_thread(_mark_render, ctx, payload.render_id, "failed")
        ctx.log(f"ffmpeg failed: {ctx.settings.redact(exc.stderr_tail[-600:])}", level="error")
        raise FatalJobError(ctx.settings.redact(f"video encoding failed: {exc}"), code="render_failed") from exc
    except Exception:
        # RetryableJobError and unexpected errors: the worker requeues the job while attempts remain,
        # so the Render row only becomes "failed" on the last attempt.
        if _final_attempt(ctx):
            await asyncio.to_thread(_mark_render, ctx, payload.render_id, "failed")
        raise
    finally:
        await asyncio.to_thread(ws.finish, keep=keep)
