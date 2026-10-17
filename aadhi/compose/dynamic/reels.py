"""The reel (9:16) adaptations of owner decision 4 (2026-10-16; docs/plans/b2/HANDOFF.md "Owner decisions"), Python
side. The page side is ``web/js/dynamic/templates/_shared.js`` ("Reel panel beat"), ``check.js``
(``shownOptions`` / ``strongestDistractor``), ``key_terms.js`` and ``worked_steps.js``; every number here mirrors a
JS constant and ``web/tests/dynamic/templates/reels.test.js`` / ``tests/compose/dynamic/test_reels.py`` pin the two.

**(a) A reel quiz is a two-option check.** §4.3 gives ``mcq`` to 16:9 only and R-06 gives a reel free recall or a
single answer; the owner decided that a 9:16 quiz with more than two options is drawn by ``check`` as a two-option
check: the correct answer and the **strongest distractor**, in the order the canonicaliser shuffled them (so the
correct answer is not always first). The strongest distractor is, deterministically, from the data the screenplay
has (:func:`strongest_distractor`):

1. the first wrong option tagged with a misconception (``QuizScene.option_misconception_ids``: the lesson names the
   error it diagnoses - the strongest evidence a distractor is the one students actually pick);
2. else the first wrong option with a written rationale (a non-empty ``feedback_wrong``: the author explained why it
   is tempting);
3. else the first wrong option.

"First" is the stored (shuffled) order. The 16:9 ``mcq`` is unchanged. ``check.js`` reads the misconception ids from
``QuizTiming.option_misconception_ids`` when the timeline carries them (an additive field the integrator may add, see
the step-5 requests) and falls back to tiers 2 and 3 without them.

**(b) A reel side panel is its own short full-stage beat before the content.** ``worked_steps`` and ``key_terms``
draw a scene's side panel in 16:9 beside the steps / terms; in 9:16 a panel above the stack pushed the rows under
R-29's floor, so the reel showed no panel at all (the scene fell back to ``board_generic``). Now the panel takes the
whole stage alone first, from :data:`REEL_BEAT_START` to the beat's end, then leaves, and the content enters:

* the beat ends at the first **anchored** content entrance (an item a beat or a ``reveal`` event reveals; R-17 keeps
  it on its word), never before ``start + REEL_BEAT_MIN``; when something unanchored is waiting (an item no beat
  reveals, which would otherwise wait for the anchored one) never after ``start + REEL_BEAT_MAX``; and never so late
  that the content gets less than ``REEL_BEAT_MIN`` of the scene (:func:`reel_window`);
* content that would have entered during the beat enters as it ends, staggered by :data:`REEL_STAGGER` in entrance
  order (:func:`reel_entrances`);
* :func:`reel_lead` is the extra narration delay ``compose/timeline.py`` gives such a scene so its first anchored
  reveal falls at or after ``start + REEL_BEAT_MIN``: with it no anchored item is ever deferred, so R-17 holds for
  every item; without it (a timeline built before the lead) the page still draws the beat, deferring the first
  anchored items by at most ``REEL_BEAT_MIN``;
* :func:`reel_events` rewrites the scene's events for it (``direct.plan`` calls it for a 9:16 ``worked_steps`` /
  ``key_terms`` scene whose panel is drawn): a ``reveal`` on ``panel`` (the beat: ``t`` its start, ``dur`` its
  length; the page takes the window from it), a ``reveal`` at the deferred entrance of every item the beat holds back
  (so ``state.js itemTimings``, the cues and this module agree on when an item is on screen), and no cue on an item
  before it is on screen, nor on the panel outside the beat.

Pure and deterministic; imports only the timeline schema (``events.py`` and ``templates.py`` stay independent of each
other and of this module, plan §1.6). Imported only inside the Dynamic gate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Final

from ...schemas.screenplay import SceneBase
from ...schemas.timeline import TimedScene, VisualEvent

# --- (b) the panel beat: _shared.js REEL_BEAT_START / REEL_BEAT_MIN / REEL_BEAT_MAX / REEL_BEAT_EXIT / REEL_STAGGER ---
#: The panel enters with the scene, as a 16:9 panel shown from the start does (_shared.js PANEL_DELAY).
REEL_BEAT_START: Final = 0.2
#: The panel holds the stage at least this long (a picture needs about two seconds to register; R-15's 9:16 median
#: gap is 2.5-4 s). A B2 proposal, pending the owner's review.
REEL_BEAT_MIN: Final = 2.0
#: ... and, when an item no narration anchors is waiting, at most this long (R-15's 9:16 "max static 6 s" keeps the
#: beat short). A B2 proposal, pending the owner's review.
REEL_BEAT_MAX: Final = 4.0
#: The panel leaves over this long, ending as the content starts to enter (§4.8: exits 30 % shorter than PANEL_ENTER).
REEL_BEAT_EXIT: Final = 0.35
#: Content held back by the beat enters as it ends, this far apart (state.js INITIAL_STAGGER).
REEL_STAGGER: Final = 0.09
#: state.js REVEAL_LEAD / INITIAL_DELAY / INITIAL_STAGGER (how the page times an item with no event).
REVEAL_LEAD: Final = 0.12
INITIAL_DELAY: Final = 0.35
INITIAL_STAGGER: Final = 0.09
#: events.py TERM_AFTER_ENTER: a cue waits this long after its item has entered.
CUE_AFTER_ENTER: Final = 0.2
#: Two times closer than this are the same instant (float noise from the millisecond rounding of the timeline).
EPS: Final = 1e-6
#: events.py _KIND_ORDER: how events at the same instant are ordered.
KIND_ORDER: Final = ("reveal", "fill", "var", "draw", "output", "count", "countdown", "answer", "cue_term", "look_here",
                     "exit")
#: The templates that draw a reel panel beat.
REEL_PANEL_TEMPLATES: Final = ("worked_steps", "key_terms")

Accepts = Callable[[str, str | None], bool]


@dataclass(frozen=True)
class Entrance:
    """An item's entrance as the page computes it (state.js ``itemTimings``)."""

    id: str
    enter: float
    order: int
    anchored: bool  # a beat or a reveal event reveals it (state.js: ``initial`` is its negation)


