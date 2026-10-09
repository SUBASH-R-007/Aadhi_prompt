"""Lint of authored sync anchors (aadhi.pipeline.sync_lint), registered in validate.lint_scene."""

from __future__ import annotations

from typing import Any

from aadhi.pipeline.sync_lint import lint_sync
from aadhi.pipeline.validate import lint
from aadhi.schemas.screenplay import Screenplay


def screenplay(variables: list[dict[str, Any]]) -> Screenplay:
    return Screenplay.model_validate({"scenes": [{
        "id": "s1", "type": "content", "title": "Dynamics",
        "board": [{"id": "p", "kind": "bullet", "text": "Units first"},
                  {"id": "f", "kind": "formula", "latex": "F = m a", "variables": variables}],
        "beats": [{"id": "b0", "narration": "First a word about units.", "board_item_id": "p"},
                  {"id": "b1", "narration": "Here is the second law of motion.", "board_item_id": "f"},
                  {"id": "b2", "narration": "Force is mass times acceleration."}],
    }]})


def test_valid_or_absent_anchors_are_clean() -> None:
    sp = screenplay([{"symbol_latex": "F", "meaning": "Force", "beat_id": "b2"},
                     {"symbol_latex": "m", "meaning": "Mass", "beat_id": "b1"},
                     {"symbol_latex": "a", "meaning": "Acceleration"}])
    assert lint_sync(sp.scenes[0]) == []


def test_unknown_and_too_early_beats_are_warnings_the_teacher_fixes() -> None:
    sp = screenplay([{"symbol_latex": "F", "meaning": "Force", "beat_id": "b9"},
                     {"symbol_latex": "m", "meaning": "Mass", "beat_id": "b0"}])
    issues = lint_sync(sp.scenes[0])
    assert [i.code for i in issues] == ["formula.variable_beat_invalid"] * 2
    assert all(i.severity == "warning" and not i.fixable and i.scene_id == "s1" for i in issues)
    assert "not a beat of this scene" in issues[0].message
    assert issues[1].beat_id == "b0" and "before the formula" in issues[1].message


def test_registered_in_the_screenplay_lint() -> None:
    sp = screenplay([{"symbol_latex": "F", "meaning": "Force", "beat_id": "b9"}])
    assert "formula.variable_beat_invalid" in {i.code for i in lint(sp)}
    clean = screenplay([{"symbol_latex": "F", "meaning": "Force"}])
    assert "formula.variable_beat_invalid" not in {i.code for i in lint(clean)}


def test_a_scene_rewrite_is_never_asked_to_fix_the_teachers_anchor() -> None:
    # The model never writes FormulaVariable.beat_id, so the repair rewrite does not get this warning.
    from aadhi.pipeline.quality import AUTHOR_CODES

    assert "formula.variable_beat_invalid" in AUTHOR_CODES
