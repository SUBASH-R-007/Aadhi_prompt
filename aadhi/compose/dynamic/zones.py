"""Safe zones, the layout grid, the R-29 type floors and the layout tokens of the Dynamic stage: the one source of every
zone and grid (DYNAMIC_VIDEO_DESIGN §2.5, R-59, R-29, §4.6; B2 plan §1.1).

What is here (frozen data and pure helpers, no I/O):

* :data:`ZONES`: per format the boxes ``web/js/dynamic/core.js`` ``ZONES`` draws with today (critical text boxes,
  caption band, header, title, body, text column), value for value; ``tests/compose/dynamic/test_zones.py`` pins them,
  and from step B core.js reads them from the generated catalogue (``catalogue.py`` writes
  ``web/js/dynamic/catalogue.gen.js``).
* :data:`REGIONS`: the named regions templates are specified against (§2.5): 16:9 ``full``, ``left-55`` + ``right-45``,
  ``centre-60``, ``top-band`` + ``stage``; 9:16 the hook band, the one-line headline + stage, the tall stage, the text
  columns above and below y 840 and the inserted-clip box. :data:`GRID` is the 16:9 12-column grid (24 px gutters);
  :data:`END_SCREEN` the 16:9 end-screen half.
* :data:`PLATFORM_ZONES` (``YT_SHORTS``, ``META_REELS``, ``TIKTOK``) and :data:`UNIVERSAL`, their intersection (§2.5).
* :data:`TYPE_FLOORS` (R-29 per format) and :data:`TYPE_SCALE` (§4.6 line heights and tracking, plan §2 Indic line
  heights).
* :data:`LAYOUT`: the layout tokens of plan §1.1 (8 px spacing scale, radii, structure borders, cue outlines, shadow
  levels, icon sizes); the dim and scrim alphas live in ``themes.py`` (``DIM_ALPHA``, ``SCRIM_ALPHA``).

Boxes are stage pixels of the logical stage (1920x1080 or 1080x1920), ``x0 <= x < x1`` and ``y0 <= y < y1``, whole
pixels. Changing a value changes where every Dynamic template draws: :data:`ZONES_VERSION` is bumped with it and the
catalogue regenerated (``python -m aadhi.compose.dynamic.catalogue``).

Imported only inside the Dynamic gate (preflight, the catalogue generator, B2's resolver), never by a classic build.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, NamedTuple

Format = Literal["landscape", "portrait"]
ScriptClass = Literal["latin", "indic"]

FORMATS: Final[tuple[Format, Format]] = ("landscape", "portrait")
ZONES_VERSION: Final = 1  # §2.5 "Constants in zones.py (versioned)": bump with any value below


@dataclass(frozen=True, slots=True)
class Box:
    """An axis-aligned box in stage pixels: ``x0 <= x < x1``, ``y0 <= y < y1`` (core.js ``Box``)."""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if not (self.x0 < self.x1 and self.y0 < self.y1):
            raise ValueError(f"empty box {self!r}")

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def band(self) -> tuple[int, int, int, int]:
        """``(y0, y1, x0, x1)``: the band order ``preflight.inside_bands`` reads."""
        return (self.y0, self.y1, self.x0, self.x1)

    def contains(self, other: Box) -> bool:
        return self.x0 <= other.x0 and self.y0 <= other.y0 and other.x1 <= self.x1 and other.y1 <= self.y1

    def intersect(self, other: Box) -> Box | None:
        x0, y0, x1, y1 = max(self.x0, other.x0), max(self.y0, other.y0), min(self.x1, other.x1), min(self.y1, other.y1)
        return Box(x0, y0, x1, y1) if x0 < x1 and y0 < y1 else None

    def as_dict(self) -> dict[str, int]:
        """The core.js shape ``{x0, y0, x1, y1}``."""
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1}


# --- the stage zones (R-59; core.js ZONES) ----------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatZones:
    """The zones of one format, the fields of core.js ``Zones``: ``critical`` (critical text lies inside one of these
    boxes), ``caption`` (the caption band, R-34: one fixed position), ``header`` (kicker / pips row), ``title`` (title
    or headline slot), ``body`` (B1 board_generic's content stage: cards, media) and ``text_column`` (where card text
    may sit; in 9:16 narrower than the shapes, ``textColumn`` in the JS)."""

    format: Format
    width: int
    height: int
    critical: tuple[Box, ...]
    caption: Box
    header: Box
    title: Box
    body: Box
    text_column: Box

    def as_stage_dict(self) -> dict[str, object]:
        """Exactly core.js's ``ZONES[format]`` object (``textColumn`` spelled as the JS does)."""
        return {"format": self.format, "width": self.width, "height": self.height,
                "critical": [b.as_dict() for b in self.critical], "caption": self.caption.as_dict(),
                "header": self.header.as_dict(), "title": self.title.as_dict(), "body": self.body.as_dict(),
                "textColumn": self.text_column.as_dict()}


