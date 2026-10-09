"""v1 import: mascot placement slips are repaired and v1's implicit placement rule is kept."""

from __future__ import annotations

import pytest

from aadhi.legacy import convert_legacy
from aadhi.schemas.screenplay import Screenplay

SHORT_HTML = "<p>" + "x" * 200 + "</p>"  # <= 300 characters
LONG_HTML = "<p>" + "x" * 400 + "</p>"


def position(v1_scene: dict) -> tuple[str, list[str]]:
    sp, warnings = convert_legacy({"scenes": [{"narration": "Hello there.", **v1_scene}]})
    Screenplay.model_validate(sp.model_dump(mode="json"))
    return sp.scenes[0].mascot_position, warnings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("popup", "popup_bottom_left"), ("Popup", "popup_bottom_left"), ("none", "hidden"), ("NONE", "hidden"),
     ("RIGHT", "right"), ("popup_bottom_right", "popup_bottom_right")],
)
def test_aliases_and_case_are_repaired_silently(raw: str, expected: str) -> None:
    got, warnings = position({"type": "content", "html": LONG_HTML, "aadhi_position": raw})
    assert got == expected
    assert not any("mascot position" in w for w in warnings)


def test_unknown_positions_still_warn_and_keep_the_default() -> None:
    got, warnings = position({"type": "content", "html": SHORT_HTML, "aadhi_position": "upside_down"})
    assert got == "left"
    assert any("mascot position" in w for w in warnings)


@pytest.mark.parametrize(
    ("scene", "expected"),
    [
        ({"type": "content", "html": SHORT_HTML}, "left"),
        ({"type": "title", "html": SHORT_HTML}, "left"),
        ({"type": "content", "html": LONG_HTML}, "hidden"),  # v1 hid Aadhi on long boards
        ({"type": "example", "html": SHORT_HTML}, "hidden"),
        ({"type": "chapter_card", "title": "Part 2"}, "hidden"),
        ({"type": "content", "html": LONG_HTML, "force_background": "mascot"}, "left"),
        ({"type": "content", "html": SHORT_HTML, "force_background": "none"}, "hidden"),
        ({"type": "content", "html": SHORT_HTML, "force_background": "auto"}, "left"),
        ({"type": "ai_video", "prompt": "A river at dawn"}, "left"),  # v1 kept Aadhi beside AI videos
    ],
)
def test_v1_implicit_rule_without_aadhi_position(scene: dict, expected: str) -> None:
    got, _ = position(scene)
    assert got == expected


def test_long_board_split_into_parts_keeps_the_placement() -> None:
    items = "".join(f"<p>Point number {i} about the topic.</p>" for i in range(20))  # > 12 items: continued scene
    sp, _ = convert_legacy({"scenes": [{"type": "content", "html": items, "narration": "Let us go."}]})
    assert len(sp.scenes) == 2
    assert [s.mascot_position for s in sp.scenes] == ["hidden", "hidden"]


@pytest.mark.parametrize("raw", ["hidden", "Hidden", "none"])
def test_ai_video_hidden_keeps_aadhi_on_the_left_like_v1(raw: str) -> None:
    got, _ = position({"type": "ai_video", "prompt": "A river at dawn", "aadhi_position": raw})
    assert got == "left"


@pytest.mark.parametrize("raw", ["right", "popup", "center"])
def test_ai_video_explicit_visible_position_is_kept(raw: str) -> None:
    got, _ = position({"type": "ai_video", "prompt": "A river at dawn", "aadhi_position": raw})
    assert got == {"popup": "popup_bottom_left"}.get(raw, raw)
