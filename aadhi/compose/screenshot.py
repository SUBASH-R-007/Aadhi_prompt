"""Render-mode screenshots: drive ``/render-frame`` (the player in render mode) with Playwright.

The page contract (``web/render.html``, ARCHITECTURE §9) exposes ``window.aadhiRender``:

* ``ready``: true once the timeline, fonts and MathJax are loaded; ``error`` ({code, message})
  when the page failed to boot (checked together with ``ready``, so boot failures surface at once);
* ``states(sceneIndex) -> [{t, key}]``: scene-relative times where the visual state changes;
* ``show({scene, t}) -> Promise<{state_key, media_rect, panel_media_rect, media_fit, notices}>``
  (``notices``: placeholder texts of side panels the page rendered title-only instead);
* ``showIntro({t})``: intro title cards.

Every page call has a deadline (Playwright's ``evaluate`` has none) and polls ``check_cancelled``
about once a second; pages work in parallel and the first failure cancels the others.

Network: Chromium keeps its sandbox. Requests are allowed to the app origin (``RENDER_BASE_URL``,
an internal address of the app, else ``BASE_URL``: :func:`render_origin`) and, for
GET images/media/fonts only, to the storage's media origins (CDN / presigned S3 URLs that the render
timeline points at); everything else is aborted by ``context.route``. Because routing does not see
redirect hops or WebSockets, Chromium additionally runs behind a proxy that refuses every
connection, bypassed only for the allowed origins, WebSockets are closed by
``context.route_web_socket``, and an off-origin redirect fails the capture. The scoped render token
travels in the URL fragment (never sent to the server by the browser). Screenshots are transparent
PNGs (``omit_background``) at a 1920x1080 viewport, DPR 1.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import socket
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlsplit

from ..config import Settings
from ..schemas.timeline import Timeline
from .aio import DeadlineExceeded, await_polling, gather_or_cancel
from .base import STAGE_HEIGHT, STAGE_WIDTH

log = logging.getLogger(__name__)

T = TypeVar("T")

MAX_STATES_PER_SCENE = 2000
MEDIA_RESOURCE_TYPES = frozenset({"image", "media", "font"})
CHROMIUM_ARGS = (
    "--disable-gpu",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--disable-lcd-text",
    "--hide-scrollbars",
    "--mute-audio",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-extensions",
    "--no-first-run",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
)
# Boot failures are recorded on aadhiRender.error (render.js swallows the rejection): stop waiting on either.
_READY_JS = "() => !!(window.aadhiRender && (window.aadhiRender.ready === true || !!window.aadhiRender.error))"
_ERROR_JS = "() => (window.aadhiRender && window.aadhiRender.ready !== true && window.aadhiRender.error) || null"
_STATES_JS = "(i) => window.aadhiRender.states(i)"
_SHOW_JS = "(a) => window.aadhiRender.show(a)"
_INTRO_JS = "(a) => window.aadhiRender.showIntro(a)"
# Page boot error codes (web/js/render/render.js) that a retry cannot fix.
FATAL_PAGE_CODES = frozenset({"token_missing", "player_api", "player_missing", "timeline_invalid", "fonts_missing"})
_LOCAL_SCHEMES = frozenset({"data", "blob", "about"})
_HTTP_STATUS = re.compile(r"HTTP (\d{3})")
_PROBE_KEY = "assets/render/origin-probe.png"


class ScreenshotError(RuntimeError):
    """The render page could not be loaded or did not honour its contract.

    ``retryable`` is False for deterministic failures (bad deployment, contract violations).
    """

    def __init__(self, message: str, *, code: str = "screenshot_failed", retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class StateShot:
    """One visual state of a scene (scene-relative ``t``)."""

    t: float
    key: str
    png_path: Path
    media_rect: dict[str, float] | None = None
    panel_media_rect: dict[str, float] | None = None
    media_fit: str | None = None  # fit of the MAIN media element only (render page contract)
    notices: tuple[str, ...] = ()  # placeholder texts the page replaced by a title-only panel

    def as_dict(self) -> dict[str, Any]:
        """Plain dict (debugging / logs)."""
        return {"t": self.t, "key": self.key, "png_path": self.png_path, "media_rect": self.media_rect,
                "panel_media_rect": self.panel_media_rect, "media_fit": self.media_fit,
                "notices": list(self.notices)}


@dataclass
class IntroShot:
    """One intro title-card screenshot."""

    t: float  # intro-relative time used for showIntro
    png_path: Path


@dataclass
class CaptureResult:
    """Screenshots per scene index + intro, and what the network policy blocked (origins only)."""

    scenes: dict[int, list[StateShot]] = field(default_factory=dict)
    intro: list[IntroShot] = field(default_factory=list)
    blocked_requests: list[str] = field(default_factory=list)
    blocked_media: list[str] = field(default_factory=list)  # origins of blocked images/media/fonts
    websocket_attempts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def origin_of(url: str) -> str:
    """Normalised ``scheme://host:port`` origin of ``url`` (IPv6 hosts in brackets)."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "invalid://"
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if ":" in host:
        host = f"[{host}]"
    port = port or {"http": 80, "https": 443, "ws": 80, "wss": 443}.get(scheme)
    return f"{scheme}://{host}:{port}"