def item_entrances(scene: TimedScene) -> list[Entrance]:
    """``state.js itemTimings`` for the scene's board items, in entrance order: a ``reveal`` event wins (its earliest
    ``t``), else the earliest beat that reveals the item, ``REVEAL_LEAD`` before it (never below 0), else the staggered
    scene entrance ``INITIAL_DELAY + INITIAL_STAGGER * k`` (k counts the unanchored items in board order)."""
    events: dict[str, float] = {}
    for e in scene.events:
        if e.kind != "reveal" or e.target is None:
            continue
        key = e.target.split(":")[0]
        if key not in events or events[key] > e.t:
            events[key] = float(e.t)
    beats: dict[str, float] = {}
    for b in scene.beats:
        if b.board_item_id and (b.board_item_id not in beats or beats[b.board_item_id] > b.start):
            beats[b.board_item_id] = float(b.start)
    out: list[Entrance] = []
    initial = 0
    for order, item in enumerate(scene.board):
        if item.id in events:
            out.append(Entrance(item.id, events[item.id], order, True))
        elif item.id in beats:
            out.append(Entrance(item.id, max(0.0, beats[item.id] - REVEAL_LEAD), order, True))
        else:
            out.append(Entrance(item.id, INITIAL_DELAY + INITIAL_STAGGER * initial, order, False))
            initial += 1
    out.sort(key=lambda x: (x.enter, x.order))
    return out


