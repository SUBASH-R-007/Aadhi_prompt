"""Aadhi in a Dynamic video: the presenter's clip, crop, box, timing and frame cache (DYNAMIC_VIDEO_DESIGN §3.2-§3.4,
§4.3 ``aadhi_intro`` / ``aadhi_recap``; B2 plan §1.2 ``ResolvedLook.presenter``, §1.12 the crop, §2 "Aadhi" and R-54;
owner decision 2 of 2026-10-16: every Dynamic video opens with Aadhi).

What is here (pure data and functions, plus one blocking cache helper; imported only inside the Dynamic gate):

* **Clips** (§3.2). ``left`` (``aadhi_left_clean.mp4``: all four 16:9 intro layouts and the 16:9 recap) and ``center``
  (``aadhi_center_clean.mp4``: the 9:16 card), named through ``compose.base.MASCOT_CLIPS`` so a re-encode that retires
  a file (``RETIRED_MASCOT_CLIPS``) is followed here too. ``popup`` and ``right`` are never used (R-14: the popup's
  board shows pseudo-equations, the right clip is a mirror with a backwards crest). Both clips are 1280x720, 24 fps,
  exactly 8.000 s (192 frames), measured by ``tests/compose/dynamic/test_presenter.py`` on the files themselves.
* **Placements** (§3.3): the presenter's box on the stage (``rect``), the part of the clip it shows (``crop``, source
  px) and its corner radius, per layout. A crop and its box always have the same aspect, so the frames the worker
  extracts at the box's size are drawn at 1:1 and never scaled on the page (plan §P (1): pictures are only ever
  scaled down from at least their displayed size, and here not at all). 16:9: ``fullframe`` (the whole clip 1.5x),
  ``card_left`` (a 720x720 crop at source x 0-720 as a card at x 96-816, y 180-900), ``pip`` (the same crop as a
  480x480 card at x 96-576, y 432-912), ``split`` (source x 0-576 at 1.5x filling x 0-864). 9:16: the ``card``, the
  centre clip's 720x720 crop (source x 280-1000) at 744x744 (the design's 1.03x, done by the extraction like every
  other size) at y 800-1544 and one of three seeded x positions (§3.3 "so feed covers differ").
* **Timing** (§3.3 "Timing rule for every Aadhi scene", plan §2 "Aadhi"): speech starts at ``AUDIO_OFFSET`` (0.8 s in
  16:9, after the sonic logo; 0.15 s in 9:16); ``t_exit`` = the last Aadhi word's end + 0.3 s; the scene lasts
  ``t_exit`` + the exit, clamped to [5.0, 8.0] s. **Reading** (recorded for the owner): the clamp moves ``t_exit``,
  never leaves dead time after the exit, so "if it ends early, the title holds until 5.0 s" is a scene of exactly
  5.0 s whose exit starts at 5.0 s - exit; a speech ending after 7.1 s is ``too_long`` (the ``aadhi.too_long``
  lint's threshold) and its exit is held at 8.0 s - exit. The exits: the 16:9 intro's **brand wipe** (600 ms, the
  template's own exit choreography in the next scene's accent, R-54 deviation 10), the 9:16 intro's card sliding down
  (300 ms, ``ease.exit``) and the recap card's fade (300 ms; §3.3 says only "the card exits").
* **Layout choice** (§3.3): one of the four 16:9 layouts per video, seeded on the lecture's ``look_seed`` and
  ``layout_epoch`` (New layouts re-rolls it), excluding the teacher's last used one when the caller knows it (history
  is §5.3's, B4), the teacher's stored ``Presentation.intro_layout`` winning; ``pip`` (the hook question large) only for
  an intro that has a hook. The 9:16 card's x likewise (``Presentation.reel_card_x`` when it names one of the three).
* **The studio wall** (§3.3 "ink and accent come from a fixed studio surface token; B2 measures the wall's luminance
  over all 192 frames in the title region"): measured once from the clip over every frame and pinned here
  (:data:`WALL`); the ``fullframe`` title stack is set in the content palette's light-mode inks on it, and a test
  re-measures the clip so a re-encode that darkens the wall fails it.
* **Synthesised appearances** (owner decision 2). A generated lecture has no presenter scene today (Aadhi's spoken
  lines need the Aadhi voice of B3 and the greeting pools of B4), so the timeline builder inserts one:
  :func:`intro_scene` (the session chip and the title, silent) before the first scene, and :func:`recap_scene` (the
  "Quick check" prompt card) before the lecture's retrieval recap in 16:9 (§3.4: the recap appearance is on by default
  in 16:9 and off in 9:16). A screenplay whose first scene is its own presenter scene (``SceneBase.look.presenter``)
  that ``aadhi_intro`` draws is drawn from it instead (the forced §4.4 row) and no intro is synthesised; one the
  template cannot draw (a teacher's lock on another template) gets the synthesised intro before it, so every video
  still opens with Aadhi. ``DYNAMIC_AADHI_INTRO=false`` (white-label and test deployments only, §3.4) turns both off:
  the lecture opens with its own first scene, whose hook ``hook_question`` draws.
* **Which recap** (§3.3: ``aadhi_recap`` "opens" the retrieval recap, after which ``recap_retrieval`` starts the
  free-recall think time). The card goes before the lecture's **last** ``recap`` scene that ``recap_retrieval`` draws
  (``direct.row_template``), never before one that comes ahead of the first chapter card: a "Previously in ..." recap of
  prior knowledge near the start, or one another template draws (a bullet list is ``list_build``'s), is not the
  retrieval recap, and a "Quick check: what are the 3 big ideas?" there would ask before anything was taught (step-5
  review R5-1). A lecture with no such scene gets no recap appearance, and neither does one whose screenplay has its
  own presenter recap scene that ``aadhi_recap`` draws (R-12: one recap appearance).
* **The intro's text** (readings recorded for the owner). The title is the session title, **set whole**: the page fits
  it in its slot (up to 4 lines) and a title that does not fit even at the slot's minimum is reported as overflow, so
  preflight asks for a shorter one; it is never cut (the "never drop content" rule; step-5 review R5-5). The intro
  does not repeat the lecture's own opening: when the first shown scene already shows the session title (as its title
  or a heading, the classic title scene and the generator's cold open do), the intro names the level above it, the
  unit, else the subject, and the chip leaves that part out; only a lecture with neither keeps the repeat (R5-4). The
  reviewer's alternative, drawing that first scene as the presenter scene, was not taken: its narration is the
  narrator's (§5.5: Aadhi's lines are his own voice and never the narrator's, R-13 caps them at 16 words) and may run
  past the 8 s clip.
* **The frame cache** (plan §2 "presenter frame cache under RENDER_WORK_DIR/derivatives/aadhi/<digest>/<crop>/<size>/
  (tmp+replace under a lock; swept with workspaces)"): :func:`ensure_frames` extracts every frame of a clip's crop at a
  size once, into a temporary directory renamed into place as a whole (a rename is atomic, so a reader never sees a
  half-written set and two workers racing both finish with one complete copy), and every chunk of every render reuses
  it; :func:`sweep_cache` removes the sets of clips that are no longer current and abandoned temporary directories.

Pure and deterministic except :func:`ensure_frames` / :func:`sweep_cache` (blocking file and ffmpeg work: call them
through ``asyncio.to_thread``): seeds are SHA-256 of stored values, never ``hash()``; no clock is read for any choice.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

from ...pipeline.richlite import to_plain
from ...schemas.screenplay import BoardItem, BoardItemKind, BoardScene, ChapterCardScene, Presentation, Screenplay
from ...schemas.timeline import Chapter, Layout, TimedScene
from ..base import MASCOT_CLIPS
from .zones import FORMATS, LAYOUT, Format

Kind = Literal["intro", "recap"]
IntroLayout = Literal["fullframe", "card_left", "pip", "split"]
ExitKind = Literal["wipe", "slide_down", "fade"]
EnterKind = Literal["none", "rise"]

# --- the clips (§3.2) -------------------------------------------------------------------------------------------------

#: The two usable clips by role, through the classic table (so a cleaned re-encode is followed here too).
CLIPS: Final[Mapping[str, str]] = {"left": MASCOT_CLIPS["left"], "center": MASCOT_CLIPS["center"]}
#: Never in a Dynamic video (R-14): the popup's board of pseudo-equations, and the mirrored right clip.
NEVER_CLIPS: Final = frozenset({MASCOT_CLIPS["popup_bottom_left"], MASCOT_CLIPS["right"]})
CLIP_SIZE: Final = (1280, 720)
CLIP_FPS: Final = 24
CLIP_SECONDS: Final = 8.0
CLIP_FRAMES: Final = 192

# --- layouts and placements (§3.3) ------------------------------------------------------------------------------------

INTRO_LAYOUTS: Final[tuple[IntroLayout, ...]] = ("fullframe", "card_left", "pip", "split")
REEL_LAYOUT: Final = "card"
#: The 16:9 recap: "the 720x720 left-clip card at x 96-816, y 180-900" (§3.3) - card_left's placement.
RECAP_LAYOUT: Final[Mapping[Format, str]] = {"landscape": "card_left", "portrait": REEL_LAYOUT}
#: The 9:16 card's x positions (§3.3: x 168-912, 96-840 or 240-984), in the design's order.
REEL_CARD_XS: Final = (168, 96, 240)
REEL_CARD_SIZE: Final = 744
REEL_CARD_Y: Final = 800
#: The rounded presenter card (zones.LAYOUT ``radius.presenter_card``, 40 px); a frame-filling clip has none.
CARD_RADIUS: Final = int(LAYOUT.radius["presenter_card"])


@dataclass(frozen=True)
class Placement:
    """Where the presenter is drawn: ``clip`` (a branding file name), ``rect`` (stage px ``[x, y, w, h]``), ``crop``
    (source px ``[x, y, w, h]``, the same aspect as ``rect``) and the corner ``radius``."""

    clip: str
    rect: tuple[int, int, int, int]
    crop: tuple[int, int, int, int]
    radius: int

    def __post_init__(self) -> None:
        x, y, w, h = self.crop
        if not (0 <= x and 0 <= y and w > 0 and h > 0 and x + w <= CLIP_SIZE[0] and y + h <= CLIP_SIZE[1]):
            raise ValueError(f"crop {self.crop} lies outside the {CLIP_SIZE[0]}x{CLIP_SIZE[1]} clip")
        if self.rect[2] <= 0 or self.rect[3] <= 0:
            raise ValueError(f"empty presenter box {self.rect}")
        # one aspect: the frames are extracted at the box's size and drawn 1:1 (whole px may round the ratio by 1)
        if abs(self.rect[2] * h - self.rect[3] * w) > max(w, h):
            raise ValueError(f"crop {self.crop} and box {self.rect} differ in aspect")
        if self.clip in NEVER_CLIPS:
            raise ValueError(f"{self.clip} is never shown in a Dynamic video (R-14)")


LANDSCAPE_PLACEMENTS: Final[Mapping[str, Placement]] = {
    "fullframe": Placement(CLIPS["left"], (0, 0, 1920, 1080), (0, 0, 1280, 720), 0),
    "card_left": Placement(CLIPS["left"], (96, 180, 720, 720), (0, 0, 720, 720), CARD_RADIUS),
    "pip": Placement(CLIPS["left"], (96, 432, 480, 480), (0, 0, 720, 720), CARD_RADIUS),
    "split": Placement(CLIPS["left"], (0, 0, 864, 1080), (0, 0, 576, 720), 0),
}
#: The centre clip's square crop, centred on Aadhi (≈ x 470-835 of 1280, §3.2).
REEL_CROP: Final = (280, 0, 720, 720)


def reel_placement(card_x: int) -> Placement:
    """The 9:16 card at ``card_x`` (one of :data:`REEL_CARD_XS`)."""
    if card_x not in REEL_CARD_XS:
        raise ValueError(f"the 9:16 card's x is one of {REEL_CARD_XS}, not {card_x}")
    return Placement(CLIPS["center"], (card_x, REEL_CARD_Y, REEL_CARD_SIZE, REEL_CARD_SIZE), REEL_CROP, CARD_RADIUS)


def placement(fmt: Format, layout: str, card_x: int | None = None) -> Placement:
    """The placement of ``layout`` in ``fmt`` (16:9: one of :data:`INTRO_LAYOUTS`; 9:16: ``card`` at ``card_x``)."""
    if fmt == "portrait":
        if layout != REEL_LAYOUT:
            raise ValueError(f"9:16 has the {REEL_LAYOUT!r} layout only, not {layout!r}")
        return reel_placement(REEL_CARD_XS[0] if card_x is None else card_x)
    if layout not in LANDSCAPE_PLACEMENTS:
        raise ValueError(f"unknown 16:9 presenter layout {layout!r}")
    return LANDSCAPE_PLACEMENTS[layout]


# --- the studio wall (§3.3 "studio surface token") --------------------------------------------------------------------


@dataclass(frozen=True)
class Wall:
    """The studio wall behind the ``fullframe`` title stack, measured over all 192 frames of the left clip in the
    title region (``region``: source px ``[x0, y0, x1, y1]``; the stage box :data:`WALL_TITLE_BOX` / 1.5): the darkest
    and lightest colour any frame shows there, as hex, with their relative luminances."""

    region: tuple[int, int, int, int]
    darkest: str
    lightest: str
    darkest_luminance: float
    lightest_luminance: float


#: Measured 2026-10-16 from ``video_template/aadhi_left_clean.mp4`` (all 192 frames, rgb24 decode): darkest
#: rgb(204, 203, 206) (relative luminance 0.600, a shadow fold of the wall), lightest luminance 0.913, mean rgb(232, 232,
#: 233). The region is the title box / 1.5 widened to even source px (a 4:2:0 crop is even) and stops above the
#: floor line (source y 588): the title stack never reaches it.
WALL: Final = Wall(region=(572, 36, 1216, 570), darkest="#CCCBCE", lightest="#F5F5F7", darkest_luminance=0.600,
                   lightest_luminance=0.913)
#: The ``fullframe`` title stack's box on the stage: §3.3's x 860-1824, from the top of the critical box to the
#: wall's measured bottom (WALL.region y1 x 1.5 = 855), so every title pixel lies on the measured wall.
WALL_TITLE_BOX: Final = (860, 54, 1824, 855)
#: The least contrast the wall inks must keep against the darkest wall colour (R-53's text minimum).
WALL_MIN_CONTRAST: Final = 4.5


def wall_inks() -> dict[str, str]:
    """The inks of text on the studio wall: the light-mode content palette's ``ink`` / ``ink2`` (the wall is light in
    every family), with the wall's darkest colour as the background they are checked on (``wall``)."""
    from .themes import content_tokens  # lazy: only a Dynamic build asks

    light = content_tokens("light")
    return {"wall": WALL.darkest, "wall_ink": light["ink"], "wall_ink2": light["ink2"]}


