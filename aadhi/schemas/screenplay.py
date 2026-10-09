"""Canonical screenplay schema (schema_version 2).

This is the central contract of the system. Every stage reads or writes it:

    ingest -> plan -> script -> validate/critic/repair -> companion -> assets -> timeline -> player/render

Design rules
------------
* No raw HTML anywhere. Board content is a list of typed ``BoardItem`` objects whose
  text uses a tiny "rich-lite" inline markup (see ``RICH_LITE``). The frontend renders
  them with ``textContent``-based DOM builders, so model output can never inject markup.
* Narration is split into ``Beat`` objects. A beat may reveal one board item, fill one
  previously revealed blank worked-example step, and re-highlight up to two earlier items.
  Board/narration alignment is therefore structural: nothing to count, nothing to drift.
* The screenplay holds *intent only*. Generated asset keys live in the AssetManifest
  (``aadhi.schemas.manifest``); the only asset keys here are explicit user overrides
  (``override_asset_key``) and extracted source figures.
* This model is NOT an LLM response schema (it uses discriminated unions and tuples).
  LLM-facing generation models live in ``aadhi.pipeline.gen_models`` and are canonicalised
  into these classes; the model never authors scene/beat/board ids.
* Structural validity is enforced here (``ValidationError``). Quality checks (lengths,
  pacing, pedagogy) live in ``aadhi.pipeline.validate`` and produce lint issues instead.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import deque
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .jsonsafe import check_storable

SCHEMA_VERSION = 2

# Inline markup accepted inside BoardItem.text, table cells, captions etc. Tokenised by
# web/js/player/richtext.js (never parsed as HTML); stripped to plain text for captions/TTS.
RICH_LITE = {
    "bold": "**text**",
    "italic": "*text*",
    "code": "`code`",
    "math": "$latex$ (inline only; block math uses BoardItem.kind == 'formula')",
    "keyword": "[[term]]  (glowing keyword highlight)",
    "escape": r"\$ for a literal dollar sign, \* for a literal asterisk",
}

# Translatable vs protected fields (used by aadhi.pipeline.translate).
TRANSLATABLE_FIELDS = (
    "title", "subtitle", "chapter_label", "beats[].narration", "beats[].spoken", "reveal_beats[].narration",
    "board[].text", "board[].term", "board[].caption", "board[].justification", "board[].headers",
    "board[].rows", "question", "options", "feedback_wrong", "explanation", "side_panel.title",
    "side_panel.chart.labels", "side_panel.chart.datasets[].label", "side_panel.chart.x_label",
    "side_panel.chart.y_label", "side_panel.quiz.*", "side_panel.terminal.output",
    "learning_objectives[].text", "misconceptions[].statement", "misconceptions[].correction",
    "concept_map[].title", "concept_map[].summary", "companion_sheet.*", "lexicon[].spoken",
)
PROTECTED_FIELDS = ("*.id", "*_id", "*_ids", "latex", "code", "language", "expr", "figure_id", "manim", "p5_code")

# Highest "new AI version" number of a generated visual (SidePanel.variant / AIVideoScene.variant).
MAX_VISUAL_VARIANT = 99

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")


def slugify(value: str, fallback: str = "item") -> str:
    """Normalise ids to ``[a-z0-9_-]`` slugs, deterministically and without collisions.

    Non-ASCII input (Tamil, Devanagari ...) is transliterated where Unicode allows; if
    characters were lost the result gets a short hash suffix so distinct inputs never
    collapse onto the same id.
    """
    raw = (value or "").strip()
    folded = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii").lower()
    v = re.sub(r"[^a-z0-9_\-]+", "-", folded).strip("-_")
    if not raw.isascii():  # transliteration is lossy: disambiguate with a stable hash
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
        v = f"{v[:48]}-{digest}" if v else f"{fallback}-{digest}"
    elif not v:
        v = fallback
    if not v[0].isalnum():
        v = f"{fallback}-{v}"
    return v[:64]


def _slug_validator(v: Any) -> Any:
    if isinstance(v, str):
        s = slugify(v)
        if not _SLUG_RE.match(s):
            raise ValueError(f"invalid id {v!r}")
        return s
    return v


def _slug_list(v: Any) -> Any:
    if isinstance(v, list):
        return [slugify(x) for x in v if isinstance(x, str) and x.strip()]
    return v


def _opt_slug(v: Any) -> Any:
    if v in ("", None):
        return None
    return slugify(v) if isinstance(v, str) else v


Slug = Annotated[str, Field(min_length=1, max_length=64)]

# TeX macros that could produce links/attributes/unicode tricks in MathJax output. MathJax is
# also configured to refuse them (web/js/shared/libs.js); this is defence in depth.
_FORBIDDEN_TEX = re.compile(
    r"\\(href|url|style|class|cssId|require|data|html|unicode|mmlToken|bbox|special|input|include|def|let|newcommand|renewcommand)\b"
)


def _check_latex(v: str | None) -> str | None:
    if v and _FORBIDDEN_TEX.search(v):
        raise ValueError("latex uses a forbidden macro")
    return v


# math.js expression allow-list for graph functions (evaluated client-side with math.js, never JS eval).
_EXPR_FUNCS = {
    "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "exp", "log", "log10", "log2",
    "sqrt", "cbrt", "abs", "sign", "floor", "ceil", "round", "min", "max", "pow", "mod", "pi", "e", "x",
}
_EXPR_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)|([A-Za-z_][A-Za-z0-9_]*)|(\*\*|[-+*/^(),.]))", re.ASCII)


def _check_expr(expr: str) -> str:
    pos = 0
    while pos < len(expr):
        m = _EXPR_TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            if expr[pos:].strip() == "":
                break
            raise ValueError(f"unsupported character in expression at {pos}: {expr[pos:pos + 10]!r}")
        if m.group(2) and m.group(2) not in _EXPR_FUNCS:
            raise ValueError(f"unknown identifier {m.group(2)!r} in expression (use x and math functions)")
        pos = m.end()
    return expr.replace("**", "^")


class StrictModel(BaseModel):
    """Base for all schema models: ignore unknown keys, strip strings, finite numbers only.

    ``allow_inf_nan=False``: ``NaN``/``Infinity`` (and ``1e999``, which Python's ``json`` reads as
    ``inf``) can be neither served (Starlette renders JSON with ``allow_nan=False``) nor stored in
    PostgreSQL JSONB, so every float field refuses them (``aadhi.schemas.jsonsafe``).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True, str_strip_whitespace=True, allow_inf_nan=False)