def reel_window(entrances: Sequence[Entrance], duration: float) -> tuple[float, float]:
    """The panel beat ``(start, end)`` for the content items' ``entrances`` (``_shared.js reelWindow``)."""
    start = REEL_BEAT_START
    anchored = [x.enter for x in entrances if x.anchored]
    waiting = any(not x.anchored for x in entrances)
    end = max(start + REEL_BEAT_MIN, min(anchored)) if anchored else start + REEL_BEAT_MAX
    if waiting:
        end = min(end, start + REEL_BEAT_MAX)
    end = min(end, max(start, float(duration) - REEL_BEAT_MIN))
    return start, max(start, end)


def reel_entrances(entrances: Sequence[Entrance], end: float) -> dict[str, float]:
    """Each item's entrance with the beat: one that would enter before ``end`` enters at ``end``, the k-th of them (in
    entrance order) ``k * REEL_STAGGER`` later; the rest keep theirs (``_shared.js reelEntrances``)."""
    out: dict[str, float] = {}
    k = 0
    for x in sorted(entrances, key=lambda e: (e.enter, e.order)):
        if x.enter < end - EPS:
            out[x.id] = end + REEL_STAGGER * k
            k += 1
        else:
            out[x.id] = x.enter
    return out


def _ceil_ms(x: float) -> float:
    return math.ceil(round(x * 1000.0, 6)) / 1000.0


def reel_lead(first_reveal: float | None, *, scene_lead: float) -> float:
    """The extra narration delay a 9:16 panel-beat scene needs (``compose/timeline.py``), so the first item a beat
    reveals enters (``REVEAL_LEAD`` before its beat) at or after ``REEL_BEAT_START + REEL_BEAT_MIN``: 0 when it already
    does. ``first_reveal``: the narration-relative start (before any lead) of the scene's first beat that reveals a
    board item; None when no beat reveals one (the beat is then ``REEL_BEAT_MAX`` long and holds no anchored item back).
    ``scene_lead``: the scene's ordinary lead (``SCENE_LEAD_SECONDS``). Whole milliseconds, rounded up."""
    if first_reveal is None:
        return 0.0
    anchored = max(0.0, scene_lead + float(first_reveal) - REVEAL_LEAD)
    return _ceil_ms(max(0.0, REEL_BEAT_START + REEL_BEAT_MIN - anchored))


def reel_panel_scene(scene: SceneBase, fmt: str) -> bool:
    """Whether a screenplay scene gets the reel panel beat in ``fmt``: 9:16, a side panel, and the data of a template
    that draws one (``TemplateSpec.data_fit`` of :data:`REEL_PANEL_TEMPLATES` above 0 - their fits accept a 9:16
    panel only of a kind the page draws, CG-2). ``templates`` is imported here, lazily, so ``events`` / ``templates``
    / this module never import each other at load time (plan §1.6)."""
    if fmt != "portrait" or getattr(scene, "side_panel", None) is None:
        return False
    from . import templates as tp

    return any(tp.TEMPLATES[t].data_fit(scene, "portrait") > 0 for t in REEL_PANEL_TEMPLATES)


def reel_lead_for(scene: SceneBase, beats: Sequence[Any], *, fmt: str, scene_lead: float) -> float:
    """``compose/timeline.py``'s hook: the extra narration lead of one scene (:func:`reel_lead`; 0 unless
    :func:`reel_panel_scene`). ``beats``: the audio plan's beats (each with ``.beat.board_item_id`` and a
    narration-relative ``.start``). Decided before the director runs, from the scene's data alone, so a scene whose
    look then goes to another template (R-20 variety) keeps a lead it does not need - a silent second or two under
    that template's own entrance, never a lost or mistimed reveal."""
    if not reel_panel_scene(scene, fmt):
        return 0.0
    ids = {i.id for i in getattr(scene, "board", []) or []}
    starts = [float(bp.start) for bp in beats if getattr(bp.beat, "board_item_id", None) in ids]
    return reel_lead(min(starts) if starts else None, scene_lead=scene_lead)


def _item_of(target: str | None) -> str | None:
    return target.split(":")[0] if target else None