# --- timing (§3.3, plan §2 "Aadhi") ------------------------------------------------------------------------------------

AUDIO_OFFSET: Final[Mapping[Format, float]] = {"landscape": 0.8, "portrait": 0.15}
MIN_SECONDS: Final = 5.0
MAX_SECONDS: Final = 8.0
EXIT_PAD: Final = 0.3  # t_exit = the last Aadhi word's end + 0.3 s
TOO_LONG_SPEECH_END: Final = 7.1  # aadhi.too_long: his speech ends later than this
WIPE_SECONDS: Final = 0.6  # the 16:9 intro's brand wipe (ease.hero, 600 ms)
SLIDE_SECONDS: Final = 0.3  # the 9:16 intro card slides down (ease.exit, 300 ms)
FADE_SECONDS: Final = 0.3  # the recap card's exit
#: The 9:16 card's settle: §3.3's 0.96 -> 1.0 scale over 400 ms is replaced by a 16 px whole-pixel rise over the same
#: time, because a clip is never scaled (B2c deviation 28: measured order-dependent in real Chromium).
SETTLE_SECONDS: Final = 0.4
SETTLE_RISE: Final = 16


def exit_of(kind: Kind, fmt: Format) -> tuple[ExitKind, float]:
    """How a presenter scene leaves: the 16:9 intro's wipe, the 9:16 intro card's slide down, the recap card's fade."""
    if kind == "intro":
        return ("wipe", WIPE_SECONDS) if fmt == "landscape" else ("slide_down", SLIDE_SECONDS)
    return "fade", FADE_SECONDS


