"""Drive the Dynamic render page (stage contract v3, DYNAMIC_VIDEO_DESIGN §7.3) with Playwright.

The page is ``/render-dynamic`` (``web/render-dynamic.html`` + ``web/js/dynamic/render.js``) and exposes::

    window.aadhiStage = {version: 3, ready, info(), mediaPlan(f0, f1), seek(frame), textBoxes(), setLayers(spec)}

This module opens it exactly like the classic capture opens ``/render-frame`` (``aadhi.compose.screenshot``): the same
Chromium flags (``CHROMIUM_ARGS``, plus ``DETERMINISM_ARGS`` for order-independent frames and SwiftShader only when
the plan has WebGL presets, §7.8), the Chromium sandbox,
the network policy (app origin; GET media from the storage's media origins; everything else aborted, a dead proxy for
everything the routing cannot see, WebSockets closed, an off-origin redirect fails the page) and the scoped token in
the URL fragment. Differences, all from §7.4/§7.5: the context is NOT created with ``reduced_motion="reduce"``; CDP
``Animation.setPlaybackRate(0)`` freezes any stray CSS animation; frames are captured with CDP
``Page.captureScreenshot`` (JPEG q92, ``optimizeForSpeed``), never ``page.screenshot``; ``page.clock`` is never used.

The worker already holds the timeline (it picks the route from it), so the page's ``GET /api/render/timeline`` can be
answered from that copy (``served_timeline``) instead of the API: preflight's remedies (§7.12) change the timeline,
and the capture must see the changed one. Requests for media frames (``/__frames/...``, §7.7) go to an optional
``frames`` callback that answers from the worker's workspace (``route.fulfill``).

Console errors and uncaught page errors are collected per page; lines from the timer guard (``guard.js``, §7.4 rule
3: it logs every trip) are also kept apart in ``guard_trips``, so a determinism or golden run can fail on them.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import math
import re
import socket
import struct
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from ...config import Settings
from ..aio import DeadlineExceeded, await_polling
from ..screenshot import CHROMIUM_ARGS, NetworkPolicy, ScreenshotError, missing_browser, origin_of, render_origin

log = logging.getLogger(__name__)

T = TypeVar("T")

STAGE_PATH = "/render-dynamic"
TIMELINE_PATH = "/api/render/timeline"
FRAMES_PREFIX = "/__frames/"
STAGE_VERSION = 3
JPEG_QUALITY = 92  # §7.5
WEBGL_ARGS = ("--use-angle=swiftshader", "--enable-unsafe-swiftshader")  # only when the plan has WebGL presets (§7.8)
# Order-independent frames (§7.4, P2). With partial raster, Chromium re-rasterises only the invalidated part of a tile,
# and Skia's antialiasing of a path over that smaller clip can differ from a full-tile raster by a few levels: the same
# frame then depends on what was painted before it. Measured on the portrait QA fixture (tests/determinism): 76 of 300
# frames differed between 1x300 and reverse (a MathJax glyph in a card that changed colour, <= 3 levels); with this
# flag 0 differ in both formats, at no measurable capture cost (median 73-74 ms per frame either way). Dynamic stage
# only: the classic capture keeps CHROMIUM_ARGS unchanged, so classic output cannot move (design §3.6).
DETERMINISM_ARGS = ("--disable-partial-raster",)
MAX_BOXES = 2000
_MAX_MESSAGES = 50
BOOT_ERROR_GRACE_S = 2.0  # an uncaught error before window.aadhiStage exists fails the load after this long
GUARD_LINE = re.compile(r"aadhi[-_ ]?guard|timer guard", re.IGNORECASE)
_HTTP_STATUS = re.compile(r"HTTP (\d{3})")
# Playwright's words for a page whose renderer died or was closed under a call (not an error of the page's code).
_CRASHED = re.compile(r"target crashed|page crashed|target (?:page, context or browser )?(?:has been )?closed",
                      re.IGNORECASE)
# A CDP call (the frame capture) on a page whose renderer died: it can fail before Playwright's "crash" event fires,
# with words _CRASHED does not cover ("Protocol error (Page.captureScreenshot): Internal error").
_CAPTURE_LOST = re.compile(r"unable to capture screenshot|protocol error \([^)]*\): internal error|session closed|"
                           r"target crashed|target closed", re.IGNORECASE)

_HAS_STAGE_JS = "() => !!window.aadhiStage"
_VERSION_JS = "() => { const s = window.aadhiStage; return s && typeof s === 'object' ? s.version : null; }"
_READY_JS = """async () => {
  try {
    const value = await window.aadhiStage.ready;
    return { ok: true, value: value && typeof value === 'object' ? { webgl: !!value.webgl } : {} };
  } catch (e) {
    return { ok: false, code: String((e && e.code) || 'boot_failed'), message: String((e && e.message) || e) };
  }
}"""
_INFO_JS = "() => window.aadhiStage.info()"
_SEEK_JS = """async (frame) => {
  const r = await window.aadhiStage.seek(frame);
  return r && typeof r === 'object' && r.key !== undefined && r.key !== null ? String(r.key) : null;
}"""
_BOXES_JS = "() => window.aadhiStage.textBoxes()"
_MEDIA_JS = "(a) => window.aadhiStage.mediaPlan(a[0], a[1])"
_LAYERS_JS = "async (spec) => { await window.aadhiStage.setLayers(spec); return true; }"
FATAL_STAGE_CODES = frozenset({"token_missing", "timeline_invalid", "fonts_missing", "stage_contract"})


class StageError(ScreenshotError):
    """The Dynamic render page could not be loaded or broke its contract (``retryable`` as in ScreenshotError)."""


def stage_url(base_url: str, token: str) -> str:
    """The Dynamic render page URL; the scoped token travels in the fragment (never sent to the server)."""
    return f"{base_url.rstrip('/')}{STAGE_PATH}#token={token}"


def _png_1x1() -> bytes:
    """A transparent 1x1 PNG (the stand-in media frame of a preflight: text boxes do not depend on media pixels)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