def _is_http_origin(origin: str) -> bool:
    scheme, _, rest = origin.partition("://")
    host = rest.rsplit(":", 1)[0]
    return scheme in ("http", "https") and bool(host) and not rest.endswith(":None")


def render_url(base_url: str, token: str, *, glass: bool = False) -> str:
    """The render page URL; the token is in the fragment so it never reaches access logs.

    ``glass`` asks the page for translucent glass panels (``RENDER_GLASS_PANELS``).
    """
    return f"{base_url.rstrip('/')}/render-frame#token={token}" + ("&glass=1" if glass else "")


def render_origin(settings: Settings) -> str:
    """Base URL the render worker's browser uses for the app: ``RENDER_BASE_URL`` (an internal
    address such as ``http://api:8000``), else the public ``BASE_URL``."""
    internal = (getattr(settings, "render_base_url", "") or "").strip()
    return (internal or settings.base_url).rstrip("/")


_MISSING_BROWSER = ("executable doesn't exist", "playwright install")


def missing_browser(message: str) -> bool:
    """True for Playwright's "browser is not installed" launch error."""
    low = (message or "").lower()
    return any(marker in low for marker in _MISSING_BROWSER)


def _notices(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out = [" ".join(str(v).split())[:200] for v in value[:10] if isinstance(v, str) and v.strip()]
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class NetworkPolicy:
    """What the render page may load: anything from the app origin; GET media from media origins."""

    app_origin: str
    media_origins: frozenset[str] = frozenset()

    @classmethod
    def build(cls, base_url: str, media_origins: Iterable[str] = ()) -> NetworkPolicy:
        """Policy for ``base_url`` plus the absolute http(s) ``media_origins`` (URLs or origins)."""
        app = origin_of(base_url)
        media = {origin_of(m) for m in media_origins if m}
        return cls(app, frozenset(o for o in media if o != app and _is_http_origin(o)))

    def allows(self, url: str, resource_type: str = "other", method: str = "GET") -> bool:
        """True if the page may load ``url`` (``resource_type`` as reported by Playwright)."""
        scheme = urlsplit(url).scheme.lower() if url else ""
        if scheme in _LOCAL_SCHEMES:
            return True
        origin = origin_of(url)
        if origin == self.app_origin:
            return True
        return method.upper() == "GET" and resource_type in MEDIA_RESOURCE_TYPES and origin in self.media_origins

    def proxy_bypass(self) -> str:
        """Chromium proxy bypass list: the allowed origins only (loopback is proxied as well)."""
        return ",".join(["<-loopback>", self.app_origin, *sorted(self.media_origins)])


def media_origins_for(storage: Any, settings: Settings) -> list[str]:
    """Absolute origins the served timeline's media URLs use (CDN, presigned S3); blocking."""
    urls: list[str] = [settings.cdn_base_url]
    for make in (lambda: storage.public_url(_PROBE_KEY), lambda: storage.signed_url(_PROBE_KEY, 60)):
        try:
            urls.append(str(make()))
        except Exception as exc:  # noqa: BLE001 - e.g. S3 credentials missing: fall back to the settings
            log.debug("media origin probe failed: %s", type(exc).__name__)
    if settings.storage_backend == "s3" and settings.s3_endpoint_url:
        urls.append(settings.s3_endpoint_url)
    origins = {origin_of(u) for u in urls if u and urlsplit(u).scheme in ("http", "https")}
    return sorted(o for o in origins if _is_http_origin(o))


def page_error(error: Any, settings: Settings) -> ScreenshotError:
    """ScreenshotError for an ``aadhiRender.error`` object (deterministic codes are not retryable)."""
    err = error if isinstance(error, dict) else {}
    code = re.sub(r"[^a-z0-9_]", "", str(err.get("code") or "boot_failed").lower())[:40] or "boot_failed"
    message = settings.redact(str(err.get("message") or "")[:400])
    retryable = code not in FATAL_PAGE_CODES
    status = _HTTP_STATUS.search(message)
    if code == "timeline_http" and status and status.group(1) in ("400", "401", "403", "404"):
        retryable = False
    return ScreenshotError(f"render page failed to boot ({code}): {message}", code=f"render_page_{code}",
                           retryable=retryable)


def _rect(value: Any) -> dict[str, float] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, float] = {}
    for key in ("x", "y", "left", "top", "width", "height", "w", "h"):
        v = value.get(key)
        if isinstance(v, (int, float)) and math.isfinite(v):
            out[key] = float(v)
    return out or None


