"""Dynamic video looks for the timeline (design §6.7, §4.5, §4.7, §4.9): ``TimelinePresentation`` and each scene's
``ResolvedLook``, resolved from ``Screenplay.presentation`` and ``SceneBase.look``.

The resolver picks only what the stage of ``web/js/dynamic`` renders: the ``board_generic`` template, the
``mesh_drift`` and ``colour_block`` backgrounds, ``cut`` and ``crossfade`` transitions (the catalogue's
``implemented`` entries, ``compose/dynamic/templates.py``; pinned equal by a test), and the colours of the four R1
theme families (§4.7) from ``compose/dynamic/themes.py`` on ``compose/dynamic/colour_math.py``. B2e replaces the
choices with ``pipeline/direct.py``. The stored timeline keeps whatever was resolved, so an old timeline keeps its look.

**Look-engine generation 2 (B2a; plan B2 §1.2/§1.3, deviation 21).** Versus B1 (``engine_version`` 1):

* ``look.tokens`` carries every structure colour of the scene, the five B1 anchors (unchanged in value and order,
  pinned by ``tests/compose/dynamic/test_themes.py``) plus the colours the B1 stage derived at run time
  (``themes.scene_colours``: ``block.base``/``block.highlight``, ``block.ink``/``block.ink2``, ``scrim``), so the
  stage stops deriving them (plan §1.2). Where they differ from the B1 stage's run-time values (paper_ink's R-54
  red-lowered block; three hold-safe ``block.ink2``) is the owner's decision recorded in ``themes._block_set``.
* ``SceneLook.palette_slot`` sets the scene's accent when present (plan §1.10; direct.py writes it); otherwise the
  palette slot is the scene's chapter number, as in B1.
* Chapters are the ones ``compose.timeline.build_chapters`` lists (``Timeline.chapters``: the screenplay's chapters,
  else chapter cards, else concept changes, plus the opening chapter at 0:00), and a scene is in the last chapter that
  starts at or before it, the rule ``board_generic`` uses for its kicker. B1 read only ``Screenplay.chapters`` and
  ``scene.chapter_id``, so a lecture whose chapters came from the fallbacks kept one accent for the whole video while
  its chapter list and kicker changed (B2 read, compose-classic bug 4).
* R-54: every crossfade lasts 0.3-0.45 s (:func:`crossfade_seconds`); B1 halved it on scenes shorter than 0.6 s.
* ``TimelinePresentation`` gains ``content_alt``, ``family``, ``mode``, ``motion_scale`` and ``entity_colors``.

``compose.timeline`` imports this module for every build, classic ones too, so the Dynamic modules are imported
lazily, inside the functions a Dynamic build calls (``FAMILIES`` and ``Family`` resolve on first access): a classic
build never loads ``aadhi.compose.dynamic`` (a subprocess test builds classic timelines and checks ``sys.modules``).

Pure and deterministic: seeds are SHA-256 of ``(look_seed, look_epoch, scene id, purpose)``, never ``hash()``.
Only ``compose.timeline`` calls it, and only for a screenplay with a presentation while ``DYNAMIC_ENABLED`` is on;
classic timelines never reach this module's colour functions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..schemas.screenplay import Presentation, Screenplay
from ..schemas.timeline import CaptionCue, Chapter, ResolvedLook, TimedScene, TimedWord, TimelinePresentation
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

if TYPE_CHECKING:
    from .dynamic.themes import Family

# What the stage renders (web/js/dynamic; the catalogue's ``implemented`` entries, pinned equal by a test): anything
# else falls back to these defaults. Static tuples, so importing this module loads no Dynamic module.
TEMPLATES = ("board_generic",)
BACKGROUNDS = ("mesh_drift", "colour_block")
TRANSITIONS = ("cut", "crossfade")
DEFAULT_TEMPLATE = "board_generic"
# §4.9 crossfade: 300-500 ms (web/js/dynamic/transitions.js CROSSFADE_DEFAULT / _MIN / _MAX). R-54: a full-frame
# colour change cross-fades over >= 300 ms, so no crossfade is ever shorter (crossfade_seconds).
CROSSFADE_SECONDS = 0.45
CROSSFADE_MIN_SECONDS = 0.3
CROSSFADE_MAX_SECONDS = 0.5
TONES = ("bg", "bg-alt", "bg-tint")  # §4.5: the per-scene tone step of the background
# The look-engine generation (TimelinePresentation.engine_version; plan §1.3, deviation 21): 1 = B1, 2 = B2a (see the
# module docstring). The stage contract stays aadhiStage v3. aadhi.api.timelines rebuilds a stored Dynamic timeline of
# the current revision built by an older generation, and hashes this into the served timeline's ETag.
ENGINE_VERSION = 2
CHAPTER_START_EPS = 1e-3  # a chapter starting within 1 ms of a scene is its chapter (board_generic.kickerOf)
# Portrait captions: phrase chunks of <= 32 characters in <= 2 lines (R-32), until kinetic.py (B2) measures them.
PORTRAIT_CAPTION_LINE_CHARS = 16
# R-32 (9:16): a chunk is on screen 1.5-3.0 s, 1.0 s at the very least (§4.11: a chunk too fast joins its neighbour).
PORTRAIT_CAPTION_MIN_SECONDS = 1.0
PORTRAIT_CAPTION_TARGET_SECONDS = 1.5


# --- colour (design §4.7): the catalogue lives in compose/dynamic/themes.py ------------------------------------
# B1's names stay importable from here (evals/render_parity.py and the tests use them). FAMILIES are the released
# (R1) families only, in B1's ids: family_for maps unknown ids onto them by hash, so they must not grow with W2.


def __getattr__(name: str) -> Any:
    """``FAMILIES`` and ``Family`` on first access (PEP 562), so importing this module loads no Dynamic module."""
    if name in ("FAMILIES", "Family"):
        from .dynamic import themes

        return dict(themes.RELEASED_FAMILIES) if name == "FAMILIES" else themes.Family
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _families() -> dict[str, Family]:
    from .dynamic import themes

    return themes.RELEASED_FAMILIES


def oklch_hex(lightness: float, chroma: float, hue: float) -> str:
    """``#RRGGBB`` of an OKLCH colour, clamped to the sRGB gamut by lowering the chroma (hue and lightness kept):
    ``colour_math.palette_hex``."""
    from .dynamic import colour_math

    return colour_math.palette_hex(lightness, chroma, hue)


def content_tokens(mode: str) -> dict[str, str]:
    """Resolved content tokens of a mode (§4.7): ink, ink2, entity:0..2, warn, ok, mark, focus (any mode other than
    ``"dark"`` is light, as in B1)."""
    from .dynamic import themes

    return themes.content_tokens("dark" if mode == "dark" else "light")


def structure_tokens(family: Family, accent_hue: float) -> dict[str, str]:
    """Structure tokens of one scene (§4.7): struct.accent, bg, bg-alt, bg-tint and the per-video surface."""
    from .dynamic import themes

    return themes.structure_tokens(family, accent_hue)


# --- choices ----------------------------------------------------------------------------------------------------


def seed_for(presentation: Presentation, scene_id: str, purpose: str) -> int:
    """A 31-bit seed from the stored look seed (§4.4, §7.4.5): stable across rebuilds and machines."""
    text = f"{presentation.look_seed}:{presentation.look_epoch}:{scene_id}:{purpose}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big") & 0x7FFFFFFF


def family_for(theme_family: str) -> Family:
    """The family of ``theme_family``; an id B1 does not know maps onto one of the R1 families by its hash."""
    families = _families()
    known = families.get(theme_family)
    if known is not None:
        return known
    names = sorted(families)
    pick = int.from_bytes(hashlib.sha256(theme_family.encode("utf-8")).digest()[:4], "big") % len(names)
    return families[names[pick]]


def accent_hue(family: Family, chapter: int) -> float:
    """``h0 + Δk`` (§4.7 rotation step 4): ends and middle of the window alternate, so neighbours differ >= 30°
    (``themes.accent_hue`` of palette slot ``chapter``)."""
    from .dynamic import themes

    return themes.accent_hue(family, chapter)


def chapter_indices(scenes: Sequence[TimedScene], chapters: Sequence[Chapter]) -> list[int]:
    """The chapter of each timed scene: its index in ``chapters`` (``Timeline.chapters``, as
    ``compose.timeline.build_chapters`` lists them: sorted by start, the first at 0:00), the last chapter starting at
    or before the scene (within :data:`CHAPTER_START_EPS`), which is the chapter ``board_generic`` shows as the
    scene's kicker (``kickerOf``; ``motion.chunks._kicker_chapters``). 0 for a scene before every chapter, or with
    no chapters at all. Non-decreasing over the scenes, so a change of index is a chapter boundary."""
    starts = [float(c.start) for c in chapters]
    out: list[int] = []
    for s in scenes:
        index = 0
        for k, start in enumerate(starts):
            if start <= s.start + CHAPTER_START_EPS:
                index = k
        out.append(index)
    return out


def crossfade_seconds(duration: float) -> float | None:
    """Seconds of a crossfade into a scene lasting ``duration`` (§4.9, R-54), or None when the scene cannot carry one.

    The fade lies inside the incoming scene (§4.9), so it lasts half the scene up to :data:`CROSSFADE_SECONDS` (0.45 s)
    but never less than :data:`CROSSFADE_MIN_SECONDS`: R-54 asks every full-frame colour change (into and out of a
    colour block) to cross-fade over >= 300 ms, and a shorter fade is a flash. B1 halved it on scenes under 0.6 s
    (0.25 s at 0.5 s); the stage already drew those at 0.3 s (``transitions.js`` clamps to [0.3, 0.5] and to the
    scene), so the value now says what is drawn and nothing on screen changes. A scene shorter than 0.3 s cannot hold
    a 300 ms fade at all; its transition is a cut (None here). No such scene exists: ``build_timeline`` gives every
    scene at least ``SCENE_TRANSITION_SECONDS + 0.1`` (0.5 s; pinned by a test), and a colour-block scene shorter than
    300 ms could not be faded in conformantly either way, so the Director (B2e) must not give one a colour block."""
    if not duration >= CROSSFADE_MIN_SECONDS:
        return None
    return round(min(CROSSFADE_SECONDS, max(CROSSFADE_MIN_SECONDS, duration / 2)), 3)


def stage_personality(personality: str, family: Family) -> str:
    """The personality the R1 stage animates a lecture with: the named one when it is released, else the family's
    R1 default (§4.7 table: "Cinematic -> Soft in R1", "Editorial -> Crisp in R1"; ``Family.personality``). An
    unknown id is treated like an unreleased one."""
    from .dynamic import themes

    named = themes.PERSONALITIES.get(personality)
    return named.id if named is not None and named.release == "R1" else family.personality


def motion_scale(personality: str, family: Family) -> float:
    """``TimelinePresentation.motion_scale``: the duration scale of :func:`stage_personality` (§4.8 table: Crisp
    x0.85, Soft x1.15)."""
    from .dynamic import themes

    return themes.PERSONALITIES[stage_personality(personality, family)].duration_scale


def _background_preset(requested: str | None, scene_type: str) -> str:
    if requested in BACKGROUNDS:
        return requested  # type: ignore[return-value]
    return "colour_block" if scene_type == "chapter_card" else "mesh_drift"


def resolve_looks(screenplay: Screenplay, scenes: Sequence[TimedScene],
                  chapters: Sequence[Chapter] | None = None) -> list[ResolvedLook]:
    """The resolved look of each timed scene (``TimedScene.look``), in order.

    ``chapters``: ``Timeline.chapters`` (``compose.timeline.build_chapters(screenplay, scenes)``, computed here when
    not given). Per scene: the template and background the stage draws (the teacher's when implemented; a chapter
    card's colour block, else ``mesh_drift``); the tone step (bg, bg-alt, bg-tint; reset at each chapter, §4.5); the
    structure colours of palette slot ``SceneLook.palette_slot`` or, without one, of the chapter's number
    (``themes.scene_tokens``: anchors plus derived colours); and the transition in: a cut into the first scene, a
    crossfade into and out of a colour block (R-54), else the teacher's cut or crossfade, else a crossfade where the
    structure accent changes (a new chapter, or a new palette slot) and a hard cut inside a chapter (R-23). Every
    crossfade lasts :func:`crossfade_seconds`; a scene too short for one gets a cut. The crossfade at a palette-slot
    change inside a chapter is interim (A2 proposal, pending owner review; not in the design): R-52 cross-fades an
    accent change over 600-1000 ms, which B2e's ``accent_fade`` brings. ``continues_from`` is kept when it
    names the scene just before (a split's part B continues its part A, §8.3); any other value is dropped."""
    from .dynamic import themes

    presentation = screenplay.presentation
    if presentation is None:
        raise ValueError("resolve_looks needs a screenplay with a presentation")
    if chapters is None:
        from .timeline import build_chapters  # compose.timeline imports this module: resolved at call time

        chapters = build_chapters(screenplay, scenes)
    family = family_for(presentation.theme_family)
    source = {s.id: s for s in screenplay.scenes}
    numbers = chapter_indices(scenes, chapters)
    looks: list[ResolvedLook] = []
    previous_preset: str | None = None
    previous_accent: str | None = None
    tone_step = 0
    for i, timed in enumerate(scenes):
        look = getattr(source.get(timed.scene_id), "look", None)
        template = look.template if look is not None and look.template in TEMPLATES else DEFAULT_TEMPLATE
        preset = _background_preset(look.background if look is not None else None, timed.type)
        new_chapter = i > 0 and numbers[i] != numbers[i - 1]
        tone_step = 0 if (i == 0 or new_chapter) else tone_step + 1
        slot = look.palette_slot if look is not None and look.palette_slot is not None else numbers[i]
        tokens = themes.scene_tokens(family, slot)
        # without palette slots the accent changes exactly at the chapter boundaries (consecutive slots differ, R-52)
        boundary = new_chapter or (previous_accent is not None and tokens["struct.accent"] != previous_accent)
        requested = look.transition_in if look is not None else None
        if i == 0:
            kind = "cut"  # nothing to cross-fade from
        elif preset == "colour_block" or previous_preset == "colour_block":
            kind = "crossfade"  # a full-frame colour change always cross-fades (R-54)
        elif requested in TRANSITIONS:
            kind = requested  # type: ignore[assignment]
        else:
            # hard cuts inside a chapter (R-23); an in-chapter palette-slot change cross-fades until B2e's accent_fade
            # (R-52; A2 proposal, pending owner review)
            kind = "crossfade" if boundary else "cut"
        seconds = crossfade_seconds(timed.duration) if kind == "crossfade" else 0.0
        if seconds is None:  # too short to carry a >= 300 ms fade (R-54; unreachable, see crossfade_seconds)
            kind, seconds = "cut", 0.0
        hold = look.hold_reason if look is not None else None
        follows = look.continues_from if look is not None else None
        looks.append(ResolvedLook(
            template=template,
            variant=None,
            tokens=tokens,
            background={"preset": preset, "params": {"tone": TONES[tone_step % len(TONES)]},
                        "seed": seed_for(presentation, timed.scene_id, "background"),
                        "frozen_at": 0.0 if hold is not None else None},
            transition_in={"kind": kind, "seconds": seconds},
            hold_reason=hold,
            continues_from=follows if i > 0 and follows == scenes[i - 1].scene_id else None,
        ))
        previous_preset = preset
        previous_accent = tokens["struct.accent"]
    return looks


def timeline_presentation(presentation: Presentation, scenes: Sequence[TimedScene],
                          total_duration: float) -> TimelinePresentation:
    """``Timeline.presentation`` (§6.7): the resolved content tokens and the global choices of the video, plus (plan
    §1.3) the other mode's content tokens (``content_alt``), the resolved family and its mode, the motion scale
    (:func:`motion_scale`) and the lesson-wide entity map (``Presentation.entity_colors``, R-30)."""
    from .dynamic import themes

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
        content_alt=themes.content_alt_tokens(family.mode),
        family=family.id,
        mode=family.mode,
        motion_scale=motion_scale(presentation.personality, family),
        entity_colors=dict(presentation.entity_colors),
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
