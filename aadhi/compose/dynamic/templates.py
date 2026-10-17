"""The scene-template registry of the Dynamic video mode (DYNAMIC_VIDEO_DESIGN §4.2 template contract, §4.3 catalogue
of 41 templates, §4.4 signal table, R-28, R-29; B2 plan §1.6-§1.9; deviation register 5).

What is here (declarative data and pure functions, no I/O):

* :data:`SEMANTIC_CLASSES`: §4.2's 19 classes, their claims and members, frozen (plan §1.8). Every template's class is
  in it and every member is a template (``tests/compose/dynamic/test_templates.py``).
* :data:`TEMPLATES`: one :class:`TemplateSpec` per §4.3 template (41: 23 ``R1``, 17 ``W2``, ``clip_scene`` ``B6``).
  ``implemented`` is True only where ``web/js/dynamic/templates/<id>.js`` exists (``board_generic`` since B1; the B2c
  pilots ``formula_focus``, ``media_split`` and ``list_build``); direct.py (B2e) picks only implemented templates, and
  until then the resolver draws ``board_generic`` only (``presentation.TEMPLATES``), so the pilots are reached only by
  timelines that name them (fixtures, gallery, tests). ``slots`` hold the measured-text geometry of the implemented ones
  (``board_generic``: its ``LAYOUT`` / ``MIN_*`` constants; each pilot: its JS ``LAYOUT``); the others' slots arrive
  with their JS (B2f).
* :func:`signals` (the §4.4 table rows a scene matches) and ``TemplateSpec.fit`` (how well a template renders a scene,
  0 = cannot): deterministic, documented below; direct.py (B2e) builds its choice on them.
* The **target grammar** of visual events (plan §1.7): :func:`parse_target` / :func:`format_target`, the closed list
  of target forms (:data:`TARGET_FORMS`) and ``TemplateSpec.consumes`` (the event kinds and target forms a template
  draws).
* :data:`ROLES` (plan §1.9, the closed colour-role list of §4.2) and the stage registries of backgrounds (§4.5) and
  transitions (§4.9).

``templates.py`` and ``events.py`` (B2d) never import each other (plan §1.6): the specs are declarative data, and
whoever calls events.py hands it what it needs. Imported only inside the Dynamic gate, never by a classic build.

**How ``fit`` works.** ``spec.fit(scene, fmt)`` is ``0`` when the template cannot render the scene: the format is not
in ``formats``, or its §4.3 data requirements are not met (``data_fit``: the item kinds and counts it lays out, the
panel it draws, the scene type it is forced for). Otherwise its value comes from the §4.4 rows the scene matches
(:func:`signals`): :data:`FIT_PREFERRED` when a row names the template as preferred (or forced), :data:`FIT_ALTERNATIVE`
when a row names it as an alternative, else ``0`` (variety never crosses a claim, §4.1 principle 3), capped at
:data:`FIT_STRETCHED` when the data exceeds a text limit §4.3 states (words per item): the page measures
the pixels (plan §1.11), so such a scene still renders, but is never a post-pass swap target (``fit >= 0.6``, §4.4).
``board_generic`` is :data:`FIT_PREFERRED` for a board scene no other row claims (the §4.4 fallback row) and
:data:`FIT_FALLBACK` for any other scene it can draw (B1 draws every non-clip scene type with it). Count limits that
change a layout's structure (2-4 key terms, 3-6 flow steps, <= 4 versus rows) give 0, except for a forced template on
its forced scene type, which never returns 0 there (direct.py has no other choice): beyond its limits it is stretched.
Positional rows (the last scene's ``outro_next``; ``takeaway_stamp`` at a chapter end) take ``last`` /
``chapter_end`` from the caller.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final, Literal, get_args

from ...schemas.screenplay import (
    AIVideoScene,
    BoardItem,
    BoardScene,
    ChapterCardScene,
    ClipScene,
    InteractiveScene,
    QuizScene,
    SceneBase,
    SimulationScene,
)
from ...schemas.timeline import TimedScene, VisualEvent, VisualEventKind
from . import zones as zn
from .charts import chart_build_slot_rows, chart_problem, draws_chart
from .presenter import intro_slots, recap_slots
from .themes import DERIVED_KEYS, PERSONALITIES
from .zones import FORMATS, Format

SemanticClass = Literal[
    "structure", "prompt", "definition", "statement", "enumeration", "contrast", "correspondence", "sequence", "cause",
    "completion", "hierarchy", "quantity", "formula", "procedure", "code", "table", "refutation", "media", "fallback",
]
Release = Literal["R1", "W2", "B6"]  # §4.3: R1 = B2 builds them; W2 = B7; clip_scene ships in B6
HoldReason = Literal["formula", "code", "diagram", "think"]  # SceneLook.hold_reason (R-16)
FaceRole = Literal["display", "body", "mono", "indic"]  # themes.FaceRole
TargetForm = Literal[
    "item", "item:line", "item:var", "item:step", "item:row", "item:column", "item:term",
    "panel", "panel:bar", "panel:point", "panel:row", "panel:column", "option",
]

HOLD_REASONS: Final[tuple[HoldReason, ...]] = get_args(HoldReason)
EVENT_KINDS: Final[tuple[VisualEventKind, ...]] = get_args(VisualEventKind)
FALLBACK_TEMPLATE: Final = "board_generic"

# §4.4 scores (see the module docstring); the post-pass swaps only to fit >= 0.6.
FIT_NONE: Final = 0.0
FIT_PREFERRED: Final = 1.0
FIT_ALTERNATIVE: Final = 0.8
FIT_STRETCHED: Final = 0.5
FIT_FALLBACK: Final = 0.2
SWAP_MIN_FIT: Final = 0.6  # §4.4 post-pass: "the same-class swap with fit >= 0.6"


# --- semantic classes (§4.2, plan §1.8: frozen) ---------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticClassRow:
    """One row of §4.2's class table: what a template of the class claims about its content, and its members."""

    id: SemanticClass
    claim: str
    templates: tuple[str, ...]


SEMANTIC_CLASSES: Final[Mapping[SemanticClass, SemanticClassRow]] = MappingProxyType({r.id: r for r in (
    SemanticClassRow("structure", "Orientation, not content",
                     ("aadhi_intro", "aadhi_recap", "chapter_bumper", "roadmap", "outro_next", "thumbnail")),
    SemanticClassRow("prompt", "Think about this", ("hook_question", "predict", "check", "recap_retrieval")),
    SemanticClassRow("definition", "X means Y", ("key_terms", "definition_card")),
    SemanticClassRow("statement", "One principle in words (typography only)",
                     ("kinetic_statement", "takeaway_stamp", "quote_card")),
    SemanticClassRow("enumeration", "Unordered members of a set", ("list_build",)),
    SemanticClassRow("contrast", "A differs from B", ("versus",)),
    SemanticClassRow("correspondence", "A maps to B (analogy)", ("analogy_bridge",)),
    SemanticClassRow("sequence", "Ordered steps or times", ("process_flow", "cycle_loop", "timeline_track")),
    SemanticClassRow("cause", "A causes B", ("cause_chain",)),
    SemanticClassRow("completion", "Items done or required", ("checklist",)),
    SemanticClassRow("hierarchy", "Parts of a whole", ("concept_tree",)),
    SemanticClassRow("quantity", "Numbers and functions", ("stat_punch", "chart_build", "function_plot")),
    SemanticClassRow("formula", "An equation and its symbols", ("formula_focus",)),
    SemanticClassRow("procedure", "A worked example", ("worked_steps",)),
    SemanticClassRow("code", "A program", ("code_walkthrough",)),
    SemanticClassRow("table", "A grid of attributes", ("table_reveal",)),
    SemanticClassRow("refutation", "A belief corrected", ("myth_fact",)),
    SemanticClassRow("media", "A representational picture or motion",
                     ("media_split", "full_bleed_media", "spotlight_image", "diagram_build", "model_spin",
                      "animation_stage", "interactive_poster", "clip_scene")),
    SemanticClassRow("fallback", "Anything", ("board_generic",)),
)})


# --- colour roles (§4.2, plan §1.9) ---------------------------------------------------------------------------------

# The closed list a template may ask ``roles.color()`` for (core.js ROLES, in its order): content roles, then the
# structure roles, which may colour structure elements only (R-52; the source scan's struct-role rule).
ROLES: Final = ("ink", "ink2", "surface", "entity:0", "entity:1", "entity:2", "warn", "ok", "mark", "focus",
                "struct.accent", "struct.tint", "struct.border", "struct.pip")
# The block roles (plan §1.9 "block roles from look.tokens"): the derived structure colours the resolver stores per
# scene (themes.DERIVED_KEYS): a colour block's base and highlight, the text on it, and the title-block scrim.
BLOCK_ROLES: Final = DERIVED_KEYS


# --- the target grammar (plan §1.7) ---------------------------------------------------------------------------------
# events.py writes VisualEvent.target in it; the stage's focusRect / itemEl (and cues.js) read it. ``<item>`` is a board
# item id (a screenplay slug, which has no ':'); indices are 0-based decimal integers without leading zeros (as
# sync.py's ``column:<n>`` parts and the quiz's option order); ``<series>`` is a 0-based chart dataset index (a label
# can change with translation, §6.11); ``<symbol>`` is the formula symbol as written (``FormulaVariable.symbol_latex``),
# everything after ``var:``, never an index: B1's sync cue part ``var:<n>`` (sync.py, the n-th of the item's
# ``variables``) becomes the target ``<item>:var:<variables[n].symbol_latex>`` when events.py (B2d) writes it, so
# ``f1:var:0`` names the symbol ``0``. ``panel`` and ``option`` are reserved: an item with that id cannot be a target.
# ``<item>:term:<n>`` (B2c, additive) is the n-th ``[[term]]`` of the item, 0-based, counted over its rich-lite fields
# in the order ``term``, ``text``, ``justification``, ``caption`` (each field's terms in reading order; the order
# board_generic draws them in): the one key term a ``cue_term`` event marks when the item has several; ``<item>``
# alone marks every term of the item. A template's ``itemEl`` hands the stage the item's term spans in that order, so
# a 9:16 misconception drawn as two cards (myth, then fact) reaches the correction's terms too.

TARGET_FORMS: Final[tuple[TargetForm, ...]] = get_args(TargetForm)
RESERVED_TARGET_WORDS: Final = frozenset({"panel", "option"})
_SLUG = re.compile(r"[a-z0-9][a-z0-9_\-]{0,63}")  # screenplay Slug (_SLUG_RE)
_INDEX = re.compile(r"0|[1-9][0-9]{0,5}")
_SYMBOL_MAX = 80  # Presentation.entity_colors key length
_ITEM_PARTS: Final = {"line": "item:line", "step": "item:step", "row": "item:row", "column": "item:column",
                       "term": "item:term"}
_PANEL_PARTS: Final = {"row": "panel:row", "column": "panel:column"}


@dataclass(frozen=True)
class Target:
    """A parsed visual-event target: its ``form`` and the parts it has (``item`` id, ``index``, ``series``,
    ``symbol``)."""

    form: TargetForm
    item: str | None = None
    index: int | None = None
    series: int | None = None
    symbol: str | None = None

    def __str__(self) -> str:
        return format_target(self)


def _index(text: str, whole: str) -> int:
    if not _INDEX.fullmatch(text):
        raise ValueError(f"bad target {whole!r}: {text!r} is not an index (0, 1, 2, ...)")
    return int(text)


def parse_target(text: str) -> Target:
    """The :class:`Target` of ``text`` (plan §1.7 grammar); ``ValueError`` when it is not in the grammar."""
    if not isinstance(text, str) or not text:
        raise ValueError(f"bad target {text!r}: empty")
    head, sep, rest = text.partition(":")
    if head == "panel":
        if not sep:
            return Target("panel")
        part, _, arg = rest.partition(":")
        if part in _PANEL_PARTS:
            return Target(_PANEL_PARTS[part], index=_index(arg, text))  # type: ignore[arg-type]
        if part in ("bar", "point"):
            series, sep2, i = arg.partition(":")
            if not sep2:
                raise ValueError(f"bad target {text!r}: panel:{part} needs <series>:<i>")
            return Target(f"panel:{part}", series=_index(series, text), index=_index(i, text))  # type: ignore[arg-type]
        raise ValueError(f"bad target {text!r}: unknown panel part {part!r}")
    if head == "option":
        if not sep:
            raise ValueError(f"bad target {text!r}: option needs an index (option:<i>)")
        return Target("option", index=_index(rest, text))
    if not _SLUG.fullmatch(head):
        raise ValueError(f"bad target {text!r}: {head!r} is not a board item id")
    if not sep:
        return Target("item", item=head)
    part, sep2, arg = rest.partition(":")
    if not sep2:
        raise ValueError(f"bad target {text!r}: {part!r} needs a value")
    if part == "var":
        if not arg or arg != arg.strip() or len(arg) > _SYMBOL_MAX or any(ord(c) < 32 for c in arg):
            raise ValueError(f"bad target {text!r}: bad symbol {arg!r}")
        return Target("item:var", item=head, symbol=arg)
    if part in _ITEM_PARTS:
        return Target(_ITEM_PARTS[part], item=head, index=_index(arg, text))  # type: ignore[arg-type]
    raise ValueError(f"bad target {text!r}: unknown item part {part!r}")


def format_target(target: Target) -> str:
    """The canonical string of ``target`` (``parse_target(format_target(t)) == t``); ``ValueError`` when incomplete."""
    form = target.form
    if form == "panel":
        return "panel"
    if form in ("panel:row", "panel:column"):
        return f"{form}:{_check_index(target.index)}"
    if form in ("panel:bar", "panel:point"):
        return f"{form}:{_check_index(target.series)}:{_check_index(target.index)}"
    if form == "option":
        return f"option:{_check_index(target.index)}"
    item = target.item
    if item is None or not _SLUG.fullmatch(item) or item in RESERVED_TARGET_WORDS:
        raise ValueError(f"bad target item {item!r}")
    if form == "item":
        return item
    if form == "item:var":
        out = f"{item}:var:{target.symbol}"
    elif form in _ITEM_PARTS.values():
        out = f"{item}:{form.split(':')[1]}:{_check_index(target.index)}"
    else:
        raise ValueError(f"unknown target form {form!r}")
    parse_target(out)  # the symbol's rules
    return out


def _check_index(i: int | None) -> int:
    if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i <= 999_999:
        raise ValueError(f"bad target index {i!r}")
    return i


def target_form(text: str) -> TargetForm:
    """The form of target ``text`` (``ValueError`` when it is not in the grammar)."""
    return parse_target(text).form


# --- spec parts ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Duration:
    """A template's scene length in seconds (§4.3 "Timing"): ``min``..``max`` (None: no upper bound, the clip's own
    length); ``per_item``: the range is per item (key term, list item), not per scene; ``end_screen``: the one other
    length allowed when the lesson keeps YouTube "end-screen space" (R-11, §4.3 ``outro_next`` "6–10 s or 15 s
    (16:9)": exactly :data:`zones.END_SCREEN` ``.seconds``, never a length between ``max`` and it), else None."""

    min: float
    max: float | None
    per_item: bool = False
    end_screen: float | None = None

    def __post_init__(self) -> None:
        if self.min < 0 or (self.max is not None and self.max < self.min):
            raise ValueError(f"bad duration {self!r}")
        if self.end_screen is not None and (self.max is None or self.per_item or self.end_screen <= self.max):
            raise ValueError(f"bad end-screen length in {self!r}")


@dataclass(frozen=True)
class SlotSpec:
    """The geometry of one text slot (plan §1.6, §1.11): ``w`` the widest box the text is laid out in (px; None: the
    text sizes its own box, a chip), ``max_lines`` (None: no line limit, the template fits by scale), ``size`` the
    (min, max) font size in px (the page starts at ``text_fit.size`` <= max and never goes below min), ``role`` (the
    TextEntry role it reports), ``face`` (the type role), ``weight``, ``line_height``, ``tracking_em`` (letter-spacing,
    which measured fit must include), the text limits ``max_words`` / ``max_chars`` (None: none stated),
    ``prefer_lines`` (fewer lines preferred while they fit at >= min), whether the text is ``critical`` (safe zones
    and contrast are checked, §7.12), and ``tabular_nums``: the stage draws it with ``font-variant-numeric:
    tabular-nums`` (measured fit shapes it with ``tnum``)."""

    w: int | None
    max_lines: int | None
    size: tuple[float, float]
    role: str
    face: FaceRole
    weight: int
    line_height: float
    tracking_em: float = 0.0
    max_words: int | None = None
    max_chars: int | None = None
    prefer_lines: int | None = None
    critical: bool = True
    tabular_nums: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.size[0] <= self.size[1]:
            raise ValueError(f"bad slot size {self.size!r}")


@dataclass(frozen=True)
class Consumes:
    """The visual-event kinds a template draws and the target forms it resolves (plan §1.7; the stage-driven cues of
    plan §1.5 resolve ``cue_term`` / ``look_here`` targets through the template's ``itemEl`` / ``focusRect``)."""

    kinds: tuple[VisualEventKind, ...] = ()
    targets: tuple[TargetForm, ...] = ()
    # kinds the template draws on fewer forms than ``targets`` (B2c review CG-5): (kind, its forms) pairs. Without it,
    # ``accepts`` was the cross product of kinds and forms, which let through pairs no template draws (a cue_term on a
    # panel, a look_here on a term media_split did not resolve, a var on a whole item); events.py (B2d), the fixture
    # validator and the stage's cues.js all gate on the per-kind forms
    narrow: tuple[tuple[VisualEventKind, tuple[TargetForm, ...]], ...] = ()

    def forms(self, kind: str) -> tuple[TargetForm, ...]:
        """The target forms an event of ``kind`` may name (none for a kind the template does not draw)."""
        if kind not in self.kinds:
            return ()
        return next((forms for k, forms in self.narrow if k == kind), self.targets)

    def accepts(self, kind: str, target: str | None) -> bool:
        """True when an event of ``kind`` on ``target`` (None: the scene) is drawn by the template."""
        if kind not in self.kinds:
            return False
        if target is None:
            return True
        try:
            return parse_target(target).form in self.forms(kind)
        except ValueError:
            return False


SlotTable = Mapping[str, Mapping[Format, Mapping[str, SlotSpec]]]
DataFit = Callable[[SceneBase, Format], float]


