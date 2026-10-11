"""Dynamic video looks for the timeline (design §6.7, §4.5, §4.7, §4.9): ``TimelinePresentation`` and each scene's
``ResolvedLook``, resolved from ``Screenplay.presentation`` and ``SceneBase.look``.

B1 (motion engine core) resolves only what the stage of ``web/js/dynamic`` renders: the ``board_generic`` template,
the ``mesh_drift`` and ``colour_block`` backgrounds, ``cut`` and ``crossfade`` transitions, and the colour tokens of
the four R1 theme families (§4.7). B2 replaces the family table with ``compose/dynamic/themes.py`` and the choices
with ``pipeline/direct.py``; the stored timeline keeps whatever was resolved, so an old timeline keeps its look.

Pure and deterministic: seeds are SHA-256 of ``(look_seed, look_epoch, scene id, purpose)``, never ``hash()``.
Only ``compose.timeline`` calls it, and only for a screenplay with a presentation while ``DYNAMIC_ENABLED`` is on;
classic timelines never reach this module.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..schemas.screenplay import Presentation, Screenplay
from ..schemas.timeline import CaptionCue, ResolvedLook, TimedScene, TimedWord, TimelinePresentation
from .captions import (
    _CLOSE_GAP_SECONDS,
    _SENTENCE_END,
    _fits,
    _proportional_times,
    _token_times,
    balance_lines,
    normalize_cues,
    tokens_with_owner,
)

# What the B1 stage renders (web/js/dynamic): anything else falls back to these defaults.
TEMPLATES = ("board_generic",)
BACKGROUNDS = ("mesh_drift", "colour_block")
TRANSITIONS = ("cut", "crossfade")
DEFAULT_TEMPLATE = "board_generic"
CROSSFADE_SECONDS = 0.45  # §4.9: 300-500 ms; full-frame colour changes cross-fade over >= 300 ms (R-54)
TONES = ("bg", "bg-alt", "bg-tint")  # §4.5: the per-scene tone step of the background
ENGINE_VERSION = 1  # stage contract v3, first engine (TimelinePresentation.engine_version)
# Portrait captions: phrase chunks of <= 32 characters in <= 2 lines (R-32), until kinetic.py (B2) measures them.
PORTRAIT_CAPTION_LINE_CHARS = 16
# R-32 (9:16): a chunk is on screen 1.5-3.0 s, 1.0 s at the very least (§4.11: a chunk too fast joins its neighbour).
PORTRAIT_CAPTION_MIN_SECONDS = 1.0
PORTRAIT_CAPTION_TARGET_SECONDS = 1.5


@dataclass(frozen=True)
class Family:
    """One theme family (§4.7): its mode, background hue and accent window (OKLCH degrees)."""

    mode: str  # "dark" | "light"
    hue: float
    accent: tuple[float, float]
    chroma: float = 0.045  # background chroma (dark) / tint (light)


# The four R1 families (§4.7 table, rows 1, 2, 7 and 9). Unknown ids map onto one of them deterministically.
FAMILIES: dict[str, Family] = {
    "midnight_indigo": Family("dark", 275.0, (40.0, 110.0), 0.05),
    "deep_teal": Family("dark", 200.0, (0.0, 70.0), 0.045),
    "paper_ink": Family("light", 85.0, (0.0, 70.0), 0.03),
    "mint_studio": Family("light", 170.0, (270.0, 340.0), 0.025),
}

# Content colours (§4.7): fixed in every video by mode; they carry meaning and never rotate.
_CONTENT_FIXED: dict[str, dict[str, str]] = {
    "dark": {"entity:0": "#0FE3FE", "entity:1": "#C4A4FE", "entity:2": "#FEACE3", "warn": "#F8A87F",
             "ok": "#5DD8AA", "mark": "#F2D935"},
    "light": {"entity:0": "#0267C7", "entity:1": "#80369E", "entity:2": "#870C62", "warn": "#9F4503",
              "ok": "#016F50", "mark": "#F2D935"},
}


# --- colour -----------------------------------------------------------------------------------------------------


def _oklch_to_linear_rgb(lightness: float, chroma: float, hue: float) -> tuple[float, float, float]:
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    lc, mc, sc = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    )


def _in_gamut(rgb: Sequence[float]) -> bool:
    return all(-1e-6 <= c <= 1 + 1e-6 for c in rgb)


def _encode(c: float) -> int:
    c = min(1.0, max(0.0, c))
    v = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return int(round(v * 255))


def oklch_hex(lightness: float, chroma: float, hue: float) -> str:
    """``#RRGGBB`` of an OKLCH colour, clamped to the sRGB gamut by lowering the chroma (hue and lightness kept)."""
    rgb = _oklch_to_linear_rgb(lightness, chroma, hue)
    if not _in_gamut(rgb):
        lo, hi = 0.0, chroma
        for _ in range(24):
            mid = (lo + hi) / 2
            if _in_gamut(_oklch_to_linear_rgb(lightness, mid, hue)):
                lo = mid
            else:
                hi = mid
        rgb = _oklch_to_linear_rgb(lightness, lo, hue)
    return "#" + "".join(f"{_encode(c):02X}" for c in rgb)