# ---------------------------------------------------------------------------
# Concept map, objectives, misconceptions, lexicon, chapters
# ---------------------------------------------------------------------------


class ConceptNode(StrictModel):
    id: Slug
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(default="", max_length=600)
    depends_on: list[Slug] = Field(default_factory=list, max_length=20)
    kind: Literal["prerequisite", "core"] = "core"

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_deps = field_validator("depends_on", mode="before")(_slug_list)


Bloom = Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]


class LearningObjective(StrictModel):
    id: Slug
    text: str = Field(min_length=1, max_length=400)
    bloom: Bloom = "understand"
    concept_ids: list[Slug] = Field(default_factory=list, max_length=10)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_ids", mode="before")(_slug_list)


class Misconception(StrictModel):
    id: Slug
    concept_id: Slug | None = None
    statement: str = Field(min_length=1, max_length=600)  # what students wrongly believe
    correction: str = Field(min_length=1, max_length=800)  # what is actually true, and why

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_id", mode="before")(_opt_slug)


class LexiconEntry(StrictModel):
    """Pronunciation rule applied to TTS input only (captions keep the written form)."""

    written: str = Field(min_length=1, max_length=60)
    spoken: str = Field(min_length=1, max_length=120)
    language: str | None = Field(default=None, max_length=16)  # None = all languages
    keep_in_english: bool = False  # translation must not translate this term


class ChapterRef(StrictModel):
    id: Slug
    title: str = Field(min_length=1, max_length=160)
    scene_ids: list[Slug] = Field(default_factory=list, max_length=200)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_s = field_validator("scene_ids", mode="before")(_slug_list)


# ---------------------------------------------------------------------------
# Board items (the visible "blackboard" of a scene)
# ---------------------------------------------------------------------------


class BoardItemKind(str, Enum):
    heading = "heading"  # short section heading inside the board
    bullet = "bullet"  # one concise point
    paragraph = "paragraph"  # one short sentence (prefer bullets)
    definition = "definition"  # ``term`` + ``text``
    formula = "formula"  # block LaTeX in ``latex``; optional ``text`` = what it means; ``variables``
    callout_info = "callout_info"
    callout_tip = "callout_tip"
    callout_warning = "callout_warning"
    misconception = "misconception"  # ``text`` = the misconception; ``justification`` = correction
    code = "code"  # ``language`` + ``code``
    table = "table"  # ``headers`` + ``rows`` (rich-lite cells)
    figure = "figure"  # ``figure_id`` -> Screenplay.figures; ``caption``
    example_step = "example_step"  # worked-example step; ``justification`` = why; ``blank`` = faded
    takeaway = "takeaway"  # key-takeaway line


