"""The Dynamic video theme catalogue and its validator (design §4.7 colour system, §4.5 backgrounds, §4.8 cues and
personalities, §4.11 captions, §4.6 faces, R-52..R-56, §7.5 chroma rule; plan B2 §1.1-§1.3).

What is here (data, pure functions, no I/O):

* **Content palettes** (``content.*``, §4.7): fixed per mode, never rotated, never nudged. ``ink``/``ink2`` are the B1
  OKLCH definitions (:data:`INK_LCH`), the other roles the exact §4.7 hex values. ``content_alt`` is the other mode's
  palette: the text colours on the dark code-editor surface of a light family (§4.3 ``code_walkthrough``).
* **Families** (§4.7 table, 12 rows; the four R1 ones released): mode, background hue and chroma, accent window(s),
  default personality. :func:`structure_tokens` derives a scene's anchors (``bg``, ``bg-alt``, ``bg-tint``,
  ``surface``, ``struct.accent``) with :func:`colour_math.palette_hex`, exactly as B1's ``presentation.py`` did, so
  every B1 token is unchanged; :func:`accent_hue` is B1's rotation (``h0 + Δk``, ends and middle alternating).
* **Derived structure colours** (plan §1.2): what the stage derives at run time today (``colour_block.js``
  ``blockColors``, ``core.js`` ``inkOn`` and the ``makeRoles`` surface fallback, ``stage.js`` ``blockInk2``,
  ``board_generic.js``'s ``${bg}c7`` scrim and ``scrimmedBg``, ``mesh_drift.js``'s colours) is mirrored here line by
  line on :mod:`colour_math` (parity-tested against the JS through ``tests/fixtures/colour_vectors.json``), so the
  resolver can store them in ``ResolvedLook.tokens`` (:func:`scene_colours`). Two rules are applied on top, in Python
  only: a full-frame block in the saturated-red range gets its chroma lowered (R-54), and an accent whose block text
  misses 3:1 is nudged by up to +-0.06 L (§4.7 validator; content is never nudged).
* **Personalities** (§4.8, 6 rows; Crisp and Soft released), **caption styles** (§4.11, 6 rows; ``clean_pill`` and
  ``bold_outline`` released, with the metrics of ``web/css/dynamic.css``), **faces** (§4.6), **cue colours** per mode
  (§4.8 ``cue.term``), the dim and scrim alphas.
* **The validator** (:func:`validate`): a pure function returning findings and the measured worst values. A finding on
  a released family or on the content palette is a build failure (pinned by ``tests/compose/dynamic/test_themes.py``);
  findings on W2 families are reported for their release batch.

Imported only inside the Dynamic gate (``aadhi.compose.presentation`` imports it lazily).
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from . import colour_math as cm

Mode = Literal["dark", "light"]
Release = Literal["R1", "W2"]
Format = Literal["landscape", "portrait"]
ScriptClass = Literal["latin", "indic"]
Tone = Literal["bg", "bg-alt", "bg-tint"]

MODES: Final[tuple[Mode, Mode]] = ("dark", "light")
FORMATS: Final[tuple[Format, Format]] = ("landscape", "portrait")
TONES: Final[tuple[Tone, Tone, Tone]] = ("bg", "bg-alt", "bg-tint")  # §4.5 tone step (presentation.TONES)

# --- R-53 thresholds and the §4.7/§4.8 contrast rules -------------------------------------------------------------
CONTRAST_BODY: Final = 4.5  # R-53 body text, formula symbols and entity-coloured text
CONTRAST_LARGE: Final = 3.0  # R-53 text >= 32 px regular or >= 24 px bold (bumper titles, hook headlines)
CONTRAST_GRAPHICS: Final = 3.0  # R-53 graphics (focus outlines, underlines, pips, card borders)
CONTRAST_CAPTION: Final = 7.0  # R-53 captions target
CONTRAST_INK_PALETTE: Final = 7.0  # §4.7 table: ink >= 7:1 on its palette's own backgrounds (bg, bg-alt, bg-tint, surface)
CONTRAST_MARKER: Final = 12.0  # §4.7 table: ink on mark >= 12:1
DIM_ALPHA: Final = 0.7  # cue.look_here dims the other groups to 70% (§4.8)
DIM_INK_CONTRAST: Final = 4.5  # plan §2: ink at 70% >= 4.5:1
DIM_INK2_CONTRAST: Final = 3.0  # ... ink2 at 70% >= 3:1
SCRIM_ALPHA_MIN: Final = 0.55  # §4.5: a scrim is >= 55% opaque
SCRIM_ALPHA_HEX: Final = "c7"  # board_generic.js SCRIM_ALPHA_HEX (0xc7 = 78%), spelled as the stage writes it
SCRIM_ALPHA: Final = 0xC7 / 255
HUE_SEPARATION_MIN: Final = 30.0  # §4.7 between every fixed and entity hue; R-52 between neighbouring accents
CVD_DISTANCE_MIN: Final = 0.05  # §4.7 worst simulated-dichromacy OKLab distance
TINT_CHROMA_MAX: Final[dict[Mode, float]] = {"dark": 0.07, "light": 0.03}  # §4.7 bg-tint; R-52 tint C <= 0.07
BG_LUMINANCE_RATIO_MAX: Final = 1.5  # R-55 behind-text luminance ratio of a background's colours
ACCENT_NUDGE_STEP: Final = 0.01
ACCENT_NUDGE_MAX: Final = 0.06  # §4.7: a failing structure pair is nudged by up to +-0.06 L
RED_SAFE_RATIO: Final = 0.75  # R-54: a lowered block ends at or below this (WCAG's 0.8 minus a margin for 8-bit rounding)
HOLD_DESATURATION: Final = 0.3  # R-55 hold freeze (mesh_drift.js FREEZE_DESATURATION)
# §7.5: JPEG 4:2:0 then x264 4:2:0 soften small saturated red or blue text, so such text needs L >= 0.6 or >= 32 px.
# "Saturated red or blue" is operationalised here as OKLCH C >= 0.08 with a hue in the red band [330°, 50°) (crimson
# to red-orange, both chroma-subsampling victims through Cr) or the blue band [220°, 330°) (blue to violet, through Cb).
CHROMA_RULE_MIN_L: Final = 0.6
CHROMA_RULE_MIN_PX: Final = 32
CHROMA_RULE_MIN_C: Final = 0.08
CHROMA_RULE_RED_HUES: Final = (330.0, 50.0)  # wraps through 0°
CHROMA_RULE_BLUE_HUES: Final = (220.0, 330.0)

# --- stage constants mirrored from the JS (parity-tested) ----------------------------------------------------------
ON_BLOCK: Final = ("#FFFFFF", "#14161C")  # core.js ON_BLOCK: white and near-black for text on a saturated block
BLOCK_INK2_CONTRAST: Final = 3.0  # stage.js BLOCK_INK2_CONTRAST
BLOCK_INK2_STEPS: Final = (0.25, 0.2, 0.15, 0.1, 0.05)  # stage.js blockInk2's mixes of ink toward the block
BLOCK_MIX_DARK: Final = 0.38  # colour_block.js blockColor: the accent deepened toward bg (dark families)
BLOCK_HIGHLIGHT_DARK: Final = 0.35  # colour_block.js blockColors: base toward the accent (dark)
BLOCK_HIGHLIGHT_LIGHT: Final = 0.1  # ... base toward white (light)
MESH_ACCENT_MIX: Final = 0.2  # mesh_drift.js: an accent tint of the tone
MESH_INK_MIX: Final = 0.07  # mesh_drift.js: a lift of the tone toward ink
SURFACE_FALLBACK_LIGHT: Final = 0.85  # core.js makeRoles: bg toward white (light) ...
SURFACE_FALLBACK_DARK: Final = 0.1  # ... or toward ink (dark), for a look without a surface token
PRESETS_MIRRORED: Final = ("mesh_drift", "colour_block")  # the B1 presets; dot_field, paper_grain, soft_waves and
# spotlight join the forced-scrim table when their JS lands (B2f), each with its colours mirrored like these two.

# The key order of the B1 tokens (a stored timeline's JSON keeps it).
CONTENT_ROLES: Final = ("ink", "ink2", "entity:0", "entity:1", "entity:2", "warn", "ok", "mark", "focus")
ENTITY_ROLES: Final = ("entity:0", "entity:1", "entity:2")
STRUCTURE_KEYS: Final = ("struct.accent", "bg", "bg-alt", "bg-tint", "surface")
DERIVED_KEYS: Final = ("block.base", "block.highlight", "block.ink", "block.ink2", "scrim")
SCENE_TOKEN_KEYS: Final = STRUCTURE_KEYS + DERIVED_KEYS
# R-53 minimum of each content role used as text or graphics on a background.
ROLE_CONTRAST: Final[dict[str, float]] = {
    "ink": CONTRAST_BODY, "ink2": CONTRAST_BODY, "entity:0": CONTRAST_BODY, "entity:1": CONTRAST_BODY,
    "entity:2": CONTRAST_BODY, "warn": CONTRAST_BODY, "ok": CONTRAST_BODY, "focus": CONTRAST_GRAPHICS,
}
TEXT_ROLES: Final = ("ink", "ink2", "entity:0", "entity:1", "entity:2", "warn", "ok")


def other_mode(mode: Mode) -> Mode:
    return "light" if mode == "dark" else "dark"


# --- content palettes (§4.7) ----------------------------------------------------------------------------------------

# ink / ink2 as OKLCH (L, C, H): the B1 definitions (§4.7 "L >= 0.96 / 0.78" dark, "L <= 0.22 / 0.42" light).
INK_LCH: Final[dict[Mode, tuple[tuple[float, float, float], tuple[float, float, float]]]] = {
    "dark": ((0.97, 0.006, 265.0), (0.80, 0.014, 265.0)),
    "light": ((0.21, 0.02, 265.0), (0.42, 0.02, 265.0)),
}


@dataclass(frozen=True)
class ContentPalette:
    """The fixed content colours of one mode (§4.7 table): upper-case ``#RRGGBB``."""

    mode: Mode
    ink: str
    ink2: str
    entities: tuple[str, str, str]
    warn: str
    ok: str
    mark: str

    @property
    def focus(self) -> str:
        """``cue.look_here`` outlines: ``mark`` in dark families, ``ink`` in light ones (§4.7)."""
        return self.mark if self.mode == "dark" else self.ink

    def tokens(self) -> dict[str, str]:
        """``TimelinePresentation.content``: the roles in B1's key order (ink, ink2, entity:0..2, warn, ok, mark,
        focus)."""
        return {"ink": self.ink, "ink2": self.ink2, "entity:0": self.entities[0], "entity:1": self.entities[1],
                "entity:2": self.entities[2], "warn": self.warn, "ok": self.ok, "mark": self.mark,
                "focus": self.focus}


