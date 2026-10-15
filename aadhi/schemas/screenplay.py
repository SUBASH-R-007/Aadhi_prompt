"""Canonical screenplay schema (schema_version 2; 3 when a Dynamic-video field is used).

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
* Dynamic video (design §6.7, §8.2): every v3 field exists as an *inert* field, left out of the
  JSON at its default (``exclude_if``), so classic screenplays serialise exactly as before. Each
  model lists its v3 fields in ``V3_FIELDS`` / ``V3_VALUES`` / ``V3_MODEL`` (feature names of
  ``aadhi.schemas.features``); a screenplay that uses one is written with ``schema_version = 3`` and
  a newer document is refused with ``NewerDocumentError`` (never silently stripped).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import deque
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from .features import check_reader_version, marked_version
from .jsonsafe import check_storable

SCHEMA_VERSION = 2  # classic screenplays; a screenplay using a v3 field is written as 3 (aadhi.schemas.features)

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
    "board[].unit", "board[].date", "captions[].text",  # v3: stat units, timeline dates, clip captions
)
PROTECTED_FIELDS = (
    "*.id", "*_id", "*_ids", "latex", "code", "language", "expr", "figure_id", "manim", "p5_code",
    # v3: icon keywords and stat values are never translated; credits and stock attributions stay exactly as the
    # licence requires
    "icon_hint", "value", "credit", "attribution",
)

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
# Ids of the Dynamic catalogues (theme families, personalities, fonts, caption styles, templates, backgrounds ...).
CatalogueId = Annotated[str, Field(pattern=r"^[a-z0-9_-]{1,40}$")]
# "<scene_id>/<item_id>" or "<scene_id>/<beat_id>" (hooks, predictions and the beats that answer them).
ItemRef = Annotated[str, Field(min_length=3, max_length=129, pattern=r"^[a-z0-9][a-z0-9_\-]{0,63}/[a-z0-9][a-z0-9_\-]{0,63}$")]


def _item_ref(v: Any) -> Any:
    """Normalise both halves of a ``"<scene_id>/<item_id>"`` reference like other ids (None/"" -> None)."""
    if v in ("", None):
        return None
    if isinstance(v, str) and v.count("/") == 1:
        scene, item = v.split("/")
        if scene.strip() and item.strip():
            return f"{slugify(scene)}/{slugify(item)}"
    return v

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

    # Schema v3 bookkeeping (aadhi.schemas.features): v3 field -> feature, v3 enum value -> feature, and the feature
    # of a model that is v3 as a whole. Every v3 field has an ``exclude_if`` that drops its default.
    V3_FIELDS: ClassVar[dict[str, str]] = {}
    V3_VALUES: ClassVar[dict[str, dict[str, str]]] = {}
    V3_MODEL: ClassVar[str | None] = None


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
    V3_FIELDS: ClassVar[dict[str, str]] = {"builds_on": "presentation"}

    id: Slug
    title: str = Field(min_length=1, max_length=160)
    scene_ids: list[Slug] = Field(default_factory=list, max_length=200)
    # v3 (planner): False when this chapter starts a new argument rather than building on the previous one (a new
    # background preset / music bed may start there, R-18 / R-47). Left out of the JSON when True.
    builds_on: bool = Field(default=True, exclude_if=lambda v: v is True)

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
    # v3 (Dynamic lectures, feature "items_v3"; classic surfaces render them as callout_tip / heading + paragraph /
    # bullet / callout_info / bullet "term — text", design §6.7):
    question = "question"  # ``text``; ``blank`` = answer withheld until a later beat ``answers`` it; ``level``
    stat = "stat"  # ``value`` (+ ``unit``) and ``text`` (context); source refs required (lint)
    step = "step"  # one step of a ``BoardScene.sequence`` (flow, cycle, timeline ...); ``text``, optional ``date``
    quote = "quote"  # verbatim from the source: ``text`` + ``caption`` (who said it)
    pair = "pair"  # familiar idea ``term`` <-> concept ``text`` (analogy / correspondence)


V3_BOARD_ITEM_KINDS = ("question", "stat", "step", "quote", "pair")


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


class FigurePin(StrictModel):
    """A point on a source figure placed by the teacher in Visual Review (never LLM-facing: the model never sees
    the picture). The camera can push toward it (``cue.lookHere``)."""

    id: Slug
    x: float = Field(ge=0.0, le=1.0)  # normalised to the figure's width
    y: float = Field(ge=0.0, le=1.0)  # normalised to the figure's height
    label: str = Field(default="", max_length=80)

    _norm_id = field_validator("id", mode="before")(_slug_validator)


class BoardItem(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {
        "value": "items_v3", "unit": "items_v3", "date": "items_v3", "icon_hint": "items_v3", "pins": "items_v3",
        "level": "items_v3",
    }
    V3_VALUES: ClassVar[dict[str, dict[str, str]]] = {"kind": {k: "items_v3" for k in V3_BOARD_ITEM_KINDS}}

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
    # v3 (feature "items_v3"); each is left out of the JSON at None / empty, so classic items are unchanged.
    value: str | None = Field(  # stat: the number as written ("3.5", "-40", "1,200"); protected from translation
        default=None, max_length=32, pattern=r"^-?[0-9][0-9.,]*$", exclude_if=lambda v: v is None
    )
    unit: str | None = Field(default=None, max_length=40, exclude_if=lambda v: v is None)  # stat
    date: str | None = Field(default=None, max_length=40, exclude_if=lambda v: v is None)  # step of a timeline
    icon_hint: str | None = Field(default=None, max_length=24, exclude_if=lambda v: v is None)  # one keyword, never translated
    pins: list[FigurePin] = Field(default_factory=list, max_length=8, exclude_if=lambda v: not v)  # figure (teacher)
    level: Literal["recall", "apply", "transfer"] | None = Field(default=None, exclude_if=lambda v: v is None)  # question

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_fig = field_validator("figure_id", mode="before")(_opt_slug)
    _norm_mis = field_validator("misconception_id", mode="before")(_opt_slug)
    _chk_latex = field_validator("latex")(_check_latex)

    @model_validator(mode="after")
    def _kind_fields(self) -> "BoardItem":
        k = self.kind
        if k == BoardItemKind.stat and not ((self.value or "").strip() and self.text.strip()):
            raise ValueError("stat board item requires 'value' and 'text'")
        if k == BoardItemKind.pair and not ((self.term or "").strip() and self.text.strip()):
            raise ValueError("pair board item requires 'term' and 'text'")
        if k == BoardItemKind.quote and not (self.text.strip() and (self.caption or "").strip()):
            raise ValueError("quote board item requires 'text' and 'caption'")
        if self.pins and k != BoardItemKind.figure:
            raise ValueError("only figure board items can have pins")
        if len({p.id for p in self.pins}) != len(self.pins):
            raise ValueError("figure pin ids must be unique")
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
        if self.blank and k not in (BoardItemKind.example_step, BoardItemKind.question):
            raise ValueError("only example_step and question items can be blank")
        needs_text = {
            BoardItemKind.heading, BoardItemKind.bullet, BoardItemKind.paragraph, BoardItemKind.callout_info,
            BoardItemKind.callout_tip, BoardItemKind.callout_warning, BoardItemKind.misconception,
            BoardItemKind.example_step, BoardItemKind.takeaway, BoardItemKind.question, BoardItemKind.step,
        }
        if k in needs_text and not self.text.strip():
            raise ValueError(f"{k.value} board item requires 'text'")
        return self


# ---------------------------------------------------------------------------
# Edits anchored to content (v3, design §8.2): narration cuts, clip ranges, overlays
# ---------------------------------------------------------------------------


class WordRange(StrictModel):
    """A narration cut: caption tokens ``[start, end)`` of the beat's ``narration`` (the tokens the captions show)."""

    start: int = Field(ge=0)
    end: int  # exclusive, > start
    text: str = Field(max_length=600)  # the removed words, for re-anchoring when the narration changes
    mode: Literal["splice", "revoice"] = "splice"

    @model_validator(mode="after")
    def _order(self) -> "WordRange":
        if self.end <= self.start:
            raise ValueError("a cut must end after it starts")
        return self


