"""Stage 6 — assets: build the ``AssetManifest`` (never mutates the screenplay).

Per scene: TTS per beat (lexicon + normalisation applied, content-addressed per beat), one
trimmed/joined narration file (``audio.py``), Manim renders timed to the measured beat starts
(simulation scenes and Manim side panels), generated images, source figures, AI video within the
budget (else a Ken-Burns still from ``fallback_image_prompt`` / ``fallback_figure_id``), GIF
hotlinks (live player only) and user overrides used as-is.

Incremental: a scene whose content hash (``scene_hash``) and per-beat TTS clip keys (spoken text,
provider, voice, rate, model, language) match the previous manifest — and whose assets still
exist — is reused untouched. Scenes named in ``scene_ids`` are always rebuilt (the content-addressed
TTS / Manim / image caches keep that cheap), so an explicit rebuild retries failed media. Failures of
a single scene become ``SceneMedia.warnings`` + Issues (never exceptions); a scene whose narration or
media failed transiently (provider/network errors) gets no ``scene_hashes`` entry, so it shows as
stale and the next build retries it. A hidden scene (``SceneBase.hidden``) is never built, not even when
named in ``scene_ids``: no provider is called for it; assets built before it was hidden are carried as they
were (dropped if gone) and it is never listed stale. Job-level conditions (cancellation, budget, provider rate
limits, a provider refusing the job owner's personal API key) propagate: a refused personal key
fails the job (``personal_key_rejected``) instead of quietly building a lecture without media.

Media paid with a personal API key is produced in its own in-flight de-duplication scope
(``AssetStore.get_or_create(inflight_scope=...)``), so one user's provider outcome (e.g. a rejected
key) is never handed to another user's concurrent job; the stored, content-addressed asset is shared.

Media provider chain: generated images and AI videos come from ``IMAGE_PROVIDER`` / ``VIDEO_PROVIDER``,
then the configured ``*_FALLBACK_PROVIDERS`` in order (an asset any of them made before is reused
first; with a lecture's explicit image provider choice, another provider's earlier image only after the
chosen provider failed). A failure of the provider call moves on to the next provider unless it is a
safety refusal or a refused personal key; a storage, claim or database failure around the call does not
(another provider, possibly paid, could not fix it). Providers that just failed are tried last for a
while (``aadhi.providers.health``; each failure's cooldown is also logged as a job event, which the
admin status panel reads when the workers run in their own processes). A backup's
result carries an info issue and provenance in ``Asset.meta`` (provider, model, requested provider,
fallback reasons, generation time); soft quality findings (blank picture, wrong shape) become
``assets.media_suspect`` warnings. A payment/account refusal (``ProviderUnavailable``, e.g. a
Pollinations HTTP 402) is permanent like a safety refusal: the scene keeps its hash.

Paid AI video jobs of resumable providers (Veo) are checkpointed in the job payload
(``aadhi.providers.operations``) the moment they are accepted: a restarted attempt of the job polls the
same provider job instead of paying again, and a submission whose answer was never saved is not
re-submitted automatically (``video.ambiguous_submission`` issue; an explicit scene rebuild generates it).
A provider that reports its sends (``reports_sending``: Veo) marks the record ``submitting`` only while a
request is outstanding, so a restart during a retry wait after an error answer (429, 5xx) submits normally.
Resuming such a job pays nothing new (its charge was counted by the attempt that submitted it), so it skips
the still-wanted guard (``get_or_create(guarded=False)``) and the up-front budget estimate.

Media identity: image / video prompts are normalised (``storage.assets.normalize_prompt``: NFC, whitespace
collapsed) before they are keyed and sent; for already-normalised prompts the keys are unchanged, and the
key an un-normalised prompt had before is still looked up, so nothing cached is paid for again. When
generation is off or not configured at the server level (``IMAGE_PROVIDER`` / ``VIDEO_PROVIDER=none``, no
key), media generated earlier for the same request by any known provider is still used (info issue
``assets.cached_media_used``); a lecture that opted out (``allow_generated_images`` / ``allow_ai_video``
false) never gets any.

New AI versions (Visual Review): ``SidePanel.variant`` / ``AIVideoScene.variant`` above 0 add ``"variant"`` to the
image / clip content key (and to a clip's generated still), so the same prompt makes a new asset while the
earlier one stays cached; at 0 every key is exactly as before. A provider whose ``generate`` accepts ``variant``
gets it as a seed hint (Pollinations derives its seed from the prompt). A new version that cannot be made (generation
is off on the server, or the provider failed) never hides the visual: the latest stored earlier version is shown
with a warning. A paid clip whose submission is
ambiguous is generated again only for the scenes the teacher confirmed (job payload ``confirm_paid_scene_ids``), and
only once: the consent covers a submission made before it (by another job); a confirmed submission of this very job
that becomes ambiguous in turn needs a new confirmation, and a job retry never carries the consent over.
Teacher choices come first: a missing upload / library pick is reported as ``assets.override_missing`` (the
generated visual is still shown). With ``GenerationOptions.prefer_library_visuals`` a side panel's generated
image is replaced by a picture from the lecture owner's library (``aadhi.library.auto_match`` on the image prompt:
score at least ``aadhi.library.AUTO_USE_THRESHOLD`` and enough of the prompt covered; never for a requested new
version; one plan per build, in scene order, so one picture serves one scene; media generated for this lecture or
the picture the scene showed is never used in its place). Generated images and clips of the scenes a build made are
recorded in the lecture owner's library when ``LIBRARY_AUTO_SAVE_GENERATED`` is on
(``aadhi.library.record_generated``; best effort, never fails the build). Libraries are personal: only a build the
lecture's owner started uses or fills one (an admin building someone else's lecture never does).
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..compose.base import SCENE_LEAD_SECONDS, SCENE_TAIL_SECONDS
from ..credentials import billed_to, personal_key_rejection
from ..jobs.base import BudgetExceeded, JobCancelled
from ..providers.base import (
    ContentBlocked,
    OperationLost,
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    Usage,
)
from ..providers.health import classify_failure, fallback_allowed, provider_health
from ..providers.operations import (
    ABANDONED,
    AMBIGUOUS,
    COMPLETED,
    FAILED,
    LOST,
    PENDING,
    SUBMITTED,
    SUBMITTING,
    OperationStore,
)
from ..schemas.manifest import AssetManifest, MediaInfo, SceneAudio, SceneMedia
from ..schemas.screenplay import (
    AIVideoScene,
    BoardItemKind,
    BoardScene,
    InteractiveScene,
    LexiconEntry,
    ManimSpec,
    QuizScene,
    Screenplay,
    SimulationScene,
)
from ..storage.assets import Produced, canonical_json, compute_key, media_key, normalize_prompt
from . import dbops, integrations
from .aio import gather_all
from .audio import AUDIO_VERSION, AudioError, ClipInput, build_scene_audio
from .base import GenerationOptions, Issue
from .envelope import ENVELOPE_FPS, narration_envelope
from .timing import beat_seconds

log = logging.getLogger(__name__)

PANEL_IMAGE_ASPECT = "4:3"
SCENE_IMAGE_ASPECT = "16:9"
DEFAULT_VIDEO_SECONDS = 8
SCENE_PARALLEL = 4
_PASSTHROUGH = (JobCancelled, BudgetExceeded, RateLimited, asyncio.CancelledError)
# Providers whose earlier results are looked up when generation is off at the server level (besides the
# configured chain); the offline stand-in (fake) only counts when it is configured.
_KNOWN_MEDIA_PROVIDERS = {"image": ("gemini", "pollinations"), "video": ("veo",)}
# Job payload: scenes whose possibly-billed (ambiguous) AI video the teacher confirmed generating again.
CONFIRM_PAID_KEY = "confirm_paid_scene_ids"
# Warnings of a missing teacher-chosen visual (override / poster key): reported as ``assets.override_missing``.
OVERRIDE_MISSING_WARNINGS = (
    "The uploaded media for this scene is missing.",
    "The uploaded side-panel media is missing; using the generated one.",
    "The uploaded poster for this scene is missing.",
)
# A new AI version asked for while generation is off on this server (``aadhi.review.SETTLED_WARNINGS``).
NEW_VERSION_UNAVAILABLE = "A new AI version cannot be made on this server; the earlier one is shown."


class MediaUnavailable(Exception):
    """A media provider is not configured (factory returned None)."""


_PROVIDER_CALL = "_aadhi_provider_call"


def _provider_call_failed(exc: BaseException) -> None:
    """Mark ``exc`` as raised by the media provider call itself. The chains move on to the next provider
    only then: a storage, claim or database failure around the call is not the provider's fault, and
    generating the media again elsewhere (possibly paid) cannot fix it."""
    setattr(exc, _PROVIDER_CALL, True)


class VideoNeedsAttention(Exception):
    """A paid AI video job may already have been billed (its submission was never confirmed, or the
    job cannot be found again after a restart), so it is not submitted again automatically. The scene
    shows its still image with an issue; an explicit rebuild of the scene generates the clip again."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Deterministic refusals: retrying on the next build cannot help, so the scene keeps its hash. A payment /
# account refusal of the server's keyless provider (e.g. HTTP 402) needs an admin, then an explicit rebuild.
_PERMANENT = (ContentBlocked, ProviderUnavailable, VideoNeedsAttention)
_FAILURE_WORDS = {
    "unavailable": "payment or account problem",
    "rate_limited": "rate limited",
    "transient": "temporarily unavailable",
    "invalid_output": "unusable output",
    "not_configured": "not configured",
    "lost": "job lost",
    "failed": "failed",
}


# ---------------------------------------------------------------------------
# Hashing / staleness
# ---------------------------------------------------------------------------


def relevant_lexicon(scene: Any, lexicon: list[LexiconEntry]) -> list[LexiconEntry]:
    """Lexicon entries whose written form occurs in the scene's spoken text."""
    if not lexicon:
        return []
    text = " ".join((b.spoken or b.narration) for b in scene.all_beats()).lower()
    return [e for e in lexicon if e.written.lower() in text]