def enter_of(kind: Kind, fmt: Format) -> EnterKind:
    """How the presenter enters: the 9:16 intro card settles (a rise, see :data:`SETTLE_RISE`); everything else is on
    screen from the scene's first frame (§3.3 "0.0 s: clip and chip visible")."""
    return "rise" if kind == "intro" and fmt == "portrait" else "none"


@dataclass(frozen=True)
class Timing:
    """A presenter scene's timing: speech starts at ``audio_offset``; the exit starts at ``t_exit`` and lasts
    ``exit_seconds``; the scene lasts ``duration`` (= ``t_exit + exit_seconds``, in [5, 8]); ``too_long``: his speech
    ends after 7.1 s (the lint asks for a shorter line)."""

    audio_offset: float
    t_exit: float
    exit_seconds: float
    duration: float
    too_long: bool


def _r(x: float) -> float:
    return round(float(x) + 0.0, 3)


def timing(kind: Kind, fmt: Format, speech_end: float | None = None) -> Timing:
    """The timing of a presenter scene whose Aadhi speech ends at ``speech_end`` (scene-relative seconds, the audio
    offset included; None: a silent appearance). See the module docstring for the clamp's reading."""
    _, exit_s = exit_of(kind, fmt)
    raw = 0.0 if speech_end is None else float(speech_end) + EXIT_PAD
    t_exit = min(max(raw, MIN_SECONDS - exit_s), MAX_SECONDS - exit_s)
    return Timing(audio_offset=AUDIO_OFFSET[fmt], t_exit=_r(t_exit), exit_seconds=exit_s,
                  duration=_r(t_exit + exit_s), too_long=speech_end is not None and speech_end > TOO_LONG_SPEECH_END)


# --- choices (seeded, §3.3) -------------------------------------------------------------------------------------------


def _pick(presentation: Presentation, purpose: str, n: int) -> int:
    text = f"{presentation.look_seed}:{presentation.layout_epoch}:presenter:{purpose}"
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big") % n


def intro_layout(presentation: Presentation, *, has_hook: bool, last_used: str | None = None) -> str:
    """The 16:9 intro layout of a lecture (9:16: always ``card``). The teacher's stored ``intro_layout`` wins when the
    scene can draw it; otherwise a seeded pick over :data:`INTRO_LAYOUTS` without ``last_used`` (§3.3 "excluding the
    teacher's last used") and, for an intro without a hook question, without ``pip`` (whose large text is the hook)."""
    if presentation.format == "portrait":
        return REEL_LAYOUT
    pool = [layout for layout in INTRO_LAYOUTS if has_hook or layout != "pip"]
    stored = presentation.intro_layout
    if stored in pool:
        return str(stored)
    fresh = [layout for layout in pool if layout != last_used] or pool
    return fresh[_pick(presentation, "intro_layout", len(fresh))]


def reel_card_x(presentation: Presentation) -> int:
    """The 9:16 card's x: the stored ``reel_card_x`` when it names one of :data:`REEL_CARD_XS`, else seeded."""
    stored = presentation.reel_card_x
    if stored in REEL_CARD_XS:
        return int(stored)  # type: ignore[arg-type]
    return REEL_CARD_XS[_pick(presentation, "reel_card_x", len(REEL_CARD_XS))]


def recap_enabled(presentation: Presentation) -> bool:
    """Whether the lecture shows the Aadhi recap: §3.4 "on by default in 16:9, off in 9:16". ``Presentation.aadhi_recap``
    (default True) switches the 16:9 appearance off; a 9:16 recap needs B4's per-format option (``GenerationOptions
    .aadhi_recap`` None = the format's default), so a 9:16 lecture shows one only when its screenplay has its own
    presenter recap scene."""
    return presentation.format == "landscape" and bool(presentation.aadhi_recap)


