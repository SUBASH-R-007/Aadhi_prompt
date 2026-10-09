"""Deterministic lint for screenplays (pure and fast; no I/O, no LLM).

Codes (ARCHITECTURE §7): ``board.too_many_items``, ``board.item_too_long``,
``board.item_unrevealed`` (info), ``beat.too_long``, ``beat.too_short``, ``scene.too_long``,
``quiz.missing_for_concepts``, ``quiz.answer_position_skew``, ``panel.missing_rationale``,
``concept.unused``, ``objective.untaught``, ``objective.unassessed``, ``misconception.untargeted``,
``figure.unknown``, ``example.blank_never_filled``, ``example.fill_without_pause``,
``manim.template_unknown``, ``manim.params_invalid``, ``manim.beats_steps_mismatch``,
``manim.code_forbidden``, ``narration.markup``, ``source.unknown_ref``, ``lecture.duration_mismatch``,
plus ``scene.type_disabled`` and ``panel.payload_missing``.

Source scoping (the lecture teaches the subject, never the source document's packaging):
``content.admin_leak`` (error, fixable) — narration, board, titles or quiz text talk about the source's
production ("this video", "segment 2", timecodes, "estimated duration", "prepared by", "in the next
video", "welcome back", "thank you for watching" …), a title carries a duration, clip number, course
code or person's name, or, when the ``IngestResult`` is given, the lecture repeats a person/administrative
value that source scoping removed from the header (an author's name, a course code) or that sits in a
header field that got past scoping; messages never quote such a value. A phrase is subject matter only
when the prose of the scoped source uses it (header lines and table rows never count).
``content.duplicate_intro`` (warning) — a second title scene, a second learning-objectives scene or a
scene title that nearly repeats an earlier one.

Presenter: ``layout.mascot_crowds_board`` (warning, not fixable by a rewrite) — Aadhi in the centre of
a board scene with code, a table or many items (``aadhi.pipeline.presenter_lint``).

Synchronisation: ``formula.variable_beat_invalid`` (warning, not fixable by a rewrite) — a formula legend
row names a beat (``FormulaVariable.beat_id``) that is not in the scene or comes before the formula's
reveal; the timeline ignores the anchor (``aadhi.pipeline.sync_lint``).

Lesson quality and consistency (``aadhi.pipeline.quality``, appended last): terminology variants and
casing, abbreviations, concept names, figure captions, formula symbols, code languages and indentation,
title casing, dense runs and board reading time. All are notes or warnings that never block saving and
never select a scene for a rewrite (only ``code.mixed_indentation`` is fixable, and only as a warning).

Hidden scenes (``SceneBase.hidden``: kept in the lecture, left out of the preview and the video): the checks
of what plays (quiz spacing and answer positions, coverage of concepts / objectives / misconceptions, the
lecture's length, repeated introductions, the pacing run of dense boards, and where abbreviations are first used
and spelled out) count only the scenes shown, and the length and a board's reading time count each scene's
minimum duration (``min_seconds``). A hidden scene's own findings are kept as notes (``info``: never in the
pre-render list, never a reason for a paid rewrite) beside ``scene.hidden`` (info). ``lecture.all_scenes_hidden``
(warning): every scene is hidden. ``chapter.all_scenes_hidden`` (info): every scene of a chapter is hidden
(e.g. its only scene), so the video has no such chapter.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from ..schemas.screenplay import (
    AIVideoScene,
    BoardItem,
    BoardItemKind,
    BoardScene,
    ChapterCardScene,
    ManimSpec,
    QuizScene,
    Screenplay,
    SimulationScene,
)
from . import integrations
from .base import GenerationOptions, IngestResult, Issue
from .presenter_lint import lint_mascot_layout
from .quality import COMPUTE as _COMPUTE_QUALITY
from .quality import lint_quality
from .richlite import narration_markup, to_plain
from .source_scope import admin_key_line, contact_value, header_line_value, looks_like_person
from .sync_lint import lint_sync
from .timing import scene_seconds, word_count

MAX_BOARD_ITEMS = 6
BEAT_MAX_WORDS = 60
BEAT_MAX_SENTENCES = 3
BEAT_MIN_WORDS = 3
SCENE_MAX_SECONDS = {"simulation": 120.0, "quiz_checkpoint": 90.0, "chapter_card": 15.0}
SCENE_MAX_SECONDS_DEFAULT = 150.0
DURATION_TOLERANCE = 0.25
FILL_MIN_PAUSE = 1.0
TEACHING_TYPES = ("content", "example", "simulation", "ai_video", "interactive", "title")
_CHUNK_ID = re.compile(r"^c\d{4,5}$")

# max characters (plain text) per board item kind / field
ITEM_LIMITS: dict[BoardItemKind, int] = {
    BoardItemKind.heading: 70,
    BoardItemKind.bullet: 140,
    BoardItemKind.paragraph: 220,
    BoardItemKind.definition: 260,
    BoardItemKind.formula: 160,
    BoardItemKind.callout_info: 200,
    BoardItemKind.callout_tip: 200,
    BoardItemKind.callout_warning: 200,
    BoardItemKind.misconception: 200,
    BoardItemKind.example_step: 160,
    BoardItemKind.takeaway: 160,
    BoardItemKind.figure: 200,
    BoardItemKind.table: 0,
    BoardItemKind.code: 0,
}
JUSTIFICATION_LIMIT = 260
CODE_MAX_LINES = 18
CODE_MAX_LINE_CHARS = 80
TABLE_MAX_ROWS = 6
TABLE_MAX_COLS = 4
TABLE_MAX_CELL = 40


def _issue(code: str, message: str, severity: str = "warning", scene: Any = None, beat_id: str | None = None,
           fixable: bool = True) -> Issue:
    return Issue(code=code, severity=severity, message=message, scene_id=getattr(scene, "id", scene),
                 beat_id=beat_id, source="lint", fixable=fixable)


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------


def _item_length_problem(item: BoardItem) -> str | None:
    k = item.kind
    if k == BoardItemKind.code:
        lines = (item.code or "").splitlines()
        if len(lines) > CODE_MAX_LINES:
            return f"{len(lines)} lines of code (max {CODE_MAX_LINES}); show only the essential part"
        long = max((len(x) for x in lines), default=0)
        if long > CODE_MAX_LINE_CHARS:
            return f"a code line has {long} characters (max {CODE_MAX_LINE_CHARS})"
        return None
    if k == BoardItemKind.table:
        rows, cols = len(item.rows or []), len(item.headers or [])
        if rows > TABLE_MAX_ROWS or cols > TABLE_MAX_COLS:
            return f"table is {rows}x{cols} (max {TABLE_MAX_ROWS} rows x {TABLE_MAX_COLS} columns)"
        cell = max((len(to_plain(c)) for r in (item.rows or []) for c in r), default=0)
        if cell > TABLE_MAX_CELL:
            return f"a table cell has {cell} characters (max {TABLE_MAX_CELL})"
        return None
    limit = ITEM_LIMITS.get(k, 200)
    n = len(to_plain(item.text)) + (len(to_plain(item.term or "")) if k == BoardItemKind.definition else 0)
    if k == BoardItemKind.figure:
        n = len(item.caption or "")
    if n > limit:
        return f"{k.value} has {n} characters (max {limit}); boards hold short phrases, the narration explains"
    if item.justification and len(to_plain(item.justification)) > JUSTIFICATION_LIMIT:
        return f"justification has {len(item.justification)} characters (max {JUSTIFICATION_LIMIT})"
    return None


def _lint_board(scene: BoardScene, figures: set[str]) -> list[Issue]:
    out: list[Issue] = []
    if len(scene.board) > MAX_BOARD_ITEMS:
        out.append(_issue("board.too_many_items",
                          f"The board has {len(scene.board)} items; keep it to {MAX_BOARD_ITEMS - 1} or fewer "
                          "(split the scene or merge points).", scene=scene))
    revealed = {b.board_item_id for b in scene.beats if b.board_item_id}
    filled = {b.fill_item_id for b in scene.beats if b.fill_item_id}
    for item in scene.board:
        problem = _item_length_problem(item)
        if problem:
            out.append(_issue("board.item_too_long", f"Board item {item.id}: {problem}.", scene=scene))
        if item.id not in revealed:
            out.append(_issue("board.item_unrevealed",
                              f"Board item {item.id} is not revealed by a beat; it is visible from the scene start.",
                              "info", scene=scene, fixable=False))
        if item.blank and item.id not in filled:
            out.append(_issue("example.blank_never_filled",
                              f"Blank step {item.id} is never filled; add a beat that fills it.", "error", scene=scene))
        if item.figure_id and item.figure_id not in figures:
            out.append(_issue("figure.unknown", f"Board item {item.id} shows unknown figure {item.figure_id!r}.",
                              "error", scene=scene))
    for idx, b in enumerate(scene.beats):
        if not b.fill_item_id:
            continue
        prev = scene.beats[idx - 1] if idx else None
        if prev is None or prev.pause_after < FILL_MIN_PAUSE:
            out.append(_issue("example.fill_without_pause",
                              f"Beat {b.id} fills {b.fill_item_id} without a thinking pause before it; give the "
                              f"previous beat pause_after >= {FILL_MIN_PAUSE:g} s and invite the learner to try.",
                              scene=scene, beat_id=b.id))
    return out


# ---------------------------------------------------------------------------
# Beats / narration
# ---------------------------------------------------------------------------

_SENTENCE = re.compile(r"[.!?।]+(?:\s|$)")


def _lint_beats(scene: Any) -> list[Issue]:
    out: list[Issue] = []
    for b in scene.all_beats():
        words = word_count(b.narration)
        sentences = len(_SENTENCE.findall(b.narration.strip())) or 1
        if words > BEAT_MAX_WORDS or sentences > BEAT_MAX_SENTENCES + 1:
            out.append(_issue("beat.too_long",
                              f"Beat {b.id} has {words} words / {sentences} sentences; keep one idea per beat "
                              f"(<= {BEAT_MAX_WORDS} words, 1-3 sentences).", scene=scene, beat_id=b.id))
        elif words < BEAT_MIN_WORDS and scene.type != "chapter_card":
            out.append(_issue("beat.too_short", f"Beat {b.id} has only {words} word(s).", "info", scene=scene, beat_id=b.id))
        for field_name, text in (("narration", b.narration), ("spoken", b.spoken or "")):
            found = narration_markup(text)
            if found:  # an error: the speech engine would read the symbols aloud
                out.append(_issue("narration.markup",
                                  f"Beat {b.id} {field_name} contains {', '.join(found)}; narration must be plain "
                                  "spoken text (say formulas in words).", "error", scene=scene, beat_id=b.id))
    return out


def _lint_refs(scene: Any, chunk_ids: set[str] | None) -> list[Issue]:
    refs: list[tuple[str | None, str]] = [(b.id, r) for b in scene.all_beats() for r in b.source_refs]
    if isinstance(scene, BoardScene):
        refs += [(None, r) for i in scene.board for r in i.source_refs]
    if isinstance(scene, QuizScene):
        refs += [(None, r) for r in scene.source_refs]
    if scene.intent:
        refs += [(None, r) for r in scene.intent.source_refs]
    bad = sorted({r for _, r in refs if not _CHUNK_ID.match(r) or (chunk_ids is not None and r not in chunk_ids)})
    if not bad:
        return []
    return [_issue("source.unknown_ref", f"Unknown source reference(s) {', '.join(bad[:6])}.", scene=scene)]


# ---------------------------------------------------------------------------
# Manim / panels / media
# ---------------------------------------------------------------------------


_MANIM_CODES = ("manim.template_unknown", "manim.params_invalid", "manim.beats_steps_mismatch", "manim.code_forbidden")


def _lint_manim(scene: Any, spec: ManimSpec, n_beats: int | None, where: str) -> list[Issue]:
    authoritative = integrations.manim_spec_problems(spec, n_beats)
    if authoritative is not None:  # the manim area's own checks (params, steps, AST guard, timing)
        out_: list[Issue] = []
        for problem in authoritative:
            code, _, msg = problem.partition(": ")
            if code not in _MANIM_CODES:
                code, msg = ("manim.params_invalid" if spec.template else "manim.code_forbidden"), problem
            out_.append(_issue(code, f"{where}: {msg}", "error", scene=scene))
        return out_
    out: list[Issue] = []
    if spec.template:
        if not integrations.templates_available():
            return out
        if integrations.get_template(spec.template) is None:
            return [_issue("manim.template_unknown", f"{where}: unknown Manim template {spec.template!r}.", "error", scene=scene)]
        model, problems = integrations.validate_template_params(spec.template, spec.params)
        if model is None or problems:
            msg = "; ".join(problems)[:300] or "invalid parameters"
            return [_issue("manim.params_invalid", f"{where}: template parameters are invalid: {msg}.", "error", scene=scene)]
        if n_beats is not None:
            steps = integrations.template_step_count(spec.template, spec.params)
            if steps is not None and steps != n_beats:
                out.append(_issue("manim.beats_steps_mismatch",
                                  f"{where}: the animation has {steps} steps but the scene has {n_beats} beats "
                                  "(one beat per step).", "error", scene=scene))
    elif spec.code:
        problems = integrations.check_manim_code(spec.code)
        if problems:
            out.append(_issue("manim.code_forbidden",
                              f"{where}: Manim code is not allowed: {'; '.join(problems[:3])}.", "error", scene=scene))
    return out


def panel_beat_count(scene: Any) -> int:
    """Beats during which the side panel is visible (from ``show_from_beat_id`` to the end)."""
    beats = scene.all_beats()
    shown_from = scene.side_panel.show_from_beat_id if scene.side_panel is not None else None
    idx = next((i for i, b in enumerate(beats) if b.id == shown_from), 0) if shown_from else 0
    return len(beats) - idx


def _lint_panel(scene: Any, figures: set[str], options: GenerationOptions | None) -> list[Issue]:
    p = scene.side_panel
    out: list[Issue] = []
    if p is None:
        return out
    if p.kind != "skill_tree" and not p.rationale.strip():
        out.append(_issue("panel.missing_rationale",
                          f"The {p.kind} side panel has no rationale; say why this visual helps learning "
                          "(or remove it).", scene=scene))
    if p.kind == "figure" and p.figure_id and not p.override_asset_key and p.figure_id not in figures:
        out.append(_issue("figure.unknown", f"Side panel shows unknown figure {p.figure_id!r}.", "error", scene=scene))
    if p.kind == "manim" and p.manim is not None:
        out.extend(_lint_manim(scene, p.manim, panel_beat_count(scene) if p.manim.template else None, "side panel"))
    if options is not None:
        disabled = (
            (p.kind == "manim" and not options.allow_manim)
            or (p.kind == "image" and not options.allow_generated_images and not p.override_asset_key)
            or (p.kind == "gif" and not options.allow_gifs)
        )
        if disabled:
            out.append(_issue("scene.type_disabled", f"The {p.kind} side panel is disabled in the generation options.",
                              scene=scene, fixable=True))
    return out


def _lint_media_scene(scene: Any, figures: set[str], options: GenerationOptions | None) -> list[Issue]:
    out: list[Issue] = []
    if isinstance(scene, SimulationScene) and not scene.override_asset_key:
        out.extend(_lint_manim(scene, scene.manim, len(scene.beats), "simulation"))
        if options is not None and not options.allow_manim:
            out.append(_issue("scene.type_disabled", "Simulation scenes are disabled in the generation options.", scene=scene))
    if isinstance(scene, AIVideoScene):
        if not scene.rationale.strip():
            out.append(_issue("panel.missing_rationale",
                              "This AI video scene has no rationale; real-world footage must add understanding.", scene=scene))
        if scene.fallback_figure_id and scene.fallback_figure_id not in figures:
            out.append(_issue("figure.unknown", f"Unknown fallback figure {scene.fallback_figure_id!r}.", "error", scene=scene))
    if scene.type == "interactive" and options is not None and not options.allow_interactive:
        out.append(_issue("scene.type_disabled", "Interactive scenes are disabled in the generation options.", scene=scene))
    return out


# ---------------------------------------------------------------------------
# Lecture-level checks
# ---------------------------------------------------------------------------


def _lint_quizzes(sp: Screenplay, options: GenerationOptions | None) -> list[Issue]:
    out: list[Issue] = []
    quizzes = [s for s in sp.scenes if isinstance(s, QuizScene)]
    include = options.include_quizzes if options is not None else True
    every = options.quiz_every_n_concepts if options is not None else 2
    core = {c.id for c in sp.concept_map if c.kind == "core"}
    if include and core:
        since: list[str] = []
        for s in sp.scenes:
            if isinstance(s, QuizScene):
                since = []
                continue
            if s.type in TEACHING_TYPES and s.concept_id in core and s.concept_id not in since:
                if len(since) >= every:
                    out.append(_issue("quiz.missing_for_concepts",
                                      f"{len(since)} core concepts ({', '.join(since)}) are taught without a retrieval "
                                      f"quiz; add a quiz_checkpoint before this scene (every {every} concepts).",
                                      scene=s, fixable=False))
                    since = []
                since.append(s.concept_id)
        if len(since) >= every:
            out.append(_issue("quiz.missing_for_concepts",
                              f"The lecture ends with {len(since)} core concepts ({', '.join(since)}) that are never "
                              "quizzed; add a quiz_checkpoint.", fixable=False))
    if len(quizzes) >= 3:
        positions = Counter(q.correct_index for q in quizzes)
        pos, n = positions.most_common(1)[0]
        if n == len(quizzes) or (len(quizzes) >= 5 and n / len(quizzes) > 0.6):
            out.append(_issue("quiz.answer_position_skew",
                              f"{n} of {len(quizzes)} quizzes have the correct answer in position {pos + 1}; "
                              "learners may spot the pattern.", fixable=False))
    return out


_ONLY_HIDDEN = "which the video leaves out"


def _coverage_sets(scenes: list[Any]) -> tuple[set[str], set[str], set[str], set[str]]:
    """(concepts taught, objectives taught, objectives assessed, misconceptions targeted) by ``scenes``."""
    concepts = {s.concept_id for s in scenes if s.concept_id}
    taught = {o for s in scenes if not isinstance(s, QuizScene) for o in s.objective_ids}
    assessed = {o for s in scenes if isinstance(s, QuizScene) for o in s.objective_ids}
    targeted = {m for s in scenes if isinstance(s, QuizScene) for m in s.option_misconception_ids if m}
    targeted |= {i.misconception_id for s in scenes if isinstance(s, BoardScene) for i in s.board if i.misconception_id}
    return concepts, taught, assessed, targeted


def _lint_coverage(sp: Screenplay, options: GenerationOptions | None,
                   hidden: frozenset[str] = frozenset()) -> list[Issue]:
    """Coverage by the scenes shown; what only ``hidden`` scenes cover is reported as such."""
    out: list[Issue] = []
    used_concepts, taught, assessed, targeted = _coverage_sets([s for s in sp.scenes if s.id not in hidden])
    h_concepts, h_taught, h_assessed, h_targeted = _coverage_sets([s for s in sp.scenes if s.id in hidden])
    for c in sp.concept_map:
        if c.kind == "core" and c.id not in used_concepts:
            msg = (f"Core concept {c.id!r} ({c.title}) is taught only by hidden scenes, {_ONLY_HIDDEN}."
                   if c.id in h_concepts else f"Core concept {c.id!r} ({c.title}) is not taught by any scene.")
            out.append(_issue("concept.unused", msg, fixable=False))
    include = options.include_quizzes if options is not None else True
    for o in sp.learning_objectives:
        if o.id not in taught:
            msg = (f"Objective {o.id!r} ({o.text[:80]}) is taught only by hidden scenes, {_ONLY_HIDDEN}."
                   if o.id in h_taught else f"Objective {o.id!r} ({o.text[:80]}) is not taught by any scene.")
            out.append(_issue("objective.untaught", msg, fixable=False))
        if include and o.id not in assessed:
            msg = (f"Objective {o.id!r} ({o.text[:80]}) is assessed only by hidden quizzes, {_ONLY_HIDDEN}."
                   if o.id in h_assessed else f"Objective {o.id!r} ({o.text[:80]}) is never assessed by a quiz.")
            out.append(_issue("objective.unassessed", msg, fixable=False))
    for m in sp.misconceptions:
        if m.id not in targeted:
            msg = (f"Misconception {m.id!r} is addressed only in hidden scenes, {_ONLY_HIDDEN}." if m.id in h_targeted
                   else f"Misconception {m.id!r} is never addressed (no misconception callout or quiz distractor).")
            out.append(_issue("misconception.untargeted", msg, fixable=False))
    return out


def lecture_estimate(sp: Screenplay) -> float:
    """Estimated length of the scenes shown (no intro): hidden scenes left out, each scene at least its
    ``min_seconds``. Equals ``timing.lecture_seconds`` for a lecture that uses neither."""
    return sum(max(scene_seconds(s, sp.language), s.min_seconds or 0.0) for s in sp.scenes if not s.hidden)


def _lint_duration(sp: Screenplay, options: GenerationOptions | None) -> list[Issue]:
    if options is None or all(s.hidden for s in sp.scenes):  # no scene shown: lecture.all_scenes_hidden says so
        return []
    est = lecture_estimate(sp)
    target = options.target_minutes * 60
    if abs(est - target) > DURATION_TOLERANCE * target:
        return [_issue("lecture.duration_mismatch",
                       f"Estimated length is {est / 60:.1f} min but the target is {options.target_minutes} min.",
                       fixable=False)]
    return []


# ---------------------------------------------------------------------------
# Source scoping: packaging / header leaks and repeated introductions
# ---------------------------------------------------------------------------

# Production and administrative language that never belongs in a lecture. A pattern with a group
# hinges on that word (video / clip / segment / part) and is skipped when the word is part of the
# subject matter (a lecture on video coding, line segments or clipper circuits); a pattern without
# one is skipped when the prose of the scoped source itself uses it ("SMEs" in a management lecture,
# "prepared by" in chemistry, a time range in a timetable example), so teaching content is never
# flagged. Header lines and table rows of the source never make a phrase content, "prepared by" is
# content only where the source does not follow it with a person's name, and sign-offs ("thank you
# for watching") are never content.
_TIMECODE = re.compile(r"\b\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\b")
_PREPARED_BY = re.compile(r"\b(?:prepared|reviewed)\s+by\b", re.I)
_SIGN_OFFS = (
    re.compile(r"\bthank(?:s|\s+you)\s+for\s+watching\b", re.I),
    re.compile(r"\bsee\s+you\s+(?:all\s+)?(?:in\s+the\s+next|next\s+time)\b", re.I),
)
PACKAGING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bthis\s+(video|clip)\b", re.I),
    re.compile(r"\b(?:in|of)\s+(?:the|this)\s+(video|clip|segment)\b", re.I),
    re.compile(r"\b(clip)\s*(?:no\.?\s*)?#?\d+\b", re.I),
    re.compile(r"\b(segment)\s*(?:no\.?\s*)?#?\d+\b", re.I),
    re.compile(r"\bpart\s+\d+\s+of\s+(?:the|this)\s+(video)\b", re.I),
    _TIMECODE,
    re.compile(r"\bestimated\s+duration\b", re.I),
    re.compile(r"\bsubject[\s-]+matter\s+experts?\b", re.I),
    re.compile(r"\bSMEs?\b", re.I),
    _PREPARED_BY,
    re.compile(r"\b(?:in|from|during)\s+(?:the|our)\s+(?:next|previous|last|earlier|upcoming|coming)\s+"
               r"(video|clip|segment|part)\b(?!\s+of\b)", re.I),
    re.compile(r"\b(video)\s*(?:no\.?\s*)?#?\d+\b", re.I),
    re.compile(r"\b(video|clip)\s+(?:duration|length|runs?|lasts?|running\s+time|run\s*time)\b", re.I),
    re.compile(r"\bwelcome\s+back\b", re.I),
    *_SIGN_OFFS,
)
_SUBJECT_NOUNS = {n: re.compile(rf"\b{n}", re.I) for n in ("video", "clip", "segment")}
_NAME_AFTER = re.compile(r"^[\s:\-–—]*((?:(?:dr|prof|mr|mrs|ms|er)\.?\s*)?[A-Z][\w.'’\-]*(?:\s+[A-Z][\w.'’\-]*){0,4})")
# administrative fragments in titles (title cards, scene and chapter titles); messages never quote them
_TITLE_DURATION = re.compile(r"[\[(]\s*(?:duration|time|length|approx\.?)?\s*[:\-]?\s*~?\d+(?:\.\d+)?\s*"
                             r"(?:s|secs?|seconds?|mins?|minutes?)\s*[\])]|\bduration\s*[:\-]\s*\d", re.I)
_TITLE_NUMBER = re.compile(r"\b(?:video|clip)\s*(?:no\.?|number|#)\s*:?\s*\d+", re.I)
_TITLE_CODE = re.compile(r"\b(?i:[A-Z]{2,5}-?\d{3,5}[A-Z]?|R-?20\d{2})\b|\b[A-Z]{2,5}\s\d{3,5}\b")
_TITLE_PERSON = re.compile(r"(?:\||\bby)\s*(?:dr|prof|mr|mrs|ms)\.?\s+[A-Z]", re.I)
SUBJECT_NOUN_MIN_MENTIONS = 3  # source mentions (outside packaging phrases) that make the word subject matter
LEAK_MIN_CHARS = 4
_KEY_VALUE = re.compile(r"^[^:：\n]{1,60}[:：]\s*(.+)$", re.S)
_KEY_BY = re.compile(r"\b(?:by|name)\s*[-–—:]?\s+(.+)$", re.I | re.S)
_HONORIFIC = re.compile(r"^(?:(?:dr|prof|mr|mrs|ms|miss|er|shri|smt|thiru|tmt|selvi)\.?\s+)+", re.I)
# Person-category header keys that name an institution or a role, not a person: their values ("Electronics and
# Communication Engineering", "Assistant Professor") are ordinary words in a lecture, so they are not tracked.
_INSTITUTION_KEY = re.compile(
    r"depart|\bdept\b|college|institut|universit|school|organi[sz]ation|campus|designation|^\s*faculty\s*$|\bhod\b|"
    r"head of|coordinator|branch|program", re.I)
_HOUSE_NAMES = re.compile(r"\b(?:aadhi|rajalakshmi)\b", re.I)  # the narrator and the college are named on purpose
_OBJECTIVES_TITLE = re.compile(
    r"^\s*(?:our\s+|today'?s\s+)?objectives?\s*[:.!]?\s*$"
    r"|\b(?:learning|lesson|session|lecture)\s+(?:objectives?|outcomes?|goals?)\b"
    r"|\bwhat\s+(?:you|we)(?:'ll|\s+will)\s+(?:learn|cover)\b"
    r"|\bwhat\s+(?:this|the|today'?s)\s+(?:video|clip|session|lesson|lecture|part)\s+(?:will\s+)?covers?\b",
    re.I,
)
NEAR_DUPLICATE_TITLE = 0.9  # difflib ratio between normalised scene titles


def strip_packaging(text: str) -> str:
    """``text`` with every packaging phrase removed (to judge what the subject matter is)."""
    for rx in PACKAGING_PATTERNS:
        text = rx.sub(" ", text or "")
    return text


def source_prose(source_text: str) -> str:
    """The scoped source without table rows and header-field lines ("SME Name: …"): its teaching prose."""
    return "\n".join(line for line in (source_text or "").splitlines() if line.strip() and not admin_key_line(line))


def _content_text(source_text: str) -> str:
    """The scoped source without the header fields that got past scoping (data tables stay)."""
    return "\n".join(line for line in (source_text or "").splitlines() if header_line_value(line) is None)


def _names_a_person(text: str) -> bool:
    m = _NAME_AFTER.match(text)
    return bool(m) and looks_like_person(m.group(1))


def content_patterns(source_text: str) -> frozenset[re.Pattern[str]]:
    """The word-free packaging patterns the scoped source's prose uses itself (so they are content there)."""
    if not source_text:
        return frozenset()
    prose = source_prose(source_text)
    out: set[re.Pattern[str]] = set()
    for rx in PACKAGING_PATTERNS:
        if rx.groups or rx is _TIMECODE or rx in _SIGN_OFFS:
            continue
        if rx is _PREPARED_BY:  # "Soap is prepared by heating…" is content, "prepared by Dr. X" is a credit
            if any(not _names_a_person(prose[m.end():m.end() + 80]) for m in rx.finditer(prose)):
                out.add(rx)
        elif rx.search(prose):
            out.add(rx)
    return frozenset(out)