@dataclass(frozen=True)
class TemplateSpec:
    """One scene template (§4.2, plan §1.6; deviation 5: ``implemented``, ``dense``, ``push_ok``, ``slots``,
    ``consumes`` are B2 additions).

    * ``semantic_class``: its §4.2 class; ``representational``: its own layout is a meaning-bearing visual (a picture,
      clip, animation, model, chart or plot, or a diagram of the content's relations: flow, cycle, timeline, chain,
      tree, mapping, comparison grid, table, an equation with its symbol mapping, a worked derivation, a program);
      False for set text with at most redundant icons ("typography alone", R-28).
    * ``release`` (§4.3), ``implemented`` (its JS module exists; only those are picked), ``formats`` (``thumbnail`` is
      16:9 only), ``variants`` per format (the look variants direct.py may choose; none named: empty, and
      ``ResolvedLook.variant`` stays None), ``forced_for`` (scene types it must render).
    * ``duration`` per format (§4.3 "Timing"; R-19's scene length where §4.3 gives none), ``holds`` (the
      ``hold_reason`` values it may carry, R-16), ``dense`` (formula, code, worked example: the background freezes,
      R-55; no SFX, R-49; music off with 1.5 s fades, R-44 in B3), ``push_ok`` (``cue.look_here`` may use the camera
      push form, plan §1.5; True only where §4.3 calls for a push; the outline form is every template's default).
    * ``slots``: ``{slot: {format: {variant: SlotSpec}}}`` (empty until the template's JS exists), ``consumes``.
    * ``data_fit``: its §4.3 data requirements (see the module docstring); use :meth:`fit`."""

    id: str
    semantic_class: SemanticClass
    representational: bool
    release: Release
    duration: Mapping[Format, Duration]
    consumes: Consumes
    data_fit: DataFit = field(repr=False, compare=False)
    implemented: bool = False
    formats: tuple[Format, ...] = FORMATS
    variants: Mapping[Format, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({f: () for f in FORMATS}))
    forced_for: tuple[str, ...] = ()
    holds: tuple[HoldReason, ...] = ()
    dense: bool = False
    push_ok: bool = False
    slots: SlotTable = field(default_factory=lambda: MappingProxyType({}))

    def fit(self, scene: SceneBase, fmt: Format, *, last: bool = False, chapter_end: bool = False) -> float:
        """How well this template renders ``scene`` in ``fmt`` (0 = cannot; see the module docstring). ``last``: the
        scene is the video's last shown scene; ``chapter_end``: the last of its chapter (positional §4.4 rows)."""
        if fmt not in self.formats:
            return FIT_NONE
        data = self.data_fit(scene, fmt)
        if data <= 0:
            return FIT_NONE
        rows = signals(scene, last=last, chapter_end=chapter_end)
        if self.id == FALLBACK_TEMPLATE:
            if isinstance(scene, BoardScene) and [r.id for r in rows] == ["fallback"]:
                return FIT_PREFERRED
            return FIT_FALLBACK
        best = FIT_NONE
        for row in rows:
            if row.preferred == self.id:
                best = max(best, FIT_PREFERRED)
            elif self.id in row.alternatives:
                best = max(best, FIT_ALTERNATIVE)
        return min(best, data)

    def events(self, scene: SceneBase, timed: TimedScene, fmt: Format) -> list[VisualEvent]:
        """The template's own extra events (plan §1.6; events.py adds them to the anchored ones, B2d). None yet."""
        return []


# --- scene signals (§4.4) --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """A §4.4 signal-table row a scene matches: its class, the preferred template and the named alternatives.
    ``forced``: the scene type or presenter forces the template (exempt from the repeat and share penalties)."""

    id: str
    semantic_class: SemanticClass
    preferred: str
    alternatives: tuple[str, ...] = ()
    forced: bool = False


# The board item kinds counted as "text items" by the media row (≤ 3 text items + a media panel).
TEXT_KINDS: Final = frozenset({"heading", "bullet", "paragraph", "definition", "takeaway", "callout_info",
                               "callout_tip", "callout_warning"})
STATEMENT_KINDS: Final = frozenset({"takeaway", "paragraph", "heading", "callout_info", "callout_tip",
                                    "callout_warning"})
MEDIA_PANELS: Final = ("figure", "image", "chart", "graph", "terminal", "manim", "model_3d")
# web/js/dynamic/templates/_shared.js PANEL_MEDIA_KINDS / 'terminal' / PANEL_TITLED_KINDS: the side panels the shared
# panel helper draws for any template that uses it - a resolved medium, a terminal card, or (for a chart, graph or
# model panel, which the Dynamic engine draws nowhere yet: chart_build, function_plot and model_spin are their own
# templates, and board_generic draws none of them either) the panel's own authored title on the card surface.
# web/tests/dynamic/templates/shared.test.js pins the JS lists to these.
SHARED_PANEL_MEDIA: Final = ("figure", "image", "manim", "gif")
SHARED_PANEL_TITLED: Final = ("chart", "graph", "model_3d")
SHARED_PANELS: Final = (*SHARED_PANEL_MEDIA, "terminal", *SHARED_PANEL_TITLED)
# _shared.js PANEL_TERM: the mono text of a side panel's terminal card per format, (min, max, weight, padding)
# (media_split's `terminal` slot; 9:16 at weight 700, R-29: JetBrains Mono has 400 and 700 only).
SHARED_PANEL_TERM: Final[Mapping[Format, tuple[float, float, int, int]]] = MappingProxyType(
    {"landscape": (36.0, 44.0, 400, 32), "portrait": (40.0, 44.0, 700, 28)})
_FORCED_TYPES: Final[tuple[tuple[type[SceneBase], Signal], ...]] = (
    (ChapterCardScene, Signal("chapter_card", "structure", "chapter_bumper", forced=True)),
    (QuizScene, Signal("quiz", "prompt", "check", forced=True)),
    (SimulationScene, Signal("simulation", "media", "animation_stage", forced=True)),
    (AIVideoScene, Signal("ai_video", "media", "full_bleed_media", forced=True)),
    (InteractiveScene, Signal("interactive", "media", "interactive_poster", forced=True)),
    (ClipScene, Signal("clip", "media", "clip_scene", forced=True)),
)
# Cross-class alternatives the §4.4 table names explicitly (every other alternative is in its row's class).
CROSS_CLASS_ALTERNATIVES: Final = frozenset({("presenter", "hook_question"), ("hook", "stat_punch")})
_TEX = re.compile(r"(?<!\\)\$[^$]*\$")


def _items(scene: SceneBase) -> list[BoardItem]:
    return list(scene.board) if isinstance(scene, BoardScene) else []


def _kinds(scene: SceneBase) -> Counter[str]:
    return Counter(str(getattr(i.kind, "value", i.kind)) for i in _items(scene))


def _of(scene: SceneBase, kind: str) -> list[BoardItem]:
    return [i for i in _items(scene) if str(getattr(i.kind, "value", i.kind)) == kind]


def _shape(scene: SceneBase, need: Mapping[str, tuple[int, int | None]],
           extra: Mapping[str, int] = MappingProxyType({"heading": 1})) -> bool:
    """True when a board scene's items are exactly ``need`` ({kind: (min, max)}) plus at most ``extra`` of others."""
    if not isinstance(scene, BoardScene):
        return False
    counts = _kinds(scene)
    for kind, n in counts.items():
        if kind not in need and n > extra.get(kind, 0):
            return False
    return all(lo <= counts.get(k, 0) and (hi is None or counts.get(k, 0) <= hi) for k, (lo, hi) in need.items())


def _role(scene: SceneBase) -> str | None:
    return scene.intent.narrative_role if scene.intent is not None else None


def _intent(scene: SceneBase) -> str | None:
    return scene.intent.visual_intent if scene.intent is not None else None


def _panel(scene: SceneBase) -> str | None:
    return scene.side_panel.kind if scene.side_panel is not None else None


def _draws_panel(scene: SceneBase, fmt: Format = "landscape") -> bool:
    """Whether ``_shared.js drawsPanel(scene, fmt)`` draws the scene's side panel: it has none, or one of
    :data:`SHARED_PANELS` - a ``chart`` / ``graph`` drawn as a real plot when the chart engine takes it in the format's
    layout (``charts.draws_chart``: 16:9 the template's side box, 9:16 the reel beat's column; owner decision 1), else
    like a ``model_3d`` panel only when it has the title that is all the shared helper can draw for it. A template
    whose page uses the helper must ask exactly this in its ``fit`` (B2c review CG-2: a fit that takes a scene the page
    refuses makes the director pick the template and the page silently draw board_generic)."""
    panel = scene.side_panel
    if panel is None:
        return True
    kind = str(getattr(panel.kind, "value", panel.kind))
    if kind not in SHARED_PANELS:
        return False
    if kind == "terminal":
        return panel.terminal is not None
    # a chart or graph the chart engine draws as a plot (_shared.js panelOf's layout: the reel beat's column in 9:16,
    # the template's side box in 16:9)
    if kind in ("chart", "graph") and draws_chart(panel, "portrait" if fmt == "portrait" else "panel"):
        return True
    if kind in SHARED_PANEL_TITLED:
        return bool((panel.title or "").strip())
    return True


def _panel_terminal_slot(width: int, fmt: Format) -> SlotSpec:
    """The slot of a side panel's terminal card in a panel box ``width`` px wide (``_shared.js PANEL_TERM``, which
    ``layoutPanel`` fits on the page and ``panelTexts`` reports in the ``terminal`` slot)."""
    low, high, weight, pad = SHARED_PANEL_TERM[fmt]
    return SlotSpec(w=width - 2 * pad, max_lines=None, size=(low, high), role="body", face="mono", weight=weight,
                    line_height=1.35)


def _presenter(scene: SceneBase) -> bool:
    return scene.look is not None and scene.look.presenter is not None


def _sequence(scene: SceneBase) -> str | None:
    return scene.sequence if isinstance(scene, BoardScene) else None


def _words(text: str | None) -> int:
    """Words as the R-25 lint counts them: whitespace-delimited, a ``$TeX$`` span one word."""
    return len(_TEX.sub(" x ", text).split()) if text else 0


def _phrase_text(item: BoardItem) -> str:
    """A text item as one run, the way a template draws it (media_split.js ``itemString``): a definition's term joins
    its meaning, every other kind is its text alone."""
    term = item.term if str(getattr(item.kind, "value", item.kind)) == "definition" else None
    return f"{term or ''} {item.text or ''}".strip()


def _table(scene: SceneBase) -> BoardItem | None:
    """The scene's only table item (with at most one heading beside it), else None."""
    return _of(scene, "table")[0] if _shape(scene, {"table": (1, 1)}) else None


def _columns(item: BoardItem) -> int:
    return len(item.headers or [])


def _media_board(scene: SceneBase) -> bool:
    """§4.4 media row: <= 3 text items and a media panel (or one figure item on the board), nothing else."""
    if not isinstance(scene, BoardScene):
        return False
    counts = _kinds(scene)
    if any(k not in TEXT_KINDS and k != "figure" for k in counts) or counts.get("figure", 0) > 1:
        return False
    texts = sum(n for k, n in counts.items() if k in TEXT_KINDS)
    return texts <= 3 and (_panel(scene) in MEDIA_PANELS or counts.get("figure", 0) == 1)


# The one top line ``worked_steps.js`` draws above the steps (its TOP_KINDS: a heading, or the setup line the
# generator writes as a ``callout_info`` or a ``paragraph``), and with an optional formula the only non-step items a
# worked example may carry.
WORKED_TOP: Final = ("heading", "callout_info", "paragraph")
WORKED_EXTRA: Final[Mapping[str, int]] = MappingProxyType({**{k: 1 for k in WORKED_TOP}, "formula": 1})


def _worked_shape(scene: SceneBase) -> bool:
    """The board shape ``worked_steps`` draws: ``example_step`` items, at most one top line and at most one formula.

    The §4.4 ``example`` row and :func:`_fit_worked_steps` both ask this one question, so neither can accept a shape
    the other refuses: ``fit`` is ``min(best, data)``, so a shape only one of them takes scores 0 and the template is
    unreachable (B2-demo review D1, where the data fit was widened and the row was not)."""
    if not _shape(scene, {"example_step": (1, None)}, WORKED_EXTRA):
        return False
    counts = _kinds(scene)
    return sum(counts.get(k, 0) for k in WORKED_TOP) <= 1


def signals(scene: SceneBase, *, last: bool = False, chapter_end: bool = False) -> tuple[Signal, ...]:
    """Every §4.4 row ``scene`` (a screenplay scene) matches, in the table's order. A scene type that forces a template
    matches that row alone. A board scene may match several rows (a takeaway beside a picture: ``statement`` and
    ``media``); direct.py (B2e) takes the first whose class has a candidate with ``fit > 0``, keeping R-28 (never a
    typography-only template when a representational one fits). Rows: presenter (forced ``aadhi_intro``), hook,
    pretraining, definition, statement, quote, list, sequence (flow / timeline / cycle), chain, checklist, pairs,
    compare, table, stat, chart, graph, formula, example, code, misconception, predict / check, media, skill_tree,
    recap (``aadhi_recap`` with the presenter), last (``last=True``), and fallback when no other row matches.

    Two documented readings of §4.4: the statement row also takes a single ``callout_*`` item (``kinetic_statement``'s
    §4.3 data), and the media row names ``animation_stage`` (a ``manim`` panel) and ``model_spin`` (a ``model_3d``
    panel) as alternatives, the panels their §4.3 data names."""
    for kind, row in _FORCED_TYPES:
        if isinstance(scene, kind):
            return (row,)
    if not isinstance(scene, BoardScene):
        return ()
    out: list[Signal] = []
    role, kinds = _role(scene), _kinds(scene)
    if _presenter(scene) and scene.type != "recap":
        out.append(Signal("presenter", "structure", "aadhi_intro", ("hook_question",), forced=True))
    if role == "hook":
        out.append(Signal("hook", "prompt", "hook_question", ("stat_punch",)))
    if role == "pretraining":
        out.append(Signal("pretraining", "definition", "key_terms"))
    if _shape(scene, {"definition": (1, 1)}, {"bullet": 1}):
        out.append(Signal("definition", "definition", "definition_card"))
    if sum(kinds.values()) == 1 and next(iter(kinds)) in STATEMENT_KINDS:
        out.append(Signal("statement", "statement", "kinetic_statement", ("takeaway_stamp",) if chapter_end else ()))
    if _shape(scene, {"quote": (1, 1)}, {}):
        out.append(Signal("quote", "statement", "quote_card", ("kinetic_statement",)))
    if _shape(scene, {"bullet": (2, None)}):
        out.append(Signal("list", "enumeration", "list_build"))
    seq = _sequence(scene)
    if seq is not None and _shape(scene, {"step": (1, None)}):
        if seq == "flow":
            out.append(Signal("sequence", "sequence", "process_flow", ("timeline_track",)))
        elif seq == "timeline":
            out.append(Signal("sequence", "sequence", "timeline_track", ("process_flow",)))
        elif seq == "cycle":
            out.append(Signal("sequence", "sequence", "cycle_loop"))
        elif seq == "chain":
            out.append(Signal("chain", "cause", "cause_chain"))
        elif seq == "checklist":
            out.append(Signal("checklist", "completion", "checklist"))
    if _shape(scene, {"pair": (2, None)}):
        out.append(Signal("pairs", "correspondence", "analogy_bridge"))
    table = _table(scene)
    compare = _intent(scene) == "compare"
    if (table is not None and (_columns(table) == 2 or (compare and _columns(table) == 3))) or (
            compare and _shape(scene, {"definition": (2, 2)})):
        out.append(Signal("compare", "contrast", "versus"))
    if table is not None and _columns(table) >= 3:
        out.append(Signal("table", "table", "table_reveal"))
    if _shape(scene, {"stat": (1, 1)}):
        out.append(Signal("stat", "quantity", "stat_punch"))
    if _panel(scene) == "chart":
        out.append(Signal("chart", "quantity", "chart_build"))
    if _panel(scene) == "graph":
        # chart_build draws a graph as a real plot while function_plot (W2) is unbuilt (owner decision 1, 2026-10-16)
        out.append(Signal("graph", "quantity", "function_plot", ("chart_build",)))
    if _shape(scene, {"formula": (1, 1)}):
        out.append(Signal("formula", "formula", "formula_focus"))
    if _worked_shape(scene):
        out.append(Signal("example", "procedure", "worked_steps"))
    if _shape(scene, {"code": (1, 1)}):
        out.append(Signal("code", "code", "code_walkthrough"))
    if _myth_fact_shape(scene):
        out.append(Signal("misconception", "refutation", "myth_fact"))
    if role in ("predict", "check") and any(q.blank for q in _of(scene, "question")):
        out.append(Signal(role, "prompt", role))
    if _media_board(scene):
        out.append(Signal("media", "media", "media_split",
                          ("spotlight_image", "full_bleed_media", "diagram_build", "animation_stage", "model_spin")))
    if _panel(scene) == "skill_tree":
        out.append(Signal("skill_tree", "hierarchy", "concept_tree"))
    if scene.type == "recap":
        out.append(Signal("recap_presenter", "structure", "aadhi_recap") if _presenter(scene)
                   else Signal("recap", "prompt", "recap_retrieval"))
    if last:
        out.append(Signal("last", "structure", "outro_next"))
    if not out:
        out.append(Signal("fallback", "fallback", FALLBACK_TEMPLATE))
    return tuple(out)


# --- data requirements of each template (§4.3) ----------------------------------------------------------------------


def _ok(within: bool) -> float:
    """1.0 when the data is within the template's stated text limits, else FIT_STRETCHED."""
    return FIT_PREFERRED if within else FIT_STRETCHED


def _never(scene: SceneBase, fmt: Format) -> float:
    return FIT_NONE


# The words a presenter scene's hook / prompt may have (§3.3: 16:9 the intro's hook is part of Aadhi's <= 16 words; the
# 9:16 headline <= 8 words; the recap prompt <= 12 / 10 words): beyond them the scene is stretched (the page measures).
AADHI_HOOK_WORDS: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 16, "portrait": 8})
AADHI_PROMPT_WORDS: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 12, "portrait": 10})


def _presenter_board(scene: SceneBase) -> tuple[list[BoardItem], list[BoardItem], int] | None:
    """(headings with text, questions with text, other items) of a presenter scene's board - aadhi_intro.js /
    aadhi_recap.js ``introItems`` / ``recapItems``: any item of another kind counts (a figure has no text and is still
    content), a heading or question without text draws nothing - or None for a scene that is not a presenter board
    scene with no side panel."""
    if not (isinstance(scene, BoardScene) and _presenter(scene)) or _panel(scene) is not None:
        return None
    heads: list[BoardItem] = []
    questions: list[BoardItem] = []
    others = 0
    for item in _items(scene):
        kind = str(getattr(item.kind, "value", item.kind))
        has_text = bool((item.text or "").strip())
        if kind not in ("heading", "question"):
            others += 1
        elif has_text:
            (heads if kind == "heading" else questions).append(item)
    return heads, questions, others


