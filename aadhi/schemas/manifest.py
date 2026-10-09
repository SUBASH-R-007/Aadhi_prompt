"""Asset manifest: what the asset stage produced for one ProjectVersion.

Written by ``aadhi.pipeline.assets.build_assets`` (which returns ONLY a manifest — it never
modifies the screenplay) into ``ProjectVersion.asset_manifest``; consumed by
``aadhi.compose.timeline.build_timeline``. Times are seconds. Asset references are
content-addressed keys from the ``assets`` table; ``storage_key`` is carried alongside so URL
building is pure (no DB lookup).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .screenplay import StrictModel
from .timeline import TimedWord

# Narration clips are trimmed of provider lead/trail silence, then joined with this gap
# (plus each beat's pause_after). Keeps pacing even across TTS providers.
INTER_BEAT_GAP_SECONDS = 0.15


class BeatAudio(StrictModel):
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


class SceneAudio(StrictModel):
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


class MediaInfo(StrictModel):
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


class SceneMedia(StrictModel):
    scene_id: str
    main: MediaInfo | None = None  # simulation / ai_video media (or ai_video fallback still)
    main_is_fallback: bool = False  # ai_video shown as a Ken-Burns still
    side_panel: MediaInfo | None = None  # figure/image/gif/manim panel
    poster: MediaInfo | None = None  # interactive poster frame
    figures: dict[str, MediaInfo] = Field(default_factory=dict)  # board item id -> figure media
    warnings: list[str] = Field(default_factory=list)


class AssetManifest(StrictModel):
    schema_version: int = 2
    language: str = "en-IN"
    audio: dict[str, SceneAudio] = Field(default_factory=dict)  # scene_id -> narration
    media: dict[str, SceneMedia] = Field(default_factory=dict)  # scene_id -> media
    # Per-scene content hash of the screenplay scene the assets were built from; a mismatch
    # with the current scene marks it stale (computed by aadhi.pipeline.assets.scene_hash).
    scene_hashes: dict[str, str] = Field(default_factory=dict)
    stale_scenes: list[str] = Field(default_factory=list)