def packaging_phrases(text: str, subject_nouns: set[str] | frozenset[str] = frozenset(), source_text: str = "",
                      content: frozenset[re.Pattern[str]] | None = None) -> list[str]:
    """Packaging phrases in ``text`` (lower-cased, unique, in pattern order).

    Patterns hinging on a word in ``subject_nouns`` are skipped; so are the patterns the scoped source
    (``source_text``) uses itself (``content``: precomputed ``content_patterns(source_text)``) and a
    time range that also occurs in it.
    """
    if content is None:
        content = content_patterns(source_text)
    found: list[str] = []
    for rx in PACKAGING_PATTERNS:
        if rx in content:
            continue
        for m in rx.finditer(text or ""):
            if rx.groups and m.group(1).lower() in subject_nouns:
                continue
            if rx is _TIMECODE and source_text:
                a, b = re.findall(r"\d{1,2}:\d{2}", m.group(0))[:2]
                if re.search(rf"(?<!\d){re.escape(a)}\s*[-–—]\s*{re.escape(b)}(?!\d)", source_text):
                    continue
            phrase = re.sub(r"\s+", " ", m.group(0)).strip().lower()
            if any(phrase in f for f in found):  # overlapping patterns: keep the longest phrase
                continue
            found = [f for f in found if f not in phrase] + [phrase]
    return found