# --- ResolvedLook.presenter (plan §1.2, §1.12) ---------------------------------------------------------------------------


def look_presenter(kind: Kind, fmt: Format, layout: str, t: Timing, *, card_x: int | None = None) -> dict[str, Any]:
    """``ResolvedLook.presenter`` of a presenter scene: the logical clip (a branding file name, served by the worker's
    branding lookup like the classic clips; ``clip_key`` per the schema's comment), ``crop`` / ``rect`` (plan §1.12),
    ``radius``, ``t_exit``, the exit (``exit_kind`` / ``exit_s``) and entrance (``enter_kind``) the stage paints the
    presenter with, ``src_fps``, the ``layout``, and on the studio wall (``fullframe``) the wall inks
    (:func:`wall_inks`). Every value is JSON-plain (``ResolvedLook._storable``)."""
    p = placement(fmt, layout, card_x)
    exit_kind, exit_s = exit_of(kind, fmt)
    out: dict[str, Any] = {
        "clip_key": p.clip,
        "crop": list(p.crop),
        "rect": list(p.rect),
        "radius": p.radius,
        "t_exit": t.t_exit,
        "exit_kind": exit_kind,
        "exit_s": exit_s,
        "enter_kind": enter_of(kind, fmt),
        "src_fps": CLIP_FPS,
        "layout": layout,
    }
    if fmt == "landscape" and layout == "fullframe" and kind == "intro":
        out.update(wall_inks())
    return out


# --- synthesised appearances (owner decision 2) --------------------------------------------------------------------------

INTRO_ID: Final = "aadhi-intro"
RECAP_ID: Final = "aadhi-recap"
#: How a teacher-facing message names a synthesised appearance (it has no scene number: it is not in the editor).
#: web/js/studio/lib/sceneName.js names them the same.
APPEARANCE_NAMES: Final[Mapping[str, str]] = {"intro": "Aadhi's intro", "recap": "Aadhi's recap"}
_SYNTH_RE: Final = re.compile(r"^aadhi-(intro|recap)(?:-[0-9]+)?$")
#: The 16:9 recap's prompt (§3.3: "Quick check: what are the 3 big ideas?") and its slot count. English until B4's
#: localisation, like hook_question's "True or false?" pill.
RECAP_PROMPT: Final = "Quick check: what are the 3 big ideas?"
RECAP_SLOTS: Final = 3
#: The synthesised intro's title is never cut (module docstring, "The intro's text"): the page sets it whole at the
#: title slot's size range (16:9: 72-120 px in up to 4 lines of the layout's box, 864-944 px wide; 9:16: 88-104 px in
#: at most 4 lines of the 420 px hook band) and reports what does not fit at the minimum, so preflight blocks the render
#: and asks for a shorter title. Measured with the HarfBuzz text fit (test_presenter.py), the minimum holds about 80
#: Latin, Devanagari or upper-case characters and 56 Tamil ones in 16:9, and 34-48 in 9:16 (28 in upper case).
#: The longest session chip (characters): "subject · unit · session" while it fits, else without the subject, else the
#: session alone. A one-line label at R-29's label floor (16:9 40 px in the 916 px pill, 9:16 44 px in the 696 px
#: column: 50 / 34 Latin characters, 36 / 20 Tamil, measured with the text fit); the page cuts a longer one with an
#: ellipsis and reports it as decoration, board_generic's kicker rule.
CHIP_MAX_CHARS: Final[Mapping[Format, int]] = {"landscape": 44, "portrait": 28}
CHIP_SEPARATOR: Final = " · "
_RICH_SPECIALS: Final = frozenset("\\$*`[]")  # rich-lite markup (web/js/player/richtext.js escapes; timeline.py)


def plain_rich(text: str) -> str:
    """``text`` with the rich-lite specials escaped, so a session title renders literally."""
    return "".join("\\" + ch if ch in _RICH_SPECIALS else ch for ch in " ".join(str(text or "").split()))


def _cut(text: str, limit: int) -> str:
    """At most ``limit`` characters, cut after a whole word with an ellipsis (a single longer word is cut inside)."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    head = text[: max(1, limit - 1)]
    space = head.rfind(" ")
    if space >= limit // 2:
        head = head[:space]
    return head.rstrip(" ,;:-·") + "…"


def chip_text(screenplay: Screenplay, fmt: Format, *, title: str = "") -> str:
    """The session chip (§3.3: subject · unit · session), the parts that fit :data:`CHIP_MAX_CHARS` (the subject is
    left out first, then the unit); '' without any part. A part that is the intro's ``title`` (the unit or the subject
    standing in for a session title the first scene already shows, :func:`intro_title`) is left out."""
    parts = [" ".join(str(p or "").split()) for p in (screenplay.subject_name, screenplay.unit_name,
                                                       screenplay.session_number)]
    named = _same_key(title)
    parts = [p for p in parts if p and not (named and _same_key(p) == named)]
    limit = CHIP_MAX_CHARS[fmt]
    while len(parts) > 1 and len(CHIP_SEPARATOR.join(parts)) > limit:
        parts = parts[1:]
    return _cut(CHIP_SEPARATOR.join(parts), limit) if parts else ""


_ELLIPSES: Final = ("…", "...")
#: A shortened heading ("Ohm's Law and the Rules That…") repeats a title it is a prefix of when at least this long.
_PREFIX_MIN: Final = 8


def _same_key(text: str | None) -> str:
    """``text`` as compared for a repeat: plain (rich-lite markup removed), case-folded, whitespace collapsed."""
    return " ".join(to_plain(str(text or "")).casefold().split())


def _shown_texts(scene: Any) -> list[str]:
    """What a scene shows as its name (compared with :func:`_same_key`): its title, and a board scene's headings."""
    out = [_same_key(getattr(scene, "title", ""))]
    for item in getattr(scene, "board", None) or []:
        if str(getattr(item.kind, "value", item.kind)) == "heading":
            out.append(_same_key(item.text))
    return [t for t in out if t]


def _repeats(candidate: str, shown: Iterable[str]) -> bool:
    """Whether ``candidate`` (a :func:`_same_key`) is one of the ``shown`` names, or a name shortened with an ellipsis
    is a prefix of it (the generator's cold open shortens a long session title)."""
    for s in shown:
        if s == candidate:
            return True
        for e in _ELLIPSES:
            head = s[: -len(e)].rstrip() if s.endswith(e) else ""
            if len(head) >= _PREFIX_MIN and candidate.startswith(head):
                return True
    return False


