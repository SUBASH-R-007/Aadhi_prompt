"""Repair: rewrite only the scenes with errors or fixable critic/lint warnings (<= 2 rounds).

Each failing scene is regenerated with its family prompt + ``prompts/repair.md``, given the
current scene, the issues, its intent and its neighbours. The scene id (and chapter) are kept, and
so is what the teacher set on the scene (``carry_over``): uploaded media overrides, notes and the
mascot position. The screenplay is linted with the ``IngestResult`` (so header values that source
scoping removed are caught as ``content.admin_leak``) before the first round and after every round.
A rewrite that fails keeps the original scene. The quality checks' ``AUTHOR_CODES`` (terminology, formula
notation, code language, title casing) are never handed to a rewrite, so it cannot change those on its own.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import FileInput
from ..schemas.screenplay import Screenplay
from . import integrations
from .aio import gather_all
from .assets import carry_visual_variant
from .base import ConceptBrief, GenerationOptions, IngestResult, Issue, LecturePlan
from .critic import render_scene_for_review
from .gen_models import scene_family
from .plan import original_attachment
from .prompting import json_section
from .quality import AUTHOR_CODES
from .screenplay_ops import lecture_context, position_of, replace_scenes
from .script import FALLBACK_NOTE, FAMILY_PROMPTS, write_scene
from .validate import lint, scenes_needing_repair

log = logging.getLogger(__name__)

MAX_ROUNDS = 2


@dataclass
class RepairResult:
    screenplay: Screenplay
    issues: list[Issue] = field(default_factory=list)  # lint after repair + remaining non-lint issues
    repaired_scene_ids: list[str] = field(default_factory=list)
    rounds: int = 0


def issue_lines(issues: list[Issue]) -> list[dict[str, Any]]:
    return [{"severity": i.severity, "code": i.code, "beat": i.beat_id, "message": i.message} for i in issues]


def carry_over(current: Any, new: Any) -> Any:
    """``new`` (a rewrite of ``current``) with what the teacher set on ``current`` kept.

    * ``chapter_id`` and ``mascot_position`` (layout choices);
    * ``notes`` unless empty or the automatic fallback note (the model's new notes are used then);
    * uploaded media overrides when the media slot still exists: the scene's ``override_asset_key``
      (simulation / AI video), the interactive ``poster_override_asset_key`` and the side panel's
      ``override_asset_key`` when the new panel has the same kind;
    * the "new AI version" number (``variant``, Visual Review) of a generated picture or clip whose
      request is unchanged (``assets.carry_visual_variant``), so a rewritten narration keeps the picture;
    * ``hidden`` and ``min_seconds`` (a regenerated hidden scene stays hidden, a held scene keeps its hold).
    """
    update: dict[str, Any] = {"chapter_id": current.chapter_id}
    if current.hidden:
        update["hidden"] = True
    if current.min_seconds is not None:
        update["min_seconds"] = current.min_seconds
    if new.type == current.type:
        update["mascot_position"] = current.mascot_position
        for name in ("override_asset_key", "poster_override_asset_key"):
            value = getattr(current, name, None)
            if value and hasattr(new, name):
                update[name] = value
    notes = (current.notes or "").strip()
    if notes and notes != FALLBACK_NOTE:
        update["notes"] = current.notes
    old_panel, new_panel = current.side_panel, new.side_panel
    if old_panel is not None and new_panel is not None and old_panel.override_asset_key and old_panel.kind == new_panel.kind:
        update["side_panel"] = new_panel.model_copy(update={"override_asset_key": old_panel.override_asset_key})
    return carry_visual_variant(current, new.model_copy(update=update))


async def rewrite_scene(
    ctx: Any,
    screenplay: Screenplay,
    scene_id: str,
    issues: list[Issue],
    ingest: IngestResult,
    options: GenerationOptions,
    *,
    plan: LecturePlan | None = None,
    instructions: str = "",
    llm: Any | None = None,
    files: Sequence[FileInput] | None = None,
    brief: ConceptBrief | None = None,
) -> tuple[Any | None, list[Issue]]:
    """Rewrite one scene; returns (new scene or None when the rewrite failed, side issues).

    Without the original plan, the scene's plan is rebuilt from the screenplay (``plan_from_screenplay``),
    including its narrative role and bridge, so the rewrite still continues from its neighbour. ``brief``:
    the concept brief, whose set-aside chunks never reach the writer as context.
    """
    llm = llm or integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    if files is None:
        files = await original_attachment(ctx, ingest, llm, purpose="scene writers")
    lc = lecture_context(screenplay, ingest, options, plan=plan, brief=brief)
    try:  # prefer the original plan (goals, intent) when it still contains the scene
        pos = position_of(lc, scene_id)
    except KeyError:
        lc = lecture_context(screenplay, ingest, options, brief=brief)
        pos = position_of(lc, scene_id)
    current = screenplay.scene_by_id(scene_id)
    extra = [json_section("Current scene", render_scene_for_review(current))]
    # Terms, notation, code languages and titles are the author's choices: a rewrite never "fixes" them on its own.
    issues = [i for i in issues if i.code not in AUTHOR_CODES]
    if issues:
        extra.append(json_section("Issues to fix", issue_lines(issues)))
    if instructions.strip():
        extra.append(json_section("Teacher's instructions for this rewrite", {"instructions": instructions.strip()}))
    task = ("Rewrite this scene so that every listed issue is fixed and the teacher's instructions are followed. "
            "Keep what already works. Respond with JSON that matches the response schema.")
    res = await write_scene(
        ctx, lc, pos, llm=llm, extra_sections=extra, task=task, files=files,
        prompt_names=("style", FAMILY_PROMPTS[scene_family(pos.planned.type)], "repair"),
    )
    if res.fallback:
        return None, res.issues
    return carry_over(current, res.scene), res.issues


async def repair_detailed(
    ctx: Any,
    screenplay: Screenplay,
    issues: list[Issue],
    ingest: IngestResult,
    options: GenerationOptions,
    *,
    plan: LecturePlan | None = None,
    max_rounds: int = MAX_ROUNDS,
    progress: tuple[float, float] | None = None,
    brief: ConceptBrief | None = None,
) -> RepairResult:
    """Repair loop; returns the repaired screenplay and the remaining issues."""
    chunk_ids = {c.id for c in ingest.chunks} or None
    current = screenplay
    other = [i for i in issues if i.source != "lint"]
    lint_issues = lint(current, options, chunk_ids=chunk_ids, ingest=ingest)
    repaired: list[str] = []
    llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
    files = await original_attachment(ctx, ingest, llm, purpose="scene writers")
    sem = asyncio.Semaphore(max(1, int(ctx.settings.llm_max_parallel)))
    rounds = 0
    for rnd in range(max_rounds):
        targets = scenes_needing_repair(lint_issues + other)
        if not targets:
            break
        rounds = rnd + 1
        if progress:
            lo, hi = progress
            ctx.progress("validate", lo + (hi - lo) * rnd / max_rounds, f"Repairing {len(targets)} scene(s)")

        async def one(sid: str, found: list[Issue], snapshot: Screenplay = current) -> tuple[str, Any, list[Issue]]:
            async with sem:
                ctx.check_cancelled()
                new, side = await rewrite_scene(ctx, snapshot, sid, found, ingest, options, plan=plan, llm=llm,
                                                files=files, brief=brief)
                return sid, new, side

        results = await gather_all(one(sid, found) for sid, found in targets.items())
        replacements = {sid: new for sid, new, _ in results if new is not None}
        if replacements:
            try:
                current = replace_scenes(current, replacements)
            except ValueError as exc:  # a rewrite broke a cross-reference: keep the originals
                log.warning("repair round %s rejected: %s", rnd + 1, ctx.settings.redact(str(exc))[:300])
                replacements = {}
        repaired.extend(s for s in replacements if s not in repaired)
        kept_side = [i for sid, _, side in results if sid in replacements for i in side]
        other = [i for i in other if i.scene_id not in replacements] + kept_side
        lint_issues = lint(current, options, chunk_ids=chunk_ids, ingest=ingest)
        if not replacements:
            break
    return RepairResult(current, lint_issues + other, repaired, rounds)


async def repair(ctx: Any, screenplay: Screenplay, issues: list[Issue], ingest: IngestResult,
                 options: GenerationOptions, *, brief: ConceptBrief | None = None) -> Screenplay:
    """Contract entry point (see ``aadhi.pipeline.base``); the chunks the concept ``brief`` set aside never reach
    the rewrites."""
    return (await repair_detailed(ctx, screenplay, issues, ingest, options, brief=brief)).screenplay
