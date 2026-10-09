"""Cached, sandboxed, self-healing, exactly-timed Manim rendering.

``render_manim(ctx, request)``:

1. cache key = hash(spec, beat times (ms), total duration, target, quality, background, language,
   TEMPLATE_API_VERSION, runtime/template source revision, manim version, LaTeX use, sandbox kind)
   -> ``ctx.assets.get_or_create`` (concurrent callers share one render);
2. templates: validate params -> source (``PARAMS`` literal + guarded static scene file) -> sandbox.
   When LaTeX fails, nothing is stored under the LaTeX key: the template is rendered again with
   plain-text maths under the key of a LaTeX-less render, so a later render with working LaTeX is
   never served the fallback;
   free-form code: guard -> sandbox -> on failure the fast LLM repairs the code (prompts/fix.md with
   the error tail) up to ``MANIM_MAX_REPAIR_ATTEMPTS`` times;
3. ffmpeg conforms the video to exactly ``total_duration`` (freeze last frame / trim);
4. visual QA (``MANIM_VISUAL_QA``, advisory): four frames -> vision model; free-form code gets one
   repair round, and any failure of that round keeps the original video;
5. the asset (kind ``manim``) is stored under the ORIGINAL spec key, with ``final_spec`` in its meta,
   so a spec that needed healing is healed once; when healed, the result is also stored under the key
   of the healed spec.

Errors, log tails and repair prompts have secrets redacted and host paths replaced by placeholders.
Raises :class:`ManimError` (with ``log_tail``) when the spec cannot be rendered.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.metadata
import logging
import math
import shutil
import tempfile
import threading
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..config import Settings
from ..jobs.base import BudgetExceeded, JobCancelled, JobContext
from ..models import Asset
from ..providers.base import ImageInput
from ..schemas.screenplay import ManimSpec
from ..scratch import ensure_scratch_root, scratch_root
from ..storage.assets import Produced, compute_key
from . import guard, media, qa
from . import llm as manim_llm
from .aadhi_scene import assemble_script, build_config, build_hardening, frame_pixels, runtime_source, scene_class_name
from .base import TEMPLATE_API_VERSION, ManimError, ManimRenderRequest, ManimRenderResult
from .prompts import load_prompt, prompt_version
from .sandbox import (
    ManimRunner,
    SandboxResult,
    failed_result,
    get_runner,
    latex_errors,
    log_tail,
    scrub_paths,
    valid_scene_name,
    write_owner_marker,
)
from .templates import get_template, registry, validate_params
from .templates._base import scene_file_source

log = logging.getLogger(__name__)

ASSET_KIND = "manim"
LOG_TAIL_CHARS = 4000
CANCEL_POLL_SECONDS = 0.5
ERROR_PROMPT_CHARS = 6000

_fallback_lock = threading.Lock()
_fallback_semaphores: dict[int, threading.BoundedSemaphore] = {}


class _LatexFallback(Exception):
    """Internal: a template's LaTeX failed; render it again (under its own cache key) without LaTeX."""


# --- cache key ------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def manim_version() -> str:
    """Installed Manim version (without importing Manim)."""
    try:
        return importlib.metadata.version("manim")
    except importlib.metadata.PackageNotFoundError:  # pragma: no cover - manim is a dependency
        return "unknown"


@lru_cache(maxsize=64)
def source_revision(template: str | None) -> str:
    """Short hash of the runtime (+ template scene file): changes invalidate cached renders."""
    h = hashlib.sha256(runtime_source().encode("utf-8"))
    if template and template in registry:
        h.update(scene_file_source(registry[template].scene_file).encode("utf-8"))  # type: ignore[attr-defined]
    return h.hexdigest()[:16]


def cache_inputs(req: ManimRenderRequest, *, has_latex: bool, sandbox: str, spec: ManimSpec | None = None) -> dict[str, Any]:
    """Everything that influences the rendered pixels."""
    spec = spec or req.spec
    return {
        "spec": spec.model_dump(mode="json"),
        "beat_times": [round(float(t), 3) for t in req.beat_times],
        "total_duration": round(float(req.total_duration), 3),
        "target": req.target,
        "quality": req.quality,
        "background": req.background,
        "language": req.language,
        "template_api": TEMPLATE_API_VERSION,
        "runtime_rev": source_revision(spec.template),
        "manim": manim_version(),
        "latex": bool(has_latex),
        "sandbox": sandbox,
    }