def intro_title(screenplay: Screenplay, fmt: Format) -> str:
    """The intro's title, whole (never cut: the page reports one that does not fit, module docstring): the session
    title, else the unit, else the subject, leaving out any the first shown scene already shows (its title or a
    heading: the intro does not repeat the lecture's own opening, step-5 review R5-4). When each of them is shown or
    empty, the first non-empty one of the session title, the first shown scene's title, the unit and the subject (the
    repeat is then kept: there is nothing else to name); '' without any (rich-lite escaped by the caller). ``fmt`` is
    kept for the callers: the title is the same in both formats."""
    first = next((s for s in screenplay.scenes if not s.hidden), None)
    shown = _shown_texts(first) if first is not None else []
    texts = [" ".join(str(c or "").split()) for c in (screenplay.session_title, screenplay.unit_name,
                                                       screenplay.subject_name)]
    for text in texts:
        if text and not _repeats(_same_key(text), shown):
            return text
    own = " ".join(str(first.title or "").split()) if first is not None else ""
    return next((t for t in (texts[0], own, texts[1], texts[2]) if t), "")


def unique_id(base: str, taken: Iterable[str]) -> str:
    """``base``, or ``base-2``, ``base-3``, ... when a screenplay scene already has that id."""
    used = set(taken)
    if base not in used:
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    return f"{base}-{n}"


def appearance_of(scene_id: str, screenplay_ids: Iterable[str]) -> Kind | None:
    """``intro`` / ``recap`` for a synthesised presenter scene (an id of this module's form that no screenplay scene
    has), else None (a screenplay scene, even one named like ours, is the screenplay's)."""
    m = _SYNTH_RE.match(scene_id)
    if m is None or scene_id in set(screenplay_ids):
        return None
    return m.group(1)  # type: ignore[return-value]


def has_presenter(scene: Any) -> bool:
    """The screenplay scene carries its own presenter (``SceneBase.look.presenter``: the §4.4 forced row)."""
    look = getattr(scene, "look", None)
    return look is not None and getattr(look, "presenter", None) is not None


@dataclass(frozen=True)
class Appearances:
    """Where Aadhi is synthesised into a Dynamic timeline: ``intro`` (before the first scene) and ``recap_before``
    (the screenplay id of the scene the recap goes before; None: no recap). Ids are unique against the screenplay."""

    intro: bool
    recap_before: str | None
    intro_id: str = INTRO_ID
    recap_id: str = RECAP_ID


def plan_appearances(screenplay: Screenplay, *, enabled: bool = True) -> Appearances | None:
    """What :func:`intro_scene` / :func:`recap_scene` add to a Dynamic timeline (None for a screenplay without a
    presentation, or with ``enabled`` False: ``DYNAMIC_AADHI_INTRO=false``, §3.4).

    The intro is synthesised unless the first shown scene is a presenter scene the director draws with
    ``aadhi_intro`` (one it cannot draw, or one a teacher's lock gives another template, would open the video without
    Aadhi; step-5 review R5-2). The recap goes before the lecture's retrieval recap (module docstring, "Which recap"):
    the last shown ``recap`` board scene without a presenter that the director draws with ``recap_retrieval`` and that
    does not come before the first chapter card, when :func:`recap_enabled`, and unless the screenplay has its own
    presenter recap scene that ``aadhi_recap`` draws (its own appearance wins; R-12 allows one recap appearance)."""
    presentation = screenplay.presentation
    if presentation is None or not enabled:
        return None
    shown = [s for s in screenplay.scenes if not s.hidden]
    if not shown:
        return None
    from .direct import row_template  # lazy: direct imports templates, which imports this module

    fmt: Format = presentation.format
    last = shown[-1].id

    def drawn(scene: Any) -> str:
        return row_template(scene, fmt, presentation, last=scene.id == last)

    ids = [s.id for s in screenplay.scenes]
    intro = not (has_presenter(shown[0]) and drawn(shown[0]) == "aadhi_intro")
    recap_before: str | None = None
    if recap_enabled(presentation):
        card = next((i for i, s in enumerate(shown) if isinstance(s, ChapterCardScene)), 0)
        retrieval = [i for i, s in enumerate(shown) if i >= card and isinstance(s, BoardScene) and s.type == "recap"
                     and not has_presenter(s) and drawn(s) == "recap_retrieval"]
        # the screenplay's own recap appearance, wherever it stands, is the lecture's one (R-12: Aadhi appears at the
        # start and in the recap only); one his template does not draw shows no Aadhi and does not count
        own = any(isinstance(s, BoardScene) and s.type == "recap" and has_presenter(s) and drawn(s) == "aadhi_recap"
                  for s in shown)
        if retrieval and not own:
            recap_before = shown[retrieval[-1]].id
    return Appearances(intro=intro, recap_before=recap_before, intro_id=unique_id(INTRO_ID, ids),
                       recap_id=unique_id(RECAP_ID, ids))


def _scene(scene_id: str, *, index: int, start: float, title: str, subtitle: str | None,
           board: Sequence[BoardItem], duration: float, audio_offset: float) -> TimedScene:
    return TimedScene(
        scene_id=scene_id, index=index, type="title", title=title, subtitle=subtitle, start=_r(start),
        duration=_r(duration), audio_offset=audio_offset,
        layout=Layout(mascot_position="hidden", show_side_panel=False), board=list(board),
    )


def intro_scene(screenplay: Screenplay, appearances: Appearances, *, index: int = 0, start: float = 0.0) -> TimedScene:
    """The synthesised opening presenter scene: the session chip (``subtitle``) and the title (a ``heading`` item,
    ``title``), silent, lasting :func:`timing` of a silent intro (5.0 s)."""
    p = screenplay.presentation
    if p is None:
        raise ValueError("a presenter scene needs a screenplay with a presentation")
    fmt: Format = p.format
    title = intro_title(screenplay, fmt)
    board = [BoardItem(id="title", kind=BoardItemKind.heading, text=plain_rich(title))] if title else []
    chip = chip_text(screenplay, fmt, title=title)
    t = timing("intro", fmt)
    return _scene(appearances.intro_id, index=index, start=start, title=title, subtitle=plain_rich(chip) or None,
                  board=board, duration=t.duration, audio_offset=t.audio_offset)


def recap_scene(screenplay: Screenplay, appearances: Appearances, *, index: int, start: float) -> TimedScene:
    """The synthesised recap appearance: Aadhi's card and the "Quick check" prompt (one ``question`` item), silent."""
    p = screenplay.presentation
    if p is None:
        raise ValueError("a presenter scene needs a screenplay with a presentation")
    t = timing("recap", p.format)
    board = [BoardItem(id="prompt", kind=BoardItemKind.question, text=plain_rich(RECAP_PROMPT))]
    return _scene(appearances.recap_id, index=index, start=start, title="", subtitle=None, board=board,
                  duration=t.duration, audio_offset=t.audio_offset)


