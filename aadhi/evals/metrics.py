"""Deterministic lecture-quality metrics.

Everything here is a pure function of a validated ``Screenplay`` plus optional context (lint/critic
issues, source chunk ids, asset manifest, usage records). No I/O, no randomness, no clock: the same
inputs always give the same numbers, so two eval runs can be diffed metric by metric
(``python -m aadhi.evals compare``).

Thresholds are deliberately *independent* of the lint rules in ``aadhi.pipeline.validate``: lint
decides what to flag for a teacher, these metrics measure a whole lecture so prompt/model changes
can be compared. Percentages are 0..100 and ``None`` when the denominator is zero.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import ValidationError

from ..compose.base import (
    QUIZ_REVEAL_HOLD_SECONDS,
    SCENE_LEAD_SECONDS,
    SCENE_TAIL_SECONDS,
    SILENT_SCENE_SECONDS,
)
from ..providers.base import Usage
from ..schemas.manifest import INTER_BEAT_GAP_SECONDS, AssetManifest
from ..schemas.screenplay import (
    AIVideoScene,
    Beat,
    BoardItem,
    BoardItemKind,
    BoardScene,
    ChapterCardScene,
    InteractiveScene,
    ManimSpec,
    QuizScene,
    Screenplay,
    SimulationScene,
)

METRICS_VERSION = "1"

# --- Thresholds (documented in docs/EVALS.md) ------------------------------------------------
BOARD_ITEM_LIMIT = 7  # items on one board before it stops fitting the 1920x1080 stage comfortably
ITEM_CHAR_LIMIT = 140  # visible characters of one board item (text + term + caption + justification)
BEAT_WORDS_MAX = 45  # longer beats lose the narration/board contiguity
BEAT_WORDS_MIN = 4  # shorter beats feel choppy (chapter cards and quiz reveals excepted)
HIGHER_ORDER_BLOOM = frozenset({"apply", "analyze", "evaluate", "create"})
# Speaking rate used for duration estimates when no audio exists yet (words per minute).
WORDS_PER_MINUTE: dict[str, int] = {"en": 150, "hi": 135, "ta": 105, "te": 110, "kn": 105, "ml": 100}
DEFAULT_WPM = 140
# Scene types whose beats make no factual claims (excluded from grounding denominators).
_NON_CLAIM_TYPES = frozenset({"title", "chapter_card"})
# Scene types that teach a concept (used for quiz spacing).
_TEACHING_TYPES = frozenset({"content", "example", "simulation", "ai_video", "interactive"})
_TEXT_KINDS_EXCLUDED_FROM_CHARS = frozenset({BoardItemKind.code, BoardItemKind.table})


@dataclass(frozen=True)
class UsageRecord:
    """One priced provider call, attributed to the eval stage that made it."""

    usage: Usage
    cost_usd: float = 0.0
    stage: str = ""


@dataclass(frozen=True)
class MetricSpec:
    """How a headline metric is labelled and which direction is an improvement."""

    key: str  # dotted path into the metrics dict
    label: str
    better: Literal["higher", "lower"] | None = None
    unit: str = ""


HEADLINE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("schema.valid", "Schema valid", "higher"),
    MetricSpec("structure.scenes", "Scenes"),
    MetricSpec("lint.errors", "Lint errors", "lower"),
    MetricSpec("lint.warnings", "Lint warnings", "lower"),
    MetricSpec("board.scenes_over_item_limit_pct", "Boards over item limit", "lower", "%"),
    MetricSpec("board.items_over_char_limit_pct", "Board items over char limit", "lower", "%"),
    MetricSpec("board.chars_per_item_mean", "Chars per board item"),
    MetricSpec("pacing.words_per_beat_mean", "Words per beat"),
    MetricSpec("pacing.beats_too_long_pct", "Beats too long", "lower", "%"),
    MetricSpec("pacing.est_minutes", "Estimated minutes", None, "min"),
    MetricSpec("pacing.est_vs_target_pct_error", "Duration error vs target", "lower", "%"),
    MetricSpec("objectives.taught_pct", "Objectives taught", "higher", "%"),
    MetricSpec("objectives.assessed_pct", "Objectives assessed", "higher", "%"),
    MetricSpec("misconceptions.targeted_pct", "Misconceptions targeted", "higher", "%"),
    MetricSpec("quiz.quizzes_per_10_min", "Quizzes per 10 min"),
    MetricSpec("quiz.answer_position_max_share_pct", "Answer-position skew", "lower", "%"),
    MetricSpec("quiz.distractors_with_misconception_pct", "Distractors tied to misconceptions", "higher", "%"),
    MetricSpec("grounding.beats_with_refs_pct", "Beats with source refs", "higher", "%"),
    MetricSpec("grounding.unknown_refs", "Unknown source refs", "lower"),
    MetricSpec("grounding.source_coverage_pct", "Source chunks cited", "higher", "%"),
    MetricSpec("media.rationale_coverage_pct", "Visuals with rationale", "higher", "%"),
    MetricSpec("manim.template_ratio_pct", "Manim via templates", "higher", "%"),
    MetricSpec("worked_examples.blank_filled_pct", "Faded steps filled", "higher", "%"),
    MetricSpec("cost.total_usd", "Cost", "lower", "USD"),
)
METRIC_DIRECTIONS: dict[str, str] = {m.key: m.better for m in HEADLINE_METRICS if m.better}
METRIC_DIRECTIONS.update(
    {
        "lint.total": "lower",
        "schema.error_count": "lower",
        "pacing.beats_too_short_pct": "lower",
        "objectives.practiced_pct": "higher",
        "misconceptions.in_quiz_pct": "higher",
        "quiz.wrong_feedback_coverage_pct": "higher",
        "quiz.with_explanation_pct": "higher",
        "grounding.board_items_with_refs_pct": "higher",
        "worked_examples.fills_after_pause_pct": "higher",
        "audio.missing_audio_scenes": "lower",
        "audio.fallback_media": "lower",
        "cost.input_tokens": "lower",
        "cost.output_tokens": "lower",
        "brief.unaccounted_chunks": "lower",
    }
)


# --- small helpers -----------------------------------------------------------------------------

_ESC_DOLLAR, _ESC_STAR = "\x00d", "\x00s"
_RICH_SUBS = (
    (re.compile(r"\[\[(.+?)\]\]"), r"\1"),
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"\*(.+?)\*"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\$([^$]*)\$"), r"\1"),
)


def plain_text(text: str | None) -> str:
    """Strip rich-lite markup (``**b**``, ``*i*``, `` `c` ``, ``$tex$``, ``[[kw]]``) to visible text."""
    if not text:
        return ""
    out = text.replace("\\$", _ESC_DOLLAR).replace("\\*", _ESC_STAR)
    for pattern, repl in _RICH_SUBS:
        out = pattern.sub(repl, out)
    return out.replace(_ESC_DOLLAR, "$").replace(_ESC_STAR, "*").strip()


def word_count(text: str | None) -> int:
    """Whitespace-delimited words (works for the supported Indic scripts, which use spaces)."""
    return len(text.split()) if text else 0


def pct(part: float, whole: float) -> float | None:
    """``part / whole`` as a percentage rounded to 2 decimals; ``None`` when ``whole`` is 0."""
    if not whole:
        return None
    return round(100.0 * part / whole, 2)


def _ratio(part: float, whole: float, digits: int = 4) -> float | None:
    return round(part / whole, digits) if whole else None


def _mean(values: Sequence[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _p90(values: Sequence[float]) -> float | None:
    """Nearest-rank 90th percentile."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.9 * len(ordered)) - 1)]


