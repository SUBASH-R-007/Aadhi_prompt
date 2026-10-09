"""Lint of authored synchronisation anchors (pure; registered by ``aadhi.pipeline.validate.lint_scene``).

``formula.variable_beat_invalid`` (warning): a formula legend row names a beat (``FormulaVariable.beat_id``)
that is not a beat of the scene, or that comes before the beat revealing the formula. The timeline
ignores such an anchor (the row appears when the narration names it, else with the formula), so the
lecture still plays; the teacher should pick another beat. Not fixable by a rewrite: the model never
writes this field.
"""

from __future__ import annotations

from typing import Any

from ..schemas.screenplay import BoardItemKind, BoardScene
from .base import Issue


def _issue(code: str, message: str, scene: Any, beat_id: str | None = None) -> Issue:
    return Issue(code=code, severity="warning", message=message, scene_id=scene.id, beat_id=beat_id,
                 source="lint", fixable=False)


def lint_sync(scene: Any) -> list[Issue]:
    """Scene-local issues of the authored sync anchors."""
    if not isinstance(scene, BoardScene):
        return []
    order = {b.id: i for i, b in enumerate(scene.beats)}
    reveal: dict[str, int] = {}  # the FIRST beat revealing each item, as aadhi.compose.sync reads it
    for i, b in enumerate(scene.beats):
        if b.board_item_id:
            reveal.setdefault(b.board_item_id, i)
    out: list[Issue] = []
    for item in scene.board:
        if item.kind != BoardItemKind.formula:
            continue
        for n, var in enumerate(item.variables, start=1):
            if not var.beat_id:
                continue
            at = order.get(var.beat_id)
            if at is None:
                out.append(_issue("formula.variable_beat_invalid",
                                  f"Formula {item.id}: legend row {n} names beat {var.beat_id!r}, which is not a beat "
                                  "of this scene; the row appears when the narration names it instead.", scene))
            elif at < reveal.get(item.id, -1):
                out.append(_issue("formula.variable_beat_invalid",
                                  f"Formula {item.id}: legend row {n} is set to appear on beat {var.beat_id}, before "
                                  "the formula itself; it appears when the narration names it instead.", scene,
                                  beat_id=var.beat_id))
    return out
