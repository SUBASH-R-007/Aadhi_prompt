"""The Dynamic render page driver (aadhi.compose.dynamic.stage_page) in real Chromium against the stub stage
(``stub_stage/``, contract v3): token in the fragment, the worker-served timeline, boot and contract errors, the
network policy, media-frame routes, timer-guard trips, CDP capture."""

from __future__ import annotations

import io
import time

import pytest
from PIL import Image

from aadhi.compose.dynamic.stage_page import (
    PLACEHOLDER_FRAME,
    StageError,
    open_stage,
    placeholder_frames,
    stage_url,
)

from ..stub_server import serve_other
from .helpers import dynamic_timeline, need_chromium, settings
from .stage_server import serve_stage

pytestmark = pytest.mark.slow
TOKEN = "stub.token"


@pytest.fixture(autouse=True)
def _chromium() -> None:
    need_chromium()


def test_stage_url_keeps_the_token_in_the_fragment() -> None:
    assert stage_url("http://x:1/", "a.b") == "http://x:1/render-dynamic#token=a.b"
    assert placeholder_frames("/__frames/0/m/1.jpg") == PLACEHOLDER_FRAME
    assert Image.open(io.BytesIO(PLACEHOLDER_FRAME)).size == (1, 1)


async def test_page_fetches_the_timeline_with_the_token() -> None:
    tl = dynamic_timeline("portrait")
    with serve_stage(TOKEN, tl.model_dump(mode="json")) as (base, state):
        async with open_stage(settings(), size=(1080, 1920), token=TOKEN, base_url=base) as page:
            info = await page.info()
            assert (info.width, info.height, info.fps, info.frames) == (1080, 1920, 30, 366)
            assert [(s.index, s.f0, s.f1) for s in info.scenes] == [(0, 0, 140), (1, 140, 279), (2, 279, 366)]
            assert "#" not in page.page.url  # the page dropped the fragment
            jpeg = await page.capture_jpeg()
            assert jpeg[:3] == b"\xff\xd8\xff" and Image.open(io.BytesIO(jpeg)).size == (1080, 1920)
            assert page.webgl is False and page.errors == [] and page.guard_trips == []
    assert state.timeline_requests == 1  # answered by the server, with the Bearer token


async def test_worker_serves_the_timeline_and_media_frames() -> None:
    tl = dynamic_timeline("landscape")
    served = tl.model_dump(mode="json")
    with serve_stage(TOKEN, {"broken": True}) as (base, state):
        async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base, served_timeline=served,
                              frames=placeholder_frames) as page:
            assert (await page.info()).frames == 366
            got = await page.page.evaluate("""async () => {
                const r = await fetch('/__frames/c0001/clip/12.jpg');
                return [r.status, (await r.arrayBuffer()).byteLength];
            }""")
            assert got == [200, len(PLACEHOLDER_FRAME)]
            # a reload serves a changed timeline (the preflight's second round)
            served["scenes"][0]["title"] = "Changed"
            await page.load(served)
            await page.seek(10)
            boxes = await page.text_boxes()
            assert boxes[0]["text"] == "Changed"
    assert state.timeline_requests == 0 and not any(p.startswith("/__frames/") for p in state.requests)


async def test_network_policy_blocks_other_origins() -> None:
    tl = dynamic_timeline("landscape")
    with serve_other() as (other, other_state), serve_stage(TOKEN, tl.model_dump(mode="json")) as (base, _):
        async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base) as page:
            failed = await page.page.evaluate("""async (url) => {
                try { await fetch(url, { mode: 'no-cors' }); return false; } catch (e) { return true; }
            }""", f"{other}/steal")
            assert failed
            # without a frames callback a media frame goes to the app origin (404 from the stub server)
            status = await page.page.evaluate("async () => (await fetch('/__frames/x/y/1.jpg')).status")
            assert status == 404
    assert other_state.requests == []


@pytest.mark.parametrize(("code", "retryable"), [("timeline_invalid", False), ("flaky_start", True)])
async def test_boot_failures(code: str, retryable: bool) -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, tl.model_dump(mode="json"), config={"bootError": code}) as (base, _):
        with pytest.raises(StageError) as ei:
            async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base):
                pass
    assert ei.value.code == f"render_page_{code}" and ei.value.retryable is retryable


async def test_a_script_error_during_boot_fails_fast() -> None:
    tl = dynamic_timeline("landscape")
    started = time.monotonic()
    with serve_stage(TOKEN, tl.model_dump(mode="json"), config={"throwAtBoot": True}) as (base, _):
        with pytest.raises(StageError) as ei:
            async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base, ready_timeout_s=60):
                pass
    assert ei.value.code == "render_page_script_error" and not ei.value.retryable
    assert "stub module failed" in str(ei.value) and time.monotonic() - started < 30


async def test_contract_version_is_checked() -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, tl.model_dump(mode="json"), config={"version": 2}) as (base, _):
        with pytest.raises(StageError) as ei:
            async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base):
                pass
    assert ei.value.code == "stage_contract" and not ei.value.retryable


async def test_a_stray_timer_trips_the_guard_and_is_reported() -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, tl.model_dump(mode="json"), config={"strayTimer": True}) as (base, _):
        async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base) as page:
            with pytest.raises(StageError) as ei:
                await page.seek(3)
            assert ei.value.code == "stage_call_failed" and not ei.value.retryable
            assert page.guard_trips and "aadhi-guard" in page.guard_trips[0]
            assert any("setTimeout" in e for e in page.errors)


async def test_caption_layer_is_captured_with_alpha() -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, tl.model_dump(mode="json")) as (base, _):
        async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base) as page:
            await page.seek(30)  # a caption is on screen
            await page.set_layers(captions=True, stage=False)
            png = Image.open(io.BytesIO(await page.capture_png(transparent=True))).convert("RGBA")
            assert png.size == (1920, 1080)
            assert png.getpixel((20, 20))[3] == 0  # the stage layer is gone: transparent
            assert png.getpixel((960, 972))[3] == 255  # the caption pill stays
            await page.set_layers(captions=True, stage=True)
            full = Image.open(io.BytesIO(await page.capture_png())).convert("RGBA")
            assert full.getpixel((20, 20))[3] == 255


async def test_a_refused_timeline_is_not_retried() -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, tl.model_dump(mode="json")) as (base, state):
        with pytest.raises(StageError) as ei:
            async with open_stage(settings(), size=(1920, 1080), token="wrong.token", base_url=base):
                pass
    assert ei.value.code == "render_page_timeline_http" and not ei.value.retryable and "401" in str(ei.value)
    assert state.timeline_requests == 1


async def test_a_missing_render_page_fails_at_once() -> None:
    with serve_stage(TOKEN, {}) as (base, _):
        with pytest.raises(StageError) as ei:
            async with open_stage(settings(), size=(1920, 1080), token=TOKEN, base_url=base + "/nowhere"):
                pass
    assert ei.value.code == "render_page_http" and not ei.value.retryable and "404" in str(ei.value)
