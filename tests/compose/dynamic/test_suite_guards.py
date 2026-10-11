"""Guards of the B1 suites themselves (DYNAMIC_VIDEO_DESIGN §10.2 gate 2, §10.3 B1 tests).

* In the Docker ``test`` image (``AADHI_CONTAINER_TESTS=1``) a missing tool (ffmpeg, Playwright, its Chromium, the
  stage bundle) fails a test instead of skipping it: the blocking CI job must not pass green by skipping the
  determinism, parity and engine suites. On a developer machine such a test is skipped, as before.
* ``tests/conftest.py`` pins the motion engine's settings to their defaults, so a developer ``.env`` cannot change what
  the tests build, render or assert.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from aadhi.config import Settings
from tests import conftest as root_conftest
from tests.determinism import conftest as determinism_conftest

from . import helpers


@pytest.mark.parametrize(("value", "inside"), [
    ("1", True), ("true", True), ("yes", True), (" 1 ", True),
    ("", False), ("0", False), ("false", False), ("No", False), (None, False),
])
def test_the_test_image_is_recognised(monkeypatch: pytest.MonkeyPatch, value: str | None, inside: bool) -> None:
    if value is None:
        monkeypatch.delenv(helpers.CONTAINER_TESTS_ENV, raising=False)
    else:
        monkeypatch.setenv(helpers.CONTAINER_TESTS_ENV, value)
    assert helpers.container_tests() is inside


def _outcome(check: Callable[[], object]) -> tuple[str, str]:
    """``("skip" | "fail" | "ok", message)`` of a tool check."""
    try:
        check()
    except pytest.skip.Exception as exc:
        return "skip", str(exc)
    except pytest.fail.Exception as exc:
        return "fail", str(exc)
    return "ok", ""


@pytest.mark.parametrize("in_image", [False, True])
def test_a_missing_tool_skips_here_and_fails_in_the_test_image(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                               in_image: bool) -> None:
    if in_image:
        monkeypatch.setenv(helpers.CONTAINER_TESTS_ENV, "1")
    else:
        monkeypatch.delenv(helpers.CONTAINER_TESTS_ENV, raising=False)
    expected = "fail" if in_image else "skip"
    checks: dict[str, Callable[[], object]] = {"ffmpeg": helpers.tools, "playwright": helpers.need_chromium}
    monkeypatch.setattr(helpers, "resolve_tool", lambda path: None)
    monkeypatch.setattr(helpers, "_playwright_installed", lambda: False)
    monkeypatch.setattr(determinism_conftest, "STAGE_FILES", (tmp_path / "render-dynamic.html",))
    results = {name: _outcome(check) for name, check in checks.items()}
    results["stage bundle"] = _outcome(determinism_conftest.need_real_stage)
    monkeypatch.setattr(helpers, "_playwright_installed", lambda: True)
    monkeypatch.setattr(helpers, "_chromium", lambda: False)
    results["Chromium"] = _outcome(helpers.need_chromium)
    for name, (outcome, message) in results.items():
        assert outcome == expected, (name, outcome, message)
        assert name in message, (name, message)
        assert ("AADHI_CONTAINER_TESTS=1" in message) is in_image, message
    monkeypatch.setattr(helpers, "_chromium", lambda: True)
    assert _outcome(helpers.need_chromium) == ("ok", "")  # everything there: the test runs


MOTION_SETTINGS = {
    "DYNAMIC_ENABLED": "dynamic_enabled",
    "RENDER_MOTION_WORKERS": "render_motion_workers",
    "RENDER_CPU_SLOTS": "render_cpu_slots",
    "RENDER_SLOT_DIR": "render_slot_dir",
    "MOTION_X264_PRESET": "motion_x264_preset",
    "MOTION_X264_CRF": "motion_x264_crf",
}


def test_a_developer_env_cannot_change_the_motion_settings(tmp_path: Path) -> None:
    """Environment variables win over a ``.env`` file: the suite pins the motion settings to their defaults (as it
    does ``DYNAMIC_FEATURES`` and the providers), so a developer's ``.env`` with Dynamic videos on, pinned workers and
    slots, a shared slot directory or another x264 preset/CRF changes no test's outcome."""
    dotenv = tmp_path / ".env"
    dotenv.write_text("DYNAMIC_ENABLED=true\nRENDER_MOTION_WORKERS=5\nRENDER_CPU_SLOTS=3\n"
                      f"RENDER_SLOT_DIR={tmp_path / 'slots'}\nMOTION_X264_PRESET=slow\nMOTION_X264_CRF=23\n",
                      encoding="utf-8")
    from_dotenv = Settings(_env_file=str(dotenv))
    for env, name in MOTION_SETTINGS.items():
        assert os.environ.get(env) == root_conftest._FORCED_ENV.get(env), env
        assert getattr(from_dotenv, name) == Settings.model_fields[name].default, name