def cache_key(req: ManimRenderRequest, *, has_latex: bool, sandbox: str, spec: ManimSpec | None = None) -> str:
    """Content address of the render."""
    return compute_key(ASSET_KIND, cache_inputs(req, has_latex=has_latex, sandbox=sandbox, spec=spec))


def _warm_caches() -> None:
    """Read the runtime, template scene files and prompts once, at import time.

    Every later lookup is served from memory, so ``render_manim`` does no file I/O on the event loop.
    """
    with contextlib.suppress(OSError, ValueError):
        manim_version()
        for template in (None, *registry):
            source_revision(template)
        for prompt in ("fix.md", "qa.md", "freeform_rules.md"):
            load_prompt(prompt)
            prompt_version(prompt)


_warm_caches()


# --- concurrency ----------------------------------------------------------------------------------


@asynccontextmanager
async def manim_slot(settings: Settings) -> AsyncIterator[None]:
    """Process-wide Manim concurrency limit (``aadhi.jobs.limits`` when available)."""
    try:
        from ..jobs.limits import acquire
    except ImportError:
        acquire = None
    if acquire is not None:
        async with acquire("manim"):
            yield
        return
    limit = max(1, int(settings.manim_max_concurrent))
    with _fallback_lock:
        sem = _fallback_semaphores.setdefault(limit, threading.BoundedSemaphore(limit))
    # A threading semaphore is shared by every worker loop/thread; poll it (a blocking acquire in a
    # worker thread could leak the slot if this task is cancelled while waiting).
    while not sem.acquire(blocking=False):  # noqa: ASYNC110
        await asyncio.sleep(0.1)
    try:
        yield
    finally:
        sem.release()


# --- outcome --------------------------------------------------------------------------------------


@dataclass
class _Outcome:
    video: Path
    info: media.VideoInfo
    final_spec: ManimSpec
    healed: bool = False
    qa_issues: list[str] = field(default_factory=list)
    log_tail: str = ""
    attempts: int = 1
    has_latex: bool = True
    seconds: float = 0.0
    peak_memory_mb: float = 0.0
    frames: int = 0

    def meta(self, key: str, req: ManimRenderRequest, runner: ManimRunner) -> dict[str, Any]:
        return {
            "spec_key": key,
            "final_spec": self.final_spec.model_dump(mode="json"),
            "healed": self.healed,
            "qa_issues": self.qa_issues,
            "log_tail": self.log_tail,
            "template": self.final_spec.template,
            "attempts": self.attempts,
            "latex": self.has_latex,
            "target": req.target,
            "quality": req.quality,
            "sandbox": runner.name,
            "manim": manim_version(),
            "render_seconds": round(self.seconds, 2),
            "peak_memory_mb": round(self.peak_memory_mb, 1),
            "frames": int(self.frames),
            "prompt_version": prompt_version("fix.md") if self.healed else "",
        }


def _clean(settings: Settings, text: str, workdir: Path | None = None) -> str:
    """``text`` with secrets redacted and host paths replaced (``<work>``, ``<scratch>``, ``<tmp>``, ``<python>``,
    ``<home>``; the scratch root covers a custom ``SCRATCH_DIR`` outside the temp dir)."""
    if not text:
        return ""
    roots: list[tuple[Path | None, str]] = [(workdir, "<work>")] if workdir else []
    roots.append((scratch_root(settings), "<scratch>"))
    return settings.redact(scrub_paths(text, roots))


def _tail(settings: Settings, text: str, workdir: Path | None = None, chars: int = LOG_TAIL_CHARS) -> str:
    return _clean(settings, text, workdir)[-chars:] if text else ""


