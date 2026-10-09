"""Synthetic screenplays with hand-checkable metric values (used by the eval tests)."""

from __future__ import annotations

from typing import Any

from aadhi.schemas.screenplay import Screenplay


def words(n: int, word: str = "word") -> str:
    """A narration of exactly ``n`` words."""
    return " ".join([word] * n)


def beat(bid: str, n_words: int = 10, **extra: Any) -> dict[str, Any]:
    return {"id": bid, "narration": words(n_words), **extra}


def lecture_dict() -> dict[str, Any]:
    """A small but complete lecture exercising every metric group.

    Hand-computed facts (asserted in test_metrics.py):
    * 8 scenes; board scenes with items: s-ohm (4 items), s-ex (3 items); title has none.
    * s-ohm: bullet ``b1`` is never revealed; ``long1`` paragraph has 200 characters.
    * Objectives: obj-ohm taught+assessed (quiz), obj-power taught + practiced, obj-extra neither.
    * Misconceptions: m-used-up targeted (quiz distractor + board item), m-orphan untargeted.
    * Quiz: 4 options, correct index 1, 3 distractors, 1 tied to a misconception, 1 with feedback.
    * Manim: 1 template (equation_steps) + 1 free-form panel -> 50% template.
    * Visual rationale: chart panel yes, free-form manim panel no, ai_video yes -> 2/3.
    * Worked example: 2 blank steps, 1 filled (after a pause).
    """
    return {
        "subject_name": "Basic Electrical Engineering",
        "session_title": "Ohm's Law",
        "language": "en-IN",
        "concept_map": [
            {"id": "c-basics", "title": "Charge and current", "kind": "prerequisite"},
            {"id": "c-ohm", "title": "Ohm's law", "depends_on": ["c-basics"]},
            {"id": "c-power", "title": "Electrical power", "depends_on": ["c-ohm"]},
        ],
        "learning_objectives": [
            {"id": "obj-ohm", "text": "Apply V = IR", "bloom": "apply", "concept_ids": ["c-ohm"]},
            {"id": "obj-power", "text": "Compute power", "bloom": "apply", "concept_ids": ["c-power"]},
            {"id": "obj-extra", "text": "Explain resistivity", "bloom": "understand"},
        ],
        "misconceptions": [
            {"id": "m-used-up", "concept_id": "c-ohm", "statement": "Resistors use up current",
             "correction": "Current is the same everywhere in a series loop"},
            {"id": "m-orphan", "statement": "Thicker wires resist more", "correction": "They resist less"},
        ],
        "chapters": [{"id": "ch1", "title": "Ohm's law", "scene_ids": ["s-title", "s-ohm", "s-ex", "q1"]}],
        "scenes": [
            {"id": "s-title", "type": "title", "title": "Ohm's Law", "chapter_id": "ch1",
             "beats": [beat("s-title-b1", 8)]},
            {
                "id": "s-ohm", "type": "content", "title": "The law", "concept_id": "c-ohm", "chapter_id": "ch1",
                "objective_ids": ["obj-ohm"],
                "intent": {"goal": "State the law", "source_refs": ["c0001"]},
                "board": [
                    {"id": "h1", "kind": "heading", "text": "**Ohm's** law"},
                    {"id": "f1", "kind": "formula", "latex": "V = I R",
                     "text": "voltage equals current times resistance",
                     "source_refs": ["c0001"]},
                    {"id": "b1", "kind": "bullet", "text": "[[Ohmic]] devices obey it"},
                    {"id": "long1", "kind": "paragraph", "text": "x" * 200},
                ],
                "beats": [
                    beat("s-ohm-b1", 10, board_item_id="h1", source_refs=["c0001"]),
                    beat("s-ohm-b2", 50, board_item_id="f1", source_refs=["c0002", "c9999"]),
                    beat("s-ohm-b3", 2, board_item_id="long1"),
                ],
                "side_panel": {
                    "kind": "chart", "rationale": "Shows the linear V-I relation",
                    "chart": {"labels": ["2", "4"], "datasets": [{"label": "I", "data": [0.1, 0.2]}]},
                },
            },
            {
                "id": "s-ex", "type": "example", "title": "Worked example", "concept_id": "c-power",
                "chapter_id": "ch1", "objective_ids": ["obj-power"],
                "board": [
                    {"id": "st1", "kind": "example_step", "text": "Given V = 12 V, R = 4 ohm"},
                    {"id": "st2", "kind": "example_step", "text": "I = V / R = 3 A", "blank": True},
                    {"id": "st3", "kind": "example_step", "text": "P = V I = 36 W", "blank": True},
                ],
                "beats": [
                    beat("s-ex-b1", 12, board_item_id="st1"),
                    beat("s-ex-b2", 12, board_item_id="st2", pause_after=2.0),
                    beat("s-ex-b3", 12, fill_item_id="st2"),
                    beat("s-ex-b4", 12, board_item_id="st3"),
                ],
            },
            {
                "id": "q1", "type": "quiz_checkpoint", "title": "Check", "concept_id": "c-ohm", "chapter_id": "ch1",
                "objective_ids": ["obj-ohm"], "question": "A 12 V battery drives 3 A. What is R?",
                "options": ["36 ohm", "4 ohm", "0.25 ohm", "15 ohm"], "correct_index": 1,
                "option_misconception_ids": [None, None, "m-used-up", None],
                "feedback_wrong": ["You multiplied", "", "", ""],
                "explanation": "R = V / I = 4 ohm", "bloom": "apply", "countdown_seconds": 8,
                "beats": [beat("q1-b1", 10)], "reveal_beats": [beat("q1-r1", 2)],
            },
            {
                "id": "sim1", "type": "simulation", "title": "Animation", "concept_id": "c-power",
                "manim": {"template": "equation_steps", "params": {"steps": ["a", "b"]}},
                "beats": [beat("sim1-b1", 10), beat("sim1-b2", 10)],
            },
            {
                "id": "s-panel", "type": "content", "title": "Power", "concept_id": "c-power",
                "board": [{"id": "p1", "kind": "bullet", "text": "P = V I", "misconception_id": None}],
                "beats": [beat("s-panel-b1", 10, board_item_id="p1")],
                "side_panel": {"kind": "manim", "manim": {"code": "class S(AadhiScene):\n    pass\n"}},
            },
            {"id": "cc", "type": "chapter_card", "title": "Part 2", "chapter_label": "Part 2", "beats": []},
            {
                "id": "av", "type": "ai_video", "title": "Power lines", "video_prompt": "pylons at dusk",
                "rationale": "Real-world context for power", "beats": [beat("av-b1", 10)],
            },
        ],
        "companion_sheet": {
            "practice_problems": [
                {"id": "pp1", "question": "Find P", "final_answer": "36 W", "objective_ids": ["obj-power"],
                 "source_refs": ["c0003"]},
            ]
        },
    }


def misconception_board_lecture() -> Screenplay:
    """The base lecture plus a misconception board item for m-used-up."""
    data = lecture_dict()
    ohm = data["scenes"][1]
    ohm["board"].append(
        {"id": "mis1", "kind": "misconception", "text": "Resistors use up current",
         "justification": "Charge is conserved", "misconception_id": "m-used-up"}
    )
    return Screenplay.model_validate(data)


def lecture() -> Screenplay:
    """Validated version of ``lecture_dict``."""
    return Screenplay.model_validate(lecture_dict())


def minimal_lecture(n_words: int = 150, *, language: str = "en-IN") -> Screenplay:
    """One content scene with one beat of ``n_words`` words."""
    return Screenplay.model_validate(
        {
            "language": language,
            "scenes": [
                {"id": "s1", "type": "content", "title": "Only", "board": [], "beats": [beat("s1-b1", n_words)]},
            ],
        }
    )
