"""Aadhi on the real render page, with the real clip (step 5, owner decision 2; DYNAMIC_VIDEO_DESIGN §3.3, §7.4 test
(a), §7.7; B2 plan §1.12, §P (1): a picture is never scaled up on the page). Each ``aadhi_intro`` / ``aadhi_recap``
fixture (``tests/compose/dynamic/make_aadhi_fixtures.py``) is rendered by the real ``/render-dynamic`` page with the
presenter's frames served the way the worker serves them: the page's own media plan (``aadhiStage.mediaPlan``) parsed
by the worker's parser, every presenter entry's crop extracted from ``video_template/`` by the presenter cache
(``presenter.ensure_frames``), frame n of an entry the clip's frame n. Then:

* every captured frame (CDP JPEG q92) has the same SHA-256 rendered 1xN in order, as 3 chunks on three pages at once
  and in reverse (the presenter's settle, slide and fade, the brand wipe and the crossfade out of it included);
* the presenter is drawn 1:1 - its ``<img>``'s natural size is its box's, the box the look's ``rect`` - and he shows
  the clip's frame ``floor(t x 24)`` at scene time t;
* the page asks only for frames the plan lists (no media the test does not serve) and raises no error.

Slow and container-marked like the other real-page determinism tests: hashes are compared within one machine only. The
module is skipped until step 5's stage.js / media.py / engine requests are applied (the stage must draw the presenter
through ``web/js/dynamic/presenter.js``, and the worker's parser must read the crop).
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import presenter as pr
from aadhi.compose.dynamic.determinism import run_plans, standard_plans
from aadhi.compose.dynamic.stage_page import open_stage, placeholder_frames
from aadhi.compose.motion import media as mm
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.config import ROOT_DIR
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, load_fixture

STAGE_DRAWS_PRESENTER = "from './presenter.js'" in (ROOT_DIR / "web" / "js" / "dynamic" / "stage.js").read_text("utf-8")
WORKER_READS_CROP = "crop" in getattr(mm.MediaRequest, "__dataclass_fields__", {})
pytestmark = [pytest.mark.slow, pytest.mark.container,
              pytest.mark.skipif(not (STAGE_DRAWS_PRESENTER and WORKER_READS_CROP),
                                 reason="step 5's stage.js / media.py requests (the presenter) are not applied yet")]
TOKEN = "determinism.presenter"
FRAMES = 300
BRANDING = ROOT_DIR / "video_template"
FRAME_RE = re.compile(r"^/__frames/(?P<key>[A-Za-z0-9_.:-]{1,200})/(?P<n>[0-9]{6})\.jpg$")
PATHS = sorted(FIXTURE_DIR.glob("aadhi_*.json"))

_PRESENTER_JS = """() => {
  const img = document.querySelector('.dyn-scene .dyn-presenter img');
  if (!img) return null;
  const box = img.parentElement.getBoundingClientRect();
  const own = img.getBoundingClientRect();
  return { nw: img.naturalWidth, nh: img.naturalHeight, src: img.getAttribute('src'),
           box: [box.left, box.top, box.width, box.height], img: [own.width, own.height],
           visibility: getComputedStyle(img.parentElement).visibility };
}"""


async def _plan(real_app: Any, served: dict[str, Any], tl: Timeline, total: int) -> list[mm.MediaRequest]:
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=served, frames=placeholder_frames) as page:
        raw = await page.page.evaluate(f"() => window.aadhiStage.mediaPlan(0, {total})")
    return mm.parse_media_plan(raw, stage=(tl.width, tl.height))


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_aadhi_is_drawn_from_his_cached_crop_identically_in_any_order(real_app, tmp_path: Path,
                                                                            path: Path) -> None:
    served = load_fixture(path)
    tl = Timeline.model_validate(served)
    total = scene_frame_bounds(tl)[-1][1]
    count = min(FRAMES, total)
    requests = await _plan(real_app, served, tl, total)
    presenters = [s for s in tl.scenes if s.look is not None and s.look.presenter is not None]
    assert presenters and requests, f"{path.name}: no presenter planned"
    sets: dict[str, tuple[Path, int]] = {}
    for req in requests:
        assert req.kind == "aadhi" and req.crop is not None, req
        assert req.asset in pr.CLIPS.values(), req.asset
        sets[req.key] = pr.ensure_frames(tmp_path / "work", BRANDING / req.asset, crop=req.crop, size=req.size)
    asked: set[tuple[str, int]] = set()
    unknown: set[str] = set()

    def serve(p: str) -> Path | None:
        m = FRAME_RE.match(p)
        if m is None or m["key"] not in sets:
            unknown.add(p)
            return None
        directory, n = sets[m["key"]]
        asked.add((m["key"], int(m["n"])))
        return directory / pr.frame_name(min(int(m["n"]), n - 1))

    @asynccontextmanager
    async def open_page():
        async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                              served_timeline=served, frames=serve) as page:
            yield page

    report = await run_plans(open_page, standard_plans(0, count, 3))
    assert not unknown, f"the page asked for media the plan does not list: {sorted(unknown)[:5]}"
    mismatches = report.mismatches()
    assert mismatches == [], f"{path.name}: {len(mismatches)} frames differ by render order, first: {mismatches[:3]}"
    assert report.guard_trips == [] and report.errors == [], (report.guard_trips, report.errors)
    assert report.distinct_frames() > count // 2, "Aadhi moves on every clip frame"

    # 1:1 and frame-exact, in every presenter scene inside the frames gated
    async with open_page() as page:
        for scene in presenters:
            t = 1.0
            f = round((scene.start + t) * tl.fps)
            if f >= count:
                continue
            await page.seek(f)
            got = await page.page.evaluate(_PRESENTER_JS)
            assert got is not None and got["visibility"] == "visible", (scene.scene_id, got)
            x, y, w, h = scene.look.presenter["rect"]  # type: ignore[union-attr]
            assert (got["nw"], got["nh"]) == (w + w % 2, h + h % 2), f"{scene.scene_id}: drawn 1:1 ({got})"
            assert got["box"][2:] == [w, h], got
            settle = pr.SETTLE_RISE if scene.look.presenter.get("enter_kind") == "rise" else 0  # type: ignore[union-attr]
            assert got["box"][0] == x and abs(got["box"][1] - y) <= settle, got
            n = int(((f / tl.fps) - scene.start) * pr.CLIP_FPS + 1e-6)
            assert got["src"].endswith(f"/{n:06d}.jpg"), (scene.scene_id, f, got["src"])
        assert page.errors == []
