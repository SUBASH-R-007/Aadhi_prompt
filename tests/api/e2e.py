"""Helpers for the end-to-end API tests (real inline workers, offline fake providers)."""

from __future__ import annotations

import json
import time
from typing import Any

SOURCE_MARKDOWN = """# Ohm's Law

## Voltage, current and resistance

Electric current is the rate of flow of charge through a conductor. It is measured in amperes (A).
Voltage is the potential difference that pushes charge around a circuit. It is measured in volts (V).
Resistance opposes the flow of current. It is measured in ohms.

## The law

For an ohmic conductor at constant temperature, the current through it is directly proportional to the
voltage across it: V = I x R. Doubling the voltage doubles the current when the resistance stays the same.

## Worked example

A 12 V battery is connected to a 4 ohm resistor. The current is I = V / R = 12 / 4 = 3 A.

## Common misconception

Students often think that current is "used up" by a resistor. In a series circuit the same current flows
through every component; energy is transferred, not current.
"""

# Keep generations cheap and deterministic: no Manim, AI video, generated images, GIFs or p5 sketches.
FAST_OPTIONS: dict[str, Any] = {
    "target_minutes": 3,
    "allow_manim": False,
    "allow_freeform_manim": False,
    "allow_generated_images": False,
    "allow_ai_video": False,
    "allow_interactive": False,
    "allow_gifs": False,
}

TERMINAL = ("succeeded", "failed", "cancelled", "awaiting_review")
# A queued job must be picked up within this time, else no worker can run it (fail fast instead of
# waiting out the full timeout of a minutes-long render).
START_TIMEOUT = 60.0
# Module that registers each job kind's handler (imported directly to surface its real error).
HANDLER_MODULES = {
    "generate_lecture": "aadhi.pipeline.orchestrator",
    "regenerate_scene": "aadhi.pipeline.orchestrator",
    "build_assets": "aadhi.pipeline.orchestrator",
    "translate": "aadhi.pipeline.orchestrator",
    "render_video": "aadhi.compose.video",
    "cleanup": "aadhi.jobs.cleanup",
}


def require_job_handlers(*kinds: str) -> None:
    """Fail fast, with the underlying import error, when a job kind has no registered handler."""
    import importlib

    import pytest

    from aadhi.jobs.base import registered_kinds

    try:
        registered = set(registered_kinds())
        for kind in kinds:
            if kind not in registered and kind in HANDLER_MODULES:
                importlib.import_module(HANDLER_MODULES[kind])  # registers it, or raises the real cause
        registered = set(registered_kinds())
    except Exception as exc:  # noqa: BLE001 - reported as the test failure
        pytest.fail(f"job handler modules failed to import: {type(exc).__name__}: {exc}")
    missing = [k for k in kinds if k not in registered]
    if missing:
        pytest.fail(f"no job handler registered for {missing} (registered: {sorted(registered)})")


def figure_png(width: int = 320, height: int = 200) -> bytes:
    """A PNG large enough for the PDF ingest to treat it as a figure (a simple circuit sketch)."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, width - 20, height - 20], outline="black", width=4)
    draw.rectangle([width // 2 - 40, 10, width // 2 + 40, 30], fill="orange", outline="black")
    draw.line([40, height - 20, 40, height - 60], fill="black", width=6)
    draw.text((width // 2 - 10, height // 2), "R", fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def figure_pdf() -> bytes:
    """A two-page text PDF with one embedded figure and its caption (PyMuPDF)."""
    import pymupdf

    doc = pymupdf.open()
    try:
        paragraphs = [p.strip() for p in SOURCE_MARKDOWN.split("\n\n") if p.strip()]
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(50, 50, 545, 400), "\n\n".join(paragraphs[:4]).replace("#", ""), fontsize=11)
        page.insert_image(pymupdf.Rect(150, 420, 450, 607), stream=figure_png())
        page.insert_text((150, 630), "Figure 1: A cell connected to a resistor R.", fontsize=10)
        page2 = doc.new_page()
        page2.insert_textbox(pymupdf.Rect(50, 50, 545, 750), "\n\n".join(paragraphs[4:]).replace("#", ""), fontsize=11)
        return doc.tobytes()
    finally:
        doc.close()


def describe_job(client: Any, job_id: int) -> str:
    """Job summary + its last events (for assertion messages)."""
    job = client.get(f"/api/jobs/{job_id}").json()
    events = client.get(f"/api/jobs/{job_id}/events").json().get("items", [])
    tail = [f"[{e['level']}] {e['stage']}: {e['message']}" for e in events[-12:]]
    return json.dumps(job, indent=1) + "\n" + "\n".join(tail)


def wait_job(
    client: Any,
    job_id: int,
    *,
    timeout: float = 240.0,
    until: tuple[str, ...] = TERMINAL,
    start_timeout: float = START_TIMEOUT,
) -> dict[str, Any]:
    """Poll ``GET /api/jobs/{id}`` until the job reaches one of ``until`` (AssertionError on timeout).

    A job still ``queued`` after ``start_timeout`` fails at once (no worker/handler can run it).
    """
    started = time.monotonic()
    deadline = started + timeout
    start_deadline = started + min(start_timeout, timeout)
    while True:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in until:
            return job
        now = time.monotonic()
        if job["status"] == "queued" and now > start_deadline:
            raise AssertionError(
                f"job {job_id} ({job.get('kind')}) was not picked up within {start_timeout}s; is a worker "
                f"running and a handler registered for it?\n{describe_job(client, job_id)}"
            )
        if now > deadline:
            raise AssertionError(f"job {job_id} did not finish in {timeout}s:\n{describe_job(client, job_id)}")
        time.sleep(0.25)


def assert_succeeded(client: Any, job: dict[str, Any]) -> None:
    """Fail with the job's events when it did not succeed."""
    assert job["status"] == "succeeded", describe_job(client, job["id"])
