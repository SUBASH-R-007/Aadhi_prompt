"""Fake aadhi.pipeline.validate.lint (one rule: too many board items)."""

from __future__ import annotations

from aadhi.pipeline.base import Issue


def lint(screenplay, options=None) -> list[Issue]:
    issues = []
    for scene in screenplay.scenes:
        board = getattr(scene, "board", None) or []
        if len(board) > 6:
            issues.append(
                Issue(
                    code="board.too_many_items", severity="warning", message="Too many board items", scene_id=scene.id
                )
            )
    return issues
