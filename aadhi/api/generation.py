"""Helpers for endpoints that create versions or enqueue jobs.

* GenerationOptions sanitising: the lecture's AI engine (``llm_provider``, None = the server default)
  must be configured (422 at ``options.llm_provider``); model overrides only for admins, only from
  the allow-list and only for models of that engine. A chosen image provider (``image_provider``) must be
  configured too, and generated images must be on at the server (422 at ``options.image_provider``).
* LLM jobs on an existing version (scene regeneration, translation, plan approval, retries) need
  the version's engine to be configured (409 ``engine_not_configured``).
* "Configured" means for the requester: their resolved API keys (``request_keys``: personal key >
  server key saved in the Studio > environment), exactly what their jobs will run with.
* Generation rate limit (per user per hour) + daily budget pre-check (429 ``budget``); the pre-check
  is skipped only when the AI engine AND every paid voice / image / video provider the job may use
  (the lecture's choice, ``IMAGE_PROVIDER`` / ``VIDEO_PROVIDER`` and their configured backups) run on
  the requester's personal keys (that spend is not budgeted). Free providers never count.
* Version allocation through the atomic ``Project.next_version_number`` counter.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import Settings
from ..credentials import ResolvedKeys, personal_keys_enabled, resolve_keys
from ..db import atomic_add
from ..jobs.queue import enqueue
from ..models import ACTIVE_JOB_STATUSES, VERSION_MUTATING_KINDS, Job, Project, ProjectVersion, User
from ..pipeline.base import GenerationOptions
from ..providers.factory import (
    LLM_ENGINE_LABELS,
    MEDIA_LABELS,
    engine_for_model,
    llm_configured,
    media_chain,
    media_unavailable_reason,
)
from ..schemas.jsonsafe import json_safe
from ..security.ratelimit import check_rate_limit
from ..usage.service import check_budget
from .errors import ApiException
from .storable import ensure_storable
from .util import utcnow

MODEL_OVERRIDE_FIELDS = ("llm_model_plan", "llm_model_script")
GENERATION_BUCKET = "generation"
# Minimum cost assumed for any generation when pre-checking the daily budget.
MIN_GENERATION_COST_USD = 0.01


def options_validation_error(exc: ValidationError, loc_prefix: str = "options") -> ApiException:
    """422 envelope for invalid GenerationOptions."""
    errors = []
    for err in exc.errors():
        errors.append(
            {"loc": [loc_prefix, *err.get("loc", ())], "msg": str(err.get("msg", "")), "type": str(err.get("type", ""))}
        )
    return ApiException(422, "validation", errors)


def parse_options(raw: str | dict[str, Any] | None, *, defaults: dict[str, Any] | None = None) -> GenerationOptions:
    """Parse options (JSON string or dict) on top of ``defaults``; 422 on invalid input.

    The given options must be storable JSON (no NaN/Infinity, no lone surrogates: 422 at ``options``).
    """
    import json

    data: dict[str, Any] = dict(defaults or {})
    if isinstance(raw, str):
        raw = raw.strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError as exc:
                raise ApiException(
                    422,
                    "validation",
                    [{"loc": ["options"], "msg": f"options is not valid JSON: {exc.msg}", "type": "json_invalid"}],
                ) from None
            if not isinstance(parsed, dict):
                raise ApiException(
                    422,
                    "validation",
                    [{"loc": ["options"], "msg": "options must be a JSON object", "type": "dict_type"}],
                )
            ensure_storable(parsed, ("options",))
            data.update(parsed)
    elif isinstance(raw, dict):
        ensure_storable(raw, ("options",))
        data.update(raw)
    try:
        return GenerationOptions.model_validate(data)
    except ValidationError as exc:
        raise options_validation_error(exc) from None


def engine_label(engine: str) -> str:
    """Display name of an AI engine (``Google Gemini`` ...)."""
    return LLM_ENGINE_LABELS.get(engine, engine)


def request_keys(db: Session, user: User, settings: Settings) -> ResolvedKeys:
    """The requester's effective API keys (``aadhi.credentials.resolve_keys``), as their jobs will use them."""
    return resolve_keys(db, settings, user.id)