def _max(values: Sequence[float]) -> float | None:
    return max(values) if values else None


def _sorted_counts(counter: Mapping[str, int]) -> dict[str, int]:
    return {k: counter[k] for k in sorted(counter)}


def _wpm(language: str) -> int:
    return WORDS_PER_MINUTE.get((language or "en").split("-")[0].lower(), DEFAULT_WPM)


def _issue_field(issue: Any, name: str, default: Any = None) -> Any:
    if isinstance(issue, Mapping):
        return issue.get(name, default)
    return getattr(issue, name, default)


# --- individual metric groups -------------------------------------------------------------------


def schema_check(data: Any) -> dict[str, Any]:
    """Validate a raw screenplay document; returns ``{valid, error_count, errors[:5]}``."""
    try:
        Screenplay.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}" for e in exc.errors()]
        return {"valid": False, "error_count": len(errors), "errors": errors[:5]}
    except (TypeError, ValueError) as exc:
        return {"valid": False, "error_count": 1, "errors": [str(exc)[:300]]}
    return {"valid": True, "error_count": 0, "errors": []}


def structure_metrics(sp: Screenplay) -> dict[str, Any]:
    """Counts of scenes, chapters, beats, board items and scene types."""
    types = Counter(s.type for s in sp.scenes)
    beats = sum(len(s.all_beats()) for s in sp.scenes)
    items = sum(len(s.board) for s in sp.scenes if isinstance(s, BoardScene))
    return {
        "scenes": len(sp.scenes),
        "chapters": len(sp.chapters),
        "beats": beats,
        "board_items": items,
        "concepts": len(sp.concept_map),
        "objectives": len(sp.learning_objectives),
        "misconceptions": len(sp.misconceptions),
        "scene_types": _sorted_counts(types),
    }


