"""Classic invariance with the real tools (slow: Chromium + ffmpeg; offline). Run in the Docker ``test`` image by
CI and locally wherever Chromium and ffmpeg exist (docs/DYNAMIC_VIDEO_DESIGN.md section 3.6):

* the render page, driven live, reports exactly the recorded states (``goldens/<fixture>/capture.json``);
* the pinned ffprobe facts of the branding clips (``goldens.BRANDING_PROBES``) are true;
* the classic render is reproducible: two renders of the same code compare equal (the harness's self-check);
* with ``AADHI_INVARIANCE_BASE`` (a checkout directory or a git revision) the differential pixel test of that base
  against this checkout. CI runs the differential as its own step (``tests/invariance/differential.py``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from aadhi.config import ROOT_DIR, Settings

from . import classic_fixture as fx
from . import goldens as g

pytestmark = pytest.mark.slow

HERE = Path(__file__).resolve().parent
BASE_ENV = "AADHI_INVARIANCE_BASE"


@pytest.fixture(scope="module")
def tools() -> Settings:
    from aadhi.compose.capabilities import render_capabilities

    settings = Settings(_env_file=None)
    caps = render_capabilities(settings, browser=True, force=True)
    if not caps.ok:
        pytest.skip("rendering is unavailable here: " + " ".join(caps.reasons))
    if not (ROOT_DIR / "web" / "vendor" / "manifest.json").is_file():
        pytest.skip("web/vendor is missing (npm ci)")
    return settings


def _env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, *args], cwd=ROOT_DIR, env=_env(), capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


@pytest.mark.parametrize("fixture", sorted(fx.FIXTURES))
def test_live_capture_matches_the_golden(tools: Settings, tmp_path: Path, fixture: str) -> None:
    proc = _run([str(HERE / "render_classic.py"), "--code-root", str(ROOT_DIR), "--out", str(tmp_path),
                 "--fixture", fixture, "--capture-only"], timeout=900)
    assert proc.returncode == 0, proc.stderr[-3000:]
    live = json.loads((tmp_path / "capture.json").read_text(encoding="utf-8"))
    problems = g.compare_capture(g.read_capture(fixture), live)
    assert not problems, "the render page's states changed (re-record only for an approved classic change):\n" + \
        "\n".join(problems[:40])


@pytest.mark.parametrize("name", sorted(g.BRANDING_PROBES))
def test_branding_probes_match_the_files(tools: Settings, name: str) -> None:
    from aadhi.compose.ffmpeg import probe

    info = asyncio.run(probe(ROOT_DIR / "video_template" / f"{name}.mp4", settings=tools))
    assert info.duration == pytest.approx(g.BRANDING_PROBES[name]["duration"], abs=0.05)
    assert (info.width, info.height, info.has_video, info.has_audio) == (1280, 720, True, True)
    assert info.color_space is None and info.color_range is None  # untagged, as goldens._probe_for says


def _differential(out: Path, *base_args: str) -> tuple[int, dict, str]:
    proc = _run([str(HERE / "differential.py"), *base_args, "--head-root", str(ROOT_DIR), "--out", str(out),
                 "--keep-states"], timeout=7200)
    report_path = out / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    return proc.returncode, report, proc.stdout[-4000:] + proc.stderr[-3000:]


def test_classic_render_is_reproducible(tools: Settings, tmp_path: Path) -> None:
    """The harness's self-check: two renders of this checkout (intro + first two scenes) compare equal."""
    code, report, output = _differential(tmp_path / "self", "--base-root", str(ROOT_DIR), "--max-scenes", "2")
    assert code == 0 and report.get("ok"), output
    assert report["frames"]["failing"] == 0 and report["frames"]["base"] == report["frames"]["head"] > 0
    assert report["timeline_equal"] and not report["capture"]["changed_scenes"]


@pytest.mark.skipif(not os.environ.get(BASE_ENV), reason=f"set {BASE_ENV} to a checkout or a git revision")
def test_differential_against_the_base(tools: Settings, tmp_path: Path) -> None:
    base = os.environ[BASE_ENV]
    args = ["--base-root", base] if Path(base).is_dir() else ["--base-ref", base]
    code, report, output = _differential(tmp_path / "diff", *args)
    assert code == 0 and report.get("ok"), output
