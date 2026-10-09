"""AI engines in the provider factory: model routing, configuration, per-engine providers, the meta list.

Never calls a real API: providers are only constructed (no request is made), and the Anthropic adapter
is replaced by a stub module where the wiring alone is under test.
"""

from __future__ import annotations

import sys
import types

import pytest

from aadhi.providers import factory
from aadhi.providers.base import ProviderNotConfigured

KEYS = {
    "gemini_api_key": "AIzaTEST-123456789",
    "openai_api_key": "sk-test-123456789",
    "anthropic_api_key": "sk-ant-test-123456789",
}


@pytest.mark.parametrize(
    ("model", "engine"),
    [
        ("gemini-2.5-pro", "gemini"),
        ("gemini-2.5-flash-preview-tts", "gemini"),
        ("gpt-4.1", "openai"),
        ("GPT-5", "openai"),
        ("o3-mini", "openai"),
        ("o4", "openai"),
        ("chatgpt-4o-latest", "openai"),
        ("ft:gpt-4.1-mini:org::abc", "openai"),
        ("claude-opus-5-5", "anthropic"),
        ("claude-sonnet-4-5", "anthropic"),
        ("llama-3", None),
        ("omni", None),  # "o" must be followed by a digit
        ("", None),
    ],
)
def test_engine_for_model(model: str, engine: str | None) -> None:
    assert factory.engine_for_model(model) == engine


def test_engine_constants() -> None:
    assert factory.LLM_ENGINES == ("gemini", "openai", "anthropic")
    assert factory.LLM_TIERS == ("plan", "script", "critic", "fast")
    assert factory.LLM_ENGINE_LABELS == {"gemini": "Google Gemini", "openai": "OpenAI", "anthropic": "Anthropic Claude",
                                         "fake": "Offline test engine"}


def test_llm_configured_per_engine(make_settings) -> None:
    bare = make_settings(llm_provider="gemini", app_env="development")
    assert not any(factory.llm_configured(bare, e) for e in factory.LLM_ENGINES)
    assert not factory.llm_configured(bare) and not factory.llm_configured(bare, "fake")
    assert not factory.llm_configured(bare, "klingon")
    for field, engine in (("gemini_api_key", "gemini"), ("openai_api_key", "openai"), ("anthropic_api_key", "anthropic")):
        s = make_settings(llm_provider="gemini", app_env="development", **{field: KEYS[field]})
        assert [e for e in factory.LLM_ENGINES if factory.llm_configured(s, e)] == [engine]
    pool = make_settings(llm_provider="gemini", gemini_api_keys="AIzaPOOL-1-123456,AIzaPOOL-2-123456")
    assert factory.llm_configured(pool, "gemini")
    default = make_settings(llm_provider="anthropic", anthropic_api_key=KEYS["anthropic_api_key"])
    assert factory.llm_configured(default)  # engine None = LLM_PROVIDER


def test_fake_engine_only_in_tests_or_when_it_is_the_default(make_settings) -> None:
    assert factory.llm_configured(make_settings(app_env="test", llm_provider="gemini"), "fake")
    assert factory.llm_configured(make_settings(app_env="development", llm_provider="fake"), "fake")
    dev = make_settings(app_env="development", llm_provider="gemini", **KEYS)
    assert not factory.llm_configured(dev, "fake")
    with pytest.raises(ProviderNotConfigured):
        factory.get_llm(dev, "fake")
    assert factory.get_llm(make_settings(app_env="development", llm_provider="fake")).name == "fake"


def test_llm_models_defaults_per_engine(make_settings) -> None:
    s = make_settings(llm_provider="gemini")
    assert factory.llm_models(s, "gemini") == {"plan": "gemini-2.5-pro", "script": "gemini-2.5-flash",
                                               "critic": "gemini-2.5-flash", "fast": "gemini-2.5-flash"}
    assert factory.llm_models(s) == factory.llm_models(s, "gemini")
    assert factory.llm_models(s, "openai") == {"plan": "gpt-4.1", "script": "gpt-4.1-mini", "critic": "gpt-4.1-mini",
                                               "fast": "gpt-4.1-mini"}
    assert factory.llm_models(s, "anthropic") == dict.fromkeys(factory.LLM_TIERS, "claude-opus-5-5")
    custom = make_settings(openai_model_fast="gpt-5-nano", anthropic_model_plan="claude-plan-x",
                           anthropic_model_critic="claude-critic-x")
    assert factory.llm_models(custom, "openai")["fast"] == "gpt-5-nano"
    claude = factory.llm_models(custom, "anthropic")
    assert claude["plan"] == "claude-plan-x" and claude["critic"] == "claude-critic-x"
    with pytest.raises(ProviderNotConfigured):
        factory.llm_models(s, "klingon")