def _key_settings(settings: Settings, keys: ResolvedKeys | None) -> Settings:
    return keys.settings if keys is not None else settings


def _own_key_hint(settings: Settings) -> str:
    return " You can add your own API key under API keys." if personal_keys_enabled(settings) else ""


def ensure_engine_configured(
    options: GenerationOptions, settings: Settings, keys: ResolvedKeys | None = None
) -> None:
    """422 at ``options.llm_provider`` when the lecture's engine (chosen, else the default) cannot run
    with the requester's keys (``keys``; None = the server's own keys)."""
    engine = options.llm_provider or settings.llm_provider
    if not llm_configured(_key_settings(settings, keys), engine):
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["options", "llm_provider"],
                    "msg": f"AI engine '{engine_label(engine)}' is not configured on this server."
                    + _own_key_hint(settings),
                    "type": "engine_not_configured",
                }
            ],
        )


def ensure_image_provider_configured(
    options: GenerationOptions, settings: Settings, keys: ResolvedKeys | None = None
) -> None:
    """422 at ``options.image_provider`` when the lecture's chosen image provider cannot be used: generated
    images are off on the server (``IMAGE_PROVIDER=none``) or the provider has no key for the requester."""
    name = options.image_provider
    if name is None:
        return
    label = MEDIA_LABELS.get(name, name)
    if settings.image_provider == "none":
        msg = "Generated images are disabled on this server."
    elif media_unavailable_reason("image", name, _key_settings(settings, keys)) is not None:
        msg = f"Image provider '{label}' is not configured on this server." + _own_key_hint(settings)
    else:
        return
    raise ApiException(
        422,
        "validation",
        [{"loc": ["options", "image_provider"], "msg": msg, "type": "provider_not_configured"}],
    )


def sanitize_options(
    options: GenerationOptions, user: User, settings: Settings, keys: ResolvedKeys | None = None
) -> GenerationOptions:
    """Refuse an AI engine or image provider the requester has no key for (422), then strip model
    overrides unless the user is an admin, the model is allow-listed and it belongs to the lecture's
    engine (any model with the fake engine)."""
    ensure_engine_configured(options, settings, keys)
    ensure_image_provider_configured(options, settings, keys)
    allow = set(settings.llm_model_allowlist or [])
    engine = options.llm_provider or settings.llm_provider
    updates: dict[str, Any] = {}
    for field in MODEL_OVERRIDE_FIELDS:
        value = getattr(options, field)
        if value is None:
            continue
        other_engine = engine != "fake" and engine_for_model(value) != engine
        if user.role != "admin" or value not in allow or other_engine:
            updates[field] = None
    return options.model_copy(update=updates) if updates else options


def version_options(version: ProjectVersion, project: Project | None) -> GenerationOptions:
    """The options a version's jobs use: its generation options, else the project's, else the defaults.

    Mirrors ``aadhi.pipeline.orchestrator.version_options``.
    """
    for raw in ((version.generation_meta or {}).get("options"), project.settings if project is not None else None):
        if raw:
            try:
                return GenerationOptions.model_validate(raw)
            except ValidationError:
                continue
    return GenerationOptions()


def version_engine(version: ProjectVersion, project: Project | None, settings: Settings) -> str:
    """The AI engine a version's LLM jobs use: its generation options, else the project's, else the default."""
    return version_options(version, project).llm_provider or settings.llm_provider


def ensure_engine_available(engine: str, settings: Settings, keys: ResolvedKeys | None = None) -> None:
    """409 ``engine_not_configured`` when ``engine`` cannot run with the requester's keys."""
    if not llm_configured(_key_settings(settings, keys), engine):
        raise ApiException(
            409,
            "engine_not_configured",
            f"This lecture's AI engine '{engine_label(engine)}' is not configured on this server; "
            "regenerate the lecture with another engine." + _own_key_hint(settings),
            engine=engine,
        )


