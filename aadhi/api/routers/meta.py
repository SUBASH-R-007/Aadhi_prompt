"""``/api/meta``: languages, voices, AI engines and models, features, templates, limits, generation defaults.

``llm.provider`` / ``llm.configured`` / ``llm.models`` describe the default engine (``LLM_PROVIDER``);
``llm.engines`` lists every engine a lecture may pick (``GenerationOptions.llm_provider``) with its
models, whether it is configured and where its key comes from (``key_source``: ``personal`` |
``server`` | ``env`` | null).

Availability is per user: engines, voices and features are evaluated with the current user's resolved
API keys (``aadhi.credentials.resolve_keys``: personal key > server key saved in the Studio > .env),
i.e. exactly what that user's jobs will run with. ``api_keys`` says whether keys can be saved in the
Studio at all (``enabled``) and whether personal keys are allowed (``personal_enabled``).
``library`` holds the media library switches (``library_meta``).

``image.providers`` lists the image providers a lecture may choose (``GenerationOptions.image_provider``)
with whether each is configured for this user and whether it is paid; empty when generated images are off.

``/api/admin/media-providers`` (admins only): the image / video provider chains with each provider's
state, redacted reason, paid flag and cooldown (``aadhi.providers.factory.media_provider_status``).

``/api/admin/manim/sandbox`` (admins only, a few per minute: it starts a probe process): the Manim
sandbox self-check (``aadhi.manim.health.sandbox_health``), booleans and short strings, no host paths.
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Any

from fastapi import APIRouter
from sqlalchemy import func, select

from ... import __version__
from ...config import Settings
from ...credentials import ResolvedKeys, personal_keys_enabled, resolve_keys
from ...models import JobEvent
from ...pipeline.base import SUPPORTED_LANGUAGES, GenerationOptions
from ...security.ratelimit import check_rate_limit
from ...usage.service import user_daily_budget
from ..deps import RATE_LIMITED, AdminUser, AppSettings, CurrentUser, DbSession, is_admin
from ..generation import complete_options

logger = logging.getLogger(__name__)

router = APIRouter(tags=["meta"], dependencies=RATE_LIMITED)

MANIM_CHECK_PER_MINUTE = 6  # each check starts a probe render process


def _tts_providers(settings: Settings) -> list[dict[str, Any]]:
    try:
        from ...providers.factory import available_tts_providers
    except ImportError:
        logger.warning("aadhi.providers.factory is unavailable: /api/meta lists no TTS providers")
        return []
    return list(available_tts_providers(settings))


def _image_providers(settings: Settings) -> list[dict[str, Any]]:
    from ...providers.factory import available_image_providers

    return available_image_providers(settings)


def _llm_configured(settings: Settings) -> bool:
    try:
        from ...providers.factory import llm_configured
    except ImportError:
        if settings.llm_provider == "fake":
            return True
        if settings.llm_provider == "gemini":
            return bool(settings.all_gemini_keys)
        if settings.llm_provider == "anthropic":
            return bool(settings.anthropic_api_key.get_secret_value())
        return bool(settings.openai_api_key.get_secret_value())
    return bool(llm_configured(settings))


def _llm_models(settings: Settings) -> dict[str, str]:
    """Models of the default engine per tier."""
    try:
        from ...providers.factory import llm_models
    except ImportError:
        return {tier: getattr(settings, f"llm_model_{tier}") for tier in ("plan", "script", "critic", "fast")}
    return dict(llm_models(settings))


def _llm_engines(settings: Settings, keys: ResolvedKeys) -> list[dict[str, Any]]:
    """Engines evaluated with the user's resolved keys (``keys.settings``), plus each key's source."""
    try:
        from ...providers.factory import available_llm_engines
    except ImportError:
        logger.warning("aadhi.providers.factory is unavailable: /api/meta lists no AI engines")
        return []
    return [{**engine, "key_source": keys.source(engine["id"])} for engine in available_llm_engines(keys.settings)]


def _manim_templates() -> list[dict[str, Any]]:
    try:
        from ...manim.templates import list_templates
    except ImportError:
        logger.warning("aadhi.manim.templates is unavailable: /api/meta lists no Manim templates")
        return []
    out = []
    for info in list_templates():
        out.append(
            {
                "name": info.name,
                "title": info.title,
                "description": info.description,
                "steps_hint": getattr(info, "steps_hint", ""),
                "params_schema": getattr(info, "params_schema", {}),
                "example_params": getattr(info, "example_params", {}),
            }
        )
    return out


@lru_cache(maxsize=8)
def _ffmpeg_available(path: str) -> bool:
    from ...compose.capabilities import resolve_tool

    return resolve_tool(path) is not None  # the renderer's own lookup (an extensionless .exe path too)


def _render_available(settings: Settings) -> bool:
    """Inline workers render on this host: the full check of ``POST /render`` (ffmpeg with libx264/aac,
    ffprobe, Chromium), so the button is off exactly when a render would be refused with 503. External
    workers render elsewhere: only an advisory ffmpeg check of this host (the API never refuses on it)."""
    if settings.worker_mode != "inline":
        return _ffmpeg_available(settings.ffmpeg_path)
    from ...compose.capabilities import render_capabilities

    return render_capabilities(settings, browser=True).ok


def features(settings: Settings) -> dict[str, Any]:
    """Feature switches derived from the configuration (generated images / AI video: any provider of
    the chain, i.e. ``IMAGE_PROVIDER`` / ``VIDEO_PROVIDER`` or a configured ``*_FALLBACK_PROVIDERS`` backup)."""
    from ...providers.factory import media_configured

    manim = settings.manim_sandbox != "disabled"
    return {
        "ai_video": media_configured("video", settings),
        "manim": manim,
        "manim_freeform": manim
        and settings.manim_allow_freeform
        and (settings.manim_sandbox == "docker" or not settings.is_production),
        "generated_images": media_configured("image", settings),
        "gifs": bool(settings.giphy_api_key.get_secret_value()),
        "render": _render_available(settings),
        "storage": settings.storage_backend,
    }


def api_keys_meta(settings: Settings) -> dict[str, bool]:
    """Whether API keys can be saved in the Studio (server keys) and whether personal keys are allowed."""
    return {"enabled": bool(settings.stored_api_keys_enabled), "personal_enabled": personal_keys_enabled(settings)}


def library_meta(settings: Settings, effective: Settings) -> dict[str, bool]:
    """Media library switches: generated media join the library (``auto_save_generated``) and "Suggest a
    description" is offered (``ai_describe_enabled``: switched on and an AI engine configured for this user)."""
    return {
        "auto_save_generated": bool(settings.library_auto_save_generated),
        "ai_describe_enabled": bool(settings.library_ai_describe_enabled) and _llm_configured(effective),
    }