def content_tokens(mode: str) -> dict[str, str]:
    """Resolved content tokens of a mode (§4.7): ink, ink2, entity:0..2, warn, ok, mark, focus."""
    if mode == "dark":
        ink, ink2 = oklch_hex(0.97, 0.006, 265.0), oklch_hex(0.80, 0.014, 265.0)
    else:
        ink, ink2 = oklch_hex(0.21, 0.02, 265.0), oklch_hex(0.42, 0.02, 265.0)
    fixed = _CONTENT_FIXED["dark" if mode == "dark" else "light"]
    focus = fixed["mark"] if mode == "dark" else ink
    return {"ink": ink, "ink2": ink2, **fixed, "focus": focus}


def structure_tokens(family: Family, accent_hue: float) -> dict[str, str]:
    """Structure tokens of one scene (§4.7): struct.accent, bg, bg-alt, bg-tint and the per-video surface."""
    h = family.hue
    if family.mode == "dark":
        return {
            "struct.accent": oklch_hex(0.77, 0.16, accent_hue),
            "bg": oklch_hex(0.20, family.chroma, h),
            "bg-alt": oklch_hex(0.24, family.chroma, h),
            "bg-tint": oklch_hex(0.21, 0.035, accent_hue),
            "surface": oklch_hex(0.29, min(0.04, family.chroma), h),
        }
    return {
        "struct.accent": oklch_hex(0.56, 0.17, accent_hue),
        "bg": oklch_hex(0.965, family.chroma, h),
        "bg-alt": oklch_hex(0.935, family.chroma, h),
        "bg-tint": oklch_hex(0.955, 0.028, accent_hue),
        "surface": oklch_hex(0.995, 0.004, h),
    }


# --- choices ----------------------------------------------------------------------------------------------------


def seed_for(presentation: Presentation, scene_id: str, purpose: str) -> int:
    """A 31-bit seed from the stored look seed (§4.4, §7.4.5): stable across rebuilds and machines."""
    text = f"{presentation.look_seed}:{presentation.look_epoch}:{scene_id}:{purpose}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def family_for(theme_family: str) -> Family:
    """The family of ``theme_family``; an id B1 does not know maps onto one of the R1 families by its hash."""
    known = FAMILIES.get(theme_family)
    if known is not None:
        return known
    names = sorted(FAMILIES)
    pick = int.from_bytes(hashlib.sha256(theme_family.encode("utf-8")).digest()[:4], "big") % len(names)
    return FAMILIES[names[pick]]


