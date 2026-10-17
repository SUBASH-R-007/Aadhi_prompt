"""The Director: which template draws each scene of a Dynamic lecture (DYNAMIC_VIDEO_DESIGN §4.4 template selection,
§4.2 classes, R-20, R-23, R-28; B2 plan §1.6, §A step 5).

:func:`plan` is the one entry point the resolver calls: given a screenplay, its timed scenes and the format, it
returns one :class:`ScenePlan` per scene - the template, its variant and the scene's visual events (``events.py``) -
so ``compose.presentation.resolve_looks`` can write ``ResolvedLook.template`` / ``.variant`` and
``compose.timeline.build_timeline`` ``TimedScene.events``.

**Candidates.** Only a template the stage can draw: ``implemented`` (its ``web/js/dynamic/templates/<id>.js`` exists),
release ``R1`` and a spec whose ``formats`` hold the lecture's format. A template still unimplemented is never chosen,
and becomes a candidate the moment its ``implemented`` flag flips - this module names no template of its own except
``board_generic``, the fallback (:data:`FALLBACK_TEMPLATE`).

**Choice** (§4.4's algorithm, with its post-pass left for later - see "What is not here"):

1. A **locked** look wins: ``SceneLook.lock`` ``pinned`` or ``settled`` with a template that is a candidate and whose
   ``fit`` is above 0 keeps that template (the teacher's choice, §4.4).
2. A **forced** row wins next: a scene type that forces a template (``chapter_card`` -> ``chapter_bumper``, ``quiz``
   -> ``check``, the presenter's ``aadhi_intro``, ...). Its template if it is a candidate, else the row's
   alternative, else ``board_generic`` - never an unimplemented template. Forced scenes are exempt from the repeat
   and share penalties [RV E-12].
3. Otherwise the scene's **class**: the first ``signals`` row (§4.4's table order) that has a candidate with
   ``fit > 0``, which keeps R-28 (a representational row is above the typography-only rows that follow it).
4. Within that row, the highest **score**: ``fit`` + :data:`INTENT_BONUS` when the template matches
   ``scene.intent.visual_intent`` - :data:`REPEAT_PENALTY` when it repeats the previous scene's template (R-20,
   unless the scene ``continues_from`` it) - :data:`SHARE_PENALTY` when it would pass the format's share
   (:data:`SHARE_LIMIT`) + :data:`UNUSED_BONUS` when it is unused in the last :data:`RECENT_SCENES` scenes, and a
   seeded tie-break worth at most :data:`TIE_BREAK`. Ties beyond that fall to :data:`TIE_ORDER`: the row's preferred
   template, then its alternatives in the row's own order, then the template id - so the choice never depends on
   dictionary order.
5. No row with a candidate: ``board_generic``, which is the only template this module falls back to.

**Step 4's four templates** (``animation_stage``, ``check``, ``full_bleed_media``, ``predict``) need no rule of their
own here, and none was added: each is reached through machinery that was already in place, which is what keeps the
choice deterministic and reviewable.

* ``full_bleed_media`` (forced for ``ai_video``), ``animation_stage`` (``simulation``) and ``check``
  (``quiz_checkpoint``) are **forced rows** (``templates._FORCED_TYPES``), so step 2 above takes them the moment
  ``implemented`` flips. Their ``fit`` refuses what their page refuses (B2c review CG-2), so a quiz past §4.3's
  option cap for the format - 9:16 beyond two options - scores 0 and the forced row falls through to
  ``board_generic`` rather than planning a template the page would then draw with the fallback.
* ``predict`` is an ordinary class candidate of the ``prompt`` row (a board with one blank ``question`` item), nudged
  by ``INTENT_TEMPLATES["predict"]``.
* ``worked_steps`` / ``key_terms`` draw a **side panel** in 16:9 beside the content and (step 5, owner decision 4) in
  9:16 as its own short beat before it, so their fits take a panelled scene in both formats: the §4.4 rows are
  unchanged, only what their fits accept. For such a 9:16 scene :func:`plan` rewrites the events for the beat
  (``reels.reel_events``: the ``reveal`` on ``panel`` and the deferred entrances).

**Step 5's templates** are again reached through the existing machinery, with three additions, each deterministic:

* ``aadhi_intro`` / ``aadhi_recap``: the timeline builder synthesises Aadhi's opening scene (and the 16:9 retrieval
  recap's card) with no screenplay scene behind it (``dynamic/presenter.py``); :func:`plan` gives such a timed scene
  the presenter template ``presenter.plan_for`` names, forced. ``presenter=False`` (``DYNAMIC_AADHI_INTRO`` off)
  never chooses a presenter template (:func:`_without_presenter`).
* ``outro_next``: §4.4's positional "Last scene" row is tried before the class rows, so a closing takeaway ends the
  video on its outro rather than on ``kinetic_statement``; any last scene that is not R-11's closure shape keeps its
  class row (the outro's fit refuses it). Its variant follows the destination (:func:`variant_for`).
* ``myth_fact``, ``code_walkthrough``, ``kinetic_statement`` and ``chart_build`` are ordinary class candidates of
  their rows (the graph row names ``chart_build`` while ``function_plot`` is unbuilt); a 9:16 quiz of more than two
  options is ``check``'s two-option reel check (stored ``true_false``).

The "Your prediction?" chip of §4.3's ``predict`` belongs to whichever template explains the outcome, so it is not a
template choice at all: ``web/js/dynamic/stage.js`` builds it in a layer of its own on any scene whose look carries
``refs.prediction`` (``predict.js buildChip``). Nothing here writes that ref yet - the resolver's ``refs`` are B2e's -
so no built lecture shows a chip today; when the ref is stored, every template shows it with no further change.

**Variants.** ``spec.variants[fmt]``, empty for most. ``media_split``'s media side is a function of the scene's
**chapter** (§4.3 / §4.4: the side changes only at chapter boundaries), seeded per lecture, so it never flips inside
a chapter; ``aadhi_intro`` and ``hook_question`` take the teacher's stored choice, ``check`` and
``definition_card`` the scene's own data (:func:`variant_for`). A template with variants and no rule of its own
takes the first of its list (deterministic until its rule lands with its JS).

**Events.** Each scene's :class:`ScenePlan` carries the ``VisualEvent``s ``events.py`` generates for the template
that was chosen, gated by that template's ``consumes`` - so only events the stage will draw are written, and the
lesson-wide key-term budget (R-35) is spent in scene order.

**What is not here** (recorded for the owner, deadline scope): §4.4's bounded variety post-pass (``look.variety``),
the energy parameter, ``palette_slot`` / accents / ``refs`` / ``icons`` / ``text_fit`` (B2e's resolver work), and the
history-aware share of §5.3. Everything here is a pure function of (screenplay, scenes, format): no I/O, no clock,
no ``hash()`` - the seeds are SHA-256 of the stored ``look_seed`` / ``layout_epoch``, so a rebuild reproduces the
lecture's looks exactly. Imported lazily inside the Dynamic gate, never by a classic build.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

from ...schemas.screenplay import Presentation, SceneBase, Screenplay
from ...schemas.timeline import Chapter, TimedScene, VisualEvent
from . import events as ev
from . import reels
from .templates import DEFINITION_PANELS, FALLBACK_TEMPLATE, TEMPLATES, Signal, TemplateSpec, signals
from .zones import Format

# §4.4's score terms.
INTENT_BONUS: Final = 0.15
REPEAT_PENALTY: Final = 1.00  # R-20: never the same template twice in a row unless the scene continues it
SHARE_PENALTY: Final = 0.40
UNUSED_BONUS: Final = 0.10
TIE_BREAK: Final = 0.05
RECENT_SCENES: Final = 3
# §4.4: at most 25% (16:9) / 30% (9:16) of the shown scenes on one template.
SHARE_LIMIT: Final[Mapping[Format, float]] = {"landscape": 0.25, "portrait": 0.30}
# The releases whose templates may be chosen (W2 and B6 templates ship later; an unimplemented one never is anyway).
RELEASES: Final = ("R1",)
# Which templates each ``SceneIntent.visual_intent`` is asking for (§4.4's "+0.15 if t matches
# scene.intent.visual_intent"; the keys are exactly ``schemas.screenplay.VisualIntent``, pinned by a test). The bonus
# only ever picks between the candidates of one class row, so a template absent here is never ruled out, only
# un-nudged. Intents with no template of their own (``context``) are left out.
INTENT_TEMPLATES: Final[Mapping[str, tuple[str, ...]]] = {
    "hook": ("hook_question", "stat_punch"),
    "pretraining": ("key_terms",),
    "define": ("definition_card", "key_terms"),
    "explain": ("media_split", "kinetic_statement", "list_build"),
    "compare": ("versus", "table_reveal"),
    "sequence": ("process_flow", "timeline_track"),
    "cycle": ("cycle_loop",),
    "timeline": ("timeline_track",),
    "cause": ("cause_chain",),
    "quantity": ("stat_punch", "chart_build", "function_plot"),
    "formula": ("formula_focus",),
    "example": ("worked_steps", "media_split"),
    "code": ("code_walkthrough",),
    "data": ("chart_build", "function_plot", "table_reveal"),
    "spatial": ("diagram_build", "model_spin", "spotlight_image"),
    "analogy": ("analogy_bridge",),
    "misconception": ("myth_fact",),
    "predict": ("predict",),
    "check": ("check",),
    "apply": ("check", "worked_steps"),
    "summarize": ("takeaway_stamp", "kinetic_statement"),
    "recap": ("recap_retrieval", "aadhi_recap"),
}
# media_split's sides, in the order the spec lists them (templates.py: an A2 proposal, pending the owner's review).
MEDIA_SIDES: Final = ("media_left", "media_right")


@dataclass(frozen=True)
class ScenePlan:
    """What the Director decided for one scene: the ``template`` the stage draws it with, its ``variant`` (None when
    the template has none in this format), the scene's ``events`` (sorted by ``t``), and - for the resolver, the
    lints and the tests - the ``fit`` of the chosen template, the ``semantic_class`` it was chosen for, whether the
    scene ``forced`` it, and whether a locked look kept it."""

    scene_id: str
    template: str
    variant: str | None
    events: tuple[VisualEvent, ...]
    fit: float
    semantic_class: str
    forced: bool = False
    locked: bool = False

    @property
    def fallback(self) -> bool:
        """True when the scene fell back to ``board_generic`` (no row had a candidate): §4.4's fallback row."""
        return self.template == FALLBACK_TEMPLATE


