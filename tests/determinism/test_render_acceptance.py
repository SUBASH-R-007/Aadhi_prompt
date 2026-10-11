"""B1 acceptance on the real stage and the real motion engine (DYNAMIC_VIDEO_DESIGN §10.3 B1, §7.4 (b), §7.12).

The Dynamic fixture (``evals/render_parity.py``: the Ohm's law lecture with a synthetic ``presentation``,
``board_generic``, ``mesh_drift`` / ``colour_block`` backgrounds, a cut and two crossfades, in-page captions) is
rendered once per format through ``/render-dynamic`` and ``aadhi.compose.motion.render_motion`` with small chunks (6
chunk joins, at scene starts and mid-scene). Then:

* the container checks pass: exactly the timeline's frames (every chunk too), the first video frame at 0 also
  without the edit list, the sound starting within one frame, BT.709 tags, the declared rate and the frame grid;
* parity: the live-mode stage draws what the render page draws (no codec), and matches the MP4 at 20 seeded random
  instants with SSIM >= 0.98 (``run_dynamic_parity``'s comparison, split into compositor, capture and x264 loss);
* decoded-frame continuity: around every chunk join each decoded MP4 frame is the stage's own frame of that index (a
  dropped, repeated or corrupted frame at a join would match a neighbour, or nothing);
* preflight passes on the fixture.
"""

from __future__ import annotations

import asyncio
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from aadhi.compose.dynamic.container import inspect_container
from aadhi.compose.dynamic.preflight import run_preflight
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.compose.qa import expected_frame_count
from tests.compose.dynamic.helpers import parity_eval, video_qa

pytestmark = pytest.mark.slow
TOKEN = "acceptance.check"
CHUNK_MIN, CHUNK_MAX = 40, 80
SSIM_MIN = 0.98  # live stage vs MP4 (§7.4 (b))
STAGE_SSIM_MIN = 0.99  # live stage vs the render page's lossless frame (the compositor alone, no codec)
JOIN_SSIM_FLOOR = 0.95  # decoded MP4 frame vs the stage's JPEG capture of it (x264 CRF 17 alone; grain lowers it)
JOIN_MARGIN = 0.005  # a neighbour must differ by more than this beyond the encoding loss to tell frames apart


@dataclass
class Rendered:
    fmt: str
    timeline: Any
    result: Any  # MotionResult
    mp4: Path
    seconds: float

    @property
    def total(self) -> int:
        return expected_frame_count(self.timeline.total_duration, self.timeline.fps)


@pytest.fixture(scope="module", params=["landscape", "portrait"])
def rendered(request: pytest.FixtureRequest, real_app) -> Rendered:
    engine = pytest.importorskip("aadhi.compose.motion.engine")
    fmt = request.param
    rp = parity_eval()
    tl = rp.dynamic_timeline(fmt, settings=real_app.settings)
    work = real_app.work / f"render-{fmt}"
    started = time.monotonic()
    result = rp.render_with_motion_engine(real_app.settings, tl, work / "out.mp4", work=work,
                                          burn_captions=bool(tl.presentation.captions_burn),
                                          config=engine.MotionConfig(chunk_min=CHUNK_MIN, chunk_max=CHUNK_MAX))
    return Rendered(fmt, tl, result, work / "out.mp4", time.monotonic() - started)


def test_container_checks_pass(rendered: Rendered, real_app) -> None:
    qa = rendered.result.container
    assert qa.ok, [p.message for p in qa.problems]
    assert qa.remedies == []  # nothing needed fixing
    assert qa.frames == rendered.total == rendered.result.frames == 366
    joins = [c.f0 for c in rendered.result.chunks if c.f0 > 0]
    assert len(joins) >= 5 and all(c["frames"] == c["expected"] for c in qa.chunks)
    assert qa.video_start == pytest.approx(0.0, abs=1e-6) and qa.raw_video_start == pytest.approx(0.0, abs=1e-6)
    assert qa.av_offset is not None and abs(qa.av_offset) < 1 / 30
    assert qa.color == {"color_space": "bt709", "color_primaries": "bt709", "color_transfer": "bt709",
                        "color_range": "tv"}
    assert qa.declared_cfr and qa.on_grid and qa.audible
    w, h = (1920, 1080) if rendered.fmt == "landscape" else (1080, 1920)
    assert (qa.width, qa.height) == (w, h)
    # an independent check of the file the engine handed over
    again = asyncio.run(inspect_container(rendered.mp4, settings=real_app.settings, fps=30,
                                          expected_frames=rendered.total, expect_audio=True))
    assert again.ok and again.frames == rendered.total