def _content(mode: Mode, entities: tuple[str, str, str], warn: str, ok: str, mark: str) -> ContentPalette:
    ink, ink2 = (cm.palette_hex(*lch) for lch in INK_LCH[mode])
    return ContentPalette(mode, ink, ink2, entities, warn, ok, mark)


CONTENT: Final[dict[Mode, ContentPalette]] = {
    "dark": _content("dark", ("#0FE3FE", "#C4A4FE", "#FEACE3"), "#F8A87F", "#5DD8AA", "#F2D935"),
    "light": _content("light", ("#0267C7", "#80369E", "#870C62"), "#9F4503", "#016F50", "#F2D935"),
}


def content_tokens(mode: Mode) -> dict[str, str]:
    """``TimelinePresentation.content`` of a mode (a new dict; B1's ``presentation.content_tokens``)."""
    return CONTENT[mode].tokens()


def content_alt_tokens(mode: Mode) -> dict[str, str]:
    """``TimelinePresentation.content_alt`` (plan §1.3): the other mode's palette, for text on the dark code-editor
    surface of a light family (and, symmetrically, a light surface in a dark one)."""
    return CONTENT[other_mode(mode)].tokens()


@dataclass(frozen=True)
class CueColours:
    """``cue.term`` colours of a mode (§4.8, plan §1.5): the marker sweep is ``mark`` at ``marker_alpha`` with glyphs in
    ``marker_ink`` where it has swept (dark families: the dark on-block ink, never light-on-yellow); the underline is
    ``underline``, 4 px."""

    marker_ink: str
    marker_alpha: float
    underline: str


CUE_COLOURS: Final[dict[Mode, CueColours]] = {
    "dark": CueColours(marker_ink=ON_BLOCK[1], marker_alpha=1.0, underline=CONTENT["dark"].mark),
    "light": CueColours(marker_ink=CONTENT["light"].ink, marker_alpha=0.35, underline=CONTENT["light"].ink),
}


# --- families (§4.7) ------------------------------------------------------------------------------------------------

# Lightness of bg, bg-alt, bg-tint and surface per mode (B1 values; §4.7 bands: dark bg 0.17-0.24, bg-alt +0.04,
# surface 0.27-0.32; light bg 0.95-0.98, bg-alt -0.03, surface 0.99-1.00).
TONE_L: Final[dict[Mode, tuple[float, float, float, float]]] = {
    "dark": (0.20, 0.24, 0.21, 0.29),
    "light": (0.965, 0.935, 0.955, 0.995),
}
ACCENT_LC: Final[dict[Mode, tuple[float, float]]] = {"dark": (0.77, 0.16), "light": (0.56, 0.17)}  # §4.7 struct.accent
TINT_C: Final[dict[Mode, float]] = {"dark": 0.035, "light": 0.028}
SURFACE_C_MAX_DARK: Final = 0.04  # dark surface chroma: min(0.04, the family's)
SURFACE_C_LIGHT: Final = 0.004
ACCENT_STEPS: Final = (0.0, 1.0, 0.5)  # §4.7 step 4: ends and middle of the window alternate


