"""Dynamic preflight end to end in real Chromium against the stub stage: sampling, the three checks, the automatic
remedies and the second round, blocks and warnings, and the stored result (DYNAMIC_VIDEO_DESIGN §7.12)."""

from __future__ import annotations

import json

import pytest

from aadhi.compose.dynamic.preflight import PreflightBlocked, raise_if_blocked, run_preflight, sample_frames
from aadhi.compose.dynamic.stage_page import StageInfo, StageScene
from aadhi.schemas.timeline import Timeline

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


async def test_remedies_fix_what_the_stage_honours() -> None:
    tl = dynamic_timeline("landscape")
    config = {"faults": FAULTS, "honour": ["shrink", "safe", "scrim"]}
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


async def test_classic_timelines_are_refused() -> None:
    classic = Timeline.model_validate({k: v for k, v in dynamic_timeline("landscape").model_dump(mode="json").items()
                                       if k != "presentation"})
    with pytest.raises(ValueError):
        await run_preflight(settings(), classic, token=TOKEN, base_url="http://127.0.0.1:9")
