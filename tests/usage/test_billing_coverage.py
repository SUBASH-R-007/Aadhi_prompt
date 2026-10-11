"""B0 (DYNAMIC_VIDEO_DESIGN §5.7): the up-front budget check (``_media_billed_to_server``) also counts generated
music, premium voices and the voices' calibration synthesis when a server key would pay for them. Classic
options (no Dynamic voice or music preferences) are judged exactly as before.

The Dynamic preferences (``GenerationOptions.voice`` / ``.music``, §6.3) arrive with their batches; until then
the options are duck-typed here, as the helper reads them with ``getattr``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

from aadhi.api.generation import _media_billed_to_server
from aadhi.config import Settings
from aadhi.pipeline.base import GenerationOptions


def keys(**sources: str | None) -> Any:
    """ResolvedKeys stand-in: provider -> "personal" | "server" | "env" | None."""
    return SimpleNamespace(source=lambda name: sources.get(name or ""))


def options(voice: Any = None, music: Any = None, **kw: Any) -> Any:
    base = GenerationOptions(**kw)
    data = {name: getattr(base, name) for name in type(base).model_fields}
    return SimpleNamespace(**data, voice=voice, music=music)


def free_settings(app_env: Settings) -> Settings:
    """Nothing paid by default: Edge voices, Pollinations images, no AI video."""
    return app_env.model_copy(update={"tts_provider": "edge", "image_provider": "pollinations",
                                      "image_fallback_providers": [], "video_provider": "none"})


def test_classic_options_are_judged_as_before(app_env: Settings) -> None:
    settings = free_settings(app_env)
    mine = keys(gemini="personal", openai="personal")
    assert _media_billed_to_server(settings, mine, GenerationOptions()) is False
    assert _media_billed_to_server(settings, mine, options()) is False  # no Dynamic preferences: same answer
    assert _media_billed_to_server(settings, keys(gemini="server"), GenerationOptions(tts_provider="gemini")) is True
    assert _media_billed_to_server(settings, mine, GenerationOptions(tts_provider="gemini")) is False
    assert _media_billed_to_server(settings, mine, GenerationOptions(tts_provider="elevenlabs")) is True  # server-only
    assert _media_billed_to_server(settings, mine, None) is False


def test_generated_music_counts_on_the_gemini_key(app_env: Settings) -> None:
    settings = free_settings(app_env)
    server, mine = keys(gemini="server"), keys(gemini="personal")
    generated = options(music=SimpleNamespace(source="generated"))
    assert _media_billed_to_server(settings, server, generated) is True  # Lyria on the server's Gemini key
    assert _media_billed_to_server(settings, mine, generated) is False  # ...or on the teacher's own key
    auto_opted_in = options(music=SimpleNamespace(source="auto", ai_opt_in=True))
    assert _media_billed_to_server(settings, server, auto_opted_in) is True
    assert _media_billed_to_server(settings, keys(), auto_opted_in) is True  # no key known: fail closed
    for free in (SimpleNamespace(source="auto", ai_opt_in=False), SimpleNamespace(source="procedural"),
                 SimpleNamespace(source="library"), SimpleNamespace(source="upload"), SimpleNamespace(source="off"),
                 SimpleNamespace()):
        assert _media_billed_to_server(settings, server, options(music=free)) is False, free


def test_premium_voices_and_their_calibration_count(app_env: Settings) -> None:
    settings = free_settings(app_env)
    any_premium = SimpleNamespace(premium_allowed=True, any_provider=True)
    # casting may pick (and calibrate) a paid voice from any provider whose key resolves
    assert _media_billed_to_server(settings, keys(gemini="server"), options(voice=any_premium)) is True
    assert _media_billed_to_server(settings, keys(openai="env"), options(voice=any_premium)) is True
    assert _media_billed_to_server(settings, keys(gemini="personal", openai="personal"),
                                   options(voice=any_premium)) is False
    assert _media_billed_to_server(settings, keys(), options(voice=any_premium)) is False  # no paid voice usable
    with_eleven = settings.model_copy(update={"elevenlabs_api_key": SecretStr("test-only-not-a-key")})
    assert _media_billed_to_server(with_eleven, keys(gemini="personal"), options(voice=any_premium)) is True
    # premium voices from the lecture's own (free) provider only, or premium not allowed: nothing paid
    own_provider = SimpleNamespace(premium_allowed=True, any_provider=False)
    assert _media_billed_to_server(settings, keys(gemini="server"), options(voice=own_provider)) is False
    not_premium = SimpleNamespace(premium_allowed=False, any_provider=True)
    assert _media_billed_to_server(settings, keys(gemini="server"), options(voice=not_premium)) is False
    # the lecture's paid provider still counts with voice preferences present
    assert _media_billed_to_server(settings, keys(gemini="server"),
                                   options(voice=own_provider, tts_provider="gemini")) is True


def test_a_pinned_paid_narrator_counts(app_env: Settings) -> None:
    settings = free_settings(app_env)
    pinned = options(voice=SimpleNamespace(narrator="openai:alloy"))
    assert _media_billed_to_server(settings, keys(openai="server"), pinned) is True
    assert _media_billed_to_server(settings, keys(openai="personal"), pinned) is False
    assert _media_billed_to_server(settings, keys(openai="personal"),
                                   options(voice=SimpleNamespace(narrator="elevenlabs:rachel"))) is True
    assert _media_billed_to_server(settings, keys(openai="server"),
                                   options(voice=SimpleNamespace(narrator="edge:en-IN-NeerjaNeural"))) is False


def test_ai_video_on_gemini_omni_counts_like_veo(app_env: Settings) -> None:
    """VIDEO_PROVIDERS=omni alone (no Veo in the chain) still bills the Gemini key: the check is never skipped."""
    video = GenerationOptions(allow_ai_video=True)
    for chain in (["omni"], ["veo"], ["fake", "omni"]):
        settings = free_settings(app_env).model_copy(update={"video_provider": chain[0],
                                                             "video_fallback_providers": chain[1:]})
        assert _media_billed_to_server(settings, keys(gemini="server"), video) is True, chain
        assert _media_billed_to_server(settings, keys(gemini="personal"), video) is False, chain
        assert _media_billed_to_server(settings, keys(gemini="server"), GenerationOptions()) is False  # no AI video
    fake_only = free_settings(app_env).model_copy(update={"video_provider": "fake", "video_fallback_providers": []})
    assert _media_billed_to_server(fake_only, keys(gemini="server"), video) is False
