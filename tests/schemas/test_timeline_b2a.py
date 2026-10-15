"""The B2a additions to the timeline schema (plan B2 §1.2 ResolvedLook, §1.3 TimelinePresentation; deviation register
2, 4, 21, 22): shapes, limits and sanitisation, and that each is left out of the JSON at its default."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from aadhi.schemas.timeline import (
    ICON_PATH_MAX_CHARS,
    ICON_PATHS_MAX,
    ICONS_PER_SCENE_MAX,
    MAX_ENTITY_COLORS,
    LookFixes,
    LookIcon,
    LookRefs,
    RefAnchor,
    ResolvedLook,
    SlotFix,
    TimelinePresentation,
)

LOOK = {"template": "board_generic", "tokens": {"bg": "#101820"}, "background": {"preset": "mesh_drift"},
        "transition_in": {"kind": "cut", "seconds": 0.0}}
PRESENTATION = {"style": "dynamic", "format": "landscape", "content": {"ink": "#FFFFFF"}, "fonts": {"display": "outfit"},
                "personality": "crisp", "caption_style": "clean_pill", "cue_term_style": "underline",
                "captions_burn": True, "sfx": False, "motion": "standard", "generated_seconds": 10.0}
ICON = {"name": "battery", "set": "lucide", "version": "0.469.0", "viewBox": [0, 0, 24, 24], "stroke_width": 2,
        "filled": False, "paths": ["M4 6h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4z", "m22 11v2"]}


def look(**kw: Any) -> ResolvedLook:
    return ResolvedLook.model_validate({**LOOK, **kw})


def presentation(**kw: Any) -> TimelinePresentation:
    return TimelinePresentation.model_validate({**PRESENTATION, **kw})


def test_defaults_are_left_out_and_values_kept() -> None:
    assert look().model_dump(mode="json") == {**LOOK, "variant": None, "hold_reason": None, "presenter": None,
                                              "text_fit": {}}
    assert presentation().model_dump(mode="json") == {**PRESENTATION, "safe_zone": "universal", "engine_version": 1}
    full = presentation(content_alt={"ink": "#141822"}, family="paper_ink", mode="light", motion_scale=0.85,
                        entity_colors={"V": 0, "I": 1}).model_dump(mode="json")
    assert (full["content_alt"], full["family"], full["mode"], full["motion_scale"], full["entity_colors"]) == (
        {"ink": "#141822"}, "paper_ink", "light", 0.85, {"V": 0, "I": 1})
    assert "motion_scale" not in presentation(motion_scale=1.0).model_dump(mode="json")  # 1 = B1's timings
    # B2c: the personality's text entrance (ctx.motion), left out when unset
    assert presentation(text_entrance="baseline_reveal").model_dump(mode="json")["text_entrance"] == "baseline_reveal"
    assert "text_entrance" not in presentation().model_dump(mode="json")
    with pytest.raises(ValidationError):
        presentation(text_entrance="Not An Id")
    rich = look(fixes={"safe": True}, icons={"i1": ICON}, refs={"next": {"title": "Next"}}, accent_fade=0.8,
                continues_from="s-a").model_dump(mode="json")
    assert rich["fixes"] == {"safe": True, "slots": {}} and rich["refs"] == {"next": {"title": "Next"}}
    assert (rich["accent_fade"], rich["continues_from"]) == (0.8, "s-a") and list(rich["icons"]) == ["i1"]
    again = ResolvedLook.model_validate(json.loads(json.dumps(rich))).model_dump(mode="json")
    assert again == rich


def test_fixes_have_one_canonical_shape() -> None:
    """``{safe, slots}`` always both keys; a slot carries only the keys its remedies set (what preflight writes)."""
    f = LookFixes.model_validate({"slots": {"title": {"scrim": True}, "body": {"size": 44}}})
    assert f.model_dump(mode="json") == {"safe": False, "slots": {"title": {"scrim": True}, "body": {"size": 44.0}}}
    assert SlotFix().model_dump(mode="json") == {}
    for bad in ({"slots": {"body": {"size": 0}}}, {"slots": {"body": {"size": -3}}}, {"slots": {"": {"scrim": True}}},
                {"slots": {"x" * 61: {}}}, {"safe": "maybe"}):
        with pytest.raises(ValidationError):
            LookFixes.model_validate(bad)


def test_icons_hold_path_data_only() -> None:
    """Plan §1.2: normalised to path ``d``, capped, sanitised (the stage sanitises again)."""
    assert LookIcon.model_validate(ICON).model_dump(mode="json")["viewBox"] == [0.0, 0.0, 24.0, 24.0]
    hostile = ['M0 0" onload="alert(1)', "M0 0 <script>", "M0 0 url(#x)", "L0 0", "", "M0 0;", "M0 0 &amp;",
               "javascript:M0", "M" + "1" * ICON_PATH_MAX_CHARS]
    for d in hostile:
        with pytest.raises(ValidationError):
            LookIcon.model_validate({**ICON, "paths": [d]})
    for bad in ({"paths": []}, {"paths": ["M0 0"] * (ICON_PATHS_MAX + 1)}, {"viewBox": [0, 0, 0, 24]},
                {"viewBox": [0, 0, 24]}, {"stroke_width": 9}, {"name": ""}):
        with pytest.raises(ValidationError):
            LookIcon.model_validate({**ICON, **bad})
    assert LookIcon.model_validate({**ICON, "paths": ["  m1.5e-2,3 L4-5 Z"]}).paths == ["m1.5e-2,3 L4-5 Z"]
    too_many = {f"i{k}": ICON for k in range(ICONS_PER_SCENE_MAX + 1)}
    with pytest.raises(ValidationError):
        look(icons=too_many)


def test_refs() -> None:
    refs = LookRefs.model_validate({
        "roadmap": [{"title": "Basics", "state": "done"}, {"title": "The law", "state": "current"},
                    {"title": "Practice", "state": "upcoming"}],
        "anchor": {"kind": "formula", "tex": "V = IR"}, "prediction": {"text": "Brighter?", "resolve_t": 4.2},
        "next": {"title": "Practice"}, "accent_next": "#B05641"})
    dumped = refs.model_dump(mode="json")
    assert dumped["anchor"] == {"kind": "formula", "tex": "V = IR"} and dumped["accent_next"] == "#B05641"
    assert LookRefs().model_dump(mode="json") == {}
    for bad in ({"roadmap": [{"title": "x", "state": "later"}]}, {"accent_next": "#B0564"}, {"accent_next": "red"},
                {"prediction": {"text": "x", "resolve_t": -1}}, {"roadmap": [{"title": "x", "state": "done"}] * 13}):
        with pytest.raises(ValidationError):
            LookRefs.model_validate(bad)
    for body in ({}, {"text": "a", "tex": "b"}):
        with pytest.raises(ValidationError, match="exactly one of text and tex"):
            RefAnchor.model_validate({"kind": "takeaway", **body})
    assert RefAnchor.model_validate({"kind": "takeaway", "text": "Ohm", "icon": "zap"}).icon == "zap"


@pytest.mark.parametrize(("field", "value"), [
    ("accent_fade", 0.59), ("accent_fade", 1.01), ("continues_from", ""), ("continues_from", "x" * 65)])
def test_look_limits(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        look(**{field: value})


@pytest.mark.parametrize(("field", "value"), [
    ("entity_colors", {"V": 3}), ("entity_colors", {"V": -1}), ("entity_colors", {"": 0}),
    ("entity_colors", {f"e{k}": 0 for k in range(MAX_ENTITY_COLORS + 1)}), ("motion_scale", 0.0),
    ("motion_scale", 4.5), ("mode", "sepia"), ("family", "Not An Id"), ("engine_version", 0)])
def test_presentation_limits(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        presentation(**{field: value})


def test_entity_colors_take_exactly_what_the_screenplay_allows() -> None:
    """The resolver copies Presentation.entity_colors: whatever the screenplay accepts, the timeline accepts, and the
    limits are the same."""
    from aadhi.schemas.screenplay import Presentation
    from tests.schemas.v3_fixtures import PRESENTATION as SCREENPLAY_PRESENTATION

    def screenplay_ok(colors: dict[str, int]) -> bool:
        try:
            Presentation.model_validate({**SCREENPLAY_PRESENTATION, "entity_colors": colors})
        except ValidationError:
            return False
        return True

    def timeline_ok(colors: dict[str, int]) -> bool:
        try:
            presentation(entity_colors=colors)
        except ValidationError:
            return False
        return True

    cases = [{f"e{k}": k % 3 for k in range(MAX_ENTITY_COLORS)}, {f"e{k}": 0 for k in range(MAX_ENTITY_COLORS + 1)},
             {"x" * 80: 2}, {"x" * 81: 2}, {"V": 3}, {"V": -1}, {"": 0}, {"\\omega": 1}]
    assert [screenplay_ok(c) for c in cases] == [timeline_ok(c) for c in cases] == [
        True, False, True, False, False, False, False, True]


B1_FONTS = ({"display": "outfit", "body": "inter", "mono": "jetbrains_mono"}, {"display": "inter", "body": "inter"},
            {"display": "poppins", "body": "inter", "mono": "jetbrains_mono"}, {"display": "outfit"}, {})


@pytest.mark.parametrize("fonts", B1_FONTS)
def test_b1_fonts_read_and_write_back_byte_identically(fonts: dict[str, str]) -> None:
    """B2b types ``presentation.fonts`` (plan §1.3, ``TimelineFonts``): every key is left out when unset and the keys
    serialise in B1's order, so a stored B1 or B2a timeline's fonts come back exactly as stored."""
    dumped = presentation(fonts=fonts).model_dump(mode="json")["fonts"]
    assert json.dumps(dumped) == json.dumps(fonts)


def test_b2_fonts_and_their_limits() -> None:
    b2 = {"display": "poppins", "body": "atkinson_hyperlegible_next", "mono": "jetbrains_mono", "indic": "anek"}
    assert presentation(fonts=b2).model_dump(mode="json")["fonts"] == b2
    assert list(presentation(fonts={"indic": "noto_sans", "display": "outfit"}).model_dump(mode="json")["fonts"]) == [
        "display", "indic"]
    # an unknown role is dropped, as every schema model ignores unknown keys (the resolver never wrote one)
    assert presentation(fonts={"display": "outfit", "emoji": "noto"}).model_dump(mode="json")["fonts"] == {
        "display": "outfit"}
    for bad in ({"display": "Poppins"}, {"display": "a b"}, {"indic": ""}, {"display": 7}, {"display": "x" * 41},
                {"display": "'Inter';}"}, {"mono": "url(x)"}):  # catalogue ids only: never CSS text
        with pytest.raises(ValidationError):
            presentation(fonts=bad)
