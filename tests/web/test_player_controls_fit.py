"""The player's control bar fits a Dynamic reel's box, in real Chromium (DYNAMIC_VIDEO_DESIGN §2.1, §2.5, §9.7).

A 9:16 reel's box (``data-stage-shape``, ``Player._markStageShape``) is narrow on a desktop page too: 518 px wide on the
watch page at 1920x1080, 342 px at 1366x768, 259 px in the Studio preview. ``web/css/player.css`` (section 12) makes
such a box the ``ap-shaped-box`` size container and fits the bar to the box's own width: the phone bar up to 720 px,
then one part fewer at a time (mute, the time, the scene list, the speed). Play/pause, the seek bar, captions and
fullscreen stay inside the box at every width; the box clips what overflows it (``overflow: hidden``), so a control
past its edge cannot be clicked. Fullscreen (a box as wide as the screen) brings the full bar back. The reel's box is
measured on the small viewport (``100svh``, after the ``100vh`` fallback).

The pages are the real ones: ``web/watch.html`` booted by ``watch.js`` (the real Player, Controls and Dynamic stage),
and the Studio editor's preview pane (``previewPane.js``, which mounts the same Player and Controls) inside the
editor's grid, with the Studio's stylesheets in ``web/index.html``'s order. The lectures are the web tests' fixture
(``web/tests/dynamic/_fixture.js``: a 40 s reel and its classic twin). Nothing listens on the origin: a page route
answers every request, from the repo's ``web/`` (read only) or with the fixture lecture.

A classic lecture's box is untouched: with every reel-only rule taken out of the stylesheets (the ``[data-stage-shape]``
rules and the ``@container ap-shaped-box`` blocks), its box and control bar lay out exactly the same.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from aadhi.compose.screenshot import CHROMIUM_ARGS

pytestmark = pytest.mark.slow

WEB = (Path(__file__).resolve().parents[2] / "web").resolve()
ORIGIN = "http://aadhi.test"
TOKEN = "reel-fit-check"
VERSION_ID = 7
WATCH_BOX = "[data-player]"
STUDIO_BOX = "#side .preview-box"
TYPES = {".html": "text/html", ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
         ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2"}

# controls.js accessible names (the play button while paused).
PLAY, MUTE, CAPTIONS, SPEED, SCENES, FULLSCREEN = ("Play (k)", "Mute (m)", "Captions (c)", "Playback speed", "Scenes",
                                                   "Fullscreen (f)")
DYNAMIC_SIZE, CLASSIC_SIZE = "Caption size: set by this video", "Caption size: Medium"
FULL_BAR = [PLAY, "Previous scene (p)", "Next scene (n)", MUTE, CAPTIONS, DYNAMIC_SIZE, SPEED, SCENES, FULLSCREEN]
ESSENTIAL = (PLAY, "Seek", CAPTIONS, FULLSCREEN)
ONE_LINE = 136  # px: the narrowest bar (play/pause | captions, fullscreen) on one line, with its padding

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CONTAINER_BLOCK = re.compile(r"@container\s+ap-shaped-box\b[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}")
_SHAPED_RULE = re.compile(r"[^{}]*\[data-stage-shape\][^{}]*\{[^{}]*\}")


def reel_bar(width: float) -> list[str]:
    """The buttons (left to right) a reel's bar shows in a box this wide: player.css section 12."""
    if width > 720:
        return list(FULL_BAR)
    names = [PLAY, MUTE, CAPTIONS, SPEED, SCENES, FULLSCREEN]
    for limit, name in ((410, MUTE), (260, SCENES), (220, SPEED)):
        if width <= limit:
            names.remove(name)
    return names


def reel_time_shown(width: float) -> bool:
    return width > 370


def without_reel_rules(css: str) -> tuple[str, int]:
    """The stylesheet with every reel-only rule taken out; how many were."""
    css = _COMMENT.sub("", css)
    css, blocks = _CONTAINER_BLOCK.subn("", css)
    css, rules = _SHAPED_RULE.subn("", css)
    assert "data-stage-shape" not in css and "ap-shaped-box" not in css, "a reel-only rule the test did not take out"
    return css, blocks + rules