def test_llm_model_tier_settings_apply_to_the_engine_of_their_model(make_settings) -> None:
    """A pre-engine ``.env`` (LLM_MODEL_PLAN=gpt-5 for OpenAI) keeps working; other engines are unaffected."""
    s = make_settings(llm_provider="openai", llm_model_plan="gpt-5", llm_model_script="claude-sonnet-4-5",
                      llm_model_critic="gemini-2.5-pro", llm_model_fast="my-custom-model")
    openai = factory.llm_models(s, "openai")
    assert openai == {"plan": "gpt-5", "script": "gpt-4.1-mini", "critic": "gpt-4.1-mini", "fast": "gpt-4.1-mini"}
    gemini = factory.llm_models(s, "gemini")
    assert gemini == {"plan": "gemini-2.5-pro", "script": "gemini-2.5-flash", "critic": "gemini-2.5-pro",
                      "fast": "gemini-2.5-flash"}
    claude = factory.llm_models(s, "anthropic")
    assert claude["script"] == "claude-sonnet-4-5" and claude["plan"] == "claude-opus-5-5"
    fake = factory.llm_models(s, "fake")  # the offline engine reports the tier settings unchanged
    assert fake == {"plan": "gpt-5", "script": "claude-sonnet-4-5", "critic": "gemini-2.5-pro", "fast": "my-custom-model"}


@pytest.fixture()
def stub_anthropic(monkeypatch):
    """``aadhi.providers.llm.anthropic`` replaced by a stub (the factory's lazy import resolves to it)."""
    built: list[object] = []

    class AnthropicLLM:
        name = "anthropic"

        def __init__(self, settings) -> None:
            if not settings.anthropic_api_key.get_secret_value():
                raise ProviderNotConfigured("anthropic: ANTHROPIC_API_KEY is not configured", provider="anthropic")
            self.settings = settings
            built.append(self)

    module = types.ModuleType("aadhi.providers.llm.anthropic")
    module.AnthropicLLM = AnthropicLLM  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "aadhi.providers.llm.anthropic", module)
    return built


def test_get_llm_builds_the_requested_engine_cached_per_engine(make_settings, stub_anthropic) -> None:
    s = make_settings(llm_provider="gemini", **KEYS)
    gemini, openai, claude = factory.get_llm(s, "gemini"), factory.get_llm(s, "openai"), factory.get_llm(s, "anthropic")
    assert (gemini.name, openai.name, claude.name) == ("gemini", "openai", "anthropic")
    assert factory.get_llm(s) is gemini  # engine None = LLM_PROVIDER, same cache entry
    assert factory.get_llm(s, "anthropic") is claude and stub_anthropic == [claude]
    assert factory.get_llm(s, "fake").name == "fake"  # tests may always use the offline engine
    with pytest.raises(ProviderNotConfigured):
        factory.get_llm(s, "klingon")


def test_get_llm_refuses_engines_without_keys(make_settings, stub_anthropic) -> None:
    s = make_settings(llm_provider="fake")
    for engine in factory.LLM_ENGINES:
        with pytest.raises(ProviderNotConfigured):
            factory.get_llm(s, engine)
    assert factory.get_llm(s).name == "fake"


def test_real_anthropic_adapter_needs_its_key(make_settings) -> None:
    pytest.importorskip("aadhi.providers.llm.anthropic")
    with pytest.raises(ProviderNotConfigured, match="ANTHROPIC_API_KEY"):
        factory.get_llm(make_settings(llm_provider="fake"), "anthropic")
    configured = make_settings(llm_provider="fake", anthropic_api_key=KEYS["anthropic_api_key"])
    assert factory.get_llm(configured, "anthropic").name == "anthropic"  # constructed only: no request is made


def test_available_llm_engines(make_settings) -> None:
    s = make_settings(app_env="test", llm_provider="fake", anthropic_api_key=KEYS["anthropic_api_key"],
                      llm_model_plan="gpt-5")
    engines = factory.available_llm_engines(s)
    assert [e["id"] for e in engines] == ["fake", "gemini", "openai", "anthropic"]
    by_id = {e["id"]: e for e in engines}
    assert all(set(e) == {"id", "label", "configured", "models"} for e in engines)
    assert by_id["anthropic"]["label"] == "Anthropic Claude" and by_id["fake"]["label"] == "Offline test engine"
    assert {k: e["configured"] for k, e in by_id.items()} == {"fake": True, "gemini": False, "openai": False,
                                                              "anthropic": True}
    assert by_id["openai"]["models"]["plan"] == "gpt-5" and by_id["gemini"]["models"]["plan"] == "gemini-2.5-pro"
    assert by_id["anthropic"]["models"] == factory.llm_models(s, "anthropic")
    dev = make_settings(app_env="development", llm_provider="gemini", gemini_api_key=KEYS["gemini_api_key"])
    listed = factory.available_llm_engines(dev)
    assert [e["id"] for e in listed] == ["gemini", "openai", "anthropic"]
    assert [e["configured"] for e in listed] == [True, False, False]
    demo = make_settings(app_env="development", llm_provider="fake")
    assert factory.available_llm_engines(demo)[0]["id"] == "fake"