def accent_hue(family: Family, chapter: int) -> float:
    """``h0 + Δk`` (§4.7 rotation step 4): ends and middle of the window alternate, so neighbours differ >= 30°."""
    lo, hi = family.accent
    step = (0.0, 1.0, 0.5)[chapter % 3]
    return (lo + (hi - lo) * step) % 360.0


def chapter_indices(screenplay: Screenplay, scenes: Sequence[TimedScene]) -> list[int]:
    """Chapter number (0-based, in order of first appearance) of each timed scene."""
    by_scene: dict[str, str] = {}
    for ch in screenplay.chapters:
        for sid in ch.scene_ids:
            by_scene.setdefault(sid, ch.id)
    order: dict[str, int] = {}
    out: list[int] = []
    current = 0
    for s in scenes:
        key = by_scene.get(s.scene_id) or s.chapter_id
        if key is not None:
            if key not in order:
                order[key] = len(order)
            current = order[key]
        out.append(current)
    return out


def _background_preset(requested: str | None, scene_type: str) -> str:
    if requested in BACKGROUNDS:
        return requested  # type: ignore[return-value]
    return "colour_block" if scene_type == "chapter_card" else "mesh_drift"


def resolve_looks(screenplay: Screenplay, scenes: Sequence[TimedScene]) -> list[ResolvedLook]:
    """The resolved look of each timed scene (``TimedScene.look``), in order."""
    presentation = screenplay.presentation
    if presentation is None:
        raise ValueError("resolve_looks needs a screenplay with a presentation")
    family = family_for(presentation.theme_family)
    source = {s.id: s for s in screenplay.scenes}
    chapters = chapter_indices(screenplay, scenes)
    looks: list[ResolvedLook] = []
    previous_preset: str | None = None
    tone_step = 0
    for i, timed in enumerate(scenes):
        look = getattr(source.get(timed.scene_id), "look", None)
        template = look.template if look is not None and look.template in TEMPLATES else DEFAULT_TEMPLATE
        preset = _background_preset(look.background if look is not None else None, timed.type)
        new_chapter = i > 0 and chapters[i] != chapters[i - 1]
        tone_step = 0 if (i == 0 or new_chapter) else tone_step + 1
        requested = look.transition_in if look is not None else None
        if i == 0:
            kind = "cut"  # nothing to cross-fade from
        elif preset == "colour_block" or previous_preset == "colour_block":
            kind = "crossfade"  # a full-frame colour change always cross-fades (R-54)
        elif requested in TRANSITIONS:
            kind = requested  # type: ignore[assignment]
        else:
            kind = "crossfade" if new_chapter else "cut"  # hard cuts inside a chapter (R-23)
        seconds = round(min(CROSSFADE_SECONDS, timed.duration / 2), 3) if kind == "crossfade" else 0.0
        hold = look.hold_reason if look is not None else None
        looks.append(ResolvedLook(
            template=template,
            variant=None,
            tokens=structure_tokens(family, accent_hue(family, chapters[i])),
            background={"preset": preset, "params": {"tone": TONES[tone_step % len(TONES)]},
                        "seed": seed_for(presentation, timed.scene_id, "background"),
                        "frozen_at": 0.0 if hold is not None else None},
            transition_in={"kind": kind, "seconds": seconds},
            hold_reason=hold,
        ))
        previous_preset = preset
    return looks


def timeline_presentation(presentation: Presentation, scenes: Sequence[TimedScene],
                          total_duration: float) -> TimelinePresentation:
    """``Timeline.presentation`` (§6.7): the resolved content tokens and the global choices of the video."""
    family = family_for(presentation.theme_family)
    inserted = sum(s.duration for s in scenes if s.type == "clip")  # R-01: teacher clips are not generated runtime
    return TimelinePresentation(
        style="dynamic",
        format=presentation.format,
        content=content_tokens(family.mode),
        fonts={"display": presentation.display_font, "body": "inter", "mono": "jetbrains_mono"},
        personality=presentation.personality,
        caption_style=presentation.caption_style,
        cue_term_style=presentation.cue_term_style,
        captions_burn=presentation.captions_burn,
        sfx=presentation.sfx,
        motion=presentation.motion,
        generated_seconds=round(max(0.0, total_duration - inserted), 3),
        engine_version=ENGINE_VERSION,
    )