def fold_opening_chapter(chapters: Sequence[Chapter], scenes: Sequence[TimedScene],
                         appearances: Appearances | None) -> list[Chapter]:
    """The chapters with the synthesised intro inside the first chapter (``compose.timeline.build_chapters`` gives it
    an "Introduction" chapter of its own, because the first real chapter now starts after it). A 5 s opening chapter
    would cost the lecture its first real chapter on YouTube (``chapters.youtube_chapter_list`` skips a chapter less
    than 10 s after the one before), and the design's intro wipes into "the chapter-1 accent" (§3.3): so the first
    real chapter starts at 0:00 instead, and the intro takes its accent. Unchanged when nothing was synthesised or the
    chapter list has another shape."""
    out = list(chapters)
    if (appearances is None or not appearances.intro or len(out) < 2 or len(scenes) < 2
            or scenes[0].scene_id != appearances.intro_id or out[0].start > 1e-3
            or abs(out[1].start - scenes[1].start) > 1e-3):
        return out
    return [Chapter(start=0.0, title=out[1].title), *out[2:]]


def speech_end(scene: TimedScene) -> float | None:
    """The scene-relative end of the last spoken word (None for a silent scene)."""
    ends = [w.end for b in scene.beats for w in b.words] + [b.speech_end for b in scene.beats]
    return max(ends) if ends else None


def presenter_of(timed: TimedScene, screenplay: Screenplay, fmt: Format) -> tuple[Kind, str, dict[str, Any]] | None:
    """``(kind, layout, ResolvedLook.presenter)`` of a presenter scene of the timeline - a synthesised one, or a
    screenplay scene with ``look.presenter`` - else None. The intro's layout is :func:`intro_layout` (a hook question
    on the board allows ``pip``); a screenplay scene's own ``PresenterSpec.layout`` wins when it is valid for the
    format (``card`` in 9:16, a 16:9 intro layout otherwise)."""
    presentation = screenplay.presentation
    if presentation is None:
        return None
    ids = [s.id for s in screenplay.scenes]
    kind = appearance_of(timed.scene_id, ids)
    source = next((s for s in screenplay.scenes if s.id == timed.scene_id), None)
    spec = None
    if kind is None:
        if source is None or not has_presenter(source) or not isinstance(source, BoardScene):
            return None
        spec = source.look.presenter  # type: ignore[union-attr]
        kind = "recap" if source.type == "recap" else "intro"
    has_hook = any(str(getattr(i.kind, "value", i.kind)) == "question" and (i.text or "").strip()
                   for i in timed.board)
    if fmt == "portrait":
        layout = REEL_LAYOUT
    elif kind == "recap":
        layout = RECAP_LAYOUT["landscape"]
    else:
        wanted = getattr(spec, "layout", None)
        layout = wanted if wanted in INTRO_LAYOUTS and (has_hook or wanted != "pip") else \
            intro_layout(presentation, has_hook=has_hook)
    end = speech_end(timed)
    t = timing(kind, fmt, end)
    # a scene the builder timed otherwise (a screenplay scene's narration may run past 8 s): the exit stays inside it
    t_exit = min(t.t_exit, max(0.0, float(timed.duration) - t.exit_seconds))
    t = Timing(t.audio_offset, _r(t_exit), t.exit_seconds, t.duration, t.too_long)
    card_x = reel_card_x(presentation) if fmt == "portrait" else None
    return kind, layout, look_presenter(kind, fmt, layout, t, card_x=card_x)


def template_of(kind: Kind) -> str:
    """The template that draws an appearance."""
    return "aadhi_intro" if kind == "intro" else "aadhi_recap"


TEMPLATES: Final = ("aadhi_intro", "aadhi_recap")


def plan_for(timed: TimedScene, screenplay: Screenplay, fmt: Format) -> tuple[str, str] | None:
    """``(template, variant)`` of a synthesised presenter scene (``direct.plan``'s answer for a timed scene with no
    screenplay scene), else None. The variant is the layout :func:`presenter_of` places Aadhi with, so the page lays its
    text out around the box the look stores."""
    if appearance_of(timed.scene_id, [s.id for s in screenplay.scenes]) is None:
        return None
    found = presenter_of(timed, screenplay, fmt)
    if found is None:
        return None
    kind, layout, _ = found
    return template_of(kind), layout


def apply_to_looks(screenplay: Screenplay, scenes: Sequence[TimedScene], looks: Sequence[Any]) -> list[Any]:
    """The resolved looks with Aadhi's parts filled in (the resolver's last step; ``compose.presentation.resolve_looks``).

    For every scene the director gave ``aadhi_intro`` / ``aadhi_recap`` (a synthesised appearance or a screenplay
    presenter scene; a scene drawn by any other template - ``hook_question`` when ``DYNAMIC_AADHI_INTRO`` is off - gets
    no presenter): ``look.presenter`` (:func:`presenter_of`) and ``look.variant`` = its layout. For an intro also
    ``look.refs.accent_next``, the next scene's block colour (the brand wipe's, R-54), and the next scene enters by a
    crossfade of at least 300 ms out of the wipe's block (R-54: a full-frame colour change always cross-fades; plan §2
    "the next scene enters with a crossfade >= 300 ms from the block"). Returns new looks; the inputs are not changed."""
    from ...schemas.timeline import LookRefs
    from ..presentation import CROSSFADE_MIN_SECONDS, crossfade_seconds

    presentation = screenplay.presentation
    out = list(looks)
    if presentation is None:
        return out
    fmt: Format = presentation.format
    for i, timed in enumerate(scenes):
        look = out[i]
        if look is None or look.template not in TEMPLATES:
            continue
        found = presenter_of(timed, screenplay, fmt)
        if found is None:
            continue
        kind, layout, spec = found
        if template_of(kind) != look.template:
            continue
        update: dict[str, Any] = {"presenter": spec, "variant": layout}
        nxt = out[i + 1] if i + 1 < len(out) else None
        if kind == "intro" and nxt is not None:
            block = nxt.tokens.get("block.base") or nxt.tokens.get("struct.accent")
            if block:
                refs = look.refs if look.refs is not None else LookRefs()
                update["refs"] = refs.model_copy(update={"accent_next": block})
            seconds = crossfade_seconds(float(scenes[i + 1].duration))
            fade = max(CROSSFADE_MIN_SECONDS, seconds) if seconds is not None else None
            if fade is not None:
                out[i + 1] = nxt.model_copy(update={"transition_in": {"kind": "crossfade", "seconds": fade}})
        out[i] = look.model_copy(update=update)
    return out


# --- the templates' text slots (templates.py ``SlotSpec`` rows; the JS modules' ``slotRows`` mirror them) --------------
# aadhi_intro.js LAYOUT / CHIP / PORTRAIT and aadhi_recap.js LAYOUT: the boxes and size ranges the pages fit text in.
# test_presenter.py runs both JS ``slotRows()`` and compares them with these, value for value; templates.py wraps each
# row in a ``SlotSpec`` (``SlotSpec(**row)``). Every size is a B2 proposal pending the owner's review of the frames.