def _fit_aadhi_intro(scene: SceneBase, fmt: Format) -> float:
    # the opening BoardScene with look.presenter (Aadhi's words are linted on the beats: R-12, R-13): at most one
    # heading (the title) and one question (the hook) with text, at least one of them, nothing else and no side panel
    # (aadhi_intro.js drawsScene; B2c review CG-2). A board with no heading takes the scene's own title as the title
    # (aadhi_intro.js introItems): a screenplay's presenter scene often carries the session title there with an empty
    # board, and refusing it drew the opening with board_generic and no Aadhi (owner decision 2; step-5 review R5-2)
    board = _presenter_board(scene)
    if board is None or scene.type == "recap":
        return FIT_NONE
    heads, questions, others = board
    titled = bool(heads) or bool((scene.title or "").strip())
    if others or len(heads) > 1 or len(questions) > 1 or not (titled or questions):
        return FIT_NONE
    return _ok(not questions or _words(questions[0].text) <= AADHI_HOOK_WORDS[fmt])


def _fit_aadhi_recap(scene: SceneBase, fmt: Format) -> float:
    # a recap BoardScene with look.presenter: exactly one question with text (the prompt), nothing else, no side panel
    # (aadhi_recap.js drawsScene; CG-2)
    board = _presenter_board(scene)
    if board is None or scene.type != "recap":
        return FIT_NONE
    heads, questions, others = board
    if others or heads or len(questions) != 1:
        return FIT_NONE
    return _ok(_words(questions[0].text) <= AADHI_PROMPT_WORDS[fmt])


def _fit_hook_question(scene: SceneBase, fmt: Format) -> float:
    # one question item (<= 12 words; <= 8 in 9:16), and the opening scene's title heading. The page draws no side
    # panel (hook_question.js drawsScene hands such a scene to board_generic whole), so a scene with one is not this
    # template's (B2c review CG-2: the fit and the page agree, or the director picks a template that then falls back)
    if _panel(scene) is not None:
        return FIT_NONE
    if not _shape(scene, {"question": (1, 1)}):
        return FIT_NONE
    return _ok(_words(_of(scene, "question")[0].text) <= (12 if fmt == "landscape" else 8))


def _fit_key_terms(scene: SceneBase, fmt: Format) -> float:
    # 2-4 definition items: term <= 3 words, meaning <= 8 words. key_terms.js draws the scene's side panel only of a
    # kind _shared.js draws: in 16:9 beside at most KEY_TERMS_PANEL_MAX terms, in 9:16 as its own beat before any
    # number of terms (owner decision 4, 2026-10-16; `drawsScene`); anything else is board_generic's whole, and this
    # fit refuses exactly what the page refuses (B2c review CG-2).
    if not _shape(scene, {"definition": (2, 4)}):
        return FIT_NONE
    if scene.side_panel is not None and (
            not _draws_panel(scene, fmt)
            or (fmt == "landscape" and len(_of(scene, "definition")) > KEY_TERMS_PANEL_MAX)):
        return FIT_NONE
    return _ok(all(_words(d.term) <= 3 and _words(d.text) <= 8 for d in _of(scene, "definition")))


def _fit_chapter_bumper(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, ChapterCardScene) else FIT_NONE


def _fit_recap_retrieval(scene: SceneBase, fmt: Format) -> float:
    # a recap BoardScene: question items (2 in 16:9, 1 in 9:16) and a free-recall prompt, then 3 takeaway tiles.
    # §4.3 gives the recap no picture and recap_retrieval.js draws none (its ``mediaBox`` returns null), so a recap
    # carrying a side panel is board_generic's: without this guard the director picked this template and the panel's
    # medium was never planned, fetched or drawn (B2c review CG-2, B2-demo review D5)
    if _panel(scene) is not None:
        return FIT_NONE
    if not (isinstance(scene, BoardScene) and scene.type == "recap" and _of(scene, "question")):
        return FIT_NONE
    counts = _kinds(scene)
    if not set(counts) <= {"question", "takeaway", "paragraph", "heading"}:
        return FIT_NONE
    return _ok(counts.get("takeaway", 0) <= 3)


def _fit_outro_next(scene: SceneBase, fmt: Format) -> float:
    # closure, no new content (R-11): at most one takeaway, paragraph or heading (OUTRO_KINDS), and something to show -
    # that item (the schema gives each of these kinds text) or the scene's title. outro_next.js draws no side panel and
    # no medium, so a scene with a panel is board_generic's whole: its drawsScene refuses exactly that (B2c review CG-2)
    if not isinstance(scene, BoardScene) or scene.side_panel is not None:
        return FIT_NONE
    items = _items(scene)
    if len(items) > 1 or any(str(getattr(i.kind, "value", i.kind)) not in OUTRO_KINDS for i in items):
        return FIT_NONE
    return FIT_PREFERRED if items or scene.title.strip() else FIT_NONE


def _fit_definition_card(scene: SceneBase, fmt: Format) -> float:
    # a definition item plus an optional bullet example; a side panel only where the page draws one
    # (definition_card.js drawsScene: 16:9 a picture or clip beside the card, 9:16 nothing - the column holds the card
    # alone). Such a scene is board_generic's, and direct.py, which builds on this score, never sends it here (B2c
    # review CG-2). DEFINITION_PANELS is defined with the slot table below.
    panel = _panel(scene)
    if panel is not None and (fmt == "portrait" or panel not in ("figure", "image", "manim", "gif")):
        return FIT_NONE
    return FIT_PREFERRED if _shape(scene, {"definition": (1, 1)}, {"bullet": 1}) else FIT_NONE


# kinetic_statement.js MAX_WORDS: the most words the page sets as one statement. Measured with ``textfit`` on the
# vendored faces: ordinary English statements of 19-22 words fill the 16:9 statement's four lines at its 100 px floor
# (1436 px measure), and of 9-11 words the 9:16 one's four lines at 88 px (696 px column), so the caps sit at or below
# the worst case seen. Between §4.3's limit (12; 8 in 9:16) and the cap a statement is FIT_STRETCHED; past the cap it
# is board_generic's whole, so a long statement never blocks a render with "Shorten text" (the B2 review's F3 lesson,
# MEDIA_SPLIT_PHRASE_WORDS). kinetic_statement.test.js pins the two against each other. A B2 reading, pending the
# owner's review.
KINETIC_STATEMENT_WORDS: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 18, "portrait": 9})


def _fit_kinetic_statement(scene: SceneBase, fmt: Format) -> float:
    # one takeaway, paragraph, heading or callout_* item with text, of <= 12 words (<= 8 in 9:16); a quote as the quote
    # row's alternative. Typography only: kinetic_statement.js drawsScene hands a scene with a side panel, any second
    # item, an item without text or a statement past KINETIC_STATEMENT_WORDS to board_generic whole, and this fit
    # refuses exactly those (B2c review CG-2: the fit and the page agree, or the director picks a template that then
    # falls back)
    if _panel(scene) is not None:
        return FIT_NONE
    items = _items(scene)
    if len(items) != 1 or str(getattr(items[0].kind, "value", items[0].kind)) not in STATEMENT_KINDS | {"quote"}:
        return FIT_NONE
    if not (items[0].text or "").strip():
        return FIT_NONE
    words = _words(items[0].text)
    if words > KINETIC_STATEMENT_WORDS[fmt]:
        return FIT_NONE
    return _ok(words <= (12 if fmt == "landscape" else 8))


# list_build.js PICTURE_PANELS: the side panels list_build draws beside the list, in 16:9 only (a picture or a clip; a
# GIF's title card in an MP4, board_generic's convention). 9:16 draws no panel (the compact stack takes the stage).
LIST_PANELS: Final = ("figure", "image", "manim", "gif")


def _fit_list_build(scene: SceneBase, fmt: Format) -> float:
    # >= 2 bullets; a side panel only when the page draws it (B2c review CG-2: a terminal, chart, graph, model or
    # skill-tree panel beside a list, and any panel in a reel, were dropped; list_build.js drawsScene hands such a scene
    # to board_generic, and direct.py, which builds on this score, never sends it here)
    panel = _panel(scene)
    if panel is not None and (fmt == "portrait" or panel not in LIST_PANELS):
        return FIT_NONE
    return FIT_PREFERRED if _shape(scene, {"bullet": (2, None)}) else FIT_NONE


# media_split.js LAYOUT.portrait.body: the 9:16 phrase is **two** lines of 48-64 px in a 588 px column (570 px when
# the scene pushes). Measured with ``textfit`` on the vendored faces at the slot's minimum: six words of average
# length fit those two lines, seven do not. A reel phrase past this is board_generic's whole - a board_generic board
# sets its items at its own sizes, where they fit. Without the cap the page draws the item anyway, reports the excess
# through ``texts()`` and Dynamic preflight **blocks** the render ("Shorten text", §7.12): the showcase lecture's s3
# (a 9-word bullet) and s13 (an 11-, a 14- and a 22-word item) made a real 9:16 render of it impossible (B2 review
# F3). 16:9 has a 3-line column of its own and is not capped. A B2 reading, pending the owner's review.
MEDIA_SPLIT_PHRASE_WORDS: Final = 6


def _fit_media_split(scene: SceneBase, fmt: Format) -> float:
    # <= 3 text items and a figure, image, chart, graph or terminal panel
    ok = _media_board(scene) and _panel(scene) in ("figure", "image", "chart", "graph", "terminal")
    if ok and fmt == "portrait" and any(
            _words(_phrase_text(i)) > MEDIA_SPLIT_PHRASE_WORDS for i in _items(scene)
            if str(getattr(i.kind, "value", i.kind)) in TEXT_KINDS):
        return FIT_NONE
    return FIT_PREFERRED if ok else FIT_NONE


def _fit_full_bleed_media(scene: SceneBase, fmt: Format) -> float:
    # forced for ai_video; on a board, an application image (an image panel) under a lower-third label (one text item).
    # The page draws a side panel only when the panel *is* the medium (the lower third holds the scene's words and
    # nothing else, full_bleed_media.js `drawsScene`), so an ``ai_video`` - whose medium is its own footage or still -
    # carrying a panel beside it is board_generic's whole, which draws that panel's card. Without this the forced
    # ``ai_video`` row wins and the panel's content disappears from the video (CG-2 parity, never drop content).
    if isinstance(scene, AIVideoScene):
        return FIT_NONE if scene.side_panel is not None else FIT_PREFERRED
    ok = _media_board(scene) and _panel(scene) == "image" and sum(
        n for k, n in _kinds(scene).items() if k in TEXT_KINDS) <= 1 and not _of(scene, "figure")
    return FIT_PREFERRED if ok else FIT_NONE


def _fit_spotlight_image(scene: SceneBase, fmt: Format) -> float:
    # a figure item or an image / figure panel (pins, placed in Visual Review, are optional)
    ok = _media_board(scene) and (_panel(scene) in ("figure", "image") or len(_of(scene, "figure")) == 1)
    return FIT_PREFERRED if ok else FIT_NONE


def _fit_versus(scene: SceneBase, fmt: Format) -> float:
    # a table with exactly 2 value columns (+ an attribute column when the intent is compare), <= 4 rows; or
    # visual_intent "compare" with two definition items. Never pair items (correspondence).
    table = _table(scene)
    if table is not None:
        cols = _columns(table)
        if (cols == 2 or (cols == 3 and _intent(scene) == "compare")) and len(table.rows or []) <= 4:
            return FIT_PREFERRED
        return FIT_NONE
    return FIT_PREFERRED if _intent(scene) == "compare" and _shape(scene, {"definition": (2, 2)}) else FIT_NONE


