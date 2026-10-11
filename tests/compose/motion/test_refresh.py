"""Presigned media links are signed again for every stage page the motion engine opens (S3 without a CDN).

``render_dynamic`` resolves the served timeline once, but a worker may open its first page after waiting for a CPU
slot, recycle its page every 1,800 frames, retry a chunk or reopen a page for a remedy, long after
``S3_PRESIGN_TTL_SECONDS``. A picture whose signature expired fails to decode silently (a broken picture degrades),
so every page load must serve freshly signed links, with the preflight's remedies kept.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from aadhi.compose.dynamic.preflight import Remedy
from aadhi.compose.motion import engine as engine_mod
from aadhi.compose.motion import job as job_mod
from aadhi.compose.motion.stage import StageError, StageSession
from aadhi.config import Settings


class _FakePage:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.guard_trips: list[str] = []
        self.page = self
        self.layers: list[dict[str, bool]] = []

    async def set_layers(self, **spec: bool) -> None:
        self.layers.append(spec)

    async def close(self) -> None:
        return None


class _FakeBrowser:
    """Records the timeline each page load would answer the page's timeline request with."""

    def __init__(self) -> None:
        self.opened: list[dict[str, Any] | None] = []
        self.pages: list[_FakePage] = []

    async def open(self, *, token: str, served_timeline: dict[str, Any] | None, frames: Any,
                   ready_timeout_s: float) -> _FakePage:
        self.opened.append(served_timeline)
        page = _FakePage()
        self.pages.append(page)
        return page


def _signed(n: int) -> dict[str, Any]:
    return {"scenes": [{"media": {"url": f"https://bucket.s3.example/a.png?X-Amz-Signature={n}"},
                        "look": {"template": "board_generic"}}]}


def test_every_page_load_serves_freshly_signed_links() -> None:
    calls: list[int] = []

    def refresh() -> dict[str, Any]:
        calls.append(len(calls) + 1)
        return _signed(len(calls))

    session = StageSession(settings=Settings(_env_file=None), size=(320, 180), token="t", served=_signed(0),
                           refresh_served=refresh)
    browser = _FakeBrowser()
    session._browser = browser  # type: ignore[assignment]  # what start() sets after launching Chromium

    async def go() -> None:
        await session.open_page()  # start()
        await session.recycle()  # every 1,800 frames, after a slowdown or a failed chunk
        await session.recycle()

    asyncio.run(go())
    urls = [o["scenes"][0]["media"]["url"] for o in browser.opened if o is not None]
    assert urls == [f"https://bucket.s3.example/a.png?X-Amz-Signature={n}" for n in (1, 2, 3)]
    assert calls == [1, 2, 3] and session.pages_opened == 3


def test_without_a_refresher_the_served_copy_is_kept() -> None:
    session = StageSession(settings=Settings(_env_file=None), size=(320, 180), token="t", served=_signed(0))
    browser = _FakeBrowser()
    session._browser = browser  # type: ignore[assignment]
    asyncio.run(session.open_page())
    asyncio.run(session.recycle())
    assert browser.opened == [_signed(0), _signed(0)]


def test_a_failed_refresh_is_a_retryable_stage_error() -> None:
    def broken() -> dict[str, Any]:
        raise OSError("storage unreachable")

    session = StageSession(settings=Settings(_env_file=None), size=(320, 180), token="t", served=_signed(0),
                           refresh_served=broken)
    session._browser = _FakeBrowser()  # type: ignore[assignment]
    with pytest.raises(StageError) as ei:
        asyncio.run(session.open_page())
    assert ei.value.retryable and ei.value.code == "stage_refresh_failed"
    assert engine_mod._retryable(ei.value)  # the chunk is captured again on a fresh page


def test_render_job_refresher_signs_again_and_keeps_the_remedies(monkeypatch: pytest.MonkeyPatch) -> None:
    from aadhi.api import timelines

    served: list[int] = []

    def fake_served(timeline: Any, assets: Any, settings: Any) -> dict[str, Any]:
        served.append(1)
        return _signed(len(served))

    monkeypatch.setattr(job_mod, "served_timeline", fake_served)
    remedies = [Remedy("shrink", 0, "body", 44.0), Remedy("scrim", 0, "title")]
    monkeypatch.setattr(timelines, "signed_urls", lambda settings: False)
    assert job_mod.refresher(None, None, Settings(_env_file=None), remedies) is None  # links that never expire

    monkeypatch.setattr(timelines, "signed_urls", lambda settings: True)
    refresh = job_mod.refresher(None, None, Settings(_env_file=None), remedies)
    assert refresh is not None
    first, second = refresh(), refresh()
    assert first["scenes"][0]["media"]["url"].endswith("=1") and second["scenes"][0]["media"]["url"].endswith("=2")
    for body in (first, second):
        assert body["scenes"][0]["look"]["text_fit"] == {"body": {"size": 44.0}, "title": {"scrim": True}}


def test_engine_sessions_carry_the_refresher() -> None:
    def refresh() -> dict[str, Any]:
        return {}

    run = SimpleNamespace(settings=Settings(_env_file=None), capture_size=(320, 180), ctx=None, driver=None,
                          refresh_served=refresh)
    session = engine_mod._session(run, token="t", served={}, origins=[],  # type: ignore[arg-type]
                                  options=engine_mod.StageOptions(), label="remedy")
    assert session.refresh_served is refresh
