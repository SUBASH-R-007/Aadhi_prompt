"""Application settings. Every knob comes from environment variables (or ``<repo>/.env``).

* Secrets are ``SecretStr`` (never printed by repr/logs); use ``Settings.secret_values()`` +
  ``redact()`` before writing any provider/job error message anywhere.
* Concurrency limits (``*_MAX_PARALLEL``, ``*_MAX_CONCURRENT``, ``RENDER_CONCURRENCY``) are
  **per-process global** limits, shared by every job/worker thread in the process
  (implemented in ``aadhi.jobs.limits``).
* Production (``APP_ENV=production``) refuses to start with unsafe configuration; see
  ``validate_for_runtime``. In development a JWT secret is generated once and persisted under
  ``DATA_DIR`` so restarts do not log everybody out.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from .security.redaction import registered_secrets

ROOT_DIR = Path(__file__).resolve().parent.parent

_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?$")
# Settings.redact: generic secret shapes echoed by providers that no setting knows about (a key inside
# a signed URL, an Authorization header). Minimum lengths keep ordinary words and slugs intact.
_SECRET_PATTERNS = (
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "[REDACTED]"),  # Google API keys
    (re.compile(r"\b(?:sk|rk)-[0-9A-Za-z_\-]{16,}"), "[REDACTED]"),  # OpenAI / Anthropic style keys
    (re.compile(r"(?i)\b(bearer\s+)[0-9A-Za-z._\-~+/=]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(sig|signature|x-goog-signature|x-goog-credential)=[^&\s]+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\b(authorization[\"']?\s*[:=]\s*[\"']?(?:basic\s+|token\s+)?)(?!bearer\s)[^\s,'\"}]{8,}"),
     r"\1[REDACTED]"),
)
ALL_JOB_KINDS = (
    "generate_lecture",
    "regenerate_scene",
    "build_assets",
    "render_video",
    "translate",
    "import_legacy",
    "cleanup",
)


def _split(v):
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return []
        if v.startswith("["):
            return json.loads(v)
        return [x.strip() for x in v.split(",") if x.strip()]
    return v


class Settings(BaseSettings):
    # hide_input_in_errors: a startup error about a malformed secret must not print the secret.
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    # --- Runtime -------------------------------------------------------------
    app_env: Literal["development", "production", "test"] = "development"
    base_url: str = "http://127.0.0.1:8000"  # public origin (share links, render worker, CSRF origin check)
    data_dir: Path = ROOT_DIR / "data"
    web_dir: Path = ROOT_DIR / "web"
    branding_dir: Path = ROOT_DIR / "video_template"  # mascot clips, bgm, logo, background
    log_level: str = "INFO"
    trusted_proxies: Annotated[list[str], NoDecode] = Field(default_factory=list)  # IPs allowed to set X-Forwarded-For
    max_json_body_mb: int = 5

    # --- Database ------------------------------------------------------------
    database_url: SecretStr = SecretStr("")  # default: sqlite:///<data_dir>/aadhi.db
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_timeout: int = 10

    # --- Auth / security -----------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_ttl_hours: int = 12
    cookie_secure: bool | None = None  # None = auto (True when base_url is https)
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)  # exact origins; empty = same-origin
    admin_username: str = "admin"
    admin_password: SecretStr = SecretStr("")  # bootstrap only (when no admin exists)
    password_min_length: int = 10
    login_rate_limit_per_minute: int = 10
    api_rate_limit_per_minute: int = 300
    generation_rate_limit_per_hour: int = 20
    sse_max_streams_per_user: int = 4
    sse_max_streams_total: int = 200

    # --- Storage -------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_local_dir: Path | None = None  # default: <data_dir>/storage
    media_url_prefix: str = "/media"  # local backend serving prefix
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""  # R2 / MinIO
    s3_access_key_id: SecretStr = SecretStr("")
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_prefix: str = "aadhi/"
    cdn_base_url: str = ""  # if set, public asset URLs are cdn_base_url + storage key (stable)
    s3_presign_ttl_seconds: int = 3600  # used only at serve time when no CDN is configured
    upload_max_mb: int = 50

    # --- Jobs ----------------------------------------------------------------
    worker_mode: Literal["inline", "external"] = "inline"  # inline = worker threads inside the API process
    worker_concurrency: int = 2  # worker threads per process
    worker_kinds: Annotated[list[str], NoDecode] = Field(default_factory=lambda: list(ALL_JOB_KINDS))
    job_max_attempts: int = 2
    job_stale_seconds: int = 300  # lease expiry: running job without heartbeat this long is requeued
    job_poll_interval_seconds: float = 1.0
    job_event_retention_days: int = 30
    analytics_retention_days: int = 365

    # --- Jobs safety: paid-generation claims and LLM stage checkpoints -------
    # (aadhi.storage.assets claims + generation guard, aadhi.pipeline.checkpoint)
    generation_claim_kinds: str = "video,image"  # paid asset kinds generated only once across worker processes, and re-checked as still wanted right before generating (comma list; empty = off)
    generation_claim_ttl_seconds: int = Field(default=120, ge=10)  # a generation claim expires this long after its last renewal (the holder renews every ttl/4), so a crashed worker's claim is taken over
    generation_claim_poll_seconds: float = Field(default=2.0, gt=0)  # how often a job waiting for another worker's identical generation looks for the result
    generation_claim_max_wait_seconds: int = Field(default=1200, ge=10)  # longest wait for another worker's generation; then the scene shows a still image and is retried on the next build
    llm_stage_checkpoints: bool = True  # a retried generate_lecture / translate job reuses the LLM stages its earlier attempt finished (plan, scenes, review, companion sheet, translation)

    # --- LLM -----------------------------------------------------------------
    # Default AI engine; a lecture may pick another configured engine (GenerationOptions.llm_provider).
    llm_provider: Literal["gemini", "openai", "anthropic", "fake"] = "gemini"
    # Model per tier. These are the Gemini models; a GPT or Claude model name set here applies to
    # that engine instead (aadhi.providers.factory.llm_models), so pre-engine .env files keep working.
    llm_model_plan: str = "gemini-2.5-pro"
    llm_model_script: str = "gemini-2.5-flash"
    llm_model_critic: str = "gemini-2.5-flash"
    llm_model_fast: str = "gemini-2.5-flash"
    llm_model_allowlist: Annotated[list[str], NoDecode] = Field(default_factory=list)  # admin overrides; empty = configured models only
    llm_max_parallel: int = 6
    llm_timeout_seconds: int = 600
    llm_first_response_timeout_seconds: int = Field(default=60, ge=10, le=600)
    llm_idle_timeout_seconds: int = Field(default=240, ge=30, le=1800)
    gemini_api_key: SecretStr = SecretStr("")
    gemini_api_keys: Annotated[list[SecretStr], NoDecode] = Field(default_factory=list)  # optional pool (rotated on 429)
    openai_api_key: SecretStr = SecretStr("")
    openai_model_plan: str = "gpt-4.1"
    openai_model_script: str = "gpt-4.1-mini"
    openai_model_critic: str = "gpt-4.1-mini"
    openai_model_fast: str = "gpt-4.1-mini"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model_plan: str = "claude-opus-5-5"
    anthropic_model_script: str = "claude-opus-5-5"
    anthropic_model_critic: str = "claude-opus-5-5"
    anthropic_model_fast: str = "claude-opus-5-5"
    anthropic_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"  # Claude thinking depth / token spend (output_config.effort)
    anthropic_max_tokens: int = Field(default=64000, ge=1024, le=128000)  # per-call output budget, includes thinking (streamed)
    anthropic_refusal_fallback: bool = True  # on a safety refusal the API re-runs the request on another Claude model
    anthropic_timeout_seconds: int = Field(default=1800, ge=60)  # cap on one streamed Claude call (replaces LLM_TIMEOUT_SECONDS for Claude)

    # --- API keys saved in the Studio ----------------------------------------
    # Precedence per provider (gemini, openai, anthropic) for a user's jobs: their personal key > the
    # server key an admin saved in the Studio > the *_API_KEY above.
    stored_api_keys_enabled: bool = True  # master switch for keys saved in the Studio (server and personal)
    user_api_keys_enabled: bool = True  # every user may save personal keys, used only for the jobs they start
    credentials_encryption_key: SecretStr = SecretStr("")  # Fernet key (urlsafe base64 of 32 bytes); empty = derived from JWT_SECRET (rotating it then requires re-entering saved keys)

    # --- TTS -----------------------------------------------------------------
    tts_provider: Literal["edge", "gemini", "openai", "elevenlabs", "fake"] = "edge"
    tts_voice: str = ""  # empty = language default (aadhi.providers.tts.voices)
    tts_rate: str = "+0%"  # edge-tts prosody rate
    tts_max_parallel: int = 6
    elevenlabs_api_key: SecretStr = SecretStr("")
    elevenlabs_model: str = "eleven_multilingual_v2"
    openai_tts_model: str = "tts-1-hd"
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"

    # --- Images / video / gifs ----------------------------------------------
    image_provider: Literal["gemini", "pollinations", "none", "fake"] = "pollinations"
    image_model: str = "gemini-2.5-flash-image"
    image_style_suffix: str = (
        "clean educational illustration, consistent soft lighting, purple and gold accent palette, no text"
    )
    video_provider: Literal["veo", "none", "fake"] = "none"
    veo_model: str = "veo-2.0-generate-001"
    max_ai_videos_per_lecture: int = 2
    ai_video_timeout_seconds: int = 420
    giphy_api_key: SecretStr = SecretStr("")

    # --- Media providers: fallback, cooldowns, output limits -------------------
    # (aadhi/providers/factory.py, health.py, operations.py; aadhi/pipeline/assets.py)
    # IMAGE_PROVIDER / VIDEO_PROVIDER stay the preferred provider ("none" still disables the medium). The
    # backups are tried in order when it is not configured, cooling down, or fails in a way another
    # provider may not (payment/account refusal, outage, rate limit, unusable output); never after a safety
    # refusal or a refused personal API key. A paid backup (gemini, veo) bills the server key, or the
    # user's own key when they saved one: configure it deliberately.
    image_fallback_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)  # backups for generated images in order, e.g. gemini (gemini | pollinations | fake); empty = no fallback
    video_fallback_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)  # backups for AI video in order (veo | fake); empty = no fallback
    media_provider_cooldown_seconds: int = Field(default=60, ge=0, le=3600)  # a provider that just hit a rate limit, outage, timeout or unusable output is tried after the others for this long (per worker process)
    media_provider_auth_cooldown_seconds: int = Field(default=600, ge=0, le=86400)  # same after a payment/account refusal (HTTP 401/402/403)
    ai_max_image_bytes: int = Field(default=20_000_000, ge=100_000)  # generated images above this size are rejected (downloads are streamed with this cap)
    ai_max_video_bytes: int = Field(default=500_000_000, ge=1_000_000)  # generated videos above this size are rejected
    veo_duration_seconds: int | None = Field(default=None, ge=1, le=60)  # requested Veo clip length (billed per second; Veo 2 accepts 5-8); empty = the model's default (8 s)
    allow_fake_providers: bool = False  # let APP_ENV=production run with LLM/TTS/IMAGE/VIDEO_PROVIDER=fake (offline demos only)

    @field_validator("image_fallback_providers", "video_fallback_providers", mode="before")
    @classmethod
    def _media_fallbacks(cls, v, info):
        names = [str(x).strip().lower() for x in (_split(v) or [])]
        allowed = ("gemini", "pollinations", "fake") if info.field_name == "image_fallback_providers" else ("veo", "fake")
        bad = [n for n in names if n not in allowed]
        if bad:
            raise ValueError(f"unknown {info.field_name.upper()} {bad}; use {', '.join(allowed)}")
        return list(dict.fromkeys(n for n in names if n))

    def fake_providers(self) -> list[str]:
        """Settings that select an offline stand-in provider (refused in production, see validate_for_runtime)."""
        out = [f"{name.upper()}_PROVIDER" for name in ("llm", "tts", "image", "video")
               if getattr(self, f"{name}_provider") == "fake"]
        out += [f"{name.upper()}_FALLBACK_PROVIDERS" for name in ("image", "video")
                if "fake" in getattr(self, f"{name}_fallback_providers")]
        return out

    # --- Manim ---------------------------------------------------------------
    manim_sandbox: Literal["docker", "subprocess", "disabled"] = "subprocess"
    manim_docker_image: str = "aadhi-manim-sandbox:latest"
    manim_allow_freeform: bool = True
    manim_quality: Literal["l", "m", "h"] = "m"
    manim_timeout_seconds: int = Field(default=180, ge=10, le=900)  # wall-clock cap on one render
    manim_max_concurrent: int = Field(default=2, ge=0, le=16)
    manim_max_repair_attempts: int = 2
    manim_visual_qa: bool = True
    manim_background: str = "#1A0B2E"

    # --- Manim sandbox hardening ---------------------------------------------
    # (aadhi/manim/sandbox.py, winjob.py, runtime/hardening.py; self-check: aadhi.cli manim-check)
    # Hard maximums the sandbox enforces. Existing .env values above a cap fail validation at startup
    # (see docs/OPERATIONS.md); the caps are generous for 480p-1080p renders.
    manim_memory_limit_mb: int = Field(default=2048, ge=256, le=8192)  # per-render memory ceiling (RSS poll); the Windows job uses a looser commit backstop
    manim_docker_cpus: float = Field(default=2.0, gt=0, le=8)  # docker --cpus for the sandbox container
    manim_max_processes: int = Field(default=64, ge=8, le=256)  # Windows job process-count limit (must allow LaTeX's helpers)
    manim_max_workspace_mb: int = Field(default=1024, ge=64, le=4096)  # a render writing more than this to its work dir is stopped
    manim_max_output_mb: int = Field(default=300, ge=16, le=2048)  # a finished video larger than this is rejected
    manim_audit_hook: bool = True  # install the in-sandbox runtime audit hook (defence in depth; kill switch)

    # --- Render (MP4) --------------------------------------------------------
    render_width: int = 1920
    render_height: int = 1080
    render_fps: int = 30
    render_concurrency: int = 3
    render_crf: int = 20
    render_preset: str = "medium"
    render_burn_captions: bool = False
    render_include_intro: bool = True
    render_bgm: bool = True
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # --- Render (MP4): preview parity, output QA, admission, workspace -------
    # (aadhi/compose/video.py, ffmpeg.py, qa.py, workspace.py, preflight.py; aadhi/api/routers/renders.py)
    render_soft_subtitles: bool = False  # default for renders that do not choose: embed the captions as a selectable mov_text track in the MP4 (not with burn-in; MP4 marks its only caption track enabled, so some players show it at once)
    render_color_bt709: bool = True  # convert overlays/stills to BT.709 and tag the MP4 BT.709 (browser colours); false = untagged BT.601 as before
    render_mascot_continuity: bool = True  # mascot loop keeps its phase across scenes, only the scene layer fades in, 0.6 s crossfade on a position change (as the live player)
    render_output_qa: bool = True  # verify every MP4 (frame count, constant frame rate, audio present/audible, black edges) and record the result on the render
    render_max_per_user: int = 2  # active (queued/running) renders per user across all lectures; 0 = unlimited (429 render_busy)
    render_max_queued: int = 50  # active renders on the whole server; 0 = unlimited (429 render_queue_full)
    render_base_url: str = ""  # origin the render worker's browser uses to reach the app (e.g. http://api:8000); empty = BASE_URL
    render_work_dir: str = ""  # resumable render workspaces (segments reused by a retry); empty = <DATA_DIR>/render-work
    render_keep_failed_hours: int = 24  # keep a failed render's workspace this long so a retry can resume it
    render_min_free_gb: float = 2.0  # refuse to start a render when the workspace disk has less free space (0 = no check)
    render_glass_panels: bool = True  # panels keep the live player's translucent glass colours over the mascot (without the blur); false = the original solid surfaces

    # --- Presenter + visual synchronisation (word-anchored moments) ----------
    # (aadhi/compose/sync.py at timeline build; web/js/player/schedule.js plays them in the live player
    # and the render page alike.) Takes effect when a timeline is (re)built.
    sync_word_anchors: bool = True  # formula legend rows, terminal output, side-panel focus pulses and authored highlights follow the spoken word; false = beat-start timing as before
    sync_auto_emphasis: bool = False  # also highlight a visible board item when the narration names its [[keyword]], definition term or table column (at most 2 per beat, never over an authored highlight)

    # --- Presenter: Aadhi's behaviour cues -----------------------------------
    # (web/js/player/mascot-state.js; aadhi/api/timelines.py applies Layout.mascot_cues when a timeline is
    # served.) Takes effect immediately; the live player and the MP4 render show the same bubble.
    mascot_cues: bool = True  # a small bubble beside Aadhi's head: thinking dots in long pauses and the quiz countdown, "?" while he asks a quiz question, a gold check at the reveal; false = no bubble

    # --- Scratch space: temporary work dirs and their housekeeping ----------
    # (aadhi/scratch.py; aadhi/manim/sandbox.py + render.py, aadhi/compose/workspace.py sweeps)
    scratch_dir: str = ""  # private root of the app's temporary work dirs (Manim renders); host sweeps only ever look inside it, never at other folders of the system temp dir; empty = <system temp>/aadhi (aadhi-<uid> on POSIX)

    # --- Lesson quality checks (aadhi/pipeline/quality) ----------------------
    # The deterministic checks (terminology, abbreviations, formula symbols, code, titles, pacing) always run
    # with lint and never call a model. The optional assistant below asks the lecture's AI engine (fast model)
    # once per generated lecture about at most 8 ambiguous term pairs; its answers are notes only
    # (terminology.ambiguous, never repaired) and its usage counts toward the job budget like every LLM call.
    quality_ai_terminology: bool = False  # true = run the AI terminology assistant in the critic stage of generate_lecture

    # --- Media library (aadhi/library.py, aadhi/api/routers/library.py) -----
    # Each teacher's own pictures and clips. Uploads always join the uploader's library; titles, descriptions
    # and keywords are private to that teacher (never written to the shared asset rows).
    library_auto_save_generated: bool = True  # pictures and clips a build generates also join the library of the user who started it (normally the lecture's owner; recorded by the build, a failure there never fails the build)
    library_ai_describe_enabled: bool = False  # "Suggest a description" in the library: the default AI engine (fast model) proposes a title, description and keywords for one item; billed like every LLM call and counted against the daily budget

    # --- Budgets / quotas (USD, estimates from aadhi.usage.pricing) ----------
    max_cost_per_lecture_usd: float = 5.0
    daily_budget_usd_per_user: float = 15.0
    pricing_file: Path | None = None  # JSON overriding the default price table

    # --- Generation defaults -------------------------------------------------
    default_language: str = "en-IN"
    max_source_chars: int = 400_000
    max_source_pages: int = 400

    # --- validators ----------------------------------------------------------
    @field_validator("storage_local_dir", "pricing_file", mode="before")
    @classmethod
    def _blank_path_is_none(cls, v):
        # An empty env var must mean "use the default", not Path("") == the current directory.
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        return v

    @field_validator("data_dir", "web_dir", "branding_dir", mode="before")
    @classmethod
    def _required_path_not_blank(cls, v):
        if isinstance(v, str) and not v.strip():
            raise ValueError("path setting must not be empty")
        return v

    @field_validator("trusted_proxies", "worker_kinds", "llm_model_allowlist", mode="before")
    @classmethod
    def _split_lists(cls, v):
        return _split(v)

    @field_validator("gemini_api_keys", mode="before")
    @classmethod
    def _split_keys(cls, v):
        return _split(v)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _origins(cls, v):
        origins = _split(v)
        for o in origins:
            if o == "*" or not _ORIGIN_RE.match(o):
                raise ValueError(f"CORS_ORIGINS entries must be exact origins like https://app.example.com, got {o!r}")
        return origins

    @field_validator("worker_kinds")
    @classmethod
    def _kinds(cls, v):
        bad = [k for k in v if k not in ALL_JOB_KINDS]
        if bad:
            raise ValueError(f"unknown WORKER_KINDS {bad}")
        return v

    @field_validator("credentials_encryption_key")
    @classmethod
    def _fernet_key(cls, v: SecretStr) -> SecretStr:
        value = v.get_secret_value().strip()
        if not value:
            return v
        try:
            ok = len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32
        except (ValueError, binascii.Error, UnicodeEncodeError):
            ok = False
        if not ok:  # the value itself is never echoed
            raise ValueError("CREDENTIALS_ENCRYPTION_KEY must be a Fernet key (urlsafe base64 of 32 bytes)")
        return v

    # --- Derived -------------------------------------------------------------
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url.get_secret_value()
        if url:
            if url.startswith("postgres://"):
                url = "postgresql+psycopg://" + url[len("postgres://") :]
            elif url.startswith("postgresql://"):
                url = "postgresql+psycopg://" + url[len("postgresql://") :]
            return url
        return f"sqlite:///{(self.data_dir / 'aadhi.db').as_posix()}"

    @property
    def is_sqlite(self) -> bool:
        return self.resolved_database_url.startswith("sqlite")

    @property
    def resolved_storage_dir(self) -> Path:
        return self.storage_local_dir or (self.data_dir / "storage")

    @property
    def resolved_cookie_secure(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.base_url.startswith("https://")

    @property
    def cookie_name(self) -> str:
        # __Host- prefix requires Secure + Path=/ + no Domain (https only).
        return "__Host-aadhi_session" if self.resolved_cookie_secure else "aadhi_session"

    @property
    def all_gemini_keys(self) -> list[str]:
        keys = [k.get_secret_value() for k in [self.gemini_api_key, *self.gemini_api_keys]]
        return list(dict.fromkeys(k for k in keys if k))

    def secret_values(self) -> list[str]:
        """Every configured secret value (for redaction), plus the process-wide registered secrets
        (decrypted API keys saved in the Studio, ``aadhi.security.redaction``)."""
        vals: list[str] = []
        for name, field in type(self).model_fields.items():
            value = getattr(self, name)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, SecretStr):
                    s = item.get_secret_value()
                    if s and len(s) >= 6:
                        vals.append(s)
        db = self.database_url.get_secret_value()
        m = re.search(r"://[^:/@]+:([^@]+)@", db)
        if m:
            vals.append(m.group(1))
        vals.extend(registered_secrets())
        return vals

    def redact(self, text: str) -> str:
        """Replace secret values and key-like substrings in ``text``."""
        if not text:
            return text
        for s in sorted(set(self.secret_values()), key=len, reverse=True):  # a longer secret may contain a shorter one
            text = text.replace(s, "[REDACTED]")
        text = re.sub(r"(?i)(key|token|secret|password)=([^&\s]+)", r"\1=[REDACTED]", text)
        for pattern, repl in _SECRET_PATTERNS:  # media-providers area: secrets nobody configured here
            text = pattern.sub(repl, text)
        return text

    def resolved_jwt_secret(self) -> str:
        """Explicit secret, or (dev/test only) a secret persisted under data_dir."""
        explicit = self.jwt_secret.get_secret_value()
        if explicit:
            return explicit
        if self.is_production:
            raise RuntimeError("JWT_SECRET must be set when APP_ENV=production")
        path = self.data_dir / ".jwt_secret"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        value = secrets.token_urlsafe(48)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(value)
        return value

    def validate_for_runtime(self) -> None:
        """Fail fast on unsafe production configuration."""
        if not self.is_production:
            return
        problems: list[str] = []
        if len(self.jwt_secret.get_secret_value()) < 32:
            problems.append("JWT_SECRET (>= 32 chars) is required")
        if not self.base_url.startswith("https://"):
            problems.append("BASE_URL must be https")
        if any(not o.startswith("https://") for o in self.cors_origins):
            problems.append("CORS_ORIGINS must be https origins")
        if self.manim_sandbox == "subprocess" and self.manim_allow_freeform:
            problems.append("free-form Manim needs MANIM_SANDBOX=docker (or MANIM_ALLOW_FREEFORM=false)")
        if not self.database_url.get_secret_value():
            problems.append("DATABASE_URL must be set explicitly")
        if self.storage_backend == "local":
            storage = self.resolved_storage_dir.resolve()
            if ROOT_DIR in storage.parents or storage == ROOT_DIR:
                problems.append("STORAGE_LOCAL_DIR must be outside the source tree (or use STORAGE_BACKEND=s3)")
        if self.worker_mode == "inline" and not self.is_sqlite:
            problems.append("WORKER_MODE=inline is for single-box dev; run `python -m aadhi.worker` in production")
        fakes = self.fake_providers()  # media-providers area
        if fakes and not self.allow_fake_providers:
            problems.append(f"{', '.join(fakes)} use the offline test stand-ins (fake); set real providers "
                            "(or ALLOW_FAKE_PROVIDERS=true for an offline demo)")
        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    return s