class FormulaVariable(StrictModel):
    symbol_latex: str = Field(min_length=1, max_length=60)
    meaning: str = Field(min_length=1, max_length=160)
    unit: str = Field(default="", max_length=40)
    # Optional: the beat that explains this symbol (BoardScene formula legends). The row appears when
    # that beat names the meaning, else when it starts; None = when the narration first names it, else
    # with the formula (aadhi.compose.sync). Left out of the serialised model when None, so scene
    # hashes and stored screenplays are unchanged; an unknown or too-early beat is ignored (lint).
    beat_id: Slug | None = Field(default=None, exclude_if=lambda v: v is None)

    _chk = field_validator("symbol_latex")(_check_latex)
    _norm_beat = field_validator("beat_id", mode="before")(_opt_slug)


class BoardItem(StrictModel):
    id: Slug
    kind: BoardItemKind
    text: str = Field(default="", max_length=1200)
    term: str | None = Field(default=None, max_length=240)  # definition
    latex: str | None = Field(default=None, max_length=1200)  # formula
    variables: list[FormulaVariable] = Field(default_factory=list, max_length=8)  # formula legend
    language: str | None = Field(default=None, pattern=r"^[a-z0-9+#-]{1,20}$")  # code
    code: str | None = Field(default=None, max_length=4000)  # code
    headers: list[str] | None = Field(default=None, max_length=8)  # table
    rows: list[list[str]] | None = Field(default=None, max_length=20)  # table
    figure_id: Slug | None = None  # figure
    caption: str | None = Field(default=None, max_length=600)  # figure
    justification: str | None = Field(default=None, max_length=800)  # example_step / misconception
    blank: bool = False  # example_step shown as "______" until a beat fills it (Beat.fill_item_id)
    misconception_id: Slug | None = None  # kind == misconception -> Screenplay.misconceptions
    source_refs: list[str] = Field(default_factory=list, max_length=10)  # IngestResult chunk ids

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_fig = field_validator("figure_id", mode="before")(_opt_slug)
    _norm_mis = field_validator("misconception_id", mode="before")(_opt_slug)
    _chk_latex = field_validator("latex")(_check_latex)

    @model_validator(mode="after")
    def _kind_fields(self) -> "BoardItem":
        k = self.kind
        if k == BoardItemKind.formula and not (self.latex or "").strip():
            raise ValueError("formula board item requires 'latex'")
        if k == BoardItemKind.code and not (self.code or "").strip():
            raise ValueError("code board item requires 'code'")
        if k == BoardItemKind.table:
            if not self.headers or not self.rows:
                raise ValueError("table board item requires 'headers' and 'rows'")
            width = len(self.headers)
            if any(len(r) != width for r in self.rows):
                raise ValueError("table rows must have the same length as headers")
        if k == BoardItemKind.figure and not self.figure_id:
            raise ValueError("figure board item requires 'figure_id'")
        if k == BoardItemKind.definition and not (self.term or self.text):
            raise ValueError("definition board item requires 'term' and/or 'text'")
        if self.blank and k != BoardItemKind.example_step:
            raise ValueError("only example_step items can be blank")
        needs_text = {
            BoardItemKind.heading, BoardItemKind.bullet, BoardItemKind.paragraph, BoardItemKind.callout_info,
            BoardItemKind.callout_tip, BoardItemKind.callout_warning, BoardItemKind.misconception,
            BoardItemKind.example_step, BoardItemKind.takeaway,
        }
        if k in needs_text and not self.text.strip():
            raise ValueError(f"{k.value} board item requires 'text'")
        return self


# ---------------------------------------------------------------------------
# Beats (narration units)
# ---------------------------------------------------------------------------


class Beat(StrictModel):
    id: Slug
    # Plain spoken text, also used for captions. No markup, no LaTeX; formulas as words.
    narration: str = Field(min_length=1, max_length=1500)
    # Optional TTS-only override (pronunciation); captions still use ``narration``.
    spoken: str | None = Field(default=None, max_length=2000)
    # Board item revealed when this beat starts (BoardScene only; each item revealed at most once).
    board_item_id: Slug | None = None
    # Blank example_step (already revealed) that this beat fills in (faded worked examples).
    fill_item_id: Slug | None = None
    # Already-visible items re-emphasised during this beat (signalling), max 2.
    highlight_item_ids: list[Slug] = Field(default_factory=list, max_length=2)
    # Deliberate silence after this beat (seconds) for thinking / reading.
    pause_after: float = Field(default=0.0, ge=0.0, le=8.0)
    # Simulation scenes: what the animation does during this beat (one animation step per beat).
    visual_cue: str | None = Field(default=None, max_length=600)
    # IngestResult chunk ids supporting the claims of this beat (grounding checks).
    source_refs: list[str] = Field(default_factory=list, max_length=10)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_ref = field_validator("board_item_id", "fill_item_id", mode="before")(_opt_slug)
    _norm_hl = field_validator("highlight_item_ids", mode="before")(_slug_list)