def scene_hash(scene: Any, lexicon: list[LexiconEntry] | None = None, voice: dict[str, Any] | None = None) -> str:
    """Content hash of a scene for asset staleness.

    Canonical scene JSON (teacher notes and intent excluded) + the lexicon entries that affect its
    speech (only when ``lexicon`` is given and some apply) + voice settings (only when given).
    ``scene_hash(scene)`` equals the stored hash for scenes no lexicon entry applies to; pass the
    screenplay's lexicon (see ``stale_scene_ids``) to be exact. ``hidden`` and ``min_seconds`` are
    excluded too (they never change the narration or media; the timeline applies them), so hiding,
    showing or holding a scene keeps its assets. They are absent from the JSON at their defaults anyway,
    so every earlier hash is unchanged.
    """
    data = scene.model_dump(mode="json", exclude={"notes", "intent", "hidden", "min_seconds"})
    payload: dict[str, Any] = {"scene": data}
    rel = relevant_lexicon(scene, lexicon or [])
    if rel:
        payload["lexicon"] = [e.model_dump(mode="json") for e in rel]
    if voice:
        payload["voice"] = voice
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:32]


def stale_scene_ids(screenplay: Screenplay, manifest: AssetManifest | None) -> list[str]:
    """Scenes whose assets are missing or were built from different content.

    Hidden scenes are never stale: the build skips them (``_Builder.run``) and the timeline leaves them out.
    Showing one again makes it stale when its assets are missing or out of date.
    """
    if manifest is None:
        return [s.id for s in screenplay.scenes if not s.hidden]
    return [s.id for s in screenplay.scenes
            if not s.hidden and manifest.scene_hashes.get(s.id) != scene_hash(s, screenplay.lexicon)]


def carry_visual_variant(old: Any, new: Any) -> Any:
    """``new`` (a rewrite of scene ``old``, e.g. ``regenerate_scene``) keeping ``old``'s new-AI-version number
    where the visual request is unchanged (same normalised image / video prompt), so a rewritten narration does
    not bring back the first picture. Returns ``new`` itself when nothing is carried."""
    if isinstance(old, AIVideoScene) and isinstance(new, AIVideoScene):
        if old.variant > 0 and new.variant == 0 and \
                normalize_prompt(old.video_prompt) == normalize_prompt(new.video_prompt):
            return new.model_copy(update={"variant": old.variant})
        return new
    po, pn = getattr(old, "side_panel", None), getattr(new, "side_panel", None)
    if po is not None and pn is not None and po.kind == pn.kind == "image" and po.variant > 0 and pn.variant == 0 \
            and normalize_prompt(po.image_prompt) == normalize_prompt(pn.image_prompt):
        return new.model_copy(update={"side_panel": pn.model_copy(update={"variant": po.variant})})
    return new


_VARIANT_KWARG: dict[type, bool] = {}


def _accepts_variant(provider: Any) -> bool:
    """True when the provider's ``generate`` takes a ``variant`` seed hint (cached per provider class)."""
    cls = type(provider)
    if cls not in _VARIANT_KWARG:
        try:
            params = inspect.signature(provider.generate).parameters
        except (TypeError, ValueError):
            params = {}
        _VARIANT_KWARG[cls] = "variant" in params
    return _VARIANT_KWARG[cls]


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VoiceConfig:
    provider: str
    voice: str
    rate: str
    model: str
    language: str


def voice_config(settings: Any, options: GenerationOptions, language: str, tts: Any) -> VoiceConfig:
    provider = getattr(tts, "name", None) or options.tts_provider or settings.tts_provider
    voice = options.tts_voice or (settings.tts_voice if language == settings.default_language else "") or ""
    voice = voice or integrations.default_voice(provider, language, tts)
    model = {"elevenlabs": settings.elevenlabs_model, "openai": settings.openai_tts_model,
             "gemini": settings.gemini_tts_model}.get(provider, "")
    return VoiceConfig(provider=provider, voice=voice, rate=options.tts_rate or settings.tts_rate or "+0%",
                       model=model, language=language)


@dataclass
class AssetBuildResult:
    manifest: AssetManifest
    issues: list[Issue] = field(default_factory=list)
    built: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    asset_keys: list[str] = field(default_factory=list)


def _issue(code: str, scene_id: str, message: str, severity: str = "warning", source: str = "assets",
           data: dict[str, Any] | None = None) -> Issue:
    return Issue(code=code, severity=severity, message=message, scene_id=scene_id, source=source, fixable=False,
                 data=data)


