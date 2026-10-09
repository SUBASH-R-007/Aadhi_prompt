"""Can this host render MP4s? A cached check with plain-language reasons.

Checks: ``ffmpeg`` and ``ffprobe`` exist, ``ffmpeg -encoders`` lists ``libx264`` and ``aac``,
``ffmpeg -filters`` lists ``subtitles`` (libass, only needed to burn captions in) and, when asked
(``browser=True``), that Playwright's Chromium is installed. The render job checks the tools
before it opens the browser (a missing Chromium is reported by the screenshot step itself), and
``GET /api/render/capabilities`` reports the full result.

Results are cached per tool configuration for :data:`CACHE_TTL_SECONDS` (failures for
:data:`FAILURE_TTL_SECONDS`, so installing a tool takes effect quickly); ``reset_capabilities_cache``
clears the cache. Blocking: call through ``asyncio.to_thread`` from async code (the Chromium
probe uses Playwright's sync API, which refuses to run inside an event loop).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from .ffmpeg import subprocess_env

log = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 300.0
FAILURE_TTL_SECONDS = 30.0
_TOOL_TIMEOUT = 20.0
_lock = threading.Lock()
_cache: dict[tuple[str, str, bool], tuple[float, RenderCapabilities]] = {}


@dataclass
class RenderCapabilities:
    """``ok`` = an MP4 can be rendered here; ``reasons`` say why not, in plain words."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, bool | None] = field(default_factory=dict)  # None = not checked
    burn_captions: bool = False  # the libass ``subtitles`` filter is available
    checked_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly copy."""
        return asdict(self)


def resolve_tool(binary: str) -> str | None:
    """The file ``binary`` (FFMPEG_PATH / FFPROBE_PATH) runs as, else None. A path is taken as written; on
    Windows a path without an extension also finds its ``.exe``, as CreateProcess (the renderer's
    subprocess) does; a bare name is looked up on PATH."""
    if not binary:
        return None
    path = Path(binary)
    if path.is_absolute() or path.parent != Path("."):
        if path.is_file():
            return str(path)
        if sys.platform == "win32" and not path.suffix:  # CreateProcess appends .exe (nothing else of PATHEXT)
            exe = path.with_name(path.name + ".exe")
            if exe.is_file():
                return str(exe)
        return None
    return shutil.which(binary)



def _listing(binary: str, flag: str) -> str | None:
    """``binary flag`` output; None when it could not be run (or timed out)."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([binary, "-hide_banner", flag], capture_output=True, timeout=_TOOL_TIMEOUT,
                              env=subprocess_env(), stdin=subprocess.DEVNULL, check=False, **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("ffmpeg %s failed: %s", flag, type(exc).__name__)
        return None
    return proc.stdout.decode("utf-8", "replace")


def _has_entry(listing: str, name: str) -> bool:
    """True if a ``-encoders`` / ``-filters`` listing has an entry called exactly ``name``."""
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == name:
            return True
    return False


def chromium_installed() -> bool | None:
    """True/False when Playwright can tell whether its Chromium is installed; None if unknown.

    ``playwright install chromium`` installs the full browser and the headless shell; either is
    accepted. Never call from a thread that runs an asyncio event loop.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            exe = Path(pw.chromium.executable_path)
    except Exception as exc:  # noqa: BLE001 - driver could not start: unknown, not "missing"
        log.warning("could not query Playwright's Chromium: %s", type(exc).__name__)
        return None
    if exe.exists():
        return True
    root = exe.parents[2] if len(exe.parents) > 2 else exe.parent
    return any(p.is_dir() for p in root.glob("chromium_headless_shell-*"))


def _check(settings: Settings, browser: bool) -> RenderCapabilities:
    caps = RenderCapabilities(ok=True, checked_at=time.time())
    ffmpeg = resolve_tool(settings.ffmpeg_path)
    ffprobe = resolve_tool(settings.ffprobe_path)
    caps.checks["ffmpeg"] = ffmpeg is not None
    caps.checks["ffprobe"] = ffprobe is not None
    if ffmpeg is None:
        caps.reasons.append(f"ffmpeg was not found ({settings.ffmpeg_path!r}); install it or set FFMPEG_PATH.")
    if ffprobe is None:
        caps.reasons.append(f"ffprobe was not found ({settings.ffprobe_path!r}); install it or set FFPROBE_PATH.")
    if ffmpeg is not None:
        encoders = _listing(ffmpeg, "-encoders")
        if encoders is None:  # not "no encoder": the tool itself failed (cached briefly, like every failure)
            caps.checks["libx264"] = caps.checks["aac"] = None
            caps.reasons.append(f"could not run {settings.ffmpeg_path!r} -encoders (it failed or timed out).")
        else:
            for name in ("libx264", "aac"):
                present = _has_entry(encoders, name)
                caps.checks[name] = present
                if not present:
                    caps.reasons.append(f"this ffmpeg build has no {name} encoder (needed for MP4 video/audio).")
        caps.burn_captions = _has_entry(_listing(ffmpeg, "-filters") or "", "subtitles")
        caps.checks["subtitles"] = caps.burn_captions
    if browser:
        chromium = chromium_installed()
        caps.checks["chromium"] = chromium
        if chromium is False:
            caps.reasons.append("Playwright's Chromium is not installed (run: python -m playwright install chromium).")
    caps.ok = not caps.reasons
    return caps


def render_capabilities(settings: Settings, *, browser: bool = True, force: bool = False) -> RenderCapabilities:
    """Cached capability check (blocking; see the module docstring)."""
    key = (str(settings.ffmpeg_path), str(settings.ffprobe_path), browser)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
    if hit is not None and not force:
        expires, caps = hit
        if now < expires:
            return caps
    caps = _check(settings, browser)
    ttl = CACHE_TTL_SECONDS if caps.ok else FAILURE_TTL_SECONDS
    with _lock:
        _cache[key] = (now + ttl, caps)
    if not caps.ok:
        log.warning("MP4 rendering is unavailable on this host: %s", " ".join(caps.reasons))
    return caps


def reset_capabilities_cache() -> None:
    """Forget cached results (e.g. after installing ffmpeg or Chromium)."""
    with _lock:
        _cache.clear()