# --- candidates -----------------------------------------------------------------------------------------------------


def candidate(template_id: str, fmt: Format) -> TemplateSpec | None:
    """The spec of ``template_id`` when the stage can draw it in ``fmt`` (implemented, released, right format), else
    None. The one gate on what this module may choose, so a template becomes available by flipping ``implemented``."""
    spec = TEMPLATES.get(template_id)
    if spec is None or not spec.implemented or spec.release not in RELEASES or fmt not in spec.formats:
        return None
    return spec


def candidates(fmt: Format) -> tuple[str, ...]:
    """Every template the stage can draw in ``fmt``, in registry order (``board_generic`` included)."""
    return tuple(tid for tid in TEMPLATES if candidate(tid, fmt) is not None)


def _row_templates(row: Signal) -> tuple[str, ...]:
    """The row's templates, preferred first (:data:`TIE_ORDER`'s order), ``board_generic`` only from its own row."""
    return (row.preferred, *row.alternatives)


# --- scoring --------------------------------------------------------------------------------------------------------


def seed_for(presentation: Presentation | None, scene_id: str, template_id: str) -> float:
    """§4.4's tie-break in [0, 1): SHA-256 of the stored ``look_seed`` / ``layout_epoch``, the scene and the
    template (never ``hash()``), so the same lecture always breaks a tie the same way."""
    look = getattr(presentation, "look_seed", 0)
    epoch = getattr(presentation, "layout_epoch", 0)
    text = f"{look}:{epoch}:{scene_id}:{template_id}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big") / 2 ** 32