async def _run(
    ctx: JobContext,
    runner: ManimRunner,
    req: ManimRenderRequest,
    source: str,
    *,
    has_latex: bool,
    workdir: Path,
) -> SandboxResult:
    scene = scene_class_name(source)
    if not scene:
        return failed_result("no class deriving from AadhiScene found")
    if not valid_scene_name(scene):
        return failed_result(f"scene class name {scene!r} is not allowed: use ASCII letters, digits and "
                             "underscores only, starting with a letter (at most 64 characters)")
    cfg = build_config(
        beat_times=req.beat_times,
        total_duration=req.total_duration,
        target=req.target,
        language=req.language,
        has_latex=has_latex,
        background=req.background,
        quality=req.quality,
    )
    hardening = build_hardening(
        total_duration=req.total_duration,
        audit_hook=ctx.settings.manim_audit_hook,
        has_latex=has_latex,
    )
    script = assemble_script(source, cfg, hardening)
    ctx.check_cancelled()
    async with manim_slot(ctx.settings):
        ctx.check_cancelled()
        try:
            render = runner.run(script, scene, workdir=workdir, quality=req.quality,
                                timeout=float(ctx.settings.manim_timeout_seconds))
            return await _cancellable(ctx, render)
        except ValueError as exc:  # a runner refused its arguments: treat it as a failed render
            return failed_result(str(exc))


