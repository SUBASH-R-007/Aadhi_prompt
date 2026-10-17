"""The visual events of a Dynamic scene (DYNAMIC_VIDEO_DESIGN §4.8 choreography grammar, §1.4 anchored events, R-15,
R-16, R-17, R-26, R-35; B2 plan §1.5-§1.7, §2 "R-16 holds").

Python decides WHEN, the stage decides HOW (``schemas.timeline.VisualEvent``). This module turns the timing data a
``TimedScene`` already carries - its beats (``TimedBeat.board_item_id`` / ``fill_item_id``, word timings) and its
word-anchored cues (``compose.sync.scene_sync_cues``: ``var`` / ``emphasis`` / ``output`` / ``focus``) - into the
events the scene's template draws:

* ``reveal`` per board item on its revealing beat, **120 ms before the anchor** (R-17, :data:`REVEAL_LEAD`); exactly
  the time the stage computes for a beat reveal without an event (``web/js/dynamic/state.js`` ``itemTimings``:
  ``max(0, beat.start - REVEAL_LEAD)``), so writing it changes no frame. An item no beat reveals gets **no** event:
  the stage's staggered scene entrance (``INITIAL_DELAY`` / ``INITIAL_STAGGER``) stays its own.
* ``fill`` per blank item filled by a beat, at the beat start (the stage's ``plan.fillAt``).
* ``var`` per formula symbol the narration names: B1's ``var:<n>`` sync cue mapped to the §1.7 target
  ``<item>:var:<symbol_latex>`` (plan §1.7; ``formula_focus.variableTimes`` reads both and times them the same).
* ``draw`` on a formula item once every named symbol is out (``formula_focus.boxDrawAt``: the result's box).
* ``cue_term`` on the first :data:`TERM_MENTIONS` narrated mentions of a ``[[term]]`` key term (R-35), on its anchor
  word (no lead: only reveals lead, R-17).
* ``look_here`` for the pointing cues - a side-panel ``focus`` pulse, an authored highlight (``emphasis``) - and a
  **walk** through the content during a hold, so a hold always has a cue moving (R-16).
* ``output`` when the narration says what a terminal printed (the ``output`` sync cue).
* ``draw`` on a chart's bar or point (``panel:bar:<s>:<i>`` / ``panel:point:<s>:<i>``, or a graph's marked point) on the
  first narrated mention of its category or label, 120 ms before the word (R-17), so the mark is revealed on its beat
  (§4.3 ``chart_build``); a later mention is a ``look_here`` candidate on it (§4.3 "the named bar takes
  cue.lookHere"). ``charts.mark_targets`` names the marks.

Every cue (``cue_term`` and ``look_here``) passes R-26's budget - at most :data:`MAX_CONCURRENT_CUES` on screen, at
most one new cue per :data:`CUE_MIN_GAP` seconds, and never the same target twice within :data:`CUE_REPEAT_GAP` -
with the word-anchored cues taking precedence over a generated hold walk. R-26's punch-zoom clause is not binding
here: B2 emits no punch-zoom (a ``push_ok`` template's ``look_here`` is the 3% camera push of §4.3, not §4.8's 1.10
zoom), so the two limits above are the whole budget.

Only events the template **consumes** are written (plan §1.6 ``TemplateSpec.consumes``, the kind *and* the target's
form, B2c review CG-5): the caller hands in ``accepts`` (``spec.consumes.accepts``), so this module never imports
``templates.py`` and ``templates.py`` never imports it (plan §1.6). ``direct.plan`` is the one caller.

Pure and deterministic: a function of the timed scene alone (plus the lesson-wide :class:`TermBudget`), no I/O, no
randomness, no clock. Every time is rounded to milliseconds and clamped inside the scene, so
``TimedScene.check_inside`` always passes. Imported only inside the Dynamic gate, never by a classic build.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Final

from ...pipeline.richlite import tokenize as rich_spans
from ...schemas.screenplay import BoardItem
from ...schemas.timeline import TimedScene, VisualEvent
from ..sync import MAX_PHRASE_TOKENS, Stream, stem, tokens, word_stream
from .charts import mark_targets

# R-17: "reveals start 120 ms before word onset" (web/js/dynamic/state.js REVEAL_LEAD, board_generic's rule).
REVEAL_LEAD: Final = 0.12
# R-26: at most one new cue per 2 s and at most 2 on screen at once.
CUE_MIN_GAP: Final = 2.0
MAX_CONCURRENT_CUES: Final = 2
# How long a cue stays without ``dur`` (web/js/dynamic/cues.js ``CUE_HOLD``): what the budget counts as "on screen".
CUE_HOLD: Final = 2.0
# The same target never carries a cue twice within this (cues.js' "no repeat within 4 s; Python's budget").
CUE_REPEAT_GAP: Final = 4.0
# R-35: a key term is marked on its first two mentions only (counted over the whole lesson).
TERM_MENTIONS: Final = 2
# A term cue waits this long after its item's entrance, so the sweep never runs during the entrance.
TERM_AFTER_ENTER: Final = 0.2
# A cue with less than this left in the scene is not worth drawing (its sweep would be cut off).
CUE_MIN_VISIBLE: Final = 0.3
# How long after its last named symbol a formula's box draws (§4.3: "the result is boxed"), so the box follows the
# symbol rather than entering with it (§4.8: one thing at a time).
DRAW_AFTER_VAR: Final = 0.4
# R-16 hold walk: a look_here every HOLD_WALK_GAP seconds while the hold lasts, each held HOLD_WALK_SECONDS.
HOLD_WALK_GAP: Final = 3.0
HOLD_WALK_SECONDS: Final = 2.0
# Bounds the extra render states a scene gains (compose.sync.MAX_CUES_PER_SCENE is the same bound on sync cues).
MAX_CUES_PER_SCENE: Final = 24
# The stage's staggered entrance of an item no beat reveals (state.js INITIAL_DELAY): when its terms are on screen.
INITIAL_DELAY: Final = 0.35

# plan §1.7: ``panel`` and ``option`` are reserved, so a board item with that id is never a target (reports/b.json,
# "events.py never emits the reserved target ids panel / option"); and an id outside the Slug grammar is not one either.
RESERVED_TARGET_WORDS: Final = frozenset({"panel", "option"})
_SLUG = re.compile(r"[a-z0-9][a-z0-9_\-]{0,63}")
# The rich-lite fields a key term is counted over, in the order the stage's ``itemEl`` hands its ``.rt-k`` spans to
# cues.js (plan §1.7's ``<item>:term:<n>``: term, text, justification, caption, each field's terms in reading order).
TERM_FIELDS: Final = ("term", "text", "justification", "caption")
# Ties in the returned order (events at the same t): the order the stage reads them in.
_KIND_ORDER: Final = ("reveal", "fill", "var", "draw", "output", "count", "countdown", "answer", "cue_term",
                      "look_here", "exit")

Accepts = Callable[[str, str | None], bool]


# --- the lesson-wide key-term budget (R-35) -------------------------------------------------------------------------


@dataclass
class TermBudget:
    """How often each key term has been marked in the lesson so far (R-35: the first two narrated mentions only).

    The key is the term's normalised phrase, so the same term written differently in two scenes shares its count.
    One budget per video; :func:`scene_events` without one marks every scene as if it were the first."""

    limit: int = TERM_MENTIONS
    counts: dict[str, int] = field(default_factory=dict)

    def take(self, key: str) -> bool:
        """Claim one mention of ``key``; False when the term has had its :data:`TERM_MENTIONS` already."""
        used = self.counts.get(key, 0)
        if used >= self.limit:
            return False
        self.counts[key] = used + 1
        return True


# --- key terms of a board item --------------------------------------------------------------------------------------


def term_phrases(item: BoardItem) -> list[tuple[int, list[str]]]:
    """``(index, phrase)`` of every ``[[term]]`` of ``item``, in plan §1.7's order (:data:`TERM_FIELDS`).

    ``index`` is the term's ordinal within the item, which is what ``<item>:term:<n>`` names; ``phrase`` is its
    normalised tokens (``compose.sync``'s), which is what the narration is matched against. Only ``[[keyword]]``
    spans count (the stage's ``.rt-k``), never ``**bold**`` (``.rt-b``), and a term of one-letter words alone is
    dropped: it cannot be heard (``compose.sync`` makes the same refusal)."""
    out: list[tuple[int, list[str]]] = []
    n = 0
    for field_name in TERM_FIELDS:
        text = getattr(item, field_name, None)
        if not isinstance(text, str) or not text:
            continue
        for span in rich_spans(text):
            if span.kind != "keyword":
                continue
            phrase = tokens(span.text)[:MAX_PHRASE_TOKENS]
            if phrase and any(len(t) >= 2 for t in phrase):
                out.append((n, phrase))
            n += 1  # every .rt-k span takes an ordinal, drawn or not, so an index never shifts
    return out


def _matches(norm: str, stemmed: str, key: str) -> bool:
    """``compose.sync._same``: a spoken token names ``key`` exactly, or in its plural form when both are ASCII."""
    return norm == key or (key.isascii() and stemmed == stem(key))


def phrase_times(stream: Stream, phrase: Sequence[str]) -> list[tuple[float, str]]:
    """``(start, spoken words)`` of every occurrence of ``phrase`` in the scene's spoken-word stream
    (``compose.sync.word_stream``), in time order. Occurrences never overlap: the scan continues after the one it
    found. The spoken words are the Studio's (``VisualEvent.words``), never shown to a student."""
    n = len(phrase)
    if not n or len(stream) < n:
        return []
    out: list[tuple[float, str]] = []
    i = 0
    while i + n <= len(stream):
        window = stream[i:i + n]
        if all(_matches(t.norm, t.stem, phrase[k]) for k, t in enumerate(window)):
            out.append((float(window[0].start), " ".join(t.raw for t in window)[:80]))
            i += n
        else:
            i += 1
    return out


# --- generation -----------------------------------------------------------------------------------------------------


def _targetable(item_id: str) -> bool:
    return bool(_SLUG.fullmatch(item_id)) and item_id not in RESERVED_TARGET_WORDS


def _entrances(scene: TimedScene) -> dict[str, float]:
    """When each board item enters: its revealing beat's start, else the stage's staggered scene entrance (the
    entrance a cue must wait for; state.js ``itemTimings``)."""
    out: dict[str, float] = {}
    for beat in scene.beats:
        if beat.board_item_id and beat.board_item_id not in out:
            out[beat.board_item_id] = max(0.0, float(beat.start))
    for item in scene.board:
        out.setdefault(item.id, INITIAL_DELAY)
    return out


def _reveals(scene: TimedScene, accepts: Accepts) -> list[VisualEvent]:
    """A ``reveal`` per item a beat reveals, 120 ms before the beat (R-17), and a ``fill`` per blank item filled."""
    out: list[VisualEvent] = []
    seen: set[str] = set()
    filled: set[str] = set()
    for beat in sorted(scene.beats, key=lambda b: (b.start, b.index)):
        item = beat.board_item_id
        if item and item not in seen and _targetable(item) and accepts("reveal", item):
            seen.add(item)
            out.append(VisualEvent(t=max(0.0, round(float(beat.start) - REVEAL_LEAD, 3)), kind="reveal", target=item,
                                   anchored=True))
        fill = beat.fill_item_id
        if fill and fill not in filled and _targetable(fill) and accepts("fill", fill):
            filled.add(fill)
            out.append(VisualEvent(t=max(0.0, round(float(beat.start), 3)), kind="fill", target=fill, anchored=True))
    return out


def _symbols(scene: TimedScene) -> dict[str, list[str]]:
    """The symbols of each formula item, in legend order (B1's ``var:<n>`` part is an index into this)."""
    return {i.id: [v.symbol_latex for v in i.variables] for i in scene.board if i.variables}


def _vars(scene: TimedScene, accepts: Accepts) -> list[VisualEvent]:
    """A ``var`` per ``var:<n>`` sync cue, on the §1.7 target ``<item>:var:<symbol>``, 120 ms before its word (the
    time ``formula_focus.variableTimes`` gives the sync cue it replaces, so the visual does not move)."""
    symbols = _symbols(scene)
    out: list[VisualEvent] = []
    for cue in scene.sync_cues:
        if cue.kind != "var" or not cue.item_id or not cue.part or not cue.part.startswith("var:"):
            continue
        index = cue.part[4:]
        names = symbols.get(cue.item_id, [])
        if not index.isdigit() or int(index) >= len(names) or not _targetable(cue.item_id):
            continue
        target = f"{cue.item_id}:var:{names[int(index)]}"
        if not accepts("var", target):
            continue
        out.append(VisualEvent(t=max(0.0, round(float(cue.start) - REVEAL_LEAD, 3)), kind="var", target=target,
                               anchored=True, words=cue.words))
    return out


def _draws(scene: TimedScene, var_events: Sequence[VisualEvent], accepts: Accepts) -> list[VisualEvent]:
    """A ``draw`` on a formula item :data:`DRAW_AFTER_VAR` after its last named symbol (§4.3 formula_focus: "the
    result is boxed"; ``boxDrawAt``). No ``var`` event means no box of its own: the box comes with the card."""
    latest: dict[str, float] = {}
    for e in var_events:
        item = (e.target or "").split(":")[0]
        if item:
            latest[item] = max(latest.get(item, 0.0), e.t)
    out: list[VisualEvent] = []
    for item in sorted(latest):
        if accepts("draw", item):
            out.append(VisualEvent(t=round(latest[item] + DRAW_AFTER_VAR, 3), kind="draw", target=item))
    return out


def _outputs(scene: TimedScene, accepts: Accepts) -> list[VisualEvent]:
    """An ``output`` on the panel when the narration says what the program printed (the ``output`` sync cue)."""
    out: list[VisualEvent] = []
    for cue in scene.sync_cues:
        if cue.kind == "output" and accepts("output", "panel"):
            out.append(VisualEvent(t=max(0.0, round(float(cue.start), 3)), kind="output", target="panel",
                                   anchored=True, words=cue.words))
    return out


@dataclass(frozen=True)
class _Cue:
    """A candidate cue and, for a ``cue_term``, the key term it marks (the lesson-wide budget's key, R-35)."""

    event: VisualEvent
    term: str | None = None


def _term_cues(scene: TimedScene, accepts: Accepts) -> list[_Cue]:
    """A ``cue_term`` candidate per narrated mention of each ``[[term]]`` (R-35 and R-26 are applied once the cues
    are ordered, in :func:`_budgeted`, so a cue the budget drops does not spend a mention).

    The target is the item itself when it holds one term (the stage marks every term of the item) and
    ``<item>:term:<n>`` when it holds several (plan §1.7). A mention before the item is on screen is skipped."""
    stream = word_stream(scene.beats)
    enter = _entrances(scene)
    out: list[_Cue] = []
    for item in scene.board:
        phrases = term_phrases(item)
        if not phrases or not _targetable(item.id):
            continue
        many = len(phrases) > 1
        after = enter.get(item.id, INITIAL_DELAY) + TERM_AFTER_ENTER
        for index, phrase in phrases:
            target = f"{item.id}:term:{index}" if many else item.id
            if not accepts("cue_term", target):
                continue
            key = " ".join(phrase)
            for at, spoken in phrase_times(stream, phrase):
                if at < after:
                    continue
                out.append(_Cue(VisualEvent(t=round(at, 3), kind="cue_term", target=target, anchored=True,
                                            words=spoken), key))
    return out


def _look_cues(scene: TimedScene, accepts: Accepts) -> list[VisualEvent]:
    """A ``look_here`` per pointing sync cue: a side-panel ``focus`` pulse on ``panel``, an authored ``emphasis`` on
    its item (or on the part the narration named, when the template resolves that form)."""
    out: list[VisualEvent] = []
    for cue in scene.sync_cues:
        target: str | None = None
        if cue.kind == "focus":
            target = "panel"
        elif cue.kind == "emphasis" and cue.item_id and _targetable(cue.item_id):
            part = cue.part or ""
            whole = cue.item_id
            named = f"{whole}:{part}" if part and part != "term" else whole
            target = named if accepts("look_here", named) else whole
        if target is None or not accepts("look_here", target):
            continue
        end = float(cue.end) if cue.end is not None else None
        start = max(0.0, float(cue.start))
        dur = round(max(0.0, end - start), 3) if end is not None else None
        out.append(VisualEvent(t=round(start, 3), kind="look_here", target=target,
                               dur=dur if dur else None, anchored=True, words=cue.words))
    return out


def _chart_marks(scene: TimedScene, accepts: Accepts) -> tuple[list[VisualEvent], list[_Cue]]:
    """A ``draw`` on each chart mark the narration names, at its first mention once the panel is shown (120 ms early,
    R-17, never before the panel), and a ``look_here`` candidate on the mark's first target at every later mention
    (R-26's budget applies to those, as to every cue). A label of one-letter words alone cannot be heard
    (``compose.sync``'s refusal). Only the kinds and forms the template consumes are written."""
    sp = scene.side_panel
    if sp is None or not scene.layout.show_side_panel:
        return [], []
    stream = word_stream(scene.beats)
    show = max(0.0, float(sp.show_at))
    draws: list[VisualEvent] = []
    looks: list[_Cue] = []
    for label, targets in mark_targets(sp.panel):
        phrase = tokens(label)[:MAX_PHRASE_TOKENS]
        if not phrase or not any(len(t) >= 2 for t in phrase):
            continue
        heard = [(at, spoken) for at, spoken in phrase_times(stream, phrase) if at >= show]
        if not heard:
            continue
        first, spoken = heard[0]
        for target in targets:
            if accepts("draw", target):
                draws.append(VisualEvent(t=max(show, round(first - REVEAL_LEAD, 3)), kind="draw", target=target,
                                         anchored=True, words=spoken))
        if accepts("look_here", targets[0]):
            looks += [_Cue(VisualEvent(t=round(at, 3), kind="look_here", target=targets[0], anchored=True, words=w))
                      for at, w in heard[1:]]
    return draws, looks


def walk_targets(scene: TimedScene, accepts: Accepts) -> list[str]:
    """What an R-16 hold walk points at, in order: a formula's symbols when the template resolves them (the §4.3
    formula_focus walk), a code item's non-blank lines when the template resolves ``item:line`` (code_walkthrough
    reads through the program line by line; the indices are the page's ``codeLines``, whose tab expansion keeps the
    line count), else every board item the template resolves - and the panel last, when it has one."""
    out: list[str] = []
    for item in scene.board:
        if not _targetable(item.id):
            continue
        symbols = [f"{item.id}:var:{v.symbol_latex}" for v in item.variables]
        resolved = [s for s in symbols if accepts("look_here", s)]
        lines = ([f"{item.id}:line:{n}" for n, text in enumerate((item.code or "").splitlines()) if text.strip()]
                 if str(getattr(item.kind, "value", item.kind)) == "code" else [])
        lines = [t for t in lines if accepts("look_here", t)]
        if resolved:
            out += resolved
        elif lines:
            out += lines
        elif accepts("look_here", item.id):
            out.append(item.id)
    if scene.side_panel is not None and accepts("look_here", "panel"):
        out.append("panel")
    return out


def _hold_walk(scene: TimedScene, accepts: Accepts, hold_reason: str | None) -> list[VisualEvent]:
    """R-16: a hold keeps a cue moving. From one :data:`HOLD_WALK_GAP` after the narration ends (the hold itself) to
    the scene's end, a ``look_here`` every gap, cycling through :func:`walk_targets`. Unanchored: a walk is the
    template's own motion, not a narrated moment, so it never counts for R-15 (§1.4)."""
    if hold_reason is None:
        return []
    targets = walk_targets(scene, accepts)
    if not targets:
        return []
    spoken_end = max((float(b.end) for b in scene.beats), default=0.0)
    duration = float(scene.duration)
    out: list[VisualEvent] = []
    at = spoken_end + HOLD_WALK_GAP
    k = 0
    while at + CUE_MIN_VISIBLE <= duration and len(out) < MAX_CUES_PER_SCENE:
        dur = round(min(HOLD_WALK_SECONDS, duration - at), 3)
        out.append(VisualEvent(t=round(at, 3), kind="look_here", target=targets[k % len(targets)],
                               dur=dur if dur > 0 else None))
        at += HOLD_WALK_GAP
        k += 1
    return out


# --- R-26's budget --------------------------------------------------------------------------------------------------


def _hold_end(event: VisualEvent) -> float:
    return event.t + (event.dur if event.dur is not None else CUE_HOLD)


def _fits_budget(kept: Sequence[VisualEvent], cue: VisualEvent) -> bool:
    """R-26: at most one new cue per :data:`CUE_MIN_GAP`, at most :data:`MAX_CONCURRENT_CUES` on screen, and never
    the same target again within :data:`CUE_REPEAT_GAP`."""
    if len(kept) >= MAX_CUES_PER_SCENE:
        return False
    concurrent = 0
    for other in kept:
        if abs(other.t - cue.t) < CUE_MIN_GAP - 1e-9:
            return False
        if other.target == cue.target and abs(other.t - cue.t) < CUE_REPEAT_GAP - 1e-9:
            return False
        if other.t <= cue.t < _hold_end(other):
            concurrent += 1
    return concurrent < MAX_CONCURRENT_CUES


def _budgeted(word_cues: Sequence[_Cue], walk: Sequence[_Cue], terms: TermBudget) -> list[VisualEvent]:
    """The cues that pass R-26's budget and R-35's: the word-anchored ones first (they carry the meaning), then the
    hold walk in the room they leave. Earliest first, so the budget is a function of the scene alone, and a key
    term's mention is spent only on a cue that is actually drawn."""
    kept: list[VisualEvent] = []
    for cue in [*sorted(word_cues, key=lambda c: (c.event.t, c.event.kind, c.event.target or "")), *walk]:
        if _fits_budget(kept, cue.event) and (cue.term is None or terms.take(cue.term)):
            kept.append(cue.event)
    return kept


# --- the entry point ------------------------------------------------------------------------------------------------


def _inside(events: Iterable[VisualEvent], duration: float) -> list[VisualEvent]:
    """Events clamped inside the scene (``TimedScene.check_inside``): one that cannot be seen is dropped, and a
    ``dur`` that would run past the end is cut to it."""
    out: list[VisualEvent] = []
    for e in events:
        if e.t > duration + 1e-9:
            continue
        t = min(max(0.0, e.t), duration)
        dur = e.dur
        if dur is not None:
            dur = round(min(dur, max(0.0, duration - t)), 3)
            if dur <= 0:
                dur = None
        out.append(e.model_copy(update={"t": round(t, 3), "dur": dur}))
    return out


def scene_events(scene: TimedScene, *, accepts: Accepts, hold_reason: str | None = None,
                 terms: TermBudget | None = None) -> list[VisualEvent]:
    """The visual events of one timed scene, sorted by ``t`` (``TimedScene.events``).

    ``accepts``: the template's ``spec.consumes.accepts`` - only the kinds and target forms it draws are written, so
    a template that declares no cue is never handed one. ``hold_reason``: the scene's hold (``SceneLook.hold_reason``
    / ``ResolvedLook.hold_reason``), which adds the R-16 walk. ``terms``: the lesson-wide key-term budget (R-35);
    without one every scene counts its mentions from zero."""
    duration = float(scene.duration)
    if duration <= 0:
        return []
    budget = terms if terms is not None else TermBudget()
    var_events = _vars(scene, accepts)
    chart_draws, chart_looks = _chart_marks(scene, accepts)
    plain = [*_reveals(scene, accepts), *var_events, *_draws(scene, var_events, accepts), *_outputs(scene, accepts),
             *chart_draws]
    word_cues = [*_term_cues(scene, accepts), *(_Cue(e) for e in _look_cues(scene, accepts)), *chart_looks]
    visible = [c for c in word_cues if c.event.t + CUE_MIN_VISIBLE <= duration]
    walk = [_Cue(e) for e in _hold_walk(scene, accepts, hold_reason)]
    cues = _budgeted(visible, walk, budget)
    out = _inside([*plain, *cues], duration)
    out.sort(key=lambda e: (e.t, _KIND_ORDER.index(e.kind) if e.kind in _KIND_ORDER else len(_KIND_ORDER),
                            e.target or ""))
    return out


def anchored_gaps(events: Sequence[VisualEvent], duration: float) -> list[float]:
    """The gaps between the scene's anchored events (R-15's cadence; the lint of §7.12 measures the lesson, this is
    the per-scene input): from 0 to the first anchored event, between them, and from the last to the scene's end."""
    times = sorted(e.t for e in events if e.anchored)
    marks = [0.0, *times, max(duration, times[-1] if times else 0.0)]
    return [round(b - a, 3) for a, b in zip(marks, marks[1:], strict=False)]


__all__ = ["TermBudget", "anchored_gaps", "phrase_times", "scene_events", "term_phrases", "walk_targets"]