def ensure_version_engine(
    version: ProjectVersion, project: Project | None, settings: Settings, keys: ResolvedKeys | None = None
) -> None:
    """409 ``engine_not_configured`` when the version's AI engine cannot run with the requester's keys."""
    ensure_engine_available(version_engine(version, project, settings), settings, keys)


def complete_options(options: GenerationOptions) -> dict[str, Any]:
    """``options`` as JSON with every field, including the ones stored dumps leave out at their default
    (``prefer_library_visuals``, so stored options and checkpoint digests stay unchanged): the full set the
    Studio's options form starts from."""
    data = options.model_dump(mode="json")
    for name in type(options).model_fields:
        if name not in data:
            data[name] = getattr(options, name)
    return data


def project_default_options(project: Project, settings: Settings) -> dict[str, Any]:
    """Options stored on the project (validated; invalid legacy values are dropped; text stored before
    lone surrogates were refused is sanitised so it can be served and stored again)."""
    stored = json_safe(dict(project.settings or {}))
    try:
        return complete_options(GenerationOptions.model_validate(stored))
    except ValidationError:
        return complete_options(GenerationOptions(language=project.language or settings.default_language))


# Paid voice providers -> the API key that pays for them (None: always a server key).
_PAID_TTS_KEYS: dict[str, str | None] = {"gemini": "gemini", "openai": "openai", "elevenlabs": None}


def _media_billed_to_server(settings: Settings, keys: ResolvedKeys, options: GenerationOptions | None) -> bool:
    """True when the job's voices, generated images or AI videos would be paid with a server (Studio or
    .env) key. ``options`` None = the defaults; free providers (edge, pollinations, fake, none) never count."""
    opts = options or GenerationOptions()
    payers: list[str | None] = []
    tts = opts.tts_provider or settings.tts_provider
    if tts in _PAID_TTS_KEYS:
        payers.append(_PAID_TTS_KEYS[tts])
    images = media_chain("image", settings)
    if images and opts.image_provider:  # the lecture's choice goes first (when images are on at all)
        images = [opts.image_provider, *images]
    if opts.allow_generated_images and "gemini" in images:  # preferred or a backup: it may bill the key
        payers.append("gemini")
    if opts.allow_ai_video and "veo" in media_chain("video", settings):  # Veo runs on the Gemini key
        payers.append("gemini")
    return any(p is None or keys.source(p) != "personal" for p in payers)


def ensure_budget(
    db: Session,
    user: User,
    settings: Settings,
    *,
    engine: str | None = None,
    keys: ResolvedKeys | None = None,
    options: GenerationOptions | None = None,
) -> None:
    """Raise BudgetExceeded (429 ``budget``) when the user's daily budget is exhausted.

    Skipped only when the AI ``engine`` (None = ``options``' engine, else the default) AND every paid
    voice / image / video provider the job uses (``options``, None = the defaults) run on the user's
    personal keys (``keys``): that spend is billed to the user and the daily budget does not count it.
    A job that would still bill server voices or images is refused up front, before the user pays for
    its script with their own key.
    """
    if keys is not None:
        engine = engine or (options.llm_provider if options is not None else None) or settings.llm_provider
        if keys.source(engine) == "personal" and not _media_billed_to_server(settings, keys, options):
            return
    check_budget(
        db,
        user_id=user.id,
        job_cost_so_far=0.0,
        extra_usd=MIN_GENERATION_COST_USD,
        settings=settings,
    )


