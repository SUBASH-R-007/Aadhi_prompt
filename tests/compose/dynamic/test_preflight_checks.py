"""Dynamic preflight (DYNAMIC_VIDEO_DESIGN §7.12) without a browser: text boxes, the three B1 checks (overflow, safe
zones R-59, analytic contrast R-53), sampling, remedies, the second-round statuses and the result schema; and
``run_preflight`` on a stand-in page (the caption layer the render burns, the frame count the stage reports)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from aadhi.compose.dynamic import preflight as pf
from aadhi.compose.dynamic.preflight import (
    PREFLIGHT_SCHEMA,
    Finding,
    PreflightBlocked,
    PreflightResult,
    Remedy,
    TextBox,
    apply_remedies,
    contrast_required,
    evaluate_box,
    evaluate_boxes,
    inside_bands,
    merge_findings,
    plan_remedies,
    raise_if_blocked,
    run_preflight,
    sample_frames,
)
from aadhi.compose.dynamic.stage_page import StageError, StageInfo, StageScene
from aadhi.compose.dynamic.zones import ZONES
from aadhi.compose.motion.chunks import scene_frame_bounds, total_frames
from aadhi.compose.qa import expected_frame_count
from aadhi.jobs.base import FatalJobError
from aadhi.schemas.timeline import Timeline

from ..motion.fixtures import dynamic_timeline as motion_timeline
from .helpers import dynamic_timeline, settings

INFO = StageInfo(1920, 1080, 30, 366, (StageScene(0, 0, 140), StageScene(1, 140, 279), StageScene(2, 279, 366)))


def box(**kw) -> TextBox:
    base = {"x": 200, "y": 200, "w": 600, "h": 100, "role": "body", "critical": True, "ink": "#F4F4FA",
            "bg": "#1B1F3B", "overflow": 0}
    base.update(kw)
    parsed = TextBox.parse(base)
    assert parsed is not None
    return parsed


# --- text boxes ------------------------------------------------------------------------------------------------


def test_text_box_parsing() -> None:
    b = TextBox.parse({"x": 1, "y": 2.5, "w": 3, "h": 4, "role": " Headline ", "critical": True, "ink": "#fff",
                       "bg": 7, "overflow": True, "scene": 2, "slot": "headline", "size": 96, "weight": 700,
                       "min": 48, "text": "  Ohm's   law  "})
    assert b is not None
    assert (b.x, b.y, b.w, b.h, b.role, b.critical, b.ink, b.bg, b.overflow) == (1, 2.5, 3, 4, "headline", True,
                                                                                  "#fff", None, 1.0, )
    assert (b.scene, b.slot, b.size, b.weight, b.min_size, b.text, b.key) == (2, "headline", 96, 700, 48, "Ohm's law",
                                                                              "headline")
    assert TextBox.parse({"x": 0, "y": 0, "w": 1, "h": 1}).key == "text"  # role defaults; key falls back to it
    loose = TextBox.parse({"x": 0, "y": 0, "w": 1, "h": 1, "critical": 1, "overflow": -5, "scene": True, "role": 5})
    assert loose is not None and not loose.critical and loose.overflow == 0 and loose.scene is None
    for bad in ({"x": 0, "y": 0, "w": -1, "h": 1}, {"x": "0", "y": 0, "w": 1, "h": 1},
                {"x": float("nan"), "y": 0, "w": 1, "h": 1}, {"y": 0, "w": 1, "h": 1}, "box", None):
        assert TextBox.parse(bad) is None


# --- safe zones (R-59) -----------------------------------------------------------------------------------------


def _rect(b) -> tuple[int, int, int, int]:
    """(x, y, w, h) of a zones.Box."""
    return (b.x0, b.y0, b.w, b.h)


@pytest.mark.parametrize(("rect", "inside"), [
    (_rect(ZONES["landscape"].critical[0]), True),  # exactly the critical box (x 96-1824, y 54-912)
    ((95.5, 54, 100, 100), True),         # within the 1 px tolerance
    ((90, 100, 100, 100), False),         # left margin
    ((1800, 100, 100, 100), False),       # right margin
    ((200, 40, 100, 100), False),         # top margin
    ((200, 880, 100, 60), False),         # into the caption band
    ((200, 300, 0, 0), True),             # an empty box always fits
])
def test_landscape_critical_zone(rect, inside) -> None:
    x, y, w, h = rect
    assert inside_bands(box(x=x, y=y, w=w, h=h), pf.CRITICAL_BANDS["landscape"]) is inside


@pytest.mark.parametrize(("rect", "inside"), [
    (_rect(ZONES["portrait"].critical[0]), True),  # exactly the upper column (x 192-888, y 288-840)
    ((200, 700, 560, 300), True),         # spans y 840: inside both bands (x 200-760)
    ((192, 700, 696, 300), False),        # spans y 840 but too wide for the lower column (x 120-780)
    ((150, 300, 300, 100), False),        # left of the upper column
    ((130, 900, 600, 100), True),         # the lower column, x 120-780
    ((130, 1200, 600, 100), False),       # below y 1248 (platform UI)
    ((300, 200, 300, 200), False),        # above y 288 (platform header)
])
def test_portrait_critical_zone(rect, inside) -> None:
    x, y, w, h = rect
    assert inside_bands(box(x=x, y=y, w=w, h=h), pf.CRITICAL_BANDS["portrait"]) is inside


def test_captions_must_sit_in_the_caption_band() -> None:
    landscape_ok = box(role="caption", critical=False, x=160, y=940, w=1600, h=64)
    assert evaluate_box(landscape_ok, fmt="landscape", frame=1, scene_index=0)[0] == []
    high = box(role="caption", critical=False, x=160, y=600, w=1600, h=64)
    found = evaluate_box(high, fmt="landscape", frame=1, scene_index=0)[0]
    assert [f.check for f in found] == ["safe_zones"]
    portrait_ok = box(role="caption", critical=False, x=120, y=1060, w=660, h=120)
    assert evaluate_box(portrait_ok, fmt="portrait", frame=1, scene_index=0)[0] == []
    wide = box(role="caption", critical=False, x=100, y=1060, w=800, h=120)
    assert [f.check for f in evaluate_box(wide, fmt="portrait", frame=1, scene_index=0)[0]] == ["safe_zones"]


def test_burned_captions_take_their_band_from_every_other_critical_box() -> None:
    """In 9:16 the caption band lies inside the lower critical band: a card stack running into it passed the zone
    check while the caption was drawn over it. With burned captions it is a zone problem (safe variant, else block)."""
    option = box(x=206, y=1095, w=569, h=60)  # a quiz option under the caption pill (y 1074-1207)
    assert inside_bands(option, pf.CRITICAL_BANDS["portrait"])
    assert evaluate_box(option, fmt="portrait", frame=1, scene_index=0)[0] == []  # captions not burned: its band
    found = evaluate_box(option, fmt="portrait", frame=1, scene_index=0, captions=True)[0]
    assert [(f.check, f.remedy) for f in found] == [("safe_zones", "safe_variant")]
    above = box(x=206, y=960, w=569, h=60)  # ends at y 1020, above the band
    assert evaluate_box(above, fmt="portrait", frame=1, scene_index=0, captions=True)[0] == []
    caption = box(role="caption", critical=False, x=254, y=1074, w=391, h=133)
    assert evaluate_box(caption, fmt="portrait", frame=1, scene_index=0, captions=True)[0] == []
    chip = box(role="chip", critical=False, x=206, y=1095, w=200, h=50)  # not critical: not judged
    assert evaluate_box(chip, fmt="portrait", frame=1, scene_index=0, captions=True)[0] == []
    landscape = box(x=200, y=800, w=600, h=150)  # 16:9: already outside the critical area, one finding
    assert [f.check for f in evaluate_box(landscape, fmt="landscape", frame=1, scene_index=0, captions=True)[0]] == [
        "safe_zones"]
    boxes = [{"x": 206, "y": 1095, "w": 569, "h": 60, "role": "body", "critical": True, "ink": "#F4F4FA",
              "bg": "#1B1F3B", "scene": 0}]
    assert [f.check for f in evaluate_boxes(boxes, fmt="portrait", frame=1, captions=True)[0]] == ["safe_zones"]


def test_type_floor_warns_below_the_r29_sizes_and_weights() -> None:
    small_chip = evaluate_box(box(role="chip", critical=False, size=36), fmt="landscape", frame=1, scene_index=0)[0]
    assert [f.check for f in small_chip] == ["type_floor"]
    assert evaluate_box(box(role="chip", critical=False, size=40), fmt="landscape", frame=1, scene_index=0)[0] == []
    found = evaluate_box(box(role="label", critical=False, size=40, weight=600), fmt="portrait", frame=1,
                         scene_index=0)[0]
    assert [(f.check, f.status, f.remedy) for f in found] == [("type_floor", "warn", None)]
    assert evaluate_box(box(role="label", critical=False, size=44, weight=600), fmt="portrait", frame=1,
                        scene_index=0)[0] == []
    light = box(y=400, size=44.8, weight=400)  # 9:16 card text at regular weight (R-29: >= 600 at <= 64 px)
    assert [f.check for f in evaluate_box(light, fmt="portrait", frame=1, scene_index=0)[0]] == ["type_floor"]
    assert evaluate_box(light, fmt="landscape", frame=1, scene_index=0)[0] == []  # 16:9 has no weight rule
    assert evaluate_box(box(y=400, size=44.8, weight=600), fmt="portrait", frame=1, scene_index=0)[0] == []
    assert evaluate_box(box(y=400, size=88, weight=400), fmt="portrait", frame=1, scene_index=0)[0] == []  # > 64 px
    assert evaluate_box(box(y=400, size=None), fmt="portrait", frame=1, scene_index=0)[0] == []  # undeclared: not judged
    res = PreflightResult(format="portrait", frames=10, fps=30, findings=[
        Finding("type_floor", 0, "chip", "chip", 3, [0, 0, 1, 1], status="warn")])
    assert res.ok and res.summary()["checks"]["type_floor"] == {"status": "warn", "count": 1}
    assert res.summary()["warnings"] and not res.summary()["problems"]


# --- contrast (R-53) -------------------------------------------------------------------------------------------


def test_contrast_thresholds() -> None:
    assert contrast_required(box(role="caption")) == 7.0
    assert contrast_required(box(role="formula", size=120)) == 4.5  # formula symbols: 4.5 at any size
    assert contrast_required(box(size=32)) == 3.0
    assert contrast_required(box(size=24, weight=700)) == 3.0
    assert contrast_required(box(size=24, weight=600)) == 4.5
    assert contrast_required(box(size=31.9)) == 4.5
    assert contrast_required(box()) == 4.5  # size unknown: the body threshold


def test_contrast_findings() -> None:
    low = box(ink="#4a4a5a", bg="#202030", size=52)  # about 1.9:1 for large text (3:1 needed)
    found, measured = evaluate_box(low, fmt="landscape", frame=7, scene_index=1)
    assert measured and [(f.check, f.remedy, f.required) for f in found] == [("contrast", "scrim", 3.0)]
    assert found[0].ratio == pytest.approx(1.9, abs=0.2)
    ok = box(ink="#F4F4FA", bg="#1B1F3B")
    assert evaluate_box(ok, fmt="landscape", frame=7, scene_index=1) == ([], True)
    caption = box(role="caption", critical=False, x=160, y=940, w=1600, h=64, ink="#c0c0c0", bg="#202020")
    found, _ = evaluate_box(caption, fmt="landscape", frame=1, scene_index=0)  # about 8.6:1 > 7: fine
    assert found == []
    dim_caption = box(role="caption", critical=False, x=160, y=940, w=1600, h=64, ink="#8a8a8a", bg="#202020")
    assert [f.required for f in evaluate_box(dim_caption, fmt="landscape", frame=1, scene_index=0)[0]] == [7.0]
    unknown = box(ink="#ffffff", bg="rgba(0, 0, 0, 0.4)")
    assert evaluate_box(unknown, fmt="landscape", frame=1, scene_index=0) == ([], False)
    decoration = box(critical=False, ink="#222", bg="#202020", x=0, y=0)  # not critical, not a caption: unchecked
    assert evaluate_box(decoration, fmt="landscape", frame=1, scene_index=0) == ([], False)


# --- overflow --------------------------------------------------------------------------------------------------


def test_overflow_findings() -> None:
    spill = box(overflow=120, size=96, slot="headline")
    found, _ = evaluate_box(spill, fmt="landscape", frame=3, scene_index=0)
    assert [(f.check, f.remedy, f.slot, f.overflow) for f in found] == [("text_overflow", "shrink", "headline", 120)]
    at_floor = box(overflow=10, size=36)  # already at the 16:9 floor: nothing left to shrink
    assert evaluate_box(at_floor, fmt="landscape", frame=3, scene_index=0)[0][0].remedy is None
    at_slot_min = box(overflow=10, size=48, min=48)
    assert evaluate_box(at_slot_min, fmt="landscape", frame=3, scene_index=0)[0][0].remedy is None
    assert evaluate_box(box(overflow=0.4), fmt="landscape", frame=3, scene_index=0)[0] == []  # sub-pixel
    decorative = box(overflow=30, critical=False)
    found, _ = evaluate_box(decorative, fmt="landscape", frame=3, scene_index=0)
    assert [(f.check, f.status, f.remedy) for f in found] == [("text_overflow", "warn", None)]


def test_zone_remedy_depends_on_the_safe_layout() -> None:
    outside = box(x=20)
    assert evaluate_box(outside, fmt="landscape", frame=1, scene_index=0)[0][0].remedy == "safe_variant"
    assert evaluate_box(outside, fmt="landscape", frame=1, scene_index=0, safe=True)[0][0].remedy is None


def test_a_scene_is_safe_by_either_channel() -> None:
    """``fixes.safe`` (the fixes channel, plan §1.2) or B1's legacy ``variant == "safe"`` (an engine_version 1 timeline
    remedied before B2a: the stage still reads it, after ``fixes``)."""
    assert pf.look_is_safe({"fixes": {"safe": True, "slots": {}}}) and pf.look_is_safe({"variant": "safe"})
    assert pf.look_is_safe({"variant": "media_left", "fixes": {"safe": True, "slots": {}}})
    for look in (None, {}, {"variant": None}, {"variant": "media_left"}, {"fixes": {"safe": False, "slots": {}}},
                 {"fixes": {"safe": "yes"}}, {"fixes": True}, "safe", {"fixes": None, "variant": "SAFE"}):
        assert not pf.look_is_safe(look), look
    served = {"scenes": [{"look": {"variant": "safe"}}, {"look": None}, {"look": {"fixes": {"safe": True}}}, "junk",
                         {}]}
    assert pf.safe_scenes(served) == frozenset({0, 2})
    assert pf.safe_scenes({"scenes": None}) == frozenset() and pf.safe_scenes({}) == frozenset()


def test_evaluate_boxes_maps_scenes_and_counts() -> None:
    raw = [{"x": 20, "y": 100, "w": 100, "h": 50, "role": "headline", "critical": True, "ink": "#fff", "bg": "#000"},
           {"x": 20, "y": 100, "w": 100, "h": 50, "role": "body", "critical": True, "scene": 0},
           {"x": "bad"}, "junk"]
    found, stats = evaluate_boxes(raw, fmt="landscape", frame=150, info=INFO, safe_scenes={1})
    assert stats == {"boxes": 2, "malformed": 2, "contrast_measured": 1}
    by_slot = {f.slot: f for f in found}
    assert by_slot["headline"].scene_index == 1 and by_slot["headline"].remedy is None  # scene 1 is already safe
    assert by_slot["body"].scene_index == 0 and by_slot["body"].remedy == "safe_variant"  # the box names its scene


def test_merge_findings_keeps_the_worst_frame() -> None:
    a = Finding("contrast", 1, "body", "body", 10, [0, 0, 1, 1], "scrim", ratio=2.5, required=4.5)
    b = Finding("contrast", 1, "body", "body", 20, [0, 0, 1, 1], "scrim", ratio=1.5, required=4.5)
    c = Finding("contrast", 2, "body", "body", 30, [0, 0, 1, 1], "scrim", ratio=4.0, required=4.5)
    merged = merge_findings([a, b, c])
    assert [(f.scene_index, f.frames, f.ratio, f.frame) for f in merged] == [(1, 2, 1.5, 20), (2, 1, 4.0, 30)]
    assert a.frames == 1  # inputs are not mutated
    o1 = Finding("text_overflow", 0, "h", "h", 1, [0, 0, 1, 1], "shrink", overflow=5)
    o2 = Finding("text_overflow", 0, "h", "h", 2, [0, 0, 1, 1], None, overflow=9)
    (merged_o,) = merge_findings([o1, o2])
    assert merged_o.overflow == 9 and merged_o.remedy is None  # at its limit somewhere: no remedy


def test_a_decorative_sighting_never_masks_a_checked_one() -> None:
    # critical text mid-entrance is reported as not critical (stage.js: checked only when fully shown), so its overflow
    # is first seen as a warning; the same slot seen fully shown later must still shrink or block (B2c review round)
    early = Finding("text_overflow", 0, "body", "body", 18, [0, 0, 1, 1], None, overflow=139, status="warn")
    shown = Finding("text_overflow", 0, "body", "body", 56, [0, 0, 2, 2], None, overflow=120)
    (m,) = merge_findings([early, shown])
    assert (m.status, m.frame, m.box, m.frames, m.remedy, m.overflow) == ("open", 56, [0, 0, 2, 2], 2, None, 139)
    shrink = Finding("text_overflow", 0, "body", "body", 56, [0, 0, 1, 1], "shrink", overflow=20)
    (m,) = merge_findings([early, shrink])
    assert (m.status, m.remedy, m.overflow) == ("open", "shrink", 139)
    # the other order: a later warn sighting keeps the checked status and never clears its remedy
    (m,) = merge_findings([shrink, early])
    assert (m.status, m.remedy, m.frame, m.overflow, m.frames) == ("open", "shrink", 56, 139, 2)
    # two decorative sightings stay a warning
    (m,) = merge_findings([early, early])
    assert m.status == "warn" and m.frames == 2
    assert [f.status for f in pf._finalise(merge_findings([early, shown]), None, set())] == ["block"]


# --- remedies --------------------------------------------------------------------------------------------------


def _open(check, scene, slot, remedy):
    return Finding(check, scene, slot, slot, 1, [0, 0, 1, 1], remedy)


def test_plan_remedies() -> None:
    findings = [_open("safe_zones", 0, "headline", "safe_variant"), _open("safe_zones", 0, "body", "safe_variant"),
                _open("text_overflow", 1, "headline", "shrink"), _open("contrast", 2, "body", "scrim"),
                _open("contrast", 2, "body", "scrim"), _open("text_overflow", 1, "body", None),
                _open("contrast", 9, "body", "scrim")]
    findings.append(Finding("text_overflow", 3, "x", "x", 1, [0, 0, 1, 1], None, status="warn"))
    plan = plan_remedies(findings, fmt="portrait", min_sizes={(1, "headline"): 56.0}, has_look=lambda i: i != 9)
    assert [r.as_dict() for r in plan] == [
        {"kind": "safe_variant", "scene_index": 0},
        {"kind": "shrink", "scene_index": 1, "slot": "headline", "size": 56.0},
        {"kind": "scrim", "scene_index": 2, "slot": "body"},
    ]
    floor = plan_remedies([_open("text_overflow", 1, "headline", "shrink")], fmt="portrait")
    assert floor[0].size == 40.0  # the 9:16 floor when the stage gives no slot minimum
    for bad in (0.0, -12.0):  # a slot minimum that is no size: the floor, so fixes.slots[slot].size stays valid
        planned = plan_remedies([_open("text_overflow", 1, "headline", "shrink")], fmt="landscape",
                                min_sizes={(1, "headline"): bad})
        assert planned[0].size == 36.0


def test_apply_remedies_writes_the_look() -> None:
    """The fixes channel only (plan §1.2, from B2a step B, when the stage reads ``fixes`` first): ``look.fixes`` in its
    canonical ``{safe, slots}`` shape; B1's legacy ``variant`` and ``text_fit`` are left exactly as they were."""
    served = dynamic_timeline("landscape").model_dump(mode="json")
    served["scenes"][1]["look"]["text_fit"] = {"headline": {"size": 96, "lines": 1}, "odd": 5}
    del served["scenes"][2]["look"]
    out = apply_remedies(served, [Remedy("safe_variant", 0), Remedy("shrink", 1, "headline", 48.0),
                                  Remedy("scrim", 1, "body"), Remedy("scrim", 1, "odd"), Remedy("scrim", 2, "body"),
                                  Remedy("scrim", 7, "body")])
    look0, look1 = out["scenes"][0]["look"], out["scenes"][1]["look"]
    assert look0["fixes"] == {"safe": True, "slots": {}}
    assert look0["variant"] is None, "the safe layout is fixes.safe: the variant stays the resolver's"
    assert look0["text_fit"] == served["scenes"][0]["look"]["text_fit"]
    assert look1["fixes"] == {"safe": False, "slots": {"headline": {"size": 48.0}, "body": {"scrim": True},
                                                       "odd": {"scrim": True}}}
    assert look1["text_fit"] == {"headline": {"size": 96, "lines": 1}, "odd": 5}, "text_fit is never written"
    assert look1.get("variant") is None, "a slot remedy never touches the layout"
    assert "look" not in out["scenes"][2]
    # the input is not mutated
    assert served["scenes"][0]["look"]["variant"] is None and "body" not in served["scenes"][1]["look"]["text_fit"]
    assert "fixes" not in served["scenes"][0]["look"] and "fixes" not in served["scenes"][1]["look"]
    served["scenes"][2]["look"] = out["scenes"][1]["look"]
    tl = Timeline.model_validate(out)  # still a valid timeline, and the schema reads back exactly what was written
    assert tl.scenes[0].look.fixes.safe is True and tl.scenes[1].look.fixes.slots["headline"].size == 48.0
    assert tl.model_dump(mode="json")["scenes"][1]["look"]["fixes"] == look1["fixes"]


