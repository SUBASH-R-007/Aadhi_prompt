"""Presenter director rule: layout.mascot_crowds_board (aadhi.pipeline.presenter_lint) in the lint."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.pipeline.presenter_lint import CENTER_MAX_ITEMS, lint_mascot_layout
from aadhi.pipeline.validate import lint, scenes_needing_repair
from aadhi.schemas.screenplay import Screenplay

CODE = {"id": "s1-code", "kind": "code", "code": "x = 1\nprint(x)", "language": "python"}
TABLE = {"id": "s1-table", "kind": "table", "headers": ["V", "I"], "rows": [["1", "2"]]}


def bullets(n: int) -> list[dict[str, Any]]:
    return [{"id": f"s1-i{k}", "kind": "bullet", "text": f"point {k}"} for k in range(n)]


def scene(position: str, board: list[dict[str, Any]], kind: str = "content") -> Any:
    sp = Screenplay.model_validate({"scenes": [{
        "id": "s1", "type": kind, "title": "T", "mascot_position": position, "board": board,
        "beats": [{"id": "s1-b1", "narration": "We look at the board together now."}],
    }]})
    return sp.scenes[0]


@pytest.mark.parametrize("board", [[CODE], [TABLE], bullets(CENTER_MAX_ITEMS + 1)])
def test_centre_with_a_wide_board_is_a_warning_the_teacher_fixes(board) -> None:
    issues = lint_mascot_layout(scene("center", board))
    assert [(i.code, i.severity, i.scene_id, i.fixable, i.source) for i in issues] == [
        ("layout.mascot_crowds_board", "warning", "s1", False, "lint")]
    assert "centre" in issues[0].message and ("popup" in issues[0].message)


@pytest.mark.parametrize(("position", "board", "kind"), [
    ("center", bullets(CENTER_MAX_ITEMS), "content"),  # small enough
    ("center", [{"id": "s1-h", "kind": "heading", "text": "Ohm's law"}], "title"),  # the default title layout
    ("left", [CODE, TABLE, *bullets(3)], "content"),
    ("right", [CODE], "example"),
    ("popup_bottom_left", [TABLE], "content"),
    ("hidden", [CODE], "content"),
])
def test_other_layouts_are_fine(position, board, kind) -> None:
    assert lint_mascot_layout(scene(position, board, kind)) == []


def test_part_of_the_lint_and_never_triggers_a_paid_rewrite() -> None:
    sp = Screenplay.model_validate({"scenes": [{
        "id": "s1", "type": "content", "title": "T", "mascot_position": "center", "board": [CODE],
        "beats": [{"id": "s1-b1", "narration": "Here is the code we will run today.", "board_item_id": "s1-code"}],
    }]})
    issues = [i for i in lint(sp) if i.code == "layout.mascot_crowds_board"]
    assert len(issues) == 1
    assert scenes_needing_repair(issues) == {}
    assert lint_mascot_layout(sp.scenes[0]) == issues