def generation_defaults(settings: Settings) -> dict[str, Any]:
    """Default GenerationOptions (configured default language when supported), every field included (also the
    ones stored options leave out at their default, e.g. ``prefer_library_visuals``)."""
    language = settings.default_language if settings.default_language in SUPPORTED_LANGUAGES else "en-IN"
    return complete_options(GenerationOptions(language=language))


@router.get("/api/meta")
def meta(user: CurrentUser, settings: AppSettings, db: DbSession) -> dict[str, Any]:
    """Everything the Studio needs to build its forms (availability for this user's API keys)."""
    keys = resolve_keys(db, settings, user.id)
    effective = keys.settings
    return {
        "version": __version__,
        "languages": [{"code": code, "label": label} for code, label in SUPPORTED_LANGUAGES.items()],
        "tts": {"default_provider": settings.tts_provider, "providers": _tts_providers(effective)},
        "image": {"default_provider": settings.image_provider, "providers": _image_providers(effective)},
        "llm": {
            "provider": settings.llm_provider,
            "configured": _llm_configured(effective),
            "models": _llm_models(settings),
            "engines": _llm_engines(settings, keys),
            "override_allowlist": list(settings.llm_model_allowlist) if is_admin(user) else [],
        },
        "api_keys": api_keys_meta(settings),
        "library": library_meta(settings, effective),
        "features": features(effective),
        "manim_templates": _manim_templates(),
        "limits": {
            "upload_max_mb": settings.upload_max_mb,
            "max_source_chars": settings.max_source_chars,  # characters of a source that generation reads
            "password_min_length": settings.password_min_length,
            "max_json_body_mb": settings.max_json_body_mb,
            "max_cost_per_lecture_usd": settings.max_cost_per_lecture_usd,
            "daily_budget_usd": user_daily_budget(user, settings),
        },
        "generation_defaults": generation_defaults(settings),
    }