def test_apply_remedies_keeps_earlier_fixes_and_repairs_malformed_ones() -> None:
    served = dynamic_timeline("landscape").model_dump(mode="json")
    served["scenes"][0]["look"]["fixes"] = {"safe": True, "slots": {"title": {"scrim": True}}}
    served["scenes"][1]["look"]["fixes"] = {"safe": "yes", "slots": [1], "extra": 1}
    served["scenes"][2]["look"]["fixes"] = 5
    out = apply_remedies(served, [Remedy("shrink", 0, "title", 40.0), Remedy("scrim", 1, "body"),
                                  Remedy("safe_variant", 2), Remedy("shrink", 0, "body", None),
                                  Remedy("unknown", 0, "body")])
    assert out["scenes"][0]["look"]["fixes"] == {"safe": True, "slots": {"title": {"scrim": True, "size": 40.0}}}
    assert out["scenes"][0]["look"]["text_fit"] == served["scenes"][0]["look"]["text_fit"]  # legacy: never written
    assert out["scenes"][1]["look"]["fixes"] == {"safe": False, "slots": {"body": {"scrim": True}}, "extra": 1}
    assert out["scenes"][2]["look"]["fixes"] == {"safe": True, "slots": {}}
    Timeline.model_validate(out)


def test_the_refresher_path_reapplies_the_same_remedies() -> None:
    """motion.job.refresher re-serves the timeline and applies the preflight's remedies again on every page load:
    applying them to a fresh copy gives the same timeline, and applying them twice changes nothing."""
    served = dynamic_timeline("portrait").model_dump(mode="json")
    remedies = [Remedy("safe_variant", 1), Remedy("shrink", 0, "headline", 56.0), Remedy("scrim", 2, "body")]
    once = apply_remedies(served, remedies)
    assert apply_remedies(json.loads(json.dumps(served)), remedies) == once
    assert apply_remedies(once, remedies) == once