def studio_page() -> str:
    """The editor's grid (studio.css .editor-grid) with the preview pane in its side column, as editor.js builds it."""
    index = (WEB / "index.html").read_text(encoding="utf-8")
    sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', index)
    assert "/web/css/studio.css" in sheets and "/web/css/player.css" in sheets, sheets
    links = "".join(f'<link rel="stylesheet" href="{s}">' for s in sheets)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
{links}</head>
<body class="studio-page"><div class="studio-app"><main class="app-main" id="main-content">
<div class="view view-editor"><div class="editor"><div class="editor-grid">
<aside class="pane pane-scenes"></aside><div class="pane pane-inspector"></div>
<aside class="pane pane-side" id="side"></aside>
</div></div></div></main></div>
<script type="module">
import {{ createPreviewPane }} from '/web/js/studio/views/editor/previewPane.js';
try {{
  const getSource = () => ({{ mode: 'built', screenplay: null, blockedReason: null }});
  const pane = createPreviewPane({{ versionId: {VERSION_ID}, getSource }});
  document.getElementById('side').append(pane.el);
  await pane.refresh();
  window.__fit = {{ ok: true }};
}} catch (err) {{
  window.__fit = {{ error: String((err && err.stack) || err) }};
}}
</script></body></html>"""


class Site:
    """Answers every request of the page: the repo's web/ (read only), the pages and the fixture lecture."""

    def __init__(self, timeline: dict[str, Any] | None, *, reel_rules: bool = True) -> None:
        self.timeline = timeline
        self.reel_rules = reel_rules
        self.stripped: dict[str, int] = {}

    async def handle(self, route: Any) -> None:
        url = route.request.url
        if not url.startswith(ORIGIN + "/"):
            await route.abort()
            return
        path = unquote(url[len(ORIGIN):].split("#", 1)[0].split("?", 1)[0])
        if path == "/blank":
            await self._send(route, b"<!doctype html><title>blank</title>", "text/html")
        elif path == f"/watch/{TOKEN}":
            await self._send(route, (WEB / "watch.html").read_bytes(), "text/html")
        elif path == "/studio":
            await self._send(route, studio_page().encode("utf-8"), "text/html")
        elif path == f"/api/public/watch/{TOKEN}":
            body = {"project": {"title": "Ohm's law"}, "share_token": TOKEN, "timeline": self.timeline}
            await self._send(route, json.dumps(body).encode("utf-8"), "application/json")
        elif path == f"/api/versions/{VERSION_ID}/timeline":
            await self._send(route, json.dumps(self.timeline).encode("utf-8"), "application/json")
        elif path.startswith("/api/"):
            await route.fulfill(status=204, body="")  # analytics
        elif (f := self._web_file(path)) is not None:
            data = f.read_bytes()
            if not self.reel_rules and f.suffix == ".css":
                text, count = without_reel_rules(data.decode("utf-8"))
                self.stripped[f.name] = count
                data = text.encode("utf-8")
            await self._send(route, data, TYPES.get(f.suffix, "application/octet-stream"))
        else:
            await route.fulfill(status=404, body="not found")  # e.g. the fixture's /media/ files

    @staticmethod
    def _web_file(path: str) -> Path | None:
        if not path.startswith("/web/"):
            return None
        f = (WEB / path[len("/web/"):]).resolve()
        return f if f.is_file() and f.is_relative_to(WEB) else None

    @staticmethod
    async def _send(route: Any, body: bytes, content_type: str) -> None:
        await route.fulfill(status=200, body=body, headers={"content-type": content_type, "cache-control": "no-store"})


