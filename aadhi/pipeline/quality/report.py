"""The quality checks' output: lint ``Issue``s (the existing contract) plus optional safe repairs.

A repair is a list of exact field edits (``before`` -> ``after``) inside named scenes. The Studio
applies one only when every ``before`` still matches the draft, as an ordinary undoable edit that is
saved through the editor's normal save path; nothing is ever changed on the server by a check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..base import Issue, Severity


@dataclass
class Repair:
    label: str  # the button's words, e.g. 'Write it as “machine learning”'
    edits: list[dict[str, Any]] = field(default_factory=list)  # {"scene_id", "path", "before", "after"}


@dataclass
class Finding:
    issue: Issue
    repair: Repair | None = None


def lint_issue(code: str, message: str, severity: Severity = "info", scene_id: str | None = None,
               fixable: bool = False) -> Issue:
    """A lint issue of the quality family (``fixable=False`` unless a scene rewrite may safely fix it)."""
    return Issue(code=code, severity=severity, message=message[:1500], scene_id=scene_id, source="lint",
                 fixable=fixable)


def repair_entry(f: Finding) -> dict[str, Any] | None:
    """The JSON a client needs to offer a repair for an issue (matched by code, scene and message)."""
    if f.repair is None or not f.repair.edits:
        return None
    return {"code": f.issue.code, "scene_id": f.issue.scene_id, "message": f.issue.message,
            "label": f.repair.label, "edits": f.repair.edits}