# ---------------------------------------------------------------------------
# Side panels
# ---------------------------------------------------------------------------


class ChartDataset(StrictModel):
    label: str = Field(default="", max_length=120)
    data: list[float] = Field(min_length=1, max_length=64)


class ChartSpec(StrictModel):
    chart_type: Literal["bar", "line", "pie", "doughnut", "radar"] = "bar"
    labels: list[str] = Field(min_length=1, max_length=64)
    datasets: list[ChartDataset] = Field(min_length=1, max_length=6)
    x_label: str | None = Field(default=None, max_length=120)
    y_label: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _lengths(self) -> "ChartSpec":
        for ds in self.datasets:
            if len(ds.data) != len(self.labels):
                raise ValueError("each dataset must have one value per label")
        return self


class GraphFunction(StrictModel):
    # math.js syntax in the variable x, e.g. "x^2 + 2*x", "sin(x)", "exp(-x/2)". Never JS-eval'd.
    expr: str = Field(min_length=1, max_length=200)
    label: str | None = Field(default=None, max_length=80)

    _chk = field_validator("expr")(_check_expr)


class GraphPoint(StrictModel):
    x: float
    y: float
    label: str | None = Field(default=None, max_length=60)


class GraphSpec(StrictModel):
    functions: list[GraphFunction] = Field(default_factory=list, max_length=4)
    points: list[GraphPoint] = Field(default_factory=list, max_length=20)
    x_range: tuple[float, float] = (-5.0, 5.0)
    y_range: tuple[float, float] | None = None  # None = auto
    x_label: str | None = Field(default=None, max_length=60)
    y_label: str | None = Field(default=None, max_length=60)

    @model_validator(mode="after")
    def _non_empty(self) -> "GraphSpec":
        if not self.functions and not self.points:
            raise ValueError("graph needs at least one function or point")
        if self.x_range[0] >= self.x_range[1]:
            raise ValueError("x_range must be increasing")
        if self.y_range and self.y_range[0] >= self.y_range[1]:
            raise ValueError("y_range must be increasing")
        return self


class Primitive3D(StrictModel):
    shape: Literal["sphere", "box", "cylinder", "cone", "torus", "arrow"]
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # sphere: [r]; box: [w,h,d]; cylinder/cone: [r,h]; torus: [R,r]; arrow: direction [dx,dy,dz] (length = norm)
    size: list[float] = Field(default_factory=lambda: [1.0], min_length=1, max_length=3)
    color: str = Field(default="#b026ff", pattern=r"^#[0-9a-fA-F]{6}$")
    label: str | None = Field(default=None, max_length=60)


class Model3DSpec(StrictModel):
    primitives: list[Primitive3D] = Field(min_length=1, max_length=40)
    auto_rotate: bool = True  # live player only; MP4 renders show a fixed angle


class ManimSpec(StrictModel):
    """Either a library template with typed params (preferred) or free-form code.

    Free-form code must define exactly one ``class <Name>(AadhiScene)`` and call
    ``self.wait_until_beat(i)`` before the animation step that belongs to beat ``i``.
    ``AadhiScene`` and ``BEAT_TIMES`` are injected by ``aadhi.manim``.
    """

    template: str | None = Field(default=None, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)
    code: str | None = Field(default=None, max_length=20000)

    @field_validator("params")
    @classmethod
    def _storable_params(cls, v: dict[str, Any]) -> dict[str, Any]:
        # free-form values: the schema cannot type them, so check them like stored JSON
        check_storable(v)
        return v

    @model_validator(mode="after")
    def _exclusive(self) -> "ManimSpec":
        if bool(self.template) == bool(self.code):
            raise ValueError("manim spec needs exactly one of 'template' or 'code'")
        if len(repr(self.params)) > 16_000:
            raise ValueError("manim params too large")
        return self


class TerminalSpec(StrictModel):
    command: str = Field(max_length=300)
    output: str = Field(default="", max_length=3000)  # revealed line by line across the scene


class QuizTeaserSpec(StrictModel):
    question: str = Field(max_length=400)
    options: list[str] = Field(min_length=2, max_length=5)
    correct_index: int = Field(ge=0)

    @model_validator(mode="after")
    def _idx(self) -> "QuizTeaserSpec":
        if self.correct_index >= len(self.options):
            raise ValueError("correct_index out of range")
        return self


