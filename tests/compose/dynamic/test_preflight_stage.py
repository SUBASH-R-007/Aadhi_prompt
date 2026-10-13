"""Dynamic preflight end to end in real Chromium against the stub stage: sampling, the three checks, the automatic
remedies and the second round, blocks and warnings, and the stored result (DYNAMIC_VIDEO_DESIGN §7.12)."""

from __future__ import annotations

import json

import pytest

from aadhi.compose.dynamic.preflight import PreflightBlocked, raise_if_blocked, run_preflight, sample_frames
from aadhi.compose.dynamic.stage_page import StageInfo, StageScene
from aadhi.schemas.timeline import Timeline

from ..motion.fixtures import dynamic_timeline as motion_timeline
from .helpers import dynamic_timeline, need_chromium, settings
from .stage_server import serve_stage

pytestmark = pytest.mark.slow
TOKEN = "stub.token"
FAULTS = {"1": ["unsafe", "overflow"], "2": ["contrast"]}


@pytest.fixture(autouse=True)
def _chromium() -> None:
    need_chromium()


def _page_loads(state) -> int:
    return sum(1 for p in state.requests if p == "/render-dynamic")


@pytest.mark.parametrize("fmt", ["landscape", "portrait"])
async def test_clean_timeline_passes_in_one_round(fmt: str) -> None:
    tl = dynamic_timeline(fmt)
    with serve_stage(TOKEN, {}) as (base, state):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base)
    assert res.ok and res.rounds == 1 and res.remedies == [] and _page_loads(state) == 1
    w, h = (1080, 1920) if fmt == "portrait" else (1920, 1080)
    info = StageInfo(w, h, 30, 366, (StageScene(0, 0, 140), StageScene(1, 140, 279), StageScene(2, 279, 366)))
    assert res.sampled == sample_frames(info, tl)
    s = res.summary()
    assert s["checks"] == {c: {"status": "pass", "count": 0}
                           for c in ("text_overflow", "safe_zones", "contrast", "type_floor")}
    assert s["stats"]["frames"] == len(res.sampled) and s["stats"]["boxes"] >= 2 * len(res.sampled)
    assert s["stats"]["contrast_measured"] == s["stats"]["boxes"] and s["webgl"] is False
    assert res.timeline == tl.model_dump(mode="json")  # nothing changed
    json.dumps(res.as_qa())


@pytest.mark.parametrize("channel", [None, "fixes", "legacy"])
async def test_remedies_fix_what_the_stage_honours(channel: str | None) -> None:
    """The remedies reach the page through both channels (plan §1.2): a stub reading fixes first and legacy second
    (as board_generic from B2a step B), one reading only ``look.fixes``, and one reading only B1's fields."""
    tl = dynamic_timeline("landscape")
    config: dict = {"faults": FAULTS, "honour": ["shrink", "safe", "scrim"]}
    if channel is not None:
        config["channel"] = channel
    with serve_stage(TOKEN, {}, config=config) as (base, state):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base)
    assert res.ok and res.rounds == 2 and _page_loads(state) == 2  # reloaded once with the fixed timeline
    raise_if_blocked(res)
    assert {(i["check"], i["scene_index"], i["status"], i["remedy"]) for i in res.items()} == {
        ("text_overflow", 1, "fixed", "shrink"), ("safe_zones", 1, "fixed", "safe_variant"),
        ("contrast", 2, "fixed", "scrim")}
    assert sorted((r.kind, r.scene_index) for r in res.remedies) == [
        ("safe_variant", 1), ("scrim", 2), ("shrink", 1)]
    look1, look2 = res.timeline["scenes"][1]["look"], res.timeline["scenes"][2]["look"]
    assert look1["variant"] == "safe" and look1["text_fit"]["headline"]["size"] == 36  # the stage's slot minimum
    assert look2["text_fit"]["body"]["scrim"] is True
    assert look1["fixes"] == {"safe": True, "slots": {"headline": {"size": 36}}}
    assert look2["fixes"] == {"safe": False, "slots": {"body": {"scrim": True}}}
    Timeline.model_validate(res.timeline)  # the capture can use it as is
    assert res.summary()["checks"]["safe_zones"]["status"] == "fixed"