def lint_metrics(issues: Iterable[Any], n_scenes: int) -> dict[str, Any]:
    """Issue counts by severity, code and source (accepts ``Issue`` objects or dicts)."""
    severities: Counter[str] = Counter()
    codes: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    error_scenes: set[str] = set()
    total = 0
    for issue in issues:
        total += 1
        sev = _issue_field(issue, "severity", "warning") or "warning"
        severities[sev] += 1
        codes[_issue_field(issue, "code", "unknown.code") or "unknown.code"] += 1
        sources[_issue_field(issue, "source", "lint") or "lint"] += 1
        if sev == "error" and _issue_field(issue, "scene_id"):
            error_scenes.add(_issue_field(issue, "scene_id"))
    return {
        "total": total,
        "errors": severities.get("error", 0),
        "warnings": severities.get("warning", 0),
        "infos": severities.get("info", 0),
        "per_scene": _ratio(total, n_scenes, 3),
        "scenes_with_errors": len(error_scenes),
        "by_severity": {k: severities.get(k, 0) for k in ("error", "warning", "info")},
        "by_source": _sorted_counts(sources),
        "by_code": _sorted_counts(codes),
    }


def board_item_chars(item: BoardItem) -> int:
    """Visible characters of a board item (LaTeX source, code and tables are measured elsewhere)."""
    if item.kind in _TEXT_KINDS_EXCLUDED_FROM_CHARS:
        return 0
    return sum(len(plain_text(x)) for x in (item.text, item.term, item.caption, item.justification) if x)


def board_metrics(sp: Screenplay) -> dict[str, Any]:
    """Board fit: items per board scene, characters per item, unrevealed items, table/code size."""
    boards = [s for s in sp.scenes if isinstance(s, BoardScene) and s.board]
    per_scene = [len(s.board) for s in boards]
    chars: list[int] = []
    kinds: Counter[str] = Counter()
    unrevealed = 0
    table_rows: list[int] = []
    code_lines: list[int] = []
    for s in boards:
        revealed = {b.board_item_id for b in s.beats if b.board_item_id}
        for item in s.board:
            kinds[item.kind.value] += 1
            if item.id not in revealed:
                unrevealed += 1
            if item.kind == BoardItemKind.table:
                table_rows.append(len(item.rows or []))
            elif item.kind == BoardItemKind.code:
                code_lines.append(len((item.code or "").splitlines()))
            else:
                chars.append(board_item_chars(item))
    total_items = sum(per_scene)
    over_items = sum(1 for n in per_scene if n > BOARD_ITEM_LIMIT)
    over_chars = sum(1 for c in chars if c > ITEM_CHAR_LIMIT)
    return {
        "board_scenes": len(boards),
        "items_total": total_items,
        "items_per_scene_mean": _mean(per_scene),
        "items_per_scene_p90": _p90(per_scene),
        "items_per_scene_max": _max(per_scene),
        "scenes_over_item_limit": over_items,
        "scenes_over_item_limit_pct": pct(over_items, len(boards)),
        "chars_per_item_mean": _mean(chars),
        "chars_per_item_p90": _p90(chars),
        "chars_per_item_max": _max(chars),
        "items_over_char_limit": over_chars,
        "items_over_char_limit_pct": pct(over_chars, len(chars)),
        "unrevealed_items_pct": pct(unrevealed, total_items),
        "table_rows_max": _max(table_rows),
        "code_lines_max": _max(code_lines),
        "kinds": _sorted_counts(kinds),
        "limits": {"items": BOARD_ITEM_LIMIT, "chars": ITEM_CHAR_LIMIT},
    }