class ClipRange(StrictModel):
    """A trimmed range of a normalised clip (asset kind ``clip``, the mezzanine): 30 fps frame grid."""

    asset_key: str = Field(min_length=1, max_length=128)  # authorised through asset_uses
    in_frame: int = Field(ge=0)
    out_frame: int  # exclusive

    @model_validator(mode="after")
    def _order(self) -> "ClipRange":
        if self.out_frame <= self.in_frame:
            raise ValueError("a clip range must end after it starts")
        return self

    @property
    def frames(self) -> int:
        return self.out_frame - self.in_frame


OVERLAY_MIN_FRAMES = 30  # an overlay shows at least one second of its clip
CLIP_SCENE_MIN_FRAMES = 15  # a clip scene plays at least half a second


class Overlay(StrictModel):
    """B-roll shown over the narration from an anchor word of its beat (design §8.3)."""

    id: Slug
    at_word: int = Field(ge=0)  # anchor caption-token index in the beat ...
    at_text: str = Field(max_length=120)  # ... and its text, for re-anchoring
    clip: ClipRange
    region: Literal["full", "panel"] = "full"
    sound: Literal["mute", "quiet"] = "mute"  # quiet = narration - 20 LU
    concept_id: Slug | None = None
    keep_full_length: bool = False  # do not shorten it to keep the reveals under it visible (§8.3)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_id", mode="before")(_opt_slug)

    @model_validator(mode="after")
    def _length(self) -> "Overlay":
        if self.clip.frames < OVERLAY_MIN_FRAMES:
            raise ValueError(f"an overlay needs at least {OVERLAY_MIN_FRAMES} frames of its clip")
        return self