def subject_nouns_in(topic_text: str, source_text: str = "") -> set[str]:
    """The words of ``_SUBJECT_NOUNS`` that are subject matter: named in ``topic_text`` (titles, concepts,
    glossary) or used at least ``SUBJECT_NOUN_MIN_MENTIONS`` times in the scoped source, packaging
    phrases ("in this video") not counted."""
    meta, src = strip_packaging(topic_text), strip_packaging(source_text)
    return {n for n, rx in _SUBJECT_NOUNS.items() if rx.search(meta) or len(rx.findall(src)) >= SUBJECT_NOUN_MIN_MENTIONS}


def subject_nouns(screenplay: Screenplay, source_text: str = "") -> set[str]:
    """``subject_nouns_in`` for a screenplay's topic, concepts and glossary."""
    return subject_nouns_in(" ".join([
        screenplay.subject_name, screenplay.unit_name, screenplay.session_title,
        *(f"{c.title} {c.summary}" for c in screenplay.concept_map), *(e.written for e in screenplay.lexicon),
    ]), source_text)


def visible_texts(scene: Any) -> list[tuple[str, str | None, str]]:
    """(where, beat id, plain text) for everything a learner hears or reads in ``scene``."""
    out: list[tuple[str, str | None, str]] = [("the scene title", None, scene.title or ""),
                                              ("the subtitle", None, scene.subtitle or "")]
    if isinstance(scene, ChapterCardScene):
        out.append(("the chapter label", None, scene.chapter_label))
    for b in scene.all_beats():
        out.append((f"beat {b.id}", b.id, b.narration))
        if b.spoken:
            out.append((f"beat {b.id}", b.id, b.spoken))
    if isinstance(scene, BoardScene):
        for i in scene.board:
            parts = [i.text, i.term or "", i.caption or "", i.justification or "", *(i.headers or []),
                     *(c for r in (i.rows or []) for c in r)]
            out.append((f"board item {i.id}", None, " \n ".join(to_plain(p) for p in parts if p)))
    if isinstance(scene, QuizScene):
        parts = [scene.question, *scene.options, scene.explanation, *scene.feedback_wrong]
        out.append(("the quiz text", None, " \n ".join(to_plain(p) for p in parts if p)))
    panel = scene.side_panel
    if panel is not None:
        parts = [panel.title or ""] + ([panel.quiz.question, *panel.quiz.options] if panel.quiz is not None else [])
        out.append(("the side panel", None, " \n ".join(to_plain(p) for p in parts if p)))
    return [(w, b, t) for w, b, t in out if t and t.strip()]


