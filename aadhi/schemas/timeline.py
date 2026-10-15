"""Timeline schema: a fully resolved, fully timed lecture.

Produced by ``aadhi.compose.timeline.build_timeline`` after the asset stage. It is the ONLY
input of both the live web player (``web/js/player``) and the deterministic MP4 renderer
(``aadhi.compose.video``), which guarantees the export matches browser playback.

URLs vs asset keys
------------------
Stored timelines carry **asset keys** (``asset_key``/``audio_asset_key``); every ``url`` field is
filled at *serve time* by ``aadhi.compose.timeline.resolve_urls(timeline, store)`` (so presigned
or CDN URLs never go stale). The MP4 renderer never uses URLs: it reads inputs through
``AssetStore.local_copy(asset_key)``. Branding URLs are stable app paths (``/branding/...``).

Time base (enforced by ``Timeline`` validator)
---------------------------------------------
* ``scenes[0].start == intro.duration`` (0 without intro); scenes never overlap:
  ``scenes[i+1].start == scenes[i].start + scenes[i].duration``;
  ``total_duration == scenes[-1].start + scenes[-1].duration`` (or the intro alone).
* Each scene fades in over its first ``transition_seconds``; narration starts at
  ``audio_offset`` (scene-relative). Beat, word, caption, quiz and panel times inside a scene
  are relative to the scene start (``audio_offset`` already added).
* ``Timeline.captions`` and ``Timeline.chapters`` are absolute (include the intro).

Audio
-----
Each scene has at most one narration file that already contains the beat clips (trimmed to a
constant inter-beat gap) and ``pause_after`` silences, so beat starts are exact offsets.

Dynamic video (v3, design §6.7 / §8.2)
-------------------------------------
``Timeline.presentation`` / ``music`` / ``sfx`` and ``TimedScene.look`` / ``events`` / ``kinetic_captions`` /
``overlays`` (plus the clip fields of ``MediaRef``) are inert from B0: left out of the JSON at their defaults,
so classic timelines are unchanged. A timeline that uses one is written with ``schema_version = 3`` and a newer
timeline is refused (``aadhi.schemas.features``). Resolved tokens are stored, so an old timeline keeps its look
when the catalogue changes. Events, kinetic chunks and overlays stay inside their scene (validator).

B2a (plan B2 §1.2/§1.3) adds, each left out of the JSON at its default so a B1 timeline (``engine_version`` 1) reads
and writes back byte-identically: ``TimelinePresentation.content_alt`` / ``family`` / ``mode`` / ``motion_scale`` /
``entity_colors`` and ``ResolvedLook.fixes`` / ``icons`` / ``refs`` / ``accent_fade`` / ``continues_from``; the B2
resolver (``engine_version`` 2) also stores the derived structure colours in ``ResolvedLook.tokens``.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from .features import check_reader_version, marked_version
from .jsonsafe import check_storable
from .screenplay import (
    BoardItem,
    CatalogueId,
    ConceptNode,
    LearningObjective,
    MascotPosition,
    SidePanel,
    Slug,
    StrictModel,
)

TIMELINE_SCHEMA_VERSION = 2  # classic timelines; a timeline using a v3 field is written as 3
_EPS = 1e-3


class TimedWord(StrictModel):
    text: str
    start: float
    end: float


class CaptionCue(StrictModel):
    start: float
    end: float
    text: str  # plain text, already line-broken (max 2 lines x 42 chars)


class TimedBeat(StrictModel):
    beat_id: str
    index: int  # position in the scene's combined beat order (main beats, then reveal beats)
    phase: Literal["main", "reveal"] = "main"  # quiz reveal beats use "reveal"
    start: float  # scene-relative
    speech_end: float  # end of spoken audio for this beat
    end: float  # speech_end + pause_after
    narration: str
    board_item_id: str | None = None  # revealed at ``start``
    fill_item_id: str | None = None  # blank example step filled at ``start``
    highlight_item_ids: list[str] = Field(default_factory=list)  # emphasised during [start, end)
    words: list[TimedWord] = Field(default_factory=list)  # scene-relative
    captions: list[CaptionCue] = Field(default_factory=list)  # scene-relative
    visual_cue: str | None = None
    estimated: bool = False  # True in editor previews built before TTS exists


class KenBurnsPoint(StrictModel):
    cx: float = 0.5  # focus centre, normalised 0..1
    cy: float = 0.5
    scale: float = 1.0  # >= 1


class KenBurns(StrictModel):
    """Linear pan/zoom over [0, scene.duration]; the CSS player and ffmpeg zoompan both read this."""

    start: KenBurnsPoint = Field(default_factory=KenBurnsPoint)
    end: KenBurnsPoint = Field(default_factory=lambda: KenBurnsPoint(scale=1.15))


class MediaRef(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {
        "trim_in": "clip", "audio": "clip", "volume_db": "clip", "focus": "clip", "src_fps": "clip",
    }
    V3_VALUES: ClassVar[dict[str, dict[str, str]]] = {
        "fit": {"contain_blur": "clip"}, "end_behavior": {"clamp": "clip"},
    }

    kind: Literal["video", "image", "gif"]
    asset_key: str | None = None  # stored asset (None only for external hotlinks such as GIPHY)
    url: str | None = None  # filled at serve time
    mime: str | None = None
    duration: float | None = None  # seconds, for video
    width: int | None = None
    height: int | None = None
    # v3 "contain_blur": contained over a blurred cover copy of itself (inserted clips, §8.2).
    fit: Literal["contain", "cover", "contain_blur"] = "contain"
    # manim -> freeze on last frame; v3 "clamp": hold the last frame without looping (inserted clips). The default
    # stays "loop" so classic timelines are unchanged.
    end_behavior: Literal["loop", "freeze", "clamp"] = "loop"
    ken_burns: KenBurns | None = None  # stills used as ai_video fallbacks
    attribution: str | None = None  # e.g. "Powered by GIPHY"
    link_url: str | None = None
    render_in_mp4: bool = True  # False for hotlinked GIFs (live player only)
    # v3 (inserted clips and overlays): each is left out of the JSON at its default.
    trim_in: float = Field(default=0.0, ge=0.0, exclude_if=lambda v: v == 0)  # seconds into the source
    audio: bool = Field(default=False, exclude_if=lambda v: v is False)  # the clip's own sound plays
    volume_db: float = Field(default=0.0, ge=-60.0, le=24.0, exclude_if=lambda v: v == 0)
    focus: tuple[float, float] | None = Field(default=None, exclude_if=lambda v: v is None)  # cover focus, 0..1
    src_fps: float | None = Field(default=None, gt=0, le=240, exclude_if=lambda v: v is None)


class ResolvedSidePanel(StrictModel):
    panel: SidePanel
    media: MediaRef | None = None  # figure/image/gif/manim resolved media
    show_at: float = 0.0  # scene-relative seconds


class QuizTiming(StrictModel):
    question: str
    options: list[str]
    correct_index: int
    feedback_wrong: list[str]
    explanation: str
    countdown_start: float  # scene-relative
    countdown_seconds: int
    reveal_start: float  # scene-relative


class SyncCue(StrictModel):
    """A visual moment anchored to a spoken word (``aadhi.compose.sync``); scene-relative seconds.

    * ``var``: formula legend row ``part`` (``var:<n>``) of ``item_id`` appears at ``start``;
    * ``emphasis``: board item ``item_id`` is highlighted during ``[start, end)`` and its ``part``
      (``column:<n>`` of a table, ``term`` of a definition, None = the whole item) emphasised; it
      replaces the authored highlight of that item in beat ``beat_id``;
    * ``output``: the terminal side panel's output lines start at ``start``;
    * ``focus``: the side panel is pulsed during ``[start, end)``.

    ``words`` are the spoken words that anchor the cue (shown in the Studio, never to students).
    """

    kind: Literal["var", "emphasis", "output", "focus"]
    start: float
    end: float | None = None  # emphasis / focus only
    item_id: str | None = None
    part: str | None = None
    beat_id: str | None = None
    words: str = ""


class Layout(StrictModel):
    mascot_position: MascotPosition = "left"
    show_side_panel: bool = True
    # simulation / ai_video / interactive: media fills the board region (mascot stays visible
    # beside it unless mascot_position == "hidden").
    fullscreen_media: bool = False
    # Cue bubble beside Aadhi (thinking dots / ? / check; web/js/player/mascot-state.js), drawn in the
    # player and the MP4 alike. False only when Settings.mascot_cues is off; left out of the JSON when on.
    mascot_cues: bool = Field(default=True, exclude_if=lambda v: v is True)


# ---------------------------------------------------------------------------
# Dynamic video (v3): presentation, resolved looks, visual events, kinetic captions, music, SFX, overlays
# ---------------------------------------------------------------------------


def _storable(v: Any) -> Any:
    check_storable(v)  # free-form values: checked like stored JSON
    return v


# --- B2a additions (plan B2 §1.2, §1.3; deviation register 4): every new field is left out of the JSON at its
# default, so a timeline the B1 resolver built (engine_version 1) reads and writes back byte-identically, and the stage
# feature-detects each field instead of branching on the version.

HexColour = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$")]
EntityKey = Annotated[str, Field(min_length=1, max_length=80)]  # as Presentation.entity_colors' keys
EntitySlot = Annotated[int, Field(ge=0, le=2)]  # content role entity:<slot> (§4.7: three entity slots)
SlotKey = Annotated[str, Field(min_length=1, max_length=60)]  # a template's text slot (preflight TextBox.key)
MAX_ENTITY_COLORS = 60  # as Presentation.entity_colors
# Icon geometry (plan §1.2: "normalised to path d, capped, sanitised both sides"): only SVG path data, starting with a
# moveto; numbers, commands, separators. No attribute, URL, entity or markup can be expressed in it.
ICON_PATH_RE = r"^\s*[Mm][MmZzLlHhVvCcSsQqTtAa0-9eE.,+\-\s]*$"
ICON_PATH_MAX_CHARS = 4096
ICON_PATHS_MAX = 32
ICONS_PER_SCENE_MAX = 32
ROADMAP_MAX = 12


class TimelineFonts(StrictModel):
    """The video's type families by role (§4.6, plan §1.3): catalogue ids of ``compose/dynamic/faces.py`` (the stage
    resolves them through its generated copy, ``web/js/dynamic/faces.gen.js``, never as CSS text). Each key is left
    out when unset, so the B1 resolver's ``{display, body, mono}`` reads and writes back byte-identically.

    ``indic`` (B2b) names the Indic group (``noto_sans``: Noto Sans per script, B1's faces; ``anek``: Anek per script)
    and marks the B2 form, which the stage feature-detects: with it every role is drawn as named (when vendored for
    that role) and the render page derives its font requests from these roles; without it (B1 and B2a timelines) the
    stage draws ``display`` as B1 did (``outfit`` or ``inter``, any other id as Outfit) with Inter, JetBrains Mono and
    the Noto Sans faces, and loads B1's requests. The B2b resolver stores the B2 form naming exactly those faces."""

    display: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)
    body: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)
    mono: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)
    indic: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)


