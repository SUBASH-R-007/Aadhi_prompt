"""Asset manifest: what the asset stage produced for one ProjectVersion.

Written by ``aadhi.pipeline.assets.build_assets`` (which returns ONLY a manifest — it never
modifies the screenplay) into ``ProjectVersion.asset_manifest``; consumed by
``aadhi.compose.timeline.build_timeline``. Times are seconds. Asset references are
content-addressed keys from the ``assets`` table; ``storage_key`` is carried alongside so URL
building is pure (no DB lookup).

Dynamic video (v3, design §6.7): ``BeatAudio.persona`` / ``words_exact``, ``SceneAudio.lufs`` /
``speaker_gains``, ``MediaInfo.has_audio`` and ``AssetManifest.music`` are inert from B0 and left out of the
JSON at their defaults; a manifest that uses one is written with ``schema_version = 3`` and a newer manifest is
refused (``aadhi.schemas.features``).
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import Field, SerializerFunctionWrapHandler, field_validator, model_serializer, model_validator

from .features import check_reader_version, marked_version
from .jsonsafe import check_storable
from .screenplay import StrictModel
from .timeline import TimedWord

# Narration clips are trimmed of provider lead/trail silence, then joined with this gap
# (plus each beat's pause_after). Keeps pacing even across TTS providers.
INTER_BEAT_GAP_SECONDS = 0.15


class BeatAudio(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"persona": "presentation", "words_exact": "cuts"}

    beat_id: str
    phase: Literal["main", "reveal"] = "main"
    offset: float  # start of this beat's (trimmed) speech inside the scene audio file
    speech_duration: float  # trimmed spoken duration
    pause_after: float = 0.0  # silence after the clip (beyond INTER_BEAT_GAP_SECONDS)
    lead_silence: float = 0.0  # measured & removed
    trail_silence: float = 0.0  # measured & removed
    spoken_text: str = ""  # text actually sent to TTS (after lexicon/normalisation)
    words: list[TimedWord] = Field(default_factory=list)  # scene-audio-relative, mapped to written tokens
    words_estimated: bool = False  # True when the provider gave no word timings
    clip_asset_key: str | None = None  # per-beat clip (cache unit for edits)
    # v3: the voice persona that spoke this beat (casting, §5.3), and whether ``words`` are exact: the provider's word
    # count equals the caption tokens and no lexicon ``spoken`` override applies (cuts may splice only then, §8.3).
    persona: str | None = Field(default=None, max_length=80, exclude_if=lambda v: v is None)
    words_exact: bool = Field(default=False, exclude_if=lambda v: v is False)


class SceneAudio(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"lufs": "presentation", "speaker_gains": "presentation"}

    scene_id: str
    asset_key: str | None = None  # concatenated narration (beats + gaps [+ quiz countdown silence])
    storage_key: str | None = None
    mime: str = "audio/mpeg"
    duration: float = 0.0
    beats: list[BeatAudio] = Field(default_factory=list)
    provider: str = ""
    voice: str = ""
    language: str = "en-IN"
    # Quiz scenes: silence between question beats and reveal beats (scene-audio-relative).
    countdown_start: float | None = None
    countdown_seconds: int | None = None
    # Loudness of the narration file for the mascot's speech motion (aadhi.pipeline.envelope): base64,
    # one byte (0 silence .. 255 loud) per 1/envelope_fps s from the file's start. None in manifests
    # built before it existed (the player then falls back to its WebAudio meter / synthetic motion).
    envelope: str | None = Field(default=None, exclude_if=lambda v: v is None)
    envelope_fps: int = Field(default=30, ge=1, le=100, exclude_if=lambda v: v == 30)
    # v3: integrated loudness of the narration file, and the per-speaker gains baked into it (dB, §5.4).
    lufs: float | None = Field(default=None, exclude_if=lambda v: v is None)
    speaker_gains: dict[str, float] = Field(default_factory=dict, exclude_if=lambda v: not v)


class MediaInfo(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"has_audio": "clip"}

    asset_key: str | None = None
    storage_key: str | None = None
    kind: Literal["video", "image", "gif"]
    mime: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    external_url: str | None = None  # GIPHY hotlink (never stored, live player only)
    attribution: str | None = None
    source: Literal["manim", "veo", "image", "figure", "upload", "gif", "poster", "fallback"] = "image"
    has_audio: bool = Field(default=False, exclude_if=lambda v: v is False)  # v3: the video carries its own sound


class SceneMedia(StrictModel):
    scene_id: str
    main: MediaInfo | None = None  # simulation / ai_video media (or ai_video fallback still)
    main_is_fallback: bool = False  # ai_video shown as a Ken-Burns still
    side_panel: MediaInfo | None = None  # figure/image/gif/manim panel
    poster: MediaInfo | None = None  # interactive poster frame
    figures: dict[str, MediaInfo] = Field(default_factory=dict)  # board item id -> figure media
    warnings: list[str] = Field(default_factory=list)


class MusicAsset(StrictModel):
    """A music bed the asset stage produced or adopted (v3, §5.10): what it is, how to fit and credit it."""

    asset_key: str
    provider: str  # "procedural" | "lyria" | "elevenlabs" | "upload" | "library"
    model: str | None = None
    prompt_hash: str | None = None
    seed: int | None = None
    duration: float = Field(ge=0.0)
    lufs: float | None = None
    bpm: float | None = Field(default=None, gt=0)
    key: str | None = Field(default=None, max_length=16)
    loop_points: list[tuple[float, float]] = Field(default_factory=list)  # loopable (start, end) segments
    sections: list[dict[str, Any]] = Field(default_factory=list)  # e.g. {start, end, label, layers}
    licence: str | None = Field(default=None, max_length=400)

    @field_validator("sections")
    @classmethod
    def _storable_sections(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        check_storable(v)
        return v


class AssetManifest(StrictModel):
    V3_FIELDS: ClassVar[dict[str, str]] = {"music": "presentation"}

    # 2 for classic manifests, 3 when a v3 field is used; a newer manifest is refused (aadhi.schemas.features).
    schema_version: int = 2
    language: str = "en-IN"
    audio: dict[str, SceneAudio] = Field(default_factory=dict)  # scene_id -> narration
    media: dict[str, SceneMedia] = Field(default_factory=dict)  # scene_id -> media
    # Per-scene content hash of the screenplay scene the assets were built from; a mismatch
    # with the current scene marks it stale (computed by aadhi.pipeline.assets.scene_hash).
    scene_hashes: dict[str, str] = Field(default_factory=dict)
    stale_scenes: list[str] = Field(default_factory=list)
    music: list[MusicAsset] = Field(default_factory=list, exclude_if=lambda v: not v)  # v3: music beds

    @model_validator(mode="before")
    @classmethod
    def _reader_version(cls, data: Any) -> Any:
        check_reader_version(data, "asset manifest")
        return data

    @model_validator(mode="after")
    def _mark_version(self) -> "AssetManifest":
        version = marked_version(self, self.schema_version)
        if version != self.schema_version:
            self.schema_version = version
        return self

    @model_serializer(mode="wrap")
    def _write_version(self, handler: SerializerFunctionWrapHandler) -> Any:
        data = handler(self)
        if isinstance(data, dict) and "schema_version" in data:
            data["schema_version"] = marked_version(self, self.schema_version)
        return data
