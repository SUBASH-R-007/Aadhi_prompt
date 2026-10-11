"""Clip frames at another rate than the page grid (a 24 fps Veo clip on the 30 fps timeline grid; real ffmpeg, slow).

The page asks for index ``n`` on its ``srcFps`` grid; the engine must hand it the source frame on screen at ``n / 30``,
``floor(n * 24 / 30)``, whatever the chunk plan: extraction runs start at every chunk's first media frame and after
every gap, so a frame that depended on where its run began would differ between chunk plans and skip a source frame
at chunk joins (§7.4, §7.7).
"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aadhi.compose.capabilities import resolve_tool
from aadhi.compose.motion.engine import _extract_media, _Files
from aadhi.compose.motion.media import LOOKAHEAD_FRAMES, MediaRequest
from aadhi.config import Settings

from .fixtures import BITS, CLIP_KEY, decode_bits, make_bits_video

pytestmark = pytest.mark.slow

SIZE = (320, 180)
SOURCE_FRAMES = 120  # 5 s at 24 fps: page slots 0..149 at 30


@pytest.fixture(scope="module")
def clip24(tmp_path_factory: pytest.TempPathFactory) -> Path:
    settings = Settings(_env_file=None)
    ffmpeg = resolve_tool(settings.ffmpeg_path)
    if ffmpeg is None or resolve_tool(settings.ffprobe_path) is None:
        pytest.skip("ffmpeg/ffprobe are not installed")
    return make_bits_video(ffmpeg, tmp_path_factory.mktemp("rate24") / "veo24.mp4", SOURCE_FRAMES, SIZE, fps=24)


def _extract(clip: Path, frames: list[int], work: Path) -> dict[int, tuple[int, str]]:
    """``page index -> (source frame drawn in it, sha256 of the served JPEG)`` for one chunk's plan."""
    settings = Settings(_env_file=None)
    files = _Files(None, settings, work / "inputs", {CLIP_KEY: clip})
    req = MediaRequest("v-rate24-320x180", "video", 30.0, tuple(sorted(set(frames))), SIZE, CLIP_KEY)
    entries, _, _ = asyncio.run(_extract_media([req], files, work, settings=settings, rates={},
                                               check_cancelled=None))
    out: dict[int, tuple[int, str]] = {}
    for n in req.frames:
        path = entries[(req.key, n)]
        gray = np.asarray(Image.open(path).convert("L"))
        value = decode_bits(gray, x0=0, y0=0, w=SIZE[0], h=SIZE[1] / 4)
        assert value < 1 << BITS
        out[n] = (value, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def test_resampled_clip_frames_do_not_depend_on_the_run_start(clip24: Path, tmp_path: Path) -> None:
    whole = _extract(clip24, list(range(0, 150)), tmp_path / "whole")
    for n, (value, _) in whole.items():
        assert value == (n * 24) // 30, f"slot {n} shows source frame {value}"
    plans: list[list[int]] = [list(range(start, start + 30)) for start in range(1, 41)]
    for join in (57, 58, 59, 60, 61, 62, 63, 120, 121):  # chunk A (with the lookahead) and chunk B of a join
        plans.append(list(range(max(0, join - 40), min(150, join + LOOKAHEAD_FRAMES))))
        plans.append(list(range(join, min(150, join + 25))))
    plans.append([5, 30, 31, 32, 90])  # gaps: several runs in one chunk
    for i, plan in enumerate(plans):
        got = _extract(clip24, plan, tmp_path / f"plan{i}")
        for n in plan:
            assert got[n][0] == whole[n][0], f"plan {i} ({plan[0]}..{plan[-1]}): slot {n} shows {got[n][0]}"
            assert got[n][1] == whole[n][1], f"plan {i}: slot {n} differs from the whole-clip run"
    seen = [whole[n][0] for n in range(150)]
    assert sorted(set(seen)) == list(range(SOURCE_FRAMES)), "every source frame is shown, none skipped"