PLACEHOLDER_FRAME = _png_1x1()


def placeholder_frames(path: str) -> bytes:
    """``frames`` callback that answers every media-frame request with :data:`PLACEHOLDER_FRAME`."""
    return PLACEHOLDER_FRAME


# ---------------------------------------------------------------------------
# info()
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageScene:
    index: int
    f0: int  # first frame
    f1: int  # one past the last frame


@dataclass(frozen=True)
class StageInfo:
    """``aadhiStage.info()``: the stage size, frame rate, frame count and each scene's frame range."""

    width: int
    height: int
    fps: int
    frames: int
    scenes: tuple[StageScene, ...] = ()

    @classmethod
    def parse(cls, raw: Any) -> StageInfo:
        """Validate the page's answer (``StageError``, not retryable, on a malformed one)."""

        def bad(why: str) -> StageError:
            return StageError(f"aadhiStage.info() {why}", code="stage_contract", retryable=False)

        if not isinstance(raw, dict):
            raise bad("did not return an object")

        def integer(key: str, obj: dict[str, Any], low: int = 0) -> int:
            v = obj.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v != int(v):
                raise bad(f"has no integer {key!r}")
            if int(v) < low:
                raise bad(f"has {key}={int(v)}")
            return int(v)

        width, height = integer("width", raw, 1), integer("height", raw, 1)
        fps, frames = integer("fps", raw, 1), integer("frames", raw, 1)
        scenes_raw = raw.get("scenes")
        if not isinstance(scenes_raw, list):
            raise bad("has no scene list")
        scenes: list[StageScene] = []
        for entry in scenes_raw[:10_000]:
            if not isinstance(entry, dict):
                raise bad("has a malformed scene")
            sc = StageScene(integer("index", entry), integer("f0", entry), integer("f1", entry))
            if not sc.f0 < sc.f1 <= frames:
                raise bad(f"has scene {sc.index} with frames {sc.f0}..{sc.f1} outside 0..{frames}")
            scenes.append(sc)
        return cls(width, height, fps, frames, tuple(scenes))

    def scene_at(self, frame: int) -> StageScene | None:
        """The scene whose range holds ``frame`` (the incoming one during a transition)."""
        for sc in self.scenes:
            if sc.f0 <= frame < sc.f1:
                return sc
        return None

    def check(self, *, size: tuple[int, int], fps: int, frames: int) -> None:
        """Raise ``StageError`` (not retryable) when the page laid out another stage than the timeline's."""
        if (self.width, self.height) != tuple(size) or self.fps != fps or self.frames != frames:
            raise StageError(
                f"the stage reports {self.width}x{self.height} at {self.fps} fps with {self.frames} frames; the "
                f"timeline needs {size[0]}x{size[1]} at {fps} fps with {frames} frames",
                code="stage_contract", retryable=False)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


