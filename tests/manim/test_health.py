"""Sandbox self-check (``aadhi.manim.health.sandbox_health``): booleans only, no host paths or secrets."""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.manim import health
from aadhi.manim.health import sandbox_health
from aadhi.manim.render import manim_version


def test_disabled_sandbox_reports_unavailable(app_env) -> None:
    settings = app_env.model_copy(update={"manim_sandbox": "disabled"})
    result = sandbox_health(settings)
    assert not result.available and not result.isolated and result.sandbox == "disabled"
    assert not result.probe_ran


def test_subprocess_probe_denies_network_and_sees_no_secrets(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    # Secrets in the environment must not reach the probe (the runner's env allow-list drops them).
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-never-leak")
    monkeypatch.setenv("JWT_SECRET", "jwt-should-never-leak")
    settings = app_env.model_copy(update={"manim_sandbox": "subprocess"})
    result = sandbox_health(settings)
    assert result.available and result.sandbox == "subprocess"
    assert result.isolated is False and "subprocess" in result.isolation  # honest: process isolation only
    assert result.probe_ran is True
    assert result.network_denied is True  # the audit hook (or the OS) refuses the connection
    assert result.env_clean is True  # no secret-shaped vars were visible
    assert result.manim_version == manim_version() and result.manim_version_ok


def test_health_output_leaks_no_host_paths(app_env) -> None:
    settings = app_env.model_copy(update={"manim_sandbox": "subprocess"})
    data = sandbox_health(settings).as_dict()
    blob = repr(data)
    for leak in (str(settings.data_dir), "Users", "AppData", "/home/"):
        assert leak not in blob, leak


def test_env_allowlist_has_no_secret_names() -> None:
    assert health._env_allowlist_is_secret_free()


def _captured_probe(app_env, monkeypatch: pytest.MonkeyPatch, **settings) -> tuple[str, object]:
    seen: dict = {}

    def fake_run_process(cmd, *, cwd, env, timeout, **kw):
        seen["script"] = Path(cmd[-1]).read_text(encoding="utf-8")
        return 0, '[AADHI_PROBE] {"network": "denied", "env_has_secrets": false, "host_file": "denied"}', "", 0.1

    monkeypatch.setattr(health, "run_process", fake_run_process)
    result = sandbox_health(app_env.model_copy(update={"manim_sandbox": "subprocess", **settings}))
    return seen["script"], result


def test_probe_follows_the_audit_hook_setting(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    script, result = _captured_probe(app_env, monkeypatch)
    assert script.startswith("_AADHI_HARDEN = {'audit_hook': True,") and "_CANARY = " in script
    assert "audit hook" in result.isolation and "disabled" not in result.isolation and result.files_denied is True
    script, result = _captured_probe(app_env, monkeypatch, manim_audit_hook=False)
    assert script.startswith("_AADHI_HARDEN = {'audit_hook': False,")
    assert "audit hook disabled" in result.isolation
    assert any("MANIM_AUDIT_HOOK=false" in note for note in result.notes)
    assert str(app_env.data_dir) not in repr(result.as_dict())


def test_a_readable_host_file_is_reported(app_env, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_process(cmd, *, cwd, env, timeout, **kw):
        return 0, '[AADHI_PROBE] {"network": "denied", "env_has_secrets": false, "host_file": "readable"}', "", 0.1

    monkeypatch.setattr(health, "run_process", fake_run_process)
    result = sandbox_health(app_env.model_copy(update={"manim_sandbox": "subprocess"}))
    assert result.files_denied is False and any("host file" in note for note in result.notes)


def test_subprocess_probe_cannot_read_host_files_with_the_hook_on(app_env) -> None:
    result = sandbox_health(app_env.model_copy(update={"manim_sandbox": "subprocess"}))
    assert result.probe_ran and result.files_denied is True


def test_probe_work_dir_is_inside_the_scratch_root(app_env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # Like render work dirs: SCRATCH_DIR (the Docker sandbox's host-shared path); removed afterwards.
    scratch = tmp_path / "scratch"
    seen: dict = {}

    def fake_run_process(cmd, *, cwd, env, timeout, **kw):
        seen["cwd"] = Path(cwd)
        return 0, '[AADHI_PROBE] {"network": "denied", "env_has_secrets": false, "host_file": "denied"}', "", 0.1

    monkeypatch.setattr(health, "run_process", fake_run_process)
    sandbox_health(app_env.model_copy(update={"manim_sandbox": "subprocess", "scratch_dir": str(scratch)}))
    assert seen["cwd"].parent == scratch and seen["cwd"].name.startswith("aadhi-probe-")
    assert not seen["cwd"].exists()
