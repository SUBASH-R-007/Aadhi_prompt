"""Test isolation: runs before any ``aadhi`` import.

* Forces fake providers and blanks every API key so tests can never call paid APIs, even
  though a developer ``.env`` with real keys exists in the repo root (env vars override .env).
  API keys saved in the Studio are switched off (``STORED_API_KEYS_ENABLED=false``) so existing
  tests see exactly the environment keys; credential tests enable them on their settings object
  (``settings.stored_api_keys_enabled = True``).
* Each test gets a fresh data dir + SQLite database via the ``app_env`` fixture.
* Temporary files never reach the real system temp dir: ``TEMP`` / ``TMP`` / ``TMPDIR`` (inherited by
  subprocesses) and ``tempfile.tempdir`` point into this session's own directory, and so does
  ``SCRATCH_DIR`` (the only place host sweeps look). A test (or an inline worker it starts) that sweeps
  "old" work dirs therefore never touches a developer's real folders. pytest's ``tmp_path`` keeps its usual
  place and retention (``PYTEST_DEBUG_TEMPROOT``); the session directory is removed after a passing run.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import tempfile

_REAL_TEMP = tempfile.gettempdir()
_SESSION_TMP = pathlib.Path(tempfile.mkdtemp(prefix="aadhi-test-"))
SESSION_TEMP = _SESSION_TMP / "tmp"  # the temp dir of this test session (tests compare against it)
SESSION_TEMP.mkdir()
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", _REAL_TEMP)  # tmp_path stays where pytest keeps it
for _name in ("TEMP", "TMP", "TMPDIR"):
    os.environ[_name] = str(SESSION_TEMP)
tempfile.tempdir = str(SESSION_TEMP)

_FORCED_ENV = {
    "APP_ENV": "test",
    "DATA_DIR": str(_SESSION_TMP / "data"),
    "DATABASE_URL": f"sqlite:///{(_SESSION_TMP / 'session.db').as_posix()}",
    "BASE_URL": "http://testserver",
    "LLM_PROVIDER": "fake",
    "TTS_PROVIDER": "fake",
    "IMAGE_PROVIDER": "fake",
    "VIDEO_PROVIDER": "fake",
    "IMAGE_FALLBACK_PROVIDERS": "",
    "VIDEO_FALLBACK_PROVIDERS": "",
    # Production-mode tests opt in explicitly (Settings(allow_fake_providers=True)); a developer .env
    # must not switch the production fake-provider guard off for the whole suite.
    "ALLOW_FAKE_PROVIDERS": "false",
    "GEMINI_API_KEY": "",
    "GEMINI_API_KEYS": "",
    "OPENAI_API_KEY": "",
    "ANTHROPIC_API_KEY": "",
    "STORED_API_KEYS_ENABLED": "false",
    "USER_API_KEYS_ENABLED": "true",
    "CREDENTIALS_ENCRYPTION_KEY": "",
    "ELEVENLABS_API_KEY": "",
    "GIPHY_API_KEY": "",
    "S3_ACCESS_KEY_ID": "",
    "S3_SECRET_ACCESS_KEY": "",
    "STORAGE_BACKEND": "local",
    "JWT_SECRET": "test-jwt-secret-" + "x" * 40,
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD": "admin-test-password-123",
    "WORKER_MODE": "external",
    "MANIM_SANDBOX": "subprocess",
    "MANIM_VISUAL_QA": "false",
    "CORS_ORIGINS": "",
    # A developer .env pointing renders at a real workspace (or another host) must never reach tests: the
    # workspace sweep of a test render would delete a real render's files.
    "RENDER_WORK_DIR": "",
    "RENDER_BASE_URL": "",
    # Host sweeps (Manim orphans, legacy render temp dirs) look only here: never at a developer's real folders.
    "SCRATCH_DIR": str(_SESSION_TMP / "scratch"),
    # Defaults the timeline / lint tests assert (a developer .env must not change what a test builds); the
    # optional AI terminology assistant stays off unless a test enables it on its settings.
    "SYNC_WORD_ANCHORS": "true",
    "SYNC_AUTO_EMPHASIS": "false",
    "MASCOT_CUES": "true",
    "QUALITY_AI_TERMINOLOGY": "false",
    # Media library defaults (tests that need another value set it on their settings).
    "LIBRARY_AUTO_SAVE_GENERATED": "true",
    "LIBRARY_AI_DESCRIBE_ENABLED": "false",
}
os.environ.update(_FORCED_ENV)

import pytest  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: renders real manim/ffmpeg/playwright output (deselect with -m 'not slow')")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Remove this session's own directory after a passing run (kept after a failure, for debugging)."""
    if exitstatus == 0:
        shutil.rmtree(_SESSION_TMP, ignore_errors=True)


@pytest.fixture()
def app_env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Fresh settings + data dir + SQLite DB (all tables created) for one test.

    Yields the Settings object. Caches (settings, engine, storage) are reset before and after.
    """
    from aadhi import config, db, models  # noqa: F401  (models registers tables)
    from aadhi.credentials import reset_key_cache
    from aadhi.security.redaction import clear_registered_secrets
    from aadhi.storage import reset_storage_cache

    reset_key_cache()
    clear_registered_secrets()
    data = tmp_path / "data"
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    config.get_settings.cache_clear()
    db.reset_engine_cache()
    reset_storage_cache()
    settings = config.get_settings()
    db.Base.metadata.create_all(db.get_engine())
    try:
        yield settings
    finally:
        db.get_engine().dispose()
        config.get_settings.cache_clear()
        db.reset_engine_cache()
        reset_storage_cache()
        reset_key_cache()
        clear_registered_secrets()


@pytest.fixture()
def db_session(app_env):
    from aadhi.db import get_sessionmaker

    s = get_sessionmaker()()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def asset_store(app_env):
    from aadhi.db import get_sessionmaker
    from aadhi.storage import get_storage
    from aadhi.storage.assets import AssetStore

    return AssetStore(get_storage(), get_sessionmaker())


@pytest.fixture()
def job_ctx(app_env, asset_store):
    """In-memory JobContext (see tests/helpers.py) for unit-testing stages without a worker."""
    from tests.helpers import FakeJobContext

    return FakeJobContext(settings=app_env, assets=asset_store)
