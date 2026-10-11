"""One capture worker on the Dynamic render stage (``/render-dynamic``, stage contract v3).

The page driver itself is :mod:`aadhi.compose.dynamic.stage_page` (shared with the preflight and the determinism
checks): the classic capture's Chromium flags, sandbox and network policy, the token in the URL fragment, no
``reduced_motion="reduce"``, CDP ``Animation.setPlaybackRate(0)`` as the backstop for stray CSS animation, no
``page.clock``, frames captured with CDP ``Page.captureScreenshot`` (JPEG q92, ``optimizeForSpeed``) [MR C11]. The
page's timeline request is answered with the timeline the worker holds (``served``: after the preflight's remedies).
With presigned storage URLs (S3 without a CDN) ``refresh_served`` signs them again for every page the session opens
(start, recycling, a chunk retry, a remedy), so a page opened late in a long render never loads expired links.

A :class:`StageSession` adds what a capture worker needs on top of it (DYNAMIC_VIDEO_DESIGN §7.6, §7.7):

* its own ``chrome-headless-shell`` process and one page at a time; the Playwright driver (a node process) is
  shared by the render's workers when the engine passes one (``playwright``), else the session starts its own;
* page recycling (:meth:`StageSession.recycle`: a fresh page in the same browser, the old one closed);
* the media frames of the chunk being captured: ``GET <app>/__frames/[<chunk>/]<media key>/<source frame>.jpg`` is
  answered by ``route.fulfill`` from :class:`FrameRegistry` (frames extracted into the worker's workspace for that
  chunk) and never reaches the server; anything else under ``/__frames/`` is a 404. The ``<chunk>`` segment is
  optional and only informative; ``<source frame>`` is the ``srcIndex`` the page listed in ``mediaPlan()``;
* the Chromium version (part of the chunk cache key).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ...config import Settings
from ..dynamic.stage_page import (
    DETERMINISM_ARGS,
    JPEG_QUALITY,
    WEBGL_ARGS,
    StageBrowser,
    StageError,
    StageInfo,
    StagePage,
    stage_browser,
)
from ..screenshot import CHROMIUM_ARGS

# /__frames/[<chunk>/]<media key>/<source frame index>.jpg
_FRAMES_RE = re.compile(r"^/__frames/(?:[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}/)?([^/]{1,256})/(\d{1,7})\.jpg$")
_MEDIA_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")

__all__ = ["FrameRegistry", "StageError", "StageInfo", "StageOptions", "StageSession", "scene_mismatch"]


class FrameRegistry:
    """Media frames a worker may serve at ``/__frames/...``: ``(media key, source frame) -> file``."""

    def __init__(self) -> None:
        self._files: dict[tuple[str, int], Path] = {}
        self.served = 0
        self.missing = 0

    def set(self, files: Mapping[tuple[str, int], Path]) -> None:
        """Serve exactly ``files`` from now on (the chunk being captured)."""
        self._files = dict(files)

    def clear(self) -> None:
        """Serve nothing."""
        self._files = {}

    def get(self, key: str, index: int) -> Path | None:
        """The file of ``key``'s source frame ``index``, if extracted."""
        return self._files.get((key, index))

    @staticmethod
    def parse_path(path: str) -> tuple[str, int] | None:
        """``(media key, source frame)`` of a ``/__frames/...`` path; None when it is malformed."""
        m = _FRAMES_RE.match(path or "")
        if not m:
            return None
        key = unquote(m.group(1))
        if not _MEDIA_KEY_RE.match(key):
            return None
        return key, int(m.group(2))

    def __call__(self, path: str) -> Path | None:
        """The ``frames`` callback of :meth:`StageBrowser.open` (None = 404)."""
        parsed = self.parse_path(path)
        file = self.get(*parsed) if parsed is not None else None
        if file is None:
            self.missing += 1
            return None
        self.served += 1
        return file


@dataclass
class StageOptions:
    """How a capture worker's browser and page are set up (the parts that are not settings)."""

    webgl: bool = False  # SwiftShader WebGL flags (plans with shader presets only, W2)
    jpeg_quality: int = JPEG_QUALITY
    ready_timeout: float = 120.0
    call_timeout: float = 60.0
    layers: dict[str, bool] = field(default_factory=lambda: {"captions": False, "stage": True})
    extra_args: tuple[str, ...] = ()  # added Chromium flags (benchmark experiments only; production adds none)

    def chromium_args(self) -> list[str]:
        """The Chromium flags the page driver launches with (part of the chunk cache key)."""
        return [*CHROMIUM_ARGS, *DETERMINISM_ARGS, *(WEBGL_ARGS if self.webgl else ()), *self.extra_args]

    def capture_fingerprint(self) -> dict[str, Any]:
        """What changes the captured pixels besides the page and the timeline (part of the chunk cache key)."""
        return {"format": "jpeg", "quality": int(self.jpeg_quality), "args": self.chromium_args(),
                "layers": dict(sorted(self.layers.items()))}


def scene_mismatch(info: StageInfo, bounds: Sequence[tuple[int, int]]) -> str | None:
    """Why the page's scene frame ranges differ from the timeline's (None when they agree frame by frame). A page
    that lists no scenes for a timeline that has some disagrees too: an empty list would leave the layout unchecked
    but for its total frame count."""
    got = [(s.f0, s.f1) for s in sorted(info.scenes, key=lambda s: s.index)]
    if not got and bounds:
        return "the stage reports no scene frame ranges"
    if got != list(bounds):
        return "the stage's scene frame ranges differ from the timeline's"
    return None