async def test_unfixed_problems_block_or_warn() -> None:
    tl = dynamic_timeline("landscape")
    with serve_stage(TOKEN, {}, config={"faults": FAULTS}) as (base, _):  # the stub ignores every remedy
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base)
    assert not res.ok and res.rounds == 2
    statuses = {(f.check, f.scene_index): f.status for f in res.findings}
    assert statuses == {("text_overflow", 1): "block", ("safe_zones", 1): "block", ("contrast", 2): "warn"}
    summary = res.summary()
    assert len(summary["problems"]) == 2 and all(p.endswith("(Shorten text)") for p in summary["problems"])
    assert len(summary["warnings"]) == 1 and "needed" in summary["warnings"][0]
    with pytest.raises(PreflightBlocked) as ei:
        raise_if_blocked(res)
    assert ei.value.code == "preflight_blocked" and "scene 2" in str(ei.value)
    qa = res.as_qa()
    assert qa["ok"] is False and qa["problems"] == summary["problems"]


async def test_portrait_samples_caption_chunks_and_checks_the_reel_zones() -> None:
    tl = dynamic_timeline("portrait")
    with serve_stage(TOKEN, {}, config={"faults": {"0": ["unsafe"]}, "honour": ["safe"]}) as (base, _):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base)
    assert res.ok and [(r.kind, r.scene_index) for r in res.remedies] == [("safe_variant", 0)]
    starts = {round((s.start + c.start) * 30) for s in tl.scenes for c in s.kinetic_captions}
    assert starts and all(any(abs(f - s) <= 1 for f in res.sampled) for s in starts)
    # the caption boxes were checked against the 9:16 caption band (none failed)
    assert not any(f.check == "safe_zones" and f.role == "caption" for f in res.findings)


def _captions_burn(tl: Timeline, on: bool) -> Timeline:
    return tl.model_copy(update={"presentation": tl.presentation.model_copy(update={"captions_burn": on})})


@pytest.mark.parametrize(("presented", "burned"), [(False, True), (True, False)])
async def test_the_caption_layer_checked_is_the_one_the_render_burns(presented: bool, burned: bool) -> None:
    """Like the real stage, the stub draws captions as ``presentation.captions_burn`` says until ``setLayers``; the
    render's own choice (the request's ``burn_captions`` wins) decides which caption boxes are checked."""
    tl = _captions_burn(dynamic_timeline("landscape"), presented)
    with serve_stage(TOKEN, {}, config={"faults": {"1": ["caption_overflow"]}}) as (base, _):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base, captions=burned)
    captions = [f for f in res.findings if f.role == "caption"]
    if burned:  # burned but not presented: the overflowing caption is seen, and no remedy fixes it in the stub
        assert not res.ok and captions and {(f.check, f.scene_index, f.status) for f in captions} == {
            ("text_overflow", 1, "block")}
    else:  # presented but not burned: no caption is drawn, so none can block the render
        assert res.ok and res.rounds == 1 and not captions


async def test_the_second_round_checks_the_render_caption_layer() -> None:
    """The reload after the remedies starts the page over on the presentation's caption layer: preflight sets the
    render's layer again, so captions the render does not burn are not checked in the second round either."""
    tl = _captions_burn(dynamic_timeline("landscape"), True)
    config = {"faults": {"1": ["unsafe", "caption_overflow"]}, "honour": ["safe"]}
    with serve_stage(TOKEN, {}, config=config) as (base, state):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base, captions=False)
    assert res.ok and res.rounds == 2 and _page_loads(state) == 2
    assert {(f.check, f.scene_index, f.status) for f in res.findings} == {("safe_zones", 1, "fixed")}


async def test_a_scene_shorter_than_half_a_frame_keeps_its_frame() -> None:
    """The stage gives every scene at least one frame (``core.js stageInfo``, as the engine's ``scene_frame_bounds``):
    a last scene of 0.01 s makes 121 frames of a 4.01 s lecture, and preflight expects exactly that."""
    tl = motion_timeline([2.0, 2.0, 0.01], size=(1920, 1080))
    with serve_stage(TOKEN, {}) as (base, _):
        res = await run_preflight(settings(), tl, token=TOKEN, base_url=base)
    assert res.ok and res.frames == 121 and 120 in res.sampled


async def test_classic_timelines_are_refused() -> None:
    classic = Timeline.model_validate({k: v for k, v in dynamic_timeline("landscape").model_dump(mode="json").items()
                                       if k != "presentation"})
    with pytest.raises(ValueError):
        await run_preflight(settings(), classic, token=TOKEN, base_url="http://127.0.0.1:9")
