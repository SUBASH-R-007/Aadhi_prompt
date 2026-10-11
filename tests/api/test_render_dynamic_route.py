"""``GET /render-dynamic``: the motion engine's render page (stage contract v3, DYNAMIC_VIDEO_DESIGN §7.3).

Same token-in-fragment scheme, CSP and no-referrer policy as ``/render-frame``; 404 while ``DYNAMIC_ENABLED`` is off.
The slow test boots the real page in sandboxed Chromium from a live app server (the real static files, fonts,
MathJax and CSP) with a Dynamic timeline built by ``build_timeline``, and checks the contract end to end: ``ready``,
``info()`` against the motion engine's frame plan, frames that do not depend on the order they are painted in, text
boxes inside the safe zones, the media plan, the caption-only layer and the timer guard.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import re
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJzY29wZSI6InJlbmRlciJ9.c2lnbmF0dXJlLXNpZ25hdHVyZQ"


@pytest.fixture()
def client_for(app_env):
    """A TestClient for the real web dir with DYNAMIC_ENABLED on or off."""
    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    clients: list[TestClient] = []

    def make(enabled: bool) -> TestClient:
        reset_rate_limits()
        settings = app_env.model_copy(update={"dynamic_enabled": enabled, "web_dir": REPO / "web"})
        c = TestClient(create_app(settings), base_url="http://testserver")
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def test_the_page_is_hidden_while_dynamic_is_off(client_for) -> None:
    c = client_for(False)
    assert c.get("/render-dynamic").status_code == 404
    assert c.head("/render-dynamic").status_code == 404
    assert c.get("/render-frame").status_code == 200, "the classic page is untouched"


def test_the_page_has_the_render_frame_policies(client_for) -> None:
    from aadhi.security.headers import build_csp

    c = client_for(True)
    r = c.get("/render-dynamic")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["content-security-policy"] == build_csp(c.app.state.settings, kind="app")
    assert "script-src 'self';" in r.headers["content-security-policy"]
    classic = c.get("/render-frame")
    assert classic.headers["content-security-policy"] == r.headers["content-security-policy"]
    assert classic.headers["referrer-policy"] == r.headers["referrer-policy"]
    assert 'data-aadhi-render="dynamic"' in r.text
    assert '<script type="module" src="/web/js/dynamic/render.js"></script>' in r.text
    assert c.head("/render-dynamic").status_code == 200
    # the token travels in the fragment only: the page never reads a query string
    assert c.get("/render-dynamic?token=x").status_code == 200
    for path, ctype in [("/web/js/dynamic/render.js", "text/javascript"), ("/web/js/dynamic/stage.js", "text/javascript"),
                        ("/web/css/dynamic.css", "text/css")]:
        res = c.get(path)
        assert res.status_code == 200, path
        assert res.headers["content-type"].startswith(ctype), path


def test_the_page_is_404_when_the_file_is_missing(app_env, tmp_path) -> None:
    from aadhi.main import create_app

    web = tmp_path / "web"
    web.mkdir()
    settings = app_env.model_copy(update={"dynamic_enabled": True, "web_dir": web})
    with TestClient(create_app(settings), base_url="http://testserver") as c:
        assert c.get("/render-dynamic").status_code == 404


# --- the real page in Chromium -----------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def live_app(app_env, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The real app (web dir of the repository, DYNAMIC_ENABLED on) served by uvicorn on an ephemeral port."""
    pytest.importorskip("playwright.async_api")
    import uvicorn

    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    base = f"http://127.0.0.1:{_free_port()}"
    settings = app_env.model_copy(update={"dynamic_enabled": True, "web_dir": REPO / "web", "base_url": base})
    reset_rate_limits()
    server = uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=int(base.rsplit(":", 1)[1]),
                                           log_level="warning", access_log=False, lifespan="on"))
    thread = threading.Thread(target=server.run, name="uvicorn-dynamic", daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            pytest.fail("the live server did not start")
        time.sleep(0.05)
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=30)
        reset_rate_limits()