class StageSession:
    """One capture worker: a browser with one stage page (see the module docstring)."""

    def __init__(self, *, settings: Settings, size: tuple[int, int], token: str, served: Mapping[str, Any] | None,
                 base_url: str | None = None, media_origins: Sequence[str] = (), options: StageOptions | None = None,
                 check_cancelled: Callable[[], None] | None = None, label: str = "worker",
                 playwright: Any | None = None,
                 refresh_served: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.settings = settings
        self.size = (int(size[0]), int(size[1]))
        self.token = token
        self.served = dict(served) if served is not None else None
        self.base_url = base_url
        self.media_origins = list(media_origins)
        self.options = options or StageOptions()
        self.check_cancelled = check_cancelled
        self.label = label
        self.playwright = playwright  # a Playwright driver shared by the render's workers (None: one of its own)
        # the served timeline signed again (blocking; run in a thread) before every page load: presigned URLs expire
        self.refresh_served = refresh_served
        self.frames = FrameRegistry()
        self.chromium_version = ""
        self.pages_opened = 0
        self.errors: list[str] = []
        self.guard_trips: list[str] = []
        self._cm: Any = None
        self._browser: StageBrowser | None = None
        self._page: StagePage | None = None

    @property
    def page(self) -> StagePage:
        """The current stage page."""
        if self._page is None:
            raise StageError("the stage page is not open", code="stage_closed")
        return self._page

    @property
    def webgl(self) -> bool | None:
        """What the page's WebGL probe reported (None before ready)."""
        return self._page.webgl if self._page is not None else None

    async def start(self) -> StageSession:
        """Launch the browser and open the stage page (waits for ``ready``)."""
        self._cm = stage_browser(self.settings, size=self.size, base_url=self.base_url,
                                 media_origins=self.media_origins, webgl=self.options.webgl,
                                 call_timeout_s=self.options.call_timeout, check_cancelled=self.check_cancelled,
                                 extra_args=self.options.extra_args, playwright=self.playwright)
        self._browser = await self._cm.__aenter__()
        try:
            browser = getattr(self._browser.context, "browser", None)
            self.chromium_version = str(getattr(browser, "version", "") or "")
            await self.open_page()
        except BaseException:
            await self.close()
            raise
        return self

    async def open_page(self) -> None:
        """Open a fresh stage page (and close the previous one): page recycling, §7.6."""
        assert self._browser is not None
        old, self._page = self._page, None
        if old is not None:
            self._keep_messages(old)
            with contextlib.suppress(Exception):
                await old.page.close()
            with contextlib.suppress(ValueError):
                self._browser.pages.remove(old)
        if self.refresh_served is not None:
            try:
                self.served = dict(await asyncio.to_thread(self.refresh_served))
            except Exception as exc:  # noqa: BLE001 - signing the links failed (storage): a later attempt may work
                raise StageError(f"the lecture's media links could not be signed ({type(exc).__name__})",
                                 code="stage_refresh_failed", retryable=True) from exc
        page = await self._browser.open(token=self.token, served_timeline=self.served, frames=self.frames,
                                        ready_timeout_s=self.options.ready_timeout)
        self._page = page
        self.pages_opened += 1
        await page.set_layers(captions=bool(self.options.layers.get("captions", False)),
                              stage=bool(self.options.layers.get("stage", True)))

    async def recycle(self) -> None:
        """A fresh page for the next chunk (after 1,800 frames, a slowdown, or a failed chunk)."""
        await self.open_page()

    def _keep_messages(self, page: StagePage) -> None:
        for line in page.errors:
            if len(self.errors) < 50:
                self.errors.append(line)
        for line in page.guard_trips:
            if len(self.guard_trips) < 50:
                self.guard_trips.append(line)

    def messages(self) -> tuple[list[str], list[str]]:
        """Page errors and timer-guard trips of every page this worker opened."""
        errors, trips = list(self.errors), list(self.guard_trips)
        if self._page is not None:
            errors += self._page.errors
            trips += self._page.guard_trips
        return errors[:50], trips[:50]

    async def close(self) -> None:
        """Close the browser (idempotent). A redirect off the allowed origins seen meanwhile raises ``StageError``."""
        if self._page is not None:
            self._keep_messages(self._page)
        cm, self._cm, self._browser, self._page = self._cm, None, None, None
        if cm is not None:
            await cm.__aexit__(None, None, None)

    async def __aenter__(self) -> StageSession:
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    # --- the contract calls the engine makes ---------------------------------------------------

    async def info(self) -> StageInfo:
        """``aadhiStage.info()``."""
        return await self.page.info()

    async def media_plan(self, f0: int, f1: int) -> list[dict[str, Any]]:
        """``aadhiStage.mediaPlan(f0, f1)`` (raw; validated by ``media.parse_media_plan``)."""
        return await self.page.media_plan(f0, f1)

    async def seek(self, frame: int) -> str:
        """``aadhiStage.seek(frame)``; the state key."""
        return await self.page.seek(frame)

    async def capture_jpeg(self) -> bytes:
        """The current frame as JPEG bytes (CDP ``Page.captureScreenshot``)."""
        return await self.page.capture_jpeg(self.options.jpeg_quality)
