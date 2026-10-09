"""tts.voices catalogue and the provider factory."""

from __future__ import annotations

import pytest

from aadhi.pipeline.base import SUPPORTED_LANGUAGES
from aadhi.providers import factory
from aadhi.providers.base import (
    GifProvider,
    ImageProvider,
    LLMProvider,
    ProviderNotConfigured,
    TTSProvider,
    VideoProvider,
)
from aadhi.providers.tts.voices import LANGUAGES, default_voice, is_known_voice, voices_for

# --- voices -----------------------------------------------------------------------------------


def test_languages_match_pipeline_contract() -> None:
    assert set(LANGUAGES) == set(SUPPORTED_LANGUAGES)


@pytest.mark.parametrize(
    ("language", "voice"),
    [
        ("en-IN", "en-IN-NeerjaNeural"),
        ("en-US", "en-US-AndrewMultilingualNeural"),
        ("en-GB", "en-GB-RyanNeural"),
        ("ta-IN", "ta-IN-PallaviNeural"),
        ("hi-IN", "hi-IN-SwaraNeural"),
        ("te-IN", "te-IN-ShrutiNeural"),
        ("kn-IN", "kn-IN-SapnaNeural"),
        ("ml-IN", "ml-IN-SobhanaNeural"),
        ("ta", "ta-IN-PallaviNeural"),
        ("en_in", "en-IN-NeerjaNeural"),
        ("en-AU", "en-US-AndrewMultilingualNeural"),
        ("fr-FR", "en-US-AndrewMultilingualNeural"),
    ],
)
def test_edge_defaults(language: str, voice: str) -> None:
    assert default_voice("edge", language) == voice


def test_every_language_has_male_and_female_edge_voices() -> None:
    for lang in LANGUAGES:
        voices = voices_for("edge", lang)
        assert {v["gender"] for v in voices} >= {"Male", "Female"}, lang
        assert all(v["language"] == lang for v in voices)
        assert default_voice("edge", lang) in {v["id"] for v in voices}
    assert {"en-IN-PrabhatNeural", "ta-IN-ValluvarNeural", "hi-IN-MadhurNeural", "te-IN-MohanNeural",
            "kn-IN-GaganNeural", "ml-IN-MidhunNeural"} <= {v["id"] for v in voices_for("edge")}


@pytest.mark.parametrize("provider", ["openai", "gemini", "elevenlabs", "fake"])
def test_multilingual_providers(provider: str) -> None:
    all_voices = voices_for(provider)
    assert all_voices and all(set(v) == {"id", "label", "language", "gender"} for v in all_voices)
    assert {v["language"] for v in all_voices} == {"multi"}
    assert {v["language"] for v in voices_for(provider, "ta-IN")} == {"ta-IN"}
    assert is_known_voice(provider, default_voice(provider, "hi-IN"))


def test_unknown_provider_raises() -> None:
    with pytest.raises(KeyError):
        voices_for("nope")
    with pytest.raises(KeyError):
        default_voice("nope", "en-IN")
    assert not is_known_voice("nope", "x")


# --- factory --------------------------------------------------------------------------------------


def test_test_environment_defaults(app_env) -> None:
    llm = factory.get_llm(app_env)
    assert llm.name == "fake" and isinstance(llm, LLMProvider)
    assert factory.get_llm(app_env) is llm  # cached per settings identity
    assert factory.get_llm() is factory.get_llm()  # default settings path
    tts = factory.get_tts(settings=app_env)
    assert tts.name == "fake" and isinstance(tts, TTSProvider)
    assert factory.get_tts("edge", app_env).name == "edge"
    image = factory.get_image(app_env)
    assert image is not None and image.name == "fake" and isinstance(image, ImageProvider)
    video = factory.get_video(app_env)
    assert video is not None and video.name == "fake" and isinstance(video, VideoProvider)
    gif = factory.get_gif(app_env)
    assert gif is not None and gif.name == "fake" and isinstance(gif, GifProvider)
    assert factory.llm_configured(app_env)


def test_cache_keyed_by_settings_identity(make_settings) -> None:
    a, b = make_settings(), make_settings()
    assert factory.get_llm(a) is factory.get_llm(a)
    assert factory.get_llm(a) is not factory.get_llm(b)
    factory.reset_provider_cache()
    assert factory.get_llm(a) is not None


def test_real_providers_need_keys(make_settings) -> None:
    s = make_settings(llm_provider="gemini", tts_provider="edge", image_provider="none", video_provider="none")
    assert not factory.llm_configured(s)
    with pytest.raises(ProviderNotConfigured):
        factory.get_llm(s)
    for name in ("gemini", "openai", "elevenlabs"):
        with pytest.raises(ProviderNotConfigured):
            factory.get_tts(name, s)
    with pytest.raises(ProviderNotConfigured):
        factory.get_tts("klingon", s)
    assert factory.get_image(s) is None and factory.get_video(s) is None


def test_configured_real_providers(make_settings) -> None:
    s = make_settings(
        llm_provider="openai", openai_api_key="sk-test-123456789", gemini_api_key="AIzaTEST-123456789",
        elevenlabs_api_key="el-test-123456789", giphy_api_key="giphy-test-123456789",
        image_provider="pollinations", video_provider="veo", tts_provider="edge",
    )
    assert factory.llm_configured(s)
    assert factory.get_llm(s).name == "openai"
    assert factory.get_tts("gemini", s).name == "gemini"
    assert factory.get_tts("openai", s).name == "openai"
    assert factory.get_tts("elevenlabs", s).name == "elevenlabs"
    assert factory.get_image(s).name == "pollinations"
    assert factory.get_video(s).name == "veo"
    assert factory.get_gif(s).name == "giphy"
    s2 = make_settings(llm_provider="gemini", gemini_api_key="AIzaTEST-123456789", image_provider="gemini")
    assert factory.get_llm(s2).name == "gemini" and factory.get_image(s2).name == "gemini"


def test_fake_tts_only_in_tests_or_when_selected(make_settings) -> None:
    dev = make_settings(app_env="development", tts_provider="edge")
    assert "fake" not in [p["id"] for p in factory.available_tts_providers(dev)]
    with pytest.raises(ProviderNotConfigured):
        factory.get_tts("fake", dev)
    assert factory.get_gif(dev) is None
    chosen = make_settings(app_env="development", tts_provider="fake")
    assert factory.get_tts(settings=chosen).name == "fake"


def test_available_tts_providers_shape(make_settings) -> None:
    s = make_settings(elevenlabs_api_key="el-test-123456789")
    providers = {p["id"]: p for p in factory.available_tts_providers(s)}
    assert list(providers) == ["edge", "gemini", "openai", "elevenlabs", "fake"]
    assert providers["edge"]["configured"] and providers["edge"]["word_timings"]
    assert providers["edge"]["label"] == "Microsoft Edge (free)"
    assert not providers["gemini"]["configured"] and not providers["openai"]["configured"]
    assert providers["elevenlabs"]["configured"] and providers["elevenlabs"]["word_timings"]
    assert providers["fake"]["configured"]
    voice = providers["edge"]["voices"][0]
    assert set(voice) == {"id", "label", "language", "gender"}
