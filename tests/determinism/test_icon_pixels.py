"""Icon fidelity in real Chromium (design §4.12; plan B2 §1.2 "normalised to path d"): every icon of the committed
index (``aadhi/compose/dynamic/icon_index.json``), drawn from its stored paths the way a timeline carries it, draws the
same pixels as the library's own SVG file at the 96 px card size (``zones.LAYOUT.icon["card_px"]``).

* An icon whose file holds only ``<path>`` elements is pixel-identical (its path data is only re-spelled).
* An icon with a ``circle``, ``ellipse``, ``rect``, ``line``, ``polyline`` or ``polygon`` converted to its SVG 2
  equivalent path may differ by anti-aliasing only: Skia rasterises a circle, an oval or a rounded rect by its own fast
  path, the equivalent arcs by the general one. Bounded here per icon: no pixel more than ``AA_MAX`` grey levels apart
  and at most ``AA_PIXELS`` pixels different, which a missing or extra stroke would exceed (a hole in a dot: 255
  levels).
* The excluded icons are excluded for a reason this page would show: a filled dot drawn stroke-only differs by whole
  pixels (``test_a_filled_dot_cannot_be_drawn_by_its_stroke``), which is why ``icons.py`` refuses every fill.

Needs node_modules (the library files) and Playwright's Chromium.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from aadhi.compose.dynamic import icons as ic
from aadhi.compose.dynamic.zones import LAYOUT
from aadhi.compose.screenshot import CHROMIUM_ARGS

pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not ic.NODE_MODULES.is_dir(), reason="node_modules is not installed")]

CELL = int(LAYOUT.icon["card_px"])  # 96
COLUMNS = 12
ROWS = 10
# Measured on Windows (2026-10-14, Lucide 1.48.0, Tabler 3.48.0): 5,430 path-only icons identical; of 755 with a
# converted shape 729 identical, the worst 8 grey levels on 28 pixels (Lucide's r=10 circles: volleyball, aperture).
AA_MAX = 12  # grey levels (of 255): anti-aliasing of a converted shape's edge
AA_PIXELS = 40  # pixels per 96 x 96 icon
STYLE_ATTRS = ('fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round"')


def _library_svg(set_id: str, name: str) -> str:
    s = next(x for x in ic.SETS if x.id == set_id)
    text = (ic.NODE_MODULES / s.package / s.svg_dir / f"{name}.svg").read_text(encoding="utf-8")
    start = text.index("<svg")
    return text[start:].replace('width="24"', f'width="{CELL}"', 1).replace('height="24"', f'height="{CELL}"', 1)


def _stored_svg(paths: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{CELL}" height="{CELL}" viewBox="0 0 24 24" '
            f'{STYLE_ATTRS}>' + "".join(f'<path d="{d}"/>' for d in paths) + "</svg>")


def _page(svgs: list[str]) -> str:
    cells = "".join(f'<div class="c">{s}</div>' for s in svgs)
    return ("<!doctype html><html><head><style>html,body{margin:0;background:#fff;color:#000}"
            f".g{{display:grid;grid-template-columns:repeat({COLUMNS},{CELL}px);grid-auto-rows:{CELL}px}}"
            ".c svg{display:block}</style></head>"
            f'<body><div class="g">{cells}</div></body></html>')


async def _cells(page, svgs: list[str]) -> list[np.ndarray]:
    await page.set_content(_page(svgs))
    shot = np.asarray(Image.open(io.BytesIO(await page.screenshot(type="png"))).convert("L")).astype(np.int16)
    return [shot[(i // COLUMNS) * CELL:(i // COLUMNS + 1) * CELL, (i % COLUMNS) * CELL:(i % COLUMNS + 1) * CELL]
            for i in range(len(svgs))]


async def _compare(pairs: list[tuple[str, str]]) -> list[tuple[int, int]]:
    """(max grey difference, differing pixels) per (library svg, stored svg) pair."""
    from playwright.async_api import async_playwright

    out: list[tuple[int, int]] = []
    per_page = COLUMNS * ROWS
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            context = await browser.new_context(viewport={"width": COLUMNS * CELL, "height": ROWS * CELL},
                                                device_scale_factor=1)
            page = await context.new_page()
            for k in range(0, len(pairs), per_page):
                chunk = pairs[k:k + per_page]
                a = await _cells(page, [p[0] for p in chunk])
                b = await _cells(page, [p[1] for p in chunk])
                for x, y in zip(a, b, strict=True):
                    d = np.abs(x - y)
                    out.append((int(d.max()), int((d > 0).sum())))
        finally:
            await browser.close()
    return out


def _converted(set_id: str, name: str) -> bool:
    """Whether the library file draws with anything but <path> (a shape converted to arcs or lines)."""
    s = next(x for x in ic.SETS if x.id == set_id)
    _, elements = ic.parse_svg((ic.NODE_MODULES / s.package / s.svg_dir / f"{name}.svg").read_text(encoding="utf-8"))
    return any(tag != "path" for tag, attrs in elements if not (attrs.get("stroke") == "none"))


async def test_every_indexed_icon_draws_the_librarys_pixels() -> None:
    index = json.loads(Path(ic.INDEX_PATH).read_bytes().decode("utf-8"))
    entries = [(s, n, p) for s in ic.SET_ORDER for n, p in sorted(index["sets"][s]["icons"].items())]
    assert len(entries) > 6000
    results = await _compare([(_library_svg(s, n), _stored_svg(p)) for s, n, p in entries])
    exact_bad, aa_bad = [], []
    converted = 0
    for (s, n, _), (worst, count) in zip(entries, results, strict=True):
        if _converted(s, n):
            converted += 1
            if worst > AA_MAX or count > AA_PIXELS:
                aa_bad.append((s, n, worst, count))
        elif worst:
            exact_bad.append((s, n, worst, count))
    assert converted > 700  # the test covers the conversions (circles, rects, lines, polylines ...)
    assert exact_bad == [], exact_bad[:20]
    assert aa_bad == [], aa_bad[:20]


async def test_a_filled_dot_cannot_be_drawn_by_its_stroke() -> None:
    """Why icons.py excludes every fill: Lucide's tag dot (r .5, filled, stroke 2) drawn stroke-only loses its centre
    in Chromium although geometry says the stroke covers it, and the comparison above would see it."""
    lib = _library_svg("lucide", "tag")
    attrs, elements = ic.parse_svg(lib.replace(f'width="{CELL}"', 'width="24"').replace(f'height="{CELL}"',
                                                                                         'height="24"'))
    assert any(a.get("fill") == "currentColor" for _, a in elements)
    stroke_only = [ic.shape_path(tag, {k: v for k, v in a.items() if k != "fill"}) for tag, a in elements]
    (worst, count), = await _compare([(lib, _stored_svg(stroke_only))])
    assert worst > AA_MAX and count > 0, (worst, count)
    assert "tag" in index_excluded("lucide")


def index_excluded(set_id: str) -> dict[str, str]:
    return json.loads(Path(ic.INDEX_PATH).read_bytes().decode("utf-8"))["sets"][set_id]["excluded"]