def _spoken_beats(sp: Screenplay) -> list[tuple[str, Beat, bool]]:
    """(scene type, beat, is_quiz_reveal) for every narrated beat."""
    out: list[tuple[str, Beat, bool]] = []
    for s in sp.scenes:
        for b in s.beats:
            out.append((s.type, b, False))
        if isinstance(s, QuizScene):
            for b in s.reveal_beats:
                out.append((s.type, b, True))
    return out


def estimate_scene_seconds(scene: Any, wpm: int) -> float:
    """Rough runtime of one scene before TTS exists (mirrors the timeline's lead/tail/countdown)."""
    beats = scene.all_beats()
    if not beats:
        return SILENT_SCENE_SECONDS
    speech = sum(word_count(b.narration) / wpm * 60.0 + b.pause_after for b in beats)
    gaps = INTER_BEAT_GAP_SECONDS * max(0, len(beats) - 1)
    seconds = SCENE_LEAD_SECONDS + speech + gaps + SCENE_TAIL_SECONDS
    if isinstance(scene, QuizScene):
        seconds += scene.countdown_seconds + QUIZ_REVEAL_HOLD_SECONDS
    return seconds


def estimate_minutes(sp: Screenplay) -> float:
    """Estimated lecture runtime in minutes (without the branded intro)."""
    wpm = _wpm(sp.language)
    return sum(estimate_scene_seconds(s, wpm) for s in sp.scenes) / 60.0


def pacing_metrics(sp: Screenplay, target_minutes: float | None) -> dict[str, Any]:
    """Words per beat, too long/short beats, pauses and estimated runtime vs the target."""
    words: list[int] = []
    too_long = too_short = 0
    pauses = 0.0
    for scene_type, beat, is_reveal in _spoken_beats(sp):
        n = word_count(beat.narration)
        words.append(n)
        pauses += beat.pause_after
        if n > BEAT_WORDS_MAX:
            too_long += 1
        if n < BEAT_WORDS_MIN and scene_type != "chapter_card" and not is_reveal:
            too_short += 1
    est = round(estimate_minutes(sp), 2)
    target = float(target_minutes) if target_minutes else None
    return {
        "beats": len(words),
        "words_total": sum(words),
        "words_per_beat_mean": _mean(words),
        "words_per_beat_p90": _p90(words),
        "words_per_beat_max": _max(words),
        "beats_too_long": too_long,
        "beats_too_long_pct": pct(too_long, len(words)),
        "beats_too_short": too_short,
        "beats_too_short_pct": pct(too_short, len(words)),
        "pause_seconds_total": round(pauses, 2),
        "wpm_assumed": _wpm(sp.language),
        "est_minutes": est,
        "target_minutes": target,
        "est_vs_target_ratio": _ratio(est, target, 3) if target else None,
        "est_vs_target_pct_error": round(abs(est - target) / target * 100.0, 2) if target else None,
        "limits": {"beat_words_max": BEAT_WORDS_MAX, "beat_words_min": BEAT_WORDS_MIN},
    }


def objective_metrics(sp: Screenplay) -> dict[str, Any]:
    """Objectives taught (non-quiz scenes), assessed (quizzes / quiz panels) and practiced (companion)."""
    taught_ids: set[str] = set()
    assessed_ids: set[str] = set()
    taught_concepts: set[str] = set()
    assessed_concepts: set[str] = set()
    for s in sp.scenes:
        quiz_like = isinstance(s, QuizScene) or (s.side_panel is not None and s.side_panel.kind == "quiz")
        if isinstance(s, QuizScene):
            assessed_ids.update(s.objective_ids)
            if s.concept_id:
                assessed_concepts.add(s.concept_id)
            continue
        if isinstance(s, ChapterCardScene):
            continue
        taught_ids.update(s.objective_ids)
        if s.concept_id:
            taught_concepts.add(s.concept_id)
        if quiz_like:
            assessed_ids.update(s.objective_ids)
            if s.concept_id:
                assessed_concepts.add(s.concept_id)
    practiced_ids = {o for p in sp.companion_sheet.practice_problems for o in p.objective_ids}
    objectives = sp.learning_objectives
    taught = [o.id for o in objectives if o.id in taught_ids or taught_concepts.intersection(o.concept_ids)]
    assessed = [o.id for o in objectives if o.id in assessed_ids or assessed_concepts.intersection(o.concept_ids)]
    practiced = [o.id for o in objectives if o.id in practiced_ids]
    n = len(objectives)
    return {
        "total": n,
        "taught": len(taught),
        "assessed": len(assessed),
        "practiced": len(practiced),
        "taught_pct": pct(len(taught), n),
        "assessed_pct": pct(len(assessed), n),
        "practiced_pct": pct(len(practiced), n),
        "untaught": sorted(o.id for o in objectives if o.id not in taught),
        "unassessed": sorted(o.id for o in objectives if o.id not in assessed),
    }


