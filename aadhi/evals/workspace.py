"""Isolated runtime for an eval run: its own data dir, SQLite database and local storage.

Evals must never touch the developer database or storage, and ``--provider fake`` must never reach
a paid API even when ``.env`` holds real keys. ``isolated_workspace`` therefore overrides the
relevant environment variables (environment beats ``.env`` in pydantic-settings), resets every
settings/engine/storage cache, creates the schema, and restores the previous environment and caches
on exit.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings

logger = logging.getLogger(__name__)

ProviderMode = Literal["fake", "configured"]

# Every key/secret a provider could use: blanked in fake mode.
SECRET_ENV_VARS = (
    "GEMINI_API_KEY",
    "GEMINI_API_KEYS",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "ELEVENLABS_API_KEY",
    "GIPHY_API_KEY",
    "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY",
)
FAKE_PROVIDER_ENV = {
    "LLM_PROVIDER": "fake",
    "TTS_PROVIDER": "fake",
    "IMAGE_PROVIDER": "fake",
    "VIDEO_PROVIDER": "fake",
    "MANIM_VISUAL_QA": "false",
}


@dataclass
class Workspace:
    """Handles for one isolated eval runtime."""

    root: Path
    settings: Settings
    session_factory: sessionmaker[Session]
    provider: ProviderMode

    def asset_store(self):  # -> AssetStore (imported lazily: storage factory reads settings)
        """A fresh ``AssetStore`` bound to the workspace storage + database."""
        from ..storage import get_storage
        from ..storage.assets import AssetStore

        return AssetStore(get_storage(), self.session_factory)


def workspace_env(root: Path, provider: ProviderMode, *, render_manim: bool = False) -> dict[str, str]:
    """Environment overrides for a workspace rooted at ``root``."""
    env = {
        "DATA_DIR": str(root / "data"),
        "DATABASE_URL": f"sqlite:///{(root / 'eval.db').as_posix()}",
        "STORAGE_BACKEND": "local",
        "STORAGE_LOCAL_DIR": str(root / "storage"),
        "WORKER_MODE": "external",
        # Evals never serve HTTP; production-only checks (JWT secret, https) are irrelevant here.
        "APP_ENV": "test" if os.environ.get("APP_ENV") == "test" else "development",
    }
    if provider == "fake":
        env.update(FAKE_PROVIDER_ENV)
        env.update({name: "" for name in SECRET_ENV_VARS})
    if not render_manim:
        env["MANIM_SANDBOX"] = "disabled"
    return env


def reset_caches() -> None:
    """Drop cached settings, engines and storage so the next access re-reads the environment."""
    from .. import config, db
    from ..storage import reset_storage_cache

    try:
        db.reset_engine_cache()  # disposes a created engine, clears engine/sessionmaker/settings
    finally:
        config.get_settings.cache_clear()
        reset_storage_cache()


@contextmanager
def patched_environ(overrides: Mapping[str, str]) -> Iterator[None]:
    """Temporarily set environment variables, restoring the previous values afterwards."""
    saved = {k: os.environ.get(k) for k in overrides}
    os.environ.update(overrides)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def isolated_workspace(
    root: Path | None = None,
    *,
    provider: ProviderMode = "fake",
    render_manim: bool = False,
    keep: bool = False,
    extra_env: Mapping[str, str] | None = None,
) -> Iterator[Workspace]:
    """Run the body against a throw-away data dir + SQLite DB (+ fake providers in fake mode).

    ``root=None`` uses a temporary directory that is deleted afterwards unless ``keep`` is set.
    """
    from .. import db, models  # noqa: F401  (models registers the tables on Base.metadata)
    from ..config import get_settings

    created = root is None
    base = Path(tempfile.mkdtemp(prefix="aadhi-eval-")) if root is None else Path(root)
    base.mkdir(parents=True, exist_ok=True)
    env = workspace_env(base.resolve(), provider, render_manim=render_manim)
    env.update(extra_env or {})
    with patched_environ(env):
        reset_caches()
        try:
            settings = get_settings()
            engine = db.get_engine()
            db.Base.metadata.create_all(engine)
            yield Workspace(root=base, settings=settings, session_factory=db.get_sessionmaker(), provider=provider)
        finally:
            reset_caches()
    reset_caches()  # pick the caller's environment back up
    if created and not keep:
        shutil.rmtree(base, ignore_errors=True)