# 16:9 (§2.5, R-59): 96 px margins left and right, 54 px at the top; critical content in x 96-1824, y 54-912, the
# caption band y 924-1032 under it (2 lines x 52 px), the bottom 130 px under it kept for the player's controls.
_LANDSCAPE_CRITICAL: Final = Box(96, 54, 1824, 912)
# 9:16 (§2.5, R-59): the intersection of the YouTube, Meta and TikTok zones, with a column symmetric about the centre:
# critical text in x 192-888 for y 288-840 and in x 120-780 for y 840-1248 (left of the TikTok action rail).
_PORTRAIT_UPPER: Final = Box(192, 288, 888, 840)
_PORTRAIT_LOWER: Final = Box(120, 840, 780, 1248)

ZONES: Final[Mapping[Format, FormatZones]] = MappingProxyType({
    "landscape": FormatZones(
        format="landscape", width=1920, height=1080,
        critical=(_LANDSCAPE_CRITICAL,),
        caption=Box(96, 924, 1824, 1032),
        header=Box(96, 54, 1824, 102),
        title=Box(96, 112, 1824, 352),
        body=Box(96, 300, 1824, 900),
        text_column=Box(96, 300, 1824, 900),
    ),
    "portrait": FormatZones(
        format="portrait", width=1080, height=1920,
        critical=(_PORTRAIT_UPPER, _PORTRAIT_LOWER),
        caption=Box(120, 1040, 780, 1240),  # R-59: x 120-780, y 1040-1240, one position for the whole video (R-34)
        header=Box(192, 296, 888, 336),  # the "progress pips + act label (40 px)" row of §2.5
        title=Box(192, 340, 888, 460),
        body=Box(60, 470, 1020, 1020),  # shapes may span x 60-1020 (B1 board_generic's stage ends 20 px above captions)
        # text below y 840 stays left of the TikTok action rail (x 780): one column for B1's whole card stack
        text_column=Box(192, 470, 780, 1020),
    ),
})


def zones_for(fmt: str) -> FormatZones:
    """The zones of ``fmt`` (anything other than ``"portrait"`` is landscape, as core.js ``zonesFor``)."""
    return ZONES["portrait" if fmt == "portrait" else "landscape"]



# --- the 16:9 grid and the named regions (§2.5) -----------------------------------------------------------------------


@dataclass(frozen=True)
class Grid:
    """A column grid over ``box``: ``columns`` equal columns with ``gutter`` px between them (whole pixels)."""

    box: Box
    columns: int
    gutter: int

    def __post_init__(self) -> None:
        if (self.box.w - (self.columns - 1) * self.gutter) % self.columns:
            raise ValueError("the grid's columns must be whole pixels")

    @property
    def column_width(self) -> int:
        return (self.box.w - (self.columns - 1) * self.gutter) // self.columns

    def column(self, i: int) -> Box:
        """Column ``i`` (0-based) over the grid box's full height."""
        if not 0 <= i < self.columns:
            raise IndexError(i)
        x0 = self.box.x0 + i * (self.column_width + self.gutter)
        return Box(x0, self.box.y0, x0 + self.column_width, self.box.y1)

    def span(self, first: int, last: int) -> Box:
        """Columns ``first..last`` (inclusive) and the gutters between them."""
        a, b = self.column(first), self.column(last)
        return Box(a.x0, self.box.y0, b.x1, self.box.y1)


# 12 columns of 122 px with 24 px gutters fill the 1728 px content box exactly (§2.5).
GRID: Final = Grid(_LANDSCAPE_CRITICAL, 12, 24)