@dataclass(frozen=True)
class Family:
    """One theme family (§4.7 table). The first four fields are B1's ``presentation.Family`` (positional, same
    meaning); the rest are A1 additions with keyword defaults."""

    mode: Mode
    hue: float  # bg hue, OKLCH degrees
    accent: tuple[float, float]  # accent window (h0 range), degrees
    chroma: float = 0.045  # bg chroma
    id: str = ""
    name: str = ""
    release: Release = "W2"
    personality: str = "soft"  # the default personality in the family's release (R1 maps W2 ones onto Crisp/Soft)
    personality_target: str = "soft"  # the design's default personality (§4.7 table)
    accent_alt: tuple[float, float] | None = None  # a second window, alternated with the first (Cosmic, Blueprint)
    tones: tuple[float, float, float, float] | None = None  # L of bg, bg-alt, bg-tint, surface; None: TONE_L[mode]
    note: str = ""

    @property
    def windows(self) -> tuple[tuple[float, float], ...]:
        return (self.accent,) if self.accent_alt is None else (self.accent, self.accent_alt)

    @property
    def accent_period(self) -> int:
        """Slots after which the accent sequence repeats (3 per window)."""
        return len(ACCENT_STEPS) * len(self.windows)


def _f(fid: str, name: str, mode: Mode, hue: float, accent: tuple[float, float], chroma: float, *, release: Release,
       personality: str, target: str, **extra: object) -> Family:
    return Family(mode, hue, accent, chroma, id=fid, name=name, release=release, personality=personality,
                  personality_target=target, **extra)  # type: ignore[arg-type]


# §4.7 table order. The four R1 rows keep B1's numbers exactly (the B1 tokens are pinned by the tests).
FAMILIES: Final[dict[str, Family]] = {f.id: f for f in (
    _f("midnight_indigo", "Midnight Indigo", "dark", 275.0, (40.0, 110.0), 0.05, release="R1", personality="soft",
       target="cinematic", note="Cinematic is W2: Soft in R1"),
    _f("deep_teal", "Deep Teal", "dark", 200.0, (0.0, 70.0), 0.045, release="R1", personality="crisp",
       target="crisp"),
    _f("aubergine", "Aubergine", "dark", 320.0, (140.0, 210.0), 0.045, release="W2", personality="cinematic",
       target="cinematic"),
    _f("charcoal_math", "Charcoal Math", "dark", 260.0, (200.0, 280.0), 0.01, release="W2", personality="chalk",
       target="chalk", note="near-neutral bg (C ~ 0.01, §4.7)"),
    _f("forest_night", "Forest Night", "dark", 155.0, (20.0, 90.0), 0.045, release="W2", personality="soft",
       target="soft"),
    _f("cosmic", "Cosmic", "dark", 285.0, (30.0, 70.0), 0.07, release="W2", personality="cinematic",
       target="cinematic", accent_alt=(170.0, 210.0), note="bg C 0.07 (§4.7 table) is above the 0.02-0.06 band"),
    _f("paper_ink", "Paper & Ink", "light", 85.0, (0.0, 70.0), 0.03, release="R1", personality="crisp",
       target="editorial", note="Editorial is W2: Crisp in R1"),
    _f("blueprint", "Blueprint", "light", 230.0, (230.0, 290.0), 0.03, release="W2", personality="crisp",
       target="crisp", accent_alt=(30.0, 70.0), note="the grid itself is a W2 background (blueprint_grid)"),
    _f("mint_studio", "Mint Studio", "light", 170.0, (270.0, 340.0), 0.025, release="R1", personality="soft",
       target="soft"),
    _f("sunrise", "Sunrise", "light", 60.0, (260.0, 330.0), 0.03, release="W2", personality="bouncy_soft",
       target="bouncy_soft"),
    _f("chalkboard", "Chalkboard", "dark", 150.0, (60.0, 130.0), 0.045, release="W2", personality="chalk",
       target="chalk", tones=(0.28, 0.32, 0.29, 0.37),
       note="bg L 0.28 (§4.7 table); surface lifted to 0.37, above the 0.27-0.32 band, to stay distinct from bg"),
    _f("bold_blocks", "Bold Blocks", "dark", 265.0, (0.0, 300.0), 0.03, release="W2", personality="crisp",
       target="crisp", note="PROVISIONAL: §4.7 says 'any' hue and accent; this stand-in (neutral blue-grey bg, a "
                            "300° window) is to be decided with its batch"),
)}
RELEASED_FAMILIES: Final[dict[str, Family]] = {k: f for k, f in FAMILIES.items() if f.release == "R1"}


