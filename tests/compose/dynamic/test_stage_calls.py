"""How a failed stage call is classified (``StagePage._call``): an error of the page's own code (a guard trip, a bug)
is final, a page whose renderer died may work again on a fresh page (the engine retries such a chunk once)."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.compose.dynamic.stage_page import StageError, StagePage, _Deadline

from .helpers import settings


class _Page:
    """A Playwright page stand-in whose ``evaluate`` raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        raise self.error


def _stage(error: Exception, *, crashed: bool = False) -> StagePage:
    return StagePage(page=_Page(error), cdp=None, settings=settings(), deadline=_Deadline(5.0, None), url="x",
                     ready_timeout_s=5.0, crashed=crashed)


async def test_an_error_of_the_page_code_is_final() -> None:
    page = _stage(Exception("Error: aadhi timer guard: setTimeout is not allowed after ready"))
    with pytest.raises(StageError) as ei:
        await page.seek(3)
    assert ei.value.code == "stage_call_failed" and not ei.value.retryable
    assert page.guard_trips  # still recorded as a guard trip


@pytest.mark.parametrize("message", ["Target crashed", "Page crashed",
                                     "Target page, context or browser has been closed"])
async def test_a_crashed_page_may_be_retried(message: str) -> None:
    with pytest.raises(StageError) as ei:
        await _stage(Exception(message)).seek(3)
    assert ei.value.code == "stage_crashed" and ei.value.retryable
    assert "crashed" in str(ei.value)


async def test_the_crash_event_marks_any_later_failure_retryable() -> None:
    with pytest.raises(StageError) as ei:
        await _stage(Exception("Execution context was destroyed"), crashed=True).text_boxes()
    assert ei.value.code == "stage_crashed" and ei.value.retryable


# --- the CDP capture (``StagePage._send``): a renderer that dies while a frame is captured --------------------------


class _Cdp:
    """A CDP session stand-in whose ``send`` raises ``error`` (or answers with a frame)."""

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.sent: list[str] = []

    async def send(self, method: str, params: Any = None) -> Any:
        self.sent.append(method)
        if self.error is not None:
            raise self.error
        return {"data": "AAEC"}


def _capturing(error: Exception | None, *, crashed: bool = False) -> StagePage:
    return StagePage(page=_Page(Exception("unused")), cdp=_Cdp(error), settings=settings(),
                     deadline=_Deadline(5.0, None), url="x", ready_timeout_s=5.0, crashed=crashed)


@pytest.mark.parametrize("crashed", [False, True])
@pytest.mark.parametrize("message", [
    "CDPSession.send: Protocol error (Page.captureScreenshot): Unable to capture screenshot",
    "CDPSession.send: Protocol error (Page.captureScreenshot): Internal error",
    "CDPSession.send: Target closed",
    "Protocol error (Page.captureScreenshot): Session closed. Most likely the page has been closed.",
])
async def test_a_capture_on_a_dead_renderer_is_a_retryable_crash(message: str, crashed: bool) -> None:
    for capture in ("capture_jpeg", "capture_png"):
        page = _capturing(Exception(message), crashed=crashed)
        with pytest.raises(StageError) as ei:
            await getattr(page, capture)()
        assert ei.value.code == "stage_crashed" and ei.value.retryable, capture
        assert page.errors  # recorded for the render's log


async def test_any_other_capture_failure_is_retryable_too() -> None:
    page = _capturing(Exception("CDPSession.send: Protocol error (Page.captureScreenshot): Invalid parameters"))
    with pytest.raises(StageError) as ei:
        await page.capture_png(transparent=True)
    assert ei.value.code == "stage_capture_failed" and ei.value.retryable
    assert page.cdp.sent == ["Emulation.setDefaultBackgroundColorOverride"]


async def test_a_capture_that_works_returns_the_frame_bytes() -> None:
    page = _capturing(None)
    assert await page.capture_jpeg() == b"\x00\x01\x02" and await page.capture_png() == b"\x00\x01\x02"


async def test_the_engine_retries_a_chunk_whose_capture_crashed() -> None:
    from aadhi.compose.motion.engine import _retryable

    page = _capturing(Exception("CDPSession.send: Protocol error (Page.captureScreenshot): Internal error"))
    with pytest.raises(StageError) as ei:
        await page.capture_jpeg()
    assert _retryable(ei.value)
