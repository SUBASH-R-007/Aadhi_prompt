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
:data:`FIT_STRETCHED` when the data exceeds a text limit §4.3 states (words per item, code lines): the page measures
the pixels (plan §1.11), so such a scene still renders, but is never a post-pass swap target (``fit >= 0.6``, §4.4).
``board_generic`` is :data:`FIT_PREFERRED` for a board scene no other row claims (the §4.4 fallback row) and
:data:`FIT_FALLBACK` for any other scene it can draw (B1 draws every non-clip scene type with it). Count limits that
change a layout's structure (2-4 key terms, 3-6 flow steps, <= 4 versus rows) give 0, except for a forced template on
its forced scene type, which never returns 0 there (direct.py has no other choice): beyond its limits it is stretched.
Positional rows (the last scene's ``outro_next``; ``takeaway_stamp`` at a chapter end) take ``last`` /
``chapter_end`` from the caller.
"""

from __future__ import annotations

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
    ``prefer_lines`` (fewer lines preferred while they fit at >= min), and whether the text is ``critical`` (safe zones
    and contrast are checked, §7.12)."""

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

    def __post_init__(self) -> None:
        if not 0 < self.size[0] <= self.size[1]:
            raise ValueError(f"bad slot size {self.size!r}")


@dataclass(frozen=True)
class Consumes:
    """The visual-event kinds a template draws and the target forms it resolves (plan §1.7; the stage-driven cues of
    plan §1.5 resolve ``cue_term`` / ``look_here`` targets through the template's ``itemEl`` / ``focusRect``)."""

    kinds: tuple[VisualEventKind, ...] = ()
    targets: tuple[TargetForm, ...] = ()

    def accepts(self, kind: str, target: str | None) -> bool:
        """True when an event of ``kind`` on ``target`` (None: the scene) is drawn by the template."""
        if kind not in self.kinds:
            return False
        if target is None:
            return True
        try:
            return parse_target(target).form in self.targets
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


def _presenter(scene: SceneBase) -> bool:
    return scene.look is not None and scene.look.presenter is not None


def _sequence(scene: SceneBase) -> str | None:
    return scene.sequence if isinstance(scene, BoardScene) else None


def _words(text: str | None) -> int:
    """Words as the R-25 lint counts them: whitespace-delimited, a ``$TeX$`` span one word."""
    return len(_TEX.sub(" x ", text).split()) if text else 0


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
        out.append(Signal("graph", "quantity", "function_plot"))
    if _shape(scene, {"formula": (1, 1)}):
        out.append(Signal("formula", "formula", "formula_focus"))
    if _shape(scene, {"example_step": (1, None)}, {"heading": 1, "formula": 1}):
        out.append(Signal("example", "procedure", "worked_steps"))
    if _shape(scene, {"code": (1, 1)}):
        out.append(Signal("code", "code", "code_walkthrough"))
    if _shape(scene, {"misconception": (1, 1)}):
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


def _fit_aadhi_intro(scene: SceneBase, fmt: Format) -> float:
    # the opening BoardScene with look.presenter (Aadhi's words are linted on the beats: R-12, R-13)
    return FIT_PREFERRED if isinstance(scene, BoardScene) and _presenter(scene) and scene.type != "recap" else FIT_NONE


def _fit_aadhi_recap(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, BoardScene) and _presenter(scene) and scene.type == "recap" else FIT_NONE


def _fit_hook_question(scene: SceneBase, fmt: Format) -> float:
    # one question item (<= 12 words; <= 8 in 9:16), and the opening scene's title heading
    if not _shape(scene, {"question": (1, 1)}):
        return FIT_NONE
    return _ok(_words(_of(scene, "question")[0].text) <= (12 if fmt == "landscape" else 8))


def _fit_key_terms(scene: SceneBase, fmt: Format) -> float:
    # 2-4 definition items: term <= 3 words, meaning <= 8 words
    if not _shape(scene, {"definition": (2, 4)}):
        return FIT_NONE
    return _ok(all(_words(d.term) <= 3 and _words(d.text) <= 8 for d in _of(scene, "definition")))


def _fit_chapter_bumper(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, ChapterCardScene) else FIT_NONE


def _fit_recap_retrieval(scene: SceneBase, fmt: Format) -> float:
    # a recap BoardScene: question items (2 in 16:9, 1 in 9:16) and a free-recall prompt, then 3 takeaway tiles
    if not (isinstance(scene, BoardScene) and scene.type == "recap" and _of(scene, "question")):
        return FIT_NONE
    counts = _kinds(scene)
    if not set(counts) <= {"question", "takeaway", "paragraph", "heading"}:
        return FIT_NONE
    return _ok(counts.get("takeaway", 0) <= 3)


def _fit_outro_next(scene: SceneBase, fmt: Format) -> float:
    # closure, no new content (R-11): at most one takeaway, paragraph or heading
    if not isinstance(scene, BoardScene):
        return FIT_NONE
    items = _items(scene)
    return FIT_PREFERRED if len(items) <= 1 and all(
        str(getattr(i.kind, "value", i.kind)) in ("takeaway", "paragraph", "heading") for i in items) else FIT_NONE


def _fit_definition_card(scene: SceneBase, fmt: Format) -> float:
    # a definition item plus an optional bullet example
    return FIT_PREFERRED if _shape(scene, {"definition": (1, 1)}, {"bullet": 1}) else FIT_NONE


def _fit_kinetic_statement(scene: SceneBase, fmt: Format) -> float:
    # one takeaway, paragraph, heading or callout_* item of <= 12 words (<= 8 in 9:16); a quote as the quote row's
    # alternative
    items = _items(scene)
    if len(items) != 1 or str(getattr(items[0].kind, "value", items[0].kind)) not in STATEMENT_KINDS | {"quote"}:
        return FIT_NONE
    return _ok(_words(items[0].text) <= (12 if fmt == "landscape" else 8))


def _fit_list_build(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _shape(scene, {"bullet": (2, None)}) else FIT_NONE


def _fit_media_split(scene: SceneBase, fmt: Format) -> float:
    # <= 3 text items and a figure, image, chart, graph or terminal panel
    ok = _media_board(scene) and _panel(scene) in ("figure", "image", "chart", "graph", "terminal")
    return FIT_PREFERRED if ok else FIT_NONE


def _fit_full_bleed_media(scene: SceneBase, fmt: Format) -> float:
    # forced for ai_video; on a board, an application image (an image panel) under a lower-third label (one text item)
    if isinstance(scene, AIVideoScene):
        return FIT_PREFERRED
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
    # example_step items (blank steps plus fill beats) and an optional formula
    return FIT_PREFERRED if _shape(scene, {"example_step": (1, None)}, {"heading": 1, "formula": 1}) else FIT_NONE


# §4.3 code_walkthrough with deviation 13 (R-29 wins: 36 px in 16:9): <= 17 lines of <= 60 chars; 9:16 <= 10 lines of
# <= 28 chars (else lint portrait.code_too_wide).
CODE_LIMITS: Final[Mapping[Format, tuple[int, int]]] = MappingProxyType({"landscape": (17, 60), "portrait": (10, 28)})


def _fit_code_walkthrough(scene: SceneBase, fmt: Format) -> float:
    if not _shape(scene, {"code": (1, 1)}):
        return FIT_NONE
    lines = (_of(scene, "code")[0].code or "").splitlines()
    max_lines, max_chars = CODE_LIMITS[fmt]
    return _ok(len(lines) <= max_lines and all(len(line) <= max_chars for line in lines))


def _panel_only(scene: SceneBase, kind: str) -> float:
    # the panel is the content: at most a heading beside it
    return FIT_PREFERRED if _panel(scene) == kind and _shape(scene, {}) else FIT_NONE


def _fit_chart_build(scene: SceneBase, fmt: Format) -> float:
    return _panel_only(scene, "chart")


def _fit_function_plot(scene: SceneBase, fmt: Format) -> float:
    return _panel_only(scene, "graph")


def _fit_model_spin(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _media_board(scene) and _panel(scene) == "model_3d" else FIT_NONE


def _fit_animation_stage(scene: SceneBase, fmt: Format) -> float:
    # forced for simulation scenes; a manim panel beside <= 3 text items on a board
    if isinstance(scene, SimulationScene):
        return FIT_PREFERRED
    return FIT_PREFERRED if _media_board(scene) and _panel(scene) == "manim" else FIT_NONE


def _fit_interactive_poster(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, InteractiveScene) else FIT_NONE


def _fit_clip_scene(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if isinstance(scene, ClipScene) else FIT_NONE


def _fit_myth_fact(scene: SceneBase, fmt: Format) -> float:
    # a misconception item with its correction (justification): the fact card is the final frame (R-63)
    if not _shape(scene, {"misconception": (1, 1)}):
        return FIT_NONE
    return FIT_PREFERRED if (_of(scene, "misconception")[0].justification or "").strip() else FIT_NONE


def _blank_question(scene: SceneBase) -> bool:
    return _shape(scene, {"question": (1, 1)}) and _of(scene, "question")[0].blank


def _fit_predict(scene: SceneBase, fmt: Format) -> float:
    return FIT_PREFERRED if _blank_question(scene) else FIT_NONE


# check: mcq <= 4 options in 16:9 only; reels use free recall or true / false (2 options).
CHECK_MAX_OPTIONS: Final[Mapping[Format, int]] = MappingProxyType({"landscape": 4, "portrait": 2})


def _fit_check(scene: SceneBase, fmt: Format) -> float:
    if isinstance(scene, QuizScene):  # forced: never 0 on a quiz
        return _ok(len(scene.options) <= CHECK_MAX_OPTIONS[fmt])
    return FIT_PREFERRED if _blank_question(scene) else FIT_NONE


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
            critical=False)}  # the countdown's number (R-07: the think time is the ring itself)
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
# headline region (696 px), the phrase column below the TikTok rail (x 120-780, 660 px), the stage (960 px) less
# 2 x 28 px padding. Heading / takeaway items are drawn at 700 in 16:9 (the slot's weight is the 400 of the others).
def _media_split_slots() -> SlotTable:
    variants = ("media_left", "media_right")
    title_l = SlotSpec(w=742, max_lines=3, size=(64, 88), role="title", face="display", weight=700, line_height=1.08,
                       tracking_em=-0.015)
    body_l = SlotSpec(w=742 - 36, max_lines=None, size=(44, 52), role="body", face="body", weight=400, line_height=1.3)
    term_l = SlotSpec(w=938 - 2 * 32, max_lines=None, size=(36, 44), role="body", face="mono", weight=400,
                      line_height=1.35)
    title_p = SlotSpec(w=696, max_lines=2, size=(56, 88), role="title", face="display", weight=700, line_height=1.06,
                       tracking_em=-0.015, prefer_lines=1)
    body_p = SlotSpec(w=660, max_lines=2, size=(48, 64), role="body", face="body", weight=700, line_height=1.2)
    term_p = SlotSpec(w=960 - 2 * 28, max_lines=None, size=(40, 44), role="body", face="mono", weight=700,
                      line_height=1.35)
    out = {slot: MappingProxyType({"landscape": MappingProxyType({v: land for v in variants}),
                                   "portrait": MappingProxyType({"default": port})})
           for slot, land, port in (("title", title_l, title_p), ("body", body_l, body_p),
                                    ("terminal", term_l, term_p))}
    return MappingProxyType(out)


# --- list_build's slots (B2c): web/js/dynamic/templates/list_build.js LAYOUT, PAD, boxes() and slotRows();
# list_build.test.js pins the catalogue's rows to slotRows. The 16:9 "default" variant is the split layout (the
# narrower boxes: a size fitted there is an upper bound for the centred one too).
LIST_PAD: Final = 12  # cues.js FOCUS_OUTSET + FOCUS_PX: the look_here outline around a row's text lies on the row's edge
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


# --- the registry (§4.3) -------------------------------------------------------------------------------------------

# R-19 scene length where §4.3 gives no timing; board_generic, the fallback for every scene kind (worked examples and
# quizzes included), takes R-19's widest bounds (<= 45 s in 16:9, <= 30 s in 9:16).
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


def _c(kinds: Iterable[VisualEventKind], targets: Iterable[TargetForm] | None = None) -> Consumes:
    """``Consumes`` of ``kinds`` on ``targets`` (default: whole board items; none when there are no kinds)."""
    k = tuple(kinds)
    return Consumes(k, tuple(targets) if targets is not None else (("item",) if k else ()))


_R19 = _d(SCENE_LENGTH["landscape"], SCENE_LENGTH["portrait"])
# Which templates may carry hold_reason "diagram" (the specs below that name _DIAGRAM): A2 proposal, pending owner
# review; not in the design (R-16 lists the reasons and §4.2 gives every template a ``holds`` field, but names no
# template's)
_DIAGRAM: Final[tuple[HoldReason, ...]] = ("diagram",)
_THINK: Final[tuple[HoldReason, ...]] = ("think",)

_SPECS: Final[tuple[TemplateSpec, ...]] = (
    # A. Bookends and structure
    TemplateSpec("aadhi_intro", "structure", False, "R1", _d((5.0, 8.0)), _c(("reveal", "cue_term", "exit")),
                 _fit_aadhi_intro, variants=_v(("fullframe", "card_left", "pip", "split"), ("card",))),
    TemplateSpec("aadhi_recap", "structure", False, "R1", _d((5.0, 8.0)), _c(("reveal", "exit")), _fit_aadhi_recap),
    TemplateSpec("hook_question", "prompt", False, "R1", _d((4.0, 10.0), (3.0, 6.0)), _c(("reveal", "cue_term")),
                 _fit_hook_question, variants=_v(("question", "true_false", "stakes", "mystery", "challenge"))),
    TemplateSpec("roadmap", "structure", False, "W2", _R19, _c(("reveal",)), _never),
    # §4.3 times key terms in 16:9 only ("3–4 s per term (16:9), optional in 9:16"): "optional" is about using the
    # template in 9:16, not its length, so a 9:16 key-terms scene keeps the same per-term range (A2 reading, pending
    # owner review)
    TemplateSpec("key_terms", "definition", False, "R1", _d((3.0, 4.0), per_item=True),
                 _c(("reveal", "draw", "cue_term")), _fit_key_terms),
    TemplateSpec("chapter_bumper", "structure", False, "R1", _d((2.0, 3.0), (1.2, 1.8)), _c(()), _fit_chapter_bumper,
                 forced_for=("chapter_card",)),
    TemplateSpec("recap_retrieval", "prompt", False, "R1", _d((25.0, 40.0), (14.0, 20.0)),
                 _c(("reveal", "fill", "countdown", "answer", "cue_term")), _fit_recap_retrieval, holds=_THINK),
    # §4.3 "6–10 s or 15 s (16:9)", R-11 "or 15 s with end-screen space": two lengths, not 6-15 s; 9:16 has no
    # end-screen space (§2.5)
    TemplateSpec("outro_next", "structure", False, "R1",
                 MappingProxyType({"landscape": Duration(6.0, 10.0, end_screen=zn.END_SCREEN.seconds),
                                   "portrait": Duration(3.0, 5.0)}), _c(("reveal",)), _fit_outro_next),
    TemplateSpec("thumbnail", "structure", True, "R1", MappingProxyType({"landscape": Duration(0.0, 0.0)}), _c(()),
                 _never, formats=("landscape",), variants=MappingProxyType({"landscape": ()})),
    # B. Explanation
    TemplateSpec("definition_card", "definition", False, "R1", _d((6.0, 12.0), (4.0, 8.0)),
                 _c(("reveal", "cue_term")), _fit_definition_card,
                 variants=_v(("card_icon_right", "card_image_left", "centred"))),
    TemplateSpec("kinetic_statement", "statement", False, "R1", _d((3.0, 7.0), (2.0, 5.0)),
                 _c(("reveal", "cue_term")), _fit_kinetic_statement),
    TemplateSpec("list_build", "enumeration", False, "R1", _d((3.0, 5.0), per_item=True),
                 _c(("reveal", "cue_term", "look_here"), ("item", "item:term")), _fit_list_build, implemented=True,
                 slots=_list_build_slots()),
    # variant names "media_left" / "media_right": A2 proposal, pending owner review; not in the design (§4.3 / §4.4
    # say only that the media side changes at chapter boundaries); 9:16 stacks, so it has none
    TemplateSpec("media_split", "media", True, "R1", _d((8.0, 20.0), (5.0, 12.0)),
                 _c(("reveal", "cue_term", "look_here", "output"), ("item", "item:term", "panel", "panel:row")),
                 _fit_media_split, implemented=True, variants=_v(("media_left", "media_right"), ()), holds=_DIAGRAM,
                 push_ok=True, slots=_media_split_slots()),
    TemplateSpec("full_bleed_media", "media", True, "R1", _d((3.0, 12.0)), _c(("reveal",)), _fit_full_bleed_media,
                 forced_for=("ai_video",)),
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
                 _c(("reveal", "var", "draw", "look_here"), ("item", "item:var")), _fit_formula_focus,
                 implemented=True, holds=("formula",), dense=True, slots=_formula_focus_slots()),
    TemplateSpec("worked_steps", "procedure", True, "R1", _d((15.0, 45.0), (10.0, 30.0)),
                 _c(("reveal", "fill", "countdown", "look_here"), ("item", "item:var")), _fit_worked_steps,
                 holds=("formula", "think"), dense=True),
    TemplateSpec("code_walkthrough", "code", True, "R1", _d((10.0, 30.0), (8.0, 20.0)),
                 _c(("reveal", "look_here", "output"), ("item", "item:line", "panel")), _fit_code_walkthrough,
                 holds=("code",), dense=True),
    TemplateSpec("chart_build", "quantity", True, "R1", _d((8.0, 20.0)),
                 _c(("draw", "count", "look_here"), ("panel", "panel:bar", "panel:point")), _fit_chart_build,
                 holds=_DIAGRAM),
    TemplateSpec("function_plot", "quantity", True, "W2", _d((6.0, 25.0)), _c(("draw", "look_here"),
                                                                              ("panel", "panel:point")),
                 _fit_function_plot, holds=_DIAGRAM),
    TemplateSpec("model_spin", "media", True, "W2", _d((6.0, 25.0)), _c(("look_here",), ("panel",)), _fit_model_spin,
                 holds=_DIAGRAM),
    TemplateSpec("animation_stage", "media", True, "R1", _d((8.0, 40.0)), _c(()), _fit_animation_stage,
                 forced_for=("simulation",)),
    # C. Practice and consolidation
    TemplateSpec("myth_fact", "refutation", False, "R1", _d((8.0, 14.0), (6.0, 10.0)),
                 _c(("reveal", "draw", "answer")), _fit_myth_fact),
    TemplateSpec("predict", "prompt", False, "R1", _d((6.0, 12.0), (5.0, 9.0)), _c(("reveal", "countdown")),
                 _fit_predict, holds=_THINK),
    TemplateSpec("check", "prompt", False, "R1", _d((15.0, 30.0), (10.0, 18.0)),
                 _c(("reveal", "countdown", "answer"), ("item", "option")), _fit_check,
                 variants=_v(("mcq", "free_recall", "true_false", "apply"), ("free_recall", "true_false")),
                 forced_for=("quiz_checkpoint",), holds=_THINK),
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
