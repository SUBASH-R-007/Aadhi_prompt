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

from collections.abc import Collection, Mapping
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
    # ``"<provider>:<voice>"`` of the built lecture's own voice when the free Edge voice narrated this scene in its
    # place (owner decision O17: the kept voice had no API key for that build, or its model was not served): read by
    # ``built_voice``, so a stand-in never becomes the voice the lecture keeps. None (left out of the JSON: classic
    # manifests are unchanged) for a scene narrated in the lecture's own voice. Not a v3 field: it changes no
    # rendering, and a reader that predates it ignores it (``StrictModel`` ignores unknown keys).
    stand_in_for: str | None = Field(default=None, max_length=200, exclude_if=lambda v: v is None)


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


# The only voice a build ever narrates with in place of the lecture's own: the free Edge voice, used when the
# lecture's voice provider has no API key or its voice model is no longer served (aadhi.pipeline.assets).
STAND_IN_TTS_PROVIDER = "edge"


def stand_in_mark(provider: str, voice: str) -> str:
    """``SceneAudio.stand_in_for`` of a scene the Edge stand-in narrated in place of ``provider``'s ``voice``."""
    return f"{provider}:{voice}"


def _stood_in_for(mark: object) -> tuple[str, str] | None:
    """``(provider, voice)`` a ``SceneAudio.stand_in_for`` names, else None (absent or unreadable)."""
    if not isinstance(mark, str):
        return None
    provider, sep, voice = mark.partition(":")
    return (provider, voice) if sep and provider and voice else None


def built_voice(manifest: AssetManifest | Mapping[str, Any] | None, language: str | None = None,
                skip: Collection[str] = ()) -> tuple[str, str] | None:
    """``(provider, voice)`` the built lecture was narrated with, read from its stored manifest (owner decision O17,
    2026-10-17: a built lecture keeps the voice it was made with on every later rebuild or edit; only the teacher's
    explicit re-voice moves it to another one). None when nothing is narrated yet (a new lecture).

    The scene narrations (``SceneAudio.provider`` / ``voice``, those with a narration file) are the source of truth:
    they say what was really built, whatever the options asked for. A scene the Edge voice narrated in place of a
    built lecture's own voice (its provider had no key for that build, or its voice model was not served) carries
    that voice in ``SceneAudio.stand_in_for`` and counts as it: a stand-in never becomes the kept voice, even when it
    ended up narrating every scene (a key missing while a ``TTS_RATE`` change narrated everything again, a misspelt
    ``*_TTS_MODEL``). ``language``: only narrations in the lecture's language count. ``skip``: scene ids left out
    (hidden scenes, which a build never narrates again). When the narrations disagree, a voice of another provider
    wins over the free Edge voice, which a build only ever uses as a stand-in next to the lecture's own voice
    (``STAND_IN_TTS_PROVIDER``; this also reads manifests written before ``stand_in_for`` existed); then the voice of
    the most scenes, ties to the earliest scene. A lecture narrated only in Edge without a ``stand_in_for`` (built
    keyless, on the old Edge default, or a first build whose voice model was not served) was made with Edge and
    keeps it.

    Takes the manifest model or its stored JSON (the API reads the column without validating it)."""
    if manifest is None:
        return None
    audio = manifest.audio if isinstance(manifest, AssetManifest) else (
        manifest.get("audio") if isinstance(manifest, Mapping) else None)
    if not isinstance(audio, Mapping):
        return None
    counts: dict[tuple[str, str], int] = {}  # in scene order: the manifest's audio follows the screenplay
    for scene_id, entry in audio.items():
        if scene_id in skip:
            continue
        if isinstance(entry, SceneAudio):
            values: Mapping[str, Any] = {"asset_key": entry.asset_key, "provider": entry.provider,
                                         "voice": entry.voice, "language": entry.language,
                                         "stand_in_for": entry.stand_in_for}
        elif isinstance(entry, Mapping):
            values = {**entry, "language": entry.get("language", SceneAudio.model_fields["language"].default)}
        else:
            continue
        provider, voice = _stood_in_for(values.get("stand_in_for")) or (values.get("provider"), values.get("voice"))
        if not values.get("asset_key") or not isinstance(provider, str) or not isinstance(voice, str) \
                or not provider or not voice:
            continue  # no narration (a scene without beats, or one whose narration failed)
        if language is not None and values.get("language") != language:
            continue
        counts[(provider, voice)] = counts.get((provider, voice), 0) + 1
    own = {k: n for k, n in counts.items() if k[0] != STAND_IN_TTS_PROVIDER} or counts
    if not own:
        return None
    most = max(own.values())
    return next(k for k, n in own.items() if n == most)