def misconception_metrics(sp: Screenplay) -> dict[str, Any]:
    """Misconceptions targeted by a quiz distractor or a misconception board item."""
    in_quiz: set[str] = set()
    on_board: set[str] = set()
    for s in sp.scenes:
        if isinstance(s, QuizScene):
            in_quiz.update(m for m in s.option_misconception_ids if m)
        elif isinstance(s, BoardScene):
            on_board.update(i.misconception_id for i in s.board if i.misconception_id)
    ids = [m.id for m in sp.misconceptions]
    targeted = [m for m in ids if m in in_quiz or m in on_board]
    return {
        "total": len(ids),
        "targeted": len(targeted),
        "targeted_pct": pct(len(targeted), len(ids)),
        "in_quiz": sum(1 for m in ids if m in in_quiz),
        "in_quiz_pct": pct(sum(1 for m in ids if m in in_quiz), len(ids)),
        "on_board": sum(1 for m in ids if m in on_board),
        "untargeted": sorted(m for m in ids if m not in targeted),
    }


def quiz_metrics(sp: Screenplay, est_minutes: float) -> dict[str, Any]:
    """Retrieval-practice density, spacing, answer-position balance and distractor quality."""
    quizzes = [s for s in sp.scenes if isinstance(s, QuizScene)]
    panel_quizzes = sum(1 for s in sp.scenes if s.side_panel is not None and s.side_panel.kind == "quiz")
    core = {c.id for c in sp.concept_map if c.kind == "core"}
    if not core:
        core = {s.concept_id for s in sp.scenes if s.concept_id and s.type in _TEACHING_TYPES}
    # Longest run of distinct taught concepts between two quiz checkpoints.
    longest = run = 0
    run_concepts: set[str] = set()
    for s in sp.scenes:
        if isinstance(s, QuizScene):
            run_concepts.clear()
            run = 0
        elif s.type in _TEACHING_TYPES and s.concept_id and s.concept_id not in run_concepts:
            run_concepts.add(s.concept_id)
            run += 1
            longest = max(longest, run)
    positions: Counter[str] = Counter(str(q.correct_index) for q in quizzes)
    wrong = with_misc = with_feedback = 0
    for q in quizzes:
        for idx in range(len(q.options)):
            if idx == q.correct_index:
                continue
            wrong += 1
            if idx < len(q.option_misconception_ids) and q.option_misconception_ids[idx]:
                with_misc += 1
            if idx < len(q.feedback_wrong) and q.feedback_wrong[idx].strip():
                with_feedback += 1
    blooms: Counter[str] = Counter(q.bloom for q in quizzes)
    n = len(quizzes)
    max_share = max(positions.values()) if positions else 0
    return {
        "quiz_scenes": n,
        "panel_quizzes": panel_quizzes,
        "core_concepts": len(core),
        "quizzes_per_concept": _ratio(n, len(core), 3),
        "quizzes_per_10_min": round(n / (est_minutes / 10.0), 3) if est_minutes > 0 else None,
        "max_concepts_between_quizzes": longest,
        "answer_positions": _sorted_counts(positions),
        "answer_position_max_share_pct": pct(max_share, n),
        "distractors": wrong,
        "distractors_with_misconception_pct": pct(with_misc, wrong),
        "wrong_feedback_coverage_pct": pct(with_feedback, wrong),
        "with_explanation_pct": pct(sum(1 for q in quizzes if q.explanation.strip()), n),
        "bloom": _sorted_counts(blooms),
        "higher_order_pct": pct(sum(blooms[b] for b in HIGHER_ORDER_BLOOM), n),
    }