SidePanelKind = Literal[
    "skill_tree", "figure", "image", "chart", "graph", "model_3d", "manim", "terminal", "quiz", "gif"
]


class SidePanel(StrictModel):
    kind: SidePanelKind
    title: str | None = Field(default=None, max_length=160)
    # Why this visual helps learning (coherence principle). Lint warns when empty (except skill_tree).
    rationale: str = Field(default="", max_length=400)
    # Beat from which the panel is visible (None = scene start).
    show_from_beat_id: Slug | None = None
    figure_id: Slug | None = None  # figure
    image_prompt: str | None = Field(default=None, max_length=800)  # image (generated)
    chart: ChartSpec | None = None
    graph: GraphSpec | None = None
    model_3d: Model3DSpec | None = None
    manim: ManimSpec | None = None
    terminal: TerminalSpec | None = None
    quiz: QuizTeaserSpec | None = None
    gif_query: str | None = Field(default=None, max_length=80)  # live player only (not in MP4)
    # User-uploaded media replacing the generated one (asset key of kind upload/image/video).
    override_asset_key: str | None = Field(default=None, max_length=128)
    # "New AI version" of the generated image (Visual Review): part of the image's content key only when above
    # 0, so 0 keeps every existing key. Left out of the serialised model at 0 (scene hashes and stored
    # screenplays unchanged); never LLM-facing.
    variant: int = Field(default=0, ge=0, le=MAX_VISUAL_VARIANT, exclude_if=lambda v: v == 0)

    _norm_fig = field_validator("figure_id", "show_from_beat_id", mode="before")(_opt_slug)

    @model_validator(mode="after")
    def _payload(self) -> "SidePanel":
        need = {
            "figure": self.figure_id or self.override_asset_key,
            "image": self.image_prompt or self.override_asset_key,
            "chart": self.chart,
            "graph": self.graph,
            "model_3d": self.model_3d,
            "manim": self.manim or self.override_asset_key,
            "terminal": self.terminal,
            "quiz": self.quiz,
            "gif": self.gif_query,
        }
        if self.kind != "skill_tree" and not need.get(self.kind):
            raise ValueError(f"side panel of kind {self.kind!r} is missing its payload")
        return self


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

MascotPosition = Literal["left", "right", "center", "popup_bottom_left", "popup_bottom_right", "hidden"]


NarrativeRoleName = Literal[
    "hook", "context", "prerequisite", "concept", "example", "practice", "check", "application", "synthesis", "transition"
]


class SceneIntent(StrictModel):
    """What the scene is for (copied from the plan; teacher-facing, never shown to students).

    ``narrative_role`` and ``bridge_in`` keep the lecture's flow when a single scene is repaired or
    regenerated later (the plan itself is not stored with the screenplay)."""

    goal: str = Field(default="", max_length=600)
    key_points: list[str] = Field(default_factory=list, max_length=12)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    narrative_role: NarrativeRoleName | None = None
    bridge_in: str = Field(default="", max_length=300)


class SceneBase(StrictModel):
    id: Slug
    concept_id: Slug | None = None
    chapter_id: Slug | None = None
    title: str = Field(default="", max_length=240)
    subtitle: str | None = Field(default=None, max_length=320)
    mascot_position: MascotPosition = "left"
    beats: list[Beat] = Field(min_length=1, max_length=40)
    side_panel: SidePanel | None = None
    objective_ids: list[Slug] = Field(default_factory=list, max_length=10)
    intent: SceneIntent | None = None
    notes: str = Field(default="", max_length=4000)  # teacher-facing; never shown to students
    # Teacher choices (never LLM-facing). Both are left out of the serialised model at their defaults, so stored
    # screenplays, scene hashes and cache keys of lectures that do not use them are unchanged; the asset content
    # hash ignores them (``aadhi.pipeline.assets.scene_hash``): hiding or holding a scene never re-synthesises it.
    # ``hidden``: the scene stays in the lecture but is skipped by the timeline (player and MP4), the captions and
    # chapters, and the asset stage (no paid work).
    hidden: bool = Field(default=False, exclude_if=lambda v: v is False)
    # ``min_seconds``: the scene lasts at least this long; the extra time is added after its content (Aadhi idles,
    # the narration and captions keep their timing) in both renderers (``aadhi.compose.timeline``).
    min_seconds: float | None = Field(default=None, ge=1.0, le=600.0, exclude_if=lambda v: v is None)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_id", "chapter_id", mode="before")(_opt_slug)
    _norm_o = field_validator("objective_ids", mode="before")(_slug_list)

    @model_validator(mode="after")
    def _beats_ok(self) -> "SceneBase":
        beats = self.all_beats()
        ids = [b.id for b in beats]
        if len(ids) != len(set(ids)):
            raise ValueError(f"scene {self.id}: beat ids must be unique")
        if not isinstance(self, BoardScene):
            for b in beats:
                if b.board_item_id or b.fill_item_id or b.highlight_item_ids:
                    raise ValueError(f"scene {self.id}: only board scenes may reference board items")
        if self.side_panel and self.side_panel.show_from_beat_id and self.side_panel.show_from_beat_id not in ids:
            raise ValueError(f"scene {self.id}: side_panel.show_from_beat_id is not a beat of this scene")
        return self

    def all_beats(self) -> list[Beat]:
        return list(self.beats)


