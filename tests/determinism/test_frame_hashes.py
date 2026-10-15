"""Determinism (DYNAMIC_VIDEO_DESIGN §7.4 test (a)): 300 frames rendered as 1x300, as 3x100 (three pages at once,
like three render workers) and in reverse order give identical SHA-256 per captured frame (CDP JPEG q92, the
production capture), with no page error and no timer-guard trip; and the timer guard trips on a stray timer.

The real stage runs through the real app's ``/render-dynamic`` (both formats); the stub stage
(``tests/compose/dynamic/stub_stage``) proves the harness itself, including that it notices a non-deterministic
stage. Hashes are compared within one machine only (Windows with Windows, the container with the container).
"""

from __future__ import annotations

import copy
import hashlib
from contextlib import asynccontextmanager
from typing import Any

import pytest

from aadhi.compose.dynamic.determinism import (
    DeterminismReport,
    Plan,
    hash_frames,
    run_plans,
    spot_check,
    spot_frames,
    standard_plans,
)
from aadhi.compose.dynamic.stage_page import StageError, drain, open_stage
from tests.compose.dynamic.helpers import dynamic_timeline, need_chromium, settings
from tests.compose.dynamic.stage_server import serve_stage

pytestmark = [pytest.mark.slow, pytest.mark.container]
TOKEN = "determinism.check"
FRAMES = 300


def test_standard_plans() -> None:
    one, three, rev = standard_plans(10, 300, 3)
    assert one == Plan("1x300", (tuple(range(10, 310)),))
    assert [len(c) for c in three.chunks] == [100, 100, 100] and three.chunks[1][0] == 110 and three.name == "3x100"
    assert rev.chunks == (tuple(range(309, 9, -1)),)
    assert one.frames == three.frames == rev.frames
    assert [len(c) for c in standard_plans(0, 10, 3)[1].chunks] == [4, 4, 2]
    with pytest.raises(ValueError):
        standard_plans(0, 0)


def test_report_mismatches() -> None:
    report = DeterminismReport(hashes={"a": {0: "x", 1: "y"}, "b": {0: "x", 1: "z"}, "c": {0: "x"}})
    assert report.mismatches() == [{"frame": 1, "hashes": {"a": "y", "b": "z", "c": None}}]
    assert not report.ok and report.distinct_frames() == 2
    assert DeterminismReport(hashes={"a": {0: "x"}, "b": {0: "x"}}).ok


def _opener(base: str, served: dict[str, Any], size: tuple[int, int]):
    @asynccontextmanager
    async def open_page():
        async with open_stage(settings_for(base), size=size, token=TOKEN, base_url=base,
                              served_timeline=served) as page:
            yield page

    return open_page


def settings_for(base: str) -> Any:
    return settings()


def test_spot_frames() -> None:
    assert spot_frames(366) == [61, 183, 305]
    assert spot_frames(2) == [0, 1] and spot_frames(0) == [] and spot_frames(10, 0) == []


# --- the harness, on the stub stage ------------------------------------------------------------------------------


async def test_spot_check_on_a_fresh_page() -> None:
    need_chromium()
    tl = dynamic_timeline("portrait")
    served = tl.model_dump(mode="json")
    frames = spot_frames(366)
    with serve_stage(TOKEN, {}) as (base, _):
        expected = {f: (await _hash_one(base, served, f)) for f in frames}  # as a worker captured them for the MP4
        async with open_stage(settings(), size=(1080, 1920), token=TOKEN, base_url=base, served_timeline=served) as b:
            assert await spot_check(b, expected) == []
            assert await spot_check(b, {**expected, frames[1]: "0" * 64}) == [frames[1]]


async def _hash_one(base: str, served: dict[str, Any], frame: int) -> str:
    async with open_stage(settings(), size=(1080, 1920), token=TOKEN, base_url=base, served_timeline=served) as page:
        for f in range(max(0, frame - 5), frame):  # some history before it
            await page.seek(f)
        return (await hash_frames(page, [frame]))[frame]




async def test_stub_stage_is_deterministic_in_any_order() -> None:
    need_chromium()
    tl = dynamic_timeline("landscape")
    served = tl.model_dump(mode="json")
    with serve_stage(TOKEN, {}) as (base, _):
        report = await run_plans(_opener(base, served, (1920, 1080)), standard_plans(0, FRAMES, 3))
    assert set(report.hashes) == {"1x300", "3x100", "reverse"}
    assert all(len(h) == FRAMES for h in report.hashes.values())
    assert report.mismatches() == [] and report.ok, report.mismatches()[:3]
    assert report.distinct_frames() > FRAMES // 2  # the stub moves every frame: identical hashes mean something


async def test_the_harness_notices_a_non_deterministic_stage() -> None:
    need_chromium()
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, {}, config={"random": True}) as (base, _):
        report = await run_plans(_opener(base, tl.model_dump(mode="json"), (1920, 1080)), standard_plans(0, 30, 3))
    assert len(report.mismatches()) > 20 and not report.ok


