"""Schema v3 test documents (design §6.7, §8.2): a maximal Dynamic screenplay that uses every v3 field, enum value
and feature, plus helpers to add one feature to a classic screenplay.

``tests/schemas/fixtures/screenplay_v3_maximal.json`` is the canonical dump of ``maximal_screenplay_dict()`` (kept in
sync by ``tests/schemas/test_v3_schema.py``; rewrite it with ``python -m tests.schemas.v3_fixtures``). The
transform-survival tests (Python and node) use that file: every transform must give it back unchanged.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "screenplay_v3_maximal.json"

PRESENTATION: dict[str, Any] = {
    "style": "dynamic",
    "format": "portrait",
    "length_profile": "platform_safe",
    "reel_profile": "social",
    "destination": "youtube",
    "look_seed": 123456,
    "look_epoch": 2,
    "layout_epoch": 1,
    "theme_family": "teal_coral",
    "personality": "crisp",
    "display_font": "outfit",
    "caption_style": "clean_pill",
    "cue_term_style": "underline",
    "intro_layout": "card_left",
    "reel_card_x": 540,
    "hook_kind": "mystery",
    "check_order": ["recall", "apply", "predict"],
    "greeting_id": "vanakkam-2",
    "asset_style": {"family": "teal_coral", "personality": "crisp"},
    "voices": {
        "narrator": "en-in-neerja",
        "aadhi": "en-in-aadhi",
        "student": "en-in-prabhat",
        "calibration": {
            "en-in-neerja": {"natural_wpm": 158.5, "control": "+8%"},
            "en-in-prabhat": {"natural_wpm": 171.0, "control": "speed=1.05"},
        },
    },
    "music": {
        "source": "library", "provider": "procedural", "mood": "curious", "asset_key": "music-key-1",
        "mode": "gaps_bookends", "level": "low",
    },
    "entity_colors": {"V": 0, "I": 1, "R": 2},
    "hook": {"question_item": "s-hook/q-hook", "kind": "mystery", "answer_beat": "s-explain/b-answer"},
    "aadhi_recap": False,
    "captions_burn": False,
    "sfx": False,
    "motion": "calm",
    "pins": ["theme", "voice", "music"],
}


def _beat(bid: str, narration: str, **kw: Any) -> dict[str, Any]:
    return {"id": bid, "narration": narration, **kw}


def maximal_screenplay_dict() -> dict[str, Any]:
    """A valid screenplay using every v3 field, enum value and feature (and a clip scene, cuts and overlays)."""
    return {
        "schema_version": 3,
        "subject_name": "Physics",
        "unit_name": "Electric circuits",
        "session_number": "Session 2",
        "session_title": "Ohm's law",
        "language": "en-IN",
        "concept_map": [{"id": "ohm", "title": "Ohm's law"}],
        "chapters": [
            {"id": "ch1", "title": "Why the bulb glows", "scene_ids": ["s-hook", "s-pre"]},
            {"id": "ch2", "title": "The law", "scene_ids": ["s-explain", "c1", "s-check"], "builds_on": False},
        ],
        "figures": [{"id": "fig1", "caption": "A simple circuit", "asset_key": "figure-key-1"}],
        "presentation": copy.deepcopy(PRESENTATION),
        "scenes": [
            {
                "id": "s-hook",
                "type": "title",
                "title": "Why does a thin wire glow?",
                "chapter_id": "ch1",
                "intent": {"goal": "Hook", "narrative_role": "hook", "visual_intent": "hook"},
                "look": {
                    "template": "intro_card", "variant": "a", "background": "mesh_drift", "palette_slot": 3,
                    "transition_in": "crossfade", "hold_reason": "think",
                    "presenter": {"clip": "center", "seconds": 4.5, "layout": "card_left"}, "lock": "settled",
                },
                "board": [
                    {"id": "q-hook", "kind": "question", "text": "Why does the thin wire glow?", "blank": True,
                     "justification": "More resistance, more heat.", "level": "recall", "icon_hint": "bulb"},
                ],
                "beats": [
                    _beat("b-hi", "Vanakkam! Today we solve a mystery.", speaker="aadhi", board_item_id="q-hook"),
                ],
            },
            {
                "id": "s-pre",
                "type": "content",
                "title": "Three words first",
                "chapter_id": "ch1",
                "concept_id": "ohm",
                "intent": {"goal": "Pre-training", "narrative_role": "pretraining", "visual_intent": "pretraining"},
                "colour_is_content": True,
                "sequence": "timeline",
                "look": {"template": "step_flow", "lock": "pinned", "continues_from": "s-hook"},
                "side_panel": {"kind": "image", "image_prompt": "resistor colour bands", "colour_is_content": True},
                "board": [
                    {"id": "st1", "kind": "stat", "value": "1,200", "unit": "ohm", "text": "a bulb filament",
                     "source_refs": ["c1"]},
                    {"id": "step1", "kind": "step", "text": "Ohm measures current", "date": "1827"},
                    {"id": "step2", "kind": "step", "text": "The law is named", "date": "1830s"},
                    {"id": "qt1", "kind": "quote", "text": "The current is proportional to the voltage.",
                     "caption": "G. S. Ohm"},
                    {"id": "pr1", "kind": "pair", "term": "Water pressure", "text": "Voltage"},
                    {"id": "fig-item", "kind": "figure", "figure_id": "fig1", "caption": "A circuit",
                     "pins": [{"id": "pin-r", "x": 0.25, "y": 0.75, "label": "Resistor"}]},
                    {"id": "q-pre", "kind": "question", "text": "What will the bulb do?", "blank": True,
                     "justification": "Glow dimmer.", "level": "transfer"},
                ],
                "beats": [
                    _beat("b-st", "A filament can be twelve hundred ohms.", board_item_id="st1",
                          speaker="student"),
                    _beat("b-step1", "First, Ohm measured the current.", board_item_id="step1",
                          cuts=[{"start": 0, "end": 1, "text": "First,", "mode": "splice"}]),
                    _beat("b-step2", "Then the law got his name.", board_item_id="step2",
                          voice_override_asset_key="narration-key-1"),
                    _beat("b-quote", "In his own words, current follows voltage.", board_item_id="qt1",
                          overlays=[{"id": "o1", "at_word": 3, "at_text": "words",
                                     "clip": {"asset_key": "clip-key-1", "in_frame": 30, "out_frame": 90},
                                     "region": "panel", "sound": "quiet", "concept_id": "ohm",
                                     "keep_full_length": True}]),
                    _beat("b-pair", "Think of voltage as water pressure.", board_item_id="pr1"),
                    _beat("b-fig", "Look at the resistor here.", board_item_id="fig-item"),
                    _beat("b-predict", "Predict: what will the bulb do?", board_item_id="q-pre", speaker="student",
                          cuts=[{"start": 0, "end": 1, "text": "Predict:", "mode": "revoice"},
                                {"start": 4, "end": 6, "text": "bulb do?"}]),
                    _beat("b-fill", "It glows dimmer.", fill_item_id="q-pre"),
                ],
            },
            {
                "id": "s-explain",
                "type": "content",
                "title": "The law",
                "chapter_id": "ch2",
                "intent": {"goal": "Explain", "narrative_role": "predict", "visual_intent": "formula"},
                "board": [{"id": "f1", "kind": "formula", "latex": "V = IR", "icon_hint": "zap"}],
                "beats": [
                    _beat("b-answer", "The thin wire resists the current, so it heats and glows.",
                          answers="s-hook/q-hook", board_item_id="f1"),
                    _beat("b-answer2", "So the bulb glows dimmer.", answers="s-pre/q-pre"),
                ],
            },
            {
                "id": "c1",
                "type": "clip",
                "title": "Lab demo",
                "chapter_id": "ch2",
                "look": {"template": "clip_scene", "lock": "auto"},
                "clip": {"asset_key": "clip-key-1", "in_frame": 0, "out_frame": 150},
                "sound": "mute",
                "gain_db": -6.5,
                "fit": "cover",
                "focus": {"landscape": [0.5, 0.4], "portrait": [0.3, 0.6]},
                "end_behavior": "loop",
                "captions": [{"start": 0.0, "end": 1.5, "text": "Switching on"}],
                "credit": "Video by A. Teacher (CC BY 4.0)",
                "beats": [_beat("b-vo", "Watch the filament as the switch closes.")],
            },
            {
                "id": "s-check",
                "type": "quiz_checkpoint",
                "title": "Check",
                "chapter_id": "ch2",
                "intent": {"goal": "Recap", "narrative_role": "recap", "visual_intent": "check"},
                "question": "Doubling R at fixed V does what to I?",
                "options": ["Halves it", "Doubles it", "Nothing"],
                "correct_index": 0,
                "beats": [_beat("b-q", "Doubling the resistance does what?", speaker="aadhi")],
                "reveal_beats": [
                    _beat("b-r", "It halves the current.",
                          overlays=[{"id": "o2", "at_word": 0, "at_text": "It",
                                     "clip": {"asset_key": "clip-key-1", "in_frame": 0, "out_frame": 30}}]),
                ],
            },
        ],
    }


def canonical_maximal() -> dict[str, Any]:
    """The canonical dump of the maximal screenplay (what the fixture file holds)."""
    from aadhi.schemas.screenplay import Screenplay

    return Screenplay.model_validate(maximal_screenplay_dict()).model_dump(mode="json")


def write_fixture() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(canonical_maximal(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8",
                       newline="\n")  # LF on every platform, as .gitattributes stores *.json


# --- one feature at a time on a classic screenplay ---------------------------------------------------------------


def classic_screenplay_dict() -> dict[str, Any]:
    from tests.api.factories import screenplay_dict

    return screenplay_dict()


def with_feature(feature: str, sp: dict[str, Any] | None = None) -> dict[str, Any]:
    """A copy of ``sp`` (default: the API factories' classic screenplay) using exactly one v3 ``feature``."""
    sp = copy.deepcopy(sp if sp is not None else classic_screenplay_dict())
    beat = sp["scenes"][0]["beats"][1]
    if feature == "presentation":
        sp["presentation"] = copy.deepcopy(PRESENTATION)
        sp["presentation"]["hook"] = None  # the classic screenplay has no hook question
        sp["presentation"]["music"] = {"source": "procedural", "provider": "procedural"}  # no stored asset needed
    elif feature == "items_v3":
        sp["scenes"][0]["board"].append({"id": "st-x", "kind": "stat", "value": "42", "text": "the answer"})
    elif feature == "cuts":
        beat["cuts"] = [{"start": 0, "end": 2, "text": "Let us"}]
    elif feature == "overlays":
        beat["overlays"] = [{"id": "o-x", "at_word": 1, "at_text": "us",
                             "clip": {"asset_key": "clip-x", "in_frame": 0, "out_frame": 45}}]
    elif feature == "clip":
        sp["scenes"].insert(1, {"id": "c-x", "type": "clip", "title": "Demo",
                                "clip": {"asset_key": "clip-x", "in_frame": 0, "out_frame": 60}})
    elif feature == "voice_override":
        beat["voice_override_asset_key"] = "narration-x"
    else:  # pragma: no cover
        raise KeyError(feature)
    return sp


if __name__ == "__main__":  # pragma: no cover
    write_fixture()
    print(f"wrote {FIXTURE}")