def _collect_refs(sp: Screenplay) -> list[str]:
    refs: list[str] = []
    for s in sp.scenes:
        for b in s.all_beats():
            refs.extend(b.source_refs)
        if isinstance(s, BoardScene):
            for item in s.board:
                refs.extend(item.source_refs)
        if isinstance(s, QuizScene):
            refs.extend(s.source_refs)
        if s.intent is not None:
            refs.extend(s.intent.source_refs)
    for p in sp.companion_sheet.practice_problems:
        refs.extend(p.source_refs)
    return refs


def grounding_metrics(sp: Screenplay, chunk_ids: Iterable[str] | None) -> dict[str, Any]:
    """Share of claims carrying source refs, refs that point nowhere, and how much source is used."""
    beats = [b for s in sp.scenes if s.type not in _NON_CLAIM_TYPES for b in s.all_beats()]
    items = [i for s in sp.scenes if isinstance(s, BoardScene) and s.type not in _NON_CLAIM_TYPES for i in s.board]
    claim_scenes = [s for s in sp.scenes if s.type not in _NON_CLAIM_TYPES]
    with_intent = sum(1 for s in claim_scenes if s.intent is not None and s.intent.source_refs)
    refs = _collect_refs(sp)
    out: dict[str, Any] = {
        "beats": len(beats),
        "beats_with_refs": sum(1 for b in beats if b.source_refs),
        "beats_with_refs_pct": pct(sum(1 for b in beats if b.source_refs), len(beats)),
        "board_items_with_refs_pct": pct(sum(1 for i in items if i.source_refs), len(items)),
        "scenes_with_intent_refs_pct": pct(with_intent, len(claim_scenes)),
        "refs_total": len(refs),
        "chunks_total": None,
        "chunks_cited": None,
        "source_coverage_pct": None,
        "unknown_refs": None,
        "unknown_ref_ids": [],
    }
    if chunk_ids is not None:
        known = set(chunk_ids)
        unknown = [r for r in refs if r not in known]
        cited = {r for r in refs if r in known}
        out.update(
            chunks_total=len(known),
            chunks_cited=len(cited),
            source_coverage_pct=pct(len(cited), len(known)),
            unknown_refs=len(unknown),
            unknown_ref_ids=sorted(set(unknown))[:20],
        )
    return out


def media_metrics(sp: Screenplay) -> dict[str, Any]:
    """Media mix (panel kinds, visual scene types) and how many visuals justify themselves."""
    panels: Counter[str] = Counter()
    required = present = 0
    visual_scenes = 0
    for s in sp.scenes:
        has_visual = isinstance(s, SimulationScene | AIVideoScene | InteractiveScene)
        if s.side_panel is not None:
            panels[s.side_panel.kind] += 1
            if s.side_panel.kind != "skill_tree":
                has_visual = True
                required += 1
                present += bool(s.side_panel.rationale.strip())
        if isinstance(s, AIVideoScene):
            required += 1
            present += bool(s.rationale.strip())
        visual_scenes += has_visual
    return {
        "side_panels": _sorted_counts(panels),
        "panel_kinds_distinct": len(panels),
        "visual_scenes": visual_scenes,
        "visual_scenes_pct": pct(visual_scenes, len(sp.scenes)),
        "rationale_required": required,
        "rationale_present": present,
        "rationale_coverage_pct": pct(present, required),
    }


def _manim_specs(sp: Screenplay) -> list[ManimSpec]:
    specs: list[ManimSpec] = []
    for s in sp.scenes:
        if isinstance(s, SimulationScene):
            specs.append(s.manim)
        if s.side_panel is not None and s.side_panel.kind == "manim" and s.side_panel.manim is not None:
            specs.append(s.side_panel.manim)
    return specs


def manim_metrics(sp: Screenplay, known_templates: Iterable[str] | None = None) -> dict[str, Any]:
    """Template vs free-form Manim usage (templates are tested, cheaper and safer)."""
    specs = _manim_specs(sp)
    templates = Counter(s.template for s in specs if s.template)
    n_template = sum(templates.values())
    unknown: int | None = None
    if known_templates is not None:
        known = set(known_templates)
        unknown = sum(n for name, n in templates.items() if name not in known)
    return {
        "specs": len(specs),
        "template": n_template,
        "freeform": len(specs) - n_template,
        "template_ratio_pct": pct(n_template, len(specs)),
        "templates_used": _sorted_counts(templates),
        "unknown_templates": unknown,
    }