_FONTS_JS = """async () => {
  await document.fonts.load('500 13px "JetBrains Mono"', '0:00 / 0:40');
  await document.fonts.load('12px Inter', '0.75x 1.25x');
  await document.fonts.load('600 12px Inter', 'Aa M');
  await document.fonts.ready;
  return document.fonts.check('500 13px "JetBrains Mono"', '0') && document.fonts.check('12px Inter', 'x');
}"""

# The box and every control a viewer can reach in it (shown: in the layout and not hidden), relative to the box.
_MEASURE_JS = """(sel) => {
  const box = document.querySelector(sel);
  const b = box.getBoundingClientRect();
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const reachable = '.ap-controls button, .ap-controls select, .ap-controls [role="slider"]';
  const controls = [...box.querySelectorAll(reachable)].filter(shown).map((el) => {
    const r = el.getBoundingClientRect();
    return { name: el.getAttribute('aria-label') || el.textContent.trim(), row: !!el.closest('.ap-controls-row'),
             left: r.left - b.left, right: r.right - b.left, top: r.top - b.top, bottom: r.bottom - b.top,
             width: r.width, height: r.height };
  });
  const time = box.querySelector('.ap-time');
  const cs = getComputedStyle(box);
  return { width: b.width, height: b.height, shape: box.dataset.stageShape || null, containerType: cs.containerType,
           containerName: cs.containerName, controls, time: time && shown(time) ? time.textContent : null,
           timeWhole: !!time && time.scrollWidth <= time.clientWidth };
}"""

# The reel's box at each width (an inline width beats the stylesheet's; the height follows the 9:16 aspect ratio) with
# each time text (the paused player leaves it as set); reading the boxes lays the page out, container queries included.
_SWEEP_JS = """({ sel, widths, times }) => {
  const measure = """ + _MEASURE_JS + """;
  const box = document.querySelector(sel);
  const time = box.querySelector('.ap-time');
  const out = [];
  for (const text of times) {
    time.textContent = text;
    for (const w of widths) {
      box.style.width = `${w}px`;
      out.push({ text, w, m: measure(sel) });
    }
  }
  box.style.removeProperty('width');
  return out;
}"""

# Box, frame and control-bar geometry and the styles the reel-only rules set (classic: must not move).
_SNAPSHOT_JS = """(sel) => {
  const box = document.querySelector(sel);
  const bar = '.ap-controls, .ap-controls *, .ap-bigplay, .ap-bigplay *, .ap-drawer, .ap-drawer *';
  const els = [box.parentElement, box, ...box.querySelectorAll(bar)];
  const r2 = (v) => Math.round(v * 100) / 100;
  return els.map((el) => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return [el.tagName, el.getAttribute('class') || '', el.getAttribute('aria-label') || '',
            r2(r.left), r2(r.top), r2(r.width), r2(r.height), cs.display, cs.visibility, cs.containerType,
            cs.containerName, cs.flexWrap, cs.rowGap, cs.marginLeft, cs.padding, cs.overflow, cs.backgroundColor,
            cs.borderRadius, cs.boxShadow];
  });
}"""

_SETTLED_JS = """async () => {
  await new Promise((r) => setTimeout(r, 400));
  const own = (a) => !!(a.effect && a.effect.target && a.effect.target.closest
    && a.effect.target.closest('.ap-controls, .ap-bigplay, .ap-drawer'));
  await Promise.all(document.getAnimations().filter(own).map((a) => a.finished.catch(() => null)));
  return true;
}"""


async def _fixture_lectures(browser: Any) -> dict[str, Any]:
    context = await browser.new_context()
    await context.route("**/*", Site(None).handle)
    page = await context.new_page()
    await page.goto(f"{ORIGIN}/blank")
    lectures = await page.evaluate("""async () => {
      const m = await import('/web/tests/dynamic/_fixture.js');
      return { reel: m.dynamicTimeline('portrait'), classic: m.classicTimeline() };
    }""")
    await context.close()
    return lectures