def _timeline(fmt: str) -> dict[str, Any]:
    """The compose test screenplay as a Dynamic lecture, every media URL pointed at the stub routes below."""
    from aadhi.compose.timeline import build_timeline, iter_media_refs
    from aadhi.config import Settings
    from aadhi.schemas.screenplay import Screenplay
    from tests.compose.factories import make_manifest, screenplay_dict
    from tests.schemas.v3_fixtures import PRESENTATION

    data = screenplay_dict()
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": "deep_teal",
                            "captions_burn": True, "music": {"source": "procedural", "provider": "procedural"}}
    ids = [s["id"] for s in data["scenes"]]
    data["chapters"] = [{"id": "c1", "title": "Basics", "scene_ids": ids[:2]},
                        {"id": "c2", "title": "The law", "scene_ids": ids[2:]}]
    sp = Screenplay.model_validate(data)
    tl = build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, dynamic_enabled=True))
    for ref in iter_media_refs(tl):
        if ref.asset_key and not ref.url:
            ref.url = f"/__stub_media/{ref.asset_key}.png"
    return tl.model_dump(mode="json")


def _png(size: tuple[int, int], rgb: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, rgb).save(buf, "PNG")
    return buf.getvalue()


def _jpeg(size: tuple[int, int], n: int) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (20, (n * 9) % 255, 120)).save(buf, "JPEG", quality=85)
    return buf.getvalue()


