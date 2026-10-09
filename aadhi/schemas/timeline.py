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
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from .screenplay import (
    BoardItem,
    ConceptNode,
    LearningObjective,
    MascotPosition,
    SidePanel,
    StrictModel,
)

TIMELINE_SCHEMA_VERSION = 2
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
    kind: Literal["video", "image", "gif"]
    asset_key: str | None = None  # stored asset (None only for external hotlinks such as GIPHY)
    url: str | None = None  # filled at serve time
    mime: str | None = None
    duration: float | None = None  # seconds, for video
    width: int | None = None
    height: int | None = None
    fit: Literal["contain", "cover"] = "contain"
    end_behavior: Literal["loop", "freeze"] = "loop"  # manim -> freeze on last frame
    ken_burns: KenBurns | None = None  # stills used as ai_video fallbacks
    attribution: str | None = None  # e.g. "Powered by GIPHY"
    link_url: str | None = None
    render_in_mp4: bool = True  # False for hotlinked GIFs (live player only)


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


class TimedScene(StrictModel):
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
            t = s.start + s.duration
        if abs(self.total_duration - t) > _EPS:
            raise ValueError(f"total_duration {self.total_duration:.3f} != {t:.3f}")
        return self


def timeline_json_schema() -> dict[str, Any]:
    return Timeline.model_json_schema()