async def _open(browser: Any, host: str, timeline: dict[str, Any], viewport: tuple[int, int], *,
                reel_rules: bool = True) -> tuple[Any, Any, Site, list[str]]:
    """The watch page or the Studio preview with this lecture, its bar built and the vendored fonts in use."""
    site = Site(timeline, reel_rules=reel_rules)
    context = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, device_scale_factor=1,
                                        color_scheme="dark", service_workers="block")
    await context.route("**/*", site.handle)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if host == "watch":
        await page.goto(f"{ORIGIN}/watch/{TOKEN}")
        await page.wait_for_selector(f"{WATCH_BOX} .ap-controls-row", state="attached", timeout=60_000)
    else:
        await page.goto(f"{ORIGIN}/studio")
        await page.wait_for_function("() => !!window.__fit", timeout=60_000)
        state = await page.evaluate("() => window.__fit")
        assert state.get("ok"), state
        await page.wait_for_selector(f"{STUDIO_BOX} .ap-controls-row", state="attached", timeout=60_000)
    assert await page.evaluate(_FONTS_JS), "the vendored Inter / JetBrains Mono did not load"
    await page.evaluate(_SETTLED_JS)
    return context, page, site, errors


def _check_inside(m: dict[str, Any], where: str) -> None:
    """Every shown control lies inside the box (the box clips the rest); the essentials are shown."""
    names = [c["name"] for c in m["controls"]]
    for essential in ESSENTIAL:
        assert essential in names, f"{where}: {essential!r} is not shown ({names})"
    for c in m["controls"]:
        assert c["width"] > 0 and c["height"] > 0, f"{where}: {c}"
        inside = -0.5 <= c["left"] and c["right"] <= m["width"] + 0.5 and -0.5 <= c["top"]
        size = f"{m['width']:.2f}x{m['height']:.2f}"
        assert inside and c["bottom"] <= m["height"] + 0.5, f"{where}: {c['name']!r} extends past the {size} box: {c}"


def _row_names(m: dict[str, Any]) -> list[str]:
    """The bar's buttons in reading order (line by line, left to right)."""
    row = (c for c in m["controls"] if c["row"])
    return [c["name"] for c in sorted(row, key=lambda c: (round((c["top"] + c["bottom"]) / 2), c["left"]))]


def _one_line(m: dict[str, Any]) -> bool:
    """The bar's buttons sit on one line (centred: the 34 px buttons and the 32 px speed select share a middle)."""
    middles = [(c["top"] + c["bottom"]) / 2 for c in m["controls"] if c["row"]]
    return max(middles) - min(middles) < 1


async def _reel_case(host: str, viewport: tuple[int, int]) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            lectures = await _fixture_lectures(browser)
            context, page, _site, errors = await _open(browser, host, lectures["reel"], viewport)
            sel = WATCH_BOX if host == "watch" else STUDIO_BOX
            out = {"m": await page.evaluate(_MEASURE_JS, sel), "errors": errors}
            if host == "watch":
                out["shaped_width"] = await page.evaluate("""() => {
                  for (const sheet of document.styleSheets) {
                    let rules;
                    try { rules = sheet.cssRules; } catch { continue; }
                    for (const r of rules) {
                      const shaped = r.selectorText === '.watch-player[data-stage-shape]';
                      if (shaped) return r.style.getPropertyValue('width');
                    }
                  }
                  return null;
                }""")
            await context.close()
            return out
        finally:
            await browser.close()


@pytest.fixture(scope="module", autouse=True)
def _vendored_fonts() -> None:
    pytest.importorskip("playwright.async_api")
    if not (WEB / "vendor" / "fonts.css").is_file():
        pytest.skip("web/vendor is generated by `npm install` (the bar was measured with its Inter and JetBrains Mono)")