# --- 9:16 phrase captions (R-32) -------------------------------------------------------------------------------


def portrait_caption_cues(text: str, words: Sequence[TimedWord] | None, *, start: float, speech_end: float,
                          hold_end: float, width: int = PORTRAIT_CAPTION_LINE_CHARS) -> list[CaptionCue]:
    """One beat's 9:16 phrase captions: <= 2 lines of ``width`` characters each, grouped so that no chunk flashes by.

    The classic splitter fills each cue greedily and leaves one-word orphans of a third of a second ("back?",
    "rule."); here the grouping is a minimum-cost split by dynamic programming: every cue costs 1, a cue shorter than
    PORTRAIT_CAPTION_MIN_SECONDS (R-32's hard minimum) costs 100 more plus 400 x the square of the missing seconds
    (when a beat is too fast for every chunk to reach it, they share the time evenly), one shorter than
    PORTRAIT_CAPTION_TARGET_SECONDS 5 per missing second, and a cue that does not end on a sentence end 0.3. A cue's
    time runs to the next cue's start when the gap is closed (< 0.5 s, as ``split_caption``), the last one to
    ``hold_end`` (the beat's caption hold). Long words are split between grapheme clusters and keep their word's
    timing (``captions.tokens_with_owner``). Only Dynamic 9:16 timelines use this; classic captions never do.
    """
    tokens, owner = tokens_with_owner(text, width, clusters=True)
    if not tokens:
        return []
    times = _token_times(tokens, words or [], owner) or _proportional_times(tokens, start, max(speech_end, start))
    n = len(tokens)

    def cue_end(a: int, b: int) -> float:
        if b == n:
            return max(times[b - 1][1], min(hold_end, times[a][0] + PORTRAIT_CAPTION_MIN_SECONDS), hold_end)
        nxt = times[b][0]
        return nxt if nxt - times[b - 1][1] < _CLOSE_GAP_SECONDS else times[b - 1][1]

    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for b in range(1, n + 1):
        for a in range(b - 1, -1, -1):
            if not _fits(tokens[a:b], width):
                break
            d = cue_end(a, b) - times[a][0]
            cost = 1.0
            if d < PORTRAIT_CAPTION_MIN_SECONDS:  # convex: two short chunks share the time rather than leave an orphan
                cost += 100.0 + 400.0 * (PORTRAIT_CAPTION_MIN_SECONDS - d) ** 2
            elif d < PORTRAIT_CAPTION_TARGET_SECONDS:
                cost += 5.0 * (PORTRAIT_CAPTION_TARGET_SECONDS - d)
            if b < n and not _SENTENCE_END.search(tokens[b - 1]):
                cost += 0.3
            if best[a] + cost < best[b]:
                best[b], back[b] = best[a] + cost, a
    groups: list[tuple[int, int]] = []
    b = n
    while b > 0:
        groups.append((back[b], b))
        b = back[b]
    groups.reverse()
    cues = [CaptionCue(start=round(times[a][0], 3), end=round(max(times[b - 1][1], times[a][0]), 3),
                       text="\n".join(balance_lines(tokens[a:b], width))) for a, b in groups]
    for i in range(len(cues) - 1):
        nxt = cues[i + 1].start
        if nxt - cues[i].end < _CLOSE_GAP_SECONDS:
            cues[i] = cues[i].model_copy(update={"end": max(cues[i].start, nxt)})
    return normalize_cues(cues)


def dynamic_enabled(settings: Any) -> bool:
    """True when the admin flag ``DYNAMIC_ENABLED`` is on (Dynamic timelines are built and rendered)."""
    return bool(getattr(settings, "dynamic_enabled", False))