async def _cancellable(ctx: JobContext, awaitable: Awaitable[SandboxResult]) -> SandboxResult:
    """Await a render while polling ``ctx.check_cancelled()``; a cancelled job kills the render.

    Cancelling the runner task makes the runner kill the process tree (or container) right away
    instead of letting it run to completion or to ``MANIM_TIMEOUT_SECONDS``. This waits until the
    runner has finished, so the Manim slot is released only once the process is gone.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=CANCEL_POLL_SECONDS)
            if done:
                return task.result()
            ctx.check_cancelled()
    except BaseException:
        task.cancel()
        while not task.done():
            with contextlib.suppress(BaseException):
                await asyncio.wait({task})
        if not task.cancelled():
            task.exception()  # retrieved: the original error is what matters
        raise


def _fix_prompt(req: ManimRenderRequest, code: str, error: str, settings: Settings) -> str:
    shape = "a portrait side panel (4:5, self.IS_PANEL is True)" if req.target == "panel" else "a full 16:9 frame"
    beats = []
    for i, t in enumerate(req.beat_times):
        cue = req.beat_cues[i] if i < len(req.beat_cues) and req.beat_cues[i] else ""
        beats.append(f"- beat {i} starts at {t:.2f} s" + (f": {cue}" if cue else ""))
    parts = [
        f"Scene title: {req.title or '(none)'}",
        f"The animation fills {shape}, lasts {req.total_duration:.1f} s and has {len(req.beat_times)} beat(s):",
        *(beats or ["- (no beats: spread the steps evenly)"]),
    ]
    if req.narration_context:
        parts.append("Narration (context): " + req.narration_context[:1500])
    parts += [
        "",
        "Current code:",
        "```python",
        code,
        "```",
        "",
        "Problem to fix:",
        _clean(settings, error)[-ERROR_PROMPT_CHARS:],
    ]
    return "\n".join(parts)


async def _repair(
    ctx: JobContext,
    req: ManimRenderRequest,
    code: str,
    error: str,
    *,
    images: list[bytes] | None = None,
) -> str:
    """Ask the fast LLM for corrected code (validated by the guard inside the re-ask loop)."""
    settings = ctx.settings
    n_beats = len(req.beat_times)

    def validate(fix: Any) -> list[str]:
        code_text = manim_llm.strip_code_fences(fix.code)
        return guard.check_code(code_text) + guard.check_timing(code_text, n_beats)

    provider = manim_llm.get_llm(settings, req.llm_provider)
    fix = await provider.generate_json(
        model=manim_llm.fast_model(settings, req.llm_provider),
        system=load_prompt("fix.md"),
        prompt=_fix_prompt(req, code, error, settings),
        schema=manim_llm.ManimCodeFix,
        images=[ImageInput(data=b, mime="image/png") for b in images or []],
        temperature=0.2,
        on_usage=ctx.record_usage,
        validate=validate,
        validation_retries=1,
    )
    return manim_llm.strip_code_fences(fix.code)


def _summary(text: str) -> str:
    rows = [r for r in text.strip().splitlines() if r.strip()]
    return rows[-1][:300] if rows else "unknown error"


async def _render_template(
    ctx: JobContext, req: ManimRenderRequest, runner: ManimRunner, has_latex: bool, workdir: Path
) -> tuple[SandboxResult, ManimSpec]:
    """Render a template. Raises :class:`_LatexFallback` when LaTeX failed (only when ``has_latex``)."""
    spec = req.spec
    template = get_template(spec.template or "")
    model, problems = validate_params(spec.template or "", spec.params)
    if model is None:
        raise ManimError("invalid template params: " + "; ".join(problems), category="invalid_request")
    steps = template.step_count(model)
    if req.beat_times and steps != len(req.beat_times):
        ctx.log(
            f"Manim template {spec.template} has {steps} steps but the scene has {len(req.beat_times)} beats",
            "warning",
        )
    source = template.render_source(model, target=req.target, language=req.language)
    problems = guard.check_template_source(source)
    if problems:  # the static scene file is tested against the guard; PARAMS is validated data
        raise ManimError(f"template {spec.template!r} source failed the safety check: " + "; ".join(problems),
                         category="unsafe_code")
    result = await _run(ctx, runner, req, source, has_latex=has_latex, workdir=workdir / "t0")
    if not result.ok and has_latex and not result.killed_reason:
        tex = latex_errors(result.log + "\n" + result.error_report)
        if tex:
            raise _LatexFallback(tex)
    if not result.ok:
        raise ManimError(
            f"Manim template {spec.template!r} failed: {_summary(_clean(ctx.settings, result.error_report, workdir))}",
            log_tail=_tail(ctx.settings, result.log, workdir),
            category=result.category,
        )
    return result, ManimSpec(template=spec.template, params=spec.params)


async def _render_freeform(
    ctx: JobContext, req: ManimRenderRequest, runner: ManimRunner, has_latex: bool, workdir: Path
) -> tuple[SandboxResult, str, int]:
    """Guard -> render -> LLM repair loop. Returns (result, final code, attempts)."""
    settings = ctx.settings
    code = req.spec.code or ""
    max_repairs = max(0, int(settings.manim_max_repair_attempts))
    n_beats = len(req.beat_times)
    last_log = ""
    category = "render_failed"
    for attempt in range(max_repairs + 1):
        ctx.check_cancelled()
        problems = guard.check_code(code)
        timing = guard.check_timing(code, n_beats) if not problems else []
        if problems:
            category = "unsafe_code"
            error = "The code was rejected by the safety checker:\n" + "\n".join(f"- {p}" for p in problems)
        elif timing and attempt < max_repairs:
            error = "\n".join(timing)
        else:
            result = await _run(ctx, runner, req, code, has_latex=has_latex, workdir=workdir / f"f{attempt}")
            if result.ok:
                return result, code, attempt + 1
            last_log = result.log
            error = result.error_report
            category = result.category
        error = _clean(settings, error, workdir)
        if attempt >= max_repairs:
            raise ManimError(
                f"Manim code failed after {max_repairs} repair attempt(s): {_summary(error)}",
                log_tail=_tail(settings, last_log or error, workdir),
                category=category,
            )
        ctx.log(f"Manim code failed; asking the model to repair it (attempt {attempt + 1}/{max_repairs})", "warning",
                error=error[-500:])
        try:
            code = await _repair(ctx, req, code, error)
        except (BudgetExceeded, JobCancelled):
            raise
        except Exception as exc:
            raise ManimError(
                f"Manim repair failed: {_clean(settings, str(exc))[:300]}",
                log_tail=_tail(settings, last_log or error, workdir),
                category="repair_failed",
            ) from exc
    raise ManimError("unreachable")  # pragma: no cover


async def _qa_round(
    ctx: JobContext,
    req: ManimRenderRequest,
    runner: ManimRunner,
    code: str,
    video: Path,
    info: media.VideoInfo,
    has_latex: bool,
    workdir: Path,
) -> tuple[Path, media.VideoInfo, str, list[str], bool]:
    """Visual QA; free-form code gets one repair round. Returns (video, info, code, issues, repaired).

    QA is advisory: when the repair round fails in any way (model, guard, render, ffmpeg), the
    original video is kept. Budget and cancellation errors propagate.
    """
    issues = await qa.review_frames(ctx, video, req)
    if not issues or not req.spec.code:
        return video, info, code, issues, False
    ctx.log(f"Manim visual QA found {len(issues)} issue(s); repairing once", "warning")
    try:
        frames = await media.extract_frames(video, qa.frame_times(req, info.duration, fps=info.fps), ctx.settings,
                                            max_width=640)
        fixed = await _repair(ctx, req, code, "Visual QA found layout problems:\n" + "\n".join(f"- {i}" for i in issues),
                              images=frames)
        if guard.check_code(fixed):
            return video, info, code, issues, False
        result = await _run(ctx, runner, req, fixed, has_latex=has_latex, workdir=workdir / "qa")
        if not result.ok or result.video_path is None:
            ctx.log("Manim QA repair did not render; keeping the original video", "warning")
            return video, info, code, issues, False
        conformed = workdir / "final_qa.mp4"
        new_info = await media.conform(result.video_path, conformed, req.total_duration, ctx.settings)
        remaining = await qa.review_frames(ctx, conformed, req)
    except (BudgetExceeded, JobCancelled):
        raise
    except Exception as exc:
        ctx.log(f"Manim QA repair failed; keeping the original video: {_clean(ctx.settings, str(exc), workdir)[:200]}",
                "warning")
        return video, info, code, issues, False
    return conformed, new_info, fixed, remaining, True


async def _produce(
    ctx: JobContext, req: ManimRenderRequest, runner: ManimRunner, has_latex: bool, workdir: Path,
    *, latex_fallback: bool = False,
) -> _Outcome:
    started = time.monotonic()
    settings = ctx.settings
    if not has_latex and not latex_fallback:
        ctx.log("LaTeX is not available in the Manim sandbox: formulas are drawn as plain text", "warning")
    if req.spec.template:
        result, final_spec = await _render_template(ctx, req, runner, has_latex, workdir)
        code, attempts, healed = "", 1, False
    else:
        result, code, attempts = await _render_freeform(ctx, req, runner, has_latex, workdir)
        final_spec, healed = ManimSpec(code=code), attempts > 1
    assert result.video_path is not None
    final = workdir / "final.mp4"
    try:
        info = await media.conform(result.video_path, final, req.total_duration, settings)
    except media.MediaError as exc:
        raise ManimError(f"post-processing the Manim video failed: {_clean(settings, str(exc), workdir)}",
                         category="invalid_output") from exc
    _check_output(info, req)
    issues: list[str] = []
    if settings.manim_visual_qa:
        final, info, new_code, issues, repaired = await _qa_round(ctx, req, runner, code, final, info, has_latex, workdir)
        if repaired:
            final_spec, healed = ManimSpec(code=new_code), True
    return _Outcome(
        video=final,
        info=info,
        final_spec=final_spec,
        healed=healed,
        qa_issues=issues,
        log_tail=_tail(settings, log_tail(result.log, 30), workdir, 1500),
        attempts=attempts,
        has_latex=has_latex,
        seconds=time.monotonic() - started,
        peak_memory_mb=result.peak_memory_mb,
        frames=result.frames,
    )


def _check_output(info: media.VideoInfo, req: ManimRenderRequest) -> None:
    """Reject a conformed video whose pixel size is not the exact one the profile asked for."""
    expected = frame_pixels(req.target, req.quality)
    if (info.width, info.height) != expected:
        raise ManimError(
            f"the rendered video is {info.width}x{info.height}, not the expected {expected[0]}x{expected[1]}",
            category="invalid_output",
        )


def _result(asset: Asset, *, cached: bool, req: ManimRenderRequest) -> ManimRenderResult:
    meta = asset.meta or {}
    try:
        final_spec = ManimSpec.model_validate(meta.get("final_spec") or req.spec.model_dump(mode="json"))
    except ValueError:  # pragma: no cover - corrupted meta
        final_spec = req.spec
    return ManimRenderResult(
        asset_key=asset.key,
        storage_key=asset.storage_key,
        duration=float(asset.duration_s or req.total_duration),
        width=int(asset.width or 0),
        height=int(asset.height or 0),
        cached=cached,
        healed=bool(meta.get("healed", False)),
        final_spec=final_spec,
        qa_issues=list(meta.get("qa_issues") or []),
        log_tail=str(meta.get("log_tail") or ""),
    )


async def _cached_render(
    ctx: JobContext, req: ManimRenderRequest, runner: ManimRunner, *, has_latex: bool, workdir: Path,
    latex_fallback: bool = False,
) -> tuple[Asset, bool, _Outcome | None]:
    """``get_or_create`` under the key of a render with/without LaTeX. Returns (asset, created, outcome)."""
    key = cache_key(req, has_latex=has_latex, sandbox=runner.name)
    holder: dict[str, _Outcome] = {}

    async def producer() -> Produced:
        outcome = await _produce(ctx, req, runner, has_latex, workdir, latex_fallback=latex_fallback)
        holder["outcome"] = outcome
        return Produced(
            path=outcome.video,
            mime="video/mp4",
            duration_s=outcome.info.duration,
            width=outcome.info.width,
            height=outcome.info.height,
            meta=outcome.meta(key, req, runner),
        )

    asset, created = await ctx.assets.get_or_create(key, ASSET_KIND, producer, created_by=ctx.user_id)
    return asset, created, holder.get("outcome")


def _check_request(req: ManimRenderRequest) -> None:
    if not (math.isfinite(req.total_duration) and req.total_duration > 0):
        raise ManimError("total_duration must be positive and finite", category="invalid_request")
    if not all(math.isfinite(t) and round(t, 3) >= 0 for t in req.beat_times):
        raise ManimError("beat_times must be finite and non-negative", category="invalid_request")


def _new_workdir(settings: Settings) -> Path:
    """A private work dir inside the scratch root (``SCRATCH_DIR``), where the orphan sweep looks (blocking)."""
    return Path(tempfile.mkdtemp(prefix="aadhi-manim-", dir=ensure_scratch_root(settings)))


async def render_manim(ctx: JobContext, req: ManimRenderRequest) -> ManimRenderResult:
    """Render ``req`` (cached by content). Raises :class:`ManimError` when it cannot be rendered."""
    settings = ctx.settings
    runner = get_runner(settings)
    if not req.spec.template:
        if not settings.manim_allow_freeform:
            raise ManimError("free-form Manim code is disabled (MANIM_ALLOW_FREEFORM=false)", category="disabled")
        if settings.is_production and runner.name != "docker":
            raise ManimError("free-form Manim code requires MANIM_SANDBOX=docker in production", category="disabled")
    elif req.spec.template not in registry:
        raise ManimError(f"unknown manim template {req.spec.template!r}", category="invalid_request")
    _check_request(req)
    has_latex = await asyncio.to_thread(runner.has_latex)
    workdir = await asyncio.to_thread(_new_workdir, settings)
    await asyncio.to_thread(write_owner_marker, workdir)
    try:
        try:
            asset, created, outcome = await _cached_render(ctx, req, runner, has_latex=has_latex, workdir=workdir / "a")
        except _LatexFallback as exc:
            ctx.log("LaTeX failed in a Manim template; rendering it with plain-text maths", "warning",
                    error=_clean(settings, str(exc), workdir)[-500:])
            asset, created, outcome = await _cached_render(ctx, req, runner, has_latex=False, workdir=workdir / "b",
                                                           latex_fallback=True)
        if created and outcome is not None and outcome.healed:
            healed_key = cache_key(req, has_latex=outcome.has_latex, sandbox=runner.name, spec=outcome.final_spec)
            if healed_key != asset.key:
                meta = dict(outcome.meta(healed_key, req, runner), healed_from=asset.key)
                produced = Produced(path=outcome.video, mime="video/mp4", duration_s=outcome.info.duration,
                                    width=outcome.info.width, height=outcome.info.height, meta=meta)
                await asyncio.to_thread(ctx.assets.put, healed_key, ASSET_KIND, produced, created_by=ctx.user_id)
    finally:
        await asyncio.to_thread(shutil.rmtree, workdir, True)
    return _result(asset, cached=not created, req=req)
