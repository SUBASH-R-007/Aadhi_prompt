"""The Dynamic part of evals/render_parity.py: the fixture timelines, SSIM, the seeded instants, and the whole live
stage vs MP4 comparison against the stub stage with a reference capture + encode (the real stage and the motion
engine run in ``tests/determinism/test_render_acceptance.py``)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pytest

from .helpers import dynamic_timeline, need_chromium, parity_eval, reference_render, settings, tools
from .stage_server import serve_stage


def test_ssim_properties() -> None:
    rp = parity_eval()
    rng = np.random.default_rng(7)
    img = (rng.random((120, 160, 3)) * 255).astype(np.uint8)
    smooth = np.tile(np.linspace(0, 255, 160, dtype=np.uint8)[None, :, None], (120, 1, 3))
    assert rp.ssim(img, img) == pytest.approx(1.0)
    assert rp.ssim(smooth, smooth.copy()) == pytest.approx(1.0)
    noisy = np.clip(smooth.astype(int) + rng.integers(-40, 40, smooth.shape), 0, 255).astype(np.uint8)
    assert rp.ssim(smooth, noisy) < 0.9
    shifted = np.roll(img, 3, axis=1)
    assert rp.ssim(img, shifted) < 0.5
    gray = smooth[..., 0]
    assert rp.ssim(gray, gray) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        rp.ssim(img, img[:100])


def test_sample_frames_are_seeded() -> None:
    rp = parity_eval()
    a = rp.dynamic_sample_frames(366)
    assert a == rp.dynamic_sample_frames(366) and len(a) == 20 == len(set(a)) and a == sorted(a)
    assert all(0 <= f < 366 for f in a)
    assert rp.dynamic_sample_frames(366, seed=1) != a
    assert rp.dynamic_sample_frames(5) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
def test_dynamic_fixture_timeline(fmt: str) -> None:
    tl = dynamic_timeline(fmt)
    assert tl.presentation is not None and tl.presentation.style == "dynamic" and tl.presentation.format == fmt
    assert (tl.width, tl.height) == ((1920, 1080) if fmt == "landscape" else (1080, 1920)) and tl.fps == 30
    assert tl.intro is None and tl.schema_version == 3 and tl.total_duration == pytest.approx(12.2)
    looks = [s.look for s in tl.scenes]
    assert all(lk is not None and lk.template == "board_generic" for lk in looks)
    assert [lk.background["preset"] for lk in looks] == ["mesh_drift", "colour_block", "mesh_drift"]
    assert [lk.transition_in["kind"] for lk in looks] == ["cut", "crossfade", "crossfade"]
    chunks = [c for s in tl.scenes for c in s.kinetic_captions]
    if fmt == "landscape":
        assert chunks == [] and tl.captions
    else:
        assert chunks and all(len(" ".join(c.lines)) <= 32 and len(c.lines) <= 2 for c in chunks)
        for s in tl.scenes:
            assert all(0 <= c.start < c.end <= s.duration + 1e-6 for c in s.kinetic_captions)


@pytest.mark.slow
def test_live_stage_vs_mp4_on_the_stub_stage(tmp_path: Path) -> None:
    need_chromium()
    tools()
    rp = parity_eval()
    tl = dynamic_timeline("landscape")
    with serve_stage("parity.stub", {}) as (base, _):
        def render(timeline, out: Path) -> Path:
            asyncio.run(reference_render(base, timeline, out, chunk_frames=150))
            return out

        report = rp.run_dynamic_parity(settings(), tmp_path / "ok", timeline=tl, render=render, base=base)
        assert report["ok"], report["samples"]
        assert len(report["samples"]) == 20 and report["worst_ssim"] >= 0.98 and report["cfr"]["ok"]
        assert report["worst_stage_ssim"] >= 0.999  # one compositor: live == render page before any codec
        assert report["worst_capture_ssim"] >= 0.98 and report["worst_encode_ssim"] >= 0.98
        assert {"frame", "t", "ssim", "stage_ssim", "capture_ssim", "encode_ssim"} <= set(report["samples"][0])

        def late(timeline, out: Path) -> Path:  # every MP4 frame one second late: parity must fail
            asyncio.run(reference_render(base, timeline, out, chunk_frames=150, shift=30))
            return out

        bad = rp.run_dynamic_parity(settings(), tmp_path / "late", timeline=tl, render=late, base=base, samples=6)
        assert not bad["ok"] and bad["worst_ssim"] < 0.98