def ensure_generation_allowed(
    db: Session,
    user: User,
    settings: Settings,
    *,
    rate_limited: bool = True,
    engine: str | None = None,
    keys: ResolvedKeys | None = None,
    options: GenerationOptions | None = None,
) -> None:
    """Budget pre-check (see ``ensure_budget``), then the per-user hourly generation rate limit (429).

    Spends a rate-limit token: use it only where nothing can refuse the request afterwards (a new
    version cannot conflict with a running job). Where ``enqueue`` may still raise ``JobInProgress``
    (409), call ``ensure_budget`` first and ``charge_generation_rate`` after enqueueing instead.
    """
    ensure_budget(db, user, settings, engine=engine, keys=keys, options=options)
    if rate_limited:
        _spend_generation_token(user, settings)


def _spend_generation_token(user: User, settings: Settings) -> None:
    if settings.generation_rate_limit_per_hour > 0:
        check_rate_limit(GENERATION_BUCKET, f"user:{user.id}", per_hour=settings.generation_rate_limit_per_hour)


def charge_generation_rate(db: Session, user: User, settings: Settings) -> None:
    """Spend one per-user hourly generation token after the job was enqueued (flushed, not committed).

    Requests refused by the enqueue (409 ``job_in_progress``) therefore never use up the quota. On
    429 the session is rolled back, discarding the flushed job, before the error propagates.
    """
    try:
        _spend_generation_token(user, settings)
    except Exception:
        db.rollback()
        raise


def allocate_version_number(db: Session, project_id: int) -> int:
    """Next version number via the atomic counter (safe under concurrency)."""
    new_value = atomic_add(db, Project, project_id, "next_version_number", 1)
    if new_value is None:  # pragma: no cover - project vanished inside the transaction
        raise ApiException(404, "not_found", "Project not found")
    return int(new_value) - 1


def create_version(
    db: Session,
    project: Project,
    *,
    status: str,
    language: str,
    created_by: int | None,
    label: str = "",
    source_version_id: int | None = None,
) -> ProjectVersion:
    """Insert a new ProjectVersion with an atomically allocated number (flushed, not committed)."""
    number = allocate_version_number(db, project.id)
    version = ProjectVersion(
        project_id=project.id,
        number=number,
        label=label[:255],
        status=status,
        language=language,
        source_version_id=source_version_id,
        created_by=created_by,
        revision=1,
        issues=[],
        generation_meta={},
        issue_counts={"error": 0, "warning": 0, "info": 0},
    )
    db.add(version)
    db.flush()
    return version


def touch_project(project: Project) -> None:
    """Bump ``updated_at`` so the project sorts first in lists."""
    project.updated_at = utcnow()


def enqueue_job(
    db: Session,
    kind: str,
    *,
    user: User,
    project_id: int | None,
    version_id: int | None,
    payload: dict[str, Any],
) -> Job:
    """Enqueue (flush only; the caller commits). JobInProgress propagates as 409."""
    return enqueue(db, kind, user_id=user.id, project_id=project_id, version_id=version_id, payload=payload)


def commit_and_notify(db: Session, settings: Settings) -> None:
    """Commit enqueued jobs, then wake inline workers."""
    db.commit()
    notify_workers(settings)


def notify_workers(settings: Settings) -> None:
    """Wake inline workers after a commit that enqueued jobs (optional latency optimisation)."""
    if settings.worker_mode != "inline":
        return
    try:
        from ..jobs.inline import notify_inline_workers
    except ImportError:
        return
    notify_inline_workers()


def active_mutating_job(db: Session, version_id: int) -> Job | None:
    """The active version-mutating job of a version, if any (renders do not count)."""
    stmt = (
        select(Job)
        .where(
            Job.version_id == version_id,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.kind.in_(VERSION_MUTATING_KINDS),
        )
        .order_by(Job.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def ensure_not_busy(db: Session, version_id: int) -> None:
    """409 ``version_busy`` while a mutating job is active on the version."""
    job = active_mutating_job(db, version_id)
    if job is not None:
        raise ApiException(409, "version_busy", "A job is currently modifying this version.", job_id=job.id)
