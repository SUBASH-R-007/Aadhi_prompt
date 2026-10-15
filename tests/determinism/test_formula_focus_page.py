"""``formula_focus`` on the real render page (B2c; DYNAMIC_VIDEO_DESIGN §4.3, §7.12, R-29, R-53, R-59): what jsdom
cannot measure. Every formula_focus fixture (``tests/compose/dynamic/template_fixtures.py``: typical, light family,
lint-limit, a very long word, Tamil, Hindi, mixed Tamil + English + TeX; both formats) passes the real preflight in one
round with no finding at all (no overflow, every critical box in the safe zones, the contrast and the type floor
held: the layout's fit, the 9:16 "=" break and compact stack, the sentence rule), converts no TeX after the page
declared its strings (the static ``tex()`` is complete in both formats), and draws the tints and braces where the
events put them. The 1x / 3x / reverse frame-hash gate of the same fixtures is ``test_template_fixtures.py``.

Slow (real Chromium) and container-marked like the other real-page tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic.preflight import run_preflight
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import fixture_name, fixture_paths, load_fixture

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "formula_focus.page"
PATHS = [p for p in fixture_paths() if fixture_name(p)[0] == "formula_focus"]

# what the page draws of one scene: the tint groups' fills by symbol, the braces drawn, the shown formula form
_STATE_JS = """(index) => {
  const scene = document.querySelector(`.dyn-scene[data-scene="${index}"]`);
  const root = scene ? scene.querySelector('.ff') : null;
  if (!root) return null;
  const shown = [...root.querySelectorAll('.ff-tex')].find((e) => e.style.display !== 'none');
  return {
    fills: [...shown.querySelectorAll('[data-dyn-tint]')].map((g) => [g.getAttribute('data-dyn-tint'), g.getAttribute('fill')]),
    braces: [...root.querySelectorAll('.ff-brace')].filter((b) => b.getAttribute('d')).length,
    broken: shown.classList.contains('ff-tex--broken'),
  };
}"""


def test_there_are_formula_focus_fixtures_in_both_formats() -> None:
    assert {fixture_name(p)[1] for p in PATHS} == {"landscape", "portrait"}


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_fixture_passes_preflight_with_no_finding(real_app, path: Path) -> None:
    served = load_fixture(path)
    tl = Timeline.model_validate(served)
    res = await run_preflight(real_app.settings, tl, token=TOKEN, served_timeline=served, base_url=real_app.base)
    assert res.ok and res.rounds == 1 and res.remedies == [], [i for i in res.items()]
    checks = res.summary()["checks"]
    assert checks == {c: {"status": "pass", "count": 0} for c in ("text_overflow", "safe_zones", "contrast", "type_floor")}


@pytest.mark.parametrize("path", PATHS, ids=[p.stem for p in PATHS])
async def test_fixture_draws_its_tints_and_braces_and_converts_no_late_tex(real_app, path: Path) -> None:
    served = load_fixture(path)
    tl = Timeline.model_validate(served)
    entity = served["presentation"].get("entity_colors") or {}
    content = served["presentation"]["content"]
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        info = await page.info()
        for sc, scene in zip(info.scenes, tl.scenes, strict=True):
            await page.seek(sc.f1 - 1)  # the scene's last frame: every cue has come
            state: Any = await page.page.evaluate(_STATE_JS, sc.index)
            assert state is not None, f"{scene.scene_id}: formula_focus did not draw the scene"
            formula = next(i for i in scene.board if i.kind == "formula")
            symbols = [v.symbol_latex for v in formula.variables]
            # one placeholder group or more per symbol the formula shows, each in its lesson-wide entity colour
            # (R-30, §4.7) or the ink around it (currentColor) when it has no slot
            by_fill = {fill: s for s, fill in ((s, f"#0A0B{k:02X}") for k, s in enumerate(dict.fromkeys(symbols)))}
            assert state["fills"], f"{scene.scene_id}: no tinted symbol found"
            for tag, fill in state["fills"]:
                symbol = by_fill[tag.upper()]
                slot = entity.get(symbol)
                want = content[f"entity:{slot}"] if slot is not None else "currentColor"
                assert fill.upper() == want.upper(), (scene.scene_id, symbol, fill, want)
            # a brace for every symbol a cue introduces: its var event, else B1's var:<n> sync cue
            cued = {e.target.split(":var:", 1)[1] for e in scene.events if e.kind == "var" and e.target}
            cued |= {symbols[int(c.part[4:])] for c in scene.sync_cues
                     if c.kind == "var" and c.item_id == formula.id and c.part and c.part.startswith("var:")
                     and c.part[4:].isdigit() and int(c.part[4:]) < len(symbols)}
            assert state["braces"] == len(cued & set(symbols)), (scene.scene_id, state)
        assert await page.page.evaluate("() => window.aadhiStage.texConversions()") == 0
        assert page.errors == []