def _intent_match(template_id: str, scene: SceneBase) -> bool:
    intent = scene.intent.visual_intent if scene.intent is not None else None
    return intent is not None and template_id in INTENT_TEMPLATES.get(intent, ())


def _share_exceeded(used: Mapping[str, int], template_id: str, total: int, fmt: Format) -> bool:
    """True when one more scene on ``template_id`` would pass the format's share of ``total`` shown scenes. A share
    needs room for at least one scene, so a lecture of three scenes never penalises its first use."""
    allowed = max(1, math.ceil(SHARE_LIMIT[fmt] * max(1, total)))
    return used.get(template_id, 0) + 1 > allowed


@dataclass(frozen=True)
class _Context:
    """What the running choice knows about the scenes before this one."""

    fmt: Format
    total: int
    previous: str | None = None
    recent: tuple[str, ...] = ()
    used: Mapping[str, int] = field(default_factory=dict)


def score(template_id: str, fit: float, scene: SceneBase, ctx: _Context, presentation: Presentation | None,
          *, continues: bool) -> float:
    """§4.4's score of a candidate (see the module docstring). ``continues``: the scene continues the previous one
    (``look.continues_from``), which lifts R-20's repeat penalty."""
    out = fit
    if _intent_match(template_id, scene):
        out += INTENT_BONUS
    if ctx.previous == template_id and not continues:
        out -= REPEAT_PENALTY
    if _share_exceeded(ctx.used, template_id, ctx.total, ctx.fmt):
        out -= SHARE_PENALTY
    if template_id not in ctx.recent:
        out += UNUSED_BONUS
    return out + TIE_BREAK * seed_for(presentation, scene.id, template_id)