# --- the real stage ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
async def test_real_stage_frames_are_identical_in_any_order(real_app, fmt: str) -> None:
    tl = dynamic_timeline(fmt)
    served = tl.model_dump(mode="json")

    @asynccontextmanager
    async def open_page():
        async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                              served_timeline=served) as page:
            yield page

    plans = standard_plans(0, FRAMES, 3)  # frames 0-299 cross two scene joins (140, 279) and two crossfades
    report = await run_plans(open_page, plans)
    mismatches = report.mismatches()
    assert mismatches == [], f"{len(mismatches)} frames differ, first: {mismatches[:3]}"
    assert report.guard_trips == [] and report.errors == []
    assert report.distinct_frames() > 30  # the background drifts and the scenes change: frames do differ


async def test_real_stage_guard_trips_on_a_stray_timer(real_app) -> None:
    tl = dynamic_timeline("landscape")
    async with open_stage(real_app.settings, size=(1920, 1080), token=TOKEN, base_url=real_app.base,
                          served_timeline=tl.model_dump(mode="json")) as page:
        await page.seek(12)
        assert page.guard_trips == []
        for api in ("setTimeout", "setInterval"):
            outcome = await page.page.evaluate(f"""() => {{
                try {{ {api}(() => {{}}, 10); return 'ran'; }} catch (e) {{ return String(e && e.message || e); }}
            }}""")
            assert outcome != "ran" and "timer guard" in outcome, outcome
        await drain()
        assert len(page.guard_trips) == 2 and all("timer guard" in t for t in page.guard_trips)
        trips = await page.page.evaluate("() => window.aadhiStageGuard && window.aadhiStageGuard.trips.length")
        assert trips == 2
        # a stray timer inside a frame's work fails that seek loudly instead of producing a different frame
        await page.page.evaluate("""() => {
            const real = window.aadhiStage.seek;
            window.aadhiStage.seek = async (f) => { setTimeout(() => {}, 0); return real(f); };
        }""")
        with pytest.raises(StageError) as ei:
            await page.seek(13)
        assert ei.value.code == "stage_call_failed" and not ei.value.retryable


# --- a face the page did not preload ------------------------------------------------------------------------------

HINDI = "वोल्टेज दोगुना करने पर धारा भी दोगुनी हो जाती है।"
ENGLISH_DANDA = "Voltage doubles, so the current doubles। Units matter॥"
_FACES_JS = """() => [...document.fonts].filter((f) => f.status !== 'unloaded').map((f) => `${f.family} ${f.weight} ${f.status}`)"""


def _with_danda(served: dict[str, Any], *, hindi: bool) -> dict[str, Any]:
    """The fixture with the captions and board text of every scene after the first ending in a danda (U+0964/0965),
    which the Noto Sans Tamil and Telugu subsets claim too: as a Hindi lecture (the Devanagari face is preloaded and
    comes first in the stacks) or as an English one (no stack puts Devanagari first, so a face nobody preloaded is
    loaded mid-render). The first scene keeps its text: the page boots on it, so the danda first appears mid-render."""
    data = copy.deepcopy(served)
    text = HINDI if hindi else ENGLISH_DANDA
    if hindi:
        data["language"] = data["board_language"] = "hi-IN"
    later = float(data["scenes"][1]["start"])
    for scene in data["scenes"][1:]:
        scene["kinetic_captions"] = []
        for item in scene.get("board") or []:
            if isinstance(item.get("text"), str):
                item["text"] = text
        for beat in scene.get("beats") or []:
            beat["narration"] = text
            for cue in beat.get("captions") or []:
                cue["text"] = text
    for cue in data.get("captions") or []:
        if float(cue["start"]) >= later:
            cue["text"] = text
    return data


@pytest.mark.parametrize("hindi", [True, False], ids=["hindi", "english-with-danda"])
async def test_a_danda_caption_is_identical_on_fresh_pages_in_any_order(real_app, hindi: bool) -> None:
    """The first frames with a danda caption, captured on fresh pages forward and in reverse: identical bytes, with
    every face the text needs loaded by the time the frame is captured (no frame drawn while text is invisible)."""
    tl = dynamic_timeline("landscape")
    served = _with_danda(tl.model_dump(mode="json"), hindi=hindi)
    scene = served["scenes"][1]
    first = round((scene["start"] + scene["beats"][0]["captions"][0]["start"]) * tl.fps) + 1
    frames = list(range(first, first + 6))
    hashes: dict[str, dict[int, str]] = {}
    for name, order in (("forward", frames), ("reverse", list(reversed(frames)))):
        async with open_stage(real_app.settings, size=(tl.width, tl.height), token=TOKEN, base_url=real_app.base,
                              served_timeline=served) as page:
            hashes[name] = {}
            for f in order:
                await page.seek(f)
                faces = await page.page.evaluate(_FACES_JS)
                assert all(face.endswith(" loaded") for face in faces), (name, f, faces)
                hashes[name][f] = hashlib.sha256(await page.capture_jpeg()).hexdigest()
            assert page.errors == [] and page.guard_trips == []
    assert hashes["forward"] == hashes["reverse"]