def _keys_of(audio: SceneAudio | None, media: SceneMedia | None) -> list[str]:
    keys: list[str] = []
    if audio is not None:
        keys += [k for k in [audio.asset_key, *(b.clip_asset_key for b in audio.beats)] if k]
    if media is not None:
        infos = [media.main, media.side_panel, media.poster, *media.figures.values()]
        keys += [m.asset_key for m in infos if m is not None and m.asset_key]
    return keys


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, ctx: Any, sp: Screenplay, options: GenerationOptions, previous: AssetManifest | None,
                 scene_ids: list[str] | None, progress: tuple[float, float] | None) -> None:
        self.ctx = ctx
        self.settings = ctx.settings
        self.sp = sp
        self.options = options
        self.previous = previous
        self.scene_ids = set(scene_ids) if scene_ids is not None else None
        self.progress = progress
        self.language = sp.language
        self.board_language = sp.board_language or sp.language
        self.issues: list[Issue] = []
        self.tts = self._tts_provider()
        self.voice = voice_config(self.settings, self.options, self.language, self.tts)
        self.tts_sem = asyncio.Semaphore(max(1, int(self.settings.tts_max_parallel)))
        self.video_scene_ids = self._video_allowance()
        self.retry_scene_ids: set[str] = set()  # scenes with transient media failures (no hash stored)
        self.operations = OperationStore(ctx)  # checkpoints of paid video jobs (resume after a restart)
        self._choice_reported: set[str] = set()
        payload = getattr(ctx, "payload", None)
        confirmed = payload.get(CONFIRM_PAID_KEY) if isinstance(payload, dict) else None
        # Scenes whose possibly-billed clip the teacher confirmed generating again (Visual Review "retry"): the
        # consent covers a submission another job made before it, never one this job made itself.
        self.confirmed_paid = {str(s) for s in confirmed} if isinstance(confirmed, list) else set()
        self._job_id = int(getattr(ctx, "job_id", 0) or 0)
        self._building: set[str] = set()  # scenes this run builds (set by run())
        # prefer_library_visuals: one plan per build (scene id -> (asset key, title, score)); see library_image
        self._library_plan: dict[str, tuple[str, str, float]] | None = None
        self._library_lock = asyncio.Lock()
        self._owner_started: bool | None = None  # the job's user owns the lecture (libraries are personal)

    def _tts_provider(self) -> Any:
        """The requested TTS provider, else the server default (reported) when it is not configured."""
        requested = self.options.tts_provider
        try:
            return integrations.get_tts(requested, self.settings)
        except ProviderNotConfigured:
            if not requested or requested == self.settings.tts_provider:
                raise
        self.issues.append(Issue(
            code="assets.tts_fallback", severity="warning", source="assets", fixable=False,
            message=f"The voice provider '{requested}' is not configured on this server; "
                    f"'{self.settings.tts_provider}' was used instead."))
        self.options = self.options.model_copy(update={"tts_provider": None, "tts_voice": None})
        return integrations.get_tts(None, self.settings)

    # --- personal API keys ------------------------------------------------------------
    def _key_sources(self) -> dict[str, str | None]:
        # Only DBJobContext resolves keys; test and eval contexts have no ``key_sources``.
        return getattr(self.ctx, "key_sources", None) or {}

    def _key_rejected(self, exc: BaseException) -> bool:
        """True when ``exc`` is a provider refusing the job owner's personal key: that fails the job."""
        return personal_key_rejection(exc, self._key_sources()) is not None

    def _key_scope(self, provider: str) -> str:
        """In-flight de-dup scope: work paid with a personal API key is not shared with other users' jobs."""
        return f"user:{self.ctx.user_id}" if billed_to(self._key_sources(), provider) == "user" else ""

    def _health_scope(self, provider: str) -> str:
        """Cooldown scope: one user's failing personal key never demotes the server's provider for others."""
        return self._key_scope(provider) or "server"

    # --- media provider chain (IMAGE_PROVIDER / VIDEO_PROVIDER + *_FALLBACK_PROVIDERS) -------------
    def _media_providers(self, kind: str) -> tuple[list[Any], list[tuple[str, BaseException]]]:
        """Providers of the ``kind`` chain in preference order, plus the unusable ones with the reason.

        The preferred provider comes through the ``integrations`` seam (``None`` = the medium is off:
        no backups either); the backups are ``IMAGE_FALLBACK_PROVIDERS`` / ``VIDEO_FALLBACK_PROVIDERS``.
        A lecture's own image provider choice (``GenerationOptions.image_provider``, when that option
        exists) goes first, like the TTS provider choice (warning issue when it is not configured).
        """
        getter = integrations.get_image if kind == "image" else integrations.get_video
        server_default = self.settings.image_provider if kind == "image" else self.settings.video_provider
        providers: list[Any] = []
        skipped: list[tuple[str, BaseException]] = []
        try:
            primary = getter(self.settings)
        except ProviderNotConfigured as exc:
            skipped.append((server_default, exc))
        else:
            if primary is None:
                return [], []
            providers.append(primary)
        backups = self.settings.image_fallback_providers if kind == "image" else self.settings.video_fallback_providers
        requested = getattr(self.options, "image_provider", None) if kind == "image" else None
        names = [str(requested)] if requested and requested != server_default else []
        for name in [*names, *backups]:
            if name in {p.name for p in providers} or name in {n for n, _ in skipped}:
                continue
            try:
                backup = _backup_provider(kind, name, self.settings)
            except ProviderNotConfigured as exc:
                skipped.append((name, exc))
                continue
            if backup is None:
                continue
            if name in names:
                providers.insert(0, backup)
            else:
                providers.append(backup)
        if names and not (providers and providers[0].name == names[0]) and kind not in self._choice_reported:
            self._choice_reported.add(kind)
            self.issues.append(Issue(
                code=f"assets.{kind}_provider_fallback", severity="warning", source="assets", fixable=False,
                message=f"The {kind} provider '{names[0]}' is not configured on this server; "
                        "the server's providers were used instead."))
        return providers, skipped

    def _ordered(self, providers: list[Any], aspect: str) -> list[Any]:
        """Preferred provider first, then the backups that serve ``aspect`` natively; a provider in a
        cooldown (recent failure) goes after the others but is never dropped."""
        head, rest = providers[:1], providers[1:]
        rest = sorted(rest, key=lambda p: aspect not in (getattr(p, "aspects", None) or {aspect}))
        return provider_health().order(head + rest, lambda p: (p.name, self._health_scope(p.name)))

    def _chain_failed(self, kind: str, provider: Any, exc: BaseException, remaining: int,
                      scene_id: str | None) -> str:
        """Note a fallback-able failure (cooldown) and log the decision; returns its short description."""
        category = classify_failure(exc)
        scope = self._health_scope(provider.name)
        seconds = provider_health().note_failure(provider.name, scope, exc, self.settings)
        words = _FAILURE_WORDS.get(category, category)
        # The cooldown goes into the job event too: with external workers the admin status panel (another
        # process) learns it from there (``api.routers.meta.media_providers``).
        data = {"provider": provider.name, "category": category, "scene_id": scene_id, "kind": kind,
                "scope": "server" if scope == "server" else "user", "cooldown_seconds": int(seconds)}
        if remaining:
            self.ctx.log(f"{kind.capitalize()} provider {provider.name} failed ({words}); trying the next "
                         "configured provider.", level="warning", **data)
        elif seconds > 0:
            self.ctx.log(f"{kind.capitalize()} provider {provider.name} failed ({words}); it is tried after the "
                         "others for a while.", level="info", **data)
        return f"{provider.name}: {words}"

    def _chain_issue(self, kind: str, made_by: str, requested: str, failed: list[str], scene_id: str | None) -> None:
        """Info issue when a backup provider made the media (provenance the teacher can see)."""
        if made_by == requested:
            return
        why = f" ({'; '.join(failed)})" if failed else ""
        self.issues.append(Issue(code=f"assets.{kind}_fallback", severity="info", source="assets", fixable=False,
                                 scene_id=scene_id, message=f"This {kind} was made by the backup provider "
                                 f"'{made_by}' instead of '{requested}'{why}."))

    @staticmethod
    def _chain_error(errors: list[BaseException]) -> BaseException:
        """The failure reported when every provider failed: a retryable one if any (so the next build
        tries again), else the last."""
        return next((e for e in errors if not isinstance(e, _PERMANENT)), errors[-1])

    def _media_suspect(self, scene_id: str | None, meta: dict[str, Any] | None) -> None:
        """Soft quality findings of generated media (kept, but worth a look)."""
        for text in list((meta or {}).get("warnings") or [])[:3]:
            self.issues.append(Issue(code="assets.media_suspect", severity="warning", source="assets", fixable=False,
                                     scene_id=scene_id, message=f"Please check this generated visual: {text}."))

    def _provenance(self, provider: Any, model: str, aspect: str, requested: str, failed: list[str],
                    started: float) -> dict[str, Any]:
        """How generated media was made (stored in ``Asset.meta``; no secrets, no prompt copies)."""
        meta: dict[str, Any] = {"provider": provider.name, "model": model, "aspect": aspect,
                                "generation_ms": int((time.monotonic() - started) * 1000)}
        if requested != provider.name:
            meta["requested_provider"] = requested
            meta["fallback_from"] = failed[:5]
        if getattr(self.ctx, "job_id", None):
            meta["job_id"] = self.ctx.job_id
        return meta

    # --- planning -----------------------------------------------------------------
    def _video_allowance(self) -> set[str]:
        if self.settings.video_provider == "none":
            return set()
        return self._wanted_video_ids()

    def _wanted_video_ids(self) -> set[str]:
        """AI video scenes the lecture wants a clip for (its opt-in and the per-lecture caps)."""
        o, s = self.options, self.settings
        if not o.allow_ai_video:
            return set()
        n = min(o.max_ai_videos, s.max_ai_videos_per_lecture)
        ids = [sc.id for sc in self.sp.scenes if isinstance(sc, AIVideoScene) and not sc.override_asset_key]
        return set(ids[:max(0, n)])

    def speech(self, beat: Any) -> str:
        return integrations.speech_text(beat.spoken or beat.narration, self.sp.lexicon, self.language)

    def clip_key(self, text: str) -> str:
        """Content key of one beat's TTS clip (text + every voice setting that changes the audio)."""
        v = self.voice
        return compute_key("tts", {"text": text, "voice": v.voice, "provider": v.provider, "rate": v.rate,
                                   "model": v.model, "language": v.language})

    def _reusable(self, scene: Any, h: str) -> bool:
        """True when the previous manifest's assets for ``scene`` match its content and voice settings."""
        prev = self.previous
        if prev is None or prev.scene_hashes.get(scene.id) != h or scene.id not in prev.media:
            return False
        beats = scene.all_beats()
        audio = prev.audio.get(scene.id)
        if beats:
            if audio is None or audio.asset_key is None:
                return False
            if (audio.provider, audio.voice, audio.language) != (self.voice.provider, self.voice.voice, self.language):
                return False
            if [b.beat_id for b in audio.beats] != [b.id for b in beats]:
                return False
            texts = [self.speech(b) for b in beats]
            if [b.spoken_text for b in audio.beats] != texts:
                return False
            # the clip keys also cover rate and model (e.g. a TTS_RATE change): never mix speaking rates
            stored = [b.clip_asset_key for b in audio.beats]
            if all(stored) and stored != [self.clip_key(t) for t in texts]:
                return False
        return True

    # --- TTS / audio ----------------------------------------------------------------
    async def tts_clip(self, text: str) -> Any:
        v = self.voice
        key = self.clip_key(text)

        async def produce() -> Produced:
            async with self.tts_sem, integrations.limit("tts"):
                res = await self.tts.synthesize(text, voice=v.voice, language=v.language, rate=v.rate,
                                                on_usage=self.ctx.record_usage)
            words = [{"text": w.text, "start": w.start, "end": w.end} for w in res.words]
            return Produced(data=res.audio, mime=res.mime, duration_s=res.duration,
                            meta={"words": words, "voice": res.voice, "provider": res.provider,
                                  "sample_rate": res.sample_rate, "characters": len(text)})

        asset, _ = await self.ctx.assets.get_or_create(key, "tts", produce, created_by=self.ctx.user_id,
                                                       inflight_scope=self._key_scope(v.provider))
        return asset

    async def scene_audio(self, scene: Any) -> SceneAudio:
        beats = scene.all_beats()
        v = self.voice
        base = SceneAudio(scene_id=scene.id, provider=v.provider, voice=v.voice, language=self.language)
        if not beats:
            return base
        texts = [self.speech(b) for b in beats]
        clips = await gather_all(self.tts_clip(t) for t in texts)
        n_reveal = len(scene.reveal_beats) if isinstance(scene, QuizScene) else 0
        phases = ["main"] * (len(beats) - n_reveal) + ["reveal"] * n_reveal
        countdown = scene.countdown_seconds if isinstance(scene, QuizScene) else None
        key = compute_key("scene_audio", {
            "clips": [c.key for c in clips], "pauses": [b.pause_after for b in beats], "phases": phases,
            "narration": [b.narration for b in beats], "countdown": countdown, "audio": AUDIO_VERSION,
        })

        measured: list[str | None] = []  # set when this call produced the file (its envelope was just measured)

        async def produce() -> Produced:
            datas = await gather_all(asyncio.to_thread(self.ctx.storage.get_bytes, c.storage_key) for c in clips)
            inputs = [
                ClipInput(beat_id=b.id, data=d, mime=c.mime, narration=b.narration, spoken_text=t, phase=ph,
                          pause_after=b.pause_after, words=list((c.meta or {}).get("words") or []), clip_asset_key=c.key)
                for b, c, d, t, ph in zip(beats, clips, datas, texts, phases, strict=True)
            ]
            res = await build_scene_audio(inputs, countdown_seconds=countdown, ffmpeg=self.settings.ffmpeg_path)
            envelope = await self.loudness_envelope(res.mp3, "audio/mpeg", scene.id)
            measured.append(envelope)
            return Produced(data=res.mp3, mime="audio/mpeg", duration_s=res.duration, meta={
                "beats": [b.model_dump(mode="json") for b in res.beats], "countdown_start": res.countdown_start,
                **({"envelope": envelope, "envelope_fps": ENVELOPE_FPS} if envelope else {}),
            })

        asset, _ = await self.ctx.assets.get_or_create(key, "scene_audio", produce, created_by=self.ctx.user_id)
        meta = asset.meta or {}
        # The key is content-only, so the cached file may come from another scene (or lecture) with the
        # same narration: timing is identical by construction, but the beat ids are this scene's.
        cached = list(meta.get("beats") or [])
        if len(cached) != len(beats):
            raise AudioError(f"cached narration {asset.key} has {len(cached)} beats, the scene has {len(beats)}")
        envelope, envelope_fps = meta.get("envelope"), meta.get("envelope_fps") or ENVELOPE_FPS
        if not envelope and not measured:  # narration cached before envelopes existed: measure the stored file
            envelope, envelope_fps = await self.stored_envelope(asset.storage_key, asset.mime, scene.id), ENVELOPE_FPS
        return SceneAudio.model_validate({
            **base.model_dump(mode="json"),
            "asset_key": asset.key, "storage_key": asset.storage_key, "mime": asset.mime,
            "duration": float(asset.duration_s or 0.0),
            "beats": [{**dict(m), "beat_id": b.id} for m, b in zip(cached, beats, strict=True)],
            "countdown_start": meta.get("countdown_start"), "countdown_seconds": countdown,
            "envelope": envelope, "envelope_fps": envelope_fps,
        })

    async def loudness_envelope(self, data: bytes, mime: str, scene_id: str) -> str | None:
        """Loudness envelope of a narration file (aadhi.pipeline.envelope); None when it cannot be measured
        (the player then moves the mascot without it, as before)."""
        try:
            return await narration_envelope(data, mime, ffmpeg=self.settings.ffmpeg_path)
        except Exception as exc:  # noqa: BLE001 - decoration only: never fails the narration
            log.warning("loudness envelope failed for scene %s: %s", scene_id, type(exc).__name__)
            return None

    async def _add_missing_envelopes(self, audio: dict[str, SceneAudio], scene_ids: list[str]) -> None:
        """Reused narration from a manifest built before envelopes existed gets one (measured once from
        the stored file; the next build reuses it from the manifest)."""
        todo = [sid for sid in scene_ids if sid in audio and audio[sid].storage_key and not audio[sid].envelope]
        if not todo:
            return
        sem = asyncio.Semaphore(SCENE_PARALLEL)

        async def one(sid: str) -> None:
            a = audio[sid]
            async with sem:
                envelope = await self.stored_envelope(a.storage_key, a.mime, sid)
            if envelope:
                audio[sid] = a.model_copy(update={"envelope": envelope, "envelope_fps": ENVELOPE_FPS})

        await gather_all(one(sid) for sid in todo)

    async def stored_envelope(self, storage_key: str | None, mime: str, scene_id: str) -> str | None:
        """Envelope of a stored narration file (scene audio built before envelopes existed)."""
        if not storage_key:
            return None
        try:
            data = await asyncio.to_thread(self.ctx.storage.get_bytes, storage_key)
        except Exception as exc:  # noqa: BLE001 - decoration only
            log.warning("narration %s unreadable for its loudness envelope: %s", scene_id, type(exc).__name__)
            return None
        return await self.loudness_envelope(data, mime, scene_id)

    # --- media helpers ----------------------------------------------------------------
    async def asset_info(self, key: str, source: str) -> MediaInfo | None:
        asset = await asyncio.to_thread(self.ctx.assets.get, key)
        if asset is None:
            return None
        mime = asset.mime
        kind = "video" if mime.startswith("video/") else "image"
        return MediaInfo(asset_key=asset.key, storage_key=asset.storage_key, kind=kind, mime=mime,
                         duration=asset.duration_s, width=asset.width, height=asset.height, source=source)  # type: ignore[arg-type]

    async def figure(self, figure_id: str | None) -> MediaInfo | None:
        fig = next((f for f in self.sp.figures if f.id == figure_id), None)
        if fig is None or not fig.asset_key:
            return None
        return await self.asset_info(fig.asset_key, "figure")

    def _full_image_prompt(self, prompt: str) -> str:
        return f"{prompt.strip().rstrip('.')}. {self.settings.image_style_suffix}".strip()

    def _image_keys(self, full: str, name: str, aspect: str, variant: int = 0) -> list[str]:
        """Content keys of an image request: the canonical key (normalised prompt), then the key the
        un-normalised prompt had before prompts were normalised (identical for normalised prompts).

        A new version (``variant`` above 0) adds ``"variant"`` to the inputs and has only the canonical key
        (variants came after prompt normalisation); at 0 the keys are exactly the earlier ones."""
        model = self.settings.image_model if name == "gemini" else ""
        if variant > 0:
            return [compute_key("image", {"prompt": normalize_prompt(full), "provider": name, "model": model,
                                          "aspect": aspect, "variant": int(variant)})]
        legacy = compute_key("image", {"prompt": full, "provider": name, "model": model, "aspect": aspect})
        return list(dict.fromkeys([media_key("image", full, provider=name, model=model, aspect=aspect), legacy]))

    def _image_key(self, full: str, provider: Any, aspect: str, variant: int = 0) -> str:
        return self._image_keys(full, provider.name, aspect, variant)[0]

    def _offline_names(self, kind: str) -> list[str]:
        """Providers whose earlier results count when generation is off: the configured chain, then the
        other known providers."""
        s = self.settings
        primary = s.image_provider if kind == "image" else s.video_provider
        backups = s.image_fallback_providers if kind == "image" else s.video_fallback_providers
        names = [n for n in (primary, *backups) if n and n != "none"]
        return list(dict.fromkeys([*names, *_KNOWN_MEDIA_PROVIDERS[kind]]))

    def _cached_media_issue(self, kind: str, scene_id: str | None) -> None:
        label = "AI video" if kind == "video" else "Image"
        self.issues.append(Issue(code="assets.cached_media_used", severity="info", source="assets", fixable=False,
                                 scene_id=scene_id, message=f"{label} generation is not available on this server; "
                                 f"the {kind} generated earlier for this request was used."))

    async def earlier_image(self, prompt: str, aspect: str, scene_id: str | None = None,
                            variant: int = 0) -> MediaInfo | None:
        """An image generated earlier for this request by any known provider (generation is off or not
        configured at the server level; callers check the lecture's own opt-in first)."""
        full = self._full_image_prompt(prompt)
        candidates = [k for name in self._offline_names("image")
                      for k in self._image_keys(full, name, aspect, variant)]
        cached = await asyncio.to_thread(self.ctx.assets.first_existing, candidates)
        if cached is None:
            return None
        self._cached_media_issue("image", scene_id)
        self._media_suspect(scene_id, cached.meta)
        return self._image_info(cached)

    async def earlier_image_version(self, prompt: str, aspect: str, scene_id: str | None, variant: int) -> MediaInfo | None:
        """The latest earlier version (``variant`` - 1 down to the first) of an image request that is stored, by
        any known provider: what a scene keeps showing when its new AI version cannot be made."""
        full = self._full_image_prompt(prompt)
        candidates = [k for v in range(int(variant) - 1, -1, -1) for name in self._offline_names("image")
                      for k in self._image_keys(full, name, aspect, v)]
        cached = await asyncio.to_thread(self.ctx.assets.first_existing, candidates)
        if cached is None:
            return None
        self._media_suspect(scene_id, cached.meta)
        return self._image_info(cached)

    async def new_image_version(self, prompt: str, aspect: str, scene_id: str, variant: int,
                                warnings: list[str]) -> MediaInfo:
        """``image`` for a new version (``variant`` above 0); when it cannot be made, the earlier version stays
        (a warning; retried by the next build when the failure was transient). Safety refusals and a refused
        personal key are not hidden: without an earlier version they propagate as before."""
        try:
            return await self.image(prompt, aspect, scene_id, variant)
        except _PASSTHROUGH:
            raise
        except Exception as exc:
            if self._key_rejected(exc):
                raise
            earlier = await self.earlier_image_version(prompt, aspect, scene_id, variant)
            if earlier is None:
                raise
            if not isinstance(exc, _PERMANENT):
                self.retry_scene_ids.add(scene_id)
            warnings.append(f"The new AI version could not be made ({self.settings.redact(str(exc))[:160]}); "
                            "the earlier one is shown.")
            return earlier

    async def image(self, prompt: str, aspect: str, scene_id: str | None = None, variant: int = 0) -> MediaInfo:
        """Generated (or cached) image from the first provider of the chain that can make it.

        With backups configured, an image any chain provider made before is reused first (never paid
        twice); with the lecture's explicit choice (``GenerationOptions.image_provider``, not the server
        default) only the chosen provider's earlier image is, another provider's only after the choice failed.
        A failure of the provider call moves on to the next provider unless it was a safety refusal or a
        refused personal API key (those propagate); a storage / claim / database failure propagates too.
        When every provider failed, a retryable failure is raised if there was one (the scene is retried by
        the next build), else the last one. ``variant`` above 0 asks for a new version of the same request
        (its own content key; ``_image_keys``).
        """
        providers, skipped = self._media_providers("image")
        if not providers:
            earlier = await self.earlier_image(prompt, aspect, scene_id, variant)
            if earlier is not None:
                return earlier
            if skipped:
                raise skipped[0][1]
            raise MediaUnavailable("image generation is not configured")
        raw = self._full_image_prompt(prompt)
        full = normalize_prompt(raw)  # what the provider is sent (and keyed by)
        requested = providers[0].name
        keys = {p.name: self._image_keys(raw, p.name, aspect, variant) for p in providers}
        # A lecture's explicit choice is honoured even when another provider made this image before: only the
        # chosen provider's earlier result is reused up front; the others' only after the choice failed.
        choice = getattr(self.options, "image_provider", None)
        explicit = bool(choice) and requested == choice and choice != self.settings.image_provider
        candidates = [k for p in providers for k in keys[p.name] if not explicit or p.name == requested]
        if len(candidates) > 1:  # a backup (or the pre-normalisation key) may have this image already
            cached = await asyncio.to_thread(self.ctx.assets.first_existing, candidates)
            if cached is not None:
                made_by = next(p.name for p in providers if cached.key in keys[p.name])
                self._chain_issue("image", made_by, requested, [], scene_id)
                self._media_suspect(scene_id, cached.meta)
                return self._image_info(cached)
        ordered = self._ordered(providers, aspect)
        errors: list[BaseException] = []
        failed: list[str] = []
        for i, provider in enumerate(ordered):
            remaining = len(ordered) - i - 1
            if explicit and provider.name != requested and len(keys[provider.name]) > 1:
                # a backup made it before under its pre-normalisation key (get_or_create finds the canonical one)
                cached = await asyncio.to_thread(self.ctx.assets.first_existing, keys[provider.name])
                if cached is not None:
                    self._chain_issue("image", provider.name, requested, failed, scene_id)
                    self._media_suspect(scene_id, cached.meta)
                    return self._image_info(cached)
            try:
                asset = await self._produce_image(provider, keys[provider.name][0], full, prompt, aspect, requested,
                                                  failed, variant)
            except RateLimited as exc:
                if not remaining:
                    raise
                failed.append(self._chain_failed("image", provider, exc, remaining, scene_id))
                errors.append(exc)
                continue
            except _PASSTHROUGH:
                raise
            except Exception as exc:
                if not getattr(exc, _PROVIDER_CALL, False):
                    raise  # storage / claim / database failure: the scene fails, no other provider is tried
                if self._key_rejected(exc) or not fallback_allowed(classify_failure(exc)):
                    raise
                failed.append(self._chain_failed("image", provider, exc, remaining, scene_id))
                errors.append(exc)
                continue
            provider_health().note_success(provider.name, self._health_scope(provider.name))
            self._chain_issue("image", provider.name, requested, failed, scene_id)
            self._media_suspect(scene_id, asset.meta)
            return self._image_info(asset)
        raise self._chain_error(errors)

    @staticmethod
    def _image_info(asset: Any) -> MediaInfo:
        return MediaInfo(asset_key=asset.key, storage_key=asset.storage_key, kind="image", mime=asset.mime,
                         width=asset.width, height=asset.height, source="image")

    async def _produce_image(self, provider: Any, key: str, full: str, prompt: str, aspect: str, requested: str,
                             failed: list[str], variant: int = 0) -> Any:
        # A new version is a seed hint for providers that take one (others make a new image anyway).
        extra: dict[str, Any] = {"variant": int(variant)} if variant > 0 and _accepts_variant(provider) else {}

        async def produce() -> Produced:
            started = time.monotonic()
            async with integrations.limit("image"):
                try:
                    res = await provider.generate(full, aspect=aspect, on_usage=self.ctx.record_usage, **extra)
                except Exception as exc:
                    _provider_call_failed(exc)
                    raise
            model = self.settings.image_model if provider.name == "gemini" else ""
            meta: dict[str, Any] = {"prompt": prompt[:500],
                                    **self._provenance(provider, model, aspect, requested, failed, started)}
            if variant > 0:
                meta["variant"] = int(variant)
            warnings = [str(w)[:200] for w in (getattr(res, "warnings", None) or [])]
            if warnings:
                meta["warnings"] = warnings[:5]
            return Produced(data=res.data, mime=res.mime, width=res.width, height=res.height, meta=meta)

        asset, _ = await self.ctx.assets.get_or_create(key, "image", produce, created_by=self.ctx.user_id,
                                                       inflight_scope=self._key_scope(provider.name))
        return asset

    def _manim_disabled_reason(self) -> str | None:
        if not self.options.allow_manim:
            return "Manim animations are disabled in the generation options"
        if self.settings.manim_sandbox == "disabled":
            return "Manim rendering is disabled on this server"
        if integrations.render_manim_fn() is None:
            return "the Manim renderer is not installed"
        return None

    async def manim(self, scene: Any, spec: ManimSpec, audio: SceneAudio | None, target: str,
                    show_from: str | None = None) -> MediaInfo | None:
        from ..manim.base import ManimError, ManimRenderRequest

        render = integrations.render_manim_fn()
        assert render is not None
        beats = scene.all_beats()
        if audio is not None and audio.beats:
            starts = [SCENE_LEAD_SECONDS + b.offset for b in audio.beats]
            total = SCENE_LEAD_SECONDS + audio.duration + SCENE_TAIL_SECONDS
        else:  # no narration audio: estimated timing
            starts, t = [], SCENE_LEAD_SECONDS
            for b in beats:
                starts.append(t)
                t += beat_seconds(b, self.language)
            total = t + SCENE_TAIL_SECONDS
        cues = [b.visual_cue or "" for b in beats]
        if show_from:  # panel video starts when the panel appears
            idx = next((i for i, b in enumerate(beats) if b.id == show_from), 0)
            origin = starts[idx] if starts else 0.0
            starts, cues, total = [s - origin for s in starts[idx:]], cues[idx:], total - origin
        req = ManimRenderRequest(
            spec=spec, beat_times=[round(s, 3) for s in starts], beat_cues=cues, total_duration=round(max(1.0, total), 3),
            target=target, title=scene.title, narration_context=" ".join(b.narration for b in beats)[:4000],  # type: ignore[arg-type]
            background=self.settings.manim_background, quality=self.settings.manim_quality, language=self.board_language,
            llm_provider=self.options.llm_provider,  # code repair / visual QA use the lecture's AI engine
        )
        try:
            res = await render(self.ctx, req)
        except ManimError as exc:
            # The plain-words reason goes to the teacher (no code, paths or log lines); the details to the log.
            log.warning("manim render failed for scene %s (%s): %s", scene.id, exc.category,
                        self.settings.redact(str(exc))[:300])
            self.issues.append(_issue("manim.render_failed", scene.id,
                                      f"The animation could not be rendered ({exc.friendly}); the scene falls back to "
                                      "its content. Rebuild this scene to try again, or regenerate it.",
                                      source="manim", data={"category": exc.category}))
            return None
        if res.healed:
            self.issues.append(_issue("manim.healed", scene.id, "The animation code was repaired automatically; please review it.",
                                      "info", "manim"))
        for qa in res.qa_issues[:3]:
            self.issues.append(_issue("manim.visual_qa", scene.id, f"Animation layout: {qa}", "info", "manim"))
        return MediaInfo(asset_key=res.asset_key, storage_key=res.storage_key, kind="video", mime="video/mp4",
                         duration=res.duration, width=res.width, height=res.height, source="manim")

    def _video_keys(self, scene: AIVideoScene, name: str) -> list[str]:
        """Content keys of a clip request: canonical (normalised prompt) first, then the key of the
        un-normalised prompt used before prompts were normalised (identical for normalised prompts).
        A new version (``scene.variant`` above 0) adds ``"variant"`` and has only the canonical key."""
        inputs: dict[str, Any] = {"provider": name, "model": self.settings.veo_model, "aspect": SCENE_IMAGE_ASPECT}
        if name == "veo" and self.settings.veo_duration_seconds:  # part of the key only when set
            inputs["duration"] = int(self.settings.veo_duration_seconds)
        variant = int(getattr(scene, "variant", 0) or 0)
        if variant > 0:
            return [compute_key("video", {**inputs, "prompt": normalize_prompt(scene.video_prompt),
                                          "variant": variant})]
        canonical = compute_key("video", {**inputs, "prompt": normalize_prompt(scene.video_prompt)})
        return list(dict.fromkeys([canonical, compute_key("video", {**inputs, "prompt": scene.video_prompt})]))

    def _video_key(self, scene: AIVideoScene, provider: Any) -> str:
        return self._video_keys(scene, provider.name)[0]

    async def earlier_video(self, scene: AIVideoScene) -> MediaInfo | None:
        """A clip generated earlier for this scene's request by any known provider (AI video is off or
        not configured at the server level; the lecture's opt-in and caps are checked by the caller)."""
        candidates = [k for name in self._offline_names("video") for k in self._video_keys(scene, name)]
        cached = await asyncio.to_thread(self.ctx.assets.first_existing, candidates, verify_blob=True)
        if cached is None:
            return None
        self._cached_media_issue("video", scene.id)
        self._media_suspect(scene.id, cached.meta)
        return self._video_info(cached)

    async def earlier_video_version(self, scene: AIVideoScene) -> MediaInfo | None:
        """The latest stored earlier version (``variant`` - 1 down to the first) of the scene's clip, by any known
        provider: what the scene keeps showing when its new AI version cannot be made."""
        candidates = [k for v in range(int(scene.variant) - 1, -1, -1)
                      for name in self._offline_names("video")
                      for k in self._video_keys(scene.model_copy(update={"variant": v}), name)]
        cached = await asyncio.to_thread(self.ctx.assets.first_existing, candidates, verify_blob=True)
        if cached is None:
            return None
        self._media_suspect(scene.id, cached.meta)
        return self._video_info(cached)

    def _video_seconds(self, provider: Any) -> float:
        if provider.name == "veo" and self.settings.veo_duration_seconds:
            return float(self.settings.veo_duration_seconds)
        return float(DEFAULT_VIDEO_SECONDS)

    async def _generate_video(self, scene: AIVideoScene) -> MediaInfo | None:
        """Generated (or cached) clip; None when the estimated cost would exceed the budget.

        Only the up-front estimate degrades to a still: a ``BudgetExceeded`` raised while recording
        the actual spend propagates and fails the job like every other budget breach. Backups
        (``VIDEO_FALLBACK_PROVIDERS``) are tried like image backups, each with its own budget estimate.
        A clip an earlier attempt of this job already submitted is collected without an estimate: its
        charge was counted then (the actual usage is still enforced by ``record_usage``).
        """
        providers, skipped = self._media_providers("video")
        if not providers:
            earlier = await self.earlier_video(scene)
            if earlier is not None:
                return earlier
            if skipped:
                raise skipped[0][1]
            raise MediaUnavailable("AI video is not configured")
        requested = providers[0].name
        keys = {p.name: self._video_keys(scene, p.name) for p in providers}
        cached = await asyncio.to_thread(self.ctx.assets.first_existing,
                                         [k for p in providers for k in keys[p.name]], verify_blob=True)
        if cached is not None:
            made_by = next(p.name for p in providers if cached.key in keys[p.name])
            self._chain_issue("video", made_by, requested, [], scene.id)
            self._media_suspect(scene.id, cached.meta)
            return self._video_info(cached)
        ordered = self._ordered(providers, SCENE_IMAGE_ASPECT)
        errors: list[BaseException] = []
        failed: list[str] = []
        for i, provider in enumerate(ordered):
            remaining = len(ordered) - i - 1
            if not self._collecting(provider, keys[provider.name][0]):
                estimate = integrations.estimate_cost(
                    Usage(provider=provider.name, model=self.settings.veo_model, operation="video",
                          seconds=self._video_seconds(provider), units=1), self.settings, default=2.0)
                try:
                    self.ctx.ensure_budget(estimate, provider=provider.name)
                except BudgetExceeded:
                    failed.append(f"{provider.name}: over the cost budget")
                    continue
            try:
                asset = await self._produce_video(scene, provider, keys[provider.name][0], requested, failed)
            except RateLimited as exc:
                if not remaining:
                    raise
                failed.append(self._chain_failed("video", provider, exc, remaining, scene.id))
                errors.append(exc)
                continue
            except _PASSTHROUGH:
                raise
            except VideoNeedsAttention:
                raise  # possibly billed already: never paid for again elsewhere without a person
            except Exception as exc:
                if not getattr(exc, _PROVIDER_CALL, False):
                    raise  # storage / claim / database failure: the scene fails, no other provider is tried
                if self._key_rejected(exc) or not fallback_allowed(classify_failure(exc)):
                    raise
                failed.append(self._chain_failed("video", provider, exc, remaining, scene.id))
                errors.append(exc)
                continue
            provider_health().note_success(provider.name, self._health_scope(provider.name))
            self._chain_issue("video", provider.name, requested, failed, scene.id)
            self._media_suspect(scene.id, asset.meta)
            return self._video_info(asset)
        if errors:
            raise self._chain_error(errors)
        return None  # every provider would exceed the budget

    def _collecting(self, provider: Any, key: str) -> bool:
        """An earlier attempt of this job already submitted this clip to a resumable provider: collecting it
        pays nothing new (actual usage is still enforced by ``record_usage``)."""
        if not (getattr(provider, "resumable", False) and callable(getattr(provider, "resume", None))):
            return False
        record = self.operations.get(key, self._key_scope(provider.name))
        return record is not None and record.status == SUBMITTED and bool(record.operation)

    @staticmethod
    def _video_info(asset: Any) -> MediaInfo:
        return MediaInfo(asset_key=asset.key, storage_key=asset.storage_key, kind="video", mime=asset.mime,
                         duration=asset.duration_s, width=asset.width, height=asset.height, source="veo")

    async def _produce_video(self, scene: AIVideoScene, provider: Any, key: str, requested: str,
                             failed: list[str]) -> Any:
        scope = self._key_scope(provider.name)
        resumable = bool(getattr(provider, "resumable", False)) and callable(getattr(provider, "resume", None))
        resumed = {"value": False}
        prompt = normalize_prompt(scene.video_prompt)
        # Collecting a provider job an earlier attempt already submitted pays nothing: no still-wanted guard.
        pending = self.operations.get(key, scope) if resumable else None
        resuming = pending is not None and pending.status == SUBMITTED and bool(pending.operation)

        async def produce() -> Produced:
            started = time.monotonic()
            async with integrations.limit("video"):
                if resumable:
                    res = await self._checkpointed_video(scene, provider, key, scope, resumed)
                else:
                    try:
                        res = await provider.generate(prompt, aspect=SCENE_IMAGE_ASPECT,
                                                      timeout_s=self.settings.ai_video_timeout_seconds,
                                                      on_usage=self.ctx.record_usage)
                    except Exception as exc:
                        _provider_call_failed(exc)
                        raise
            model = self.settings.veo_model if provider.name == "veo" else ""
            meta: dict[str, Any] = {"prompt": scene.video_prompt[:500],
                                    **self._provenance(provider, model, SCENE_IMAGE_ASPECT, requested, failed, started)}
            if scene.variant > 0:
                meta["variant"] = int(scene.variant)
            if resumed["value"]:
                meta["resumed"] = True
            warnings = [str(w)[:200] for w in (getattr(res, "warnings", None) or [])]
            if warnings:
                meta["warnings"] = warnings[:5]
            return Produced(data=res.data, mime=res.mime, duration_s=res.duration, width=res.width,
                            height=res.height, meta=meta)

        asset, _ = await self.ctx.assets.get_or_create(key, "video", produce, created_by=self.ctx.user_id,
                                                       inflight_scope=scope, guarded=not resuming)
        if resumable:  # stored: nothing left to resume (a crash before this line finds the asset first)
            record = self.operations.get(key, scope)
            if record is not None and record.status in (PENDING, SUBMITTING, SUBMITTED):
                record.status = COMPLETED
                await self._save_operation(record)
        return asset

    async def _save_operation(self, record: Any) -> None:
        """Best-effort update of a checkpoint after the outcome is known (a lost lease still propagates)."""
        try:
            await self.operations.save(record)
        except JobCancelled:
            raise
        except Exception:  # noqa: BLE001 - the media outcome matters more than its bookkeeping
            log.warning("could not update the video checkpoint", exc_info=True)

    async def _checkpointed_video(self, scene: AIVideoScene, provider: Any, key: str, scope: str,
                                  resumed: dict[str, bool]) -> Any:
        """Resume the provider job a previous attempt of this job started, or submit a checkpointed one.

        A paid job whose submission was never confirmed (``submitting``), or that cannot be found again
        (``lost``), is not submitted again automatically: ``VideoNeedsAttention``. A ``pending`` record (saved,
        but no request outstanding: the last answer was an error) is submitted normally.
        """
        paid = bool(getattr(provider, "paid", False))
        timeout = self.settings.ai_video_timeout_seconds
        record = self.operations.get(key, scope)
        if record is not None and record.status == SUBMITTED and record.operation:
            self.ctx.log("Resuming the AI video started earlier (before a restart, or by another job); "
                         "no new generation is paid.", scene_id=scene.id, provider=provider.name, asset_key=key)
            try:
                res = await provider.resume(record.operation, record.key_fingerprint, timeout_s=timeout,
                                            on_usage=self.ctx.record_usage, usage_recorded=record.usage_recorded,
                                            checkpoint=self.operations.checkpoint(record))
            except OperationLost:
                record.status = LOST
                await self._save_operation(record)
                self.ctx.log("The AI video started earlier (before a restart, or by another job) cannot be found "
                             "again.", level="warning", scene_id=scene.id, provider=provider.name, asset_key=key)
                if paid:
                    raise VideoNeedsAttention(
                        "video.operation_lost",
                        "The AI video started before a restart could not be found again and may already have "
                        "been billed, so it was not generated again automatically. Rebuild this scene to "
                        "generate it again.") from None
            except Exception as exc:
                _provider_call_failed(exc)
                record.status = FAILED
                await self._save_operation(record)
                raise
            else:
                resumed["value"] = True
                return res
        elif record is not None and paid and (record.status in (SUBMITTING, AMBIGUOUS, LOST)
                                              or (record.status == SUBMITTED and not record.operation)):
            # The teacher accepted a possible second charge for an earlier job's submission: submit anew (once; a
            # submission of this job that became ambiguous itself is never re-sent without a new confirmation).
            if scene.id in self.confirmed_paid and record.job_id != self._job_id:
                self.ctx.log("Generating the AI video again: the teacher confirmed that an earlier attempt may "
                             "already have been billed.", level="warning", scene_id=scene.id, provider=provider.name,
                             asset_key=key)
                return await self._submit_video(scene, provider, key, scope, timeout)
            if record.status in (SUBMITTING, SUBMITTED):
                record.status = AMBIGUOUS
                await self._save_operation(record)
            self.ctx.log("Not regenerating the AI video: a previous attempt may already have been billed.",
                         level="warning", scene_id=scene.id, provider=provider.name, asset_key=key)
            raise VideoNeedsAttention(
                "video.ambiguous_submission",
                "A previous attempt to generate this AI video stopped before its answer was saved and may "
                "already have been billed, so it was not generated again automatically. Rebuild this scene "
                "to generate it again.")
        return await self._submit_video(scene, provider, key, scope, timeout)

    async def _submit_video(self, scene: AIVideoScene, provider: Any, key: str, scope: str, timeout: int) -> Any:
        """Submit a new paid clip job with a checkpoint saved first (see ``_checkpointed_video``)."""
        record = self.operations.new(key, scope, provider.name, self.settings.veo_model)
        if getattr(provider, "reports_sending", False):  # it marks the record "submitting" around each request
            record.status = PENDING
        await self.operations.save(record)  # durable before the paid request is sent
        try:
            return await provider.generate(normalize_prompt(scene.video_prompt), aspect=SCENE_IMAGE_ASPECT,
                                           timeout_s=timeout,
                                           on_usage=self.ctx.record_usage,
                                           checkpoint=self.operations.checkpoint(record))
        except Exception as exc:
            _provider_call_failed(exc)
            # This process is alive and knows the outcome: not ambiguous. An accepted job given up on was
            # reported as abandoned usage by the provider.
            record.status = ABANDONED if record.status == SUBMITTED else FAILED
            await self._save_operation(record)
            raise

    async def ai_video(self, scene: AIVideoScene, warnings: list[str]) -> tuple[MediaInfo | None, bool]:
        if scene.override_asset_key:
            info = await self.asset_info(scene.override_asset_key, "upload")
            if info is not None:
                return info, info.kind != "video"
            warnings.append("The uploaded media for this scene is missing.")
        if scene.id in self.video_scene_ids:
            try:
                info = await self._generate_video(scene)
                if info is not None:
                    return info, False
                warnings.append("AI video skipped: it would exceed the cost budget; showing a still image instead.")
            except _PASSTHROUGH:
                raise
            except Exception as exc:  # noqa: BLE001 - provider failure degrades to a still
                if self._key_rejected(exc):
                    raise
                if not isinstance(exc, _PERMANENT):
                    self.retry_scene_ids.add(scene.id)  # transient: try the clip again on the next build
                if isinstance(exc, VideoNeedsAttention):
                    self.issues.append(_issue(exc.code, scene.id, exc.message))
                    warnings.append("AI video not generated again automatically (it may already have been "
                                    "billed); showing a still image instead.")
                else:
                    earlier = await self.earlier_video_version(scene) if scene.variant > 0 else None
                    if earlier is not None:  # a new version that failed: the earlier clip stays
                        warnings.append(f"The new AI version could not be made "
                                        f"({self.settings.redact(str(exc))[:160]}); the earlier one is shown.")
                        return earlier, False
                    warnings.append(f"AI video failed ({self.settings.redact(str(exc))[:160]}); "
                                    "showing a still image instead.")
        elif self.options.allow_ai_video and self.settings.video_provider != "none":
            warnings.append("AI video limit reached for this lecture; showing a still image instead.")
        elif scene.id in self._wanted_video_ids():  # AI video is off on this server: a clip made earlier
            earlier = await self.earlier_video(scene)
            if earlier is None and scene.variant > 0:
                earlier = await self.earlier_video_version(scene)
                if earlier is not None:
                    warnings.append(NEW_VERSION_UNAVAILABLE)
            if earlier is not None:
                return earlier, False
        if scene.fallback_figure_id:
            fig = await self.figure(scene.fallback_figure_id)
            if fig is not None:
                return fig.model_copy(update={"source": "fallback"}), True
        prompt = scene.fallback_image_prompt or scene.video_prompt
        if self.options.allow_generated_images and self.settings.image_provider != "none":
            try:
                if scene.variant > 0:
                    img = await self.new_image_version(prompt, SCENE_IMAGE_ASPECT, scene.id, scene.variant, warnings)
                else:
                    img = await self.image(prompt, SCENE_IMAGE_ASPECT, scene.id, scene.variant)
                return img.model_copy(update={"source": "fallback"}), True
            except MediaUnavailable as exc:
                warnings.append(f"No still image: {exc}.")
                return None, True
        if self.options.allow_generated_images:  # image generation is off on this server
            earlier_still = await self.earlier_image(prompt, SCENE_IMAGE_ASPECT, scene.id, scene.variant)
            if earlier_still is None and scene.variant > 0:
                earlier_still = await self.earlier_image_version(prompt, SCENE_IMAGE_ASPECT, scene.id, scene.variant)
                if earlier_still is not None:
                    warnings.append(NEW_VERSION_UNAVAILABLE)
            if earlier_still is not None:
                return earlier_still.model_copy(update={"source": "fallback"}), True
        warnings.append("No still image is available for this video scene.")
        return None, True

    async def gif(self, query: str) -> MediaInfo | None:
        provider = integrations.get_gif(self.settings)
        if provider is None:
            raise MediaUnavailable("GIF search is not configured")
        res = await provider.search(query, on_usage=self.ctx.record_usage)
        return MediaInfo(kind="gif", mime="image/gif", width=res.width, height=res.height, external_url=res.url,
                         attribution=res.attribution, source="gif")

    def _starts_own_lecture(self, db: Any) -> bool:
        """True when the job's user owns the lecture being built (cached). Libraries are personal: only such a
        build uses or fills one; an admin building someone else's lecture never mixes libraries (SECURITY.md)."""
        if self._owner_started is None:
            from sqlalchemy import select

            from ..models import Project

            user_id, project_id = getattr(self.ctx, "user_id", None), getattr(self.ctx, "project_id", None)
            owner = None
            if user_id and project_id:
                owner = db.execute(select(Project.owner_id).where(Project.id == int(project_id))).scalar_one_or_none()
            self._owner_started = owner is not None and int(owner) == int(user_id)
        return self._owner_started

    def _wants_library(self, scene: Any) -> bool:
        """The scene's image side panel would be generated now and may come from the library instead."""
        p = scene.side_panel
        return (p is not None and p.kind == "image" and bool(p.image_prompt) and not p.override_asset_key
                and p.variant == 0)

    def _plan_library(self) -> dict[str, tuple[str, str, float]]:
        """``prefer_library_visuals`` for the whole build (blocking): scene id -> (asset key, title, score) of the
        owner's library picture each image panel this run builds gets (``library.auto_match`` on its image prompt),
        in scene order, one picture per scene of the lecture. Never media generated for this lecture, nor the
        picture the scene showed (a prompt edit must show a new picture; an unchanged prompt finds its own picture
        in the cache), nor a picture another scene of the lecture shows. One matcher for the build."""
        user_id, project_id = getattr(self.ctx, "user_id", None), getattr(self.ctx, "project_id", None)
        if not user_id or not project_id or not callable(getattr(self.ctx, "session", None)):
            return {}
        wanted = [s for s in self.sp.scenes if s.id in self._building and self._wants_library(s)]
        if not wanted:
            return {}
        from .. import library

        prev = self.previous.media if self.previous is not None else {}
        shown_by = {sid: m.side_panel.asset_key for sid, m in prev.items()
                    if m.side_panel is not None and m.side_panel.asset_key}
        current = {s.id for s in self.sp.scenes}
        taken = {key for sid, key in shown_by.items() if sid in current and sid not in self._building}
        plan: dict[str, tuple[str, str, float]] = {}
        with self.ctx.session() as db:
            if not self._starts_own_lecture(db):
                return {}
            matcher = library.LibraryMatcher.load(db, int(user_id), kinds=["image"],
                                                  texts=[s.side_panel.image_prompt for s in wanted])
            for scene in wanted:
                own = shown_by.get(scene.id)

                def superseded(item: Any, own: str | None = own) -> bool:
                    return item.source == "generated" and (
                        item.origin_project_id == int(project_id) or item.asset_key == own)

                found = library.auto_match(db, int(user_id), scene.side_panel.image_prompt, "image",
                                           exclude=taken, skip=superseded, matcher=matcher)
                if found is None:
                    continue
                item, value = found
                taken.add(str(item.asset_key))
                plan[scene.id] = (str(item.asset_key), str(getattr(item, "title", "") or ""), float(value))
        return plan

    async def library_image(self, scene: Any) -> MediaInfo | None:
        """``prefer_library_visuals``: the picture of the build's library plan for this scene (``_plan_library``:
        the lecture owner's own library, matched on the image prompt), shown like an upload with an info issue
        naming it; else None (the image is generated as usual). Only a build the lecture's owner started uses a
        library; any failure of the lookup just means no match."""
        async with self._library_lock:
            if self._library_plan is None:
                try:
                    self._library_plan = await asyncio.to_thread(self._plan_library)
                except _PASSTHROUGH:
                    raise
                except Exception as exc:  # noqa: BLE001 - the library is optional: generate as usual
                    log.warning("library match failed: %s", type(exc).__name__)
                    self._library_plan = {}
        found = self._library_plan.get(scene.id)
        if found is None:
            return None
        key, title, score = found
        info = await self.asset_info(key, "upload")
        if info is None or info.kind != "image":
            return None
        label = f" '{title[:80]}'" if title else ""
        self.issues.append(Issue(
            code="assets.library_visual_used", severity="info", source="assets", fixable=False, scene_id=scene.id,
            message=f"A picture from your library{label} was used instead of generating one "
                    f"(match {score:.2f})."))
        return info

    async def side_panel(self, scene: Any, audio: SceneAudio | None, warnings: list[str]) -> MediaInfo | None:
        p = scene.side_panel
        if p is None or p.kind not in ("figure", "image", "manim", "gif"):
            return None
        if p.override_asset_key:
            info = await self.asset_info(p.override_asset_key, "upload")
            if info is not None:
                return info
            warnings.append("The uploaded side-panel media is missing; using the generated one.")
        if p.kind == "figure":
            info = await self.figure(p.figure_id)
            if info is None:
                warnings.append(f"Figure {p.figure_id!r} is not available.")
            return info
        if p.kind == "image" and p.image_prompt:
            if getattr(self.options, "prefer_library_visuals", False) and p.variant == 0:  # never for a new version
                picked = await self.library_image(scene)
                if picked is not None:
                    return picked
            if not self.options.allow_generated_images or self.settings.image_provider == "none":
                earlier = None
                if self.options.allow_generated_images:  # off on this server, not by the teacher's choice
                    earlier = await self.earlier_image(p.image_prompt, PANEL_IMAGE_ASPECT, scene.id, p.variant)
                    if earlier is None and p.variant > 0:  # a new version cannot be made: the earlier one stays
                        earlier = await self.earlier_image_version(p.image_prompt, PANEL_IMAGE_ASPECT, scene.id,
                                                                   p.variant)
                        if earlier is not None:
                            warnings.append(NEW_VERSION_UNAVAILABLE)
                if earlier is None:
                    warnings.append("Generated images are disabled; the image panel is hidden.")
                return earlier
            if p.variant > 0:
                return await self.new_image_version(p.image_prompt, PANEL_IMAGE_ASPECT, scene.id, p.variant, warnings)
            return await self.image(p.image_prompt, PANEL_IMAGE_ASPECT, scene.id, p.variant)
        if p.kind == "manim" and p.manim is not None:
            reason = self._manim_disabled_reason()
            if reason:
                warnings.append(f"Animation panel skipped: {reason}.")
                return None
            return await self.manim(scene, p.manim, audio, "panel", p.show_from_beat_id)
        if p.kind == "gif" and p.gif_query:
            if not self.options.allow_gifs:
                return None
            return await self.gif(p.gif_query)
        return None

    async def scene_media(self, scene: Any, audio: SceneAudio | None) -> SceneMedia:
        media = SceneMedia(scene_id=scene.id)
        warnings: list[str] = []
        steps: list[tuple[str, Any]] = []
        if isinstance(scene, BoardScene):
            for item in scene.board:
                if item.kind == BoardItemKind.figure and item.figure_id:
                    steps.append(("figure:" + item.id, item))
        steps.append(("panel", None))
        if isinstance(scene, SimulationScene):
            steps.append(("simulation", None))
        if isinstance(scene, AIVideoScene):
            steps.append(("ai_video", None))
        if isinstance(scene, InteractiveScene) and scene.poster_override_asset_key:
            steps.append(("poster", None))
        figures: dict[str, MediaInfo] = {}
        for name, item in steps:
            try:
                if name.startswith("figure:"):
                    info = await self.figure(item.figure_id)
                    if info is None:
                        warnings.append(f"Figure {item.figure_id!r} is not available.")
                    else:
                        figures[item.id] = info
                elif name == "panel":
                    media.side_panel = await self.side_panel(scene, audio, warnings)
                elif name == "simulation":
                    if scene.override_asset_key:
                        media.main = await self.asset_info(scene.override_asset_key, "upload")
                        if media.main is None:
                            warnings.append(OVERRIDE_MISSING_WARNINGS[0])
                    if media.main is None:
                        reason = self._manim_disabled_reason()
                        if reason:
                            warnings.append(f"Animation skipped: {reason}; the scene shows its content instead.")
                        else:
                            media.main = await self.manim(scene, scene.manim, audio, "fullscreen")
                            if media.main is None:
                                warnings.append("The animation could not be rendered; the scene shows its content instead.")
                elif name == "ai_video":
                    media.main, media.main_is_fallback = await self.ai_video(scene, warnings)
                elif name == "poster":
                    media.poster = await self.asset_info(scene.poster_override_asset_key, "poster")
                    if media.poster is None:
                        warnings.append(OVERRIDE_MISSING_WARNINGS[2])
            except _PASSTHROUGH:
                raise
            except MediaUnavailable as exc:
                warnings.append(f"{name.split(':')[0].replace('_', ' ').capitalize()} skipped: {exc}.")
            except Exception as exc:  # noqa: BLE001 - one failing medium must not fail the lecture
                if self._key_rejected(exc):
                    raise
                msg = self.settings.redact(f"{type(exc).__name__}: {exc}")[:200]
                log.warning("asset step %s failed for scene %s: %s", name, scene.id, msg)
                if not isinstance(exc, _PERMANENT):
                    self.retry_scene_ids.add(scene.id)
                warnings.append(f"{name.split(':')[0].replace('_', ' ').capitalize()} failed: {msg}")
        media.figures = figures
        media.warnings = warnings
        for w in warnings:
            # A teacher's upload or library pick that is gone is never replaced silently: its own issue code.
            code = "assets.override_missing" if w in OVERRIDE_MISSING_WARNINGS else "assets.media_degraded"
            self.issues.append(_issue(code, scene.id, w))
        return media

    async def build_scene(self, scene: Any) -> tuple[SceneAudio | None, SceneMedia]:
        audio: SceneAudio | None = None
        try:
            audio = await self.scene_audio(scene)
        except _PASSTHROUGH:
            raise
        except Exception as exc:  # noqa: BLE001 - a scene without audio degrades, the lecture continues
            if self._key_rejected(exc):
                raise
            msg = self.settings.redact(f"{type(exc).__name__}: {exc}")[:200]
            log.warning("narration failed for scene %s: %s", scene.id, msg)
            self.issues.append(_issue("assets.tts_failed", scene.id, f"Narration audio could not be generated ({msg}).", "error"))
        media = await self.scene_media(scene, audio)
        if audio is None and scene.all_beats():
            media.warnings.append("Narration audio is missing.")
        return audio, media

    # --- run ------------------------------------------------------------------------------
    async def run(self) -> AssetBuildResult:
        sp, prev = self.sp, self.previous
        audio: dict[str, SceneAudio] = {}
        media: dict[str, SceneMedia] = {}
        hashes: dict[str, str] = {}
        stale: list[str] = []
        to_build: list[Any] = []
        reused: list[str] = []
        kept_hidden: list[str] = []  # hidden scenes: never built (no provider call), earlier assets kept
        current = {s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}
        for s in sp.scenes:
            h = current[s.id]
            have_prev = prev is not None and s.id in prev.media
            if s.hidden:  # even when requested by ``scene_ids``: the timeline leaves it out
                if have_prev:
                    kept_hidden.append(s.id)
            elif self.scene_ids is not None and s.id not in self.scene_ids and have_prev:
                reused.append(s.id)
            elif self.scene_ids is not None and s.id in self.scene_ids:
                to_build.append(s)  # explicitly requested: always rebuilt (retries failed media)
            elif self._reusable(s, h):
                reused.append(s.id)
            else:
                to_build.append(s)
        if reused and prev is not None:  # verify that reused assets still exist
            keys = [k for sid in reused for k in _keys_of(prev.audio.get(sid), prev.media.get(sid))]
            found = await asyncio.to_thread(self.ctx.assets.get_many, keys)
            for sid in list(reused):
                if any(k not in found for k in _keys_of(prev.audio.get(sid), prev.media.get(sid))):
                    reused.remove(sid)
                    to_build.append(next(x for x in sp.scenes if x.id == sid))
            for sid in reused:
                if sid in prev.audio:
                    audio[sid] = prev.audio[sid]
                media[sid] = prev.media[sid]
                hashes[sid] = prev.scene_hashes.get(sid, "")
                if hashes[sid] != current[sid]:
                    stale.append(sid)
            await self._add_missing_envelopes(audio, reused)
        if kept_hidden and prev is not None:
            # Carried as they are (with their earlier hash, never listed stale: the build stays complete), so showing
            # the scene again needs no rebuild when it is unchanged; assets that are gone are dropped, not rebuilt.
            keys = [k for sid in kept_hidden for k in _keys_of(prev.audio.get(sid), prev.media.get(sid))]
            found = await asyncio.to_thread(self.ctx.assets.get_many, keys) if keys else {}
            for sid in kept_hidden:
                if any(k not in found for k in _keys_of(prev.audio.get(sid), prev.media.get(sid))):
                    continue
                if sid in prev.audio:
                    audio[sid] = prev.audio[sid]
                media[sid] = prev.media[sid]
                if prev.scene_hashes.get(sid):
                    hashes[sid] = prev.scene_hashes[sid]
        self._building = {s.id for s in to_build}
        sem = asyncio.Semaphore(SCENE_PARALLEL)
        done = {"n": 0}
        total = len(to_build)
        lo, hi = self.progress or (0.0, 1.0)

        async def one(scene: Any) -> tuple[str, SceneAudio | None, SceneMedia]:
            async with sem:
                self.ctx.check_cancelled()
                a, m = await self.build_scene(scene)
            done["n"] += 1
            self.ctx.progress("assets", lo + (hi - lo) * done["n"] / max(1, total), f"Building assets {done['n']}/{total}")
            return scene.id, a, m

        for sid, a, m in await gather_all(one(s) for s in to_build):
            if a is not None:
                audio[sid] = a
            media[sid] = m
            complete = (a is not None or not sp.scene_by_id(sid).all_beats()) and sid not in self.retry_scene_ids
            hashes[sid] = current[sid] if complete else ""  # no hash: shown stale, retried by the next build
        order = [s.id for s in sp.scenes]
        manifest = AssetManifest(
            language=sp.language,
            audio={k: audio[k] for k in order if k in audio},
            media={k: media[k] for k in order if k in media},
            scene_hashes={k: hashes[k] for k in order if hashes.get(k)},
            stale_scenes=[k for k in order if k in stale],
        )
        keys = sorted({k for sid in order for k in _keys_of(audio.get(sid), media.get(sid))})
        if self.ctx.project_id is not None and keys:
            def refs() -> None:
                with self.ctx.session() as db:
                    dbops.ensure_asset_refs(db, self.ctx.project_id, keys)

            await asyncio.to_thread(refs)
        await self._save_generated_to_library([s.id for s in to_build], media)
        return AssetBuildResult(manifest=manifest, issues=self.issues, built=[s.id for s in to_build],
                                reused=reused, asset_keys=keys)

    def _generated_media(self, scene_ids: list[str], media: dict[str, SceneMedia]) -> list[tuple[str, str, str]]:
        """(scene id, asset key, prompt) of the generated images and clips the built scenes show."""
        out: list[tuple[str, str, str]] = []
        for sid in scene_ids:
            m = media.get(sid)
            scene = next((s for s in self.sp.scenes if s.id == sid), None)
            if m is None or scene is None:
                continue
            panel = scene.side_panel
            if m.side_panel is not None and m.side_panel.asset_key and m.side_panel.source == "image" \
                    and panel is not None and panel.image_prompt:
                out.append((sid, m.side_panel.asset_key, panel.image_prompt))
            if isinstance(scene, AIVideoScene) and m.main is not None and m.main.asset_key:
                if m.main.source == "veo":
                    out.append((sid, m.main.asset_key, scene.video_prompt))
                elif m.main.source == "fallback" and m.main.kind == "image":
                    out.append((sid, m.main.asset_key, scene.fallback_image_prompt or scene.video_prompt))
        return out

    async def _save_generated_to_library(self, scene_ids: list[str], media: dict[str, SceneMedia]) -> None:
        """``LIBRARY_AUTO_SAVE_GENERATED``: record the generated images / clips of the built scenes in the library of
        the job's user when they own the lecture (``aadhi.library.record_generated``, idempotent; an admin's build
        of someone else's lecture records nothing). Only assets the stage generated (kind image / video) count,
        never uploads or figures. Best effort: a failure is logged, never raised."""
        user_id, project_id = getattr(self.ctx, "user_id", None), getattr(self.ctx, "project_id", None)
        if not (getattr(self.settings, "library_auto_save_generated", False) and user_id and project_id
                and callable(getattr(self.ctx, "session", None))):
            return
        found = self._generated_media(scene_ids, media)
        if not found:
            return

        def record() -> None:
            from .. import library

            rows = self.ctx.assets.get_many([key for _, key, _ in found])
            with self.ctx.session() as db:
                if not self._starts_own_lecture(db):
                    return
                for sid, key, prompt in found:
                    asset = rows.get(key)
                    if asset is None or asset.kind not in ("image", "video"):
                        continue
                    meta = asset.meta or {}
                    library.record_generated(
                        db, user_id=int(user_id), asset_key=key, kind=asset.kind, project_id=int(project_id),
                        scene_id=sid, prompt=prompt[:1200], provider=str(meta.get("provider") or "") or None,
                        model=str(meta.get("model") or "") or None, settings=self.settings)

        try:
            await asyncio.to_thread(record)
        except _PASSTHROUGH:
            raise
        except Exception as exc:  # noqa: BLE001 - bookkeeping only: never fails the build
            log.warning("could not save generated media to the library: %s", type(exc).__name__)