def accent_hue(family: Family, slot: int) -> float:
    """``h0 + Δk`` of palette slot ``slot`` (§4.7 rotation step 4; B1's ``presentation.accent_hue``): one window's ends
    and middle alternate (steps 0, 1, 0.5), so neighbours differ by >= 30° in every 70°+ window; a family with two
    windows alternates between them first (slot k uses window k % 2 at step (k // 2) % 3)."""
    windows = family.windows
    lo, hi = windows[slot % len(windows)]
    step = ACCENT_STEPS[(slot // len(windows)) % len(ACCENT_STEPS)]
    return (lo + (hi - lo) * step) % 360.0


def structure_tokens(family: Family, accent: float, *, accent_shift: float = 0.0) -> dict[str, str]:
    """The anchor structure tokens of a scene (§4.7; B1's ``presentation.structure_tokens``): ``struct.accent`` at
    ``accent`` degrees (its lightness shifted by ``accent_shift``, the validator's nudge), ``bg``, ``bg-alt``,
    ``bg-tint`` (at the accent hue) and the per-video ``surface``. Upper-case ``#RRGGBB``, B1's key order."""
    bg_l, alt_l, tint_l, surface_l = family.tones or TONE_L[family.mode]
    acc_l, acc_c = ACCENT_LC[family.mode]
    h = family.hue
    surface_c = min(SURFACE_C_MAX_DARK, family.chroma) if family.mode == "dark" else SURFACE_C_LIGHT
    return {
        "struct.accent": cm.palette_hex(acc_l + accent_shift if accent_shift else acc_l, acc_c, accent),
        "bg": cm.palette_hex(bg_l, family.chroma, h),
        "bg-alt": cm.palette_hex(alt_l, family.chroma, h),
        "bg-tint": cm.palette_hex(tint_l, TINT_C[family.mode], accent),
        "surface": cm.palette_hex(surface_l, surface_c, h),
    }


def code_surface(family: Family) -> str:
    """The code editor's surface (§4.3 ``code_walkthrough``: "a dark surface in every family"): the family's own
    surface in a dark family; in a light one the dark mode's surface rule at the family's hue (L 0.29, C <= 0.04).
    Its text uses ``content_alt``."""
    if family.mode == "dark":
        return structure_tokens(family, accent_hue(family, 0))["surface"]
    return cm.palette_hex(TONE_L["dark"][3], min(SURFACE_C_MAX_DARK, family.chroma), family.hue)


# --- stage mirrors (parity-tested against the JS through tests/fixtures/colour_vectors.json) ----------------------
# Each returns exactly the string the JS returns (mixes are lower-case hex; tokens keep their spelling).


def tone_colour(tokens: Mapping[str, str], tone: object) -> str:
    """core.js ``toneColor``: the scene's background tone (bg, bg-alt or bg-tint; anything else is bg)."""
    key = tone if tone in ("bg-alt", "bg-tint") else "bg"
    return tokens.get(key) or tokens["bg"]  # type: ignore[call-overload]


def block_colours(mode: Mode, tokens: Mapping[str, str]) -> tuple[str, str]:
    """colour_block.js ``blockColors``: the block's base (the accent deepened toward bg in dark families, the accent
    itself in light ones) and its highlight (toward the accent, or toward white), on one side of the light/dark
    divide so one text colour holds over both."""
    accent = tokens["struct.accent"]
    base = cm.mix_oklch(accent, tokens.get("bg") or "#000000", BLOCK_MIX_DARK) if mode == "dark" else accent
    highlight = (cm.mix_oklch(base, accent, BLOCK_HIGHLIGHT_DARK) if mode == "dark"
                 else cm.mix_oklch(base, "#FFFFFF", BLOCK_HIGHLIGHT_LIGHT))
    return base, highlight


def ink_on(ink: str, bgs: str | Sequence[str]) -> str:
    """core.js ``inkOn``: of ``ink``, white and near-black, the one whose worst contrast over ``bgs`` is best (ties
    keep the earlier option)."""
    colours = [bgs] if isinstance(bgs, str) else list(bgs)

    def worst(c: str) -> float:
        return min(cm.contrast_ratio(c, b) for b in colours)

    options = [ink, *ON_BLOCK]
    best = options[0]
    for c in options:
        if worst(c) > worst(best):
            best = c
    return best


def block_ink2(ink: str, bgs: Sequence[str]) -> str:
    """stage.js ``blockInk2``: the quieter ink on a block, the strongest mix of ``ink`` toward ``bgs[0]`` (at most
    25%) that still holds 3:1 over every block colour, else ``ink`` itself."""
    for p in BLOCK_INK2_STEPS:
        c = cm.mix_oklch(ink, bgs[0], p)
        if min(cm.contrast_ratio(c, b) for b in bgs) >= BLOCK_INK2_CONTRAST:
            return c
    return ink


def mesh_drift_colours(tokens: Mapping[str, str], tone: object, ink: str) -> list[str]:
    """mesh_drift.js ``colors()``: the tone, then its blob colours (an accent tint of the tone, bg-alt, bg-tint and a
    lift toward ink)."""
    base = tone_colour(tokens, tone)
    return [base, cm.mix_oklch(base, tokens["struct.accent"], MESH_ACCENT_MIX), tokens.get("bg-alt") or base,
            tokens.get("bg-tint") or base, cm.mix_oklch(base, ink, MESH_INK_MIX)]


def derived_surface(mode: Mode, bg: str, ink: str) -> str:
    """core.js ``makeRoles``' surface for a look without one: bg toward white (light) or toward ink (dark)."""
    return cm.mix_oklch(bg, "#FFFFFF", SURFACE_FALLBACK_LIGHT) if mode == "light" else cm.mix_oklch(
        bg, ink, SURFACE_FALLBACK_DARK)


def scrim_colour(bg: str | None) -> str:
    """board_generic.js: the title block's scrim, the scene's bg at alpha ``SCRIM_ALPHA_HEX`` (``#000000`` without a
    bg), spelled as the stage writes it."""
    return cm.with_alpha(bg or "#000000", SCRIM_ALPHA_HEX)


def scrimmed_bg(scrim: str, bgs: Sequence[str], ink: str) -> str:
    """board_generic.js ``scrimmedBg``: the scrim composited over each background colour, the one with the least
    contrast against ``ink`` (the first on ties); opaque lower-case hex."""
    worst = cm.composite(scrim, bgs[0])
    low = cm.contrast_ratio(ink, worst)
    for b in bgs[1:]:
        c = cm.composite(scrim, b)
        ratio = cm.contrast_ratio(ink, c)
        if ratio < low:
            low, worst = ratio, c
    return worst


# --- derived scene colours (stored by the resolver, plan §1.2) -----------------------------------------------------


def _in_red_band(colour: str) -> bool:
    return cm.red_ratio(colour) >= cm.SATURATED_RED_RATIO


def lower_red(colours: Sequence[str]) -> tuple[tuple[str, ...], float]:
    """R-54 for a full-frame block: when any of ``colours`` is a saturated red (WCAG red ratio >= 0.8), every colour's
    OKLCH chroma is scaled by the largest factor (24-step bisection) that brings each to <= ``RED_SAFE_RATIO``,
    lightness and hue kept. Returns the (lower-case hex) colours and the factor (1.0: unchanged, colours as given)."""
    if not any(_in_red_band(c) for c in colours):
        return tuple(colours), 1.0
    lch = [cm.to_oklch(c) for c in colours]

    def scaled(s: float) -> tuple[str, ...]:
        return tuple(cm.from_oklch(v.l, v.c * s, v.h) for v in lch)

    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if all(cm.red_ratio(c) <= RED_SAFE_RATIO for c in scaled(mid)):
            lo = mid
        else:
            hi = mid
    return scaled(lo), lo


@dataclass(frozen=True)
class SceneColours:
    """Every structure colour of one scene (family, palette slot), as the resolver stores them (upper-case hex).

    ``tokens`` holds :data:`SCENE_TOKEN_KEYS`: B1's five anchors (``struct.accent`` possibly nudged) and the derived
    ``block.base``/``block.highlight`` (red-lowered when needed), ``block.ink``/``block.ink2`` (the text on the block)
    and ``scrim`` (``#RRGGBBC7``, the bg at the stage's scrim alpha). ``accent_shift`` is the validator's L nudge,
    ``red_factor`` the R-54 chroma factor (1.0: none), ``block_ok`` whether the block text holds 3:1."""

    family: str
    slot: int
    accent_hue: float
    tokens: dict[str, str]
    accent_shift: float = 0.0
    red_factor: float = 1.0
    block_ok: bool = True


def _block_set(mode: Mode, tokens: Mapping[str, str], ink: str) -> tuple[tuple[str, str], float, str, str, float]:
    """(lowered base/highlight, red factor, block ink, block ink2, worst block-text contrast).

    The text colours are stage.js's choice (``inkOn``, then ``blockInk2`` mixing toward the base) made over the block's
    colours both as shown and fully held (desaturated by 30%, R-55; colour_block.js desaturates during a hold), a
    superset of the stage's run-time criterion: the stored ink keeps 3:1 through a hold too. Where the hold changes
    nothing the result is the stage's own."""
    (base, highlight), factor = lower_red(block_colours(mode, tokens))
    colours = [base, highlight]
    shown = colours + [cm.desaturate(c, HOLD_DESATURATION) for c in colours]
    text = ink_on(ink, shown)
    text2 = block_ink2(text, shown)
    worst = min(cm.worst_contrast(text, shown), cm.worst_contrast(text2, shown))
    return (base, highlight), factor, text, text2, worst


def _nudges() -> list[float]:
    out = [0.0]
    for i in range(1, round(ACCENT_NUDGE_MAX / ACCENT_NUDGE_STEP) + 1):
        d = round(i * ACCENT_NUDGE_STEP, 4)
        out += [d, -d]
    return out


@functools.lru_cache(maxsize=512)
def _scene_colours(family: Family, slot: int) -> SceneColours:
    hue = accent_hue(family, slot)
    ink = CONTENT[family.mode].ink
    first: tuple[float, dict[str, str], tuple] | None = None
    for shift in _nudges():
        tokens = structure_tokens(family, hue, accent_shift=shift)
        block = _block_set(family.mode, tokens, ink)
        if first is None:
            first = (shift, tokens, block)
        if block[4] >= CONTRAST_LARGE:
            break
    else:  # no nudge within +-0.06 L holds: keep the un-nudged colours; the validator reports block_text
        shift, tokens, block = first  # type: ignore[misc]
    (base, highlight), factor, text, text2, worst = block
    out = dict(tokens)
    out.update({"block.base": base.upper(), "block.highlight": highlight.upper(), "block.ink": text.upper(),
                "block.ink2": text2.upper(), "scrim": scrim_colour(tokens["bg"]).upper()})
    return SceneColours(family.id, slot, hue, out, shift, factor, worst >= CONTRAST_LARGE)


def scene_colours(family: Family, slot: int) -> SceneColours:
    """The structure colours of palette slot ``slot`` of ``family`` (cached; the token dict is a copy). The tone
    variant (bg / bg-alt / bg-tint) changes no token: it picks which one the background paints
    (:func:`preset_colours`)."""
    sc = _scene_colours(family, slot)
    return SceneColours(sc.family, sc.slot, sc.accent_hue, dict(sc.tokens), sc.accent_shift, sc.red_factor,
                        sc.block_ok)


def scene_tokens(family: Family, slot: int) -> dict[str, str]:
    """``scene_colours(family, slot).tokens``: :data:`SCENE_TOKEN_KEYS`, upper-case hex."""
    return scene_colours(family, slot).tokens


def preset_colours(preset: str, family: Family, slot: int, tone: Tone = "bg", *, hold: bool = False) -> list[str]:
    """What a background preset shows behind text (its ``colors()``), for the scene's stored tokens: ``mesh_drift``
    mirrors mesh_drift.js on the anchors, ``colour_block`` is the stored (red-lowered) block. ``hold``: as fully frozen
    (desaturated by 30%, R-55). ``ValueError`` for a preset not mirrored yet (:data:`PRESETS_MIRRORED`)."""
    tokens = _scene_colours(family, slot).tokens
    if preset == "mesh_drift":
        colours = mesh_drift_colours(tokens, tone, CONTENT[family.mode].ink)
    elif preset == "colour_block":
        colours = [tokens["block.base"], tokens["block.highlight"]]
    else:
        raise ValueError(f"preset colours not mirrored yet: {preset!r}")
    return [cm.desaturate(c, HOLD_DESATURATION) for c in colours] if hold else colours


def chroma_restricted(mode: Mode) -> tuple[str, ...]:
    """§7.5: the content roles of ``mode`` that are saturated red or blue below L 0.6, i.e. may only be drawn at >= 32
    px (constants ``CHROMA_RULE_*``)."""
    out = []
    for role, hexv in content_tokens(mode).items():
        v = cm.to_oklch(hexv)
        lo, hi = CHROMA_RULE_RED_HUES
        red = v.h >= lo or v.h < hi
        blue = CHROMA_RULE_BLUE_HUES[0] <= v.h < CHROMA_RULE_BLUE_HUES[1]
        if v.c >= CHROMA_RULE_MIN_C and (red or blue) and v.l < CHROMA_RULE_MIN_L and role not in out:
            out.append(role)
    return tuple(out)


# --- personalities (§4.8) ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Personality:
    """A motion personality (§4.8 table). ``caption_style`` per format; ``transitions`` is the design's family,
    ``transitions_r1`` what a released personality uses in R1 (plan §2 R-54: iris is W2, so Soft is cut/crossfade).
    ``sfx_pack`` and ``music_mood`` are names for B3."""

    id: str
    name: str
    release: Release
    duration_scale: float
    text_entrance: str
    transitions: tuple[str, ...]
    cue_term_style: Literal["marker", "underline"]
    caption_style: Mapping[Format, str]
    sfx_pack: str
    music_mood: str
    transitions_r1: tuple[str, ...] = ()


PERSONALITIES: Final[dict[str, Personality]] = {p.id: p for p in (
    Personality("crisp", "Crisp", "R1", 0.85, "baseline_reveal", ("cut", "push", "colour_wipe"), "underline",
                {"landscape": "clean_pill", "portrait": "bold_outline"}, "clean_short", "bright-curious",
                transitions_r1=("cut", "push", "colour_wipe")),
    Personality("soft", "Soft", "R1", 1.15, "fade_rise", ("crossfade", "iris"), "marker",
                {"landscape": "clean_pill", "portrait": "clean_pill"}, "soft_bells", "calm-focus",
                transitions_r1=("cut", "crossfade")),
    Personality("cinematic", "Cinematic", "W2", 1.25, "opacity_rise_tracking", ("crossfade", "zoom_through"), "marker",
                {"landscape": "glow_outline", "portrait": "glow_outline"}, "deep_sub_hit", "cinematic-wonder"),
    Personality("editorial", "Editorial", "W2", 1.0, "highlighter_swipe", ("cut", "paper_slide"), "marker",
                {"landscape": "marker", "portrait": "marker"}, "paper_pen", "classical-focus"),
    Personality("chalk", "Chalk", "W2", 1.1, "stroke_draw", ("cut", "wipe"), "underline",
                {"landscape": "minimal_lower", "portrait": "minimal_lower"}, "soft_chalk", "calm-focus"),
    Personality("bouncy_soft", "Bouncy-soft", "W2", 0.9, "pop_overshoot", ("push", "iris"), "marker",
                {"landscape": "bold_outline", "portrait": "bold_outline"}, "soft_pops", "bright-curious"),
)}
TEXT_ENTRANCE_RISE_PX: Final = 16  # Soft: "fade + 16 px rise"


# --- caption styles (§4.11) -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptionMetrics:
    """Geometry and colours of a caption style, as ``web/css/dynamic.css`` draws it (pinned by a test that reads the
    stylesheet). ``line_height`` per format and script class: the latin values are the stylesheet's today; the indic
    ones are plan §2's >= 1.35, which the stage adopts with B2's caption work (the stylesheet has no script rule yet)."""

    padding_x: int
    padding_y: int
    radius_px: int
    outline_px: int  # outside the glyph (bold_outline: a 12 px stroke centred on the outline, the fill covers half)
    word_spacing_em: float
    size: Mapping[Format, int]
    weight: Mapping[Format, int]
    line_height: Mapping[Format, Mapping[ScriptClass, float]]
    ink: str
    backdrop: str | None  # the pill colour (clean_pill), drawn at backdrop_alpha
    backdrop_alpha: float
    outline: str | None  # the outline colour (bold_outline)
    key_ink: str  # key terms (R-35): captions.js CAPTION_MARK


@dataclass(frozen=True)
class CaptionStyle:
    id: str
    release: Release
    look: str
    metrics: CaptionMetrics | None  # None: not drawn by the stage yet (W2)


CAPTION_INK: Final = "#FFFFFF"  # captions.js CAPTION_INK
CAPTION_PILL: Final = "#0B0D14"  # captions.js CAPTION_PILL; dynamic.css rgba(11, 13, 20, 0.78)
CAPTION_PILL_ALPHA: Final = 0.78
CAPTION_MARK: Final = "#F2D935"  # captions.js CAPTION_MARK (= mark)
_CAPTION_SIZE: Final[dict[Format, int]] = {"landscape": 48, "portrait": 56}
_CAPTION_LH: Final[dict[Format, dict[ScriptClass, float]]] = {
    "landscape": {"latin": 1.08, "indic": 1.35}, "portrait": {"latin": 1.15, "indic": 1.35}}

CAPTION_STYLES: Final[dict[str, CaptionStyle]] = {s.id: s for s in (
    CaptionStyle("clean_pill", "R1", "sentence chunks; white on a translucent dark pill", CaptionMetrics(
        padding_x=24, padding_y=2, radius_px=18, outline_px=0, word_spacing_em=0.0, size=_CAPTION_SIZE,
        weight={"landscape": 600, "portrait": 700}, line_height=_CAPTION_LH, ink=CAPTION_INK, backdrop=CAPTION_PILL,
        backdrop_alpha=CAPTION_PILL_ALPHA, outline=None, key_ink=CAPTION_MARK)),
    CaptionStyle("bold_outline", "R1", "weight 700, white with a 6 px dark outline", CaptionMetrics(
        padding_x=0, padding_y=0, radius_px=0, outline_px=6, word_spacing_em=0.16, size=_CAPTION_SIZE,
        weight={"landscape": 700, "portrait": 700}, line_height=_CAPTION_LH, ink=CAPTION_INK, backdrop=None,
        backdrop_alpha=0.0, outline=CAPTION_PILL, key_ink=CAPTION_MARK)),
    CaptionStyle("highlight_box", "W2", "key terms boxed in mark", None),
    CaptionStyle("marker", "W2", "key terms get a mark swipe", None),
    CaptionStyle("minimal_lower", "W2", "small phrase chunks low in the band", None),
    CaptionStyle("glow_outline", "W2", "soft outer glow (pre-computed text shadow, no blur filter)", None),
)}


# --- faces (§4.6) ---------------------------------------------------------------------------------------------------

FaceRole = Literal["display", "body", "mono", "indic"]
_OFL: Final = "OFL-1.1"
_OFL_VERIFY: Final = "OFL-1.1 (believed; verify the LICENSE file when vendoring, §4.6)"


@dataclass(frozen=True)
class Face:
    """A type face (§4.6). ``vendored``: under web/vendor today (B1); B2b vendors the rest of R1 with LICENSE files."""

    id: str
    family: str  # CSS font-family
    role: FaceRole
    release: Release
    vendored: bool
    licence: str
    note: str = ""


FACES: Final[dict[str, Face]] = {f.id: f for f in (
    Face("outfit", "Outfit", "display", "R1", True, _OFL),
    Face("poppins", "Poppins", "display", "R1", False, _OFL_VERIFY),
    Face("lexend", "Lexend", "display", "R1", False, _OFL_VERIFY),
    Face("montserrat", "Montserrat", "display", "W2", False, _OFL_VERIFY),
    Face("sora", "Sora", "display", "W2", False, _OFL_VERIFY),
    Face("space_grotesk", "Space Grotesk", "display", "W2", False, _OFL_VERIFY),
    Face("bricolage_grotesque", "Bricolage Grotesque", "display", "W2", False, _OFL_VERIFY),
    Face("baloo_2", "Baloo 2", "display", "W2", False, _OFL_VERIFY),
    Face("fraunces", "Fraunces", "display", "W2", False, _OFL_VERIFY, "Editorial"),
    Face("dm_serif_display", "DM Serif Display", "display", "W2", False, _OFL_VERIFY, "Editorial"),
    Face("inter", "Inter", "body", "R1", True, _OFL, "also accepted as a display id by the B1 stage"),
    Face("atkinson_hyperlegible_next", "Atkinson Hyperlegible Next", "body", "R1", False,
         "OFL-1.1 (verified [CP S57])", "the 'High legibility' body option"),
    Face("jetbrains_mono", "JetBrains Mono", "mono", "R1", True, _OFL),
    Face("anek", "Anek", "indic", "R1", False, "OFL-1.1 (verified [CP S58])",
         "variable; 9 Indic scripts + Latin; rotation varies its axes, not the face"),
    Face("noto_sans_tamil", "Noto Sans Tamil", "indic", "R1", True, _OFL, "B1 Indic face"),
    Face("noto_sans_devanagari", "Noto Sans Devanagari", "indic", "R1", True, _OFL, "B1 Indic face"),
    Face("noto_sans_telugu", "Noto Sans Telugu", "indic", "R1", True, _OFL, "B1 Indic face"),
    Face("noto_sans_kannada", "Noto Sans Kannada", "indic", "R1", True, _OFL, "B1 Indic face"),
    Face("noto_sans_malayalam", "Noto Sans Malayalam", "indic", "R1", True, _OFL, "B1 Indic face"),
)}
DISPLAY_FACES_R1: Final = tuple(f.id for f in FACES.values() if f.role == "display" and f.release == "R1")

# The Aadhi studio wall (§3.3, §4.7 validator): B2f measures its luminance over the 192 intro frames
# (scripts/measure_studio_wall.py) and writes the worst-case colour here. Until then there is no value, and the
# validator's studio check runs only when a wall colour is passed to validate(studio=...). Never a guessed value.
STUDIO_WALL: str | None = None


# --- the validator ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """A failed check. ``release`` is the release of what failed (the content palette and released families: R1)."""

    code: str
    subject: str
    measured: float
    required: float
    family: str | None = None
    release: Release = "R1"


@dataclass(frozen=True)
class Measure:
    """The worst measured value of a check over one scope (a mode or a family) and where it occurs."""

    code: str
    scope: str
    value: float
    required: float
    subject: str
    higher_is_better: bool = True


# Checks whose failures are reported as advisories, not findings, because the thing that fails is owned by a later
# step: R-55's behind-text luminance ratio depends on how a preset composes its colours (mesh_drift.js draws blobs at
# up to 85% alpha, overlapping), which the backgrounds work (plan B2f, "R-55 numeric tests per preset") owns. The
# measure here is the conservative bound over the preset's pure colours.
ADVISORY_CODES: Final = frozenset({"bg_luminance_ratio"})


@dataclass(frozen=True)
class Report:
    findings: tuple[Finding, ...]  # build failures when on R1 (released families, the content palette)
    advisories: tuple[Finding, ...]  # failures of ADVISORY_CODES (owned by a later step; reported, never blocking)
    measures: tuple[Measure, ...]
    scrims: dict[str, dict[str, bool]]  # {preset: {family: forced}} (§4.5 forced-scrim table)
    scrim_roles: dict[str, dict[str, tuple[str, ...]]]  # {preset: {family: text roles that need the scrim}}
    nudges: dict[str, dict[int, float]]  # {family: {slot: accent L shift}} (non-zero only)
    red_lowered: dict[str, dict[int, float]]  # {family: {slot: chroma factor}} (lowered blocks only)
    chroma_restricted: dict[str, tuple[str, ...]]  # {mode: roles restricted to >= 32 px (§7.5)}

    def released_findings(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.release == "R1")

    def worst(self, code: str, scope: str) -> Measure:
        for m in self.measures:
            if m.code == code and m.scope == scope:
                return m
        raise KeyError((code, scope))


@dataclass
class _Collector:
    findings: list[Finding] = field(default_factory=list)
    worst: dict[tuple[str, str], Measure] = field(default_factory=dict)

    def check(self, code: str, scope: str, subject: str, value: float, required: float, *, family: str | None = None,
              release: Release = "R1", higher_is_better: bool = True) -> None:
        key = (code, scope)
        prev = self.worst.get(key)
        worse = prev is None or (value < prev.value if higher_is_better else value > prev.value)
        if worse:
            self.worst[key] = Measure(code, scope, value, required, subject, higher_is_better)
        failed = value < required if higher_is_better else value > required
        if failed:
            self.findings.append(Finding(code, subject, value, required, family, release))


def _palette_backgrounds(family: Family, slot: int) -> dict[str, str]:
    t = _scene_colours(family, slot).tokens
    return {k: t[k] for k in ("bg", "bg-alt", "bg-tint", "surface")}


def _over(fg: str, alpha: float) -> tuple[float, float, float, float]:
    r, g, b, _ = cm.parse_hex(fg)  # type: ignore[misc]
    return r, g, b, alpha


def _validate_content(col: _Collector) -> None:
    """Mode-level checks of the fixed content palettes (§4.7): hue separation, colour vision, marker ink on mark."""
    for mode in MODES:
        p = content_tokens(mode)
        hued = {r: p[r] for r in (*ENTITY_ROLES, "warn", "ok", "mark")}
        for (ra, a), (rb, b) in itertools.combinations(hued.items(), 2):
            if ra not in ENTITY_ROLES and rb not in ENTITY_ROLES:
                continue  # fixed vs fixed hues are not constrained (§4.7: entity vs fixed and entity vs entity)
            d = cm.hue_distance(cm.to_oklch(a).h, cm.to_oklch(b).h)
            col.check("hue_separation", mode, f"{mode} {ra} vs {rb}", d, HUE_SEPARATION_MIN)
        for kind in cm.CVD_KINDS:
            for ra, rb in itertools.combinations(ENTITY_ROLES, 2):
                d = cm.oklab_distance(cm.simulate_cvd(p[ra], kind), cm.simulate_cvd(p[rb], kind))
                col.check("cvd_entities", mode, f"{mode} {kind}: {ra} vs {rb}", d, CVD_DISTANCE_MIN)
            for ra in ENTITY_ROLES:
                for rb in ("warn", "ok", "mark"):
                    d = cm.oklab_distance(cm.simulate_cvd(p[ra], kind), cm.simulate_cvd(p[rb], kind))
                    col.check("cvd_entity_fixed", mode, f"{mode} {kind}: {ra} vs {rb}", d, CVD_DISTANCE_MIN)
        if mode == "dark":
            cue = CUE_COLOURS[mode]
            col.check("marker", mode, f"{mode} marker ink {cue.marker_ink} on mark",
                      cm.contrast_ratio(cue.marker_ink, p["mark"]), CONTRAST_MARKER)
    # captions: bold_outline's ink (and key-term ink) on its own outline does not depend on the family
    outline = CAPTION_STYLES["bold_outline"].metrics
    assert outline is not None and outline.outline is not None
    col.check("caption", "bold_outline", "bold_outline ink on its outline",
              cm.contrast_ratio(outline.ink, outline.outline), CONTRAST_CAPTION)
    col.check("caption", "bold_outline", "bold_outline key ink on its outline",
              cm.contrast_ratio(outline.key_ink, outline.outline), CONTRAST_CAPTION)


def _validate_family(col: _Collector, family: Family, scrims: dict[str, dict[str, bool]],
                     scrim_roles: dict[str, dict[str, tuple[str, ...]]], nudges: dict[str, dict[int, float]],
                     red: dict[str, dict[int, float]], studio: str | None) -> None:
    fid, rel = family.id, family.release
    mode = family.mode
    p = content_tokens(mode)
    cue = CUE_COLOURS[mode]
    pill = CAPTION_STYLES["clean_pill"].metrics
    assert pill is not None and pill.backdrop is not None

    def check(code: str, subject: str, value: float, required: float, higher: bool = True) -> None:
        col.check(code, fid, f"{fid} {subject}", value, required, family=fid, release=rel, higher_is_better=higher)

    # R-52: neighbouring accents >= 30° apart (two periods, so the wrap-around pair is seen too)
    hues = [accent_hue(family, k) for k in range(2 * family.accent_period + 1)]
    for k, (a, b) in enumerate(itertools.pairwise(hues)):
        check("accent_rotation", f"slot {k} -> {k + 1}", cm.hue_distance(a, b), HUE_SEPARATION_MIN)

    mesh_roles: set[str] = set()
    block_forced = False
    for slot in range(family.accent_period):
        sc = _scene_colours(family, slot)
        t = sc.tokens
        if sc.accent_shift:
            nudges.setdefault(fid, {})[slot] = sc.accent_shift
        if sc.red_factor < 1.0:
            red.setdefault(fid, {})[slot] = sc.red_factor
        backgrounds = _palette_backgrounds(family, slot)
        # R-53 / §4.7: every content role on every palette background (ink: the palette's 7:1)
        for role, need in ROLE_CONTRAST.items():
            required = CONTRAST_INK_PALETTE if role == "ink" else need
            for name, bg in backgrounds.items():
                check("content_contrast", f"/{slot} {role} on {name}", cm.contrast_ratio(p[role], bg), required)
        for name, bg in backgrounds.items():
            # dimmed groups (cue.look_here) and cue colours
            check("dim", f"/{slot} ink at {DIM_ALPHA:.0%} on {name}",
                  cm.contrast_ratio(_over(p["ink"], DIM_ALPHA), bg), DIM_INK_CONTRAST)
            check("dim", f"/{slot} ink2 at {DIM_ALPHA:.0%} on {name}",
                  cm.contrast_ratio(_over(p["ink2"], DIM_ALPHA), bg), DIM_INK2_CONTRAST)
            check("underline", f"/{slot} underline on {name}", cm.contrast_ratio(cue.underline, bg),
                  CONTRAST_GRAPHICS)
            if cue.marker_alpha < 1.0:
                marked = cm.source_over(_over(p["mark"], cue.marker_alpha), cm.parse_hex(bg))  # type: ignore[arg-type]
                check("marker", f"/{slot} {cue.marker_ink} on mark at {cue.marker_alpha:.0%} over {name}",
                      cm.contrast_ratio(cue.marker_ink, marked), CONTRAST_MARKER)
            # struct.accent as a graphic (pips on bg, card borders on surface)
            check("accent_graphic", f"/{slot} struct.accent on {name}", cm.contrast_ratio(t["struct.accent"], bg),
                  CONTRAST_GRAPHICS)
        # §4.7 tint chroma (R-52: C <= 0.07 on content scenes)
        check("tint_chroma", f"/{slot} bg-tint chroma", cm.to_oklch(t["bg-tint"]).c, TINT_CHROMA_MAX[mode],
              higher=False)
        # the block (colour_block, bumpers, hooks): text >= 3:1 large (nudged accent), no saturated red (R-54)
        block = [t["block.base"], t["block.highlight"]]
        shown = block + [cm.desaturate(c, HOLD_DESATURATION) for c in block]
        for key in ("block.ink", "block.ink2"):
            v = cm.worst_contrast(t[key], shown)
            check("block_text", f"/{slot} {key} on the block", v, CONTRAST_LARGE)
            block_forced = block_forced or v < CONTRAST_LARGE
        for c in block:
            check("red_flash", f"/{slot} block {c} red ratio", cm.red_ratio(c), cm.SATURATED_RED_RATIO - 1e-9,
                  higher=False)
        # mesh_drift behind text: R-55 numerics and the R-53 worst case (scrim forced when it cannot hold)
        accent_c = cm.to_oklch(t["struct.accent"]).c
        for tone in TONES:
            for hold in (False, True):
                colours = preset_colours("mesh_drift", family, slot, tone, hold=hold)
                tag = f"/{slot} mesh_drift {tone}{' (hold)' if hold else ''}"
                lums = [cm.relative_luminance(c) for c in colours]
                check("bg_luminance_ratio", f"{tag} luminance ratio", (max(lums) + 0.05) / (min(lums) + 0.05),
                      BG_LUMINANCE_RATIO_MAX, higher=False)
                for c in colours:
                    check("bg_saturation", f"{tag} {c} chroma vs accent", cm.to_oklch(c).c, accent_c, higher=False)
                for role in TEXT_ROLES:
                    worst = cm.worst_contrast(p[role], colours)
                    col.check("preset_contrast", f"mesh_drift:{fid}", f"{fid} {tag} {role}", worst,
                              ROLE_CONTRAST[role], family=fid, release=rel)  # measured, never a finding by itself
                    if worst < ROLE_CONTRAST[role]:
                        mesh_roles.add(role)
                # captions (clean_pill) over this background: R-53 7:1 target
                for c in colours + list(backgrounds.values()):
                    pill_bg = cm.composite(cm.to_hex(_over(pill.backdrop, pill.backdrop_alpha)), c)
                    check("caption", f"{tag} clean_pill ink over {c}", cm.contrast_ratio(pill.ink, pill_bg),
                          CONTRAST_CAPTION)
                    check("caption", f"{tag} clean_pill key ink over {c}", cm.contrast_ratio(pill.key_ink, pill_bg),
                          CONTRAST_CAPTION)
        # the code editor (§4.3): content_alt on the dark editor surface of a light family
        if mode == "light":
            alt = content_alt_tokens(mode)
            editor = code_surface(family)
            for role, need in ROLE_CONTRAST.items():
                required = CONTRAST_INK_PALETTE if role == "ink" else need
                check("code_surface", f"content_alt {role} on the code surface", cm.contrast_ratio(alt[role], editor),
                      required)
    # forced scrims, then the scrimmed contrast must hold
    scrims["mesh_drift"][fid] = bool(mesh_roles)
    scrim_roles["mesh_drift"][fid] = tuple(r for r in TEXT_ROLES if r in mesh_roles)
    scrims["colour_block"][fid] = block_forced
    scrim_roles["colour_block"][fid] = ("block.ink", "block.ink2") if block_forced else ()
    if mesh_roles:
        for slot in range(family.accent_period):
            scrim = scrim_colour(_scene_colours(family, slot).tokens["bg"])
            for tone in TONES:
                for hold in (False, True):
                    colours = preset_colours("mesh_drift", family, slot, tone, hold=hold)
                    for role in TEXT_ROLES:
                        under = scrimmed_bg(scrim, colours, p[role])
                        check("scrimmed_contrast", f"/{slot} {tone} {role} on the scrim",
                              cm.contrast_ratio(p[role], under), ROLE_CONTRAST[role])
    if studio is not None:  # hook (STUDIO_WALL, B2f): a text colour that holds R-53 body over the measured wall
        ink = ink_on(p["ink"], [studio])
        check("studio", f"{ink} on the studio wall {studio}", cm.contrast_ratio(ink, studio), CONTRAST_BODY)


def validate(families: Mapping[str, Family] | Iterable[Family] | None = None, *,
             studio: str | None = STUDIO_WALL) -> Report:
    """Validate the content palettes and ``families`` (default: all of :data:`FAMILIES`). Pure; returns every failed
    check (``Report.findings``) and the worst measured value of each check per mode/family (``Report.measures``), the
    forced-scrim table, the accent nudges and the red-lowered blocks.

    Checks: content hue separation >= 30° (entity vs entity and vs warn/ok/mark) and Machado 2009 dichromacy distance
    >= 0.05 OKLab (entity vs entity, entity vs fixed); every content role on every palette background (ink 7:1, text
    4.5:1, focus 3:1) and, for light families, ``content_alt`` on the code surface; the 70% dim states; cue colours
    (marker ink on mark >= 12:1, the underline >= 3:1); ``struct.accent`` as a graphic >= 3:1; bg-tint chroma; the
    block text >= 3:1 (after the nudge) and no saturated-red block (R-54); accent rotation >= 30° (R-52); mesh_drift
    luminance ratio <= 1.5 (an advisory, :data:`ADVISORY_CODES`) and chroma below the accent (R-55); caption
    contrast (7:1 target) over every background; the scrimmed contrast where the scrim is forced; the studio wall
    when given. Not here: R-56's "<= 3 hue families on screen at once" is a property of a frame (a template and its
    content), checked where frames are (the gallery and preflight); a scene's structure palette is five anchors."""
    if families is None:
        fams: list[Family] = list(FAMILIES.values())
    elif isinstance(families, Mapping):
        fams = list(families.values())
    else:
        fams = list(families)
    col = _Collector()
    scrims: dict[str, dict[str, bool]] = {preset: {} for preset in PRESETS_MIRRORED}
    scrim_roles: dict[str, dict[str, tuple[str, ...]]] = {preset: {} for preset in PRESETS_MIRRORED}
    nudges: dict[str, dict[int, float]] = {}
    red: dict[str, dict[int, float]] = {}
    _validate_content(col)
    for family in fams:
        _validate_family(col, family, scrims, scrim_roles, nudges, red, studio)
    # preset_contrast is a measurement feeding the scrim table, never a finding on its own
    failed = [f for f in col.findings if f.code != "preset_contrast"]
    return Report(findings=tuple(f for f in failed if f.code not in ADVISORY_CODES),
                  advisories=tuple(f for f in failed if f.code in ADVISORY_CODES),
                  measures=tuple(col.worst.values()), scrims=scrims, scrim_roles=scrim_roles, nudges=nudges,
                  red_lowered=red, chroma_restricted={m: chroma_restricted(m) for m in MODES})


def forced_scrims(families: Mapping[str, Family] | None = None) -> dict[str, dict[str, bool]]:
    """The forced-scrim table ``{preset: {family: bool}}`` (§4.5): True where text cannot hold its R-53 ratio on the
    preset's worst-case colour (any palette slot, tone or hold state) without the >= 55% scrim."""
    return validate(RELEASED_FAMILIES if families is None else families).scrims
