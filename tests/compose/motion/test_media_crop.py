"""The presenter's crop in the worker's media plan (B2 plan §1.12, §2 "Aadhi"; step 5): ``parse_media_plan`` reads an
'aadhi' entry's ``crop`` (and refuses one anywhere else, or malformed), and ``engine._extract_media`` serves such an
entry from the presenter frame cache (``aadhi/compose/dynamic/presenter.py ensure_frames``): every frame of the crop
extracted once at the planned size, frame n of the page's plan the clip's frame n, a later chunk reusing the set.

These exercise step 5's requests to ``aadhi/compose/motion/media.py`` and ``engine.py``; until those are applied the
module is skipped (the integrator applies them). The extraction test needs ffmpeg and uses the real clip.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import presenter as pr
from aadhi.compose.motion import media as mm
from aadhi.config import ROOT_DIR, Settings

pytestmark = pytest.mark.skipif("crop" not in getattr(mm.MediaRequest, "__dataclass_fields__", {}),
                                reason="step 5's media.py request (the presenter crop) is not applied yet")

CLIP = ROOT_DIR / "video_template" / pr.CLIPS["left"]


def entry(**kw: Any) -> dict[str, Any]:
    e = {"key": "a-0123456789abcdef-480x480", "kind": "aadhi", "srcFps": 24, "frames": [0, 1, 2], "size": [480, 480],
         "asset_key": pr.CLIPS["left"], "crop": [0, 0, 720, 720]}
    e.update(kw)
    return e


def test_an_aadhi_entry_carries_its_crop() -> None:
    (req,) = mm.parse_media_plan([entry()], stage=(1920, 1080))
    assert req.crop == (0, 0, 720, 720)
    assert req.asset == pr.CLIPS["left"]
    (plain,) = mm.parse_media_plan([entry(crop=None)], stage=(1920, 1080))
    assert plain.crop is None, "without a crop the clip is scaled to cover the size, as before"
    merged = mm.parse_media_plan([entry(frames=[0]), entry(frames=[5])], stage=(1920, 1080))
    assert len(merged) == 1 and merged[0].frames == (0, 5) and merged[0].crop == (0, 0, 720, 720)


@pytest.mark.parametrize(("bad", "match"), [
    ({"kind": "video"}, "only a presenter entry"),
    ({"crop": [0, 0, 720]}, "whole px"),
    ({"crop": [0, 0, 720.5, 720]}, "whole px"),
    ({"crop": [0, 0, True, 720]}, "whole px"),
    ({"crop": [-2, 0, 720, 720]}, "x, y >= 0"),
    ({"crop": [0, 0, 1, 720]}, "w, h >= 2"),
    ({"crop": "0,0,720,720"}, "whole px"),
])
def test_a_bad_crop_breaks_the_contract(bad: dict[str, Any], match: str) -> None:
    with pytest.raises(mm.MediaPlanError, match=match):
        mm.parse_media_plan([entry(**bad)], stage=(1920, 1080))


def test_one_key_with_two_crops_breaks_the_contract() -> None:
    with pytest.raises(mm.MediaPlanError, match="crop"):
        mm.parse_media_plan([entry(), entry(crop=[0, 0, 576, 720])], stage=(1920, 1080))


class _Files:
    async def get(self, key: str, *, required: bool) -> Path | None:
        return CLIP if key == pr.CLIPS["left"] else None


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_extract_media_serves_a_crop_from_the_presenter_cache(tmp_path: Path) -> None:
    from aadhi.compose.motion import engine

    settings = Settings(_env_file=None, render_work_dir=str(tmp_path / "work"), data_dir=str(tmp_path / "data"))
    (req,) = mm.parse_media_plan([entry(frames=[0, 30, 191, 250], size=[96, 96],
                                        key="a-0123456789abcdef-96x96")], stage=(1920, 1080))
    out, _, warnings = asyncio.run(engine._extract_media([req], _Files(), tmp_path / "chunk", settings=settings,  # type: ignore[arg-type]
                                                         rates={}, check_cancelled=None))
    cache = pr.cache_dir(tmp_path / "work", pr.clip_digest(CLIP), (0, 0, 720, 720), (96, 96))
    assert out[(req.key, 0)] == cache / pr.frame_name(0)
    assert out[(req.key, 30)] == cache / pr.frame_name(30)
    assert out[(req.key, 191)] == cache / pr.frame_name(191)
    assert out[(req.key, 250)] == cache / pr.frame_name(191), "past the clip's end: its last frame"
    assert warnings == []
    assert not (tmp_path / "chunk").exists() or not any((tmp_path / "chunk").iterdir()), (
        "nothing of the presenter is written into the chunk's own (deleted) directory")