def _backup_provider(kind: str, name: str, settings: Any) -> Any:
    """A backup media provider by name (``ProviderNotConfigured`` when it cannot be used)."""
    from ..providers import factory

    reason = factory.media_unavailable_reason(kind, name, settings)
    if reason is not None:
        raise ProviderNotConfigured(f"{name}: {reason}", provider=name)
    return factory.get_image(settings, name) if kind == "image" else factory.get_video(settings, name)


async def build_assets_detailed(
    ctx: Any,
    screenplay: Screenplay,
    options: GenerationOptions,
    *,
    previous: AssetManifest | None = None,
    scene_ids: list[str] | None = None,
    progress: tuple[float, float] | None = None,
) -> AssetBuildResult:
    """Build (or reuse) every scene's assets; returns the manifest plus issues and stats."""
    return await _Builder(ctx, screenplay, options, previous, scene_ids, progress).run()


async def build_assets(
    ctx: Any,
    screenplay: Screenplay,
    options: GenerationOptions,
    *,
    previous: AssetManifest | None = None,
    scene_ids: list[str] | None = None,
) -> AssetManifest:
    """Contract entry point: the AssetManifest only (the screenplay is never modified)."""
    return (await build_assets_detailed(ctx, screenplay, options, previous=previous, scene_ids=scene_ids)).manifest