@pytest.fixture(scope="module")
def parity(rendered: Rendered, real_app) -> list[dict[str, Any]]:
    """The 20 parity samples of ``rendered``: live vs MP4, plus the split into compositor, capture and x264 loss."""
    rp, vqa = parity_eval(), video_qa()
    tl = rendered.timeline
    served = tl.model_dump(mode="json")
    burn = bool(tl.presentation.captions_burn)
    frames = rp.dynamic_sample_frames(rendered.total)
    shots = asyncio.run(rp.live_stage_shots(real_app.base, served, frames, rendered.mp4.parent / "live",
                                            captions=burn))
    renders = asyncio.run(rp.render_mode_shots(real_app.settings, real_app.base, served, frames,
                                               rendered.mp4.parent / "render", captions=burn))
    return rp.compare_dynamic(vqa, rendered.mp4, shots, frames, 30, renders)


def test_live_stage_draws_what_the_render_page_draws(parity: list[dict[str, Any]]) -> None:
    """The one compositor (``stage.js``) in live mode and in render mode, before any codec: the same picture."""
    assert len(parity) == 20
    worst = min(r["stage_ssim"] for r in parity)
    assert worst >= STAGE_SSIM_MIN, sorted(parity, key=lambda r: r["stage_ssim"])[:3]


def test_live_player_matches_the_mp4(parity: list[dict[str, Any]]) -> None:
    """§7.4 (b): the live player against the MP4 at 20 random instants, SSIM >= 0.98."""
    worst = min(r["ssim"] for r in parity)
    assert worst >= SSIM_MIN, sorted(parity, key=lambda r: r["ssim"])[:3]


async def _captures(real_app, tl: Any, frames: list[int]) -> dict[int, np.ndarray]:
    out: dict[int, np.ndarray] = {}
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=tl.model_dump(mode="json")) as page:
        await page.set_layers(captions=bool(tl.presentation.captions_burn), stage=True)
        for f in frames:
            await page.seek(f)  # the production capture (JPEG q92), so only x264 separates it from the MP4
            out[f] = np.asarray(Image.open(io.BytesIO(await page.capture_jpeg())).convert("RGB"))
    return out


def test_decoded_frames_continue_across_chunk_joins(rendered: Rendered, real_app) -> None:
    rp, vqa = parity_eval(), video_qa()
    joins = [c.f0 for c in rendered.result.chunks if c.f0 > 0]
    checked = sorted({j + d for j in joins for d in (-2, -1, 0, 1) if 0 <= j + d < rendered.total})
    wanted = sorted({f + d for f in checked for d in (-1, 0, 1) if 0 <= f + d < rendered.total})
    stage = asyncio.run(_captures(real_app, rendered.timeline, wanted))
    distinguishable = 0
    for f in checked:
        decoded = vqa.frame_rgb(rendered.mp4, f, 30)
        own = rp.ssim(decoded, stage[f])
        assert own >= JOIN_SSIM_FLOOR, (f, own)  # a broken reference across the join would garble the frame
        for n in (f - 1, f + 1):
            # a neighbour that differs from this frame by more than x264 does: a frame dropped, repeated or
            # reordered at this join would match that neighbour better than its own index
            if n in stage and rp.ssim(stage[n], stage[f]) < own - JOIN_MARGIN:
                distinguishable += 1
                assert own > rp.ssim(decoded, stage[n]), (f, n)
    assert distinguishable > 0  # some joins sit in moving footage (the crossfades), so a shifted frame would show


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
async def test_preflight_passes_on_the_fixture(real_app, fmt: str) -> None:
    tl = parity_eval().dynamic_timeline(fmt, settings=real_app.settings)
    res = await run_preflight(real_app.settings, tl, token=TOKEN, base_url=real_app.base)
    assert res.ok, res.summary()["problems"]
    assert res.summary()["stats"]["frames"] >= len(res.sampled)
