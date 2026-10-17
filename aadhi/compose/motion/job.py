"""The ``render_video`` job's Dynamic branch: preflight, the motion engine, QA, storage (DYNAMIC_VIDEO_DESIGN §7.1).

``aadhi.compose.video.render_video`` picks the engine from the timeline it built: ``timeline.presentation.style ==
"dynamic"`` comes here (one render never mixes engines); every classic timeline keeps the classic path untouched.
The render token needs no new claim (the same scoped ``render`` token as ``/render-frame``).

1. ``DYNAMIC_ENABLED`` must be on (else ``dynamic_disabled``); the host must have ffmpeg with libx264 and AAC and
   ``RENDER_MIN_FREE_GB`` of free disk;
2. **preflight** (``aadhi.compose.dynamic.preflight``) before any capture: the stage loads once, sampled frames'
   ``textBoxes()`` are checked, automatic remedies are applied to the served timeline; a blocking finding stores
   the preflight QA on the render and fails the job with ``preflight_blocked`` and the teacher's action;
3. **capture** (``engine.render_motion``) with the preflight's served timeline, the workspace's chunk cache (a
   retry captures only the chunks the failed attempt did not finish), captions in the stage when burned
   (``presentation.captions_burn`` unless the request says otherwise) and, for a clean file, the default-on
   ``mov_text`` track;
4. the container checks ran inside the engine; ``Render.options["qa"]`` gets ``dynamic.container.motion_qa`` plus the
   engine's measurements (``qa.motion``: workers, fps per worker, chunks reused, timings, memory);
5. MP4 / SRT / VTT are stored as content-addressed assets and the render finishes, as the classic path does.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from typing import Any

from ...jobs.base import FatalJobError, JobContext, RetryableJobError
from ...storage.assets import Produced, compute_key
from ..base import output_size
from ..capabilities import render_capabilities
from ..captions import to_srt, to_vtt
from ..chapters import youtube_chapters
from ..dynamic.container import ContainerCheckFailed, motion_qa, record_qa
from ..dynamic.preflight import Remedy, apply_remedies, raise_if_blocked, run_preflight
from ..dynamic.stage_page import StageError
from ..ffmpeg import probe
from ..workspace import RenderWorkspace, free_gb
from .cache import ChunkCache
from .engine import ENGINE, MotionError, is_dynamic, media_origins, render_motion

TOKEN_TTL_SECONDS = 4 * 3600
# The classic render shows such a side panel's title (compose.preflight.MESSAGES); the Dynamic stage lays the scene out
# without the medium it cannot show (board_generic.mediaOf), so its warnings say that instead.
DYNAMIC_MESSAGES = {
    "panel_media_missing": "The side panel's picture or animation is not available: the video leaves the panel out.",
    "panel_not_in_video": "This side panel (an online GIF) is not included in the video: the scene is laid out "
                          "without it.",
}
# Side panels the Dynamic stage does not draw yet. board_generic shows a panel's picture or clip and the terminal card
# (web/js/dynamic/templates/board_generic.js mediaOf, cardSpecs) and lays the scene out without any other panel, in the
# live player and the MP4 alike; the classic render draws them. Each such panel gets a render warning (G10: nothing is
# dropped silently). Remove a kind when a template draws it.
UNDRAWN_PANEL_KINDS = {
    "model_3d": "a 3D model",
    "skill_tree": "a skill tree",
    "quiz": "a quiz question",
}
# A chart or graph panel is drawn as a real plot (owner decision 1, step 5) by chart_build (its own layout, the scene's
# format) and by the templates that draw panels through _shared.js's panel helper (its side-panel layout: 16:9 the side
# box, 9:16 the reel beat's column), whenever the chart engine takes it (charts.draws_chart, the page's chartPlan).
# board_generic and media_split still draw its title only, and a chart past the engine's limits is a title card
# everywhere: those keep the warning.
CHART_PANEL_KINDS = {"chart": "a chart", "graph": "a graph"}
CHART_PANEL_TEMPLATES = ("key_terms", "myth_fact", "worked_steps")
UNDRAWN_PANEL_REASON = "panel_not_drawn"
UNDRAWN_PANEL_MESSAGE = ("This side panel ({what}) is not shown in Dynamic videos yet: the scene is laid out without "
                         "it.")


def undrawn_panel_warnings(timeline: Any, numbers: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """A ``video.render_warnings``-style item for every shown side panel the Dynamic stage leaves out
    (:data:`UNDRAWN_PANEL_KINDS`), in scene order; with ``numbers`` (scene id -> the editor's 1-based position) each
    item also carries ``scene_number``."""
    items: list[dict[str, Any]] = []
    for scene in timeline.scenes:
        sp = scene.side_panel
        if sp is None or not scene.layout.show_side_panel:
            continue
        what = UNDRAWN_PANEL_KINDS.get(sp.panel.kind)
        if what is None and sp.panel.kind in CHART_PANEL_KINDS:
            from ..dynamic.charts import draws_chart  # Dynamic only (this runs for a Dynamic render)

            fmt = timeline.presentation.format if timeline.presentation is not None else "landscape"
            template = scene.look.template if scene.look is not None else None
            layout = (fmt if template == "chart_build" else ("portrait" if fmt == "portrait" else "panel")
                      if template in CHART_PANEL_TEMPLATES else None)
            drawn = layout is not None and draws_chart(sp.panel, layout)
            what = None if drawn else CHART_PANEL_KINDS[sp.panel.kind]
        if what is None:
            continue
        item: dict[str, Any] = {"scene_index": scene.index, "scene_id": scene.scene_id,
                                "title": scene.title or scene.scene_id, "reason": UNDRAWN_PANEL_REASON,
                                "blocking": False, "message": UNDRAWN_PANEL_MESSAGE.format(what=what)}
        if numbers is not None:
            item["scene_number"] = numbers.get(scene.scene_id)
        items.append(item)
    return items


def dynamic_warnings(items: list[dict[str, Any]], timeline: Any = None,
                     numbers: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """``video.render_warnings`` items worded for the motion engine (see ``DYNAMIC_MESSAGES``); with ``timeline``,
    plus :func:`undrawn_panel_warnings` (``numbers`` as there), every item in scene order."""
    out = [{**it, "message": DYNAMIC_MESSAGES[it["reason"]]} if it.get("reason") in DYNAMIC_MESSAGES else it
           for it in items]
    if timeline is None:
        return out
    added = undrawn_panel_warnings(timeline, numbers)
    if not added:
        return out

    def order(it: dict[str, Any]) -> tuple[bool, int]:
        index = it.get("scene_index")
        return (not isinstance(index, int), index if isinstance(index, int) else 0)

    return sorted([*out, *added], key=order)  # stable: the items of one scene keep their order


def caption_modes(payload: dict[str, Any], timeline: Any) -> tuple[bool, bool]:
    """``(burn, soft)`` for a Dynamic render: burned (drawn by the stage) when the request asks for it, else as the
    lecture's presentation says (``captions_burn``); a clean file gets the default-on ``mov_text`` track unless the
    request turns it off (R-31, §7.5)."""
    shown = bool(timeline.captions) or any(s.kinetic_captions for s in timeline.scenes)
    burn_raw = payload.get("burn_captions")
    burn = shown and (bool(burn_raw) if burn_raw is not None else bool(timeline.presentation.captions_burn))
    soft_raw = payload.get("soft_subtitles")
    soft = bool(timeline.captions) and not burn and (soft_raw is None or bool(soft_raw))
    return burn, soft


def served_timeline(timeline: Any, assets: Any, settings: Any) -> dict[str, Any]:
    """The timeline exactly as ``GET /api/render/timeline`` would serve it (URLs filled; blocking)."""
    from ...api.timelines import resolve_timeline

    return resolve_timeline(timeline, assets, settings)


def refresher(timeline: Any, assets: Any, settings: Any, remedies: list[Remedy]) -> Callable[[], dict[str, Any]] | None:
    """With presigned storage URLs (S3 without a CDN; they expire after ``S3_PRESIGN_TTL_SECONDS``), a callable that
    serves the timeline again with fresh signatures and the preflight's remedies, for every stage page the motion
    engine opens; None when URLs do not expire (the served copy stays valid for the whole render)."""
    from ...api.timelines import signed_urls

    if not signed_urls(settings):
        return None
    kept = list(remedies)

    def refresh() -> dict[str, Any]:
        return apply_remedies(served_timeline(timeline, assets, settings), kept)

    return refresh


def _job_error(exc: MotionError | StageError) -> FatalJobError | RetryableJobError:
    message = str(exc)
    if exc.retryable:
        return RetryableJobError(message)
    return FatalJobError(message, code=exc.code)


async def render_dynamic(ctx: JobContext, job: Any, payload: Any, ws: RenderWorkspace) -> dict[str, Any]:
    """Render a Dynamic lecture (``job``: ``video._Job``; ``payload``: ``video.RenderPayload``); returns the job
    result. Raises ``FatalJobError`` / ``RetryableJobError`` (the caller marks the render), ``FFmpegError`` and
    ``JobCancelled``."""
    from ..video import _finish_render, _log_warnings, _mint_token, render_warnings

    settings = ctx.settings
    tl = job.timeline
    if not is_dynamic(tl):
        raise FatalJobError("this lecture is not a Dynamic video", code="bad_payload")
    if not getattr(settings, "dynamic_enabled", False):
        raise FatalJobError("Dynamic videos are switched off on this server (DYNAMIC_ENABLED).",
                            code="dynamic_disabled")
    caps = await asyncio.to_thread(render_capabilities, settings, browser=False)
    if not caps.ok:
        raise FatalJobError("This server cannot render videos: " + " ".join(caps.reasons), code="render_unavailable")
    min_free = float(settings.render_min_free_gb or 0)
    if min_free > 0:
        free = await asyncio.to_thread(free_gb, ws.root)
        if free < min_free:
            raise FatalJobError(f"Not enough free disk space to render ({free:.1f} GB free, {min_free:g} GB needed).",
                                code="disk_full")
    burn, soft = caption_modes(dict(ctx.payload or {}), tl)
    warnings = dynamic_warnings(render_warnings(tl, numbers=job.numbers), tl, numbers=job.numbers)
    _log_warnings(ctx, warnings)
    token = _mint_token(settings, job.version_id, TOKEN_TTL_SECONDS, render_id=job.render_id,
                        include_intro=payload.include_intro)
    served = await asyncio.to_thread(served_timeline, tl, ctx.assets, settings)
    origins = await asyncio.to_thread(media_origins, ctx, settings)

    # --- preflight (§7.12) ---------------------------------------------------------------------------
    ctx.progress("preflight", 0.01, "Checking the lecture before rendering")
    try:
        pre = await run_preflight(settings, tl, token=token, served_timeline=served, media_origins=origins,
                                  check_cancelled=ctx.check_cancelled, captions=burn,
                                  log=lambda line: ctx.log(line, level="info"))
    except StageError as exc:
        raise _job_error(exc) from exc
    for item in pre.items():
        if item["status"] in ("block", "warn"):
            ctx.log(f"Preflight: {item['message']}", level="warning" if item["blocking"] else "info",
                    reason=item["check"])
    if not pre.ok:
        await asyncio.to_thread(record_qa, ctx, job.render_id, pre.as_qa())
        raise_if_blocked(pre, job.numbers)

    # --- capture, join, check ------------------------------------------------------------------------
    output = ws.attempt_dir / "output.mp4"
    try:
        result = await render_motion(ctx, tl, output, settings, burn_captions=burn, soft_subtitles=soft, token=token,
                                     served=pre.timeline or served, work_dir=ws.attempt_dir,
                                     cache=ChunkCache.in_workspace(ws.root), title=job.title,
                                     refresh_served=refresher(tl, ctx.assets, settings, pre.remedies))
    except (MotionError, StageError) as exc:
        raise _job_error(exc) from exc
    except ContainerCheckFailed as exc:  # an engine bug: keep what the checks measured on the render
        await asyncio.to_thread(record_qa, ctx, job.render_id, motion_qa(exc.qa, pre))
        raise
    qa = motion_qa(result.container, pre)
    qa["motion"] = result.summary()
    options: dict[str, Any] = {"qa": qa}
    if warnings:
        options["warnings"] = warnings
    for text in [*result.container.warnings, *result.warnings]:
        ctx.log(f"Video check: {text}", level="warning")
    if result.reused:
        ctx.log(f"Reused {result.reused} of {len(result.chunks)} chunk(s) captured by an earlier attempt", level="info")
    per_worker = ", ".join(f"{w.fps:.1f}" for w in result.worker_stats)
    ctx.log(f"Motion render: {result.frames} frames with {result.workers} worker(s) ({per_worker} fps each) in "
            f"{result.timings.get('total', 0.0):.0f} s.", level="info")
    info = await probe(output, settings=settings)
    out_w, out_h = output_size(tl, settings)
    duration = round(info.duration or result.frames / result.fps, 3)

    # --- store (as the classic path) ------------------------------------------------------------------
    ctx.check_cancelled()
    ctx.progress("store", 0.95, "Saving the video")
    tl_hash = hashlib.sha256(tl.model_dump_json().encode("utf-8")).hexdigest()
    video_key = compute_key("render", {
        "render_id": job.render_id, "version_id": job.version_id, "revision": job.revision, "timeline": tl_hash,
        "engine": ENGINE, "burn": burn, "soft": soft, "w": out_w, "h": out_h, "fps": result.fps,
        "preset": result.encoder.get("preset"), "crf": result.encoder.get("crf"),
        "bgm": bool(settings.render_bgm and tl.branding.bgm_url)})
    srt_text, vtt_text = to_srt(tl.captions), to_vtt(tl.captions)
    srt_bytes, vtt_bytes = srt_text.encode("utf-8"), vtt_text.encode("utf-8")
    srt_key = compute_key("captions", {"render_id": job.render_id, "fmt": "srt",
                                       "sha": hashlib.sha256(srt_bytes).hexdigest()})
    vtt_key = compute_key("captions", {"render_id": job.render_id, "fmt": "vtt",
                                       "sha": hashlib.sha256(vtt_bytes).hexdigest()})
    await asyncio.to_thread(ctx.assets.put, video_key, "render", Produced(
        path=output, mime="video/mp4", duration_s=duration, width=out_w, height=out_h,
        meta={"render_id": job.render_id, "version_id": job.version_id, "engine": ENGINE}))
    await asyncio.to_thread(ctx.assets.put, srt_key, "captions",
                            Produced(data=srt_bytes, mime="application/x-subrip", meta={"render_id": job.render_id}))
    await asyncio.to_thread(ctx.assets.put, vtt_key, "captions",
                            Produced(data=vtt_bytes, mime="text/vtt", meta={"render_id": job.render_id}))
    flush = getattr(ctx, "flush", None)
    if flush is not None:
        await flush()
    await asyncio.to_thread(_finish_render, ctx, job, {
        "video_asset_key": video_key, "srt_asset_key": srt_key, "vtt_asset_key": vtt_key, "duration_s": duration,
        "chapters_text": youtube_chapters(tl), "built_revision": job.revision,
    }, [video_key, srt_key, vtt_key], options)
    ctx.progress("done", 1.0, "Video ready")
    return {"render_id": job.render_id, "video_asset_key": video_key, "srt_asset_key": srt_key,
            "vtt_asset_key": vtt_key, "duration_s": duration, "chapters": len(info.chapters), "engine": ENGINE}
