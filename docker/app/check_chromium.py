"""Verify that sandboxed Chromium works in this container (render-worker self-check).

    docker compose run --rm --no-deps render-worker python /app/docker/app/check_chromium.py

Launches Playwright's Chromium exactly like the render worker (headless, ``chromium_sandbox=True``)
and screenshots a small inline page. Chromium refuses to start when its sandbox cannot be set up
("No usable sandbox!"), so success proves the seccomp profile and the host's user-namespace
settings are right. Exit codes: 0 ok, 1 Chromium failed, 2 Playwright missing.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PAGE = (
    "data:text/html,<!doctype html><meta charset=utf-8>"
    "<body style='margin:0;background:%231A0B2E;color:white;font:32px sans-serif'>Aadhi render check</body>"
)
FALLBACK_ARGS = ("--disable-gpu", "--hide-scrollbars", "--mute-audio", "--no-first-run")


def chromium_args() -> list[str]:
    """The render worker's Chromium flags (falls back to a minimal set outside the app image)."""
    app_root = str(Path(__file__).resolve().parents[2])  # /app in the image, the checkout locally
    if app_root not in sys.path:
        sys.path.insert(0, app_root)
    try:
        from aadhi.compose.screenshot import CHROMIUM_ARGS
    except Exception:  # the check must also run where the app package is incomplete
        return list(FALLBACK_ARGS)
    return list(CHROMIUM_ARGS)


def check(timeout_s: float = 60.0) -> tuple[bool, str]:
    """Launch sandboxed Chromium and take one screenshot; returns ``(ok, message)``."""
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ModuleNotFoundError as exc:
        return False, f"playwright is not installed: {exc}"
    started = time.monotonic()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, chromium_sandbox=True, args=chromium_args(), timeout=timeout_s * 1000
            )
            try:
                page = browser.new_page(viewport={"width": 640, "height": 360})
                page.goto(PAGE, timeout=timeout_s * 1000)
                png = page.screenshot(type="png", timeout=timeout_s * 1000)
                version = browser.version
            finally:
                browser.close()
    except PlaywrightError as exc:
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        hint = ""
        if "sandbox" in str(exc).lower():
            hint = (" (run the container with security_opt seccomp=docker/app/chromium-seccomp.json and allow"
                    " unprivileged user namespaces on the host: docs/OPERATIONS.md section 4.2)")
        return False, f"chromium failed: {first[:300]}{hint}"
    if not png.startswith(b"\x89PNG"):
        return False, "chromium returned something that is not a PNG"
    return True, f"chromium {version} with sandbox: ok ({len(png)} byte screenshot in {time.monotonic() - started:.1f}s)"


def main() -> int:
    ok, message = check()
    print(message, file=sys.stdout if ok else sys.stderr)
    if not ok and message.startswith("playwright is not installed"):
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