class TimelinePresentation(StrictModel):
    style: Literal["dynamic"]
    format: Literal["landscape", "portrait"]
    safe_zone: str = "universal"
    content: dict[str, str]  # resolved content tokens (hex): ink, ink2, entity:0..2, warn, ok, mark, focus
    fonts: TimelineFonts  # role -> family id (B2b: a typed model; the keys B1 wrote, plus ``indic``)
    personality: str
    caption_style: str
    cue_term_style: str
    captions_burn: bool
    sfx: bool
    motion: Literal["standard", "calm"]
    generated_seconds: float = Field(ge=0.0)  # R-01 (§1.4)
    # The look-engine generation (plan §1.3, deviation 21; design §6.7 called it the stage contract): 1 = B1's
    # resolver, 2 = B2's (derived structure colours in look.tokens, the fields below). The stage contract stays
    # aadhiStage v3. A stored Dynamic timeline of the current revision below the server's generation is rebuilt when
    # served (aadhi.api.timelines.current_timeline); the stage refuses one above what it knows.
    engine_version: int = Field(default=1, ge=1)
    # B2a (plan §1.3), each left out at its default:
    # content tokens of the OTHER mode (themes.content_alt_tokens): the text on the dark code-editor surface of a light
    # family (§4.3 code_walkthrough), read through roles.on(mode) (plan §1.9).
    content_alt: dict[str, str] = Field(default_factory=dict, exclude_if=lambda v: not v)
    family: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)  # the resolved theme family
    mode: Literal["dark", "light"] | None = Field(default=None, exclude_if=lambda v: v is None)  # the family's mode
    # The personality's duration scale (§4.8 table: Crisp x0.85, Soft x1.15); 1 = B1's timings.
    motion_scale: float = Field(default=1.0, gt=0.0, le=4.0, exclude_if=lambda v: v == 1.0)
    # R-30 / §4.7: the lesson-wide entity map, normalised symbol or entity name -> content slot (entity:0..2), copied
    # from Presentation.entity_colors (deviation 22: one map per lesson, not per scene).
    entity_colors: dict[EntityKey, EntitySlot] = Field(default_factory=dict, exclude_if=lambda v: not v)
    # B2c (plan §1.4 ``ctx.motion``): the text entrance of the personality the stage draws (§4.8 table: Crisp
    # ``baseline_reveal``, Soft ``fade_rise``; themes.Personality.text_entrances), so the stage reads it from the
    # timeline, never from the catalogue (plan §1.1). None (left out): the stage's default, ``fade_rise``.
    text_entrance: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)

    @field_validator("entity_colors")
    @classmethod
    def _few_entities(cls, v: dict[str, int]) -> dict[str, int]:
        if len(v) > MAX_ENTITY_COLORS:
            raise ValueError(f"at most {MAX_ENTITY_COLORS} entity colours")
        return v