# ---------------------------------------------------------------------------
# Beats (narration units)
# ---------------------------------------------------------------------------


class Beat(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {
        "speaker": "presentation", "answers": "presentation", "cuts": "cuts", "overlays": "overlays",
        "voice_override_asset_key": "voice_override",
    }

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
    # v3 (Dynamic lectures and the editor, design §6.7 / §8.2). Each is left out of the JSON at its default, so
    # classic beats (and their scene hashes) are unchanged. cuts, speaker and voice_override_asset_key change the
    # audio and stay in the scene hash; overlays do not (aadhi.pipeline.assets.scene_hash).
    speaker: Literal["narrator", "aadhi", "student"] = Field(default="narrator", exclude_if=lambda v: v == "narrator")
    # "<scene_id>/<item_id>": the hook or predict question (in an earlier scene) this beat answers.
    answers: ItemRef | None = Field(default=None, exclude_if=lambda v: v is None)
    cuts: list[WordRange] = Field(default_factory=list, max_length=20, exclude_if=lambda v: not v)
    overlays: list[Overlay] = Field(default_factory=list, max_length=4, exclude_if=lambda v: not v)
    # The teacher's own recorded narration for this beat (asset kind narration_upload), replacing TTS.
    voice_override_asset_key: str | None = Field(default=None, max_length=128, exclude_if=lambda v: v is None)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_ref = field_validator("board_item_id", "fill_item_id", mode="before")(_opt_slug)
    _norm_hl = field_validator("highlight_item_ids", mode="before")(_slug_list)
    _norm_answers = field_validator("answers", mode="before")(_item_ref)


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
    V3_FIELDS: ClassVar[dict[str, str]] = {"colour_is_content": "presentation"}

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
    # v3: the colours of this picture or animation carry meaning (resistor bands, flame tests, spectra): no palette
    # style suffix, no Manim colour mapping (design §4.7). Left out of the JSON when False.
    colour_is_content: bool = Field(default=False, exclude_if=lambda v: v is False)

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
    "hook", "context", "prerequisite", "concept", "example", "practice", "check", "application", "synthesis", "transition",
    "pretraining", "predict", "recap",  # v3 (Dynamic lectures)
]
V3_NARRATIVE_ROLES = ("pretraining", "predict", "recap")

# v3: what a Dynamic scene's picture should do (planner hint; direct.py picks a template within its class, §4.4).
VisualIntent = Literal[
    "hook", "context", "pretraining", "define", "explain", "compare", "sequence", "cycle", "timeline", "cause",
    "quantity", "formula", "example", "code", "data", "spatial", "analogy", "misconception", "predict", "check",
    "apply", "summarize", "recap",
]