@pytest.mark.parametrize(("viewport", "box_width"), [
    ((1920, 1080), (1080 - 160) * 9 / 16),  # 517.5: the phone bar, all of it
    ((1366, 768), (768 - 160) * 9 / 16),  # 342: no mute, no time
    ((1366, 657), (657 - 160) * 9 / 16),  # a 1366x768 laptop with the browser's toolbars: 279.6
    ((390, 844), (844 - 160) * 9 / 16),  # a phone (the 640 px query too): 384.75, no mute, the time
    ((360, 800), 360.0),  # no mute, no time
    ((844, 390), (390 - 160) * 9 / 16),  # a phone held sideways: 129.4 px, below the one line's 136 (two lines)
])
def test_reel_bar_stays_in_the_watch_box(viewport: tuple[int, int], box_width: float) -> None:
    out = asyncio.run(_reel_case("watch", viewport))
    m = out["m"]
    assert out["errors"] == []
    assert m["shape"] == "portrait"
    _check_inside(m, f"watch {viewport}")
    assert _row_names(m) == reel_bar(m["width"])
    assert (m["time"] is not None) == reel_time_shown(m["width"]), m["time"]
    assert m["time"] is None or m["timeWhole"]
    assert _one_line(m) == (m["width"] >= ONE_LINE), "the bar wraps only where its one line cannot fit"
    assert (m["containerType"], m["containerName"]) == ("inline-size", "ap-shaped-box")
    assert m["width"] == pytest.approx(box_width, abs=0.5)
    assert m["height"] == pytest.approx(m["width"] * 16 / 9, abs=1)
    # The small-viewport line is the one in use (a mistyped one would be dropped, leaving the 100vh fallback).
    assert out["shaped_width"] and "100svh" in out["shaped_width"], out["shaped_width"]


@pytest.mark.parametrize("viewport", [(1920, 1080), (1366, 768), (1280, 610)])
def test_reel_bar_stays_in_the_studio_preview(viewport: tuple[int, int]) -> None:
    out = asyncio.run(_reel_case("studio", viewport))
    m = out["m"]
    assert out["errors"] == []
    assert m["shape"] == "portrait"
    _check_inside(m, f"studio {viewport}")
    assert _row_names(m) == reel_bar(m["width"])
    assert (m["time"] is not None) == reel_time_shown(m["width"])
    assert _one_line(m)
    assert (m["containerType"], m["containerName"]) == ("inline-size", "ap-shaped-box")
    # studio.css: at most 60% of the window tall (364.5, 259.2 and 205.9 px wide here; the pane is wider).
    assert m["width"] == pytest.approx(viewport[1] * 0.6 * 9 / 16, abs=0.5)