# --- the choice per scene -------------------------------------------------------------------------------------------


def _fits(spec: TemplateSpec, scene: SceneBase, fmt: Format, *, last: bool, chapter_end: bool) -> float:
    return spec.fit(scene, fmt, last=last, chapter_end=chapter_end)


def _locked(scene: SceneBase, fmt: Format, *, last: bool, chapter_end: bool) -> tuple[str, float] | None:
    """The teacher's locked template and its fit, when the lock holds (§4.4 step 1)."""
    look = scene.look
    if look is None or look.lock not in ("pinned", "settled") or not look.template:
        return None
    spec = candidate(look.template, fmt)
    if spec is None:
        return None  # never draw a template the stage has not got, whatever the lock says
    fit = _fits(spec, scene, fmt, last=last, chapter_end=chapter_end)
    return (spec.id, fit) if fit > 0 else None


#: The presenter's templates: never chosen while ``DYNAMIC_AADHI_INTRO`` is off (§3.4).
PRESENTER_TEMPLATES: Final = ("aadhi_intro", "aadhi_recap")


def _without_presenter(row: Signal) -> Signal:
    """A §4.4 row with Aadhi switched off (§3.4): the presenter row's alternative (``hook_question``) is preferred, and a
    presenter recap scene is the retrieval recap's (the row a recap scene without the presenter matches)."""
    if row.preferred == "aadhi_intro":
        return Signal(row.id, row.semantic_class, row.alternatives[0], row.alternatives[1:], row.forced)
    if row.preferred == "aadhi_recap":
        return Signal("recap", "prompt", "recap_retrieval")
    return row