def _fit_table_reveal(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _table(scene) is not None else FIT_NONE


def _fit_analogy_bridge(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _shape(scene, {"pair": (2, None)}) else FIT_NONE


def _steps(scene: SceneBase, sequences: Sequence[str], *, dated: bool = False) -> float:
    # 3-6 step items (process_flow; the other sequence templates "as process_flow")
    if _sequence(scene) not in sequences or not _shape(scene, {"step": (3, 6)}):
        return FIT_NONE
    if dated and not all((s.date or "").strip() for s in _of(scene, "step")):
        return FIT_NONE  # §4.4: "a flow without dates never becomes a timeline"
    return FIT_PREFERRED


def _fit_process_flow(scene: SceneBase, fmt: Format) -> float:
    return _steps(scene, ("flow", "timeline"))


def _fit_cycle_loop(scene: SceneBase, fmt: Format) -> float:
    return _steps(scene, ("cycle",))


def _fit_timeline_track(scene: SceneBase, fmt: Format) -> float:
    return _steps(scene, ("timeline", "flow"), dated=True)


def _fit_cause_chain(scene: SceneBase, fmt: Format) -> float:
    return _steps(scene, ("chain",))


def _fit_concept_tree(scene: SceneBase, fmt: Format) -> float:
    ok = _panel(scene) == "skill_tree" and isinstance(scene, BoardScene) and all(
        k in TEXT_KINDS for k in _kinds(scene)) and len(_items(scene)) <= 3
    return FIT_PREFERRED if ok else FIT_NONE


def _fit_formula_focus(scene: SceneBase, fmt: Format) -> float:
    # a formula item (its variables: the legend rows and the var cues); 9:16 width is measured (portrait.formula_wide).
    # The page draws no side panel (formula_focus.js fitsScene hands such a scene to board_generic whole), so a scene
    # with one is not this template's
    if _panel(scene) is not None:
        return FIT_NONE
    return FIT_PREFERRED if _shape(scene, {"formula": (1, 1)}) else FIT_NONE


def _fit_worked_steps(scene: SceneBase, fmt: Format) -> float:
    # example_step items (blank steps plus fill beats) and an optional formula, plus the one top line the page draws
    # (worked_steps.js: heading | callout_info | paragraph - the generator writes a worked example's setup as a
    # ``callout_info``, so without this the demo's worked examples would all fall back to board_generic). At most one
    # of the three: two such items send the scene to board_generic (B2 reading, pending the owner's review).
    # ``_worked_shape`` is the §4.4 ``example`` row's own question, so the row and this fit never drift apart (D1).
    # The page draws the scene's side panel only of a kind _shared.js draws: in 16:9 beside the steps, never beside a
    # formula card (the right 45% holds one visual); in 9:16 as its own beat before the steps, with or without a
    # formula (owner decision 4, 2026-10-16; worked_steps.js `drawsScene`); anything else is board_generic's whole
    # (CG-2).
    if scene.side_panel is not None and (
            not _draws_panel(scene, fmt) or (fmt == "landscape" and _kinds(scene).get("formula", 0) > 0)):
        return FIT_NONE
    if not _worked_shape(scene):
        return FIT_NONE
    # 9:16: only text the reel's step column sets whole (REEL_WRAP; else board_generic, never a blocked render)
    return FIT_PREFERRED if fmt != "portrait" or _reel_steps_fit(scene) else FIT_NONE


# code_walkthrough.js LAYOUT / codeBox / codeLines / codeFits (deviation 13: R-29's floor wins over §4.3's 34 px): the
# code is set in whole-pixel line boxes, one pitch per line (the Indic one when the program holds Indic text, plan §2),
# and the program must fit the editor whole at the slot's minimum size, or the page hands the scene to board_generic -
# so the fit refuses exactly those programs (the CG-2 rule; tests/compose/dynamic/test_code_walkthrough_fixtures.py runs
# the page's functions under node against these). Per format: the px the line boxes may take without and below a
# heading, a line's box at the minimum size (Latin, Indic), and the characters per line (alone, beside a terminal
# panel; 9:16 draws no panel). 16:9: 17 / 13 Latin lines (14 / 11 Indic) of 60 characters (38 beside a terminal);
# 9:16: 10 / 8 (8 / 6) of 28.
CODE_TAB: Final = 4
CODE_INDIC: Final = re.compile("[\u0900-\u0DFF]")  # Devanagari to Sinhala (code_walkthrough.js)


@dataclass(frozen=True)
class CodeBox:
    """code_walkthrough.js ``codeBox(format)``: the editor's line-box budget and line width in one format."""

    lines_px: int
    headed_px: int
    pitch: int
    pitch_indic: int
    chars: int
    chars_panel: int | None


CODE_BOX: Final[Mapping[Format, CodeBox]] = MappingProxyType({
    "landscape": CodeBox(lines_px=740, headed_px=596, pitch=43, pitch_indic=52, chars=60, chars_panel=38),
    "portrait": CodeBox(lines_px=484, headed_px=388, pitch=48, pitch_indic=58, chars=28, chars_panel=None),
})


def code_lines(code: str) -> list[str]:
    """A program's lines as the page draws them (code_walkthrough.js ``codeLines``): ``str.splitlines``, tabs expanded
    to :data:`CODE_TAB` columns."""
    return [line.expandtabs(CODE_TAB) for line in code.splitlines()]


def code_fits(code: str, fmt: Format, *, headed: bool, panel: bool) -> bool:
    """Whether a program fits the editor whole at the minimum size (code_walkthrough.js ``codeFits``): at least one
    line, every line within the format's characters (beside a terminal panel: ``chars_panel``), and its line boxes
    within the px the format allows with (``headed``) or without a heading."""
    box = CODE_BOX[fmt]
    lines = code_lines(code)
    chars = box.chars_panel if panel else box.chars
    if not lines or chars is None:
        return False
    pitch = box.pitch_indic if CODE_INDIC.search(code) else box.pitch
    if len(lines) * pitch > (box.headed_px if headed else box.lines_px):
        return False
    return all(len(line) <= chars for line in lines)


def _fit_code_walkthrough(scene: SceneBase, fmt: Format) -> float:
    # one code item and at most one heading; a side panel only in 16:9 and only a terminal (code_walkthrough.js
    # `drawsScene`: the terminal slides up beside the editor; 9:16 draws no panel); and a program that fits the editor
    # whole (`code_fits`). Anything else is board_generic's whole: never FIT_STRETCHED, since the page refuses what it
    # cannot lay out whole (CG-2) - a longer or wider program is board_generic's, and lint portrait.code_too_wide says why.
    if not _shape(scene, {"code": (1, 1)}):
        return FIT_NONE
    panel = scene.side_panel
    if panel is not None and (fmt != "landscape" or str(getattr(panel.kind, "value", panel.kind)) != "terminal"
                              or panel.terminal is None):
        return FIT_NONE
    fits = code_fits(_of(scene, "code")[0].code or "", fmt, headed=bool(_of(scene, "heading")), panel=panel is not None)
    return FIT_PREFERRED if fits else FIT_NONE


def _panel_only(scene: SceneBase, kind: str) -> float:
    # the panel is the content: at most a heading beside it
    return FIT_PREFERRED if _panel(scene) == kind and _shape(scene, {}) else FIT_NONE


def _fit_chart_build(scene: SceneBase, fmt: Format) -> float:
    # the panel is the content (at most a heading beside it), drawn as a real plot: a chart, or a graph while
    # function_plot is unbuilt (the graph row names chart_build as its alternative). chart_build.js drawsScene draws
    # exactly the panels charts.chart_problem accepts (_charts.js chartPlan's refusals, B2c review CG-2)
    if _panel(scene) not in ("chart", "graph") or not _shape(scene, {}) or scene.side_panel is None:
        return FIT_NONE
    return FIT_PREFERRED if chart_problem(scene.side_panel, fmt) is None else FIT_NONE


def _fit_function_plot(scene: SceneBase, fmt: Format) -> float:
    return _panel_only(scene, "graph")


def _fit_model_spin(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _media_board(scene) and _panel(scene) == "model_3d" else FIT_NONE


def _fit_animation_stage(scene: SceneBase, fmt: Format) -> float:
    # forced for simulation scenes; a manim panel beside <= 3 text items on a board.
    # animation_stage.js draws the animation, the title and the step labels and nothing else (``drawsScene``), so a
    # board carrying a ``figure`` item - whose own medium it does not lay out - is spotlight_image's / board_generic's
    # whole (the CG-2 parity rule: the Python fit must refuse what the page refuses).
    # The page draws a side panel only when the panel *is* the animation (a ``manim`` panel), so a ``simulation`` -
    # whose medium is its own Manim render - carrying a panel beside it is board_generic's whole, which draws that
    # panel's card. Without this the forced ``simulation`` row wins and the panel's content disappears.
    if isinstance(scene, SimulationScene):
        return FIT_NONE if scene.side_panel is not None else FIT_PREFERRED
    if _of(scene, "figure"):
        return FIT_NONE
    return FIT_PREFERRED if _media_board(scene) and _panel(scene) == "manim" else FIT_NONE


def _fit_interactive_poster(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, InteractiveScene) else FIT_NONE


def _fit_clip_scene(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, ClipScene) else FIT_NONE


# web/js/dynamic/templates/myth_fact.js SUPPORT_MAX: how many definitions a misconception scene may carry as supporting
# lines (16:9 two: the showcase lecture's s5; 9:16 none: the reel's column holds the two cards alone).
MYTH_FACT_SUPPORT_MAX: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 2, "portrait": 0})
# The text the page has room for, in words as _words counts them (a $TeX$ span one word), each word weighted by how
# wide it draws: measured with textfit on the vendored faces at the slots' minimum sizes, a Tamil word draws about
# twice an English word's width, a Malayalam word about 3.5 times, a Telugu or Kannada word about 1.6 times, a
# Devanagari word (and the other Indic scripts' words) no wider; a word in capitals ("SAME", "ENERGY") 1.3 times. The
# caps are the most English words of this repo's screenplays that kept their line budget in every one of 300
# samples (textfit at each slot's minimum size and width, myth_fact.js LAYOUT): 16:9 a 1-line heading 5, a 3-line
# supporting line 18, a 4-line belief 15, a 4-line correction 20; 9:16 a 3-line belief 8, a 4-line correction 10, a
# 3-line one at the Indic line heights 7. With those budgets the composition fits its band at the minimum sizes in
# every case (myth_fact.js LAYOUT's comment). A heading takes the room of a supporting line (one beside a heading).
# Past a cap the scene is board_generic's whole: a page that reports the excess makes Dynamic preflight block the
# render (B2 review F3, MEDIA_SPLIT_PHRASE_WORDS' reading). B2 proposals, pending the owner's review.
MYTH_FACT_WORDS: Final[Mapping[Format, Mapping[str, int]]] = MappingProxyType({
    "landscape": MappingProxyType({"heading": 5, "support": 18, "myth": 15, "fact": 20}),
    "portrait": MappingProxyType({"myth": 8, "fact": 10, "fact_indic": 7}),
})
# (Malayalam, Tamil, then Telugu and Kannada; a word with none of them is English-wide, or 1.3 in capitals)
_MYTH_FACT_SCRIPT_WEIGHTS: Final = ((re.compile("[\u0D00-\u0D7F]"), 3.5), (re.compile("[\u0B80-\u0BFF]"), 2.0),
                                    (re.compile("[\u0C00-\u0CFF]"), 1.6))
_MYTH_FACT_INDIC: Final = re.compile("[\u0900-\u0DFF]")  # Devanagari to Sinhala (myth_fact.js INDIC): taller lines


def _myth_fact_words(text: str | None) -> float:
    """``_words`` with each word weighted by how wide it draws (see ``MYTH_FACT_WORDS``)."""
    total = 0.0
    for word in _TEX.sub(" x ", text or "").split():
        weight = next((k for rx, k in _MYTH_FACT_SCRIPT_WEIGHTS if rx.search(word)), None)
        if weight is None:
            weight = 1.3 if len(re.findall("[A-Z]", word)) >= 2 and not re.search("[a-z]", word) else 1.0
        total += weight
    return total


def _myth_fact_shape(scene: SceneBase, fmt: Format = "landscape") -> bool:
    """The board ``myth_fact.js`` draws in ``fmt``: one misconception, at most ``MYTH_FACT_SUPPORT_MAX`` definitions
    and (16:9) at most one heading. The §4.4 ``misconception`` row asks it for 16:9, the widest, so the row is never
    narrower than the fit (B2-demo review D1: a shape only one of them takes scores 0)."""
    extra: Mapping[str, int] = MappingProxyType({"heading": 1} if fmt == "landscape" else {})
    return _shape(scene, {"misconception": (1, 1), "definition": (0, MYTH_FACT_SUPPORT_MAX[fmt])}, extra)


def _fit_myth_fact(scene: SceneBase, fmt: Format) -> float:
    # a misconception item with its correction (justification): the fact card is the final frame (R-63). Owner
    # decision 3 (2026-10-16): beside it, in 16:9, the scene's definitions are drawn as supporting lines (the showcase's
    # s5), with one heading; key_terms stays pure (its fit refuses a misconception). myth_fact.js drawsScene refuses
    # every other shape, a 9:16 heading, definition or panel, a 16:9 panel _shared.js does not draw and a scene medium
    # (a board scene has none); this fit refuses the same (B2c review CG-2) and, past them, text over the measured
    # caps and a heading beside two supporting lines (MYTH_FACT_WORDS), so the director never sends a scene whose page
    # would report overflow and block preflight
    if not _myth_fact_shape(scene, fmt):
        return FIT_NONE
    myth = _of(scene, "misconception")[0]
    if not (myth.text.strip() and (myth.justification or "").strip()):
        return FIT_NONE
    if scene.side_panel is not None and (fmt != "landscape" or not _draws_panel(scene, fmt)):
        return FIT_NONE
    caps = MYTH_FACT_WORDS[fmt]
    definitions = _of(scene, "definition")
    headings = _of(scene, "heading")
    if headings and (len(definitions) > 1 or _myth_fact_words(headings[0].text) > caps["heading"]):
        return FIT_NONE
    if any(_myth_fact_words(d.term) + _myth_fact_words(d.text) > caps["support"] for d in definitions):
        return FIT_NONE
    indic = fmt == "portrait" and bool(_MYTH_FACT_INDIC.search(f"{myth.text} {myth.justification}"))
    if (_myth_fact_words(myth.text) > caps["myth"]
            or _myth_fact_words(myth.justification) > caps["fact_indic" if indic else "fact"]):
        return FIT_NONE
    return FIT_PREFERRED


def _blank_question(scene: SceneBase) -> bool:
    return _shape(scene, {"question": (1, 1)}) and _of(scene, "question")[0].blank


def _fit_predict(scene: SceneBase, fmt: Format) -> float:
    # predict.js draws no medium (drawsScene hands such a scene to board_generic whole), so a scene with a side panel
    # is not this template's (B2c review CG-2, B2-demo review D5)
    if _panel(scene) is not None:
        return FIT_NONE
    return FIT_PREFERRED if _blank_question(scene) else FIT_NONE


# check: the most options a format DRAWS - mcq <= 4 in 16:9 (more: board_generic); 9:16 two, a reel quiz of more
# reduced to the correct answer and the strongest distractor (owner decision 4).
CHECK_MAX_OPTIONS: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 4, "portrait": 2})


def _fit_check(scene: SceneBase, fmt: Format) -> float:
    # §4.3 gives a check no picture and check.js draws none (its ``mediaBox`` returns null), so a scene carrying a
    # side panel is board_generic's whole (B2-demo review D5). §4.3 gives `mcq` to 16:9 only and 9:16 two options
    # (CHECK_MAX_OPTIONS; R-06: a reel's check is free recall or true / false), and check.js hands a quiz past that
    # cap to board_generic - whose stack compacts and collapses the cards, so nothing is dropped - which the fit must
    # refuse too, forced or not (B2c review CG-2): a stretched score would have the director plan `check` for a scene
    # the page then draws with board_generic, and B2e would store `text_fit` for slots the page never lays out.
    if _panel(scene) is not None:
        return FIT_NONE
    if isinstance(scene, QuizScene):
        # 16:9: mcq <= CHECK_MAX_OPTIONS (more is board_generic's whole scene); 9:16: any quiz - check.js draws a reel quiz
        # of more than two options as two (the answer and the strongest distractor, owner decision 4, reels.py)
        if fmt == "portrait":
            return FIT_PREFERRED if _reel_check_fit(scene) else FIT_NONE
        return FIT_PREFERRED if len(scene.options) <= CHECK_MAX_OPTIONS[fmt] else FIT_NONE
    if not _blank_question(scene):
        return FIT_NONE
    return FIT_PREFERRED if fmt != "portrait" or _reel_check_fit(scene) else FIT_NONE


# --- the reel's text budget (step 5 integration): what a 9:16 check / worked_steps page holds -----------------------
# Owner decision 4 brought 9:16 quizzes of more than two options and 9:16 panelled worked examples to these two
# templates; the showcase lecture's s9 / s11 / s12 then blocked 9:16 preflight with text_overflow (a 23-word question,
# a 15-word explanation, a 17-word heading and a 14-word step), which board_generic had held before. So the fits take a
# 9:16 scene only when its text fits the page's slots at their minimum sizes (the F3 lesson, MEDIA_SPLIT_PHRASE_WORDS):
# a longer one is board_generic's whole. The build may not load the shaping libraries (test_dynamic_isolation), so the
# lines are estimated by a greedy word wrap at REEL_WRAP characters per line - measured with textfit against the
# slots: on 1,900 sample texts per slot from this repo's screenplays, the estimate never gave fewer lines than the
# certified fit. A word of an Indic script counts wider (myth_fact's measured weights: Tamil x2, Malayalam x3.5, Telugu /
# Kannada x1.6); a $TeX$ span counts the characters it draws (commands and braces dropped). B2 reading, pending the
# owner's review; B2e's measured fit supersedes it.
REEL_WRAP: Final[Mapping[str, int]] = MappingProxyType({
    "ws_heading": 24, "ws_step": 15, "ws_row": 22, "ws_why": 20,      # worked_steps: 56 / 56 / 40 / 44 px
    "ck_question": 23, "ck_explain": 26, "ck_option": 19,             # check: 48 / 40 / 40 px
})
_REEL_TEX_COMMAND = re.compile(r"\\[A-Za-z]+|[{}]|\\(?=\s)")  # \cmd, a brace, the backslash of "\ "


def _reel_text(text: str | None) -> list[float]:
    """The word widths (characters, Indic words weighted) a reel slot sets: [[term]] marks and rich-lite stars dropped,
    each $TeX$ span reduced to the characters it draws."""
    plain = _TEX.sub(lambda m: _REEL_TEX_COMMAND.sub("", m.group(0)[1:-1]), (text or "").replace("[[", "").replace("]]", ""))
    plain = plain.replace("**", "").replace("__", "")
    out: list[float] = []
    for word in plain.split():
        weight = next((k for rx, k in _MYTH_FACT_SCRIPT_WEIGHTS if rx.search(word)), 1.0)
        out.append(len(word) * weight)
    return out


def _reel_lines(text: str | None, capacity: int) -> int:
    """Lines of ``text`` in a greedy word wrap of ``capacity`` characters (a longer word breaks over several)."""
    lines, used = 0, 0.0
    for w in _reel_text(text):
        if used and used + 1 + w <= capacity:
            used += 1 + w
            continue
        whole = max(1, math.ceil(w / capacity))
        lines += whole
        used = w - (whole - 1) * capacity
    return max(lines, 1)


# worked_steps.js LAYOUT.portrait and boxes(): the heading at the title box's top (y 340), 2 lines at most, 28 px above
# the steps; the formula card (padY 24, the formula at <= 88 px, its meaning in <= 2 lines of 44 px, 12 px between,
# 24 px below it); the steps down to the text column's foot (y 1020): each step's card PAD 12 px above and below at
# 56 px (its justification 4 px under it, <= 2 lines of 44 px), the steps already through it compact (4 px, 40 px),
# 12 px apart.
REEL_STEPS: Final = MappingProxyType({"top": 340, "bottom": 1020, "heading_lh": 56 * 1.15, "heading_gap": 28,
                                      "formula_card": 24 + 88 + 12 + 2 * 44 * 1.35 + 24 + 24, "pad": 12,
                                      "step_lh": 56 * 1.35, "why_gap": 4, "why_lh": 44 * 1.35, "pad_compact": 4,
                                      "row_lh": 40 * 1.35, "gap": 12})


def _reel_steps_fit(scene: SceneBase) -> bool:
    """Whether a 9:16 ``worked_steps`` page sets the scene's text whole (see ``REEL_WRAP``): the top line in two lines,
    each justification in two, and the step stack below the top line and the formula card, every step large (with its
    justification) under the compact ones before it."""
    L = REEL_STEPS
    top = float(L["top"])
    heads = [i for i in _items(scene) if str(getattr(i.kind, "value", i.kind)) in WORKED_TOP]
    if heads:
        n = _reel_lines(heads[0].text, REEL_WRAP["ws_heading"])
        if n > 2:
            return False
        top += n * L["heading_lh"] + L["heading_gap"]
    if _of(scene, "formula"):
        top += L["formula_card"]
    avail = L["bottom"] - top
    above = 0.0
    for step in _of(scene, "example_step"):
        why = (step.justification or "").strip()
        why_lines = _reel_lines(why, REEL_WRAP["ws_why"]) if why else 0
        if why_lines > 2:
            return False
        large = 2 * L["pad"] + _reel_lines(step.text, REEL_WRAP["ws_step"]) * L["step_lh"] + (
            L["why_gap"] + why_lines * L["why_lh"] if why_lines else 0)
        if above + large > avail:
            return False
        above += 2 * L["pad_compact"] + _reel_lines(step.text, REEL_WRAP["ws_row"]) * L["row_lh"] + L["gap"]
    return True


def _reel_check_fit(scene: SceneBase) -> bool:
    """Whether a 9:16 ``check`` page sets the scene's text whole (see ``REEL_WRAP``; check.js slots: the question in
    four lines, the explanation in two, each drawn option in two - a quiz of more than two options draws the answer and
    its strongest distractor, ``reels.reel_options``)."""
    if isinstance(scene, QuizScene):
        from .reels import reel_options  # lazy: reels reads this module lazily too

        shown = reel_options(scene.options, scene.correct_index, scene.feedback_wrong, scene.option_misconception_ids)
        return (_reel_lines(scene.question, REEL_WRAP["ck_question"]) <= 4
                and _reel_lines(scene.explanation, REEL_WRAP["ck_explain"]) <= 2
                and all(_reel_lines(scene.options[i], REEL_WRAP["ck_option"]) <= 2 for i in shown))
    question = _of(scene, "question")[0]
    why = (question.justification or "").strip()
    return (_reel_lines(question.text, REEL_WRAP["ck_question"]) <= 4
            and (not why or _reel_lines(why, REEL_WRAP["ck_explain"]) <= 2))


def _fit_takeaway_stamp(scene: SceneBase, fmt: Format) -> float:
    if not _shape(scene, {"takeaway": (1, 1)}, {}):
        return FIT_NONE
    return _ok(_words(_of(scene, "takeaway")[0].text) <= 15)


def _fit_quote_card(scene: SceneBase, fmt: Format) -> float:
    if not _shape(scene, {"quote": (1, 1)}, {}):
        return FIT_NONE
    return _ok(_words(_of(scene, "quote")[0].text) <= 25)


def _fit_checklist(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _sequence(scene) == "checklist" and _shape(scene, {"step": (2, None)}) else FIT_NONE


def _fit_stat_punch(scene: SceneBase, fmt: Format) -> float:
    # a stat item: value, unit, context <= 10 words (source refs are the lint's, stat.ungrounded)
    if not _shape(scene, {"stat": (1, 1)}):
        return FIT_NONE
    return _ok(_words(_of(scene, "stat")[0].text) <= 10)


def _fit_board_generic(scene: SceneBase, fmt: Format) -> float:
    # every board scene; B1 also draws chapter cards, quizzes and simulation / AI video / interactive scenes with it
    return FIT_NONE if isinstance(scene, ClipScene) else FIT_PREFERRED


# --- board_generic's slots (web/js/dynamic/templates/board_generic.js, B1) -------------------------------------------
# LAYOUT[format].{kicker, title, titleQuiz, titleCard, body}, MIN_FIT, BODY_WEIGHT, RING_SIZE / RING_GAP and the
# centred title box, value for value, with dynamic.css's line heights and letter-spacing (.dyn-header 1.2 / 0.02em,
# .dyn-title -0.015em, .dyn-card and .dyn-subtitle 1.3, .dyn-chip 1.1); tests/compose/dynamic/test_templates.py pins
# them, and from step B a JS test pins board_generic.js against the catalogue. MIN_TEXT / MIN_LABEL are the R-29
# floors (zones.TYPE_FLOORS). The slot variants are board_generic's own layouts: "default" (LAYOUT.title, the stack),
# "quiz" (titleQuiz), "card" (the centred title layout of chapter cards: titleCard), "split" (cards beside a picture).

@dataclass(frozen=True)
class BoardTitle:
    """One of board_generic's title specs (``LAYOUT[format].title`` / ``titleQuiz`` / ``titleCard``): the largest size
    whose line count fits, in 4 px steps from ``max`` down to ``min``; ``lh`` the line height; ``prefer`` fewer lines
    tried first."""

    max: int
    min: int
    lines: int
    lh: float
    prefer: int | None = None


@dataclass(frozen=True)
class BoardLayout:
    """board_generic.js ``LAYOUT[format]``'s text sizes: ``kicker`` px, the three title specs, ``body`` px (card text
    before fitting) and ``split`` (the card side of a split layout)."""

    kicker: int
    title: BoardTitle
    title_quiz: BoardTitle
    title_card: BoardTitle
    body: int
    split: float


BOARD_LAYOUT: Final[Mapping[Format, BoardLayout]] = MappingProxyType({
    "landscape": BoardLayout(kicker=40, title=BoardTitle(96, 72, 2, 1.06), title_quiz=BoardTitle(76, 56, 3, 1.12),
                             title_card=BoardTitle(148, 96, 3, 1.02), body=52, split=0.55),
    # R-25: a 1-line headline when it fits at >= 56 px
    "portrait": BoardLayout(kicker=44, title=BoardTitle(88, 56, 2, 1.06, prefer=1),
                            title_quiz=BoardTitle(68, 50, 4, 1.12), title_card=BoardTitle(112, 72, 4, 1.04), body=56,
                            split=0.55),
})
BOARD_MIN_FIT: Final[Mapping[Format, float]] = MappingProxyType({"landscape": 0.75, "portrait": 0.72})
BOARD_BODY_WEIGHT: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 400, "portrait": 600})
BOARD_RING: Final = (96, 24)  # RING_SIZE, RING_GAP: the 16:9 quiz title block keeps clear of the countdown ring
BOARD_CENTRED_TITLE: Final[Mapping[Format, zn.Box]] = MappingProxyType(
    {"landscape": zn.Box(192, 54, 1728, 912), "portrait": zn.Box(192, 340, 888, 840)})  # centeredTitleBox