def worked_example_metrics(sp: Screenplay) -> dict[str, Any]:
    """Worked examples with fading: blank steps, whether they get filled, think-pauses before fills."""
    steps = blanks = filled = fills = fills_after_pause = 0
    example_scenes = 0
    for s in sp.scenes:
        if not isinstance(s, BoardScene):
            continue
        scene_steps = [i for i in s.board if i.kind == BoardItemKind.example_step]
        if s.type == "example" or scene_steps:
            example_scenes += 1
        steps += len(scene_steps)
        blank_ids = {i.id for i in scene_steps if i.blank}
        blanks += len(blank_ids)
        filled_ids: set[str] = set()
        for idx, b in enumerate(s.beats):
            if b.fill_item_id:
                fills += 1
                filled_ids.add(b.fill_item_id)
                if idx > 0 and s.beats[idx - 1].pause_after > 0:
                    fills_after_pause += 1
        filled += len(blank_ids & filled_ids)
    return {
        "example_scenes": example_scenes,
        "steps": steps,
        "blank_steps": blanks,
        "blank_filled": filled,
        "blank_filled_pct": pct(filled, blanks),
        "fills_after_pause_pct": pct(fills_after_pause, fills),
    }


def cost_metrics(records: Iterable[UsageRecord] | None, *, pricing_available: bool = True) -> dict[str, Any]:
    """Spend and volume by operation, stage and provider/model."""
    by_op: Counter[str] = Counter()
    by_stage: Counter[str] = Counter()
    by_model: Counter[str] = Counter()
    calls = in_tok = out_tok = chars = 0
    seconds = 0.0
    total = 0.0
    for r in records or ():
        u = r.usage
        calls += 1
        total += r.cost_usd
        by_op[u.operation] += r.cost_usd
        by_stage[r.stage or "unknown"] += r.cost_usd
        by_model[f"{u.provider}/{u.model}"] += r.cost_usd
        in_tok += u.input_tokens
        out_tok += u.output_tokens
        chars += u.characters
        seconds += u.seconds

    def rounded(c: Counter[str]) -> dict[str, float]:
        return {k: round(c[k], 6) for k in sorted(c)}

    return {
        "total_usd": round(total, 6),
        "calls": calls,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "tts_characters": chars,
        "media_seconds": round(seconds, 2),
        "by_operation": rounded(by_op),
        "by_stage": rounded(by_stage),
        "by_model": rounded(by_model),
        "pricing_available": pricing_available,
    }


def audio_metrics(sp: Screenplay, manifest: AssetManifest) -> dict[str, Any]:
    """Measured narration (after the asset stage): audio minutes, real speaking rate, fallbacks."""
    narrated = [s for s in sp.scenes if s.all_beats()]
    audio_seconds = 0.0
    speech_seconds = 0.0
    words = 0
    beats = estimated = 0
    missing = 0
    for s in narrated:
        sa = manifest.audio.get(s.id)
        if sa is None or not sa.asset_key:
            missing += 1
            continue
        audio_seconds += sa.duration
        by_id = {b.id: b for b in s.all_beats()}
        for ba in sa.beats:
            beats += 1
            estimated += ba.words_estimated
            speech_seconds += ba.speech_duration
            words += word_count(by_id[ba.beat_id].narration) if ba.beat_id in by_id else 0
    silent = sum(1 for s in sp.scenes if not s.all_beats())
    runtime = audio_seconds + len(narrated) * (SCENE_LEAD_SECONDS + SCENE_TAIL_SECONDS) + silent * SILENT_SCENE_SECONDS
    runtime += sum(QUIZ_REVEAL_HOLD_SECONDS for s in sp.scenes if isinstance(s, QuizScene))
    warnings = sum(len(m.warnings) for m in manifest.media.values())
    fallbacks = sum(1 for m in manifest.media.values() if m.main_is_fallback)
    fallbacks += sum(
        1
        for m in manifest.media.values()
        for info in (m.main, m.side_panel, m.poster)
        if info is not None and info.source == "fallback"
    )
    return {
        "narrated_scenes": len(narrated),
        "missing_audio_scenes": missing,
        "audio_minutes": round(audio_seconds / 60.0, 3),
        "runtime_minutes_est": round(runtime / 60.0, 3),
        "speech_wpm": round(words / (speech_seconds / 60.0), 1) if speech_seconds > 0 else None,
        "words_estimated_pct": pct(estimated, beats),
        "stale_scenes": len(manifest.stale_scenes),
        "media_warnings": warnings,
        "fallback_media": fallbacks,
    }


