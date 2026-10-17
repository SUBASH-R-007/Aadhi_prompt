"""The 1x / 3x / reverse determinism gate of every B2 template fixture (DYNAMIC_VIDEO_DESIGN §7.4 test (a); B2 plan
§P condition 3: "every template fixture still runs the 1x/3x/reverse gate"; §A step 3). Each fixture timeline
(``tests/compose/dynamic/template_fixtures.py``: ``tests/compose/dynamic/fixtures/templates/<template>.<format>*.json``)
is rendered by the real app's ``/render-dynamic`` page as one page in order, three pages at once and one page in
reverse, over its first 300 frames (all of them when it has fewer): every captured frame (CDP JPEG q92) must have the
same SHA-256 in every order, with no page error and no timer-guard trip, and the frames must actually change.

Slow and container-marked like ``test_frame_hashes.py``: Windows renders are compared with Windows renders only; the
Docker test image runs the same gate on Linux (§7.4 (c)). Before the first template lands there is no fixture and the
parametrisation is empty.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from aadhi.compose.dynamic.determinism import run_plans, standard_plans
from aadhi.compose.dynamic.stage_page import open_stage, placeholder_frames
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import fixture_paths, load_fixture

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "determinism.template"
FRAMES = 300
PATHS = fixture_paths()
# A stage defect a fixture reproduces, recorded rather than hidden (strict: the gate fails the day it is fixed, so the
# mark is then removed). outro_next.landscape (step 5): after a crossfade, Chromium keeps the scene and caption layers
# composited only when the page played through the crossfade, so the translucent caption pill over the second scene
# blends one colour level differently by seek history (frames 195-299 of the reverse plan, rows y 1016-1024; the
# template's DOM is identical in both orders; a cut, or no captions, and it is gone). It is the stage's, not the
# template's: any crossfade under a burned caption is exposed, this fixture's pixels just round differently. The fix
# measured (`will-change: opacity` on `.dyn-scene`) makes every order agree but changes 7,314 of 26,112 frames in 67 of
# the 96 template fixtures (1x, their first 300 frames; the two B1-shaped timelines kept every frame): output that
# already works, so an owner decision (HANDOFF).
KNOWN_STAGE_DEFECTS = {
    "outro_next.landscape": "stage: crossfade + burned caption pill composited by seek history (owner decision)",
}


def _params() -> list[object]:
    return [pytest.param(p, id=p.stem, marks=pytest.mark.xfail(strict=True, reason=KNOWN_STAGE_DEFECTS[p.stem]))
            if p.stem in KNOWN_STAGE_DEFECTS else pytest.param(p, id=p.stem) for p in PATHS]


@pytest.mark.parametrize("path", _params())
async def test_template_fixture_frames_are_identical_in_any_order(real_app, path: Path) -> None:
    tl = Timeline.model_validate(load_fixture(path))
    served = tl.model_dump(mode="json")
    total = scene_frame_bounds(tl)[-1][1] if tl.scenes else 0
    count = min(FRAMES, total)
    assert count > 0, f"{path.name}: no frames"

    @asynccontextmanager
    async def open_page():
        # the presenter's frames (aadhi_intro / aadhi_recap fixtures) as the preflight serves media: a placeholder
        # (the real clip's frames are gated by tests/determinism/test_presenter_pages.py)
        async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                              served_timeline=served, frames=placeholder_frames) as page:
            yield page

    report = await run_plans(open_page, standard_plans(0, count, 3))
    mismatches = report.mismatches()
    assert mismatches == [], f"{path.name}: {len(mismatches)} frames differ, first: {mismatches[:3]}"
    assert report.guard_trips == [] and report.errors == [], (report.guard_trips, report.errors)
    assert report.distinct_frames() > min(30, count // 4), f"{path.name}: the frames hardly change"