def choose(scene: SceneBase, ctx: _Context, presentation: Presentation | None, *, last: bool = False,
           chapter_end: bool = False, presenter: bool = True) -> tuple[str, float, str, bool, bool]:
    """``(template, fit, semantic_class, forced, locked)`` for one screenplay scene (§4.4's algorithm, steps 1-5).
    ``presenter``: Aadhi may appear (``DYNAMIC_AADHI_INTRO``); off, his templates are never chosen."""
    fmt = ctx.fmt
    rows = signals(scene, last=last, chapter_end=chapter_end)
    if not presenter:
        rows = tuple(_without_presenter(r) for r in rows)
    locked = _locked(scene, fmt, last=last, chapter_end=chapter_end)
    if locked is not None and not presenter and locked[0] in PRESENTER_TEMPLATES:
        locked = None
    if locked is not None:
        tid, fit = locked
        row = next((r for r in rows if tid in _row_templates(r)), None)
        return tid, fit, row.semantic_class if row else TEMPLATES[tid].semantic_class, False, True
    forced_row = next((r for r in rows if r.forced), None)
    if forced_row is not None:
        for tid in _row_templates(forced_row):
            spec = candidate(tid, fmt)
            fit = _fits(spec, scene, fmt, last=last, chapter_end=chapter_end) if spec is not None else 0.0
            if fit > 0:
                return tid, fit, forced_row.semantic_class, True, False
        return _fall_back(scene, fmt, last=last, chapter_end=chapter_end)
    continues = scene.look is not None and scene.look.continues_from is not None
    # §4.4 "Last scene -> outro_next" is positional: its row is tried before the class rows (which keep the table's
    # order). Otherwise a closing takeaway would go to the statement row's kinetic_statement and no video would end on
    # its outro. outro_next's fit takes only R-11's closure shape, so any other last scene keeps its class row.
    for row in sorted(rows, key=lambda r: r.id != "last"):  # stable: the other rows keep their order
        scored: list[tuple[float, int, str, float]] = []
        for order, tid in enumerate(_row_templates(row)):
            spec = candidate(tid, fmt)
            if spec is None or (tid == FALLBACK_TEMPLATE and row.semantic_class != "fallback"):
                continue
            fit = _fits(spec, scene, fmt, last=last, chapter_end=chapter_end)
            if fit <= 0:
                continue
            scored.append((score(tid, fit, scene, ctx, presentation, continues=continues), order, tid, fit))
        if scored:
            scored.sort(key=lambda s: (-s[0], s[1], s[2]))
            best = scored[0]
            return best[2], best[3], row.semantic_class, False, False
    return _fall_back(scene, fmt, last=last, chapter_end=chapter_end)


def _fall_back(scene: SceneBase, fmt: Format, *, last: bool, chapter_end: bool) -> tuple[str, float, str, bool, bool]:
    """§4.4's fallback: ``board_generic``, the one template this module names itself (B1 drew every non-clip scene
    type with it). Its ``fit`` is reported as the spec gives it - 0 for a clip scene, which has no R1 template yet."""
    spec = TEMPLATES[FALLBACK_TEMPLATE]
    return (FALLBACK_TEMPLATE, _fits(spec, scene, fmt, last=last, chapter_end=chapter_end), spec.semantic_class,
            False, False)


def row_template(scene: SceneBase, fmt: Format, presentation: Presentation | None, *, last: bool = False,
                 presenter: bool = True) -> str:
    """The template :func:`plan` gives ``scene`` (a screenplay scene) when the answer does not depend on its
    neighbours: :func:`choose` with none. The neighbours (R-20's repeat penalty, the share, the recent-use bonus) only
    move the score between the templates of one row; which row is taken, a lock, and a forced row's choice (taken in
    the row's own order) never depend on them. So this is :func:`plan`'s answer whenever the chosen row holds one
    candidate or is forced: ``recap_retrieval`` (the only template of the recap row) and ``aadhi_intro`` (the forced
    presenter row), which is what ``presenter.plan_appearances`` asks before the timeline exists. ``chapter_end`` is
    taken as False: it only adds ``takeaway_stamp`` (unimplemented, never a candidate) to the statement row."""
    ctx = _Context(fmt=fmt, total=1)
    return choose(scene, ctx, presentation, last=last, presenter=presenter)[0]


# --- variants -------------------------------------------------------------------------------------------------------


