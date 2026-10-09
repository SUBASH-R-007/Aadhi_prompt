"""Presenter director rule: Aadhi's position must leave the board room for what the scene shows.

Deterministic and advisory (part of ``aadhi.pipeline.validate.lint``):

``layout.mascot_crowds_board`` (warning, not fixable by a rewrite): a board scene with Aadhi in the
centre keeps the board to the 628-px strip left of him (``web/js/player/layout.js`` ``center``:
x 40..668), which is too narrow for code, a table or more than ``CENTER_MAX_ITEMS`` items. Standing
left or right leaves the board about 1,140 px, the popup close-up is the content-first layout (the
board takes x 40..1440 in front of it) and hidden gives it the whole stage. The position stays the
scene writer's or the teacher's choice (a scene rewrite keeps it: ``repair.carry_over``), so the
warning is advice in the editor's issue list; lint warnings never trigger an automatic rewrite
(``validate.scenes_needing_repair``). Title scenes, centred by default, hold 1-3 items and are not
affected.
"""

from __future__ import annotations

from typing import Any

from ..schemas.screenplay import BoardItemKind, BoardScene
from .base import Issue

CENTER_MAX_ITEMS = 4
WIDE_KINDS = (BoardItemKind.code, BoardItemKind.table)


def lint_mascot_layout(scene: Any) -> list[Issue]:
    """``layout.mascot_crowds_board`` for a centred board scene that needs a wide board."""
    if not isinstance(scene, BoardScene) or scene.mascot_position != "center":
        return []
    wide = next((i for i in scene.board if i.kind in WIDE_KINDS), None)
    if wide is None and len(scene.board) <= CENTER_MAX_ITEMS:
        return []
    what = f"a {wide.kind.value} item" if wide is not None else f"{len(scene.board)} board items"
    return [Issue(
        code="layout.mascot_crowds_board", severity="warning", scene_id=scene.id, source="lint", fixable=False,
        message=(f"Aadhi stands in the centre, which leaves the board a narrow strip, too small for {what}. "
                 "Put him on the left or right, use a popup layout, or hide him for this scene."),
    )]