def header_values(text: str) -> list[str]:
    """Values of a removed header line worth looking for in the lecture.

    ``'SME Name: Dr. A. Kumar'`` -> ``['Dr. A. Kumar', 'A. Kumar']``; several ``key: value`` pairs in
    one line are split. A bare single word without a key ("Faculty") is not a value.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    m = _KEY_VALUE.match(text) or _KEY_BY.search(text)
    body = m.group(1) if m else text
    if m is None and len(body.split()) < 2:
        return []
    values: list[str] = []
    for part in [body, *re.split(r"\s*[,;|/&]\s*", body)]:
        inner = _KEY_VALUE.match(part) or _KEY_BY.search(part)
        part = (inner.group(1) if inner else part).strip(" .,;:-–—\"'()[]")
        for v in (part, _HONORIFIC.sub("", part).strip()):
            if v and v not in values:
                values.append(v)
    return values


def _trackable(value: str, category: str) -> bool:
    if len(value) < LEAK_MIN_CHARS:
        return False
    compact = re.sub(r"\s", "", value)
    if compact.isdigit() and len(compact) < 6:  # a bare year or number may well be content
        return False
    if category == "admin":  # codes, dates, versions, e-mail addresses; not department or college names
        return bool(re.search(r"\d|@", value))
    return bool(re.search(r"[^\W\d_]", value)) or len(compact) >= 6


def _value_regex(value: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + r"\s+".join(re.escape(w) for w in value.split()) + r"(?!\w)", re.I)


def header_needles(ingest: IngestResult, screenplay: Screenplay, source_text: str) -> list[tuple[str, re.Pattern[str]]]:
    """(category, regex) for the person/admin header values source scoping removed, plus the values of
    header fields that got past scoping (a "| 1 | Name of the SME | Dr. X |" row left in the source).

    A person value is tracked only when it is a person's name or a contact detail. A value that also
    occurs in the scoped teaching content (a scientist named in the topic) or in the title-card
    metadata (subject, unit, session) is content and is not tracked, with one exception: a name of
    two or more words that scoping removed from a person field (SME, author, reviewer) is always
    tracked, even when the narration also names that person ("Dr. X will now demonstrate this"), and
    so is the honorific with each part of it ("Dr. Meena"). A single word is never tracked that way:
    it may be a scientist's name the lesson uses.
    """
    dm = ingest.document_meta
    meta = " \n ".join([screenplay.subject_name, screenplay.unit_name, screenplay.session_number,
                        screenplay.session_title, dm.subject_name, dm.unit_name, dm.session_number, dm.session_title])
    content = _content_text(source_text)
    out: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()

    def add(category: str, value: str, *, header_name: bool) -> None:
        if value.lower() in seen or not _trackable(value, category) or _HOUSE_NAMES.search(value):
            return
        if category == "person" and not (looks_like_person(value) or contact_value(value)):
            return
        seen.add(value.lower())
        rx = _value_regex(value)
        if rx.search(meta):
            return
        if (header_name and len(value.split()) >= 2) or not rx.search(content):
            out.append((category, rx))

    def track(category: str, text: str, *, header_name: bool = False) -> None:
        for value in header_values(text):
            add(category, value, header_name=header_name)
            honorific = _HONORIFIC.match(value)
            if header_name and honorific and looks_like_person(value):
                title = value[:honorific.end()].strip()
                for part in _HONORIFIC.sub("", value).replace(".", " ").split():
                    if len(part) >= 3 and part.isalpha():
                        add(category, f"{title} {part}", header_name=True)

    for item in ingest.excluded:
        if item.category not in ("person", "admin"):
            continue
        key = item.text.split(":", 1)[0] if ":" in item.text else ""
        if item.category == "person" and key and _INSTITUTION_KEY.search(key):
            continue
        track(item.category, item.text, header_name=item.category == "person" and bool(key))
    for line in (source_text or "").splitlines():
        found = header_line_value(line)
        if found is not None and found[0] in ("person", "admin"):
            track(found[0], f"value: {found[1]}")
    return out


def title_admin(text: str, source_text: str = "") -> list[str]:
    """What administrative detail a title carries (a duration, a clip number, a course code that the source
    does not teach (checked only when the source text is known), a person's name); descriptions only, never
    the value."""
    found: list[str] = []
    if _TITLE_DURATION.search(text or ""):
        found.append("a video duration")
    if _TITLE_NUMBER.search(text or ""):
        found.append("a video or clip number")
    if source_text and any(m.group(0).lower() not in source_text.lower() for m in _TITLE_CODE.finditer(text or "")):
        found.append("a course code or regulation")
    if _TITLE_PERSON.search(text or ""):
        found.append("a person's name")
    return found


def _places(places: list[str]) -> str:
    places = list(dict.fromkeys(places))
    shown = ", ".join(places[:4])
    return shown + (f" and {len(places) - 4} more place(s)" if len(places) > 4 else "")


_PACKAGING_FIX = ('Learners see one continuous lesson: say "today" or "in this session" and drop clip, segment, '
                  "timing, duration and author references.")
_PERSON_FIX = ("an author's name or contact detail from the source's header appears in {where}; the lecture never "
               "names the source's authors, reviewers or faculty, so remove it.")
_ADMIN_FIX = ("an administrative detail from the source's header (a code, date or version) appears in {where}; "
              "remove it.")


def lint_source_leaks(screenplay: Screenplay, ingest: IngestResult | None = None,
                      options: GenerationOptions | None = None) -> list[Issue]:
    """``content.admin_leak`` issues (messages never quote a removed header value).

    Title-card fields the teacher typed in ``options`` are the teacher's choice and are not checked.
    """
    source_text = ""
    if ingest is not None:
        source_text = " \n ".join([*(c.text for c in ingest.chunks), *(f.caption for f in ingest.figures)])
    nouns = subject_nouns(screenplay, source_text)
    content = content_patterns(source_text)
    needles = header_needles(ingest, screenplay, source_text) if ingest is not None else []
    out: list[Issue] = []
    for scene in screenplay.scenes:
        packaging: list[tuple[str, str | None]] = []
        phrases: list[str] = []
        hits: dict[str, list[tuple[str, str | None]]] = {"person": [], "admin": []}
        for where, beat_id, text in visible_texts(scene):
            found = packaging_phrases(text, nouns, source_text, content)
            if found:
                packaging.append((where, beat_id))
                phrases += [p for p in found if p not in phrases]
            for category, rx in needles:
                if rx.search(text):
                    hits[category].append((where, beat_id))
        if phrases:
            beat = next((b for _, b in packaging if b), None)
            out.append(_issue("content.admin_leak",
                              f"Source packaging in {_places([w for w, _ in packaging])}: "
                              f"{', '.join(repr(p) for p in phrases[:5])}. " + _PACKAGING_FIX,
                              "error", scene=scene, beat_id=beat))
        for category, fix in (("person", _PERSON_FIX), ("admin", _ADMIN_FIX)):
            if hits[category]:
                where = _places([w for w, _ in hits[category]])
                beat = next((b for _, b in hits[category] if b), None)
                msg = fix.format(where=where)
                out.append(_issue("content.admin_leak", msg[0].upper() + msg[1:], "error", scene=scene, beat_id=beat))
        in_title = title_admin(scene.title or "", source_text)
        if in_title:
            out.append(_issue("content.admin_leak",
                              f"The scene title carries {', '.join(in_title)} from the source document; give the scene "
                              "a title that names its idea.", "error", scene=scene))
    typed = {f: getattr(options, f, None) for f in ("subject_name", "unit_name", "session_title")} if options else {}
    lecture_texts = [(where, text) for f, where, text in (
        ("subject_name", "the subject name", screenplay.subject_name),
        ("unit_name", "the unit name", screenplay.unit_name),
        ("session_title", "the session title", screenplay.session_title),
    ) if text and text != typed.get(f)]
    lecture_texts += [(f"chapter title {ch.id}", ch.title) for ch in screenplay.chapters]
    for where, text in lecture_texts:
        found = packaging_phrases(text, nouns, source_text, content)
        if found:
            out.append(_issue("content.admin_leak",
                              f"Source packaging in {where}: {', '.join(repr(p) for p in found[:5])}; name the topic "
                              "instead.", "error", fixable=False))
        in_title = title_admin(text, source_text)
        if in_title:
            out.append(_issue("content.admin_leak",
                              f"{where[0].upper() + where[1:]} carries {', '.join(in_title)} from the source document; "
                              "name the topic instead.", "error", fixable=False))
        elif any(rx.search(text) for _, rx in needles):
            out.append(_issue("content.admin_leak",
                              f"A detail from the source's header (an author's name, a code or a date) appears in "
                              f"{where}; remove it.", "error", fixable=False))
    return out


def _title_key(title: str) -> str:
    return re.sub(r"[\W_]+", " ", to_plain(title or "").lower()).strip()


def lint_intros(screenplay: Screenplay) -> list[Issue]:
    """``content.duplicate_intro``: a second opening title scene, a second learning-objectives scene
    or a teaching scene whose title nearly repeats an earlier one."""
    out: list[Issue] = []
    first_title: str | None = None
    first_objectives: str | None = None
    seen: list[tuple[str, str]] = []
    for s in screenplay.scenes:
        reasons: list[str] = []
        if s.type == "title":
            if first_title is None:
                first_title = s.id
            else:
                reasons.append(f"it is a second opening title scene (the lecture already opens with {first_title})")
        heading = next((i.text for i in s.board if i.kind == BoardItemKind.heading), "") if isinstance(s, BoardScene) else ""
        if any(_OBJECTIVES_TITLE.search(to_plain(t)) for t in (s.title or "", heading) if t):
            if first_objectives is None:
                first_objectives = s.id
            else:
                reasons.append(f"it repeats the learning objectives already given in {first_objectives}")
        if s.type not in ("title", "chapter_card", "quiz_checkpoint"):
            key = _title_key(s.title)
            if key:
                twin = next((sid for sid, k in seen if k == key or (
                    min(len(k), len(key)) >= 8 and difflib.SequenceMatcher(None, k, key).ratio() >= NEAR_DUPLICATE_TITLE
                )), None)
                if twin:
                    reasons.append(f"its title nearly repeats the title of {twin}")
                seen.append((s.id, key))
        if reasons:
            out.append(_issue("content.duplicate_intro",
                              f"Scene {s.id} repeats earlier material: {'; '.join(reasons)}. A continuous lesson "
                              "introduces the session once; merge this scene or make it teach something new.",
                              scene=s))
    return out


def lint_scene(scene: Any, screenplay: Screenplay, options: GenerationOptions | None = None,
               chunk_ids: set[str] | None = None) -> list[Issue]:
    """Scene-local lint issues."""
    figures = {f.id for f in screenplay.figures}
    out: list[Issue] = []
    if isinstance(scene, BoardScene):
        out.extend(_lint_board(scene, figures))
    out.extend(_lint_beats(scene))
    out.extend(_lint_refs(scene, chunk_ids))
    out.extend(_lint_panel(scene, figures, options))
    out.extend(_lint_media_scene(scene, figures, options))
    out.extend(lint_sync(scene))  # authored sync anchors (aadhi.pipeline.sync_lint)
    out.extend(lint_mascot_layout(scene))  # Aadhi's position vs the board (aadhi.pipeline.presenter_lint)
    limit = SCENE_MAX_SECONDS.get(scene.type, SCENE_MAX_SECONDS_DEFAULT)
    est = scene_seconds(scene, screenplay.language)
    if est > limit:
        out.append(_issue("scene.too_long",
                          f"Scene is about {est:.0f} s (max {limit:.0f} s); split it so each scene has one focus.",
                          scene=scene))
    return out


def _chapter_scenes(sp: Screenplay) -> list[tuple[str, list[str]]]:
    """(title, scene ids) of each chapter: ``Screenplay.chapters`` (their ``scene_ids`` and the scenes naming
    them, as ``compose.timeline.build_chapters`` reads them), else each chapter card with the scenes after it."""
    if sp.chapters:
        out: list[tuple[str, list[str]]] = []
        for ch in sp.chapters:
            ids = set(ch.scene_ids) | {s.id for s in sp.scenes if s.chapter_id == ch.id}
            out.append((ch.title, [s.id for s in sp.scenes if s.id in ids]))
        return out
    runs: list[tuple[str, list[str]]] = []
    for s in sp.scenes:
        if isinstance(s, ChapterCardScene):
            runs.append(((s.title or s.chapter_label or "").strip() or "Chapter", [s.id]))
        elif runs:
            runs[-1][1].append(s.id)
    return runs


def hidden_ids(screenplay: Screenplay | dict[str, Any] | None) -> frozenset[str]:
    """Ids of the hidden scenes of a Screenplay or of a stored screenplay document (empty when there is none)."""
    if isinstance(screenplay, Screenplay):
        return frozenset(s.id for s in screenplay.scenes if s.hidden)
    scenes = screenplay.get("scenes") if isinstance(screenplay, dict) else None
    if not isinstance(scenes, list):
        return frozenset()
    return frozenset(str(s["id"]) for s in scenes if isinstance(s, dict) and s.get("id") and s.get("hidden") is True)


def hidden_as_notes(issues: Iterable[Any], hidden: Iterable[str]) -> list[Any]:
    """Each issue (a stored dict or an ``Issue``) of a ``hidden`` scene as a note (severity ``info``, as a copy); the
    others unchanged. Stored issues keep their real severity: this is applied when they are served or counted, so
    showing the scene again brings the severity back."""
    hidden = frozenset(hidden)
    if not hidden:
        return list(issues)
    out: list[Any] = []
    for i in issues:
        sid = i.get("scene_id") if isinstance(i, dict) else getattr(i, "scene_id", None)
        sev = i.get("severity") if isinstance(i, dict) else getattr(i, "severity", None)
        if sid in hidden and sev != "info":
            i = {**i, "severity": "info"} if isinstance(i, dict) else i.model_copy(update={"severity": "info"})
        out.append(i)
    return out


def lint_hidden(screenplay: Screenplay, issues: list[Issue]) -> list[Issue]:
    """``issues`` with every finding of a hidden scene kept as a note, plus the notes and the warning about hidden
    scenes (see the module docstring). ``issues`` itself when no scene is hidden."""
    hidden = hidden_ids(screenplay)
    if not hidden:
        return issues
    out = hidden_as_notes(issues, hidden)
    for s in screenplay.scenes:
        if s.id in hidden:
            out.append(_issue("scene.hidden",
                              "This scene is hidden: it stays in the lecture but is left out of the preview, the video, "
                              "the chapters and the subtitles. Its other findings are shown as notes until you show it "
                              "again.", "info", scene=s, fixable=False))
    if len(hidden) == len(screenplay.scenes):
        out.append(_issue("lecture.all_scenes_hidden",
                          "Every scene is hidden, so the preview and the video show only the opening titles. Show at "
                          "least one scene.", fixable=False))
        return out
    for title, ids in _chapter_scenes(screenplay):
        if ids and all(sid in hidden for sid in ids):
            what = "its only scene is" if len(ids) == 1 else f"all {len(ids)} of its scenes are"
            out.append(_issue("chapter.all_scenes_hidden",
                              f"The chapter {title!r} is left out of the video: {what} hidden.", "info",
                              scene=ids[0], fixable=False))
    return out


def lint(screenplay: Screenplay, options: GenerationOptions | None = None, *, chunk_ids: set[str] | None = None,
         ingest: IngestResult | None = None, quality: Any = _COMPUTE_QUALITY) -> list[Issue]:
    """All lint issues for a screenplay.

    ``chunk_ids`` enables exact source-ref checks (taken from ``ingest`` when not given); ``ingest``
    also enables the check for header values (an author's name, a course code) that source scoping
    removed and that must not reappear in the lecture. ``quality``: the screenplay's quality analysis when
    the caller already has it (``quality.analysis_of``; POST /lint shares it with the quality report), so it
    is not computed twice; ``None``: the analysis failed (already logged), so the quality family is skipped.
    """
    if chunk_ids is None and ingest is not None and ingest.chunks:
        chunk_ids = {c.id for c in ingest.chunks}
    hidden = frozenset(s.id for s in screenplay.scenes if s.hidden)
    # What plays: the screenplay without its hidden scenes (the screenplay itself when none is hidden)
    shown = screenplay.model_copy(update={"scenes": [s for s in screenplay.scenes if not s.hidden]}) if hidden \
        else screenplay
    out: list[Issue] = []
    for scene in screenplay.scenes:
        out.extend(lint_scene(scene, screenplay, options, chunk_ids))
    out.extend(_lint_quizzes(shown, options))
    out.extend(_lint_coverage(screenplay, options, hidden))
    out.extend(_lint_duration(screenplay, options))
    out.extend(lint_source_leaks(screenplay, ingest, options))
    out.extend(lint_intros(shown))
    out.extend(lint_quality(screenplay, quality))
    return lint_hidden(screenplay, out)


def scenes_needing_repair(issues: list[Issue]) -> dict[str, list[Issue]]:
    """Scene id -> the issues a scene rewrite should fix.

    Only fixable *errors* (any source) and fixable *critic warnings* trigger a rewrite: each rewrite is
    a paid model call, and lint warnings (length, pacing) are advice for the teacher, not defects.
    The selected scene's other fixable warnings are passed along so the rewrite can address them too.
    """
    selected: set[str] = set()
    for i in issues:
        if i.scene_id and i.fixable and (i.severity == "error" or (i.source == "critic" and i.severity == "warning")):
            selected.add(i.scene_id)
    out: dict[str, list[Issue]] = {}
    for i in issues:
        if i.scene_id in selected and i.fixable and i.severity != "info":
            out.setdefault(i.scene_id, []).append(i)
    return out
