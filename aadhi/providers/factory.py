"""Provider factory: builds (and caches) provider instances from ``Settings``.

Instances are cached per *settings object identity* (tests create fresh settings per test, so they
get fresh providers). Providers that need a missing API key raise ``ProviderNotConfigured``.
``none`` image/video providers (and GIFs without a GIPHY key outside tests) return ``None``.

LLM engines: ``LLM_PROVIDER`` is the default engine; a lecture may pick any other configured engine
(``GenerationOptions.llm_provider``), so the LLM helpers take an optional ``engine``. ``llm_models``
resolves the model of every tier for an engine; the offline ``fake`` engine is only available in
tests or when it is the default engine.

Media chains: ``media_chain`` lists ``IMAGE_PROVIDER`` / ``VIDEO_PROVIDER`` followed by the configured
``*_FALLBACK_PROVIDERS`` (used by ``aadhi.pipeline.assets``); ``get_image`` / ``get_video`` take an
optional provider ``name``. ``media_provider_status`` describes the chains for the admin status panel
from configuration and in-process cooldowns only (nothing is built, nothing is sent).
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, TypeVar

from ..config import Settings, get_settings
from .base import GifProvider, ImageProvider, LLMProvider, ProviderNotConfigured, TTSProvider, VideoProvider
from .tts.voices import voices_for

P = TypeVar("P")

TTS_PROVIDERS = ("edge", "gemini", "openai", "elevenlabs", "fake")
TTS_LABELS = {
    "edge": "Microsoft Edge (free)",
    "gemini": "Google Gemini TTS",
    "openai": "OpenAI TTS",
    "elevenlabs": "ElevenLabs",
    "fake": "Offline test voice",
}
TTS_WORD_TIMINGS = {"edge": True, "gemini": False, "openai": False, "elevenlabs": True, "fake": True}

_CACHE_MAX = 64
_cache: OrderedDict[tuple[int, str, str], tuple[Settings, Any]] = OrderedDict()
_cache_lock = threading.Lock()


def reset_provider_cache() -> None:
    """Drop every cached provider instance (tests / settings reload)."""
    with _cache_lock:
        _cache.clear()


def _cached(settings: Settings, kind: str, name: str, build: Callable[[], P]) -> P:
    key = (id(settings), kind, name)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] is settings:
            _cache.move_to_end(key)
            return hit[1]
    obj = build()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] is settings:
            return hit[1]
        _cache[key] = (settings, obj)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return obj


def _fake_allowed(settings: Settings, configured: str) -> bool:
    return settings.app_env == "test" or configured == "fake"


# --- LLM -------------------------------------------------------------------------------------

LLM_ENGINES = ("gemini", "openai", "anthropic")  # user-selectable engines
LLM_ENGINE_LABELS = {
    "gemini": "Google Gemini",
    "openai": "OpenAI",
    "anthropic": "Anthropic Claude",
    "fake": "Offline test engine",
}
LLM_TIERS = ("plan", "script", "critic", "fast")
# Gemini models when LLM_MODEL_<TIER> names another engine's model (the Settings defaults).
GEMINI_DEFAULT_MODELS = {
    "plan": "gemini-2.5-pro",
    "script": "gemini-2.5-flash",
    "critic": "gemini-2.5-flash",
    "fast": "gemini-2.5-flash",
}
_OPENAI_MODEL_RE = re.compile(r"^(gpt|o\d|chatgpt|ft:gpt)", re.I)


def engine_for_model(model: str) -> str | None:
    """The engine a model id belongs to (``gemini`` | ``openai`` | ``anthropic``), None when unknown."""
    model = (model or "").strip()
    if model.startswith("gemini"):
        return "gemini"
    if _OPENAI_MODEL_RE.match(model):
        return "openai"
    if model.startswith("claude"):
        return "anthropic"
    return None


def llm_configured(settings: Settings, engine: str | None = None) -> bool:
    """Whether LLM ``engine`` (default ``LLM_PROVIDER``) can make calls (key present, or fake allowed)."""
    name = engine or settings.llm_provider
    if name == "fake":
        return _fake_allowed(settings, settings.llm_provider)
    if name == "gemini":
        return bool(settings.all_gemini_keys)
    if name == "openai":
        return bool(settings.openai_api_key.get_secret_value())
    if name == "anthropic":
        return bool(settings.anthropic_api_key.get_secret_value())
    return False


def llm_models(settings: Settings, engine: str | None = None) -> dict[str, str]:
    """``{tier: model}`` for ``engine`` (default ``LLM_PROVIDER``).

    ``LLM_MODEL_<TIER>`` applies to the engine its model belongs to (a ``gpt-*`` value keeps
    configuring OpenAI, as before engines were selectable); otherwise Gemini uses its defaults and
    OpenAI / Anthropic their ``OPENAI_MODEL_<TIER>`` / ``ANTHROPIC_MODEL_<TIER>``. The fake engine
    reports ``LLM_MODEL_<TIER>`` unchanged.
    """
    name = engine or settings.llm_provider
    if name not in (*LLM_ENGINES, "fake"):
        raise ProviderNotConfigured(f"unknown LLM provider {name!r}", provider=str(name))
    out: dict[str, str] = {}
    for tier in LLM_TIERS:
        configured = getattr(settings, f"llm_model_{tier}")
        if name == "fake" or engine_for_model(configured) == name:
            out[tier] = configured
        elif name == "gemini":
            out[tier] = GEMINI_DEFAULT_MODELS[tier]
        else:
            out[tier] = getattr(settings, f"{name}_model_{tier}")
    return out


def get_llm(settings: Settings | None = None, engine: str | None = None) -> LLMProvider:
    """LLM provider for ``engine`` (default ``LLM_PROVIDER``: ``gemini`` | ``openai`` | ``anthropic`` | ``fake``).

    The fake engine is refused outside tests unless it is the default engine.
    """
    settings = settings or get_settings()
    name = engine or settings.llm_provider
    if name == "fake" and not _fake_allowed(settings, settings.llm_provider):
        raise ProviderNotConfigured("fake: the offline test engine is not enabled", provider="fake")

    def build() -> LLMProvider:
        if name == "fake":
            from .llm.fake import FakeLLM

            return FakeLLM(settings)
        if name == "gemini":
            from .llm.gemini import GeminiLLM

            return GeminiLLM(settings)
        if name == "openai":
            from .llm.openai import OpenAILLM

            return OpenAILLM(settings)
        if name == "anthropic":
            from .llm.anthropic import AnthropicLLM

            return AnthropicLLM(settings)
        raise ProviderNotConfigured(f"unknown LLM provider {name!r}", provider=str(name))

    return _cached(settings, "llm", name, build)


def available_llm_engines(settings: Settings) -> list[dict[str, Any]]:
    """``[{id, label, configured, models}]`` for ``GET /api/meta`` (the AI engine dropdown).

    ``fake`` is listed first, only in tests or when ``LLM_PROVIDER=fake``.
    """
    names = list(LLM_ENGINES)
    if _fake_allowed(settings, settings.llm_provider):
        names.insert(0, "fake")
    return [
        {
            "id": name,
            "label": LLM_ENGINE_LABELS[name],
            "configured": llm_configured(settings, name),
            "models": llm_models(settings, name),
        }
        for name in names
    ]


# --- TTS -------------------------------------------------------------------------------------


def tts_configured(name: str, settings: Settings) -> bool:
    """Whether TTS provider ``name`` can be used with these settings."""
    if name == "edge":
        return True
    if name == "gemini":
        return bool(settings.all_gemini_keys)
    if name == "openai":
        return bool(settings.openai_api_key.get_secret_value())
    if name == "elevenlabs":
        return bool(settings.elevenlabs_api_key.get_secret_value())
    if name == "fake":
        return _fake_allowed(settings, settings.tts_provider)
    return False


def get_tts(name: str | None = None, settings: Settings | None = None) -> TTSProvider:
    """TTS provider ``name`` (default ``TTS_PROVIDER``)."""
    settings = settings or get_settings()
    name = (name or settings.tts_provider or "edge").lower()
    if name not in TTS_PROVIDERS:
        raise ProviderNotConfigured(f"unknown TTS provider {name!r}", provider=name)
    if not tts_configured(name, settings):
        raise ProviderNotConfigured(f"{name}: TTS provider is not configured", provider=name)

    def build() -> TTSProvider:
        if name == "edge":
            from .tts.edge import EdgeTTS

            return EdgeTTS(settings)
        if name == "gemini":
            from .tts.gemini import GeminiTTS

            return GeminiTTS(settings)
        if name == "openai":
            from .tts.openai import OpenAITTS

            return OpenAITTS(settings)
        if name == "elevenlabs":
            from .tts.elevenlabs import ElevenLabsTTS

            return ElevenLabsTTS(settings)
        from .tts.fake import FakeTTS

        return FakeTTS(settings)

    return _cached(settings, "tts", name, build)


def available_tts_providers(settings: Settings) -> list[dict[str, Any]]:
    """``[{id, label, configured, word_timings, voices}]`` for ``GET /api/meta``.

    Edge is always configured; the others need their API key. ``fake`` is listed only in tests or
    when ``TTS_PROVIDER=fake``.
    """
    out: list[dict[str, Any]] = []
    for name in TTS_PROVIDERS:
        if name == "fake" and not _fake_allowed(settings, settings.tts_provider):
            continue
        out.append(
            {
                "id": name,
                "label": TTS_LABELS[name],
                "configured": tts_configured(name, settings),
                "word_timings": TTS_WORD_TIMINGS[name],
                "voices": voices_for(name),
            }
        )
    return out


# --- images / video / gifs ---------------------------------------------------------------------

IMAGE_PROVIDERS = ("pollinations", "gemini", "fake")
VIDEO_PROVIDERS = ("veo", "fake")
MEDIA_LABELS = {
    "pollinations": "Pollinations",
    "gemini": "Google Gemini / Imagen",
    "veo": "Google Veo",
    "fake": "Offline test stand-in",
}


def media_chain(kind: str, settings: Settings) -> list[str]:
    """Provider names for ``kind`` (``image`` | ``video``) in preference order: ``IMAGE_PROVIDER`` /
    ``VIDEO_PROVIDER`` first, then ``*_FALLBACK_PROVIDERS``. Empty when the medium is ``none``."""
    primary = settings.image_provider if kind == "image" else settings.video_provider
    if primary == "none":
        return []
    backups = settings.image_fallback_providers if kind == "image" else settings.video_fallback_providers
    return list(dict.fromkeys([primary, *backups]))


# Providers a lecture may choose for its generated images (GenerationOptions.image_provider).
CHOOSABLE_IMAGE_PROVIDERS = ("gemini", "pollinations")


def available_image_providers(settings: Settings) -> list[dict[str, Any]]:
    """``[{id, label, configured, paid}]`` a lecture may choose for its generated images (``GET /api/meta``);
    empty when generated images are off on the server (``IMAGE_PROVIDER=none``). Configuration only."""
    if settings.image_provider == "none":
        return []
    return [
        {
            "id": name,
            "label": MEDIA_LABELS[name],
            "configured": media_unavailable_reason("image", name, settings) is None,
            "paid": bool(getattr(media_class("image", name), "paid", False)),
        }
        for name in CHOOSABLE_IMAGE_PROVIDERS
    ]


def media_class(kind: str, name: str) -> type | None:
    """The adapter class of a media provider (imported lazily, never instantiated here)."""
    if kind == "image":
        if name == "fake":
            from .image.fake import FakeImage

            return FakeImage
        if name == "pollinations":
            from .image.pollinations import PollinationsImage

            return PollinationsImage
        if name == "gemini":
            from .image.gemini import GeminiImage

            return GeminiImage
    elif kind == "video":
        if name == "fake":
            from .video.fake import FakeVideo

            return FakeVideo
        if name == "veo":
            from .video.veo import VeoVideo

            return VeoVideo
    return None


def media_unavailable_reason(kind: str, name: str, settings: Settings) -> str | None:
    """Why media provider ``name`` cannot be used with ``settings`` (None = usable). Configuration only:
    no provider is built and nothing is sent anywhere."""
    if media_class(kind, name) is None:
        return f"unknown {kind} provider {name!r}"
    if name in ("gemini", "veo") and not settings.all_gemini_keys:
        return "no Gemini API key is configured"
    return None


def get_image(settings: Settings | None = None, name: str | None = None) -> ImageProvider | None:
    """Image provider ``name`` (default ``IMAGE_PROVIDER``; ``None`` when ``none``)."""
    settings = settings or get_settings()
    name = name or settings.image_provider
    if name == "none":
        return None

    def build() -> ImageProvider:
        if name == "fake":
            from .image.fake import FakeImage

            return FakeImage(settings)
        if name == "pollinations":
            from .image.pollinations import PollinationsImage

            return PollinationsImage(settings)
        if name == "gemini":
            from .image.gemini import GeminiImage

            return GeminiImage(settings)
        raise ProviderNotConfigured(f"unknown image provider {name!r}", provider=str(name))

    return _cached(settings, "image", name, build)


def get_video(settings: Settings | None = None, name: str | None = None) -> VideoProvider | None:
    """Video provider ``name`` (default ``VIDEO_PROVIDER``; ``None`` when ``none``)."""
    settings = settings or get_settings()
    name = name or settings.video_provider
    if name == "none":
        return None

    def build() -> VideoProvider:
        if name == "fake":
            from .video.fake import FakeVideo

            return FakeVideo(settings)
        if name == "veo":
            from .video.veo import VeoVideo

            return VeoVideo(settings)
        raise ProviderNotConfigured(f"unknown video provider {name!r}", provider=str(name))

    return _cached(settings, "video", name, build)


def media_configured(kind: str, settings: Settings) -> bool:
    """Whether at least one provider of the ``kind`` chain can be used (``/api/meta`` features)."""
    return any(media_unavailable_reason(kind, name, settings) is None for name in media_chain(kind, settings))


def media_provider_status(settings: Settings,
                          worker_cooldowns: dict[tuple[str, str], tuple[float, str]] | None = None) -> dict[str, Any]:
    """Server-level state of the image and video provider chains for the admin status panel.

    Configuration and cooldowns only: no provider is built, nothing is sent to a provider, and personal
    API keys play no part (``settings`` are the server's). Reasons are redacted. Cooldowns are this
    process's (inline workers), else ``worker_cooldowns``: ``(kind, provider) -> (seconds left, category)``
    as recorded by the job workers (``api.routers.meta.media_providers`` reads them from job events).
    """
    from .health import Cooling, provider_health

    health = provider_health()
    out: dict[str, Any] = {}
    for kind in ("image", "video"):
        configured = settings.image_provider if kind == "image" else settings.video_provider
        chain = media_chain(kind, settings)
        items: list[dict[str, Any]] = []
        for order, name in enumerate(chain):
            cls = media_class(kind, name)
            reason = media_unavailable_reason(kind, name, settings)
            cooling = health.cooling(name, "server") if reason is None else None
            if reason is None and cooling is None and (kind, name) in (worker_cooldowns or {}):
                cooling = Cooling(*(worker_cooldowns or {})[(kind, name)])
            if reason is not None:
                state = "not_configured"
            elif cooling is not None:
                state = "cooling"
                reason = (
                    "moved behind the others after a payment or account refusal"
                    if cooling.category == "unavailable"
                    else f"moved behind the others after a recent failure ({cooling.category.replace('_', ' ')})"
                )
            else:
                state = "available"
            model = {"gemini": settings.image_model, "veo": settings.veo_model}.get(name, "")
            items.append({
                "name": name,
                "label": MEDIA_LABELS.get(name, name),
                "role": "preferred" if order == 0 else "backup",
                "order": order,
                "state": state,
                "reason": settings.redact(reason or ""),
                "paid": bool(getattr(cls, "paid", False)),
                "model": model,
                "cooldown_seconds_left": int(round(cooling.seconds_left)) if cooling is not None else 0,
            })
        out[kind] = {
            "enabled": bool(chain),
            "configured": configured,
            "preferred": chain[0] if chain else None,
            "usable": any(item["state"] != "not_configured" for item in items),
            "providers": items,
        }
    return out


def get_gif(settings: Settings | None = None) -> GifProvider | None:
    """GIPHY when ``GIPHY_API_KEY`` is set, the offline fake in tests, else ``None``."""
    settings = settings or get_settings()
    if settings.giphy_api_key.get_secret_value():
        from .gif.giphy import GiphyGif

        return _cached(settings, "gif", "giphy", lambda: GiphyGif(settings))
    if settings.app_env == "test":
        from .gif.fake import FakeGif

        return _cached(settings, "gif", "fake", lambda: FakeGif(settings))
    return None