def variant_for(template_id: str, fmt: Format, presentation: Presentation | None, *, chapter: int,
                scene: SceneBase | None = None, icons: Mapping[str, object] | None = None) -> str | None:
    """The look variant of a chosen template (``ResolvedLook.variant``; None when it has none in ``fmt``).

    The rules, each from §4.3 / §4.4 and each a pure function of what the scene and the ``Presentation`` already say:

    * ``media_split``: the media side ("the side changes at chapter boundaries") is a function of the scene's
      ``chapter`` plus a per-lecture seed, so every scene of a chapter puts the medium on the same side and the side
      flips exactly at a boundary.
    * ``aadhi_intro`` / ``hook_question``: the teacher's stored choice (``Presentation.intro_layout``,
      ``hook_kind``) when it names one of the template's variants in this format.
    * ``check``: ``free_recall`` without options, ``true_false`` with two, else ``mcq`` (§4.3's data; 9:16 has only
      the first two): a 9:16 quiz of more than two options is the reel's two-option check (owner decision 4: the
      answer and the strongest distractor, ``reels.py``), stored as ``true_false``.
    * ``outro_next``: ``end_screen`` for the YouTube destination (§2.5 / §2.6: the right half clean for the end screen;
      ``compose/timeline.py`` then holds the outro to exactly ``Duration.end_screen``), else ``next_up``.
    * ``definition_card``: exactly what ``definition_card.js variantOf`` draws - ``card_image_left`` when the scene
      brings a picture the page shows (16:9 only; 9:16 holds the card alone), ``card_icon_right`` when the resolved
      look has an icon for the definition item (``icons``), else ``centred``. The resolver writes no ``look.icons``
      yet (B2e), so ``icons`` is None in every real build today and the honest stored variant is ``centred``: the
      two have different slot widths (1264 px vs 940 px), so a stored ``card_icon_right`` the page then draws as
      ``centred`` would fit text to a box 324 px too wide (B2-demo review D7).

    Any other template with variants takes the first of its list, which is deterministic until its own rule lands
    with its JS."""
    spec = TEMPLATES.get(template_id)
    if spec is None:
        return None
    options = tuple(spec.variants.get(fmt, ()))
    if not options:
        return None
    if template_id == "media_split" and options == MEDIA_SIDES:
        start = 1 if seed_for(presentation, "media_split", "side") >= 0.5 else 0
        return MEDIA_SIDES[(chapter + start) % 2]
    stored = {"aadhi_intro": getattr(presentation, "intro_layout", None),
              "hook_question": getattr(presentation, "hook_kind", None)}.get(template_id)
    if stored in options:
        return str(stored)
    if template_id == "check":
        count = len(getattr(scene, "options", ()) or ())
        wanted = "free_recall" if count == 0 else "true_false" if count == 2 or fmt == "portrait" else "mcq"
        if wanted in options:
            return wanted
    if template_id == "definition_card":
        # definition_card.js variantOf, item for item: a picture only in 16:9 (`pictureOf`, the panel kinds of
        # `PICTURE_PANELS` = templates.py DEFINITION_PANELS), an icon only when the look has one for the definition
        # item, else `centred` (its DEFAULT_VARIANT)
        panel = scene.side_panel.kind if scene is not None and scene.side_panel is not None else None
        picture = fmt == "landscape" and panel in DEFINITION_PANELS
        item = next((i for i in getattr(scene, "board", ())
                     if str(getattr(i.kind, "value", i.kind)) == "definition"), None)
        icon = item is not None and icons is not None and item.id in icons
        wanted = "card_image_left" if picture else "card_icon_right" if icon else "centred"
        if wanted in options:
            return wanted
    if template_id == "outro_next":
        # §2.5 / §2.6: the YouTube destination keeps end-screen space, the outro's right half clean for its last 15 s
        # (16:9 only: 9:16 has no variant); compose/timeline.py then holds the outro to exactly Duration.end_screen
        wanted = "end_screen" if getattr(presentation, "destination", None) == "youtube" else "next_up"
        if wanted in options:
            return wanted
    return options[0]


# --- chapters -------------------------------------------------------------------------------------------------------


def chapter_of(scenes: Sequence[TimedScene], chapters: Sequence[Chapter] | None) -> list[int]:
    """The chapter index of each timed scene (``presentation.chapter_indices``: the last chapter starting at or
    before the scene), 0 for every scene when there are no chapters. Imported at call time, so this module can be
    imported from ``compose.presentation`` without a cycle."""
    if not chapters:
        return [0] * len(scenes)
    from ..presentation import chapter_indices

    return chapter_indices(scenes, chapters)


# --- the entry point ------------------------------------------------------------------------------------------------


