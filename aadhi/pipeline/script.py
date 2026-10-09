"""Stage 3 — script: one structured call per planned scene (in parallel) -> ``Screenplay``.

Each scene family has its own prompt (``prompts/scene_*.md`` + ``style.md``) and response model
(``gen_models``). Validation inside the re-ask loop = canonicalisation problems (ids, blanks,
highlights, refs, figures, schema limits) + family checks (template step count, board size,
narration length, mascot-free video prompts, p5 sketch shape) + pedagogy checks from
``scene_checks`` (language, redundancy, quiz option length, faded worked examples, visuals referred
to). Quality checks are reported on the first pass only; later passes accept anything lenient
canonicalisation can turn into a valid scene. A scene that still fails — for any reason other than
job-level conditions (cancellation, budget, rate limits, a provider refusing the job owner's personal
API key) — becomes a deterministic fallback board scene plus an error issue, so one bad scene never fails the lecture (validator exceptions become
problems for the model). Before any scene is written the plan is clamped to the Screenplay limits
(``plan_rules.fit_plan``) and a source the model cannot see is refused (``plan.require_grounding``).
An optional per-scene checkpoint hook (``write_scenes_detailed(checkpoints=...)``, see
``checkpoint.SceneCheckpoints``) lets a retried job reuse the scenes its earlier attempt already wrote.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from ..credentials import personal_key_rejection
from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import FileInput, RateLimited
from ..schemas.screenplay import (
    ChapterRef,
    LexiconEntry,
    ManimSpec,
    Screenplay,
    SourceInfo,
)
from . import integrations, validate
from .aio import gather_all
from .base import ConceptBrief, GenerationOptions, IngestResult, Issue, LecturePlan
from .canonicalize import (
    SCENE_ADAPTER,
    CanonicalizationError,
    canonicalize_scene,
    child_id,
    format_validation_error,
    intent_of,
    scene_problems,
)
from .gen_models import BOARD_SCENE_TYPES, model_for_scene, scene_family
from .plan import original_attachment, require_grounding, scene_source_note, skipped_ids
from .plan_rules import CHAPTER_TITLE_MAX, META_MAX, fit_plan
from .prompting import join_sections, section, system_prompt
from .scene_checks import pedagogy_problems
from .scene_context import LectureContext, ScenePosition, positions, scene_prompt_sections
from .timing import word_count

log = logging.getLogger(__name__)

FAMILY_PROMPTS = {
    "board": "scene_board",
    "chapter": "scene_chapter",
    "quiz": "scene_quiz",
    "simulation": "scene_simulation",
    "ai_video": "scene_ai_video",
    "interactive": "scene_interactive",
}
MAX_BOARD_ITEMS = validate.MAX_BOARD_ITEMS
MAX_BEAT_WORDS = validate.BEAT_MAX_WORDS  # same limits as lint, so accepted scenes lint clean
_PASSTHROUGH = (JobCancelled, BudgetExceeded, RateLimited, asyncio.CancelledError)
FALLBACK_NOTE = "Generated from the plan because scene writing failed; regenerate this scene."
MAX_MANIM_CODE = ManimSpec.model_fields["code"].metadata[0].max_length  # 20000
_MASCOT_RE = re.compile(r"\b(aadhi|mascot|blackbuck|black buck|antelope|deer)\b", re.I)
_P5_NETWORK_RE = re.compile(r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|importScripts|loadJSON|loadStrings|httpGet|httpPost)\b")


def script_model(options: GenerationOptions, settings: Any) -> str:
    """The lecture engine's scene-writing model (an admin's ``llm_model_script`` override for that engine wins)."""
    return integrations.llm_model(settings, "script", options, override=options.llm_model_script)


@dataclass
class SceneWriteResult:
    scene: Any
    issues: list[Issue] = field(default_factory=list)
    fallback: bool = False


class SceneCheckpointHook(Protocol):
    """Per-scene checkpoints (``checkpoint.SceneCheckpoints``): ``attached`` = the original file went to the
    scene writers. ``load`` returns what ``save`` stored, or None; neither raises."""

    async def load(self, scene_id: str, attached: bool) -> dict[str, Any] | None: ...

    async def save(self, scene_id: str, attached: bool, data: dict[str, Any]) -> None: ...


def scene_result_data(result: SceneWriteResult) -> dict[str, Any]:
    """JSON-safe form of a written (not fallback) scene, for a checkpoint."""
    return {"scene": result.scene.model_dump(mode="json"), "issues": [i.model_dump(mode="json") for i in result.issues]}


def scene_result_from(data: dict[str, Any] | None, scene_id: str) -> SceneWriteResult | None:
    """The scene stored by ``scene_result_data`` (None when missing, malformed or for another scene)."""
    if not isinstance(data, dict):
        return None
    try:
        scene = SCENE_ADAPTER.validate_python(data["scene"])
        issues = [Issue.model_validate(i) for i in data.get("issues") or []]
    except (KeyError, TypeError, ValidationError):
        return None
    return SceneWriteResult(scene, issues) if getattr(scene, "id", None) == scene_id else None


@dataclass
class ScriptResult:
    screenplay: Screenplay
    issues: list[Issue] = field(default_factory=list)
    fallback_scene_ids: list[str] = field(default_factory=list)
    plan: LecturePlan | None = None  # the plan actually written (clamped to the Screenplay limits)
    source_attached: bool = False  # the original file went to the scene writers


def canon_kwargs(lc: LectureContext, pos: ScenePosition) -> dict[str, Any]:
    return {
        "chapter_id": pos.chapter_id,
        "chapter_label": f"Part {pos.chapter_number}",
        "known_misconceptions": lc.misconception_ids,
        "known_figures": lc.figure_ids,
        "known_chunks": lc.chunk_ids or None,
    }


def template_step_problem(planned: Any, out: BaseModel) -> str | None:
    """Template animations advance one step per beat: simulations over all beats, Manim side panels
    over the beats from ``show_from_beat`` on."""
    if not planned.manim_template:
        return None
    beats = len(getattr(out, "beats", None) or [])
    if planned.type == "simulation":
        params, where, n = getattr(out, "params", None), "", beats
    else:
        panel = getattr(out, "side_panel", None)
        params = getattr(panel, "manim_params", None) if panel is not None else None
        start = getattr(panel, "show_from_beat", None) or 1
        n = beats - (min(max(start, 1), beats) - 1) if beats else 0
        where = f" from beat {start}" if start > 1 else ""
    if params is None:
        return None
    dumped = params.model_dump(mode="json") if isinstance(params, BaseModel) else dict(params)
    steps = integrations.template_step_count(planned.manim_template, dumped)
    if steps is not None and steps != n:
        return (f"the '{planned.manim_template}' animation has {steps} steps with these params but it plays during "
                f"{n} beats{where}: make them equal (one animation step per beat)")
    return None


def family_problems(planned: Any, out: BaseModel) -> list[str]:
    """Quality checks beyond canonicalisation (reported on the first validation pass)."""
    problems: list[str] = []
    beats = list(getattr(out, "beats", None) or [])
    if hasattr(out, "question_beats"):
        beats = list(out.question_beats) + list(out.reveal_beats)
        if not 2 <= len(out.distractors) <= 3:
            problems.append(f"give 2 or 3 distractors (got {len(out.distractors)})")
    for n, b in enumerate(beats, 1):
        words = word_count(b.narration)
        if words > MAX_BEAT_WORDS:
            problems.append(f"beat {n}: {words} words is too long for one beat; split it (one idea per beat, 1-3 sentences)")
    if scene_family(planned.type) == "board":
        n_items = sum(1 for b in beats if b.board is not None)
        if n_items > MAX_BOARD_ITEMS:
            problems.append(f"the board has {n_items} items; keep it to 5 (at most {MAX_BOARD_ITEMS})")
    if planned.type == "ai_video":
        if _MASCOT_RE.search(out.video_prompt or ""):
            problems.append("video_prompt must not show the mascot or any animal character; describe real-world footage only")
    if planned.type == "interactive":
        code = out.p5_code or ""
        if not re.search(r"\bsetup\s*\(|setup\s*=", code):
            problems.append("p5_code must define setup() (and usually draw())")
        if _P5_NETWORK_RE.search(code):
            problems.append("p5_code must not use the network (fetch, XMLHttpRequest, loadJSON …)")
    return problems


def code_problems(planned: Any, out: BaseModel) -> list[str]:
    """Free-form Manim code checks (AST guard + one ``wait_until_beat`` per beat) from the manim area.

    Never raises: over-long or otherwise invalid code is reported as a problem for the model.
    """
    code = getattr(out, "code", None)
    if planned.type != "simulation" or not code:
        return []
    if len(code) > MAX_MANIM_CODE:
        return [f"code: the animation code is {len(code)} characters; keep it under {MAX_MANIM_CODE}"]
    try:
        spec = ManimSpec(code=code)
    except ValidationError as exc:
        return [f"code: {p}" for p in format_validation_error(exc, 3)]
    problems = integrations.manim_spec_problems(spec, len(getattr(out, "beats", [])))
    if problems is None:
        problems = integrations.check_manim_code(code)
    return [f"code: {p.partition(': ')[2] or p}" for p in problems]


def make_scene_validator(lc: LectureContext, pos: ScenePosition):
    """First pass: every problem; later passes: only what lenient canonicalisation cannot fix.

    An exception inside a check becomes a problem (the model is re-asked; after the retries the
    scene falls back) instead of escaping ``generate_json`` and failing the whole lecture.
    """
    planned = pos.planned
    kwargs = canon_kwargs(lc, pos)
    calls = {"n": 0}

    def validate(out: BaseModel) -> list[str]:
        try:
            return checks(out)
        except _PASSTHROUGH:
            raise
        except ValidationError as exc:
            return format_validation_error(exc, 4)
        except Exception as exc:  # noqa: BLE001 - a checker bug must not fail the lecture
            log.warning("scene %s: validation check raised %s", planned.id, type(exc).__name__, exc_info=True)
            return [f"the answer could not be checked ({type(exc).__name__}: {str(exc)[:200]}); simplify it"]

    def checks(out: BaseModel) -> list[str]:
        calls["n"] += 1
        hard: list[str] = []
        step = template_step_problem(planned, out)
        if step and planned.type == "simulation":  # the scene IS the animation: must match
            hard.append(step)
        elif step and calls["n"] == 1:  # a side panel: asked once, then left to lint + repair
            hard.append(step)
        if calls["n"] == 1:
            o = lc.options
            quality = pedagogy_problems(planned, out, language=o.language, board_language=o.board_language, depth=o.depth)
            return (scene_problems(planned, out, **kwargs) + family_problems(planned, out) + code_problems(planned, out)
                    + quality + hard)
        try:
            canonicalize_scene(planned, out, strict=False, **kwargs)
        except CanonicalizationError as exc:
            hard.extend(exc.problems)
        return hard

    return validate


def fallback_scene(lc: LectureContext, pos: ScenePosition) -> Any:
    """Deterministic, valid scene from the plan alone (used when generation fails)."""
    s = pos.planned
    if s.type == "chapter_card":
        return SCENE_ADAPTER.validate_python({
            "id": s.id, "type": "chapter_card", "chapter_id": pos.chapter_id, "concept_id": s.concept_id,
            "title": pos.chapter_title[:240] or s.goal[:120], "chapter_label": f"Part {pos.chapter_number}",
            "mascot_position": "center", "beats": [], "objective_ids": list(s.objective_ids),
            "intent": intent_of(s),
        })
    points = [p.strip() for p in (s.key_points or [s.goal]) if p.strip()][:5] or [s.goal]
    beats, board = [], []
    for n, point in enumerate(points, 1):
        text = re.sub(r"[*`$\[\]{}\\#<>]", "", point)[:300] or "Key idea"
        item_id = child_id(s.id, "i", n)
        board.append({"id": item_id, "kind": "bullet", "text": text[:140]})
        beats.append({"id": child_id(s.id, "b", n), "narration": text, "board_item_id": item_id, "source_refs": []})
    concepts = {c.id: c.title for c in lc.plan.concept_map}
    return SCENE_ADAPTER.validate_python({
        "id": s.id, "type": s.type if s.type in BOARD_SCENE_TYPES else "content",
        "concept_id": s.concept_id, "chapter_id": pos.chapter_id,
        "title": (concepts.get(s.concept_id or "", "") or s.goal.split(".")[0])[:120],
        "beats": beats, "board": board, "objective_ids": list(s.objective_ids),
        "intent": intent_of(s),
        "notes": FALLBACK_NOTE,
    })


def _redact(ctx: Any, text: str) -> str:
    return ctx.settings.redact(text)[:400]


def _failed(ctx: Any, lc: LectureContext, pos: ScenePosition, message: str) -> SceneWriteResult:
    return SceneWriteResult(
        fallback_scene(lc, pos),
        [Issue(code="scene.generation_failed", severity="error", scene_id=pos.planned.id, source="system",
               message=message)],
        fallback=True,
    )


async def write_scene(
    ctx: Any,
    lc: LectureContext,
    pos: ScenePosition,
    *,
    llm: Any | None = None,
    extra_sections: list[str] | None = None,
    task: str | None = None,
    prompt_names: tuple[str, ...] | None = None,
    files: Sequence[FileInput] = (),
) -> SceneWriteResult:
    """Write one scene (structured call + canonicalisation).

    Only job-level conditions propagate (cancellation, budget, rate limits); every other failure —
    provider errors, invalid output after the retries, unexpected exceptions — yields the plan-only
    fallback scene plus a ``scene.generation_failed`` issue. A provider refusing the job owner's
    personal API key (HTTP 401/403, Gemini's invalid-key 400) propagates too: every scene would fail
    the same way, and the job fails as ``personal_key_rejected``.
    """
    try:
        return await _write_scene(ctx, lc, pos, llm=llm, extra_sections=extra_sections, task=task,
                                  prompt_names=prompt_names, files=files)
    except _PASSTHROUGH:
        raise
    except Exception as exc:  # noqa: BLE001 - one scene must never fail (and re-run) the whole lecture
        if personal_key_rejection(exc, getattr(ctx, "key_sources", None) or {}):
            raise  # the user's own key was refused: never hide it behind fallback scenes
        msg = _redact(ctx, f"{type(exc).__name__}: {exc}")
        log.warning("scene %s generation failed: %s", pos.planned.id, msg)
        return _failed(ctx, lc, pos, f"Writing this scene failed ({msg}); a plain fallback from the plan is shown. "
                                     "Regenerate it.")


async def _write_scene(
    ctx: Any,
    lc: LectureContext,
    pos: ScenePosition,
    *,
    llm: Any | None,
    extra_sections: list[str] | None,
    task: str | None,
    prompt_names: tuple[str, ...] | None,
    files: Sequence[FileInput],
) -> SceneWriteResult:
    planned = pos.planned
    family = scene_family(planned.type)
    llm = llm or integrations.get_llm(ctx.settings, integrations.llm_engine(lc.options, ctx.settings))
    schema = model_for_scene(planned.type, planned.manim_template)
    names = prompt_names or ("style", FAMILY_PROMPTS[family])
    note = scene_source_note(lc.ingest, bool(files))
    prompt = join_sections(
        *scene_prompt_sections(lc, pos),
        section("About the source", note) if note else "",
        *(extra_sections or []),
        section("Task", task or "Write this scene. Respond with JSON that matches the response schema."),
    )
    kwargs = canon_kwargs(lc, pos)
    async with integrations.limit("llm"):
        out = await llm.generate_json(
            model=script_model(lc.options, ctx.settings),
            system=system_prompt(*names),
            prompt=prompt,
            schema=schema,
            files=files,
            temperature=0.6,
            on_usage=ctx.record_usage,
            validate=make_scene_validator(lc, pos),
            validation_retries=2,
        )
    issues: list[Issue] = []
    try:
        scene = canonicalize_scene(planned, out, strict=True, **kwargs)
    except CanonicalizationError as strict_exc:
        try:
            scene = canonicalize_scene(planned, out, strict=False, **kwargs)
            issues.append(Issue(code="scene.autofixed", severity="info", scene_id=planned.id, source="system",
                                message="Some references were dropped automatically: " + "; ".join(strict_exc.problems[:3])))
        except CanonicalizationError as exc:
            return _failed(ctx, lc, pos, "The generated scene was invalid (" + "; ".join(exc.problems[:3])[:300]
                           + "); a plain fallback from the plan is shown.")
    return SceneWriteResult(scene, issues)


def assemble_screenplay(
    plan: LecturePlan,
    scenes: list[Any],
    ingest: IngestResult,
    options: GenerationOptions,
    lexicon: list[LexiconEntry],
    source: SourceInfo | None = None,
) -> Screenplay:
    """Screenplay with plan metadata, chapters and scenes in plan order.

    Plan values are clamped to the (stricter) Screenplay limits; ``write_scenes_detailed`` has already
    fitted the scene count, so this never fails after the scene calls were paid for.
    """
    written = {s.id for s in scenes}
    return Screenplay(
        subject_name=plan.subject_name[: META_MAX["subject_name"]],
        unit_name=plan.unit_name[: META_MAX["unit_name"]],
        session_number=plan.session_number[: META_MAX["session_number"]] or "Session 1",
        session_title=plan.session_title[: META_MAX["session_title"]],
        language=options.language,
        board_language=options.board_language,
        learning_objectives=list(plan.learning_objectives),
        concept_map=list(plan.concept_map),
        misconceptions=list(plan.misconceptions),
        chapters=[
            ChapterRef(id=ch.id, title=(ch.title.strip() or ch.id)[:CHAPTER_TITLE_MAX],
                       scene_ids=[s.id for s in ch.scenes if s.id in written])
            for ch in plan.chapters
        ],
        lexicon=list(lexicon),
        scenes=scenes,
        figures=list(ingest.figures),
        source=source or SourceInfo(mime=ingest.source_mime, pages=ingest.pages),
    )


async def write_scenes_detailed(
    ctx: Any,
    plan: LecturePlan,
    ingest: IngestResult,
    options: GenerationOptions,
    *,
    lexicon: list[LexiconEntry] | None = None,
    source: SourceInfo | None = None,
    progress: tuple[float, float] | None = None,
    brief: ConceptBrief | None = None,
    checkpoints: SceneCheckpointHook | None = None,
) -> ScriptResult:
    """Write every planned scene in parallel and assemble the screenplay.

    ``brief``: the concept brief the plan was made from; the chunks it set aside never reach a scene writer
    as nearby context or keyword matches.

    ``checkpoints``: a scene stored by an earlier attempt of the same job is reused instead of written again,
    and every scene written now (not a fallback) is stored as soon as it is finished, so an attempt that stops
    halfway keeps what it paid for. The result is the same as without the hook.

    Before any paid call: the plan is clamped to the Screenplay limits (a ``plan.truncated`` warning
    lists what was cut — e.g. a teacher-edited plan with more than 200 scenes) and a source the model
    cannot see is refused (``IngestError``). When ingest asked for the original file but it could not
    be attached, a ``source.not_attached`` warning tells the teacher.
    """
    plan, cut = fit_plan(plan)
    early: list[Issue] = []
    if cut:
        early.append(Issue(code="plan.truncated", severity="warning", source="system", fixable=False,
                           message="The plan did not fit a lecture's limits: " + "; ".join(cut)[:600]))
    lc = LectureContext(plan=plan, ingest=ingest, options=options, lexicon=list(lexicon or []), figures=list(ingest.figures),
                        skipped_chunk_ids=skipped_ids(brief))
    pos_list = positions(plan)
    llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    files = await original_attachment(ctx, ingest, llm, purpose="scene writers")
    require_grounding(ingest, files)
    if ingest.attach_original and not files:
        early.append(Issue(
            code="source.not_attached", severity="warning", source="system", fixable=False,
            message="Some of the source (scanned pages, equations or undecodable text) could not be extracted, and "
                    "the AI model could not read the original file: check those parts of the lecture against the source.",
        ))
    sem = asyncio.Semaphore(max(1, int(ctx.settings.llm_max_parallel)))
    done = {"n": 0}
    lo, hi = progress or (0.0, 1.0)
    attached = bool(files)

    async def one(pos: ScenePosition) -> SceneWriteResult:
        scene_id = pos.planned.id
        res = scene_result_from(await checkpoints.load(scene_id, attached), scene_id) if checkpoints else None
        if res is None:
            async with sem:
                ctx.check_cancelled()
                res = await write_scene(ctx, lc, pos, llm=llm, files=files)
            if checkpoints is not None and not res.fallback:
                await checkpoints.save(scene_id, attached, scene_result_data(res))
        done["n"] += 1
        ctx.progress("script", lo + (hi - lo) * done["n"] / max(1, len(pos_list)),
                     f"Writing scene {done['n']}/{len(pos_list)}")
        return res

    results = await gather_all(one(p) for p in pos_list)
    screenplay = assemble_screenplay(plan, [r.scene for r in results], ingest, options, lc.lexicon, source)
    issues = early + [i for r in results for i in r.issues]
    return ScriptResult(screenplay, issues, [r.scene.id for r in results if r.fallback], plan=plan,
                        source_attached=bool(files))


async def write_scenes(ctx: Any, plan: LecturePlan, ingest: IngestResult, options: GenerationOptions, *,
                       brief: ConceptBrief | None = None) -> Screenplay:
    """Contract entry point (see ``aadhi.pipeline.base``): the screenplay only."""
    return (await write_scenes_detailed(ctx, plan, ingest, options, brief=brief)).screenplay