BOARD_SUBTITLE: Final[Mapping[Format, tuple[int, int, int]]] = MappingProxyType(
    {"landscape": (52, 400, 1400), "portrait": (48, 600, 1400)})  # dynamic.css .dyn-subtitle: size, weight, max-width


def _board_slots() -> SlotTable:
    out: dict[str, dict[Format, dict[str, SlotSpec]]] = {}
    for fmt in FORMATS:
        layout = BOARD_LAYOUT[fmt]
        z = zn.ZONES[fmt]
        floors = zn.TYPE_FLOORS[fmt]
        ring_room = sum(BOARD_RING) if fmt == "landscape" else 0  # 9:16 puts the ring beside the options
        widths = {"default": z.title.w, "quiz": z.title.w - ring_room, "card": BOARD_CENTRED_TITLE[fmt].w}
        out.setdefault("kicker", {})[fmt] = {
            v: SlotSpec(w=w, max_lines=1, size=(layout.kicker, layout.kicker), role="label", face="body", weight=600,
                        line_height=1.2, tracking_em=0.02, critical=v == "card")  # a chapter card's label: its message
            for v, w in widths.items()}
        titles = {"default": layout.title, "quiz": layout.title_quiz, "card": layout.title_card}
        out.setdefault("title", {})[fmt] = {
            v: SlotSpec(w=widths[v], max_lines=t.lines, size=(t.min, t.max), role="title", face="display", weight=700,
                        line_height=t.lh, tracking_em=-0.015, prefer_lines=t.prefer)
            for v, t in titles.items()}
        size, weight, max_w = BOARD_SUBTITLE[fmt]
        out.setdefault("subtitle", {})[fmt] = {"card": SlotSpec(
            w=min(BOARD_CENTRED_TITLE[fmt].w, max_w), max_lines=None, size=(size, size), role="subtitle", face="body",
            weight=weight, line_height=1.3)}  # reported in slot "title" (TITLE_BLOCK_SLOTS): its scrim is the title's
        body = layout.body
        body_min = max(floors.text_min, body * BOARD_MIN_FIT[fmt])
        if fmt == "landscape":
            body_w = {"default": z.body.w, "split": round(z.body.x0 + z.body.w * layout.split) - z.body.x0}
        else:  # the card column x 168-804 (the text column and 24 px of card padding each side) in every layout
            col = z.text_column.w + 48
            body_w = {"default": col, "split": col}
        out.setdefault("body", {})[fmt] = {
            v: SlotSpec(w=w, max_lines=None, size=(body_min, body), role="body", face="body",
                        weight=BOARD_BODY_WEIGHT[fmt], line_height=1.3) for v, w in body_w.items()}
        label = floors.label_min
        out.setdefault("chip", {})[fmt] = {"default": SlotSpec(
            w=None, max_lines=1, size=(label, label), role="chip", face="body", weight=600, line_height=1.1,
            critical=False)}
        out.setdefault("ring", {})[fmt] = {"default": SlotSpec(
            w=BOARD_RING[0], max_lines=1, size=(label, label), role="label", face="body", weight=700, line_height=1.3,
            critical=False, tabular_nums=True)}  # the countdown's number (R-07: the think time is the ring itself;
        # dynamic.css .dyn-ring-num: tabular numerals)
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})

# --- formula_focus's slots (B2c): web/js/dynamic/templates/formula_focus.js LAYOUT, LINE_HEIGHT, FORMULA_SLOT and
# slotWidths (web/tests/dynamic/templates/formula_focus.test.js pins the two against each other) ----------------------


@dataclass(frozen=True)
class FormulaFocusLayout:
    """formula_focus.js ``LAYOUT[format]``'s text slots, (min, max) px each: ``title`` (lines, prefer_lines, weight
    700), ``formula`` (the TeX size), ``text`` (the formula's sentence: lines, weight) and ``legend`` (a legend row:
    weight); and the widths the page lays each out in at most (``slotWidths``): 16:9 the critical width (1728) for
    the headline, the card's inner width (1728 - 2 x 56) for the formula and the sentence, a column of the two-column
    legend ((1728 - 48) / 2); 9:16 the headline region (696), the 768 px card's inner width (696) for the formula, the
    narrow card's (660 - 2 x 36 = 588, the text column below the rail: the conservative width) for the sentence and
    the text column (588) for a legend row."""

    title: tuple[int, int]
    title_lines: int
    title_prefer: int | None
    formula: tuple[int, int]
    text: tuple[int, int]
    text_lines: int
    text_weight: int
    legend: tuple[int, int]
    legend_weight: int
    widths: tuple[int, int, int, int]  # title, formula, text, legend


FORMULA_FOCUS_LAYOUT: Final[Mapping[Format, FormulaFocusLayout]] = MappingProxyType({
    "landscape": FormulaFocusLayout(title=(72, 96), title_lines=2, title_prefer=None, formula=(64, 104), text=(48, 52),
                                    text_lines=2, text_weight=400, legend=(40, 48), legend_weight=400,
                                    widths=(1728, 1616, 1616, 840)),
    # R-25: a 1-line headline when it fits at >= 56 px; R-29: weight >= 600 at <= 64 px (the TeX formula, whose
    # glyphs have one weight, stays above 64 px)
    "portrait": FormulaFocusLayout(title=(56, 88), title_lines=2, title_prefer=1, formula=(68, 96), text=(56, 60),
                                   text_lines=3, text_weight=600, legend=(44, 52), legend_weight=600,
                                   widths=(696, 696, 588, 588)),
})
# formula_focus.js LINE_HEIGHT (Latin: the page sets Indic text at the type scale's Indic floors, 1.25 / 1.45) and
# TITLE_TRACKING (dynamic.css .dyn-title)
FORMULA_FOCUS_LINE_HEIGHT: Final = (1.05, 1.35)  # display, body


def _formula_focus_slots() -> SlotTable:
    """formula_focus's slots, variant ``default`` (it has no look variants; 9:16's narrow card is a layout decision
    of the page, never stored). The formula slot is a TeX picture: textfit does not measure it (``max_lines`` None),
    it is reported as body text at its TeX size."""
    display, body = FORMULA_FOCUS_LINE_HEIGHT
    out: dict[str, dict[Format, dict[str, SlotSpec]]] = {}
    for fmt in FORMATS:
        lay = FORMULA_FOCUS_LAYOUT[fmt]
        w_title, w_formula, w_text, w_legend = lay.widths
        specs = {
            "title": SlotSpec(w=w_title, max_lines=lay.title_lines, size=lay.title, role="title", face="display",
                              weight=700, line_height=display, tracking_em=-0.015, prefer_lines=lay.title_prefer),
            "formula": SlotSpec(w=w_formula, max_lines=None, size=lay.formula, role="body", face="body", weight=400,
                                line_height=1.0),
            "text": SlotSpec(w=w_text, max_lines=lay.text_lines, size=lay.text, role="body", face="body",
                             weight=lay.text_weight, line_height=body),
            "legend": SlotSpec(w=w_legend, max_lines=None, size=lay.legend, role="label", face="body",
                               weight=lay.legend_weight, line_height=body),
        }
        for slot, spec in specs.items():
            out.setdefault(slot, {})[fmt] = {"default": spec}
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- media_split's slots (web/js/dynamic/templates/media_split.js LAYOUT, B2c; B2c proposals, pending the owner's
# review). 16:9: the 45% text region (right-45 or its mirror, 766 px) less the 24 px gutter = 742 px; the body text
# starts after the 36 px marker column; the terminal card is the 938 px medium region less 2 x 32 px padding. 9:16: the
# headline region (696 px), the phrase column from the headline's left edge to the TikTok rail (x 192-780, 588 px; one
# left edge for the text, B2c review V7), the stage (960 px) less 2 x 28 px padding. Heading / takeaway items are drawn
# at 700 in 16:9 (the slot's weight is the 400 of the others). Line heights: the type scale's Latin values (display
# 1.05, body 1.35, media_split.js LINE_HEIGHT); the page sets Indic text at the Indic floors (1.25 / 1.45, B2c review
# V5), as formula_focus and list_build do.
def _media_split_slots() -> SlotTable:
    variants = ("media_left", "media_right")
    title_l = SlotSpec(w=742, max_lines=3, size=(64, 88), role="title", face="display", weight=700, line_height=1.05,
                       tracking_em=-0.015)
    body_l = SlotSpec(w=742 - 36, max_lines=None, size=(44, 52), role="body", face="body", weight=400, line_height=1.35)
    term_l = SlotSpec(w=938 - 2 * 32, max_lines=None, size=(36, 44), role="body", face="mono", weight=400,
                      line_height=1.35)
    title_p = SlotSpec(w=696, max_lines=2, size=(56, 88), role="title", face="display", weight=700, line_height=1.05,
                       tracking_em=-0.015, prefer_lines=1)
    body_p = SlotSpec(w=780 - 192, max_lines=2, size=(48, 64), role="body", face="body", weight=700, line_height=1.35)
    term_p = SlotSpec(w=960 - 2 * 28, max_lines=None, size=(40, 44), role="body", face="mono", weight=700,
                      line_height=1.35)
    out = {slot: MappingProxyType({"landscape": MappingProxyType({v: land for v in variants}),
                                   "portrait": MappingProxyType({"default": port})})
           for slot, land, port in (("title", title_l, title_p), ("body", body_l, body_p),
                                    ("terminal", term_l, term_p))}
    return MappingProxyType(out)


# --- full_bleed_media's slots: web/js/dynamic/templates/full_bleed_media.js LAYOUT, labelBox() and slotRows();
# full_bleed_media.test.js pins the catalogue's rows to slotRows. The lower third is the whole critical width in 16:9
# and, in 9:16, the part both critical bands hold (the headline's left edge to the rail), so one width per format and
# one "default" variant (§4.3 gives this template no variant).
def _full_bleed_media_slots() -> SlotTable:
    land_w = zn.REGIONS["landscape"]["full"].w                                                   # 1728
    port_w = zn.REGIONS["portrait"]["text-below"].x1 - zn.REGIONS["portrait"]["headline"].x0     # 588

    def label(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.05,
                        tracking_em=-0.015, prefer_lines=1)

    def sub(w: int, size: tuple[float, float], weight: int) -> SlotSpec:
        # 9:16 at weight 700: R-29 asks >= 600 at or below 64 px
        return SlotSpec(w=w, max_lines=2, size=size, role="label", face="body", weight=weight, line_height=1.35)

    return MappingProxyType({
        "label": MappingProxyType({"landscape": MappingProxyType({"default": label(land_w, (64, 96))}),
                                   "portrait": MappingProxyType({"default": label(port_w, (56, 88))})}),
        "sub": MappingProxyType({"landscape": MappingProxyType({"default": sub(land_w, (40, 52), 600)}),
                                 "portrait": MappingProxyType({"default": sub(port_w, (44, 56), 700)})}),
    })


# --- chart_build's slots (B2 R1): web/js/dynamic/templates/chart_build.js slotRows() through charts.py; the jsdom test
# pins the catalogue's rows to them.
def _chart_build_slots() -> SlotTable:
    rows = chart_build_slot_rows()
    return MappingProxyType({
        slot: MappingProxyType({fmt: MappingProxyType({"default": SlotSpec(**row)}) for fmt, row in per.items()})
        for slot, per in rows.items()})


# --- animation_stage's slots (B2 R1): web/js/dynamic/templates/animation_stage.js LAYOUT, LINE_HEIGHT and boxes();
# animation_stage.test.js pins the catalogue's rows to them. The widths are the boxes the title and the step label are
# laid out in: 16:9 the critical box (x 96-1824), 9:16 the headline region (x 192-888) and the lower critical text
# column (x 192-780). The sizes are B2 proposals pending the owner's review: below R-29's nominal heading / body
# ranges and above its floors, as deviation 31 allows.
def _animation_stage_slots() -> SlotTable:
    wide = zn.REGIONS["landscape"]["full"].w            # 1728
    headline = zn.REGIONS["portrait"]["headline"].w     # 696
    column = zn.ZONES["portrait"].text_column.w         # 588
    title_l = SlotSpec(w=wide, max_lines=2, size=(64, 88), role="title", face="display", weight=700,
                       line_height=1.15, prefer_lines=1)
    label_l = SlotSpec(w=wide, max_lines=2, size=(44, 56), role="label", face="body", weight=700, line_height=1.35)
    title_p = SlotSpec(w=headline, max_lines=2, size=(56, 88), role="title", face="display", weight=700,
                       line_height=1.15, prefer_lines=1)
    label_p = SlotSpec(w=column, max_lines=2, size=(48, 64), role="label", face="body", weight=700, line_height=1.35)
    return MappingProxyType({
        "title": MappingProxyType({"landscape": MappingProxyType({"default": title_l}),
                                   "portrait": MappingProxyType({"default": title_p})}),
        "label": MappingProxyType({"landscape": MappingProxyType({"default": label_l}),
                                   "portrait": MappingProxyType({"default": label_p})}),
    })


# --- list_build's slots (B2c): web/js/dynamic/templates/list_build.js LAYOUT, PAD, boxes() and slotRows();
# list_build.test.js pins the catalogue's rows to slotRows. The 16:9 "default" variant is the split layout (the
# narrower boxes: a size fitted there is an upper bound for the centred one too).
# list_build.js PAD (cues.js FOCUS_OUTSET + FOCUS_PX): a row's text sits as far inside its card as the look_here outline
# runs outside it (B2c review V4: the outline goes round the card, outside its state border)
LIST_PAD: Final = 12
LIST_NUMBER: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType({"landscape": (64, 16), "portrait": (56, 12)})


def _list_build_slots() -> SlotTable:
    def heading(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.15,
                        prefer_lines=1)

    def text(w: int, size: tuple[float, float], weight: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=None, size=size, role="body", face="body", weight=weight, line_height=1.35)

    regions = zn.REGIONS["landscape"]
    portrait = zn.ZONES["portrait"]
    num_l, gap_l = LIST_NUMBER["landscape"]
    num_p, gap_p = LIST_NUMBER["portrait"]
    split_w = regions["left-55"].w - 2 * LIST_PAD - num_l - gap_l        # 834
    centred_w = regions["centre-60"].w - 2 * LIST_PAD - num_l - gap_l    # 932
    row_w = portrait.text_column.w - 2 * LIST_PAD - num_p - gap_p        # 496
    out = {
        "heading": {"landscape": {"default": heading(regions["top-band"].w, (84, 96)),
                                  "centred": heading(regions["centre-60"].w, (84, 96))},
                    "portrait": {"default": heading(portrait.title.w, (56, 88))}},
        "item": {"landscape": {"default": text(split_w, (40, 52), 700), "centred": text(centred_w, (40, 52), 700)},
                 "portrait": {"default": text(row_w, (56, 64), 700)}},
        "row": {"portrait": {"default": text(row_w, (40, 40), 600)}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})

# --- chapter_bumper's slots (B2 demo): web/js/dynamic/templates/chapter_bumper.js LAYOUT / LINE_HEIGHT (§4.3;
# deviation 14 "the bumper numeral is a decorative outline role outside R-29"): the bumper sets its text in the `full`
# region (16:9) and in the upper critical box `text-above` (9:16). The title is the chapter's name (critical); the
# kicker (the label's non-digit words) and the numeral are decoration. chapter_bumper.test.js pins the catalogue's rows
# to the template's own ``slotRows``. Every size is a B2 proposal pending the owner's review.
def _chapter_bumper_slots() -> SlotTable:
    wide = zn.REGIONS["landscape"]["full"].w            # 1728
    narrow = zn.REGIONS["portrait"]["text-above"].w     # 696

    def title(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.06,
                        prefer_lines=1)

    def kicker(w: int, size: float) -> SlotSpec:
        return SlotSpec(w=w, max_lines=1, size=(size, size), role="label", face="body", weight=600,
                        line_height=1.2, critical=False)

    def numeral(w: int, size: float) -> SlotSpec:
        return SlotSpec(w=w, max_lines=1, size=(size, size), role="label", face="display", weight=700,
                        line_height=1.0, critical=False, tabular_nums=True)

    out = {
        "kicker": {"landscape": {"default": kicker(wide, 40)}, "portrait": {"default": kicker(narrow, 44)}},
        "numeral": {"landscape": {"default": numeral(wide, 320)}, "portrait": {"default": numeral(narrow, 240)}},
        "title": {"landscape": {"default": title(wide, (84, 96))}, "portrait": {"default": title(narrow, (56, 88))}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- outro_next's slots (step 5): web/js/dynamic/templates/outro_next.js LAYOUT, plan() and slotRows() (outro_next.test.js
# pins the catalogue's rows to them). 16:9 `next_up`: the statement in left-55 (938), the "Next up" card in right-45
# less its padding and edge (664); `end_screen` (§2.5): everything left of the end-screen half less a gutter (840 px
# column, the card 738 inside it); `default` is the narrowest of the two (a scene without a stored variant). 9:16: the
# loop line across the hook band (696), the call-to-action card in the text column (588). B2 proposals, pending the
# owner's review.
OUTRO_KINDS: Final = ("takeaway", "paragraph", "heading")  # outro_next.js STATEMENT_KINDS
OUTRO_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 48, "portrait": 24})
OUTRO_GUTTER: Final = 24