class SceneIntent(StrictModel):
    """What the scene is for (copied from the plan; teacher-facing, never shown to students).

    ``narrative_role`` and ``bridge_in`` keep the lecture's flow when a single scene is repaired or
    regenerated later (the plan itself is not stored with the screenplay)."""

    V3_FIELDS: ClassVar[dict[str, str]] = {"visual_intent": "presentation"}
    V3_VALUES: ClassVar[dict[str, dict[str, str]]] = {"narrative_role": {r: "presentation" for r in V3_NARRATIVE_ROLES}}

    goal: str = Field(default="", max_length=600)
    key_points: list[str] = Field(default_factory=list, max_length=12)
    source_refs: list[str] = Field(default_factory=list, max_length=20)
    narrative_role: NarrativeRoleName | None = None
    bridge_in: str = Field(default="", max_length=300)
    visual_intent: VisualIntent | None = Field(default=None, exclude_if=lambda v: v is None)  # v3


# ---------------------------------------------------------------------------
# Dynamic presentation and scene looks (v3, design §6.7; feature "presentation")
# ---------------------------------------------------------------------------

PersonaId = Annotated[str, Field(min_length=1, max_length=80)]  # aadhi VoicePersona.id (persona registry, B3)


class VoiceCalibration(StrictModel):
    natural_wpm: float = Field(ge=60, le=300)
    control: str = Field(max_length=200)  # "+8%", "speed=1.05" or a style text, as sent (§5.4)


class VoiceCast(StrictModel):
    narrator: PersonaId
    aadhi: PersonaId | None = None
    student: PersonaId | None = None
    calibration: dict[PersonaId, VoiceCalibration] = Field(default_factory=dict)

    @field_validator("calibration")
    @classmethod
    def _few(cls, v: dict[str, VoiceCalibration]) -> dict[str, VoiceCalibration]:
        if len(v) > 20:
            raise ValueError("at most 20 voice calibrations")
        return v


class MusicChoice(StrictModel):
    source: Literal["generated", "library", "upload", "procedural", "off"]
    provider: str | None = Field(default=None, max_length=40)  # "lyria" | "elevenlabs" | "procedural" | None
    mood: str | None = Field(default=None, max_length=60)
    asset_key: str | None = Field(default=None, max_length=128)  # authorised through asset_uses
    mode: Literal["bed", "gaps_bookends", "off"] = "bed"
    level: Literal["normal", "low"] = "normal"


HookKind = Literal["question", "true_false", "stakes", "mystery", "challenge", "story", "stat"]


class HookRef(StrictModel):
    question_item: ItemRef  # "<scene_id>/<item_id>"
    kind: HookKind
    answer_beat: ItemRef | None = None  # "<scene_id>/<beat_id>"

    _norm_refs = field_validator("question_item", "answer_beat", mode="before")(_item_ref)


class AssetStyle(StrictModel):
    """Frozen at generation: drives the image style suffix and the Manim background ("New colours" keeps it)."""

    family: CatalogueId
    personality: CatalogueId


PresentationPin = Literal["theme", "personality", "display_font", "voice", "music", "caption_style"]