def plan(screenplay: Screenplay, scenes: Sequence[TimedScene], fmt: Format | None = None,
         chapters: Sequence[Chapter] | None = None, *, fallback_events: bool = True,
         presenter: bool = True) -> list[ScenePlan]:
    """One :class:`ScenePlan` per timed scene, in order: its template, variant and visual events.

    ``fmt``: the lecture's format (``screenplay.presentation.format`` when not given). ``chapters``:
    ``Timeline.chapters`` (``compose.timeline.build_chapters``), which decide the chapter boundaries a variant may
    change at; without them every scene is in chapter 0. A timed scene whose screenplay scene is missing falls back
    to ``board_generic`` with no events.

    ``fallback_events``: write events for a scene that fell back to ``board_generic`` too (the default). They are
    redundant - the stage computes exactly the same reveal and fill times from the beats when a scene carries no
    event (``state.js`` ``itemTimings``), so no frame moves either way - but they make a fallback scene's narrated
    moments countable (R-15) like any other. False leaves such a scene's ``events`` empty, which keeps a
    board_generic-only timeline byte for byte what B1 stored.

    The key-term budget (R-35: a term is marked on its first two narrated mentions) runs over the whole lecture, in
    scene order, so the same term is never marked again in a later scene."""
    presentation = screenplay.presentation
    if fmt is None:
        if presentation is None:
            raise ValueError("direct.plan needs a format, or a screenplay with a presentation")
        fmt = presentation.format
    if fmt not in SHARE_LIMIT:
        raise ValueError(f"unknown format {fmt!r}")
    source: Mapping[str, SceneBase] = {s.id: s for s in screenplay.scenes}
    numbers = chapter_of(scenes, chapters)
    terms = ev.TermBudget()
    used: dict[str, int] = {}
    recent: list[str] = []
    previous: str | None = None
    out: list[ScenePlan] = []
    for i, timed in enumerate(scenes):
        scene = source.get(timed.scene_id)
        if scene is None:
            # a scene the timeline builder synthesised: Aadhi's intro or recap (dynamic/presenter.py), forced; any
            # other timed scene without a screenplay scene falls back (never in a built timeline)
            from .presenter import plan_for  # lazy: only a Dynamic build with an appearance asks

            synth = plan_for(timed, screenplay, fmt) if presenter else None
            if synth is not None and candidate(synth[0], fmt) is not None:
                out.append(ScenePlan(timed.scene_id, synth[0], synth[1], (), 1.0, "structure", forced=True))
                previous = synth[0]
                used[previous] = used.get(previous, 0) + 1
                recent = [*recent, previous][-RECENT_SCENES:]
                continue
            out.append(ScenePlan(timed.scene_id, FALLBACK_TEMPLATE, None, (), 0.0, "fallback"))
            previous = FALLBACK_TEMPLATE
            continue
        ctx = _Context(fmt=fmt, total=len(scenes), previous=previous, recent=tuple(recent), used=used)
        last = i == len(scenes) - 1
        chapter_end = last or numbers[i + 1] != numbers[i]
        template, fit, klass, forced, locked = choose(scene, ctx, presentation, last=last, chapter_end=chapter_end,
                                                      presenter=presenter)
        spec = TEMPLATES[template]
        hold = scene.look.hold_reason if scene.look is not None else None
        events = ([] if template == FALLBACK_TEMPLATE and not fallback_events
                  else ev.scene_events(timed, accepts=spec.consumes.accepts, hold_reason=hold, terms=terms))
        if (fmt == "portrait" and template in reels.REEL_PANEL_TEMPLATES and timed.side_panel is not None
                and timed.layout.show_side_panel):
            # owner decision 4: the reel side panel is its own beat before the content (reels.py)
            events = reels.reel_events(timed, events, accepts=spec.consumes.accepts)
        out.append(ScenePlan(scene_id=timed.scene_id, template=template,
                             variant=variant_for(template, fmt, presentation, chapter=numbers[i], scene=scene),
                             events=tuple(events), fit=fit, semantic_class=klass, forced=forced, locked=locked))
        used[template] = used.get(template, 0) + 1
        recent = [*recent, template][-RECENT_SCENES:]
        previous = template
    return out


def template_counts(plans: Sequence[ScenePlan]) -> dict[str, int]:
    """How many scenes each template drew (the variety figure §4.4's post-pass and the demo's report want)."""
    out: dict[str, int] = {}
    for p in plans:
        out[p.template] = out.get(p.template, 0) + 1
    return out


__all__ = ["ScenePlan", "candidate", "candidates", "chapter_of", "choose", "plan", "row_template", "score", "seed_for",
           "template_counts", "variant_for"]