def _outro_next_slots() -> SlotTable:
    border = int(zn.LAYOUT.border["card"])
    land = zn.REGIONS["landscape"]
    column = zn.END_SCREEN.box.x0 - OUTRO_GUTTER - land["full"].x0                          # 840
    text = {"next_up": land["left-55"].w, "end_screen": column}                              # 938, 840
    card = {"next_up": land["right-45"].w - 2 * OUTRO_PAD["landscape"] - border,             # 664
            "end_screen": column - 2 * OUTRO_PAD["landscape"] - border}                      # 738
    port = {"text": zn.REGIONS["portrait"]["hook"].w, "card": zn.ZONES["portrait"].text_column.w}  # 696, 588

    def label(w: int, size: tuple[float, float], lines: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=lines, size=size, role="label", face="body", weight=600, line_height=1.35)

    def title(w: int, size: tuple[float, float], lines: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=lines, size=size, role="title", face="display", weight=700, line_height=1.05,
                        tracking_em=-0.015)

    def per(widths: Mapping[str, int], make: Callable[[int], SlotSpec]) -> dict[str, SlotSpec]:
        return {"default": make(min(widths.values())), **{v: make(w) for v, w in widths.items()}}

    out = {
        "kicker": {"landscape": per(text, lambda w: label(w, (40, 48), 2)),
                   "portrait": {"default": label(port["text"], (44, 48), 2)}},
        "statement": {"landscape": per(text, lambda w: title(w, (64, 96), 5)),
                      "portrait": {"default": title(port["text"], (64, 88), 4)}},
        "label": {"landscape": per(card, lambda w: label(w, (40, 40), 1)),
                  "portrait": {"default": label(port["card"], (44, 44), 1)}},
        "next": {"landscape": per(card, lambda w: title(w, (56, 72), 3)),
                 "portrait": {"default": title(port["card"], (56, 64), 2)}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in pf.items()})
                             for slot, pf in out.items()})


# --- definition_card's slots (B2 demo): web/js/dynamic/templates/definition_card.js LAYOUT, boxes() and slotRows()
# (definition_card.test.js pins the catalogue's rows to slotRows). The 16:9 "default" variant is the narrowest of the
# three (card_image_left), so a size stored without knowing the variant is an upper bound for all of them; 9:16 has one
# column (the text column, which the card's padding grows around), so one "default". The meaning and the example rows
# have no line cap: their height, not a line count, has to fit the card (the B2c pilots' rule). Line heights are the
# type scale's Latin values; the page sets Indic text at the Indic floors (1.25 / 1.45). B2 proposals, pending the
# owner's review.
DEFINITION_CARD_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 48, "portrait": 32})
DEFINITION_CARD_MARKER: Final = 36   # the example rows' marker column
DEFINITION_CARD_GUTTER: Final = 24   # what the 16:9 card keeps from the picture beside it
# definition_card.js PICTURE_PANELS: the panels it shows beside the card, in 16:9 only (9:16 holds the card alone)
DEFINITION_PANELS: Final = ("figure", "image", "manim", "gif")


def _definition_card_slots() -> SlotTable:
    def term(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.05,
                        tracking_em=-0.015, prefer_lines=1)

    def body(w: int, size: tuple[float, float], weight: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=None, size=size, role="body", face="body", weight=weight, line_height=1.35)

    regions = zn.REGIONS["landscape"]
    pad_l = DEFINITION_CARD_PAD["landscape"]
    icon_col = int(zn.LAYOUT.icon["feature_px"]) + pad_l            # the 16:9 icon column and its gap
    widths = {
        "card_icon_right": regions["full"].w - 2 * pad_l - icon_col,                     # 1264
        "card_image_left": regions["right-45"].w - DEFINITION_CARD_GUTTER - 2 * pad_l,   # 646
        "centred": regions["centre-60"].w - 2 * pad_l,                                   # 940
    }
    widths = {"default": widths["card_image_left"], **widths}
    port = zn.ZONES["portrait"].text_column.w                       # 588
    out = {
        "term": {"landscape": {v: term(w, (84, 120)) for v, w in widths.items()},
                 "portrait": {"default": term(port, (56, 88))}},
        "meaning": {"landscape": {v: body(w, (48, 56), 400) for v, w in widths.items()},
                    "portrait": {"default": body(port, (56, 64), 600)}},
        "example": {"landscape": {v: body(w - DEFINITION_CARD_MARKER, (48, 52), 400) for v, w in widths.items()},
                    "portrait": {"default": body(port - DEFINITION_CARD_MARKER, (56, 60), 600)}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- hook_question's slots (B2 demo): web/js/dynamic/templates/hook_question.js LAYOUT, LINE_HEIGHT, TRACKING_EM,
# boxes() and slotRows() (hook_question.test.js pins the two against each other). The stack's width is the box the hook
# is set in: 16:9 the centre 60% (1036 px), 9:16 the hook band (696 px, §2.5 / §3.3). The `true_false` variant's pill
# costs the 9:16 hook one of its four lines; the page's own cap is the box's height (hook_question.js fitInBox), so a
# scene with an eyebrow needs no variant of its own.
HOOK_QUESTION_SIZE: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(
    {"landscape": (112, 140), "portrait": (88, 104)})          # §4.3 16:9; R-29's 9:16 `hook` range
HOOK_QUESTION_LINES: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(  # default, true_false
    {"landscape": (3, 3), "portrait": (4, 3)})
HOOK_QUESTION_LABELS: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(  # pill, eyebrow
    {"landscape": (48, 40), "portrait": (44, 44)})
HOOK_QUESTION_REGION: Final[Mapping[Format, str]] = MappingProxyType(
    {"landscape": "centre-60", "portrait": "hook"})


def _aadhi_slots(rows: Mapping[str, Mapping[Format, Mapping[str, Mapping[str, object]]]]) -> SlotTable:
    """``SlotSpec`` tables of the presenter templates from ``dynamic/presenter.py``'s rows (aadhi_intro.js /
    aadhi_recap.js ``slotRows``)."""
    return MappingProxyType({slot: MappingProxyType({fmt: MappingProxyType({v: SlotSpec(**row)  # type: ignore[arg-type]
                                                                          for v, row in per.items()})
                                                     for fmt, per in fmts.items()})
                             for slot, fmts in rows.items()})


def _hook_question_slots() -> SlotTable:
    """hook_question's slots: the hook (``question``, per variant), the "True or false?" ``pill`` and the scene
    heading's ``eyebrow`` (one-line labels at R-29's label floor)."""
    out: dict[str, dict[Format, dict[str, SlotSpec]]] = {"question": {}, "pill": {}, "eyebrow": {}}
    for fmt in FORMATS:
        width = zn.REGIONS[fmt][HOOK_QUESTION_REGION[fmt]].w
        size = HOOK_QUESTION_SIZE[fmt]
        lines, pill_lines = HOOK_QUESTION_LINES[fmt]
        pill, eyebrow = HOOK_QUESTION_LABELS[fmt]

        def question(max_lines: int, *, w: int = width, px: tuple[int, int] = size) -> SlotSpec:
            return SlotSpec(w=w, max_lines=max_lines, size=px, role="title", face="display", weight=700,
                            line_height=1.05, tracking_em=-0.015)

        def label(px: int, weight: int, *, w: int = width) -> SlotSpec:
            return SlotSpec(w=w, max_lines=1, size=(px, px), role="label", face="body", weight=weight,
                            line_height=1.35)

        out["question"][fmt] = {"default": question(lines), "true_false": question(pill_lines)}
        out["pill"][fmt] = {"default": label(pill, 700)}
        out["eyebrow"][fmt] = {"default": label(eyebrow, 600)}
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- kinetic_statement's slots (step 5): web/js/dynamic/templates/kinetic_statement.js LAYOUT, COLUMNS, LINE_HEIGHT,
# TRACKING_EM, boxes() and slotRows() (kinetic_statement.test.js pins the two against each other). The width is the
# box the statement is set in: 16:9 grid columns 1-10 (1436 px, §4.3 "left-aligned"), 9:16 the hook band's column
# (the `text-above` region, 696 px: centred on the hook band, growing into the stage, R-59). Four statement lines fit
# that box at every size in every script, so the line-count fit here and the page's height check agree for a statement
# alone; the label is a callout's kind (board_generic's word for it), the attribution a quote's caption after an em
# dash and a space. The sizes are a step-5 proposal pending the owner's review.
KINETIC_STATEMENT_SIZE: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(
    {"landscape": (100, 140), "portrait": (88, 104)})          # §4.3 16:9; R-29's 9:16 `hook` range
KINETIC_STATEMENT_LINES: Final = 4
KINETIC_STATEMENT_LABELS: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(  # label, attribution
    {"landscape": (40, 48), "portrait": (44, 44)})
KINETIC_STATEMENT_ATTRIBUTION_LINES: Final = 2


def _kinetic_statement_width(fmt: Format) -> int:
    """The statement's measure: 16:9 grid columns 1-10, 9:16 the `text-above` region (kinetic_statement.js boxes)."""
    return zn.GRID.span(0, 9).w if fmt == "landscape" else zn.REGIONS[fmt]["text-above"].w


def _kinetic_statement_slots() -> SlotTable:
    """kinetic_statement's slots: the ``statement`` (display 700, four lines), a callout's ``label`` (one line) and a
    quote's ``attribution`` (two lines), both at R-29's label floor and weight 600 (its 9:16 weight clause)."""
    out: dict[str, dict[Format, dict[str, SlotSpec]]] = {"statement": {}, "label": {}, "attribution": {}}
    for fmt in FORMATS:
        width = _kinetic_statement_width(fmt)
        label, attribution = KINETIC_STATEMENT_LABELS[fmt]
        out["statement"][fmt] = {"default": SlotSpec(
            w=width, max_lines=KINETIC_STATEMENT_LINES, size=KINETIC_STATEMENT_SIZE[fmt], role="title",
            face="display", weight=700, line_height=1.05, tracking_em=-0.015)}
        out["label"][fmt] = {"default": SlotSpec(w=width, max_lines=1, size=(label, label), role="label",
                                                 face="body", weight=600, line_height=1.35)}
        out["attribution"][fmt] = {"default": SlotSpec(
            w=width, max_lines=KINETIC_STATEMENT_ATTRIBUTION_LINES, size=(attribution, attribution), role="label",
            face="body", weight=600, line_height=1.35)}
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- key_terms' slots (B2 demo): web/js/dynamic/templates/key_terms.js LAYOUT, cardWidth() and slotRows();
# key_terms.test.js pins the catalogue's rows to slotRows. The 16:9 term and meaning widths are the NARROWEST card's
# (four cards on the 12-column grid, less its padding), so a size fitted there is an upper bound for a two- or
# three-term scene too (list_build's rule). 9:16 draws rows, not cards (no padding): the 588 px text column would
# otherwise go on chrome. The size ranges are a B2-demo proposal pending the owner's review, on deviation 31's
# precedent: below R-29's nominal ranges, above its floors.
KEY_TERMS_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 32, "portrait": 0})
KEY_TERMS_CARDS_MAX: Final = 4  # §4.3: 2-4 cards in a row
KEY_TERMS_MEANING_WEIGHT: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 400, "portrait": 600})
# key_terms.js PANEL_TERMS_MAX: how many terms may stand beside the scene's panel in 16:9 (two cards in the left 55%
# are 457 px wide, above the 414 px the slot sizes were fitted against; three would be 296 px, which R-29's 48 px
# term floor cannot be set in).
KEY_TERMS_PANEL_MAX: Final = 2


def _key_terms_slots() -> SlotTable:
    def heading(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.15,
                        prefer_lines=1)

    def term(w: int, size: tuple[float, float]) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.15)

    def meaning(w: int, size: tuple[float, float], weight: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=None, size=size, role="body", face="body", weight=weight, line_height=1.35)

    narrow = (zn.GRID.box.w - zn.GRID.gutter * (KEY_TERMS_CARDS_MAX - 1)) // KEY_TERMS_CARDS_MAX   # 414
    land = narrow - 2 * KEY_TERMS_PAD["landscape"]                                                 # 350
    port = zn.ZONES["portrait"].text_column.w - 2 * KEY_TERMS_PAD["portrait"]                      # 588
    out = {
        "heading": {"landscape": {"default": heading(zn.REGIONS["landscape"]["top-band"].w, (84, 96))},
                    "portrait": {"default": heading(zn.ZONES["portrait"].title.w, (56, 88))}},
        "term": {"landscape": {"default": term(land, (48, 64))},
                 "portrait": {"default": term(port, (44, 64))}},
        "meaning": {"landscape": {"default": meaning(land, (40, 48), KEY_TERMS_MEANING_WEIGHT["landscape"])},
                    "portrait": {"default": meaning(port, (40, 48), KEY_TERMS_MEANING_WEIGHT["portrait"])}},
        # the terminal card of a side panel, mono, in the panel's own box less its padding (16:9 the right 45%; 9:16
        # the reel beat's card in the text column, owner decision 4); _shared.js layoutPanel fits it on the page and
        # panelTexts reports it in this slot
        "terminal": {"landscape": {"default": _panel_terminal_slot(zn.REGIONS["landscape"]["right-45"].w,
                                                                   "landscape")},
                     "portrait": {"default": _panel_terminal_slot(zn.ZONES["portrait"].text_column.w, "portrait")}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


# --- recap_retrieval's slots (B2 demo): web/js/dynamic/templates/recap_retrieval.js LAYOUT. A card's padding, the gap
# between a card's parts, the think ring's box and its number's size, the 16:9 tile (three columns of the 12-column
# grid: 4 columns of 122 px plus 3 of its 24 px gutters) and the card icon size the template draws a tile's icon at.
RECAP_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 32, "portrait": 24})
RECAP_GAP: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 24, "portrait": 24})
RECAP_RING: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType({"landscape": (120, 40), "portrait": (96, 44)})
RECAP_TILE_W: Final = 560
RECAP_ICON_PX: Final = 96


def _recap_retrieval_slots() -> SlotTable:
    """recap_retrieval's slots, variant ``default`` (§4.3 gives the template no look variant, and how many tiles sit
    side by side never changes a tile's width). ``label`` sits on the background above the card; ``prompt`` and
    ``answer`` share the card's text box; ``tile`` is a tile less its padding and, in 9:16, the icon column beside it
    (kept whether or not icons are drawn, so the stored width is the one the page lays out); ``ring`` is the
    countdown's number in the think ring's box, never critical (R-07: the think time is the ring itself).
    recap_retrieval.test.js pins this table to the template's own ``slotRows``."""
    def table(fmt: Format) -> dict[str, SlotSpec]:
        pad, gap = RECAP_PAD[fmt], RECAP_GAP[fmt]
        ring_px, ring_num = RECAP_RING[fmt]
        if fmt == "landscape":
            card_w = zn.REGIONS["landscape"]["centre-60"].w                     # 1036
            tile_text = RECAP_TILE_W - 2 * pad                                  # 496
            label, prompt = (40, 40, 1, 1, 600), (84, 96, 3, 2, 700)
            answer, tile = (40, 48, 3, None, 400), (40, 48, 3, None, 600)
        else:
            card_w = zn.ZONES["portrait"].text_column.w                         # 588
            tile_text = card_w - 2 * pad - RECAP_ICON_PX - gap                  # 420
            label, prompt = (44, 44, 1, 1, 600), (56, 64, 3, 2, 700)
            answer, tile = (40, 44, 3, None, 600), (40, 44, 2, None, 600)
        card_text = card_w - 2 * pad

        def spec(s: tuple[int, int, int, int | None, int], w: int, role: str, face: FaceRole, lh: float, *,
                 critical: bool = True, tabular: bool = False) -> SlotSpec:
            return SlotSpec(w=w, max_lines=s[2], size=(s[0], s[1]), role=role, face=face, weight=s[4],
                            line_height=lh, prefer_lines=s[3], critical=critical, tabular_nums=tabular)

        return {
            "label": spec(label, card_w, "label", "body", 1.3),
            "prompt": spec(prompt, card_text, "title", "display", 1.15),
            "answer": spec(answer, card_text, "body", "body", 1.35),
            "tile": spec(tile, tile_text, "body", "body", 1.35),
            "ring": spec((ring_num, ring_num, 1, 1, 700), ring_px, "label", "body", 1.3,
                         critical=False, tabular=True),
        }

    land, port = table("landscape"), table("portrait")
    return MappingProxyType({slot: MappingProxyType({
        "landscape": MappingProxyType({"default": land[slot]}),
        "portrait": MappingProxyType({"default": port[slot]}),
    }) for slot in land})


# --- check's slots (B2 demo): web/js/dynamic/templates/check.js LAYOUT and boxes(). A tile's padding, the gap between
# the block's parts, the think ring's box and its number's size, the letter column a tile keeps whether or not it
# draws one, and the 16:9 tile (two fixed columns of the content box: (1728 - 24) / 2).
CHECK_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 24, "portrait": 16})
CHECK_GAP: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 24, "portrait": 16})
CHECK_RING: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType({"landscape": (120, 40), "portrait": (96, 44)})
CHECK_BADGE: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 48, "portrait": 44})
CHECK_TILE_W: Final = 852