# --- concept brief -----------------------------------------------------------------------------


def brief_metrics(brief: Any, chunk_ids: Sequence[str]) -> dict[str, Any]:
    """What the concept brief made of the source: concepts, source questions, chunks cited / set aside (by
    reason) / neither, and ``coverage`` = the fraction of chunks it cited. ``brief`` is a ``ConceptBrief`` or
    None (the brief could not be built: the lecture was planned directly from the source)."""
    if brief is None:
        return {"available": False}
    ids = list(dict.fromkeys(chunk_ids))
    known = set(ids)
    cited = set(brief.cited_chunk_ids()) & known
    skipped = {s.chunk_id: s.reason for s in brief.skipped_chunks if s.chunk_id in known and s.chunk_id not in cited}
    return {
        "available": True,
        "concepts": len(brief.concepts),
        "source_questions": len(brief.source_questions),
        "chunks": len(ids),
        "cited_chunks": len(cited),
        "skipped_chunks": len(skipped),
        "skipped_by_reason": dict(sorted(Counter(skipped.values()).items())),
        "unaccounted_chunks": len(known - cited - set(skipped)),
        "coverage": round(len(cited) / len(ids), 4) if ids else None,
    }


# --- entry point -------------------------------------------------------------------------------


@dataclass
class MetricInputs:
    """Optional context for ``compute_metrics``."""

    issues: Sequence[Any] = ()
    target_minutes: float | None = None
    chunk_ids: Iterable[str] | None = None
    manifest: AssetManifest | None = None
    usage: Sequence[UsageRecord] | None = None
    pricing_available: bool = True
    known_templates: Iterable[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def compute_metrics(sp: Screenplay, inputs: MetricInputs | None = None) -> dict[str, Any]:
    """All metric groups for one lecture. Deterministic; JSON-serialisable."""
    ctx = inputs or MetricInputs()
    structure = structure_metrics(sp)
    pacing = pacing_metrics(sp, ctx.target_minutes)
    metrics: dict[str, Any] = {
        "metrics_version": METRICS_VERSION,
        "schema": schema_check(sp.model_dump(mode="json")),
        "structure": structure,
        "lint": lint_metrics(ctx.issues, structure["scenes"]),
        "board": board_metrics(sp),
        "pacing": pacing,
        "objectives": objective_metrics(sp),
        "misconceptions": misconception_metrics(sp),
        "quiz": quiz_metrics(sp, pacing["est_minutes"]),
        "grounding": grounding_metrics(sp, ctx.chunk_ids),
        "media": media_metrics(sp),
        "manim": manim_metrics(sp, ctx.known_templates),
        "worked_examples": worked_example_metrics(sp),
        "cost": cost_metrics(ctx.usage, pricing_available=ctx.pricing_available),
    }
    if ctx.manifest is not None:
        metrics["audio"] = audio_metrics(sp, ctx.manifest)
    metrics.update(ctx.extra)
    return metrics


def flatten_metrics(metrics: Mapping[str, Any], prefix: str = "") -> dict[str, float]:
    """Numeric leaves as ``{"group.name": value}`` (booleans become 1.0/0.0; text/lists dropped)."""
    flat: dict[str, float] = {}
    for key, value in metrics.items():
        path = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(flatten_metrics(value, f"{path}."))
        elif isinstance(value, bool):
            flat[path] = 1.0 if value else 0.0
        elif isinstance(value, int | float) and not (isinstance(value, float) and math.isnan(value)):
            flat[path] = float(value)
    return flat


def get_metric(metrics: Mapping[str, Any], dotted: str) -> Any:
    """Look up ``"group.name"`` in a nested metrics dict (``None`` when absent)."""
    node: Any = metrics
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node
