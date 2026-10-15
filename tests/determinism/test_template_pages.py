"""Every B2 template fixture on the real render page (B2c, B2 plan §A step 3 "three pilot templates through EVERY gate";
DYNAMIC_VIDEO_DESIGN §7.12 preflight, §7.4 stage contract, R-29, R-59): what jsdom cannot measure.

Each fixture timeline (``tests/compose/dynamic/template_fixtures.py``: ``<template>.<format>[.<case>].json``) passes
the real preflight with no blocking finding and no ``type_floor`` finding at all (the template's fit holds every R-29
floor and every critical box in the safe zones, with the captions the fixture burns and with captions burned in any
case), and the page converts no TeX after it declared its strings (``texConversions`` stays 0 through every scene:
each template's static ``tex()`` is complete in both formats) and raises no page error. The 1x / 3x / reverse
frame-hash gate of the same files is ``test_template_fixtures.py``; formula_focus's stricter page checks (no finding
at all, its tints and braces) are ``test_formula_focus_page.py``.

Slow (real Chromium) and container-marked like the other real-page tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from aadhi.compose.dynamic.preflight import run_preflight
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import fixture_name, fixture_paths, load_fixture

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "template.page"
PATHS = fixture_paths()
PILOTS = ("formula_focus", "list_build", "media_split")


def test_every_pilot_has_fixtures_in_both_formats() -> None:
    have = {(fixture_name(p)[0], fixture_name(p)[1]) for p in PATHS}
    assert {(t, f) for t in PILOTS for f in ("landscape", "portrait")} <= have


@pytest.mark.parametrize("captions", [None, True], ids=["as-served", "captions-burned"])
@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_fixture_passes_preflight_without_a_block_or_a_type_floor_finding(real_app, path: Path,
                                                                                captions: bool | None) -> None:
    served = load_fixture(path)
    tl = Timeline.model_validate(served)
    res = await run_preflight(real_app.settings, tl, token=TOKEN, served_timeline=served, base_url=real_app.base,
                              captions=captions)
    items = res.items()
    assert res.ok, [i for i in items if i["blocking"]]
    checks = res.summary()["checks"]
    assert checks["type_floor"] == {"status": "pass", "count": 0}, [i for i in items if i["check"] == "type_floor"]
    assert res.stats.get("frames", 0) > 0, "preflight scanned no frame"


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_fixture_converts_no_tex_after_ready_and_raises_no_page_error(real_app, path: Path) -> None:
    served = load_fixture(path)
    tl = Timeline.model_validate(served)
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        info = await page.info()
        assert await page.page.evaluate("() => window.aadhiStage.texConversions()") == 0, "converted before ready"
        for sc in info.scenes:
            for f in (sc.f0, (sc.f0 + sc.f1) // 2, sc.f1 - 1):
                await page.seek(f)
            assert await page.page.evaluate("() => window.aadhiStage.texConversions()") == 0, (
                f"{path.name}: scene {sc.index} converted TeX its template's tex() does not declare")
        assert page.errors == []