#: 16:9 title stack per layout: (box x0, y0, x1, y1), title (min, max, lines, prefer), question (min, max, lines).
INTRO_STACK: Final[Mapping[str, tuple[tuple[int, int, int, int], tuple[int, int, int, int | None], tuple[int, int, int]]]] = {
    "fullframe": (WALL_TITLE_BOX, (72, 120, 4, 2), (60, 64, 3)),
    "card_left": ((880, 180, 1824, 900), (72, 120, 4, 2), (60, 64, 3)),
    "pip": ((640, 54, 1824, 912), (64, 80, 2, 1), (88, 112, 3)),
    "split": ((960, 54, 1824, 912), (72, 120, 4, 2), (60, 64, 3)),
}
#: The session chip: 16:9 a pill (size, weight, horizontal padding) at R-29's 16:9 label floor (§3.3's 36 px is below
#: it); 9:16 a label in the progress row (x 192-888) at the 9:16 label floor.
INTRO_CHIP: Final[Mapping[Format, tuple[int, int, int]]] = {"landscape": (40, 600, 24), "portrait": (44, 600, 0)}
INTRO_CHIP_PORTRAIT_W: Final = 888 - 192
#: 9:16: the hook band (x 192-888, y 340-760): the headline (88-104 px, <= 4 lines) and the eyebrow (44 px, <= 2 lines).
HOOK_BAND_W: Final = 888 - 192
INTRO_HEADLINE: Final = (88, 104, 4)
INTRO_EYEBROW: Final = (44, 600, 2)
#: The 16:9 recap's prompt card (x 880-1824, y 180-900), its padding and the prompt (min, max, lines); 9:16 the band.
RECAP_CARD: Final = (880, 180, 1824, 900)
RECAP_PAD: Final = 48
RECAP_PROMPT_SIZE: Final[Mapping[Format, tuple[int, int, int]]] = {"landscape": (64, 80, 3), "portrait": (88, 104, 4)}
DISPLAY_LINE_HEIGHT: Final = 1.05
LABEL_LINE_HEIGHT: Final = 1.35
TRACKING_EM: Final = -0.015

SlotRow = dict[str, Any]


def _display(size: tuple[int, int, int] | tuple[int, int, int, int | None], w: int) -> SlotRow:
    prefer = size[3] if len(size) > 3 else None  # type: ignore[misc]
    return {"w": w, "max_lines": size[2], "size": (float(size[0]), float(size[1])), "role": "title", "face": "display",
            "weight": 700, "line_height": DISPLAY_LINE_HEIGHT, "tracking_em": TRACKING_EM, "prefer_lines": prefer,
            "critical": True}


def _label(size: int, weight: int, lines: int, w: int, *, critical: bool) -> SlotRow:
    return {"w": w, "max_lines": lines, "size": (float(size), float(size)), "role": "label", "face": "body",
            "weight": weight, "line_height": LABEL_LINE_HEIGHT, "tracking_em": 0.0, "prefer_lines": None,
            "critical": critical}


def intro_slots() -> dict[str, dict[Format, dict[str, SlotRow]]]:
    """``aadhi_intro``'s slots: 16:9 ``chip`` / ``title`` / ``question`` per layout; 9:16 ``chip`` / ``headline`` /
    ``eyebrow`` (``default``). The chip is decoration (not critical: board_generic's kicker rule)."""
    out: dict[str, dict[Format, dict[str, SlotRow]]] = {
        "chip": {"landscape": {}, "portrait": {}}, "title": {"landscape": {}}, "question": {"landscape": {}},
        "headline": {"portrait": {}}, "eyebrow": {"portrait": {}}}
    size, weight, pad = INTRO_CHIP["landscape"]
    for layout in INTRO_LAYOUTS:
        box, title, question = INTRO_STACK[layout]
        w = box[2] - box[0]
        out["chip"]["landscape"][layout] = _label(size, weight, 1, w - 2 * pad, critical=False)
        out["title"]["landscape"][layout] = _display(title, w)
        out["question"]["landscape"][layout] = _display(question, w)
    psize, pweight, _ = INTRO_CHIP["portrait"]
    out["chip"]["portrait"]["default"] = _label(psize, pweight, 1, INTRO_CHIP_PORTRAIT_W, critical=False)
    out["headline"]["portrait"]["default"] = _display(INTRO_HEADLINE, HOOK_BAND_W)
    out["eyebrow"]["portrait"]["default"] = _label(INTRO_EYEBROW[0], INTRO_EYEBROW[1], INTRO_EYEBROW[2], HOOK_BAND_W,
                                                   critical=True)
    return out


def recap_slots() -> dict[str, dict[Format, dict[str, SlotRow]]]:
    """``aadhi_recap``'s slot: the ``prompt`` (16:9 inside the prompt card's padding; 9:16 the hook band)."""
    return {"prompt": {
        "landscape": {"default": _display(RECAP_PROMPT_SIZE["landscape"], RECAP_CARD[2] - RECAP_CARD[0] - 2 * RECAP_PAD)},
        "portrait": {"default": _display(RECAP_PROMPT_SIZE["portrait"], HOOK_BAND_W)},
    }}


# --- the frame cache (plan §2 "Aadhi", §1.12) --------------------------------------------------------------------------

CACHE_DIR: Final = ("derivatives", "aadhi")
DONE: Final = "done.json"
CACHE_VERSION: Final = 1
JPEG_QUALITY: Final = 2  # ffmpeg -q:v (the engine's MEDIA_JPEG_QUALITY)
#: An abandoned temporary directory (a worker killed mid-extraction) older than this is swept.
STALE_TMP_SECONDS: Final = 3600.0