class Presentation(StrictModel):
    """How a Dynamic lecture looks and sounds (absent = the classic Aadhi presenter mode). Every resolved choice
    and seed is stored, so a rebuild never re-rolls (design §6.8)."""

    style: Literal["dynamic"] = "dynamic"
    format: Literal["landscape", "portrait"] = "landscape"
    length_profile: Literal["standard", "platform_safe"] = "standard"
    reel_profile: Literal["learning", "social"] = "learning"
    destination: Literal["messaging", "lms", "youtube", "lesson_page"] = "messaging"
    look_seed: int = Field(ge=0, le=2**31 - 1)  # §4.4
    look_epoch: int = Field(default=0, ge=0)  # bumped by Fresh look / New colours
    layout_epoch: int = Field(default=0, ge=0)  # bumped by New layouts
    theme_family: CatalogueId
    personality: CatalogueId
    display_font: CatalogueId
    caption_style: CatalogueId
    cue_term_style: Literal["marker", "underline"]
    intro_layout: Literal["fullframe", "card_left", "pip", "split"] | None = None
    reel_card_x: int | None = Field(default=None, ge=0, le=1080)
    hook_kind: CatalogueId
    check_order: list[CatalogueId] = Field(default_factory=list, max_length=12)
    greeting_id: Slug | None = None
    asset_style: AssetStyle
    voices: VoiceCast
    music: MusicChoice
    entity_colors: dict[Annotated[str, Field(min_length=1, max_length=80)], Annotated[int, Field(ge=0, le=2)]] = Field(
        default_factory=dict
    )  # normalised symbol / entity -> content slot 0..2
    hook: HookRef | None = None
    aadhi_recap: bool = True
    captions_burn: bool = True
    sfx: bool = True
    motion: Literal["standard", "calm"] = "standard"
    pins: list[PresentationPin] = Field(default_factory=list, max_length=6)

    @field_validator("entity_colors")
    @classmethod
    def _few_entities(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) > 60:
            raise ValueError("at most 60 entity colours")
        return v

    @field_validator("pins")
    @classmethod
    def _unique_pins(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class PresenterSpec(StrictModel):
    """Aadhi in a Dynamic scene (intro and recap only, R-12)."""

    clip: Literal["left", "center"]  # never popup/right (R-14)
    seconds: float = Field(ge=2.0, le=8.0)
    layout: Literal["fullframe", "card_left", "pip", "split", "card"]


class SceneLook(StrictModel):
    """A scene's look: set by direct.py or the teacher, never LLM-facing. ``lock``: ``auto`` may be re-directed on
    save, ``settled`` keeps its template across edits, ``pinned`` is the teacher's choice."""

    template: CatalogueId | None = None
    variant: CatalogueId | None = None
    background: CatalogueId | None = None
    palette_slot: int | None = Field(default=None, ge=0, le=40)
    transition_in: CatalogueId | None = None
    hold_reason: Literal["formula", "code", "diagram", "think"] | None = None
    presenter: PresenterSpec | None = None
    continues_from: Slug | None = None  # scene id: part B of a split continues A's final visual state
    lock: Literal["auto", "settled", "pinned"] = "auto"

    _norm_cont = field_validator("continues_from", mode="before")(_opt_slug)


class SceneBase(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"look": "presentation", "colour_is_content": "presentation"}

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
    # v3 (Dynamic lectures). ``look``: the scene's resolved template / background / transition and its lock
    # (direct.py or the teacher; never LLM-facing; outside the asset hash). ``colour_is_content``: see SidePanel.
    look: SceneLook | None = Field(default=None, exclude_if=lambda v: v is None)
    colour_is_content: bool = Field(default=False, exclude_if=lambda v: v is False)

    _norm_id = field_validator("id", mode="before")(_slug_validator)
    _norm_c = field_validator("concept_id", "chapter_id", mode="before")(_opt_slug)
    _norm_o = field_validator("objective_ids", mode="before")(_slug_list)

    @model_validator(mode="after")
    def _beats_ok(self) -> "SceneBase":
        beats = self.all_beats()
        ids = [b.id for b in beats]
        if len(ids) != len(set(ids)):
            raise ValueError(f"scene {self.id}: beat ids must be unique")
        overlay_ids = [o.id for b in beats for o in b.overlays]
        if len(overlay_ids) != len(set(overlay_ids)):
            raise ValueError(f"scene {self.id}: overlay ids must be unique")
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

    V3_FIELDS: ClassVar[dict[str, str]] = {**SceneBase.V3_FIELDS, "sequence": "presentation"}

    type: Literal["content", "example", "summary", "key_takeaway", "recap", "title"]
    board: list[BoardItem] = Field(default_factory=list, max_length=12)
    # v3: the board's ``step`` items form a flow / cycle / timeline / chain / checklist (Dynamic sequence templates).
    sequence: Literal["flow", "cycle", "timeline", "chain", "checklist"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

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


class ClipCaption(StrictModel):
    """A caption of an inserted clip, in clip-relative seconds (after the trim)."""

    start: float = Field(ge=0.0)
    end: float
    text: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _order(self) -> "ClipCaption":
        if self.end <= self.start:
            raise ValueError("a clip caption must end after it starts")
        return self


ClipFormat = Literal["landscape", "portrait"]


class ClipScene(SceneBase):
    """A teacher-inserted external clip (v3, feature "clip"; design §8.2). Never plannable: only the editor
    inserts one. It lasts max(clip length, voice-over narration + its pauses); a longer voice-over freezes the
    last frame (or loops)."""

    V3_MODEL: ClassVar[str | None] = "clip"

    type: Literal["clip"]
    beats: list[Beat] = Field(default_factory=list, max_length=40)  # optional voice-over
    clip: ClipRange
    sound: Literal["keep", "mute"] = "keep"
    gain_db: float = Field(default=0.0, ge=-30.0, le=12.0)  # after loudness matching
    fit: Literal["contain_blur", "contain", "cover"] = "contain_blur"
    focus: dict[ClipFormat, tuple[float, float]] = Field(default_factory=dict)  # cover focus point (0..1) per format
    end_behavior: Literal["freeze", "loop"] = "freeze"
    captions: list[ClipCaption] = Field(default_factory=list, max_length=500)
    credit: str = Field(default="", max_length=200)  # protected from translation (licence attribution)

    @model_validator(mode="after")
    def _clip(self) -> "ClipScene":
        if self.clip.frames < CLIP_SCENE_MIN_FRAMES:
            raise ValueError(f"scene {self.id}: a clip scene needs at least {CLIP_SCENE_MIN_FRAMES} frames")
        for fmt, (x, y) in self.focus.items():
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                raise ValueError(f"scene {self.id}: focus for {fmt} must be within 0..1")
        return self


Scene = Annotated[
    Union[BoardScene, ChapterCardScene, SimulationScene, AIVideoScene, InteractiveScene, QuizScene, ClipScene],
    Field(discriminator="type"),
]

SCENE_TYPES = (
    "title", "content", "example", "summary", "key_takeaway", "recap",
    "chapter_card", "simulation", "ai_video", "interactive", "quiz_checkpoint",
    "clip",  # v3: teacher-inserted clips (never plannable; feature "clip")
)
SceneType = Literal[
    "title", "content", "example", "summary", "key_takeaway", "recap",
    "chapter_card", "simulation", "ai_video", "interactive", "quiz_checkpoint",
    "clip",
]
# Scene types a plan may contain (the planner and plan review never create a clip scene).
PLANNABLE_SCENE_TYPES = SCENE_TYPES[:-1]


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
    V3_FIELDS: ClassVar[dict[str, str]] = {"presentation": "presentation"}

    # 2 for classic screenplays, 3 when a v3 field is used (set on validation and on every dump); a document above
    # aadhi.schemas.features.READER_SCHEMA_VERSION is refused (NewerDocumentError).
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
    # v3: absent = the classic Aadhi presenter mode (design §3.6); left out of the JSON when None.
    presentation: Presentation | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="before")
    @classmethod
    def _reader_version(cls, data: Any) -> Any:
        # A newer build's document is refused, never read with its unknown fields dropped (NewerDocumentError).
        check_reader_version(data, "screenplay")
        return data

    @model_serializer(mode="wrap")
    def _write_version(self, handler: SerializerFunctionWrapHandler) -> Any:
        # Written as 3 whenever a v3 field is used, even after a model_copy that skipped validation.
        data = handler(self)
        if isinstance(data, dict) and "schema_version" in data:
            data["schema_version"] = marked_version(self, self.schema_version)
        return data

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
        self._check_answers()
        version = marked_version(self, self.schema_version)
        if version != self.schema_version:
            self.schema_version = version
        return self

    def _check_answers(self) -> None:
        """``Beat.answers`` names a board item of an earlier scene (the hook or predict question it answers)."""
        earlier: dict[str, set[str]] = {}
        for s in self.scenes:
            for b in s.all_beats():
                if b.answers is None:
                    continue
                scene_id, item_id = b.answers.split("/")
                if item_id not in earlier.get(scene_id, ()):
                    raise ValueError(
                        f"scene {s.id}: beat {b.id} answers {b.answers}, which is not a board item of an earlier scene"
                    )
            earlier[s.id] = {i.id for i in s.board} if isinstance(s, BoardScene) else set()

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