class BoardScene(SceneBase):
    """Board-driven scenes. Items without a revealing beat are visible from scene start;
    layout always follows list order regardless of reveal order."""

    type: Literal["content", "example", "summary", "key_takeaway", "recap", "title"]
    board: list[BoardItem] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _board_refs(self) -> "BoardScene":
        item_ids = [i.id for i in self.board]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError(f"scene {self.id}: board item ids must be unique")
        items = {i.id: i for i in self.board}
        revealed_at: dict[str, int] = {}
        for idx, b in enumerate(self.beats):
            if b.board_item_id is not None:
                if b.board_item_id not in items:
                    raise ValueError(f"scene {self.id}: beat {b.id} reveals unknown item {b.board_item_id}")
                if b.board_item_id in revealed_at:
                    raise ValueError(f"scene {self.id}: item {b.board_item_id} revealed by more than one beat")
                revealed_at[b.board_item_id] = idx
        filled: set[str] = set()
        for idx, b in enumerate(self.beats):
            for hid in b.highlight_item_ids:
                if hid not in items:
                    raise ValueError(f"scene {self.id}: beat {b.id} highlights unknown item {hid}")
            if b.fill_item_id is not None:
                item = items.get(b.fill_item_id)
                if item is None or not item.blank:
                    raise ValueError(f"scene {self.id}: beat {b.id} fills {b.fill_item_id}, which is not a blank step")
                if b.fill_item_id in filled:
                    raise ValueError(f"scene {self.id}: item {b.fill_item_id} filled more than once")
                if revealed_at.get(b.fill_item_id, -1) > idx:
                    raise ValueError(f"scene {self.id}: item {b.fill_item_id} filled before it is revealed")
                filled.add(b.fill_item_id)
        return self


class ChapterCardScene(SceneBase):
    type: Literal["chapter_card"]
    chapter_label: str = Field(default="Part 1", max_length=60)
    beats: list[Beat] = Field(default_factory=list, max_length=4)  # may be silent


class SimulationScene(SceneBase):
    """Full-width Manim animation; beat i <-> animation step i."""

    type: Literal["simulation"]
    manim: ManimSpec
    override_asset_key: str | None = Field(default=None, max_length=128)  # uploaded video replaces render


class AIVideoScene(SceneBase):
    """Real-world footage. Narration plays over a looping clip.

    If AI video is disabled, over budget or fails, the asset stage produces a still from
    ``fallback_image_prompt`` (or uses ``fallback_figure_id``) shown with a Ken-Burns pan.
    """

    type: Literal["ai_video"]
    video_prompt: str = Field(min_length=1, max_length=1200)
    rationale: str = Field(default="", max_length=400)
    fallback_image_prompt: str | None = Field(default=None, max_length=800)
    fallback_figure_id: Slug | None = None
    override_asset_key: str | None = Field(default=None, max_length=128)  # uploaded video/image
    # "New AI version" of the clip (and of its generated still): like ``SidePanel.variant``.
    variant: int = Field(default=0, ge=0, le=MAX_VISUAL_VARIANT, exclude_if=lambda v: v == 0)

    _norm_fig = field_validator("fallback_figure_id", mode="before")(_opt_slug)


class InteractiveScene(SceneBase):
    """p5.js sketch executed ONLY inside the sandboxed iframe (web/sandbox/p5.html)."""

    type: Literal["interactive"]
    p5_code: str = Field(min_length=1, max_length=20000)
    poster_override_asset_key: str | None = Field(default=None, max_length=128)  # still for MP4 renders