async def _fullscreen_case(host: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    sel = WATCH_BOX if host == "watch" else STUDIO_BOX
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            lectures = await _fixture_lectures(browser)
            context, page, _site, errors = await _open(browser, host, lectures["reel"], (1366, 768))
            before = await page.evaluate(_MEASURE_JS, sel)
            await page.click(f"{sel} button[aria-label='{FULLSCREEN}']")  # a click: the user activation it needs
            await page.wait_for_function(f"() => document.fullscreenElement === document.querySelector('{sel}')")
            await page.evaluate(_SETTLED_JS)
            out = {"before": before, "after": await page.evaluate(_MEASURE_JS, sel), "errors": errors}
            await context.close()
            return out
        finally:
            await browser.close()


@pytest.mark.parametrize("host", ["watch", "studio"])
def test_fullscreen_brings_the_full_bar_back(host: str) -> None:
    out = asyncio.run(_fullscreen_case(host))
    before, after = out["before"], out["after"]
    assert out["errors"] == []
    assert before["width"] < 370 and FULLSCREEN in _row_names(before)  # 342 (watch) / 259 (Studio): a short bar
    assert (after["width"], after["height"]) == (1366, 768)  # the box fills the screen
    after["controls"] = [{**c, "name": FULLSCREEN if c["name"] == "Exit fullscreen (f)" else c["name"]}
                         for c in after["controls"]]
    _check_inside(after, f"{host} fullscreen")
    assert _row_names(after) == FULL_BAR, "the full bar, speed and scene list included"
    assert after["time"] is not None and _one_line(after)


async def _sweep() -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    steps = (220, 260, 370, 410, 720)
    widths = sorted({*range(96, 901, 2), *(s + d for s in steps for d in (-0.5, 0, 0.5, 1))})
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            lectures = await _fixture_lectures(browser)
            context, page, _site, errors = await _open(browser, "watch", lectures["reel"], (1366, 768))
            assert errors == []
            # The fixture's time (under 10:00, as every reel, §2.1), the widest under an hour, and an hour or more.
            rows = await page.evaluate(_SWEEP_JS, {"sel": WATCH_BOX, "widths": widths,
                                                   "times": ["0:00 / 0:40", "59:59 / 59:59", "1:00:00 / 1:00:00"]})
            await context.close()
            return rows
        finally:
            await browser.close()


def test_reel_bar_stays_in_the_box_at_every_width() -> None:
    rows = asyncio.run(_sweep())
    assert len(rows) > 3 * 400
    for row in rows:
        m, where = row["m"], f"{row['w']} px box, time {row['text']!r}"
        assert m["width"] == pytest.approx(row["w"], abs=0.01)
        _check_inside(m, where)
        assert _row_names(m) == reel_bar(row["w"]), where
        assert (m["time"] is not None) == reel_time_shown(row["w"]), where
        if row["text"] != "1:00:00 / 1:00:00":
            # Each step starts before the bar it replaces stops fitting: one line down to the essentials' 136 px,
            # the time whole.
            assert _one_line(m) == (row["w"] >= ONE_LINE), f"{where}: the bar wrapped"
            assert m["time"] is None or m["timeWhole"], where
        # An hour-long time, or a box narrower than the essentials' line (a phone held sideways), may wrap the bar
        # onto a second line, never push a control out of the box (checked above).


async def _classic_case(host: str, viewport: tuple[int, int]) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    sel = WATCH_BOX if host == "watch" else STUDIO_BOX
    out: dict[str, Any] = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            lectures = await _fixture_lectures(browser)
            for rules in (True, False):
                context, page, site, errors = await _open(browser, host, lectures["classic"], viewport,
                                                          reel_rules=rules)
                out[rules] = {"snapshot": await page.evaluate(_SNAPSHOT_JS, sel),
                              "m": await page.evaluate(_MEASURE_JS, sel), "stripped": dict(site.stripped),
                              "errors": errors}
                await context.close()
        finally:
            await browser.close()
    return out


@pytest.mark.parametrize(("host", "viewport"), [
    ("watch", (1920, 1080)), ("watch", (1366, 768)), ("watch", (390, 844)),
    ("studio", (1920, 1080)), ("studio", (1366, 768)),
])
def test_reel_rules_do_not_reach_a_classic_box(host: str, viewport: tuple[int, int]) -> None:
    out = asyncio.run(_classic_case(host, viewport))
    real, bare = out[True], out[False]
    assert real["errors"] == [] and bare["errors"] == []
    assert real["stripped"] == {}
    assert bare["stripped"].get("player.css", 0) > 0, bare["stripped"]
    if host == "studio":
        assert bare["stripped"].get("studio.css", 0) > 0, bare["stripped"]
    m = real["m"]
    assert m["shape"] is None and m["containerType"] == "normal"
    assert real["snapshot"] == bare["snapshot"], "a reel-only rule changed a classic box or its control bar"
    if host == "watch":
        vw, vh = viewport
        # player.css .watch-player, unchanged: 16:9, at most 1400 px and (window - 160 px) tall; full width on a phone.
        expected = vw if vw <= 640 else min(vw - 32, 1400, (vh - 160) * 16 / 9)
        assert m["width"] == pytest.approx(expected, abs=0.5)
        assert m["height"] == pytest.approx(m["width"] * 9 / 16, abs=0.5)
        _check_inside(m, f"classic watch {viewport}")
        if vw > 640:  # the full bar (the viewer's caption size: a classic lecture's captions are the player's)
            assert _row_names(m) == [CLASSIC_SIZE if n == DYNAMIC_SIZE else n for n in FULL_BAR]