# --- sampling --------------------------------------------------------------------------------------------------


def _beat_frames(tl: Timeline, info: StageInfo) -> set[int]:
    """Every beat's settled frame (``BEAT_SETTLE_S`` after its start), as preflight samples it."""
    out = set()
    for scene in tl.scenes:
        sc = info.scenes[scene.index]
        for beat in scene.beats:
            out.add(min(max(sc.f0 + int(-(-(beat.start + pf.BEAT_SETTLE_S) * 30 // 1)), sc.f0), sc.f1 - 1))
    return out


def test_sample_frames_landscape() -> None:
    tl = dynamic_timeline("landscape")
    frames = sample_frames(INFO, tl)
    assert frames == sorted(set(frames))
    spread = {(2 * k + 1) * 366 // 48 for k in range(24)}
    assert spread <= set(frames) and len(spread) == 24
    assert {0, 139, 140, 278, 279, 365} <= set(frames)
    beats = _beat_frames(tl, INFO)
    assert beats == {33, 86, 173, 226, 312}  # 0.5 s and 2.25 s into each scene, + BEAT_SETTLE_S
    # a card shown only between two beats (collapsed into the "earlier" chip before the scene ends) is sampled while
    # on screen: no spread frame or scene bound lands there
    assert {86, 226}.isdisjoint(spread | {0, 139, 140, 278, 279, 365})
    assert set(frames) == spread | {0, 139, 140, 278, 279, 365} | beats
    assert sample_frames(INFO) == sorted(spread | {0, 139, 140, 278, 279, 365}), "no timeline: spread and bounds"


def test_sample_frames_portrait_adds_caption_chunk_starts() -> None:
    tl = dynamic_timeline("portrait")
    info = StageInfo(1080, 1920, 30, 366, INFO.scenes)
    frames = set(sample_frames(info, tl))
    starts = set()
    for scene in tl.scenes:
        sc = info.scenes[scene.index]
        for chunk in scene.kinetic_captions:
            starts.add(min(max(sc.f0 + int(-(-chunk.start * 30 // 1)), sc.f0), sc.f1 - 1))
    assert starts and starts <= frames
    assert frames == starts | set(sample_frames(info, dynamic_timeline("landscape")))


def test_sample_frames_portrait_without_chunks_samples_phrase_captions() -> None:
    """B1 reels carry phrase captions (``Timeline.captions``) until kinetic.py: each one's first frame is sampled."""
    data = dynamic_timeline("portrait").model_dump(mode="json")
    for scene in data["scenes"]:
        scene.pop("kinetic_captions", None)
    tl = Timeline.model_validate(data)
    info = StageInfo(1080, 1920, 30, 366, INFO.scenes)
    frames = set(sample_frames(info, tl))
    starts = {min(365, int(-(-c.start * 30 // 1))) for c in tl.captions}
    assert starts and starts <= frames
    assert frames == starts | set(sample_frames(info, dynamic_timeline("landscape")))


def test_sample_frames_short_and_odd_stages() -> None:
    tiny = StageInfo(1920, 1080, 30, 5, (StageScene(0, 0, 5),))
    assert sample_frames(tiny) == [0, 1, 2, 3, 4]
    assert sample_frames(StageInfo(1920, 1080, 30, 1, (StageScene(0, 0, 1),))) == [0]


# --- stage info ------------------------------------------------------------------------------------------------


def test_stage_info_parsing_and_check() -> None:
    raw = {"width": 1920, "height": 1080, "fps": 30, "frames": 366,
           "scenes": [{"index": 0, "f0": 0, "f1": 140}, {"index": 1, "f0": 140, "f1": 366}]}
    info = StageInfo.parse(raw)
    assert info.scene_at(139).index == 0 and info.scene_at(140).index == 1 and info.scene_at(366) is None
    info.check(size=(1920, 1080), fps=30, frames=366)
    with pytest.raises(StageError) as ei:
        info.check(size=(1080, 1920), fps=30, frames=366)
    assert ei.value.code == "stage_contract" and not ei.value.retryable
    for bad in (None, [], {**raw, "fps": 0}, {**raw, "width": 1.5}, {**raw, "frames": True},
                {**raw, "scenes": "x"}, {**raw, "scenes": [{"index": 0, "f0": 5, "f1": 5}]},
                {**raw, "scenes": [{"index": 0, "f0": 0, "f1": 400}]}, {**raw, "scenes": [3]}):
        with pytest.raises(StageError):
            StageInfo.parse(bad)


# --- statuses after the remedies ------------------------------------------------------------------------------


def test_finalise_statuses() -> None:
    first = [_open("safe_zones", 0, "headline", "safe_variant"),   # fixed by the variant
             _open("text_overflow", 1, "headline", "shrink"),      # still overflowing -> block
             _open("contrast", 2, "body", "scrim"),                # still low -> warn (never blocks)
             _open("text_overflow", 1, "body", None),              # no remedy -> block at once
             _open("contrast", 3, "body", None),                   # no remedy (no look) -> warn
             Finding("text_overflow", 4, "deco", "deco", 1, [0, 0, 1, 1], None, status="warn")]
    second = [_open("text_overflow", 1, "headline", None), _open("contrast", 2, "body", "scrim"),
              _open("safe_zones", 1, "body", "safe_variant"),  # new in round 2
              _open("text_overflow", 1, "body", None)]  # scene 1 was re-scanned: seen again, still one item
    planned = {("safe_variant", 0, ""), ("shrink", 1, "headline"), ("scrim", 2, "body")}
    findings = pf._finalise(first, second, planned)
    idents = [(f.check, f.scene_index, f.slot) for f in findings]
    assert len(idents) == len(set(idents)), idents  # one finding per (check, scene, slot)
    final = dict(zip(idents, findings, strict=True))
    assert final[("safe_zones", 0, "headline")].status == "fixed"
    assert final[("text_overflow", 1, "headline")].status == "block"
    assert final[("text_overflow", 1, "headline")].remedy == "shrink"
    assert final[("contrast", 2, "body")].status == "warn"
    assert final[("text_overflow", 1, "body")].status == "block"
    assert final[("contrast", 3, "body")].status == "warn"
    assert final[("text_overflow", 4, "deco")].status == "warn"
    assert final[("safe_zones", 1, "body")].status == "block" and final[("safe_zones", 1, "body")].remedy is None
    # without a second round (no remedies planned) everything open is escalated
    assert {f.status for f in pf._finalise([_open("safe_zones", 0, "h", "safe_variant")], None, set())} == {"block"}
    assert len(findings) == 7 and set(idents) == {(f.check, f.scene_index, f.slot) for f in first} | {
        ("safe_zones", 1, "body")}


def test_finalise_never_duplicates_what_the_second_round_sees_again() -> None:
    """The second round re-scans every sampled frame of a touched scene, so it sees that scene's warnings and its
    findings without a remedy again: they stay one item each (no doubled items, problems, warnings or counts)."""
    first = [_open("text_overflow", 1, "headline", "shrink"),  # remedied, gone in round 2 -> fixed
             _open("text_overflow", 1, "body", None),  # at its floor: no remedy -> block
             Finding("type_floor", 1, "chip", "chip", 1, [0, 0, 1, 1], None, status="warn"),
             Finding("text_overflow", 1, "deco", "deco", 1, [0, 0, 1, 1], None, status="warn"),
             _open("contrast", 1, "label", None)]  # no remedy (no look): warns
    second = [_open("text_overflow", 1, "body", None),
              Finding("type_floor", 1, "chip", "chip", 1, [0, 0, 1, 1], None, status="warn"),
              Finding("text_overflow", 1, "deco", "deco", 1, [0, 0, 1, 1], None, status="warn"),
              _open("contrast", 1, "label", None)]
    findings = pf._finalise(first, second, {("shrink", 1, "headline")})
    assert [(f.check, f.slot, f.status) for f in findings] == [
        ("text_overflow", "headline", "fixed"), ("text_overflow", "body", "block"), ("type_floor", "chip", "warn"),
        ("text_overflow", "deco", "warn"), ("contrast", "label", "warn")]
    assert findings[1] is first[1] and findings[2] is first[2]  # the first round's own (all its frames) are kept
    res = PreflightResult(format="landscape", frames=366, fps=30, rounds=2, findings=findings)
    s = res.summary()
    assert s["checks"]["text_overflow"] == {"status": "block", "count": 3}
    assert s["checks"]["type_floor"] == {"status": "warn", "count": 1}
    assert s["checks"]["contrast"] == {"status": "warn", "count": 1}
    assert len(s["items"]) == 5 and len(s["problems"]) == 1 and len(s["warnings"]) == 3


# --- result schema ---------------------------------------------------------------------------------------------


def _result() -> PreflightResult:
    findings = [Finding("safe_zones", 1, "headline", "headline", 150, [20, 120, 800, 115], "safe_variant",
                        status="block", text="Too wide"),
                Finding("contrast", 0, "body", "body", 30, [140, 320, 1200, 72], "scrim", ratio=1.9, required=3.0,
                        status="fixed"),
                Finding("contrast", 1, "body", "body", 160, [140, 320, 1200, 72], "scrim", ratio=2.2, required=3.0,
                        status="warn")]
    return PreflightResult(format="landscape", frames=366, fps=30, sampled=list(range(30)), rounds=2, seconds=1.234,
                           webgl=False, findings=findings, remedies=[Remedy("scrim", 0, "body")],
                           stats={"frames": 40, "boxes": 120},
                           scenes={0: ("p-title", "Ohm's Law"), 1: ("p-law", "The law")})


def test_result_summary_schema() -> None:
    res = _result()
    s = res.summary()
    json.dumps(s)
    assert s["schema"] == PREFLIGHT_SCHEMA == 1 and s["ok"] is False and not res.ok
    assert set(s) == {"schema", "ok", "format", "frames", "fps", "sampled", "rounds", "seconds", "webgl", "checks",
                      "items", "remedies", "stats", "problems", "warnings"}
    assert s["checks"] == {"text_overflow": {"status": "pass", "count": 0},
                           "safe_zones": {"status": "block", "count": 1},
                           "contrast": {"status": "warn", "count": 2},
                           "type_floor": {"status": "pass", "count": 0}}
    assert [(i["scene_index"], i["check"], i["status"]) for i in s["items"]] == [
        (0, "contrast", "fixed"), (1, "safe_zones", "block"), (1, "contrast", "warn")]
    block = s["items"][1]
    assert block["blocking"] and block["action"] == "Shorten text" and block["scene_id"] == "p-law"
    assert block["t"] == 5.0 and block["remedy"] is None and block["text"] == "Too wide"
    fixed = s["items"][0]
    assert fixed["remedy"] == "scrim" and fixed["action"] is None and fixed["ratio"] == 1.9
    assert s["problems"] == [block["message"] + " (Shorten text)"]
    assert s["warnings"] == [s["items"][2]["message"]] and "2.2:1" in s["warnings"][0]
    assert s["remedies"] == [{"kind": "scrim", "scene_index": 0, "slot": "body"}] and s["seconds"] == 1.23
    qa = res.as_qa()
    assert qa["ok"] is False and qa["engine"] == "motion" and qa["stage"] == "preflight" and qa["preflight"] == s


def test_raise_if_blocked() -> None:
    res = _result()
    with pytest.raises(PreflightBlocked) as ei:
        raise_if_blocked(res)
    assert isinstance(ei.value, FatalJobError) and ei.value.code == "preflight_blocked"
    assert "scene 2" in str(ei.value) and "Shorten text" in str(ei.value) and ei.value.result is res
    res.findings = [f for f in res.findings if f.status != "block"]
    raise_if_blocked(res)  # warnings and fixes never block


# --- run_preflight on a stand-in page: the caption layer the render burns, the stage's frame count ---------------


class _Page:
    """A loaded render page (stands in for ``stage_page.StagePage``), like the real stage (``web/js/dynamic``) where it
    matters here: ``info()`` is ``core.js stageInfo`` (every scene keeps at least one frame), a render-mode page draws
    captions as ``presentation.captions_burn`` says until ``setLayers()`` is called, and a reload starts over. Every
    frame shows a headline (outside the safe area in the ``unsafe`` scenes until the scene is on its safe variant) and,
    with the caption layer on, a caption at its smallest size that overflows its box (a block: nothing to shrink)."""

    webgl = False

    def __init__(self, served: dict[str, Any], unsafe: frozenset[int], channel: str = "both") -> None:
        self.unsafe = unsafe
        # where the stand-in reads the safe layout: "both" (the stage: fixes.safe, else B1's variant), or only one of
        # them, "fixes" or "legacy" (variant == "safe")
        self.channel = channel
        self.calls: list[Any] = []
        self.guard_trips: list[str] = []
        self.frame = 0
        self._boot(served)

    def _boot(self, served: dict[str, Any]) -> None:
        self.served = served
        self.captions = bool((served.get("presentation") or {}).get("captions_burn"))
        tl = Timeline.model_validate(served)
        bounds = scene_frame_bounds(tl, tl.fps)
        self.stage_info = StageInfo(tl.width, tl.height, tl.fps, total_frames(bounds),
                                    tuple(StageScene(i, f0, f1) for i, (f0, f1) in enumerate(bounds)))

    async def load(self, served: dict[str, Any]) -> StageInfo:
        self.calls.append("load")
        self._boot(served)
        return self.stage_info

    async def info(self) -> StageInfo:
        return self.stage_info

    async def set_layers(self, *, captions: bool = True, stage: bool = True) -> None:
        self.calls.append(("set_layers", captions, stage))
        self.captions = captions

    async def seek(self, frame: int) -> str:
        self.frame = frame
        return ""

    async def text_boxes(self) -> list[dict[str, Any]]:
        sc = self.stage_info.scene_at(self.frame)
        scene = sc.index if sc is not None else 0
        look = self.served["scenes"][scene].get("look") or {}
        by_fixes = self.channel != "legacy" and (look.get("fixes") or {}).get("safe") is True
        safe = by_fixes or (self.channel != "fixes" and look.get("variant") == "safe")
        x = 20 if scene in self.unsafe and not safe else 200
        boxes = [{"x": x, "y": 200, "w": 600, "h": 100, "role": "headline", "critical": True, "ink": "#F4F4FA",
                  "bg": "#1B1F3B", "overflow": 0, "slot": "headline", "scene": scene}]
        if self.captions:
            boxes.append({"x": 160, "y": 940, "w": 1600, "h": 64, "role": "caption", "critical": False,
                          "ink": "#FFFFFF", "bg": "#111118", "overflow": 40, "slot": "caption", "size": 48,
                          "min": 48, "scene": scene})
        return boxes


def _stand_in(monkeypatch: pytest.MonkeyPatch, *, unsafe: frozenset[int] = frozenset(),
              channel: str = "both") -> list[_Page]:
    """``run_preflight`` opens :class:`_Page` s instead of Chromium; returns the pages it opened."""
    pages: list[_Page] = []

    class _Browser:
        async def open(self, *, token: str, served_timeline: dict[str, Any], frames: Any,
                       ready_timeout_s: float) -> _Page:
            pages.append(_Page(served_timeline, unsafe, channel))
            return pages[-1]

        def check_policy(self) -> None:
            return None

    @contextlib.asynccontextmanager
    async def browser(*args: Any, **kwargs: Any) -> AsyncIterator[_Browser]:
        yield _Browser()

    monkeypatch.setattr(pf, "stage_browser", browser)
    return pages


def _captions_burn(tl: Timeline, on: bool) -> Timeline:
    return tl.model_copy(update={"presentation": tl.presentation.model_copy(update={"captions_burn": on})})


@pytest.mark.parametrize(("presented", "burned"), [(False, True), (True, False), (True, True), (False, False)])
async def test_preflight_checks_the_caption_layer_the_render_burns(monkeypatch: pytest.MonkeyPatch, presented: bool,
                                                                   burned: bool) -> None:
    """The page draws captions as the presentation says; the render's own choice (the request's ``burn_captions``
    wins, ``motion.job.caption_modes``) must decide what is checked: captions burned but not presented were never
    checked, and captions presented but not burned blocked a render that shows none."""
    pages = _stand_in(monkeypatch)
    res = await run_preflight(settings(), _captions_burn(dynamic_timeline("landscape"), presented), token="t",
                              captions=burned)
    assert pages[0].calls[0] == ("set_layers", burned, True)  # before the first frame is checked
    caption_findings = [f for f in res.findings if f.role == "caption"]
    assert bool(caption_findings) is burned and res.ok is not burned
    assert all(f.check == "text_overflow" and f.status == "block" for f in caption_findings)


async def test_the_reloaded_page_keeps_the_render_caption_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    """After the remedies the page is loaded again and starts over on the presentation's caption layer: the second
    round must check the render's layer too, or it reports captions that are never burned (here: a block)."""
    pages = _stand_in(monkeypatch, unsafe=frozenset({1}))
    res = await run_preflight(settings(), _captions_burn(dynamic_timeline("landscape"), True), token="t",
                              captions=False)
    assert pages[0].calls == [("set_layers", False, True), "load", ("set_layers", False, True)]
    assert res.ok and res.rounds == 2 and [(r.kind, r.scene_index) for r in res.remedies] == [("safe_variant", 1)]
    assert [(f.check, f.scene_index, f.status) for f in res.findings] == [("safe_zones", 1, "fixed")]


@pytest.mark.parametrize("channel", ["legacy", "fixes"])
async def test_the_safe_layout_remedy_reaches_the_stage_through_the_fixes_channel(monkeypatch: pytest.MonkeyPatch,
                                                                                  channel: str) -> None:
    """apply_remedies writes ``fixes`` only (B2a step B): a stage reading ``fixes.safe`` (the stage reads ``fixes``
    first) draws the safe layout, so the finding is fixed in the second round; one reading only B1's ``variant`` (no
    such stage is left) never sees the remedy, so the finding blocks. And preflight reads both channels: a scene
    already safe by the channel this stage does NOT read is still drawn outside, and it is blocked at once (it is on
    its safe layout already, so there is no remedy left), never "remedied" with what it has."""
    pages = _stand_in(monkeypatch, unsafe=frozenset({1}), channel=channel)
    tl = dynamic_timeline("landscape")
    res = await run_preflight(settings(), tl, token="t", captions=False)
    assert res.rounds == 2 and [(r.kind, r.scene_index) for r in res.remedies] == [("safe_variant", 1)]
    status = "fixed" if channel == "fixes" else "block"
    assert res.ok is (channel == "fixes")
    assert [(f.check, f.scene_index, f.status) for f in res.findings] == [("safe_zones", 1, status)]
    look = res.timeline["scenes"][1]["look"]
    assert look["fixes"] == {"safe": True, "slots": {}} and look["variant"] is None
    assert pages[0].calls.count("load") == 1
    already = tl.model_dump(mode="json")
    if channel == "fixes":
        already["scenes"][1]["look"]["variant"] = "safe"
    else:
        already["scenes"][1]["look"]["fixes"] = {"safe": True, "slots": {}}
    res = await run_preflight(settings(), tl, token="t", served_timeline=already, captions=False)
    assert not res.ok and res.rounds == 1 and res.remedies == []
    assert [(f.check, f.scene_index, f.status) for f in res.findings] == [("safe_zones", 1, "block")]


def _b2_timeline(fmt: str = "landscape") -> Timeline:
    """A Dynamic timeline the B2 resolver builds (engine_version 2: ten stored tokens per look, variant None)."""
    from aadhi.compose.timeline import build_timeline
    from aadhi.config import Settings
    from aadhi.schemas.screenplay import Screenplay

    from ...schemas.v3_fixtures import PRESENTATION
    from ..factories import make_manifest, screenplay_dict

    data = screenplay_dict()
    data["presentation"] = {**PRESENTATION, "format": fmt, "hook": None, "theme_family": "midnight_indigo",
                            "captions_burn": True, "music": {"source": "procedural", "provider": "procedural"}}
    sp = Screenplay.model_validate(data)
    return build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None, render_fps=25,
                                                                   dynamic_enabled=True))


async def test_a_remedied_b2_timeline_never_carries_the_safe_variant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan §1.2: ``look.variant`` is direct.py's choice and never "safe"; preflight's safe layout (and every other
    remedy) travels in ``look.fixes`` alone. A B2-resolved timeline whose scenes all get every remedy keeps each look's
    variant and text_fit exactly as built; through run_preflight too (the remedied timeline the capture uses)."""
    tl = _b2_timeline()
    built = tl.model_dump(mode="json")
    assert built["presentation"]["engine_version"] == 2
    every = [r for n in range(len(built["scenes"]))
             for r in (Remedy("safe_variant", n), Remedy("shrink", n, "title", 40.0), Remedy("scrim", n, "kicker"))]
    out = apply_remedies(built, every)
    for before, after in zip(built["scenes"], out["scenes"], strict=True):
        assert after["look"]["variant"] == before["look"]["variant"] and after["look"]["variant"] != "safe"
        assert after["look"].get("text_fit") == before["look"].get("text_fit")
        assert after["look"]["fixes"] == {"safe": True, "slots": {"title": {"size": 40.0}, "kicker": {"scrim": True}}}
    Timeline.model_validate(out)
    _stand_in(monkeypatch, unsafe=frozenset({1, 2}), channel="fixes")
    res = await run_preflight(settings(), tl, token="t", captions=False)
    assert res.ok and sorted(r.scene_index for r in res.remedies if r.kind == "safe_variant") == [1, 2]
    assert all(s["look"]["variant"] != "safe" for s in res.timeline["scenes"])
    assert [s["look"].get("fixes", {}).get("safe", False) for s in res.timeline["scenes"]][:4] == [False, True, True,
                                                                                                    False]


async def test_preflight_expects_the_stage_frame_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every scene keeps at least one frame (``scene_frame_bounds``, ``core.js stageInfo``), so a last scene shorter
    than half a frame makes the video one frame longer than its rounded duration: the engine captures, and the page
    reports, that many. Preflight expected the rounded duration and failed such a lecture as a broken stage."""
    pages = _stand_in(monkeypatch)
    tl = motion_timeline([2.0, 2.0, 0.01], size=(1920, 1080))
    assert expected_frame_count(tl.total_duration, 30) == 120 and total_frames(scene_frame_bounds(tl)) == 121
    res = await run_preflight(settings(), tl, token="t")
    assert res.ok and res.frames == pages[0].stage_info.frames == 121
    assert 120 in res.sampled  # the short scene's only frame is checked too