async def _drive(base: str, fmt: str) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    from aadhi.compose.screenshot import CHROMIUM_ARGS

    timeline = _timeline(fmt)
    w, h = timeline["width"], timeline["height"]
    body = json.dumps(timeline).encode()
    out: dict[str, Any] = {"timeline": timeline, "errors": [], "frames_requested": []}

    async def route(r: Any, request: Any) -> None:
        path = re.sub(r"^https?://[^/]+", "", request.url).split("#")[0].split("?")[0]
        if path == "/api/render/timeline":
            out["auth"] = request.headers.get("authorization")
            await r.fulfill(status=200, body=body, content_type="application/json")
        elif path.startswith("/__frames/"):
            out["frames_requested"].append(path)
            key = path.split("/")[2]
            size = tuple(int(x) for x in key.rsplit("-", 1)[1].split("x"))
            await r.fulfill(status=200, body=_jpeg(size, int(path.rsplit("/", 1)[1].split(".")[0])), content_type="image/jpeg")
        elif path.startswith("/__stub_media/"):
            await r.fulfill(status=200, body=_png((640, 360), (200, 120, 40)), content_type="image/png")
        else:
            await r.continue_()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, chromium_sandbox=True, args=list(CHROMIUM_ARGS))
        try:
            ctx = await browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=1, color_scheme="dark")
            page = await ctx.new_page()
            page.on("console", lambda m: out["errors"].append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: out["errors"].append(str(e)))
            await page.route("**/*", route)
            cdp = await ctx.new_cdp_session(page)
            await page.goto(f"{base}/render-dynamic#token={TOKEN}")
            out["ready"] = await page.evaluate("""async () => { try { return {ok: true, v: await window.aadhiStage.ready}; }
                catch (e) { return {ok: false, code: e.code, message: String(e.message)}; } }""")
            out["url"] = page.url
            out["version"] = await page.evaluate("() => window.aadhiStage.version")
            info = await page.evaluate("() => window.aadhiStage.info()")
            out["info"] = info

            async def shot(f: int) -> tuple[str, str]:
                key = await page.evaluate("(f) => window.aadhiStage.seek(f).then(r => r.key)", f)
                res = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92, "optimizeForSpeed": True})
                return key, hashlib.sha256(base64.b64decode(res["data"])).hexdigest()

            picks: list[int] = []
            for s in info["scenes"]:
                picks += [s["f0"], s["f0"] + 6, (s["f0"] + s["f1"]) // 2, s["f1"] - 1]
            picks = sorted(set(p for p in picks if 0 <= p < info["frames"]))
            out["picks"] = picks
            out["forward"] = {f: await shot(f) for f in picks}
            out["reverse"] = {f: await shot(f) for f in reversed(picks)}
            boxes = {}
            for f in picks:
                await page.evaluate("(f) => window.aadhiStage.seek(f)", f)
                boxes[f] = await page.evaluate("() => window.aadhiStage.textBoxes()")
            out["boxes"] = boxes
            out["plan"] = await page.evaluate("() => window.aadhiStage.mediaPlan(0, window.aadhiStage.info().frames)")
            # the caption-only layer: transparent where there is no caption
            await page.evaluate("() => window.aadhiStage.setLayers({stage: false, captions: true})")
            caption_frame = next(f for f in picks if any(b["role"] == "caption" for b in boxes[f]))
            await page.evaluate("(f) => window.aadhiStage.seek(f)", caption_frame)
            await cdp.send("Emulation.setDefaultBackgroundColorOverride", {"color": {"r": 0, "g": 0, "b": 0, "a": 0}})
            png = await cdp.send("Page.captureScreenshot", {"format": "png"})
            out["caption_png"] = base64.b64decode(png["data"])
            await page.evaluate("() => window.aadhiStage.setLayers({stage: true, captions: true})")
            # the timer guard
            out["guard"] = await page.evaluate("""() => { try { setTimeout(() => {}, 0); return 'ran'; }
                catch (e) { return e.code || String(e); } }""")
            out["trips"] = await page.evaluate("() => window.aadhiStageGuard.trips.length")
            await asyncio.sleep(0.1)
        finally:
            await browser.close()
    return out


@pytest.mark.slow
@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
def test_the_stage_contract_in_chromium(live_app: str, fmt: str) -> None:
    from PIL import Image

    from aadhi.compose.base import stage_size
    from aadhi.schemas.timeline import Timeline

    out = asyncio.run(_drive(live_app, fmt))
    assert out["ready"] == {"ok": True, "v": {"webgl": False}}, out["ready"]
    assert out["version"] == 3
    assert out["auth"] == f"Bearer {TOKEN}"
    assert "#" not in out["url"], "the token left the URL"
    info = out["info"]
    assert (info["width"], info["height"]) == stage_size(fmt) and info["fps"] == 30
    try:
        from aadhi.compose.motion.chunks import scene_frame_bounds
    except ImportError:  # the motion engine is built in parallel; the page's own plan is checked below
        scene_frame_bounds = None
    tl = Timeline.model_validate(out["timeline"])
    if scene_frame_bounds is not None:
        assert [(s["f0"], s["f1"]) for s in info["scenes"]] == scene_frame_bounds(tl), "the page and the worker agree"
    assert info["scenes"][0]["f0"] == 0 and info["frames"] == info["scenes"][-1]["f1"]
    # pure frames: the same picture and key whatever was painted before
    for f in out["picks"]:
        assert out["forward"][f] == out["reverse"][f], f"frame {f}"
    # critical text inside the safe zones (R-59), never overflowing
    zones = {"landscape": [(96, 54, 1824, 912), (96, 924, 1824, 1032)],
             "portrait": [(192, 288, 888, 840), (120, 840, 780, 1248)]}[fmt]

    def inside(b: dict[str, Any]) -> bool:
        x0, y0, x1, y1 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]
        if fmt == "portrait" and y0 < 840 < y1:  # a box across y 840 must keep to the narrower column below it
            return x0 >= 192 - 1 and x1 <= 780 + 1 and y0 >= 288 - 1 and y1 <= 1248 + 1
        return any(x0 >= a - 1 and y0 >= b_ - 1 and x1 <= c + 1 and y1 <= d + 1 for a, b_, c, d in zones)

    critical = [(f, b) for f, bs in out["boxes"].items() for b in bs if b["critical"]]
    assert critical
    assert {b["role"] for _, b in critical} >= {"title", "body", "caption"}
    bad = [(f, b) for f, b in critical if not inside(b) and not b["overflow"]]
    assert not bad, bad[:3]
    for _, b in critical:
        assert re.match(r"^#[0-9A-Fa-f]{6}$", b["ink"]) and isinstance(b["overflow"], (int, float))
        assert b["slot"] in ("title", "body", "caption") and b["size"] >= b["min"] > 0
    # the simulation clip: frames planned, requested from the worker, never a <video>
    videos = [e for e in out["plan"] if e["kind"] == "video"]
    assert len(videos) == 1 and videos[0]["asset_key"] == "manim-sim" and videos[0]["srcFps"] == 30
    assert out["frames_requested"], "the page asked the worker for media frames"
    assert all(p.split("/")[2] == videos[0]["key"] for p in out["frames_requested"])
    # the caption-only layer keeps its alpha
    img = Image.open(io.BytesIO(out["caption_png"])).convert("RGBA")
    alpha = img.getchannel("A")
    assert alpha.getextrema()[0] == 0, "transparent outside the caption"
    assert alpha.getextrema()[1] == 255, "the caption itself is opaque"
    # the timer guard trips, logs and throws
    assert out["guard"] == "timer_guard"
    assert out["trips"] == 1
    guard_lines = [e for e in out["errors"] if "timer guard" in e]
    assert guard_lines, out["errors"]
    others = [e for e in out["errors"] if "timer guard" not in e]
    assert not others, others[:5]