# How far back (in job events) the admin panel looks for cooldowns the job workers recorded: a bounded
# primary-key range, so the read never scans the whole table.
WORKER_COOLDOWN_EVENTS = 20_000


def worker_cooldowns(db: Any) -> dict[tuple[str, str], tuple[float, str]]:
    """Cooldowns the job workers (other processes) recorded in their job events and that are still running:
    ``(kind, provider) -> (seconds left, category)``, server-scope failures only (``assets._chain_failed``).
    A later success in a worker is not seen here: the cooldown shows until its window ends."""
    newest = db.execute(select(func.max(JobEvent.id))).scalar()
    if not newest:
        return {}
    rows = db.execute(
        select(JobEvent.created_at, JobEvent.data)
        .where(JobEvent.id > newest - WORKER_COOLDOWN_EVENTS, JobEvent.level.in_(("warning", "info")),
               JobEvent.message.like("% provider % failed (%"))
        .order_by(JobEvent.id.desc())
    ).all()
    now = dt.datetime.now(dt.timezone.utc)
    out: dict[tuple[str, str], tuple[float, str]] = {}
    for created_at, data in rows:
        data = data if isinstance(data, dict) else {}
        try:
            seconds = int(data.get("cooldown_seconds") or 0)
        except (TypeError, ValueError):
            continue
        kind, name = str(data.get("kind") or ""), str(data.get("provider") or "")
        if data.get("scope") != "server" or seconds <= 0 or not kind or not name or created_at is None:
            continue
        if created_at.tzinfo is None:  # SQLite returns naive UTC timestamps
            created_at = created_at.replace(tzinfo=dt.timezone.utc)
        left = (created_at + dt.timedelta(seconds=seconds) - now).total_seconds()
        if left > 0 and left > out.get((kind, name), (0.0, ""))[0]:
            out[(kind, name)] = (left, str(data.get("category") or "failed"))
    return out


@router.get("/api/admin/media-providers")
def media_providers(admin: AdminUser, settings: AppSettings, db: DbSession) -> dict[str, Any]:
    """Image / video provider chains for admins: each provider's state (``available`` |
    ``not_configured`` | ``cooling``), a redacted reason, whether it is paid, its order and cooldown.

    Server level only (environment keys and the server keys saved in the Studio; personal keys play
    no part) and configuration only: no provider is built and nothing is sent to a provider. Cooldowns
    come from this process with inline workers, else from the job events the workers wrote recently
    (``cooldowns_source``).
    """
    from ...providers.factory import media_provider_status

    server = resolve_keys(db, settings, None).settings
    inline = settings.worker_mode == "inline"
    status = media_provider_status(server, None if inline else worker_cooldowns(db))
    return {**status, "cooldown_seconds": settings.media_provider_cooldown_seconds,
            "auth_cooldown_seconds": settings.media_provider_auth_cooldown_seconds,
            "cooldowns_source": "this process" if inline else "recent worker job events"}


@router.get("/api/admin/manim/sandbox")
def manim_sandbox(admin: AdminUser, settings: AppSettings) -> dict[str, Any]:
    """The Manim sandbox self-check for admins (same as ``aadhi.cli manim-check``): availability,
    isolation, Manim version, LaTeX, and whether a probe inside the sandbox could reach the network or
    saw secrets in its environment. Never host paths. 429 ``rate_limited`` past a few checks a minute."""
    from ...manim.health import sandbox_health

    check_rate_limit("manim_check", f"user:{admin.id}", per_minute=MANIM_CHECK_PER_MINUTE)
    return sandbox_health(settings).as_dict()