class QuizScene(SceneBase):
    """Retrieval-practice checkpoint: ``beats`` (question) -> countdown -> ``reveal_beats``.

    Options are shuffled deterministically by the canonicaliser (the model never chooses the
    answer position).
    """

    type: Literal["quiz_checkpoint"]
    question: str = Field(min_length=1, max_length=600)
    options: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(min_length=2, max_length=5)
    correct_index: int = Field(ge=0)
    feedback_wrong: list[str] = Field(default_factory=list)
    option_misconception_ids: list[Slug | None] = Field(default_factory=list)
    explanation: str = Field(default="", max_length=900)
    bloom: Bloom = "understand"
    countdown_seconds: int = Field(default=8, ge=3, le=30)
    reveal_beats: list[Beat] = Field(min_length=1, max_length=10)
    source_refs: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _quiz(self) -> "QuizScene":
        n = len(self.options)
        if self.correct_index >= n:
            raise ValueError(f"scene {self.id}: correct_index out of range")
        if len({o.strip().lower() for o in self.options}) != n:
            raise ValueError(f"scene {self.id}: quiz options must be distinct")
        if not self.feedback_wrong:
            self.feedback_wrong = [""] * n
        if len(self.feedback_wrong) != n:
            raise ValueError(f"scene {self.id}: feedback_wrong must align with options")
        self.feedback_wrong[self.correct_index] = ""
        if not self.option_misconception_ids:
            self.option_misconception_ids = [None] * n
        if len(self.option_misconception_ids) != n:
            raise ValueError(f"scene {self.id}: option_misconception_ids must align with options")
        self.option_misconception_ids = [_opt_slug(x) for x in self.option_misconception_ids]
        return self

    def all_beats(self) -> list[Beat]:
        return list(self.beats) + list(self.reveal_beats)


Scene = Annotated[
    Union[BoardScene, ChapterCardScene, SimulationScene, AIVideoScene, InteractiveScene, QuizScene],
    Field(discriminator="type"),
]

SCENE_TYPES = (
    "title", "content", "example", "summary", "key_takeaway", "recap",
    "chapter_card", "simulation", "ai_video", "interactive", "quiz_checkpoint",
)
SceneType = Literal[
    "title", "content", "example", "summary", "key_takeaway", "recap",
    "chapter_card", "simulation", "ai_video", "interactive", "quiz_checkpoint",
]


# ---------------------------------------------------------------------------
# Companion sheet (rendered to Markdown/HTML by code, never raw model markdown)
# ---------------------------------------------------------------------------


class FormulaEntry(StrictModel):
    name: str = Field(max_length=240)
    latex: str | None = Field(default=None, max_length=1200)
    description: str = Field(default="", max_length=800)
    variables: list[FormulaVariable] = Field(default_factory=list, max_length=8)
    source_item_id: str | None = None  # "<scene_id>/<item_id>" when derived from the board

    _chk = field_validator("latex")(_check_latex)


class DefinitionEntry(StrictModel):
    term: str = Field(max_length=240)
    definition: str = Field(max_length=1200)
    source_item_id: str | None = None


class MisconceptionEntry(StrictModel):
    misconception: str = Field(max_length=800)
    correction: str = Field(max_length=1200)


class PracticeProblem(StrictModel):
    id: Slug
    question: str = Field(max_length=2400)
    steps: list[str] = Field(default_factory=list, max_length=12)  # worked solution (answer key)
    final_answer: str = Field(max_length=1200)
    hints: list[str] = Field(default_factory=list, max_length=4)
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    objective_ids: list[Slug] = Field(default_factory=list, max_length=6)
    source_refs: list[str] = Field(default_factory=list, max_length=10)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_o = field_validator("objective_ids", mode="before")(_slug_list)


class CompanionSheet(StrictModel):
    """Formulas/definitions/misconceptions are derived from the board and Screenplay.misconceptions
    by ``aadhi.pipeline.companion``; only practice problems are model-generated."""

    key_formulas: list[FormulaEntry] = Field(default_factory=list, max_length=50)
    definitions: list[DefinitionEntry] = Field(default_factory=list, max_length=50)
    misconceptions: list[MisconceptionEntry] = Field(default_factory=list, max_length=50)
    practice_problems: list[PracticeProblem] = Field(default_factory=list, max_length=20)
    legacy_markdown: str | None = Field(default=None, max_length=60000)  # v1 imports


# ---------------------------------------------------------------------------
# Source figures and info
# ---------------------------------------------------------------------------


class SourceFigure(StrictModel):
    id: Slug
    caption: str = Field(default="", max_length=600)
    page: int | None = None
    asset_key: str | None = Field(default=None, max_length=128)  # stored image (kind figure/upload)
    width: int | None = None
    height: int | None = None

    _norm_id = field_validator("id", mode="before")(_slug_validator)


class SourceInfo(StrictModel):
    filename: str = Field(default="", max_length=255)
    mime: str = Field(default="", max_length=128)
    pages: int | None = None
    sha256: str | None = None


# ---------------------------------------------------------------------------
# Screenplay
# ---------------------------------------------------------------------------


