"""Optional AI terminology assistant (off by default: ``QUALITY_AI_TERMINOLOGY``).

The deterministic checks leave some term pairs undecided: an abbreviation the lesson never spells out
and a lesson term it could stand for, or two different names used for one concept. With the setting on,
the critic stage of ``generate_lecture`` asks the lecture's engine (``fast`` model, ``prompts/
quality_terms.md``) once per lecture about at most ``MAX_PAIRS`` such pairs, through
``integrations.get_llm`` like every other call: the job's usage accounting, budgets and the user's own
keys apply. Answers are validated (listed pair numbers, true / false only); a "yes" becomes a
``terminology.ambiguous`` note (info, ``source="critic"``, never fixable, so never repaired) that says it
was suggested by the AI reviewer. A failure only logs: the lecture and the rule findings never depend
on the assistant.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import Field

from ...jobs.base import BudgetExceeded, JobCancelled
from ...schemas.screenplay import Screenplay
from .. import integrations
from ..base import GenerationOptions, Issue
from ..gen_models import GenModel
from ..prompting import join_sections, json_section, section, system_prompt
from . import abbreviations
from .extract import NOT_A_CONCEPT, extract
from .terms import concept_names
from .text import Lesson, group_key, latin_board, quote

log = logging.getLogger(__name__)

ASSIST_PROMPTS = ("quality_terms",)
MAX_PAIRS = 8
MAX_CANDIDATES_PER_ABBREVIATION = 2
SUGGESTED = "Suggested by the AI reviewer (not checked by the rules)"


class GenTermAnswer(GenModel):
    pair: int = Field(description="the number of the pair, as listed")
    same: bool = Field(description="true only when both terms name the same thing in this lesson")


class GenTermAnswers(GenModel):
    answers: list[GenTermAnswer] = Field(default_factory=list)


def could_abbreviate(abbr: str, term: str) -> bool:
    """Whether ``abbr``'s letters appear in ``term`` in order, starting with its first letter."""
    letters = "".join(ch for ch in term.lower() if "a" <= ch <= "z")
    a = abbr.lower()
    if not letters or letters[0] != a[0] or len(letters) < len(a) + 2 or term.isupper():
        return False
    it = iter(letters)
    return all(ch in it for ch in a)


def ambiguous_pairs(screenplay: Screenplay) -> list[dict[str, Any]]:
    """The pairs the rules cannot decide (bounded): ``{"n", "kind", "a", "b", "scenes"}`` (scene indexes)."""
    if not latin_board(screenplay):
        return []
    facts = extract(screenplay)
    pairs: list[dict[str, Any]] = []
    for abbr, a in abbreviations.analyse(facts).items():
        if a["definitions"] or a["initials"]:
            continue
        found = 0
        for key, forms in facts.groups.items():
            form = next(iter(forms))
            if key != group_key(abbr) and could_abbreviate(abbr, form):
                pairs.append({"kind": "abbreviation", "a": abbr, "b": form, "scenes": a["scenes"][:6]})
                found += 1
                if found >= MAX_CANDIDATES_PER_ABBREVIATION:
                    break
    for c in concept_names(facts).values():
        if not c["id"]:
            continue
        names = ([(c["map_title"], None)] if c["map_title"] else []) + c["names"]
        firsts: dict[str, tuple[str, int | None]] = {}
        for name, scene in names:
            if name and not NOT_A_CONCEPT.match(name) and len(name.split()) <= 5:
                firsts.setdefault(group_key(name), (name, scene))
        listed = list(firsts.values())
        for name, scene in listed[1:3]:
            first, first_scene = listed[0]
            pairs.append({"kind": "concept", "a": first, "b": name,
                          "scenes": [s for s in (first_scene, scene) if s is not None]})
    return [{**p, "n": n + 1, "a": p["a"][:60], "b": p["b"][:60]} for n, p in enumerate(pairs[:MAX_PAIRS])]


def _issue(lesson: Lesson, pair: dict[str, Any]) -> Issue | None:
    scenes = pair["scenes"]
    if not scenes:  # an issue without a scene would outlive every edit (non-lint issues are kept by scene)
        return None
    if pair["kind"] == "abbreviation":
        message = (f"{SUGGESTED}: {quote(pair['a'])}, used in {lesson.where(scenes)}, probably stands for "
                   f"{quote(pair['b'])}, which the lesson uses elsewhere. If so, spell it out once so learners can "
                   "connect the two.")
        scene = scenes[0]
    else:
        message = (f"{SUGGESTED}: {quote(pair['a'])} and {quote(pair['b'])} ({lesson.where(scenes)}) seem to name the "
                   "same idea. If so, use one name throughout the lesson.")
        scene = scenes[-1]
    return Issue(code="terminology.ambiguous", severity="info", message=message, scene_id=lesson.scene_id(scene),
                 source="critic", fixable=False)


async def terminology_assist(ctx: Any, screenplay: Screenplay, options: GenerationOptions | None = None) -> list[Issue]:
    """The assistant's notes for a whole lecture ([] when off, nothing is ambiguous, or the call fails)."""
    if not getattr(ctx.settings, "quality_ai_terminology", False):
        return []
    pairs = ambiguous_pairs(screenplay)
    if not pairs:
        return []
    numbers = {p["n"] for p in pairs}

    def validate(out: GenTermAnswers) -> list[str]:
        bad = sorted({a.pair for a in out.answers if a.pair not in numbers})
        return [f"unknown pair numbers {bad}; answer only for the listed pairs"] if bad else []

    prompt = join_sections(
        json_section("Lesson", {"session_title": screenplay.session_title, "subject": screenplay.subject_name}),
        json_section("Pairs", [{"pair": p["n"], "a": p["a"], "b": p["b"]} for p in pairs]),
        section("Task", "For each pair, say whether the two terms name the same thing in this lesson. "
                        "Respond with JSON that matches the response schema."),
    )
    try:
        llm = integrations.get_llm(ctx.settings, integrations.llm_engine(options, ctx.settings))
        async with integrations.limit("llm"):
            out: GenTermAnswers = await llm.generate_json(
                model=integrations.llm_model(ctx.settings, "fast", options), system=system_prompt(*ASSIST_PROMPTS),
                prompt=prompt, schema=GenTermAnswers, temperature=0.0, on_usage=ctx.record_usage, validate=validate,
                validation_retries=1,
            )
    except (JobCancelled, BudgetExceeded):
        raise
    except Exception as exc:  # noqa: BLE001 - optional and advisory: the lecture never depends on it
        log.info("AI terminology check failed: %s", type(exc).__name__)
        ctx.log(f"The AI terminology check could not run: {ctx.settings.redact(str(exc))[:200]}", "warning")
        return []
    answers: dict[int, bool] = {}
    for a in out.answers:
        answers.setdefault(a.pair, a.same)
    lesson = Lesson(screenplay)
    issues = [_issue(lesson, p) for p in pairs if answers.get(p["n"]) is True]
    return [i for i in issues if i is not None]