def clip_digest(path: str | Path) -> str:
    """SHA-256 of a clip's bytes (the cache's key: a re-encoded clip gets a fresh set)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _check_crop(crop: Sequence[int]) -> tuple[int, int, int, int]:
    c = tuple(int(v) for v in crop)
    if len(c) != 4 or c[0] < 0 or c[1] < 0 or c[2] < 2 or c[3] < 2:
        raise ValueError(f"bad crop {crop!r}")
    return c  # type: ignore[return-value]


def _check_size(size: Sequence[int]) -> tuple[int, int]:
    s = tuple(int(v) for v in size)
    if len(s) != 2 or s[0] < 2 or s[1] < 2 or s[0] > 4096 or s[1] > 4096:
        raise ValueError(f"bad size {size!r}")
    return s  # type: ignore[return-value]


def cache_dir(root: str | Path, digest: str, crop: Sequence[int], size: Sequence[int]) -> Path:
    """``<root>/derivatives/aadhi/<digest>/<x>_<y>_<w>_<h>/<W>x<H>`` (``root``: ``RENDER_WORK_DIR``)."""
    if not re.fullmatch(r"[0-9a-f]{16,64}", digest):
        raise ValueError("a clip digest is lowercase hex")
    c, s = _check_crop(crop), _check_size(size)
    return Path(root).joinpath(*CACHE_DIR, digest[:32], "_".join(map(str, c)), f"{s[0]}x{s[1]}")


def frame_name(n: int) -> str:
    """The file of source frame ``n`` (the engine's ``f_<index>.jpg``, ``media.frame_files``)."""
    return f"f_{int(n):06d}.jpg"


def extract_args(src: str | Path, *, crop: Sequence[int], size: Sequence[int], out_dir: str | Path,
                 first: int = 0, count: int = CLIP_FRAMES) -> list[str]:
    """ffmpeg arguments (after the base ``ffmpeg -hide_banner -nostdin -y -loglevel error``) writing source frames
    ``first .. first + count - 1`` of ``src`` cropped to ``crop`` BEFORE scaling to exactly ``size`` (plan §1.12; crop
    and size share an aspect, so nothing is stretched), as ``<out_dir>/f_<index>.jpg``. Frame-exact: no ``fps``
    filter, every decoded frame in order from ``first``."""
    c, s = _check_crop(crop), _check_size(size)
    if count < 1 or first < 0:
        raise ValueError("extraction needs first >= 0 and count >= 1")
    vf = f"crop={c[2]}:{c[3]}:{c[0]}:{c[1]},scale=w={s[0]}:h={s[1]}:flags=bicubic,setsar=1"
    seek = ["-ss", f"{max(0.0, (first - 0.5) / CLIP_FPS):.6f}"] if first > 0 else []
    pattern = (Path(out_dir) / "f_%06d.jpg").as_posix()
    return [*seek, "-i", str(src), "-map", "0:v:0", "-an", "-sn", "-dn", "-vf", vf, "-frames:v", str(int(count)),
            "-q:v", str(JPEG_QUALITY), "-start_number", str(int(first)), "-f", "image2", pattern]


def _complete(directory: Path, digest: str, crop: Sequence[int], size: Sequence[int]) -> bool:
    try:
        done = json.loads((directory / DONE).read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(done, dict) and done.get("v") == CACHE_VERSION and done.get("digest") == digest
            and done.get("crop") == list(crop) and done.get("size") == list(size)
            and isinstance(done.get("frames"), int) and done["frames"] > 0
            and all((directory / frame_name(n)).is_file() for n in range(done["frames"])))


Runner = Callable[[list[str]], None]


def _run(args: list[str]) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=600)


def ensure_frames(root: str | Path, clip: str | Path, *, crop: Sequence[int], size: Sequence[int],
                  ffmpeg: str = "ffmpeg", runner: Runner | None = None) -> tuple[Path, int]:
    """The cached frames of ``clip``'s ``crop`` at ``size``: ``(directory, frame count)``, extracting them first when
    the set is missing (blocking; call through ``asyncio.to_thread``). The set is written into a fresh temporary
    directory beside its final place, completed with :data:`DONE`, and renamed into place as a whole; a worker that
    loses a race finds the other's complete set and discards its own."""
    clip = Path(clip)
    digest = clip_digest(clip)
    c, s = _check_crop(crop), _check_size(size)
    final = cache_dir(root, digest, c, s)
    if _complete(final, digest, c, s):
        return final, int(json.loads((final / DONE).read_text("utf-8"))["frames"])
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = final.parent / f".tmp-{final.name}-{os.getpid()}-{secrets.token_hex(4)}"
    tmp.mkdir()
    try:
        args = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                *extract_args(clip, crop=c, size=s, out_dir=tmp)]
        (runner or _run)(args)
        frames = 0
        while (tmp / frame_name(frames)).is_file():
            frames += 1
        if frames == 0:
            raise RuntimeError(f"ffmpeg wrote no frame of {clip.name}")
        (tmp / DONE).write_text(json.dumps({"v": CACHE_VERSION, "digest": digest, "crop": list(c), "size": list(s),
                                            "frames": frames}, sort_keys=True), "utf-8")
        try:
            os.rename(tmp, final)
        except OSError:
            if not _complete(final, digest, c, s):  # an incomplete leftover (never written by this module): replace
                shutil.rmtree(final, ignore_errors=True)
                os.rename(tmp, final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return final, int(json.loads((final / DONE).read_text("utf-8"))["frames"])


def sweep_cache(root: str | Path, keep: Iterable[str], *, now: float | None = None,
                stale_after: float = STALE_TMP_SECONDS) -> list[Path]:
    """Remove the cached sets of clips whose digest is not in ``keep`` (the current clips' :func:`clip_digest`), and
    temporary directories older than ``stale_after`` seconds. Returns what was removed. ``now``: the reference time
    (a test seam; the sweep reads the clock only to age abandoned temporary directories)."""
    base = Path(root).joinpath(*CACHE_DIR)
    if not base.is_dir():
        return []
    wanted = {k[:32] for k in keep}
    t = time.time() if now is None else now
    removed: list[Path] = []
    for entry in sorted(base.iterdir()):
        if entry.is_dir() and entry.name not in wanted:
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry)
            continue
        for tmp in sorted(entry.glob("*/.tmp-*")) if entry.is_dir() else []:
            with contextlib.suppress(OSError):
                if t - tmp.stat().st_mtime > stale_after:
                    shutil.rmtree(tmp, ignore_errors=True)
                    removed.append(tmp)
    return removed


__all__ = [
    "AUDIO_OFFSET", "CARD_RADIUS", "CHIP_MAX_CHARS", "CLIPS", "CLIP_FPS", "CLIP_FRAMES", "CLIP_SECONDS", "CLIP_SIZE",
    "FORMATS", "INTRO_ID", "INTRO_LAYOUTS", "LANDSCAPE_PLACEMENTS", "MAX_SECONDS", "MIN_SECONDS", "RECAP_ID",
    "RECAP_LAYOUT", "RECAP_PROMPT", "RECAP_SLOTS", "REEL_CARD_XS", "REEL_CROP", "REEL_LAYOUT",
    "WALL", "WALL_TITLE_BOX", "Appearances", "Placement", "Timing", "Wall", "appearance_of", "cache_dir",
    "chip_text", "clip_digest", "ensure_frames", "enter_of", "exit_of", "extract_args", "fold_opening_chapter", "frame_name", "has_presenter",
    "intro_layout", "intro_scene", "intro_slots", "intro_title", "look_presenter", "placement", "plain_rich", "plan_appearances",
    "plan_for", "apply_to_looks", "presenter_of", "recap_enabled", "recap_scene", "recap_slots", "reel_card_x", "reel_placement", "speech_end", "sweep_cache",
    "APPEARANCE_NAMES", "TEMPLATES", "template_of", "timing", "unique_id", "wall_inks",
]