FrameSource = Callable[[str], "Path | bytes | None"]


@dataclass
class _Deadline:
    timeout: float
    check_cancelled: Callable[[], None] | None

    async def __call__(self, aw: Awaitable[T], what: str, *, timeout: float | None = None) -> T:
        limit = self.timeout if timeout is None else timeout
        try:
            return await await_polling(aw, timeout=limit, check_cancelled=self.check_cancelled)
        except DeadlineExceeded:
            raise StageError(f"{what} timed out after {limit:.0f}s", code="render_page_timeout") from None


@dataclass
class StagePage:
    """One loaded Dynamic render page. Every call has a deadline and polls ``check_cancelled``."""

    page: Any
    cdp: Any
    settings: Settings
    deadline: _Deadline
    url: str
    ready_timeout_s: float
    served: dict[str, Any] = field(default_factory=dict)  # {"body": bytes | None}
    errors: list[str] = field(default_factory=list)  # uncaught page errors and console errors (redacted, bounded)
    guard_trips: list[str] = field(default_factory=list)
    webgl: bool | None = None
    stage_info: StageInfo | None = None
    loads: int = 0
    crashed: bool = False  # the renderer process died (Playwright's "crash" event): a fresh page may succeed

    def _note(self, text: str) -> None:
        line = self.settings.redact(" ".join(str(text).split())[:300])
        if GUARD_LINE.search(line) and len(self.guard_trips) < _MAX_MESSAGES:
            self.guard_trips.append(line)
        if len(self.errors) < _MAX_MESSAGES:
            self.errors.append(line)

    def detail(self) -> str:
        return " | page: " + "; ".join(self.errors[-3:]) if self.errors else ""

    async def load(self, served_timeline: dict[str, Any] | None = None) -> StageInfo:
        """(Re)load the page and wait for ``ready``; with ``served_timeline`` the page's timeline request is answered
        with it. Returns ``info()``."""
        if served_timeline is not None:
            self.served["body"] = json.dumps(served_timeline, separators=(",", ":"), ensure_ascii=False).encode()
        self.loads += 1
        self.stage_info = None
        errors_before = self._page_errors()
        try:
            if self.loads > 1:  # the page dropped the fragment: the same URL again would only be a fragment change
                await self.deadline(self.page.goto("about:blank"), "unloading the Dynamic render page")
            response = await self.deadline(self.page.goto(self.url, wait_until="domcontentloaded",
                                                          timeout=self.ready_timeout_s * 1000),
                                           "loading the Dynamic render page", timeout=self.ready_timeout_s + 5)
            status = getattr(response, "status", None)
            if isinstance(status, int) and status >= 400:  # 404 while DYNAMIC_ENABLED is off on the app
                raise StageError(f"GET {STAGE_PATH} answered HTTP {status}", code="render_page_http",
                                 retryable=status >= 500 or status == 429)
            await self._wait_for_stage(errors_before)
        except StageError:
            raise
        except Exception as exc:  # noqa: BLE001 - Playwright timeout/navigation errors
            raise StageError(f"the Dynamic render page never defined window.aadhiStage{self.detail()}",
                             code="render_page_not_ready") from exc
        version = await self.deadline(self.page.evaluate(_VERSION_JS), "reading aadhiStage.version")
        if version != STAGE_VERSION:
            raise StageError(f"aadhiStage.version is {version!r}, this worker speaks {STAGE_VERSION}",
                             code="stage_contract", retryable=False)
        ready = await self.deadline(self.page.evaluate(_READY_JS), "waiting for aadhiStage.ready",
                                    timeout=self.ready_timeout_s + 5)
        if not isinstance(ready, dict) or not ready.get("ok"):
            err = ready if isinstance(ready, dict) else {}
            code = re.sub(r"[^a-z0-9_]", "", str(err.get("code") or "boot_failed").lower())[:40] or "boot_failed"
            message = self.settings.redact(str(err.get("message") or "")[:400])
            retryable = code not in FATAL_STAGE_CODES
            status = _HTTP_STATUS.search(message)
            if code == "timeline_http" and status and status.group(1) in ("400", "401", "403", "404"):
                retryable = False  # as the classic page: a refused timeline does not come back by retrying
            raise StageError(f"the Dynamic render page failed to boot ({code}): {message}",
                             code=f"render_page_{code}", retryable=retryable)
        value = ready.get("value") or {}
        self.webgl = bool(value.get("webgl")) if "webgl" in value else None
        self.stage_info = StageInfo.parse(await self.deadline(self.page.evaluate(_INFO_JS), "aadhiStage.info()"))
        return self.stage_info

    def _page_errors(self) -> int:
        return sum(1 for e in self.errors if e.startswith("pageerror"))

    async def _wait_for_stage(self, errors_before: int) -> None:
        """Poll for ``window.aadhiStage``; an uncaught script error since the load started fails fast (after a short
        grace, in case it was not the page's own module that failed)."""
        loop = asyncio.get_running_loop()
        give_up = loop.time() + self.ready_timeout_s
        first_error: float | None = None
        while True:
            if self.deadline.check_cancelled is not None:
                self.deadline.check_cancelled()
            with contextlib.suppress(Exception):  # navigation in progress (or a busy page): try again
                if await asyncio.wait_for(self.page.evaluate(_HAS_STAGE_JS), timeout=5):
                    return
            now = loop.time()
            if first_error is None and self._page_errors() > errors_before:
                first_error = now
            if first_error is not None and now - first_error > BOOT_ERROR_GRACE_S:
                raise StageError(f"the Dynamic render page's script failed while booting{self.detail()}",
                                 code="render_page_script_error", retryable=False)
            if now > give_up:
                raise StageError(f"the Dynamic render page never defined window.aadhiStage{self.detail()}",
                                 code="render_page_not_ready")
            await asyncio.sleep(0.1)

    async def info(self) -> StageInfo:
        """``aadhiStage.info()`` (cached per load)."""
        if self.stage_info is None:
            self.stage_info = StageInfo.parse(await self.deadline(self.page.evaluate(_INFO_JS), "aadhiStage.info()"))
        return self.stage_info

    async def _call(self, js: str, arg: Any, what: str) -> Any:
        try:
            return await self.deadline(self.page.evaluate(js, arg), what)
        except StageError:
            raise
        except Exception as exc:  # noqa: BLE001 - an exception thrown inside the page (a guard trip, a bug)
            self._note(f"{what}: {exc}")
            msg = self.settings.redact(str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
            if self.crashed or _CRASHED.search(str(exc)):  # the page died, not its code: a fresh page may work
                raise StageError(f"{what} failed: the render page crashed ({msg})", code="stage_crashed",
                                 retryable=True) from exc
            raise StageError(f"{what} failed: {msg}", code="stage_call_failed", retryable=False) from exc

    async def seek(self, frame: int) -> str:
        """``aadhiStage.seek(frame)``; returns the state key (``""`` when the page gave none)."""
        key = await self._call(_SEEK_JS, int(frame), f"aadhiStage.seek({int(frame)})")
        return key if isinstance(key, str) else ""

    async def text_boxes(self) -> list[dict[str, Any]]:
        """``aadhiStage.textBoxes()`` for the current frame (raw dicts; see ``preflight.TextBox``)."""
        raw = await self._call(_BOXES_JS, None, "aadhiStage.textBoxes()")
        if not isinstance(raw, list):
            raise StageError("aadhiStage.textBoxes() did not return a list", code="stage_contract", retryable=False)
        if len(raw) > MAX_BOXES:
            raise StageError(f"aadhiStage.textBoxes() returned {len(raw)} boxes", code="stage_contract",
                             retryable=False)
        return [b for b in raw if isinstance(b, dict)]

    async def media_plan(self, f0: int, f1: int) -> list[dict[str, Any]]:
        """``aadhiStage.mediaPlan(f0, f1)`` (raw dicts)."""
        raw = await self._call(_MEDIA_JS, [int(f0), int(f1)], f"aadhiStage.mediaPlan({f0}, {f1})")
        return [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []

    async def set_layers(self, *, captions: bool = True, stage: bool = True) -> None:
        """``aadhiStage.setLayers({captions, stage})`` (the caption-only pass of a second version, §7.5)."""
        await self._call(_LAYERS_JS, {"captions": bool(captions), "stage": bool(stage)}, "aadhiStage.setLayers()")

    async def _send(self, method: str, params: dict[str, Any] | None, what: str) -> Any:
        """A CDP call on the loaded page; Playwright's protocol errors become retryable ``StageError``s (a renderer
        that died during the capture: with ``--disable-gpu`` raster runs in the renderer while the capture forces a
        frame, so an out-of-memory renderer most likely dies here, not in ``seek()``)."""
        try:
            return await self.deadline(self.cdp.send(method, params) if params is not None else self.cdp.send(method),
                                       what)
        except StageError:
            raise
        except Exception as exc:  # noqa: BLE001 - Playwright protocol errors: the page or its renderer is gone
            self._note(f"{what}: {exc}")
            text = str(exc)
            msg = self.settings.redact(text.splitlines()[0][:300] if text else type(exc).__name__)
            if self.crashed or _CRASHED.search(text) or _CAPTURE_LOST.search(text):
                raise StageError(f"{what} failed: the render page crashed ({msg})", code="stage_crashed",
                                 retryable=True) from exc
            raise StageError(f"{what} failed: {msg}", code="stage_capture_failed", retryable=True) from exc

    async def capture_jpeg(self, quality: int = JPEG_QUALITY) -> bytes:
        """The current frame as CDP JPEG bytes (``optimizeForSpeed``; §7.5)."""
        res = await self._send("Page.captureScreenshot", {"format": "jpeg", "quality": int(quality),
                                                          "optimizeForSpeed": True}, "capturing a frame")
        return base64.b64decode(res["data"])

    async def capture_png(self, *, transparent: bool = False) -> bytes:
        """The current frame as CDP PNG bytes (``optimizeForSpeed``); ``transparent`` clears the default page
        background so a caption-only layer keeps its alpha (§7.5 second version)."""
        if transparent:
            await self._send("Emulation.setDefaultBackgroundColorOverride",
                             {"color": {"r": 0, "g": 0, "b": 0, "a": 0}}, "clearing the background")
        res = await self._send("Page.captureScreenshot", {"format": "png", "optimizeForSpeed": True},
                               "capturing a frame")
        return base64.b64decode(res["data"])


@dataclass
class StageBrowser:
    """One Chromium process (one render worker, §7.6) that opens Dynamic render pages."""

    settings: Settings
    context: Any
    base_url: str
    size: tuple[int, int]
    deadline: _Deadline
    violations: list[str]
    pages: list[StagePage] = field(default_factory=list)

    def check_policy(self) -> None:
        """Raise when a page was redirected off-origin (routing does not see redirect hops)."""
        if self.violations:
            raise StageError(f"render page was redirected off-origin to {self.violations[0]}",
                             code="render_network_policy", retryable=False)

    async def open(self, *, token: str, served_timeline: dict[str, Any] | None = None,
                   frames: FrameSource | None = None, ready_timeout_s: float = 120.0,
                   freeze_css_animations: bool = True) -> StagePage:
        """Open a page, load the stage and wait for ``ready`` (see :meth:`StagePage.load`); a page that fails to come
        up is closed before the error is raised, and only a loaded page joins :attr:`pages`."""
        try:
            page = await self.context.new_page()
        except Exception as exc:  # noqa: BLE001 - the browser died (a recycle after a crash): a retryable failure
            raise _lost(self.settings, exc, "opening a render page") from exc
        sp = StagePage(page=page, cdp=None, settings=self.settings, deadline=self.deadline,
                       url=stage_url(self.base_url, token), ready_timeout_s=ready_timeout_s)
        app_origin = origin_of(self.base_url)

        async def page_route(route: Any, request: Any) -> None:
            url = request.url
            if origin_of(url) == app_origin and request.method == "GET":
                path = urlsplit(url).path
                body = sp.served.get("body")
                if path == TIMELINE_PATH and body is not None:
                    await route.fulfill(status=200, body=body, content_type="application/json",
                                        headers={"Cache-Control": "no-store"})
                    return
                if path.startswith(FRAMES_PREFIX) and frames is not None:
                    found = frames(path)
                    if isinstance(found, bytes):
                        await route.fulfill(status=200, body=found, content_type=_image_type(found),
                                            headers={"Cache-Control": "no-store"})
                    elif isinstance(found, Path):
                        await route.fulfill(status=200, path=str(found), headers={"Cache-Control": "no-store"})
                    else:
                        await route.fulfill(status=404, body=b"", content_type="text/plain")
                    return
            await route.fallback()

        def on_crash(*_: Any) -> None:
            sp.crashed = True
            sp._note("the render page crashed")

        page.on("crash", on_crash)
        page.on("pageerror", lambda err: sp._note(f"pageerror: {err}"))
        page.on("console", lambda msg: sp._note(f"console.{msg.type}: {msg.text}")
                if msg.type in ("error", "assert") else None)
        # A page that fails to come up is closed here: nobody else holds it, and a worker that retries on a fresh page
        # (a chunk retry, a recycle after a crash) would otherwise leave one orphan renderer per failure in its browser.
        try:
            try:
                await page.route("**/*", page_route)
                sp.cdp = await self.context.new_cdp_session(page)
            except Exception as exc:  # noqa: BLE001 - as above
                raise _lost(self.settings, exc, "preparing a render page") from exc
            if freeze_css_animations:  # backstop for stray CSS animation (§7.4 rule 4)
                await sp._send("Animation.enable", None, "Animation.enable")
                await sp._send("Animation.setPlaybackRate", {"playbackRate": 0}, "Animation.setPlaybackRate")
            try:
                await sp.load(served_timeline)
            except StageError as exc:
                if sp.detail() and "| page:" not in str(exc):
                    raise StageError(f"{exc}{sp.detail()}", code=exc.code, retryable=exc.retryable) from exc
                raise
            self.check_policy()
        except BaseException:
            await _close_page(page)
            raise
        self.pages.append(sp)
        return sp


PAGE_CLOSE_TIMEOUT_S = 10.0


async def _close_page(page: Any) -> None:
    """Close a Playwright page, quietly and within :data:`PAGE_CLOSE_TIMEOUT_S` (its browser may be gone)."""
    with contextlib.suppress(Exception):
        await asyncio.wait_for(page.close(), PAGE_CLOSE_TIMEOUT_S)


def _lost(settings: Settings, exc: BaseException, what: str) -> StageError:
    """A Playwright error while setting a page up: the browser or its renderer is gone (retryable on a fresh page;
    a browser that stays dead fails the chunk's second attempt and the job is retried)."""
    text = str(exc)
    msg = settings.redact(text.splitlines()[0][:300] if text else type(exc).__name__)
    return StageError(f"{what} failed: the render browser is gone ({msg})", code="stage_crashed", retryable=True)


def _refusing_socket() -> socket.socket:
    """A bound loopback port that never listens: the dead proxy every non-allowed connection is sent to."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def _image_type(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "application/octet-stream"


@asynccontextmanager
async def stage_browser(
    settings: Settings,
    *,
    size: tuple[int, int],
    base_url: str | None = None,
    media_origins: Iterable[str] = (),
    webgl: bool = False,
    call_timeout_s: float = 60.0,
    check_cancelled: Callable[[], None] | None = None,
    extra_args: Sequence[str] = (),
    playwright: Any | None = None,
) -> AsyncIterator[StageBrowser]:
    """Launch one sandboxed Chromium for Dynamic render pages of ``size`` (the timeline's stage, DPR 1).

    ``base_url`` defaults to :func:`aadhi.compose.screenshot.render_origin` (``RENDER_BASE_URL`` else ``BASE_URL``).
    ``extra_args`` are added to ``CHROMIUM_ARGS`` (experiments such as the benchmark's ``--disable-frame-rate-limit``;
    production adds none). ``playwright`` is an already started Playwright driver (``async_playwright().start()``) to
    launch the browser with, so several workers share one driver process; None starts one for this browser alone.
    Raises :class:`StageError` (``render_unavailable``, not retryable) when Playwright's Chromium is missing.
    """
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency is installed in the venv
        raise StageError("playwright is not installed", code="config", retryable=False) from exc

    app_base = (base_url or render_origin(settings)).rstrip("/")
    policy = NetworkPolicy.build(app_base, media_origins)
    blocked: list[str] = []
    violations: list[str] = []
    deadline = _Deadline(call_timeout_s, check_cancelled)
    width, height = int(size[0]), int(size[1])

    async def route_handler(route: Any, request: Any) -> None:
        if policy.allows(request.url, request.resource_type, request.method):
            await route.continue_()
            return
        if len(blocked) < 50:
            blocked.append(origin_of(request.url))
        await route.abort("blockedbyclient")

    async def ws_handler(ws: Any) -> None:
        await ws.close(code=1008, reason="blocked by the render worker")

    def on_request(request: Any) -> None:
        if request.redirected_from is not None and not policy.allows(request.url, request.resource_type,
                                                                     request.method):
            violations.append(origin_of(request.url))

    dead_proxy = _refusing_socket()
    try:
        async with contextlib.AsyncExitStack() as stack:
            pw = playwright if playwright is not None else await stack.enter_async_context(async_playwright())
            try:
                browser = await pw.chromium.launch(
                    headless=True, chromium_sandbox=True,
                    args=[*CHROMIUM_ARGS, *DETERMINISM_ARGS, *(WEBGL_ARGS if webgl else ()), *extra_args],
                    proxy={"server": f"http://127.0.0.1:{dead_proxy.getsockname()[1]}",
                           "bypass": policy.proxy_bypass()})
            except PlaywrightError as exc:
                if missing_browser(str(exc)):
                    raise StageError("Chromium is not installed on the render worker "
                                     "(run: python -m playwright install chromium)", code="render_unavailable",
                                     retryable=False) from exc
                raise StageError(f"Chromium could not start: {settings.redact(str(exc).splitlines()[0][:300])}",
                                 code="render_unavailable") from exc
            try:
                context = await browser.new_context(
                    viewport={"width": width, "height": height}, screen={"width": width, "height": height},
                    device_scale_factor=1, service_workers="block", accept_downloads=False, color_scheme="dark")
                await context.route("**/*", route_handler)
                await context.route_web_socket("**/*", ws_handler)
                context.on("request", on_request)
                context.set_default_timeout(call_timeout_s * 1000)
                sb = StageBrowser(settings=settings, context=context, base_url=app_base, size=(width, height),
                                  deadline=deadline, violations=violations)
                try:
                    yield sb
                    sb.check_policy()
                finally:
                    with contextlib.suppress(Exception):
                        await context.close()
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()
    except PlaywrightError as exc:
        msg = settings.redact(str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
        raise StageError(f"the Dynamic render page failed: {msg}") from exc
    finally:
        dead_proxy.close()
    if blocked:
        log.info("Dynamic render page: blocked %d off-origin request(s)", len(blocked))


@asynccontextmanager
async def open_stage(
    settings: Settings,
    *,
    size: tuple[int, int],
    token: str,
    served_timeline: dict[str, Any] | None = None,
    base_url: str | None = None,
    media_origins: Sequence[str] = (),
    frames: FrameSource | None = None,
    webgl: bool = False,
    ready_timeout_s: float = 120.0,
    call_timeout_s: float = 60.0,
    check_cancelled: Callable[[], None] | None = None,
    extra_args: Sequence[str] = (),
    playwright: Any | None = None,
) -> AsyncIterator[StagePage]:
    """One browser with one loaded Dynamic render page (``stage_browser`` + ``StageBrowser.open``)."""
    async with stage_browser(settings, size=size, base_url=base_url, media_origins=media_origins, webgl=webgl,
                             call_timeout_s=call_timeout_s, check_cancelled=check_cancelled, extra_args=extra_args,
                             playwright=playwright) as sb:
        page = await sb.open(token=token, served_timeline=served_timeline, frames=frames,
                             ready_timeout_s=ready_timeout_s)
        yield page


async def drain(seconds: float = 0.05) -> None:
    """Let pending page events (console, pageerror) reach their Python handlers."""
    await asyncio.sleep(seconds)
