"""Fixtures for provider tests: isolated settings (never reads ``.env``) and clean provider state."""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from aadhi.config import Settings

GEMINI_KEYS = ("AIzaTESTKEY-0001-abcdef", "AIzaTESTKEY-0002-abcdef", "AIzaTESTKEY-0003-abcdef")


@pytest.fixture(autouse=True)
def _isolate_provider_state() -> Iterator[None]:
    from aadhi.providers.factory import reset_provider_cache
    from aadhi.providers.gemini_common import reset_key_pools
    from aadhi.providers.health import reset_provider_health
    from aadhi.providers.llm.fake import FakeLLM

    FakeLLM.clear_registry()
    reset_provider_cache()
    reset_key_pools()
    reset_provider_health()
    yield
    FakeLLM.clear_registry()
    reset_provider_cache()
    reset_key_pools()
    reset_provider_health()


@pytest.fixture()
def make_settings(tmp_path: pathlib.Path) -> Callable[..., Settings]:
    """Build ``Settings`` from the forced test environment plus overrides (``.env`` is never read)."""

    def make(**overrides: Any) -> Settings:
        overrides.setdefault("data_dir", tmp_path / "data")
        return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]

    return make


@pytest.fixture()
def gemini_settings(make_settings: Callable[..., Settings]) -> Settings:
    return make_settings(gemini_api_key=GEMINI_KEYS[0], gemini_api_keys=",".join(GEMINI_KEYS[1:]),
                         llm_provider="gemini")


async def no_sleep(_seconds: float) -> None:
    """Retry sleep replacement (records nothing, returns immediately)."""
    return None