_C = _LANDSCAPE_CRITICAL
# left-55 + right-45 split the content box at 55% (x 1046.4 -> 1046, the line B1 board_generic splits at) with one
# grid gutter centred on the line; centre-60 is the middle 60% (20% = 345.6 -> 346 px off each side). The design
# gives the percentages, not grid columns (55% of 12 columns is 6.6), so the regions are whole-pixel fractions.
_SPLIT_55: Final = _C.x0 + round(0.55 * _C.w)
_CENTRE_INSET: Final = round(0.2 * _C.w)
# top-band + stage: the band is B1's header and title rows (y 54-352, core.js ZONES header.y0 .. title.y1); the stage
# starts one gutter below it and runs to the critical box's foot.
_TOP_BAND_Y1: Final = 352
SHAPES_X: Final = (60, 1020)  # 9:16: shapes (cards, media, diagrams) may span x 60-1020; text keeps to the columns

REGIONS: Final[Mapping[Format, Mapping[str, Box]]] = MappingProxyType({
    "landscape": MappingProxyType({
        "full": _LANDSCAPE_CRITICAL,
        "left-55": Box(_C.x0, _C.y0, _SPLIT_55 - GRID.gutter // 2, _C.y1),
        "right-45": Box(_SPLIT_55 + GRID.gutter // 2, _C.y0, _C.x1, _C.y1),
        "centre-60": Box(_C.x0 + _CENTRE_INSET, _C.y0, _C.x1 - _CENTRE_INSET, _C.y1),
        "top-band": Box(_C.x0, _C.y0, _C.x1, _TOP_BAND_Y1),
        "stage": Box(_C.x0, _TOP_BAND_Y1 + GRID.gutter, _C.x1, _C.y1),
    }),
    "portrait": MappingProxyType({
        # §2.5: HOOK band (intro and hook only): <= 4 lines, 88-104 px auto-fit, text x 192-888, y 340-760
        "hook": Box(192, 340, 888, 760),
        # ... or a 1-line HEADLINE (y 340-440) + the VISUAL stage y 460-1040 (580 px); shapes span x 60-1020
        "headline": Box(192, 340, 888, 440),
        "stage": Box(SHAPES_X[0], 460, SHAPES_X[1], 1040),
        # the "tall" variant without a headline: y 340-1040 (700 px)
        "tall": Box(SHAPES_X[0], 340, SHAPES_X[1], 1040),
        # text x 192-888 above y 840, x 120-780 below it (the TikTok action rail): R-59's critical bands
        "text-above": _PORTRAIT_UPPER,
        "text-below": _PORTRAIT_LOWER,
        # an inserted 16:9 clip shown contain_blur sits at y 360-968 (1080x608), captions below it on the blur
        "clip": Box(0, 360, 1080, 968),
    }),
})


@dataclass(frozen=True)
class EndScreen:
    """§2.5 / §2.6 YouTube "end-screen space": the last ``seconds`` of a 16:9 video keep ``box`` (the right half of
    the frame) clean for the platform's end-screen elements (``outro_next``)."""

    box: Box
    seconds: float


END_SCREEN: Final = EndScreen(Box(960, 0, 1920, 1080), 15.0)


# --- platform zones (§2.5) --------------------------------------------------------------------------------------------


class Rail(NamedTuple):
    """A right-hand action rail ``width`` px wide from ``from_y`` down (TikTok's like / comment / share column)."""

    width: int
    from_y: int


class PlatformZone(NamedTuple):
    """A platform's unsafe margins on the 1080x1920 frame, in px: ``top``, ``bottom``, ``left``, ``right``, and an
    optional right :class:`Rail` (§2.5 ``YT_SHORTS = (288, 672, 48, 192)``, ...)."""

    top: int
    bottom: int
    left: int
    right: int
    rail: Rail | None = None

    def safe_boxes(self, width: int = 1080, height: int = 1920) -> tuple[Box, ...]:
        """The area clear of the platform's UI, as boxes (two when a rail narrows the lower part)."""
        x0, y0, x1, y1 = self.left, self.top, width - self.right, height - self.bottom
        if self.rail is None or self.rail.from_y >= y1:
            return (Box(x0, y0, x1, y1),)
        rail_x = min(x1, width - self.rail.width)
        if self.rail.from_y <= y0:
            return (Box(x0, y0, rail_x, y1),)
        return (Box(x0, y0, x1, self.rail.from_y), Box(x0, self.rail.from_y, rail_x, y1))


def intersect_zones(zones: Iterable[PlatformZone]) -> PlatformZone:
    """The zone safe on every platform: the largest margin on each side; the widest rail starting highest."""
    zs = list(zones)
    if not zs:
        raise ValueError("no platform zones")
    rails = [z.rail for z in zs if z.rail is not None]
    rail = Rail(max(r.width for r in rails), min(r.from_y for r in rails)) if rails else None
    return PlatformZone(max(z.top for z in zs), max(z.bottom for z in zs), max(z.left for z in zs),
                        max(z.right for z in zs), rail)


YT_SHORTS: Final = PlatformZone(288, 672, 48, 192)
META_REELS: Final = PlatformZone(269, 672, 65, 65)
TIKTOK: Final = PlatformZone(240, 660, 120, 120, Rail(300, 840))
PLATFORM_ZONES: Final[Mapping[str, PlatformZone]] = MappingProxyType(
    {"yt_shorts": YT_SHORTS, "meta_reels": META_REELS, "tiktok": TIKTOK})
# x 120-888 for y 288-840 and x 120-780 for y 840-1248: R-59's critical bands are this with the upper column made
# symmetric about the centre (x 192-888), and the caption band uses its lower part (x 120-780).
UNIVERSAL: Final = intersect_zones(PLATFORM_ZONES.values())
SAFE_ZONES: Final[Mapping[str, PlatformZone]] = MappingProxyType({**PLATFORM_ZONES, "universal": UNIVERSAL})


# --- type (R-29, §4.6, plan §2) -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TypeFloors:
    """R-29 type sizes of one format (logical px at the 1080 output). ``text_min`` is "never below"; ``label_min`` the
    label floor (chips, card labels, option letters); ``body``/``heading``/``display``/``hook`` the ranges (None where
    R-29 gives none for the format: 9:16 headings and display text are sized per template slot); ``caption`` the
    caption size range and ``caption_min`` the floor a single long word may shrink to (§2.6, plan §2); in 9:16 text at
    <= ``heavy_max_px`` is at least weight ``heavy_min_weight``."""

    format: Format
    text_min: int
    label_min: int
    body: tuple[int, int]
    body_default: int
    heading: tuple[int, int] | None
    display: tuple[int, int] | None
    hook: tuple[int, int] | None
    caption: tuple[int, int]
    caption_min: int
    heavy_max_px: int | None = None
    heavy_min_weight: int | None = None


TYPE_FLOORS: Final[Mapping[Format, TypeFloors]] = MappingProxyType({
    # R-29 16:9: body 52 (48-56); labels >= 40; never < 36; headings 84-120; display 140-200. §2.6: captions 48-54.
    "landscape": TypeFloors("landscape", text_min=36, label_min=40, body=(48, 56), body_default=52,
                            heading=(84, 120), display=(140, 200), hook=None, caption=(48, 54), caption_min=48),
    # R-29 9:16: body 56-64; labels >= 44; never < 40; hook headline 88-104 auto-fit; weight >= 600 at <= 64 px.
    # §2.6: captions 56-64, a single long word may shrink to >= 52. body_default 56 is B1 board_generic's body.
    "portrait": TypeFloors("portrait", text_min=40, label_min=44, body=(56, 64), body_default=56,
                           heading=None, display=None, hook=(88, 104), caption=(56, 64), caption_min=52,
                           heavy_max_px=64, heavy_min_weight=600),
})


def min_text_px(fmt: str) -> int:
    """R-29 "never below" of ``fmt`` (landscape for anything but ``"portrait"``)."""
    return TYPE_FLOORS["portrait" if fmt == "portrait" else "landscape"].text_min


def min_label_px(fmt: str) -> int:
    """R-29 label floor of ``fmt``."""
    return TYPE_FLOORS["portrait" if fmt == "portrait" else "landscape"].label_min


@dataclass(frozen=True)
class TypeRole:
    """Line heights (per script class, ``(min, max)``; ``max`` None = no upper bound) and tracking (em, ``(min, max)``)
    of a type role (§4.6 "display 1.0-1.05 line height with -1 to -2% tracking; headings 1.15; body 1.35"; plan §2
    Indic minimums: display >= 1.25, body >= 1.45, captions >= 1.35 (the caption values live with the caption styles,
    ``themes.CAPTION_STYLES``)). ``max_line_chars``: §4.6 "body lines <= 45 chars (16:9)"."""

    line_height: Mapping[ScriptClass, tuple[float, float | None]]
    tracking_em: tuple[float, float] = (0.0, 0.0)
    max_line_chars: Mapping[Format, int] | None = None


TYPE_SCALE: Final[Mapping[str, TypeRole]] = MappingProxyType({
    "display": TypeRole({"latin": (1.0, 1.05), "indic": (1.25, None)}, tracking_em=(-0.02, -0.01)),
    # Indic headings: no value in the design or the plan; at least the Indic display minimum (A2 proposal, pending
    # owner review; not in the design).
    "heading": TypeRole({"latin": (1.15, 1.15), "indic": (1.25, None)}),
    "body": TypeRole({"latin": (1.35, 1.35), "indic": (1.45, None)}, max_line_chars={"landscape": 45}),
})
# §4.6 casing: sentence case everywhere; all caps only for labels of <= 4 words and single stat units, tracked +4-6%.
CAPS_LABEL_MAX_WORDS: Final = 4
CAPS_TRACKING_EM: Final = (0.04, 0.06)


# --- layout tokens (plan §1.1) ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutTokens:
    """Geometry every B2 template takes its spacing, corners, borders, outlines, shadows and icon sizes from.

    * ``spacing``: the 8 px scale (plan §1.1: ``spacing_unit``) and its steps (the grid gutter, 24, is one of them;
      which steps, 8-128: A2 proposal, pending owner review; not in the design or the plan).
    * ``radius``: card 24, media 24 (§4.3 media_split "24 px radius"), chip a pill (999 px, as dynamic.css
      ``.dyn-chip``), the Aadhi intro/recap card 40 (§3.3 ``card_left``). B1's board_generic keeps its own 28 px media
      frame and presenter radius (dynamic.css ``.dyn-frame``, ``.dyn-presenter``).
    * ``border``: structure borders 4-6 px (§4.7: pips and card borders); B1's card border is 6.
    * ``outline``: plan §1.5 cue geometry: the 4 px ``cue.look_here`` focus outline, at least 8 px outside its target;
      the 4 px ``cue.term`` underline; state.js's 2 px "current" border.
    * ``shadow``: three levels as CSS ``box-shadow`` values, the ones dynamic.css draws: 1 the chip, 2 a card, 3 the
      media frame (neutral black, the only literal colour besides captions, dynamic.css header).
    * ``icon``: the inline icon size (dynamic.css ``.dyn-icon``, 1.05 em), the stroke width of the adopted icon sets
      (Lucide's default 2 on its 24 px view box, §4.12) and two block sizes on the 8 px scale for cards and a
      feature icon (``card_px`` / ``feature_px``: A2 proposal, pending owner review; not in the design, which names
      no pixel size; B2c/B2f settle them with the owner)."""

    spacing_unit: int
    spacing: tuple[int, ...]
    radius: Mapping[str, int]
    border: Mapping[str, int]
    outline: Mapping[str, int]
    shadow: Mapping[str, str]
    icon: Mapping[str, float]


LAYOUT: Final = LayoutTokens(
    spacing_unit=8,
    spacing=(8, 16, 24, 32, 40, 48, 64, 80, 96, 128),  # A2 proposal, pending owner review (the steps)
    radius=MappingProxyType({"card": 24, "media": 24, "chip": 999, "presenter_card": 40}),
    border=MappingProxyType({"structure_min": 4, "structure_max": 6, "card": 6}),
    outline=MappingProxyType({"focus_px": 4, "focus_outset_px": 8, "underline_px": 4, "state_border_px": 2}),
    shadow=MappingProxyType({
        "1": "0 6px 18px rgba(0, 0, 0, 0.18)",
        "2": "0 18px 44px rgba(0, 0, 0, 0.26), 0 2px 6px rgba(0, 0, 0, 0.18)",
        "3": "0 24px 60px rgba(0, 0, 0, 0.32)",
    }),
    # card_px / feature_px: A2 proposal, pending owner review
    icon=MappingProxyType({"inline_em": 1.05, "stroke_width": 2, "card_px": 96, "feature_px": 320}),
)
