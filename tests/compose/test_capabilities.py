"""Render capability detection (cached, plain-language reasons); no real subprocesses."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from aadhi.compose import capabilities as caps_mod
from aadhi.compose.capabilities import render_capabilities, reset_capabilities_cache
from aadhi.config import Settings

ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC (codec h264)
 V....D libx264rgb           libx264 H.264 RGB (codec h264)
 A....D aac                  AAC (Advanced Audio Coding)
"""
FILTERS = " .. subtitles         V->V       Render text subtitles onto input video using the libass library.\n"


@pytest.fixture(autouse=True)
def fresh_cache():
    reset_capabilities_cache()
    yield
    reset_capabilities_cache()


@pytest.fixture()
def fake_tools(monkeypatch: pytest.MonkeyPatch):
    state = {"which": {"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"}, "encoders": ENCODERS,
             "filters": FILTERS, "chromium": True, "calls": 0}

    def run(cmd, **kwargs):
        state["calls"] += 1
        assert kwargs.get("timeout") and kwargs.get("stdin") is subprocess.DEVNULL
        out = state["encoders"] if cmd[-1] == "-encoders" else state["filters"]
        return SimpleNamespace(stdout=out.encode(), returncode=0)

    monkeypatch.setattr(caps_mod.shutil, "which", lambda name: state["which"].get(name))
    monkeypatch.setattr(caps_mod.subprocess, "run", run)
    monkeypatch.setattr(caps_mod, "chromium_installed", lambda: state["chromium"])
    return state


def test_everything_present(fake_tools) -> None:
    caps = render_capabilities(Settings(_env_file=None))
    assert caps.ok and caps.reasons == [] and caps.burn_captions
    assert caps.checks == {"ffmpeg": True, "ffprobe": True, "libx264": True, "aac": True, "subtitles": True,
                           "chromium": True}
    assert caps.as_dict()["ok"] is True


def test_missing_tools_give_plain_reasons(fake_tools) -> None:
    fake_tools["which"] = {"ffprobe": "/usr/bin/ffprobe"}
    fake_tools["chromium"] = False
    caps = render_capabilities(Settings(_env_file=None))
    assert not caps.ok
    assert any("ffmpeg was not found" in r for r in caps.reasons)
    assert any("playwright install chromium" in r for r in caps.reasons)
    assert caps.checks["ffmpeg"] is False and "libx264" not in caps.checks


def test_encoder_and_libass_detection(fake_tools) -> None:
    fake_tools["encoders"] = ENCODERS.replace(" libx264  ", " libx265  ").replace("libx264rgb", "libx265rgb")
    fake_tools["filters"] = ""
    caps = render_capabilities(Settings(_env_file=None), browser=False)
    assert not caps.ok and caps.checks["libx264"] is False and caps.checks["aac"] is True
    assert caps.burn_captions is False and "chromium" not in caps.checks
    assert any("no libx264 encoder" in r for r in caps.reasons)


def test_results_are_cached_and_resettable(fake_tools) -> None:
    s = Settings(_env_file=None)
    first = render_capabilities(s, browser=False)
    calls = fake_tools["calls"]
    assert render_capabilities(s, browser=False) is first and fake_tools["calls"] == calls
    assert render_capabilities(s, browser=False, force=True) is not first
    reset_capabilities_cache()
    render_capabilities(s, browser=False)
    assert fake_tools["calls"] > calls


def test_absolute_tool_paths(tmp_path: Path, fake_tools) -> None:
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    s = Settings(_env_file=None, ffmpeg_path=str(exe), ffprobe_path=str(tmp_path / "missing" / "ffprobe"))
    caps = render_capabilities(s, browser=False)
    assert caps.checks["ffmpeg"] is True and caps.checks["ffprobe"] is False


def test_unknown_chromium_state_is_not_a_failure(fake_tools) -> None:
    fake_tools["chromium"] = None
    caps = render_capabilities(Settings(_env_file=None))
    assert caps.ok and caps.checks["chromium"] is None


def test_extensionless_windows_path_finds_the_exe(tmp_path, fake_tools, monkeypatch) -> None:
    """CreateProcess (the renderer's subprocess) runs C:/tools/ffmpeg as C:/tools/ffmpeg.exe: so does the check."""
    (tmp_path / "ffmpeg.exe").write_bytes(b"")
    (tmp_path / "run.bat").write_bytes(b"")
    monkeypatch.setattr(caps_mod.sys, "platform", "win32")
    assert caps_mod.resolve_tool(str(tmp_path / "ffmpeg")) == str(tmp_path / "ffmpeg.exe")
    assert caps_mod.resolve_tool(str(tmp_path / "missing")) is None
    assert caps_mod.resolve_tool(str(tmp_path / "ffmpeg.v7")) is None  # an extension is never retried
    s = Settings(_env_file=None, ffmpeg_path=str(tmp_path / "ffmpeg"), ffprobe_path=str(tmp_path / "run"))
    caps = render_capabilities(s, browser=False)
    assert caps.checks["ffmpeg"] is True and caps.checks["ffprobe"] is False  # only .exe, as CreateProcess
    monkeypatch.setattr(caps_mod.sys, "platform", "linux")
    assert caps_mod.resolve_tool(str(tmp_path / "ffmpeg")) is None  # elsewhere a path is taken as written


def test_a_failing_encoder_listing_is_not_reported_as_a_missing_encoder(fake_tools, monkeypatch) -> None:
    def run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 20)

    monkeypatch.setattr(caps_mod.subprocess, "run", run)
    caps = render_capabilities(Settings(_env_file=None), browser=False)
    assert not caps.ok and caps.checks["libx264"] is None and caps.checks["aac"] is None
    assert any("failed or timed out" in r for r in caps.reasons)
    assert not any("no libx264 encoder" in r for r in caps.reasons)