class Screenplay(StrictModel):
    schema_version: int = SCHEMA_VERSION
    subject_name: str = Field(default="", max_length=240)
    unit_name: str = Field(default="", max_length=240)
    session_number: str = Field(default="Session 1", max_length=60)
    session_title: str = Field(default="", max_length=300)
    language: str = Field(default="en-IN", max_length=16)  # narration (BCP-47)
    board_language: str | None = Field(default=None, max_length=16)  # None = same as narration
    learning_objectives: list[LearningObjective] = Field(default_factory=list, max_length=12)
    concept_map: list[ConceptNode] = Field(default_factory=list, max_length=150)
    misconceptions: list[Misconception] = Field(default_factory=list, max_length=60)
    chapters: list[ChapterRef] = Field(default_factory=list, max_length=40)
    lexicon: list[LexiconEntry] = Field(default_factory=list, max_length=200)
    scenes: list[Scene] = Field(default_factory=list, max_length=200)
    companion_sheet: CompanionSheet = Field(default_factory=CompanionSheet)
    figures: list[SourceFigure] = Field(default_factory=list, max_length=200)
    source: SourceInfo = Field(default_factory=SourceInfo)

    @field_validator("learning_objectives", mode="before")
    @classmethod
    def _objectives_from_strings(cls, v: Any) -> Any:
        if isinstance(v, list):
            return [{"id": f"obj-{i + 1}", "text": x} if isinstance(x, str) else x for i, x in enumerate(v)]
        return v

    @model_validator(mode="after")
    def _graph(self) -> "Screenplay":
        def unique(name: str, ids: list[str]) -> set[str]:
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} ids must be unique")
            return set(ids)

        concepts = unique("concept_map", [c.id for c in self.concept_map])
        for c in self.concept_map:
            missing = [d for d in c.depends_on if d not in concepts]
            if missing:
                raise ValueError(f"concept {c.id} depends on unknown concepts {missing}")
        assert_acyclic({c.id: list(c.depends_on) for c in self.concept_map})
        scenes = unique("scene", [s.id for s in self.scenes])
        objectives = unique("learning_objective", [o.id for o in self.learning_objectives])
        miscs = unique("misconception", [m.id for m in self.misconceptions])
        chapters = unique("chapter", [c.id for c in self.chapters])
        figures = unique("figure", [f.id for f in self.figures])
        for s in self.scenes:
            if concepts and s.concept_id is not None and s.concept_id not in concepts:
                raise ValueError(f"scene {s.id} references unknown concept {s.concept_id}")
            if chapters and s.chapter_id is not None and s.chapter_id not in chapters:
                raise ValueError(f"scene {s.id} references unknown chapter {s.chapter_id}")
            bad_obj = [o for o in s.objective_ids if o not in objectives]
            if objectives and bad_obj:
                raise ValueError(f"scene {s.id} references unknown objectives {bad_obj}")
            if isinstance(s, QuizScene) and miscs:
                bad = [m for m in s.option_misconception_ids if m and m not in miscs]
                if bad:
                    raise ValueError(f"scene {s.id} references unknown misconceptions {bad}")
            if isinstance(s, BoardScene) and figures:
                for item in s.board:
                    if item.figure_id and item.figure_id not in figures:
                        raise ValueError(f"scene {s.id}: unknown figure {item.figure_id}")
        for ch in self.chapters:
            bad = [x for x in ch.scene_ids if x not in scenes]
            if bad:
                raise ValueError(f"chapter {ch.id} references unknown scenes {bad}")
        return self

    def scene_by_id(self, scene_id: str) -> Any:
        for s in self.scenes:
            if s.id == scene_id:
                return s
        raise KeyError(scene_id)


def assert_acyclic(graph: dict[str, list[str]]) -> None:
    """Kahn's algorithm (iterative; safe for any graph size)."""
    indeg = {n: 0 for n in graph}
    for n, deps in graph.items():
        for d in deps:
            if d in indeg:
                indeg[n] += 1
    rev: dict[str, list[str]] = {n: [] for n in graph}
    for n, deps in graph.items():
        for d in deps:
            if d in rev:
                rev[d].append(n)
    queue = deque(n for n, k in indeg.items() if k == 0)
    seen = 0
    while queue:
        n = queue.popleft()
        seen += 1
        for m in rev[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                queue.append(m)
    if seen != len(graph):
        cyclic = sorted(n for n, k in indeg.items() if k > 0)
        raise ValueError(f"concept_map has a cycle among: {', '.join(cyclic[:10])}")


def screenplay_json_schema() -> dict[str, Any]:
    """JSON Schema used to generate web/schemas/screenplay.schema.json (NOT an LLM schema)."""
    return Screenplay.model_json_schema()
