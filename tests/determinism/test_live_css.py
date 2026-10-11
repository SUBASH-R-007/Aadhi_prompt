"""The live player's stylesheets do not change the Dynamic stage (DYNAMIC_VIDEO_DESIGN §7.4 (b), §4.8).

``web/watch.html`` and the Studio load ``base.css``, ``player.css`` and ``panels.css`` before ``dynamic.css``; the
render page loads only ``tokens.css`` and ``dynamic.css``. The classic player's unscoped rules (``.rt-k`` gold with a
glow, ``.rt-b`` white, the ``.rt-code`` chip, body typography) must not reach ``.dyn-stage``: the same stage (render
mode, the same frame) under both stylesheet sets must give the same computed style and box for every element, and
the same pixels. The fixture's board carries a ``[[keyword]]``, ``**bold**`` and `` `code` ``
(``evals/render_parity.py`` ``DYNAMIC_RICH_TEXT``), in a dark and a light (``paper_ink``) family.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import Any

import numpy as np
import pytest
from PIL import Image

from aadhi.compose.screenshot import CHROMIUM_ARGS
from tests.compose.dynamic.helpers import parity_eval

pytestmark = pytest.mark.slow

RENDER_SHEETS = ("tokens.css", "dynamic.css")  # web/render-dynamic.html
PROPS = ("color", "text-shadow", "padding-top", "padding-right", "padding-bottom", "padding-left", "background-color",
         "background-image", "border-top-left-radius", "border-bottom-right-radius", "font-family", "font-size",
         "font-weight", "font-style", "line-height", "letter-spacing", "word-spacing", "margin-top", "margin-right",
         "margin-bottom", "margin-left", "display", "box-sizing", "text-decoration-line", "vertical-align",
         "white-space", "text-transform", "outline-style", "opacity", "transform", "filter", "visibility")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">__LINKS__
<style>html,body{margin:0;width:__W__px;height:__H__px;overflow:hidden;background:#000}
#root{position:fixed;left:0;top:0;width:__W__px;height:__H__px}</style>
<script type="module">
import { createDynamicStage } from '/web/js/dynamic/stage.js';
const tl = await (await fetch('/__css-parity/timeline.json', { cache: 'no-store' })).json();
const stage = createDynamicStage(document.getElementById('root'), tl, { mode: 'render' });
stage.setLayers({ captions: true, stage: true });
window.__css = { stage, timeline: tl };
</script></head><body__BODY__><div id="root"></div></body></html>
"""

_SNAPSHOT_JS = """async ({ frame, props }) => {
  const { stage } = window.__css;
  await stage.seek(frame);
  await document.fonts.ready;
  await stage.seek(frame);
  const root = document.querySelector('.dyn-stage');
  const out = [];
  for (const el of [root, ...root.querySelectorAll('*')]) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    out.push({ tag: el.tagName, cls: el.getAttribute('class') || '', box: [r.x, r.y, r.width, r.height].map((v) => Math.round(v * 100) / 100),
      style: Object.fromEntries(props.map((p) => [p, cs.getPropertyValue(p)])) });
  }
  const k = root.querySelector('.rt-k');
  const card = k && k.closest('.dyn-card');
  return { elements: out, keyword: !!k, code: !!root.querySelector('.rt-code'), bold: !!root.querySelector('.rt-b'),
    shown: card ? Number(card.style.opacity || '1') : 0 };
}"""


def _fulfil(body: str | bytes, content_type: str) -> Any:
    async def handler(route: Any) -> None:
        await route.fulfill(status=200, content_type=content_type, body=body)

    return handler


async def _snapshots(base: str, timeline: dict[str, Any], frame: int) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    width, height = int(timeline["width"]), int(timeline["height"])
    body = json.dumps(timeline).encode("utf-8")
    pages = {"render": (RENDER_SHEETS, ""), "live": (parity_eval().LIVE_STYLESHEETS, ' class="watch-page"')}
    out: dict[str, Any] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            for name, (sheets, body_attr) in pages.items():
                context = await browser.new_context(viewport={"width": width, "height": height},
                                                    device_scale_factor=1, color_scheme="dark")
                links = "".join(f'<link rel="stylesheet" href="/web/css/{s}">' for s in sheets)
                html = (PAGE.replace("__LINKS__", links).replace("__BODY__", body_attr)
                        .replace("__W__", str(width)).replace("__H__", str(height)))
                url = f"{base}/__css-parity"
                await context.route(url, _fulfil(html, "text/html"))
                await context.route(f"{url}/timeline.json", _fulfil(body, "application/json"))
                page = await context.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_function("() => !!window.__css", timeout=60_000)
                snap = await page.evaluate(_SNAPSHOT_JS, {"frame": frame, "props": list(PROPS)})
                snap["pixels"] = np.asarray(Image.open(io.BytesIO(await page.screenshot(type="png"))).convert("RGB"))
                snap["errors"] = errors
                out[name] = snap
                await context.close()
        finally:
            await browser.close()
    return out


@pytest.mark.parametrize(("fmt", "family"), [("landscape", "dark"), ("portrait", "dark"),
                                             ("landscape", "paper_ink"), ("portrait", "paper_ink")])
def test_live_stylesheets_leave_the_stage_unchanged(real_app, fmt: str, family: str) -> None:
    rp = parity_eval()
    tl = rp.dynamic_timeline(fmt, settings=real_app.settings, family=family)
    served = tl.model_dump(mode="json")
    scene = next(i for i, s in enumerate(tl.scenes) if any(b.id in rp.DYNAMIC_RICH_TEXT for b in s.board))
    end = tl.scenes[scene].start + tl.scenes[scene].duration
    frame = int(round(end * tl.fps)) - 2  # the scene's settled end: the rich bullet is on screen
    snaps = asyncio.run(_snapshots(real_app.base, served, frame))
    live, render = snaps["live"], snaps["render"]
    assert live["errors"] == [] and render["errors"] == []
    assert render["keyword"] and render["code"] and render["bold"] and render["shown"] > 0.99, "fixture lost its markup"
    assert len(live["elements"]) == len(render["elements"])
    for a, b in zip(live["elements"], render["elements"], strict=True):
        assert (a["tag"], a["cls"]) == (b["tag"], b["cls"])
        diff = {p: (a["style"][p], b["style"][p]) for p in PROPS if a["style"][p] != b["style"][p]}
        assert not diff, f"{a['tag']}.{a['cls']}: live vs render {diff}"
        assert a["box"] == b["box"], f"{a['tag']}.{a['cls']}: box live {a['box']} vs render {b['box']}"
    assert np.array_equal(live["pixels"], render["pixels"]), "the live stylesheets changed the stage's pixels"
