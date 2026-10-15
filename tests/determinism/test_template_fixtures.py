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
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.compose.motion.chunks import scene_frame_bounds
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import fixture_paths, load_fixture

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "determinism.template"
FRAMES = 300
PATHS = fixture_paths()


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_template_fixture_frames_are_identical_in_any_order(real_app, path: Path) -> None:
    tl = Timeline.model_validate(load_fixture(path))
    served = tl.model_dump(mode="json")
    total = scene_frame_bounds(tl)[-1][1] if tl.scenes else 0
    count = min(FRAMES, total)
    assert count > 0, f"{path.name}: no frames"

    @asynccontextmanager
    async def open_page():
        async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                              served_timeline=served) as page:
            yield page

    report = await run_plans(open_page, standard_plans(0, count, 3))
    mismatches = report.mismatches()
    assert mismatches == [], f"{path.name}: {len(mismatches)} frames differ, first: {mismatches[:3]}"
    assert report.guard_trips == [] and report.errors == [], (report.guard_trips, report.errors)
    assert report.distinct_frames() > min(30, count // 4), f"{path.name}: the frames hardly change"
