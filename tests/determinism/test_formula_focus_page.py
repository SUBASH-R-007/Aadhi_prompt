"""``formula_focus`` on the real render page (B2c; DYNAMIC_VIDEO_DESIGN §4.3, §7.12, R-29, R-53, R-59): what jsdom
cannot measure. Every formula_focus fixture (``tests/compose/dynamic/template_fixtures.py``: typical, light family,
lint-limit, a very long word, Tamil, Hindi, mixed Tamil + English + TeX; both formats) passes the real preflight in one
round with no finding at all (no overflow, every critical box in the safe zones, the contrast and the type floor
held: the layout's fit, the 9:16 "=" break, the sentence rule; no legend row is ever dropped, RV L-13), converts no
TeX after the page declared its strings (the static ``tex()`` is complete in both formats), and draws the tints and
braces where the events put them, each brace at its own symbol's glyphs (under them, or over a numerator; B2c review
V1) and every legend row on screen at the scene's end (B2c review V2). The 1x / 3x / reverse frame-hash gate of the same fixtures is ``test_template_fixtures.py``.

Slow (real Chromium) and container-marked like the other real-page tests.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic.preflight import run_preflight
from aadhi.compose.dynamic.stage_page import open_stage
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR, fixture_name, fixture_paths, load_fixture

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "formula_focus.page"
PATHS = [p for p in fixture_paths() if fixture_name(p)[0] == "formula_focus"]

# what the page draws of one scene: the tint groups' fills by symbol, the braces drawn, the shown formula form; each
# drawn brace's box (its path's geometry) beside its symbol's first glyph group's box (stage px: the page is 1:1); the
# legend rows' opacity
_STATE_JS = """(index) => {
  const scene = document.querySelector(`.dyn-scene[data-scene="${index}"]`);
  const root = scene ? scene.querySelector('.ff') : null;
  if (!root) return null;
  const shown = [...root.querySelectorAll('.ff-tex')].find((e) => e.style.display !== 'none');
  const box = (el) => { const r = el.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  const hex = (k) => `#0A0B${k.toString(16).toUpperCase().padStart(2, '0')}`;
  return {
    fills: [...shown.querySelectorAll('[data-dyn-tint]')].map((g) => [g.getAttribute('data-dyn-tint'), g.getAttribute('fill')]),
    braces: [...root.querySelectorAll('.ff-brace')].filter((b) => b.getAttribute('d')).length,
    places: [...root.querySelectorAll('.ff-brace')].filter((b) => b.getAttribute('d')).map((b) => {
      const g = shown.querySelector(`[data-dyn-tint="${hex(Number(b.getAttribute('data-var')))}" i]`);
      const r = b.getBBox();
      const brace = [r.x, r.y, r.x + r.width, r.y + r.height];
      // the formula's other glyphs and rules (MathJax: a use per glyph, a rect per bar) the brace's box overlaps
      const others = [...shown.querySelectorAll('use, rect, path')].filter((el) => !el.closest('defs') && !(g && g.contains(el))).map(box);
      const hits = others.filter((o) => Math.min(o[2], brace[2]) - Math.max(o[0], brace[0]) > 1 && Math.min(o[3], brace[3]) - Math.max(o[1], brace[1]) > 1).length;
      // the formula's other glyphs across the symbol's x span, below and above it
      const s = g ? box(g) : [0, 0, 0, 0];
      const across = others.filter((o) => Math.min(o[2], s[2]) - Math.max(o[0], s[0]) > 1);
      return { k: Number(b.getAttribute('data-var')), brace, symbol: g ? box(g) : null, hits,
               below: across.filter((o) => o[1] >= s[3] - 1).length, above: across.filter((o) => o[3] <= s[1] + 1).length };
    }),
    rows: [...root.querySelectorAll('.ff-row')].map((r) => Number(getComputedStyle(r).opacity)),
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
            # each brace at its own symbol (V1): across its x span, its near edge within BRACE_GAP (8 px, +2 for the
            # stroke and rounding) of the glyphs, under them or over them; never under another row of the formula
            for place in state["places"]:
                assert place["symbol"] is not None, (scene.scene_id, place)
                bx0, by0, bx1, by1 = place["brace"]
                sx0, sy0, sx1, sy1 = place["symbol"]
                assert abs(bx0 - sx0) <= 3 and abs(bx1 - sx1) <= 3, (scene.scene_id, place)
                under = 0 <= by0 - sy1 <= 10
                over = 0 <= sy0 - by1 <= 10
                assert under or over, (scene.scene_id, place)
                assert place["hits"] == 0, ("a brace across another glyph of the formula", scene.scene_id, place)
                # on the open side: under only when nothing is below it (or something is above it too), over only
                # when nothing is above (a numerator's brace goes over it, never between it and the bar)
                assert (under and (place["below"] == 0 or place["above"] > 0)) or (over and place["above"] == 0), (
                    "a brace on the crowded side of its symbol", scene.scene_id, place)
            # every legend row is on screen at the scene's end (V2, RV L-13: none dropped to make room)
            assert state["rows"] and all(o == 1 for o in state["rows"]), (scene.scene_id, state["rows"])
        assert await page.page.evaluate("() => window.aadhiStage.texConversions()") == 0
        assert page.errors == []


def _five_row_reel() -> dict[str, Any]:
    """The 9:16 stress timeline with its lint-limit scene's legend at five rows (the 16:9 stress case's): more than a
    reel has room for under its two-line headline and broken formula."""
    served = copy.deepcopy(load_fixture(FIXTURE_DIR / "formula_focus.portrait.stress.json"))
    wide = load_fixture(FIXTURE_DIR / "formula_focus.landscape.stress.json")
    variables = next(i for i in wide["scenes"][0]["board"] if i["kind"] == "formula")["variables"]
    formula = next(i for i in served["scenes"][0]["board"] if i["kind"] == "formula")
    assert len(variables) == 5 and len(formula["variables"]) < 5
    formula["variables"] = variables
    return served


async def test_a_legend_too_long_for_a_reel_is_blocked_never_cut_to_a_carousel(real_app) -> None:
    """B2c review V2 (RV L-13): a legend the reel cannot hold is reported as overflow, which preflight blocks ("Shorten
    text", §7.12); every row stays on screen to the scene's end (the old compact stack faded the oldest rows out)."""
    served = _five_row_reel()
    tl = Timeline.model_validate(served)
    res = await run_preflight(real_app.settings, tl, token=TOKEN, served_timeline=served, base_url=real_app.base)
    blocks = [i for i in res.items() if i["blocking"]]
    assert not res.ok and blocks and {i["scene_index"] for i in blocks} == {0}, res.items()
    # the legend's overflow blocks with "Shorten text" (no remedy left); the same scene may block on more (with all its
    # sizes at their minimums the formula takes the narrow card, wider than it at 68 px; the overflowing legend
    # reaches the caption band): every finding preflight now sees, none masked by a mid-entrance sighting
    legend = [i for i in blocks if i["slot"] == "legend" and i["check"] == "text_overflow"]
    assert legend and legend[0]["remedy"] is None and legend[0]["action"] == "Shorten text", blocks
    assert {i["slot"] for i in blocks} <= {"legend", "formula"}, blocks
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        info = await page.info()
        await page.seek(info.scenes[0].f1 - 1)
        state: Any = await page.page.evaluate(_STATE_JS, 0)
        assert state["rows"] == [1, 1, 1, 1, 1], state["rows"]
        assert page.errors == []


_OUTLINE_JS = """() => {
  const card = document.querySelector('.dyn-scene[data-scene="0"] .ff-card').getBoundingClientRect();
  const path = document.querySelector('.dyn-scene[data-scene="0"] .dyn-cue-outline');
  const b = path.getBBox();
  return { card: [card.left, card.top, card.right, card.bottom], outline: [b.x, b.y, b.x + b.width, b.y + b.height],
           visible: getComputedStyle(path).visibility };
}"""


@pytest.mark.parametrize("name", ["formula_focus.portrait.json", "formula_focus.landscape.json"])
async def test_a_look_here_on_the_card_goes_round_it(real_app, name: str) -> None:
    """B2c review CG-1: the card (9:16: x 156-924, past the critical text column) is a shape: its look_here outline
    is drawn round it, outset by FOCUS_OUTSET, never clamped across it to the text column."""
    served = copy.deepcopy(load_fixture(FIXTURE_DIR / name))
    scene = served["scenes"][0]
    walk = next(e for e in scene["events"] if e["kind"] == "look_here")
    walk["target"] = "f"
    tl = Timeline.model_validate(served)
    async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                          served_timeline=served) as page:
        info = await page.info()
        await page.seek(info.scenes[0].f0 + round((walk["t"] + 0.5) * tl.fps))
        got: Any = await page.page.evaluate(_OUTLINE_JS)
        assert page.errors == []
    cx0, cy0, cx1, cy1 = got["card"]
    ox0, oy0, ox1, oy1 = got["outline"]
    assert got["visible"] == "visible", got
    # the stroke's centre line lies FOCUS_OUTSET + FOCUS_PX / 2 = 10 px outside the card on every side (+-1 rounding)
    assert all(abs(d - 10) <= 1 for d in (cx0 - ox0, cy0 - oy0, ox1 - cx1, oy1 - cy1)), got