def _check_slots() -> SlotTable:
    """check's slots, variant ``default``: a check's layout is a function of its data (how many options it has),
    never of the stored variant, so a stored variant can never disagree with the box the page lays text out in
    (review D7). ``label`` sits at the top left of the band, left of the think ring; ``question`` and ``explain``
    take the block's full width; ``option`` is a tile less its padding, its letter column and the room the ``ok``
    check needs (both kept whether or not they are drawn, so the stored width is the one the page lays out);
    ``ring`` is the countdown's number in the ring's box, never critical (R-07: the think time is the ring itself).
    check.test.js pins this table to the template's own ``slotRows``."""
    def table(fmt: Format) -> dict[str, SlotSpec]:
        pad, gap = CHECK_PAD[fmt], CHECK_GAP[fmt]
        ring_px, ring_num = CHECK_RING[fmt]
        badge = CHECK_BADGE[fmt]
        col = zn.ZONES["portrait"].text_column if fmt == "portrait" else zn.ZONES["landscape"].body
        if fmt == "landscape":
            label, question = (40, 40, 1, 1, 600), (56, 72, 3, 2, 700)
            option, explain = (40, 48, 2, 1, 600), (40, 48, 2, 1, 400)
            tile_w = CHECK_TILE_W                                           # 852
        else:
            label, question = (44, 44, 1, 1, 600), (48, 60, 4, 3, 700)
            option, explain = (40, 44, 2, 1, 600), (40, 44, 2, 1, 600)
            tile_w = col.w                                                  # 588
        label_w = col.x1 - ring_px - gap - col.x0                           # 1584 / 476
        option_w = tile_w - 2 * pad - 2 * (badge + gap)                     # 660 / 436

        def spec(s: tuple[int, int, int, int | None, int], w: int, role: str, face: FaceRole, lh: float, *,
                 critical: bool = True, tabular: bool = False) -> SlotSpec:
            return SlotSpec(w=w, max_lines=s[2], size=(s[0], s[1]), role=role, face=face, weight=s[4],
                            line_height=lh, prefer_lines=s[3], critical=critical, tabular_nums=tabular)

        return {
            "label": spec(label, label_w, "label", "body", 1.3),
            "question": spec(question, col.w, "title", "display", 1.15),
            "option": spec(option, option_w, "body", "body", 1.35),
            "explain": spec(explain, col.w, "body", "body", 1.35),
            "ring": spec((ring_num, ring_num, 1, 1, 700), ring_px, "label", "body", 1.3,
                         critical=False, tabular=True),
        }

    land, port = table("landscape"), table("portrait")
    return MappingProxyType({slot: MappingProxyType({
        "landscape": MappingProxyType({"default": land[slot]}),
        "portrait": MappingProxyType({"default": port[slot]}),
    }) for slot in land})


# --- predict's slots (R1 step 4): web/js/dynamic/templates/predict.js LAYOUT, boxes() and chipBox(). The card's
# padding, the think ring's box and its number's size, and the "Your prediction?" chip of the explaining scene (its
# text size, its horizontal padding and the widest it may be).
PREDICT_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 32, "portrait": 24})
PREDICT_RING: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType({"landscape": (120, 40),
                                                                          "portrait": (96, 44)})
PREDICT_CHIP: Final[Mapping[Format, tuple[int, int, int]]] = MappingProxyType({"landscape": (40, 24, 720),
                                                                               "portrait": (44, 20, 420)})


def _predict_slots() -> SlotTable:
    """predict's slots, variant ``default`` (§4.3 gives the template no look variant). ``label`` sits on the background
    above the card; ``question`` and ``prompt`` ("Pause and predict") share the card's text box; ``ring`` is the think
    ring's number, never critical (R-07: the think time is the ring itself); ``chip`` is the "Your prediction?" line
    the explaining scene carries (``predict.js buildChip``), one line and never critical - a reminder of another
    scene's words, cut with an ellipsis as board_generic's kicker is. predict.test.js pins this table to the
    template's own ``slotRows``."""
    def table(fmt: Format) -> dict[str, SlotSpec]:
        pad = PREDICT_PAD[fmt]
        ring_px, ring_num = PREDICT_RING[fmt]
        chip_px, chip_pad, chip_max = PREDICT_CHIP[fmt]
        if fmt == "landscape":
            card_w = zn.REGIONS["landscape"]["centre-60"].w                     # 1036
            # the question comes down to 64 px rather than take a fourth line: the card holds three plus the
            # instruction and the think ring (predict.js LAYOUT)
            label, question, prompt = (40, 40, 1, 1, 600), (64, 96, 3, 2, 700), (40, 40, 1, 1, 600)
        else:
            card_w = zn.ZONES["portrait"].text_column.w                         # 588
            label, question, prompt = (44, 44, 1, 1, 600), (56, 64, 3, 2, 700), (44, 44, 1, 1, 600)
        card_text = card_w - 2 * pad
        chip_w = min(chip_max, zn.ZONES[fmt].header.w) - 2 * chip_pad

        def spec(s: tuple[int, int, int, int | None, int], w: int, role: str, face: FaceRole, lh: float, *,
                 critical: bool = True, tabular: bool = False) -> SlotSpec:
            return SlotSpec(w=w, max_lines=s[2], size=(s[0], s[1]), role=role, face=face, weight=s[4],
                            line_height=lh, prefer_lines=s[3], critical=critical, tabular_nums=tabular)

        return {
            "label": spec(label, card_w, "label", "body", 1.3),
            "question": spec(question, card_text, "title", "display", 1.15),
            "prompt": spec(prompt, card_text, "label", "body", 1.3),
            "ring": spec((ring_num, ring_num, 1, 1, 700), ring_px, "label", "body", 1.3,
                         critical=False, tabular=True),
            "chip": spec((chip_px, chip_px, 1, 1, 600), chip_w, "label", "body", 1.3, critical=False),
        }

    land, port = table("landscape"), table("portrait")
    return MappingProxyType({slot: MappingProxyType({
        "landscape": MappingProxyType({"default": land[slot]}),
        "portrait": MappingProxyType({"default": port[slot]}),
    }) for slot in land})


# --- worked_steps' slots (B2 demo): web/js/dynamic/templates/worked_steps.js LAYOUT, PAD, boxes() and slotRows();
# worked_steps.test.js pins the catalogue's rows to slotRows. The 16:9 "default" variant is the split layout (the steps
# beside the formula's card: the narrower column, so a size fitted there is an upper bound for the centred one too).
# The formula and its meaning exist in the split layout and in 9:16 only.
WORKED_PAD: Final = 12  # worked_steps.js PAD (cues.js FOCUS_OUTSET + FOCUS_PX)
WORKED_NUMBER: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType(
    {"landscape": (64, 16), "portrait": (56, 12)})      # the step-number column and its gap
WORKED_CARD_PAD: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 40, "portrait": 28})
WORKED_HEADING: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (84, 96), "portrait": (56, 88)})
WORKED_STEP: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (40, 52), "portrait": (56, 64)})      # a step's text (R-29 body, down to the text floor)
WORKED_WHY: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (40, 40), "portrait": (44, 44)})      # the justification, at R-29's label floor
WORKED_FORMULA: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (64, 96), "portrait": (68, 88)})      # the TeX size in the card (9:16 above R-29's 64 px rule)
WORKED_WEIGHT: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 400, "portrait": 600})


def _worked_steps_slots() -> SlotTable:
    def heading(w: int, fmt: Format) -> SlotSpec:
        return SlotSpec(w=w, max_lines=2, size=WORKED_HEADING[fmt], role="title", face="display", weight=700,
                        line_height=1.15, prefer_lines=1)

    def body(w: int, size: tuple[float, float], weight: int, role: str = "body", lines: int | None = None,
             line_height: float = 1.35) -> SlotSpec:
        return SlotSpec(w=w, max_lines=lines, size=size, role=role, face="body", weight=weight,
                        line_height=line_height)

    regions = zn.REGIONS["landscape"]
    portrait = zn.ZONES["portrait"]
    num_l, gap_l = WORKED_NUMBER["landscape"]
    num_p, gap_p = WORKED_NUMBER["portrait"]
    split_w = regions["left-55"].w - 2 * WORKED_PAD - num_l - gap_l        # 834
    centred_w = regions["centre-60"].w - 2 * WORKED_PAD - num_l - gap_l    # 932
    step_p = portrait.text_column.w - 2 * WORKED_PAD - num_p - gap_p       # 496
    card_l = regions["right-45"].w - 2 * WORKED_CARD_PAD["landscape"]      # 686
    card_p = portrait.text_column.w - 2 * WORKED_CARD_PAD["portrait"]      # 532
    out = {
        "heading": {"landscape": {"default": heading(regions["top-band"].w, "landscape"),
                                  "centred": heading(regions["centre-60"].w, "landscape")},
                    "portrait": {"default": heading(portrait.title.w, "portrait")}},
        "step": {"landscape": {"default": body(split_w, WORKED_STEP["landscape"], 700),
                               "centred": body(centred_w, WORKED_STEP["landscape"], 700)},
                 "portrait": {"default": body(step_p, WORKED_STEP["portrait"], 700)}},
        "row": {"portrait": {"default": body(step_p, (40, 40), WORKED_WEIGHT["portrait"])}},
        "why": {"landscape": {"default": body(split_w, WORKED_WHY["landscape"], WORKED_WEIGHT["landscape"],
                                              "label", 2),
                              "centred": body(centred_w, WORKED_WHY["landscape"], WORKED_WEIGHT["landscape"],
                                              "label", 2)},
                "portrait": {"default": body(step_p, WORKED_WHY["portrait"], WORKED_WEIGHT["portrait"], "label", 2)}},
        # the formula is a TeX picture: textfit does not measure it (max_lines None, line height 1), and it is
        # reported as body text at its TeX size (formula_focus's FORMULA_SLOT)
        "formula": {"landscape": {"default": body(card_l, WORKED_FORMULA["landscape"], 400, "body", None, 1.0)},
                    "portrait": {"default": body(card_p, WORKED_FORMULA["portrait"], 400, "body", None, 1.0)}},
        "meaning": {"landscape": {"default": body(card_l, WORKED_WHY["landscape"], WORKED_WEIGHT["landscape"],
                                                  "label", 2)},
                    "portrait": {"default": body(card_p, WORKED_WHY["portrait"], WORKED_WEIGHT["portrait"],
                                                 "label", 2)}},
        # the terminal card of a side panel, less its padding: 16:9 the right-45 box; 9:16 the reel beat's card in
        # the text column (owner decision 4)
        "terminal": {"landscape": {"default": _panel_terminal_slot(regions["right-45"].w, "landscape")},
                     "portrait": {"default": _panel_terminal_slot(zn.ZONES["portrait"].text_column.w, "portrait")}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})

# --- the registry (§4.3) -------------------------------------------------------------------------------------------

# R-19 scene length where §4.3 gives no timing; board_generic, the fallback for every scene kind (worked examples and
# quizzes included), takes R-19's widest bounds (<= 45 s in 16:9, <= 30 s in 9:16).
# code_walkthrough.js `slotRows`, value for value (code_walkthrough.test.js pins the catalogue to it once registered)
CODE_SIZE: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType({"landscape": (36, 48), "portrait": (40, 48)})
CODE_HEADING: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (84, 96), "portrait": (48, 64)})
CODE_WEIGHT: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 400, "portrait": 700})
CODE_LINE_HEIGHT: Final = 1.2  # code_walkthrough.js CODE_LH.latin (an Indic program takes 1.45, as textfit's Indic measure)
CODE_PAD_X: Final = 24
CODE_GAP: Final = 24
CODE_GUTTER_EM: Final = 1.2  # two digits of the mono face's 0.6 em advance


def _code_walkthrough_slots() -> SlotTable:
    regions = zn.REGIONS["landscape"]
    landscape = zn.ZONES["landscape"]
    portrait = zn.ZONES["portrait"]
    gutter = math.ceil(CODE_GUTTER_EM * CODE_SIZE["landscape"][0])  # 44 at 36 px

    def heading(fmt: Format, w: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=1, size=CODE_HEADING[fmt], role="title", face="display", weight=700,
                        line_height=1.15)

    def code(fmt: Format, w: int, headed: bool, panel: bool) -> SlotSpec:
        box = CODE_BOX[fmt]
        return SlotSpec(w=w, max_lines=(box.headed_px if headed else box.lines_px) // box.pitch, size=CODE_SIZE[fmt],
                        role="body", face="mono", weight=CODE_WEIGHT[fmt], line_height=CODE_LINE_HEIGHT,
                        max_chars=box.chars_panel if panel else box.chars)

    alone = regions["full"].w - 2 * CODE_PAD_X - gutter - CODE_GAP          # 1612
    beside = regions["left-55"].w - 2 * CODE_PAD_X - gutter - CODE_GAP      # 822
    column = portrait.critical[0].w                                         # 696: the upper critical box
    out = {
        "heading": {"landscape": {"default": heading("landscape", landscape.title.w)},
                    "portrait": {"default": heading("portrait", portrait.title.w)}},
        "code": {"landscape": {"default": code("landscape", alone, False, False),
                               "headed": code("landscape", alone, True, False),
                               "panel": code("landscape", beside, False, True),
                               "headed_panel": code("landscape", beside, True, True)},
                 "portrait": {"default": code("portrait", column, False, False),
                              "headed": code("portrait", column, True, False)}},
        # the terminal card of a side panel (16:9 only), in the right-45 box less its padding
        "terminal": {"landscape": {"default": _panel_terminal_slot(regions["right-45"].w, "landscape")}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


SCENE_LENGTH: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (6.0, 30.0), "portrait": (4.0, 20.0)})
SCENE_LENGTH_LONG: Final[Mapping[Format, tuple[float, float]]] = MappingProxyType(
    {"landscape": (6.0, 45.0), "portrait": (4.0, 30.0)})


def _d(landscape: tuple[float, float | None], portrait: tuple[float, float | None] | None = None, *,
       per_item: bool = False) -> Mapping[Format, Duration]:
    p = portrait if portrait is not None else landscape
    return MappingProxyType({"landscape": Duration(landscape[0], landscape[1], per_item),
                             "portrait": Duration(p[0], p[1], per_item)})


def _v(landscape: Iterable[str] = (), portrait: Iterable[str] | None = None) -> Mapping[Format, tuple[str, ...]]:
    lv = tuple(landscape)
    return MappingProxyType({"landscape": lv, "portrait": lv if portrait is None else tuple(portrait)})


def _c(kinds: Iterable[VisualEventKind], targets: Iterable[TargetForm] | None = None,
       narrow: Mapping[VisualEventKind, Iterable[TargetForm]] | None = None) -> Consumes:
    """``Consumes`` of ``kinds`` on ``targets`` (default: whole board items; none when there are no kinds), with the
    kinds of ``narrow`` drawn only on the forms it gives them."""
    k = tuple(kinds)
    return Consumes(k, tuple(targets) if targets is not None else (("item",) if k else ()),
                    tuple((kind, tuple(forms)) for kind, forms in (narrow or {}).items()))


# --- myth_fact's slots (step 5): web/js/dynamic/templates/myth_fact.js LAYOUT, boxes() and slotRows();
# myth_fact.test.js pins the catalogue's rows to slotRows. The cards' text widths are the card boxes less their padding
# (24) on both sides and their 6 px `warn` / `ok` edge: 16:9 the myth in grid columns 1-5 (706 px) and the fact in 6-12
# (998 px); 9:16 both the text column (588 px) exactly. The heading spans the top band (1728), the supporting lines
# left-55 (938), measured at their bold term's weight (an upper bound on the regular meaning beside it). The label is
# one line at a fixed size. 9:16 draws no heading, supporting line or panel. B2 proposals, pending the owner's review.
MYTH_FACT_PAD: Final = 24
MYTH_FACT_EDGE: Final = 6  # the catalogue's border.card
MYTH_FACT_LABEL: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 48, "portrait": 52})
MYTH_FACT_WEIGHT: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 400, "portrait": 600})


def _myth_fact_slots() -> SlotTable:
    def body(w: int, size: tuple[float, float], weight: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=None, size=size, role="body", face="body", weight=weight, line_height=1.35)

    def label(w: int, px: int) -> SlotSpec:
        return SlotSpec(w=w, max_lines=1, size=(px, px), role="label", face="body", weight=700, line_height=1.2)

    inner = 2 * MYTH_FACT_PAD + MYTH_FACT_EDGE
    myth_w = zn.GRID.span(0, 4).w - inner                                                  # 652 (columns 1-5)
    fact_w = zn.GRID.span(5, 11).w - inner                                                 # 944 (columns 6-12)
    port = zn.ZONES["portrait"].text_column.w                                               # 588
    out = {
        "heading": {"landscape": {"default": SlotSpec(
            w=zn.REGIONS["landscape"]["top-band"].w, max_lines=2, size=(84, 96), role="title", face="display",
            weight=700, line_height=1.15, prefer_lines=1)}},
        "support": {"landscape": {"default": body(zn.REGIONS["landscape"]["left-55"].w, (40, 48), 700)}},
        "label": {"landscape": {"default": label(myth_w, MYTH_FACT_LABEL["landscape"])},
                  "portrait": {"default": label(port, MYTH_FACT_LABEL["portrait"])}},
        "myth": {"landscape": {"default": body(myth_w, (44, 52), MYTH_FACT_WEIGHT["landscape"])},
                 "portrait": {"default": body(port, (44, 52), MYTH_FACT_WEIGHT["portrait"])}},
        "fact": {"landscape": {"default": body(fact_w, (48, 56), MYTH_FACT_WEIGHT["landscape"])},
                 "portrait": {"default": body(port, (48, 56), MYTH_FACT_WEIGHT["portrait"])}},
        # a side panel's terminal card (16:9 only), mono, in right-45 less its padding: the narrowest box it is drawn in
        "terminal": {"landscape": {"default": _panel_terminal_slot(zn.REGIONS["landscape"]["right-45"].w,
                                                                   "landscape")}},
    }
    return MappingProxyType({slot: MappingProxyType({f: MappingProxyType(v) for f, v in per.items()})
                             for slot, per in out.items()})


_R19 = _d(SCENE_LENGTH["landscape"], SCENE_LENGTH["portrait"])
# Which templates may carry hold_reason "diagram" (the specs below that name _DIAGRAM): A2 proposal, pending owner
# review; not in the design (R-16 lists the reasons and §4.2 gives every template a ``holds`` field, but names no
# template's)
_DIAGRAM: Final[tuple[HoldReason, ...]] = ("diagram",)
_THINK: Final[tuple[HoldReason, ...]] = ("think",)