def normalize_states(raw: Any, duration: float) -> list[tuple[float, str]]:
    """Validate ``states()`` output: sorted, clamped, consecutive duplicates merged, starts at 0.

    More than :data:`MAX_STATES_PER_SCENE` states is a hard (non-retryable) error, never a silent
    truncation.
    """
    if not isinstance(raw, list):
        raise ScreenshotError("aadhiRender.states() did not return a list", code="render_page_contract",
                              retryable=False)
    if len(raw) > MAX_STATES_PER_SCENE * 4:
        raise ScreenshotError(f"aadhiRender.states() returned {len(raw)} entries", code="too_many_states",
                              retryable=False)
    items: list[tuple[float, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        t = entry.get("t")
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t):
            continue
        key = str(entry.get("key", ""))[:200] or f"t{t:.3f}"
        items.append((min(max(0.0, float(t)), max(0.0, duration - 1e-3)), key))
    items.sort(key=lambda x: x[0])
    out: list[tuple[float, str]] = []
    for t, key in items:
        if out and (key == out[-1][1] or abs(t - out[-1][0]) < 1e-3):
            if abs(t - out[-1][0]) < 1e-3:
                out[-1] = (out[-1][0], key)  # same instant: the later state wins
            continue
        out.append((t, key))
    if not out or out[0][0] > 1e-3:
        out.insert(0, (0.0, "__scene_start"))
    if len(out) > MAX_STATES_PER_SCENE:
        raise ScreenshotError(f"scene has {len(out)} visual states (limit {MAX_STATES_PER_SCENE})",
                              code="too_many_states", retryable=False)
    return out


# ---------------------------------------------------------------------------
# Browser driving
# ---------------------------------------------------------------------------


@dataclass
class _Guard:
    """Deadline + cancel polling for one page call."""

    timeout: float
    check_cancelled: Callable[[], None] | None

    async def __call__(self, aw: Awaitable[T], what: str, *, timeout: float | None = None) -> T:
        try:
            return await await_polling(aw, timeout=self.timeout if timeout is None else timeout,
                                       check_cancelled=self.check_cancelled)
        except DeadlineExceeded:
            raise ScreenshotError(f"{what} timed out after {self.timeout if timeout is None else timeout:.0f}s",
                                  code="render_page_timeout") from None


async def _capture_scene(page: Any, index: int, duration: float, out_dir: Path, guard: _Guard,
                         check_cancelled: Callable[[], None] | None) -> list[StateShot]:
    states = normalize_states(await guard(page.evaluate(_STATES_JS, index), f"aadhiRender.states({index})"),
                              duration)
    shots: list[StateShot] = []
    by_key: dict[str, StateShot] = {}
    for n, (t, key) in enumerate(states):
        if check_cancelled:
            check_cancelled()
        cached = by_key.get(key)
        if cached is not None:  # same visual state again: reuse its PNG (one file per distinct key)
            shots.append(StateShot(t, key, cached.png_path, cached.media_rect, cached.panel_media_rect,
                                   cached.media_fit, cached.notices))
            continue
        res = await guard(page.evaluate(_SHOW_JS, {"scene": index, "t": t}),
                          f"aadhiRender.show at scene {index} t={t:.3f}")
        res = res if isinstance(res, dict) else {}
        path = out_dir / f"s{index:03d}_{n:04d}.png"
        await guard(page.screenshot(path=str(path), type="png", omit_background=True, animations="disabled",
                                    caret="hide"), f"screenshot of scene {index} t={t:.3f}")
        fit = res.get("media_fit")
        shot = StateShot(t, key, path, _rect(res.get("media_rect")), _rect(res.get("panel_media_rect")),
                         fit if fit in ("contain", "cover") else None, _notices(res.get("notices")))
        by_key[key] = shot
        shots.append(shot)
    return shots


def _refusing_socket() -> socket.socket:
    """A bound loopback port that never listens: connections to it are refused (the dead proxy)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


async def capture_render_frames(
    *,
    settings: Settings,
    token: str,
    timeline: Timeline,
    out_dir: Path,
    scene_indices: Sequence[int] | None = None,
    intro_times: Sequence[float] = (),
    pages: int = 2,
    media_origins: Sequence[str] = (),
    ready_timeout_s: float = 120.0,
    call_timeout_s: float = 60.0,
    on_scene_done: Callable[[int, int], None] | None = None,
    check_cancelled: Callable[[], None] | None = None,
) -> CaptureResult:
    """Screenshot every visual state of the requested scenes (and the intro cards).

    Scenes are distributed over ``pages`` browser pages working in parallel. ``media_origins``
    (see :func:`media_origins_for`) may serve images/media/fonts. ``on_scene_done`` receives
    ``(done, total)``. Raises :class:`ScreenshotError` on page/contract/network-policy failures and
    whatever ``check_cancelled`` raises.
    """
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover - dependency is installed in the venv
        raise ScreenshotError("playwright is not installed", code="config", retryable=False) from exc

    await asyncio.to_thread(out_dir.mkdir, parents=True, exist_ok=True)
    indices = list(range(len(timeline.scenes)) if scene_indices is None else scene_indices)
    app_base = render_origin(settings)
    policy = NetworkPolicy.build(app_base, media_origins)
    result = CaptureResult()
    page_errors: list[str] = []
    console_errors: list[str] = []
    violations: list[str] = []
    url = render_url(app_base, token, glass=bool(getattr(settings, "render_glass_panels", False)))
    guard = _Guard(call_timeout_s, check_cancelled)

    def detail() -> str:
        msgs = [*page_errors[-2:], *console_errors[-2:]]
        return settings.redact(" | page: " + "; ".join(msgs)) if msgs else ""

    async def route_handler(route: Any, request: Any) -> None:
        if policy.allows(request.url, request.resource_type, request.method):
            await route.continue_()
            return
        origin = origin_of(request.url)
        if len(result.blocked_requests) < 50:
            result.blocked_requests.append(origin)
        if request.resource_type in MEDIA_RESOURCE_TYPES and origin not in result.blocked_media:
            result.blocked_media.append(origin)
        await route.abort("blockedbyclient")

    async def ws_handler(ws: Any) -> None:
        if len(result.websocket_attempts) < 20:
            result.websocket_attempts.append(origin_of(ws.url))
        await ws.close(code=1008, reason="blocked by the render worker")

    def on_request(request: Any) -> None:
        if request.redirected_from is not None and not policy.allows(request.url, request.resource_type,
                                                                     request.method):
            violations.append(origin_of(request.url))

    def on_console(msg: Any) -> None:
        if msg.type == "error" and len(console_errors) < 50:
            console_errors.append(str(msg.text)[:300])

    def check_policy() -> None:
        if violations:
            raise ScreenshotError(f"render page was redirected off-origin to {violations[0]}",
                                  code="render_network_policy", retryable=False)

    dead_proxy = _refusing_socket()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True, chromium_sandbox=True, args=list(CHROMIUM_ARGS),
                proxy={"server": f"http://127.0.0.1:{dead_proxy.getsockname()[1]}", "bypass": policy.proxy_bypass()})
            try:
                context = await browser.new_context(
                    viewport={"width": STAGE_WIDTH, "height": STAGE_HEIGHT},
                    screen={"width": STAGE_WIDTH, "height": STAGE_HEIGHT},
                    device_scale_factor=1,
                    service_workers="block",
                    accept_downloads=False,
                    reduced_motion="reduce",
                    color_scheme="dark",
                )
                await context.route("**/*", route_handler)
                await context.route_web_socket("**/*", ws_handler)
                context.set_default_timeout(call_timeout_s * 1000)

                async def open_page() -> Any:
                    page = await context.new_page()
                    page.on("pageerror", lambda err: page_errors.append(str(err)[:300]))
                    page.on("console", on_console)
                    page.on("request", on_request)
                    await guard(page.goto(url, wait_until="domcontentloaded", timeout=ready_timeout_s * 1000),
                                "loading the render page", timeout=ready_timeout_s + 5)
                    try:
                        handle = await guard(page.wait_for_function(_READY_JS, timeout=ready_timeout_s * 1000,
                                                                    polling=100),
                                             "waiting for the render page", timeout=ready_timeout_s + 5)
                        await handle.dispose()
                    except PlaywrightError as exc:
                        raise ScreenshotError(f"render page never became ready{detail()}",
                                              code="render_page_not_ready") from exc
                    error = await guard(page.evaluate(_ERROR_JS), "reading aadhiRender.error")
                    if error:
                        raise page_error(error, settings)
                    return page

                n_pages = max(1, min(pages, len(indices) or 1))
                opened = await gather_or_cancel([open_page() for _ in range(n_pages)])
                queue: asyncio.Queue[int] = asyncio.Queue()
                for i in indices:
                    queue.put_nowait(i)
                done = 0

                async def worker(page: Any) -> None:
                    nonlocal done
                    while True:
                        try:
                            i = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            return
                        result.scenes[i] = await _capture_scene(page, i, timeline.scenes[i].duration, out_dir, guard,
                                                                check_cancelled)
                        check_policy()
                        done += 1
                        if on_scene_done:
                            on_scene_done(done, len(indices))

                await gather_or_cancel([worker(p) for p in opened])
                page = opened[0]
                for n, t in enumerate(intro_times):
                    if check_cancelled:
                        check_cancelled()
                    await guard(page.evaluate(_INTRO_JS, {"t": float(t)}), f"aadhiRender.showIntro t={t:.3f}")
                    path = out_dir / f"intro_{n:02d}.png"
                    await guard(page.screenshot(path=str(path), type="png", omit_background=True,
                                                animations="disabled", caret="hide"), "intro screenshot")
                    result.intro.append(IntroShot(float(t), path))
                check_policy()
                await context.close()
            finally:
                await browser.close()
    except PlaywrightError as exc:
        if missing_browser(str(exc)):  # a deployment problem: retrying cannot help
            raise ScreenshotError("Chromium is not installed on the render worker "
                                  "(run: python -m playwright install chromium)", code="render_unavailable",
                                  retryable=False) from exc
        msg = settings.redact(str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__)
        raise ScreenshotError(f"render-mode screenshot failed: {msg}{detail()}") from exc
    except ScreenshotError as exc:
        if detail() and "| page:" not in str(exc):
            raise ScreenshotError(f"{exc}{detail()}", code=exc.code, retryable=exc.retryable) from exc
        raise
    finally:
        dead_proxy.close()
    if result.blocked_requests:
        log.info("render page: blocked %d off-origin request(s)", len(result.blocked_requests))
    return result
