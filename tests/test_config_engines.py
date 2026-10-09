"""Settings for the selectable AI engines (``.env`` is never read here)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aadhi.config import Settings

KEY = "sk-ant-test-0123456789abcdef"


def make(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_anthropic_engine_settings() -> None:
    s = make(llm_provider="anthropic", anthropic_api_key=KEY, anthropic_effort="max", anthropic_max_tokens=2048)
    assert s.llm_provider == "anthropic" and s.anthropic_api_key.get_secret_value() == KEY
    assert KEY not in repr(s) and KEY in s.secret_values()
    assert s.redact(f"401 from Anthropic for key {KEY}") == "401 from Anthropic for key [REDACTED]"
    with pytest.raises(ValidationError):
        make(llm_provider="claude")
    with pytest.raises(ValidationError):
        make(anthropic_max_tokens=512)


def test_claude_timeout_is_separate_from_the_llm_timeout() -> None:
    s = make()
    assert (s.llm_timeout_seconds, s.anthropic_timeout_seconds) == (600, 1800)
    assert make(anthropic_timeout_seconds=60).anthropic_timeout_seconds == 60
    with pytest.raises(ValidationError):
        make(anthropic_timeout_seconds=59)


def test_production_checks_do_not_depend_on_the_engine(tmp_path) -> None:
    """Keys are checked per lecture (the API refuses an unconfigured engine), not at startup."""
    base = {"app_env": "production", "jwt_secret": "x" * 40, "base_url": "https://aadhi.example.com",
            "database_url": "postgresql://u:p@db/aadhi", "manim_sandbox": "docker", "worker_mode": "external",
            "storage_local_dir": tmp_path / "storage",
            "allow_fake_providers": True}  # TTS/image/video stay the forced offline stand-ins
    for engine in ("gemini", "openai", "anthropic"):
        make(llm_provider=engine, **base).validate_for_runtime()  # no key needed to start