def reel_events(scene: TimedScene, events: Sequence[VisualEvent], *, accepts: Accepts) -> list[VisualEvent]:
    """``events`` (``events.scene_events`` for a 9:16 ``worked_steps`` / ``key_terms`` scene whose side panel the page
    draws) rewritten for the panel beat: the beat's ``reveal`` on ``panel``; a ``reveal`` at its deferred entrance
    for each item the beat holds back (unanchored: it is no longer on its word); no ``cue_term`` / ``look_here`` on an
    item before ``CUE_AFTER_ENTER`` after its entrance; no event on the panel outside the beat (it leaves the stage
    for good). Sorted like ``scene_events``. A scene with no board item is returned unchanged."""
    if not scene.board:
        return list(events)
    entrances = item_entrances(scene.model_copy(update={"events": list(events)}))
    start, end = reel_window(entrances, float(scene.duration))
    moved = reel_entrances(entrances, end)
    before = {x.id: x.enter for x in entrances}
    out: list[VisualEvent] = []
    for e in events:
        target = e.target
        item = _item_of(target)
        if e.kind == "reveal" and item in moved and abs(moved[item] - before[item]) > EPS:
            continue  # replaced by the deferred reveal below
        if item == "panel" and e.kind != "reveal" and not (start - EPS <= e.t < end - REEL_BEAT_EXIT):
            continue
        if e.kind in ("cue_term", "look_here") and item in moved and e.t < moved[item] + CUE_AFTER_ENTER - EPS:
            continue
        out.append(e)
    if accepts("reveal", "panel"):
        out.append(VisualEvent(t=round(start, 3), kind="reveal", target="panel", dur=round(end - start, 3)))
    for item_id, at in moved.items():
        if abs(at - before[item_id]) > EPS and accepts("reveal", item_id):
            out.append(VisualEvent(t=round(at, 3), kind="reveal", target=item_id))
    out.sort(key=lambda e: (e.t, KIND_ORDER.index(e.kind) if e.kind in KIND_ORDER else len(KIND_ORDER),
                            e.target or ""))
    return out


# --- (a) the reel check: check.js strongestDistractor / shownOptions ------------------------------------------------


def strongest_distractor(options: Sequence[str], correct: int, feedback_wrong: Sequence[str] | None = None,
                         misconception_ids: Sequence[str | None] | None = None) -> int | None:
    """The index of the strongest distractor (see the module docstring), or None without a wrong option."""
    wrong = [i for i in range(len(options)) if i != correct]
    if not wrong:
        return None
    ids = list(misconception_ids or [])
    tagged = [i for i in wrong if i < len(ids) and isinstance(ids[i], str) and ids[i].strip()]
    if tagged:
        return tagged[0]
    feedback = list(feedback_wrong or [])
    reasoned = [i for i in wrong if i < len(feedback) and isinstance(feedback[i], str) and feedback[i].strip()]
    if reasoned:
        return reasoned[0]
    return wrong[0]


def reel_options(options: Sequence[str], correct: int, feedback_wrong: Sequence[str] | None = None,
                 misconception_ids: Sequence[str | None] | None = None) -> list[int]:
    """The option indices a 9:16 check draws: every option when there are at most two, else the correct one and the
    strongest distractor, in stored order."""
    if len(options) <= 2:
        return list(range(len(options)))
    other = strongest_distractor(options, correct, feedback_wrong, misconception_ids)
    return sorted({correct, other} - {None})  # type: ignore[arg-type]


__all__ = ["CUE_AFTER_ENTER", "EPS", "KIND_ORDER", "Entrance", "INITIAL_DELAY", "INITIAL_STAGGER", "REEL_BEAT_EXIT",
           "REEL_BEAT_MAX", "REEL_BEAT_MIN", "REEL_BEAT_START", "REEL_PANEL_TEMPLATES", "REEL_STAGGER", "REVEAL_LEAD",
           "item_entrances", "reel_entrances", "reel_events", "reel_lead", "reel_lead_for", "reel_options",
           "reel_panel_scene", "reel_window", "strongest_distractor"]