class SlotFix(StrictModel):
    """One text slot's preflight remedy (§7.12): ``size`` caps the slot's font size (px, the slot's minimum when it
    overflows), ``scrim`` puts the >= 55% scrim behind text drawn on the background (R-53). Each key is left out when
    unset, so a slot fixed by one remedy carries only that key."""

    size: float | None = Field(default=None, gt=0.0, le=1000.0, exclude_if=lambda v: v is None)
    scrim: bool = Field(default=False, exclude_if=lambda v: v is False)


class LookFixes(StrictModel):
    """Preflight's remedies of one scene (plan §1.2, deviation 2: a channel of their own instead of B1's
    ``variant = "safe"`` / ``text_fit[slot]``, which direct.py and textfit.py own in B2). Written by
    ``compose.dynamic.preflight.apply_remedies`` into the served timeline (MP4 only until B4 persists them, deviation
    24); always both keys when present: ``safe`` (the template's compact, zone-safe layout, R-59) and ``slots``."""

    safe: bool = False
    slots: dict[SlotKey, SlotFix] = Field(default_factory=dict)


class LookIcon(StrictModel):
    """An icon's geometry, stored in the look so the timeline stays self-contained and a library update never
    restyles an old video (plan §1.2; filled by B2b). ``viewBox`` is ``[min_x, min_y, width, height]``; ``paths`` are
    SVG path data only (:data:`ICON_PATH_RE`), capped; the stage sanitises them again before drawing."""

    name: str = Field(min_length=1, max_length=64)
    set: str = Field(min_length=1, max_length=40)
    version: str = Field(min_length=1, max_length=40)
    viewBox: tuple[float, float, float, float]  # the SVG attribute's own spelling (plan §1.2)
    stroke_width: float = Field(default=2.0, ge=0.0, le=8.0)
    filled: bool = False
    paths: list[Annotated[str, Field(min_length=1, max_length=ICON_PATH_MAX_CHARS, pattern=ICON_PATH_RE)]] = Field(
        min_length=1, max_length=ICON_PATHS_MAX)

    @field_validator("viewBox")
    @classmethod
    def _positive_box(cls, v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        if not (v[2] > 0 and v[3] > 0):
            raise ValueError("an icon viewBox needs a positive width and height")
        return v


class RoadmapEntry(StrictModel):
    """One chapter of a bumper's mini-roadmap (§4.3 chapter_bumper: past chapters ticked, the current one lit)."""

    title: str = Field(max_length=240)
    state: Literal["done", "current", "upcoming"]


class RefAnchor(StrictModel):
    """The previous chapter's anchor visual for a ``builds_on`` bumper's "from last chapter" chip (§4.3, RV L-12):
    its formula (``tex``), key figure or last takeaway (``text``); exactly one of ``text`` and ``tex``."""

    kind: Literal["formula", "figure", "takeaway"]
    text: str | None = Field(default=None, max_length=240, exclude_if=lambda v: v is None)
    tex: str | None = Field(default=None, max_length=600, exclude_if=lambda v: v is None)
    icon: CatalogueId | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _one_body(self) -> "RefAnchor":
        if (self.text is None) == (self.tex is None):
            raise ValueError("an anchor has exactly one of text and tex")
        return self


class RefPrediction(StrictModel):
    """A ``predict`` scene's question, carried as the "Your prediction?" chip of the explaining scene (§4.3 predict)
    until it resolves at ``resolve_t`` (scene-relative seconds of the answering beat; None: not in this scene)."""

    text: str = Field(max_length=240)
    resolve_t: float | None = Field(default=None, ge=0.0, exclude_if=lambda v: v is None)


class RefNext(StrictModel):
    title: str = Field(max_length=240)


class LookRefs(StrictModel):
    """Cross-scene data a template shows, denormalised by the resolver (plan §1.2) so a scene renders from its own
    slice (the chunk key hashes it): the roadmap, the builds_on anchor, the open prediction, the next scene's title
    (outro, aadhi_intro) and ``accent_next``, the next scene's block colour (the intro's brand wipe, plan §2 R-54).
    Each key is left out when unset. Filled from B2e."""

    roadmap: list[RoadmapEntry] = Field(default_factory=list, max_length=ROADMAP_MAX, exclude_if=lambda v: not v)
    anchor: RefAnchor | None = Field(default=None, exclude_if=lambda v: v is None)
    prediction: RefPrediction | None = Field(default=None, exclude_if=lambda v: v is None)
    next: RefNext | None = Field(default=None, exclude_if=lambda v: v is None)
    accent_next: HexColour | None = Field(default=None, exclude_if=lambda v: v is None)


class ResolvedLook(StrictModel):
    template: str
    variant: str | None = None
    # Per scene (resolved hex). B1 (engine_version 1): struct.accent, bg, bg-alt, bg-tint, surface. From B2a the
    # resolver adds the colours the stage derived at run time (plan §1.2; themes.SCENE_TOKEN_KEYS): block.base,
    # block.highlight, block.ink, block.ink2 and scrim (#RRGGBBAA).
    tokens: dict[str, str]
    background: dict[str, Any]  # {preset, params, seed, frozen_at: float | None}
    transition_in: dict[str, Any]  # {kind, seconds}
    hold_reason: str | None = None
    presenter: dict[str, Any] | None = None  # {clip_key, crop:[x,y,w,h], rect:[x,y,w,h], t_exit, src_fps}
    text_fit: dict[str, Any] = Field(default_factory=dict)  # slot -> {size, lines} from textfit.py
    # B2a (plan §1.2), each left out at its default:
    fixes: LookFixes | None = Field(default=None, exclude_if=lambda v: v is None)  # preflight-owned (above)
    icons: dict[Slug, LookIcon] = Field(default_factory=dict, max_length=ICONS_PER_SCENE_MAX,
                                        exclude_if=lambda v: not v)  # board item id -> icon
    refs: LookRefs | None = Field(default=None, exclude_if=lambda v: v is None)
    # R-52: seconds over which the structure accent cross-fades into this scene when no wipe carries the change.
    accent_fade: float | None = Field(default=None, ge=0.6, le=1.0, exclude_if=lambda v: v is None)
    # The previous scene this one continues (a split's part B starts from A's final visual state; §8.3, R-20).
    continues_from: Slug | None = Field(default=None, exclude_if=lambda v: v is None)

    _chk = field_validator("background", "transition_in", "presenter", "text_fit")(_storable)


VisualEventKind = Literal[
    "reveal", "fill", "cue_term", "look_here", "draw", "count", "var", "output", "countdown", "answer", "exit"
]


class VisualEvent(StrictModel):
    """Python decides WHEN (events.py); the stage decides HOW. Scene-relative seconds."""

    t: float
    kind: VisualEventKind
    target: str | None = None  # board item id, "item:part", "panel"
    dur: float | None = Field(default=None, ge=0.0)
    anchored: bool = False  # target named within +-300 ms (§1.4); only these count for R-15
    sfx: str | None = None
    words: str = ""  # anchor words (Studio only)


class KineticWord(StrictModel):
    text: str
    start: float
    end: float
    key: bool = False


class KineticChunk(StrictModel):
    """One phrase of the in-stage captions (kinetic.py), scene-relative seconds."""

    start: float
    end: float
    words: list[KineticWord]
    lines: list[str]
    blank: bool = False


class MusicCue(StrictModel):
    asset_key: str
    start: float  # absolute
    end: float
    offset: float = 0.0
    gain_lu: float = 0.0
    fade_in: float = Field(ge=0.0)
    fade_out: float = Field(ge=0.0)
    xfade: Literal["qsin", "tri"] = "qsin"
    loops: list[tuple[float, float]] = Field(default_factory=list)  # fitted loop segments (§5.10)


class MusicPlan(StrictModel):
    cues: list[MusicCue]
    duck: list[tuple[float, float]]  # (absolute t, gain dB)
    mode: Literal["bed", "gaps_bookends", "off"]
    ref_lufs: float = -16.0
    level: Literal["normal", "low"] = "normal"


class SfxCue(StrictModel):
    t: float  # absolute
    sound: str
    gain_db: float


class TimedOverlay(StrictModel):
    """An overlay (screenplay ``Beat.overlays``) resolved to scene-relative seconds, clamped to its scene."""

    id: str
    start: float
    end: float
    media: MediaRef
    region: Literal["full", "panel"] = "full"
    sound: Literal["mute", "quiet"] = "mute"


class TimedScene(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {
        "look": "presentation", "events": "presentation", "kinetic_captions": "presentation", "overlays": "overlays",
    }
    V3_VALUES: ClassVar[dict[str, dict[str, str]]] = {"type": {"clip": "clip"}}

    scene_id: str
    index: int
    type: str
    concept_id: str | None = None
    chapter_id: str | None = None
    title: str = ""
    subtitle: str | None = None
    chapter_label: str | None = None
    start: float  # absolute
    duration: float
    audio_offset: float = 0.0  # scene-relative start of narration
    layout: Layout = Field(default_factory=Layout)
    audio_asset_key: str | None = None
    audio_url: str | None = None  # filled at serve time
    audio_duration: float = 0.0
    board: list[BoardItem] = Field(default_factory=list)
    figures: dict[str, MediaRef] = Field(default_factory=dict)  # board item id -> figure media
    beats: list[TimedBeat] = Field(default_factory=list)
    side_panel: ResolvedSidePanel | None = None
    media: MediaRef | None = None  # simulation / ai_video media
    quiz: QuizTiming | None = None
    p5_code: str | None = None  # interactive scenes (sandboxed iframe only)
    poster: MediaRef | None = None  # static frame for interactive scenes in MP4 renders
    objective_ids: list[str] = Field(default_factory=list)
    # Word-anchored moments (aadhi.compose.sync), sorted by start. Left out of the serialised timeline
    # when empty, so a scene without anchors stores (and renders) exactly as before.
    sync_cues: list[SyncCue] = Field(default_factory=list, exclude_if=lambda v: not v)
    # Narration loudness for the mascot's speech motion (SceneAudio.envelope, aadhi.pipeline.envelope):
    # base64, one byte per 1/audio_envelope_fps s from audio_offset. Left out of the JSON when absent.
    audio_envelope: str | None = Field(default=None, exclude_if=lambda v: v is None)
    audio_envelope_fps: int = Field(default=30, ge=1, le=100, exclude_if=lambda v: v == 30)
    # Seconds at the end of ``duration`` added by the teacher's minimum duration (SceneBase.min_seconds): nothing new
    # appears then (beats, captions, sync cues, terminal lines and the quiz teaser keep the timing they have without
    # it; Aadhi idles). Left out of the JSON when 0.
    hold_seconds: float = Field(default=0.0, ge=0.0, exclude_if=lambda v: v == 0)
    # v3 (Dynamic lectures): each is left out of the JSON at its default.
    look: ResolvedLook | None = Field(default=None, exclude_if=lambda v: v is None)
    events: list[VisualEvent] = Field(default_factory=list, exclude_if=lambda v: not v)  # sorted by t
    kinetic_captions: list[KineticChunk] = Field(default_factory=list, exclude_if=lambda v: not v)
    overlays: list[TimedOverlay] = Field(default_factory=list, exclude_if=lambda v: not v)

    def check_inside(self) -> None:
        """Events, kinetic chunks and overlays lie inside the scene (``ValueError`` otherwise)."""
        d = self.duration
        for e in self.events:
            if e.t < -_EPS or e.t > d + _EPS or (e.dur is not None and e.t + e.dur > d + _EPS):
                raise ValueError(f"scene {self.scene_id}: event {e.kind} at {e.t:.3f} outside the scene")
        for c in self.kinetic_captions:
            if c.start < -_EPS or c.end > d + _EPS or c.end < c.start - _EPS:
                raise ValueError(f"scene {self.scene_id}: kinetic caption at {c.start:.3f} outside the scene")
        for o in self.overlays:
            if o.start < -_EPS or o.end > d + _EPS or o.end < o.start - _EPS:
                raise ValueError(f"scene {self.scene_id}: overlay {o.id} outside the scene")


class TitleCard(StrictModel):
    line1: str
    line2: str = ""
    start: float  # absolute
    duration: float


class IntroSpec(StrictModel):
    logo_video_url: str | None = None  # /branding/... (stable)
    logo_duration: float = 0.0
    background_url: str | None = None
    cards: list[TitleCard] = Field(default_factory=list)
    duration: float = 0.0


class Chapter(StrictModel):
    start: float  # absolute
    title: str


class Branding(StrictModel):
    # Stable app paths (/branding/<file>) — safe to store.
    mascot_clips: dict[str, str] = Field(default_factory=dict)  # mascot_position -> clip URL
    static_background_url: str | None = None
    bgm_url: str | None = None
    bgm_volume: float = 0.06
    tick_url: str | None = None
    ding_url: str | None = None
    mascot_rig_url: str | None = None  # optional artist-made rig (future)


class Timeline(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"presentation": "presentation", "music": "presentation", "sfx": "presentation"}

    # 2 for classic timelines, 3 when a v3 field is used; a newer timeline is refused (aadhi.schemas.features).
    schema_version: int = TIMELINE_SCHEMA_VERSION
    version_id: int | None = None
    screenplay_revision: int | None = None  # ProjectVersion.revision the timeline was built from
    language: str = "en-IN"
    board_language: str = "en-IN"
    fps: int = 30
    width: int = 1920
    height: int = 1080
    meta: dict[str, Any] = Field(default_factory=dict)  # subject_name, unit_name, session_number, session_title
    concept_map: list[ConceptNode] = Field(default_factory=list)
    learning_objectives: list[LearningObjective] = Field(default_factory=list)
    intro: IntroSpec | None = None
    scenes: list[TimedScene] = Field(default_factory=list)
    total_duration: float = 0.0  # includes intro
    chapters: list[Chapter] = Field(default_factory=list)
    captions: list[CaptionCue] = Field(default_factory=list)  # absolute
    branding: Branding = Field(default_factory=Branding)
    transition_seconds: float = 0.4  # fade-in at the start of each scene (inside its duration)
    estimated: bool = False  # True for editor previews with estimated (not synthesised) timings
    # v3 (Dynamic lectures): each is left out of the JSON at its default.
    presentation: TimelinePresentation | None = Field(default=None, exclude_if=lambda v: v is None)
    music: MusicPlan | None = Field(default=None, exclude_if=lambda v: v is None)
    sfx: list[SfxCue] = Field(default_factory=list, exclude_if=lambda v: not v)

    @model_validator(mode="before")
    @classmethod
    def _reader_version(cls, data: Any) -> Any:
        check_reader_version(data, "timeline")
        return data

    @model_serializer(mode="wrap")
    def _write_version(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and "schema_version" in data:
            data["schema_version"] = marked_version(self, self.schema_version)
        return data

    @model_validator(mode="after")
    def _time_base(self) -> "Timeline":
        t = self.intro.duration if self.intro else 0.0
        for i, s in enumerate(self.scenes):
            if s.index != i:
                raise ValueError(f"scene {s.scene_id}: index {s.index} != position {i}")
            if abs(s.start - t) > _EPS:
                raise ValueError(f"scene {s.scene_id}: start {s.start:.3f} != expected {t:.3f}")
            if s.duration <= 0:
                raise ValueError(f"scene {s.scene_id}: duration must be positive")
            for b in s.beats:
                if b.start < -_EPS or b.end > s.duration + _EPS or b.speech_end < b.start - _EPS:
                    raise ValueError(f"scene {s.scene_id}: beat {b.beat_id} timing outside the scene")
            s.check_inside()
            t = s.start + s.duration
        if abs(self.total_duration - t) > _EPS:
            raise ValueError(f"total_duration {self.total_duration:.3f} != {t:.3f}")
        if self.presentation is not None:  # Dynamic: the frame follows the format, and there is no classic intro
            portrait = self.presentation.format == "portrait"
            if portrait != (self.height > self.width):
                raise ValueError(f"a {self.presentation.format} timeline cannot be {self.width}x{self.height}")
            if self.intro is not None:
                raise ValueError("a Dynamic timeline has no classic intro")
        version = marked_version(self, self.schema_version)
        if version != self.schema_version:
            self.schema_version = version
        return self


def timeline_json_schema() -> dict[str, Any]:
    return Timeline.model_json_schema()