_SPECS: Final[tuple[TemplateSpec, ...]] = (
    # A. Bookends and structure
    # Aadhi (§3.3; dynamic/presenter.py): the presenter is the stage's medium, the templates draw the text around him.
    # The exit is the presenter's own choreography (look.presenter.t_exit: the brand wipe, the card's slide or fade),
    # not an event. The hook's keyword takes cue_term (§3.3); a reveal is the whole item.
    TemplateSpec("aadhi_intro", "structure", False, "R1", _d((5.0, 8.0)),
                 _c(("reveal", "cue_term"), ("item", "item:term"), {"reveal": ("item",)}),
                 _fit_aadhi_intro, implemented=True, variants=_v(("fullframe", "card_left", "pip", "split"), ("card",)),
                 slots=_aadhi_slots(intro_slots())),
    TemplateSpec("aadhi_recap", "structure", False, "R1", _d((5.0, 8.0)), _c(("reveal",)), _fit_aadhi_recap,
                 implemented=True, slots=_aadhi_slots(recap_slots())),
    # the cue_term reaches a single term of the hook (``<item>:term:<n>``) as well as all of them; a reveal is the
    # whole item (B2c review CG-5: the forms are per kind). ``mystery`` stays in the variants (deviation 16: the page
    # draws it as a plain question until B4's blurred asset).
    TemplateSpec("hook_question", "prompt", False, "R1", _d((4.0, 10.0), (3.0, 6.0)),
                 _c(("reveal", "cue_term"), ("item", "item:term"), {"reveal": ("item",)}),
                 _fit_hook_question, implemented=True,
                 variants=_v(("question", "true_false", "stakes", "mystery", "challenge")),
                 slots=_hook_question_slots()),
    TemplateSpec("roadmap", "structure", False, "W2", _R19, _c(("reveal",)), _never),
    # §4.3 times key terms in 16:9 only ("3–4 s per term (16:9), optional in 9:16"): "optional" is about using the
    # template in 9:16, not its length, so a 9:16 key-terms scene keeps the same per-term range (A2 reading, pending
    # owner review)
    TemplateSpec("key_terms", "definition", False, "R1", _d((3.0, 4.0), per_item=True),
                 # the shared panel helper adds the panel's cues and its `output` beat (_shared.js panelFocusRect):
                 # cue_term is narrowed explicitly now that `targets` is wider (B2c review CG-5)
                 _c(("reveal", "draw", "cue_term", "look_here", "output"),
                    ("item", "item:term", "panel", "panel:row"),
                    {"reveal": ("item", "panel"), "draw": ("item",), "cue_term": ("item", "item:term"),
                     "look_here": ("panel", "panel:row"), "output": ("panel",)}),
                 _fit_key_terms, implemented=True, slots=_key_terms_slots()),
    TemplateSpec("chapter_bumper", "structure", False, "R1", _d((2.0, 3.0), (1.2, 1.8)), _c(()), _fit_chapter_bumper,
                 forced_for=("chapter_card",), implemented=True, slots=_chapter_bumper_slots()),
    TemplateSpec("recap_retrieval", "prompt", False, "R1", _d((25.0, 40.0), (14.0, 20.0)),
                 _c(("reveal", "fill", "countdown", "answer", "cue_term"), ("item", "item:term"),
                    {"reveal": ("item",), "fill": ("item",), "countdown": ("item",), "answer": ("item",),
                     "cue_term": ("item", "item:term")}),
                 _fit_recap_retrieval, implemented=True, holds=_THINK, slots=_recap_retrieval_slots()),
    # §4.3 "6–10 s or 15 s (16:9)", R-11 "or 15 s with end-screen space": two lengths, not 6-15 s; 9:16 has no
    # end-screen space (§2.5)
    TemplateSpec("outro_next", "structure", False, "R1",
                 MappingProxyType({"landscape": Duration(6.0, 10.0, end_screen=zn.END_SCREEN.seconds),
                                   "portrait": Duration(3.0, 5.0)}), _c(("reveal",)), _fit_outro_next,
                 implemented=True, variants=_v(("next_up", "end_screen"), ()), slots=_outro_next_slots()),
    TemplateSpec("thumbnail", "structure", True, "R1", MappingProxyType({"landscape": Duration(0.0, 0.0)}), _c(()),
                 _never, formats=("landscape",), variants=MappingProxyType({"landscape": ()})),
    # B. Explanation
    TemplateSpec("definition_card", "definition", False, "R1", _d((6.0, 12.0), (4.0, 8.0)),
                 _c(("reveal", "cue_term", "look_here"), ("item", "item:term", "panel"),
                    {"reveal": ("item",), "cue_term": ("item", "item:term")}),
                 _fit_definition_card, implemented=True,
                 variants=_v(("card_icon_right", "card_image_left", "centred")),
                 slots=_definition_card_slots()),
    # the cue_term reaches a single term of the statement (``<item>:term:<n>``) as well as all of them; a reveal is the
    # whole item (hook_question's forms). No look variants (§4.3 names none), not dense, never pushed (typography).
    TemplateSpec("kinetic_statement", "statement", False, "R1", _d((3.0, 7.0), (2.0, 5.0)),
                 _c(("reveal", "cue_term"), ("item", "item:term"), {"reveal": ("item",)}),
                 _fit_kinetic_statement, implemented=True, slots=_kinetic_statement_slots()),
    TemplateSpec("list_build", "enumeration", False, "R1", _d((3.0, 5.0), per_item=True),
                 _c(("reveal", "cue_term", "look_here"), ("item", "item:term"), {"reveal": ("item",)}),
                 _fit_list_build, implemented=True, slots=_list_build_slots()),
    # variant names "media_left" / "media_right": A2 proposal, pending owner review; not in the design (§4.3 / §4.4
    # say only that the media side changes at chapter boundaries); 9:16 stacks, so it has none
    TemplateSpec("media_split", "media", True, "R1", _d((8.0, 20.0), (5.0, 12.0)),
                 _c(("reveal", "cue_term", "look_here", "output"), ("item", "item:term", "panel", "panel:row"),
                    {"reveal": ("item",), "cue_term": ("item", "item:term"), "output": ("panel",)}),
                 _fit_media_split, implemented=True, variants=_v(("media_left", "media_right"), ()), holds=_DIAGRAM,
                 push_ok=True, slots=_media_split_slots()),
    # not push_ok: the medium fills the frame and a clip is never scaled or pushed (cues.js showsClip, B2c
    # deviation 28); a still's own move is its stored Ken Burns, which media.js paints
    TemplateSpec("full_bleed_media", "media", True, "R1", _d((3.0, 12.0)),
                 _c(("reveal",), ("item",), {"reveal": ("item",)}), _fit_full_bleed_media,
                 forced_for=("ai_video",), implemented=True, slots=_full_bleed_media_slots()),
    TemplateSpec("spotlight_image", "media", True, "W2", _d((6.0, 12.0)),
                 _c(("reveal", "look_here"), ("item", "panel")), _fit_spotlight_image, holds=_DIAGRAM, push_ok=True),
    TemplateSpec("diagram_build", "media", True, "W2", _d((8.0, 30.0)), _c(("draw", "reveal", "look_here")), _never,
                 holds=_DIAGRAM),
    TemplateSpec("versus", "contrast", True, "W2", _d((8.0, 16.0)),
                 _c(("reveal", "look_here"), ("item", "item:row", "item:column")), _fit_versus),
    TemplateSpec("table_reveal", "table", True, "W2", _d((10.0, 20.0)),
                 _c(("reveal", "look_here"), ("item", "item:row", "item:column")), _fit_table_reveal),
    TemplateSpec("analogy_bridge", "correspondence", True, "W2", _d((8.0, 15.0)), _c(("reveal", "draw")),
                 _fit_analogy_bridge),
    TemplateSpec("process_flow", "sequence", True, "W2", _d((10.0, 25.0)), _c(("reveal", "draw", "look_here")),
                 _fit_process_flow, holds=_DIAGRAM),
    TemplateSpec("cycle_loop", "sequence", True, "W2", _d((8.0, 25.0)), _c(("reveal", "draw", "look_here")),
                 _fit_cycle_loop, holds=_DIAGRAM),
    TemplateSpec("timeline_track", "sequence", True, "W2", _d((8.0, 25.0)), _c(("reveal", "draw", "look_here")),
                 _fit_timeline_track, holds=_DIAGRAM),
    TemplateSpec("cause_chain", "cause", True, "W2", _d((8.0, 25.0)), _c(("reveal", "draw", "look_here")),
                 _fit_cause_chain, holds=_DIAGRAM),
    TemplateSpec("concept_tree", "hierarchy", True, "W2", _d((8.0, 15.0)), _c(("reveal", "look_here"), ("panel",)),
                 _fit_concept_tree, holds=_DIAGRAM),
    TemplateSpec("formula_focus", "formula", True, "R1", _d((10.0, 25.0), (8.0, 16.0)),
                 _c(("reveal", "var", "draw", "look_here"), ("item", "item:var"),
                    {"reveal": ("item",), "var": ("item:var",), "draw": ("item",)}), _fit_formula_focus,
                 implemented=True, holds=("formula",), dense=True, slots=_formula_focus_slots()),
    TemplateSpec("worked_steps", "procedure", True, "R1", _d((15.0, 45.0), (10.0, 30.0)),
                 # the shared panel helper adds the panel's own look_here targets and its `output` beat
                 _c(("reveal", "fill", "countdown", "look_here", "output"),
                    ("item", "item:var", "item:step", "panel", "panel:row"),
                    {"reveal": ("item", "panel"), "fill": ("item",), "countdown": ("item",),
                     "look_here": ("item", "item:var", "item:step", "panel", "panel:row"),
                     "output": ("panel",)}), _fit_worked_steps,
                 implemented=True, holds=("formula", "think"), dense=True, slots=_worked_steps_slots()),
    # a bar on a line (no zoom, §4.3: push_ok stays False); the shared panel helper adds the terminal's own targets
    TemplateSpec("code_walkthrough", "code", True, "R1", _d((10.0, 30.0), (8.0, 20.0)),
                 _c(("reveal", "look_here", "output"), ("item", "item:line", "panel", "panel:row"),
                    {"reveal": ("item",), "look_here": ("item", "item:line", "panel", "panel:row"),
                     "output": ("panel",)}),
                 _fit_code_walkthrough, implemented=True, holds=("code",), dense=True,
                 slots=_code_walkthrough_slots()),
    TemplateSpec("chart_build", "quantity", True, "R1", _d((8.0, 20.0)),
                 _c(("draw", "count", "look_here"), ("panel", "panel:bar", "panel:point")), _fit_chart_build,
                 holds=_DIAGRAM, implemented=True, slots=_chart_build_slots()),
    TemplateSpec("function_plot", "quantity", True, "W2", _d((6.0, 25.0)), _c(("draw", "look_here"),
                                                                              ("panel", "panel:point")),
                 _fit_function_plot, holds=_DIAGRAM),
    TemplateSpec("model_spin", "media", True, "W2", _d((6.0, 25.0)), _c(("look_here",), ("panel",)), _fit_model_spin,
                 holds=_DIAGRAM),
    # the label's item takes a term cue; look_here points at a label or at the animation's frame (`panel`). push_ok
    # stays False: a clip is never scaled (deviation 28), so its look_here keeps the outline form.
    TemplateSpec("animation_stage", "media", True, "R1", _d((8.0, 40.0)),
                 _c(("reveal", "cue_term", "look_here"), ("item", "item:term", "panel"),
                    {"reveal": ("item",), "cue_term": ("item", "item:term"), "look_here": ("item", "panel")}),
                 _fit_animation_stage, implemented=True, forced_for=("simulation",),
                 slots=_animation_stage_slots()),
    # C. Practice and consolidation
    # the belief's card is struck on `draw` and the fact enters on `answer` (myth_fact.js refuteTimes, when events.py
    # writes them; the narration's timing otherwise); the shared panel helper adds the panel's own look_here forms and
    # its `output` beat. No look variant; never a camera push (the outline form)
    TemplateSpec("myth_fact", "refutation", False, "R1", _d((8.0, 14.0), (6.0, 10.0)),
                 _c(("reveal", "draw", "answer", "cue_term", "look_here", "output"),
                    ("item", "item:term", "panel", "panel:row"),
                    {"reveal": ("item",), "draw": ("item",), "answer": ("item",), "cue_term": ("item", "item:term"),
                     "look_here": ("item", "panel", "panel:row"), "output": ("panel",)}),
                 _fit_myth_fact, implemented=True, slots=_myth_fact_slots()),
    # the cue_term reaches a single term of the question (``<item>:term:<n>``) as well as all of them; a reveal and a
    # countdown are the whole item (B2c review CG-5: the forms are per kind). §4.3 gives predict no look variant, and
    # push_ok stays false (R-07: the think ring never moves).
    TemplateSpec("predict", "prompt", False, "R1", _d((6.0, 12.0), (5.0, 9.0)),
                 _c(("reveal", "countdown", "cue_term"), ("item", "item:term"),
                    {"reveal": ("item",), "countdown": ("item",), "cue_term": ("item", "item:term")}),
                 _fit_predict, implemented=True, holds=_THINK, slots=_predict_slots()),
    TemplateSpec("check", "prompt", False, "R1", _d((15.0, 30.0), (10.0, 18.0)),
                 _c(("reveal", "countdown", "answer"), ("item", "option")), _fit_check,
                 variants=_v(("mcq", "free_recall", "true_false", "apply"), ("free_recall", "true_false")),
                 forced_for=("quiz_checkpoint",), holds=_THINK,
                 implemented=True, slots=_check_slots()),
    TemplateSpec("takeaway_stamp", "statement", False, "W2", _d((4.0, 7.0)), _c(("reveal",)), _fit_takeaway_stamp),
    TemplateSpec("quote_card", "statement", False, "W2", _d((5.0, 8.0)), _c(("reveal",)), _fit_quote_card),
    TemplateSpec("checklist", "completion", False, "W2", _d((8.0, 20.0)), _c(("reveal", "draw")), _fit_checklist),
    TemplateSpec("stat_punch", "quantity", False, "W2", _d((4.0, 7.0)), _c(("reveal", "count")), _fit_stat_punch),
    # D. Media, interactive and fallback
    TemplateSpec("clip_scene", "media", True, "B6", _d((0.5, None)), _c(()), _fit_clip_scene, forced_for=("clip",)),
    TemplateSpec("interactive_poster", "media", True, "R1", _R19, _c(()), _fit_interactive_poster,
                 forced_for=("interactive",)),
    TemplateSpec("board_generic", "fallback", False, "R1",
                 _d(SCENE_LENGTH_LONG["landscape"], SCENE_LENGTH_LONG["portrait"]), _c(("reveal", "fill")),
                 _fit_board_generic, implemented=True, holds=HOLD_REASONS, slots=_board_slots()),
)

TEMPLATES: Final[Mapping[str, TemplateSpec]] = MappingProxyType({s.id: s for s in _SPECS})
IMPLEMENTED: Final = tuple(s.id for s in _SPECS if s.implemented)


def template(template_id: str) -> TemplateSpec:
    """The spec of ``template_id`` (``KeyError`` for an unknown id)."""
    return TEMPLATES[template_id]


def registry_problems() -> list[str]:
    """Inconsistencies between the registry, the class table and the signal rows (empty when sound; pinned by a test,
    so a new template cannot land half-declared)."""
    problems: list[str] = []
    members = {t: c for c, row in SEMANTIC_CLASSES.items() for t in row.templates}
    if len(members) != sum(len(r.templates) for r in SEMANTIC_CLASSES.values()):
        problems.append("a template is in two classes")
    for tid, spec in TEMPLATES.items():
        if members.get(tid) != spec.semantic_class:
            problems.append(f"{tid}: class {spec.semantic_class} but §4.2 lists it under {members.get(tid)}")
        if set(spec.variants) != set(spec.formats) or set(spec.duration) != set(spec.formats):
            problems.append(f"{tid}: variants / duration not keyed by its formats")
        if not set(spec.consumes.kinds) <= set(EVENT_KINDS) or not set(spec.consumes.targets) <= set(TARGET_FORMS):
            problems.append(f"{tid}: unknown event kind or target form in consumes")
        for kind, forms in spec.consumes.narrow:
            if kind not in spec.consumes.kinds or not forms or not set(forms) <= set(spec.consumes.targets):
                problems.append(f"{tid}: consumes narrows {kind!r} to forms outside its kinds or targets")
        for slot, per_format in spec.slots.items():
            if not set(per_format) <= set(spec.formats):
                problems.append(f"{tid}: slot {slot} for a format it does not render")
    for tid in members:
        if tid not in TEMPLATES:
            problems.append(f"§4.2 lists {tid}, which has no spec")
    return problems


# --- stage registries: backgrounds (§4.5) and transitions (§4.9) ------------------------------------------------------


@dataclass(frozen=True)
class BackgroundSpec:
    """A background preset (§4.5): ``personalities`` it fits (every one for "any"), ``block`` (a solid accent block for
    hooks and bumpers only), ``mirrored`` (themes.py mirrors its colours: it has a forced-scrim row)."""

    id: str
    release: Literal["R1", "W2"]
    personalities: tuple[str, ...]
    implemented: bool = False
    block: bool = False
    mirrored: bool = False


_ALL_PERSONALITIES: Final = tuple(PERSONALITIES)  # "any" (§4.5 table)

BACKGROUNDS: Final[Mapping[str, BackgroundSpec]] = MappingProxyType({b.id: b for b in (
    BackgroundSpec("mesh_drift", "R1", ("soft", "cinematic"), implemented=True, mirrored=True),
    BackgroundSpec("dot_field", "R1", ("crisp",)),
    BackgroundSpec("paper_grain", "R1", ("editorial",)),
    BackgroundSpec("soft_waves", "R1", ("soft",)),
    BackgroundSpec("spotlight", "R1", _ALL_PERSONALITIES),
    BackgroundSpec("colour_block", "R1", _ALL_PERSONALITIES, implemented=True, block=True, mirrored=True),
    BackgroundSpec("aurora_band", "W2", ("cinematic",)),
    BackgroundSpec("blueprint_grid", "W2", ("crisp",)),
    BackgroundSpec("chalk_slate", "W2", ("chalk",)),
    BackgroundSpec("geo_shapes", "W2", ("bouncy_soft",)),
    BackgroundSpec("bokeh_field", "W2", ("cinematic",)),
    BackgroundSpec("shader_mesh", "W2", ("cinematic",)),
    BackgroundSpec("shader_grain", "W2", ("cinematic",)),
    BackgroundSpec("shader_warp", "W2", ("cinematic",)),
    BackgroundSpec("shader_waves", "W2", ("cinematic",)),
)})


@dataclass(frozen=True)
class TransitionSpec:
    """A scene transition (§4.9): its duration range in seconds (cut: 0) and release; ``implemented`` where the stage
    draws it (web/js/dynamic/transitions.js TRANSITIONS; any other kind is drawn as a cut until its release)."""

    id: str
    release: Literal["R1", "W2"]
    seconds: tuple[float, float]
    implemented: bool = False


TRANSITIONS: Final[Mapping[str, TransitionSpec]] = MappingProxyType({t.id: t for t in (
    TransitionSpec("cut", "R1", (0.0, 0.0), implemented=True),
    TransitionSpec("crossfade", "R1", (0.3, 0.5), implemented=True),  # >= 300 ms (R-54)
    TransitionSpec("push", "R1", (0.45, 0.55)),
    TransitionSpec("colour_wipe", "R1", (0.45, 0.6)),  # only into chapter_bumper (plan §2 R-54)
    TransitionSpec("morph", "R1", (0.6, 0.6)),  # shared-element morph
    TransitionSpec("iris", "W2", (0.5, 0.5)),
    TransitionSpec("zoom_through", "W2", (0.6, 0.8)),
    TransitionSpec("whip", "W2", (0.35, 0.35)),
)})
