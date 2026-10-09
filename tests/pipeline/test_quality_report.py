"""Quality check before export (render preflight quality items) and the quality report served by /lint."""

from __future__ import annotations

import json

import pytest

from aadhi.compose.preflight import QUALITY_MAX_ITEMS, has_blocking, quality_items, render_preflight
from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.pipeline.base import Issue
from aadhi.pipeline.quality import quality_report
from aadhi.schemas.screenplay import Screenplay
from tests.compose.factories import make_manifest, make_screenplay


@pytest.fixture()
def timeline():
    sp = make_screenplay()
    return build_timeline(sp, make_manifest(sp), settings=Settings(_env_file=None))


def issue(code: str, severity: str, scene_id: str | None, source: str = "lint", message: str = "") -> dict:
    return Issue(code=code, severity=severity, scene_id=scene_id, source=source,
                 message=message or f"{code} in {scene_id}").model_dump(mode="json")


def test_without_issues_the_preflight_is_exactly_the_degradations(timeline):
    assert render_preflight(timeline) == render_preflight(timeline, issues=None)
    assert all(it["reason"] != "quality" for it in render_preflight(timeline))


def test_errors_and_warnings_follow_the_degradations_and_never_block(timeline):
    board, title = timeline.scenes[3], timeline.scenes[0]
    issues = [issue("board.item_unrevealed", "info", board.scene_id),
              issue("terminology.abbreviation_conflict", "warning", board.scene_id),
              issue("factual.error", "error", title.scene_id, source="critic"),
              issue("manim.render_failed", "error", board.scene_id, source="manim"),
              issue("assets.image_failed", "warning", board.scene_id, source="assets"),
              issue("objective.unassessed", "warning", None)]
    degradations = render_preflight(timeline)
    items = render_preflight(timeline, issues=issues)
    assert items[: len(degradations)] == degradations
    quality = items[len(degradations):]
    assert [(q["code"], q["severity"], q["scene_id"]) for q in quality] == [
        ("factual.error", "error", title.scene_id),
        ("objective.unassessed", "warning", None),
        ("terminology.abbreviation_conflict", "warning", board.scene_id),
    ]
    first = quality[0]
    assert first == {"scene_index": title.index, "scene_id": title.scene_id, "title": title.title or title.scene_id,
                     "reason": "quality", "blocking": False, "message": f"factual.error in {title.scene_id}",
                     "severity": "error", "code": "factual.error"}
    assert not any(q["blocking"] for q in quality)
    assert has_blocking(items) == has_blocking(degradations)


def test_generation_and_translation_failures_are_listed(timeline):
    """System errors the editor's render dialog shows (a fallback scene, a scene still in the source language)
    are in the server's list too; info-level system notes and missing media stay out."""
    sid = timeline.scenes[1].scene_id
    issues = [issue("scene.generation_failed", "error", sid, source="system"),
              issue("translate.scene_failed", "error", timeline.scenes[2].scene_id, source="system"),
              issue("scene.autofixed", "info", sid, source="system"),
              issue("assets.image_failed", "warning", sid, source="assets")]
    assert [q["code"] for q in quality_items(issues, timeline)] == ["scene.generation_failed", "translate.scene_failed"]


def test_at_most_twelve_items_then_a_count(timeline):
    issues = [issue("beat.too_long", "warning", timeline.scenes[1].scene_id, message=f"long beat {n}") for n in range(20)]
    items = quality_items(issues, timeline)
    assert len(items) == QUALITY_MAX_ITEMS + 1
    assert items[-1]["scene_id"] is None and items[-1]["message"].startswith("8 more error(s) or warning(s)")
    assert quality_items([], timeline) == [] and quality_items(issues[:1]) and quality_items(issues[:1])[0]["scene_index"] is None


def test_the_quality_report_is_json_and_its_repairs_name_issues_in_the_lint():
    from aadhi.pipeline.validate import lint

    sp = Screenplay.model_validate({"scenes": [
        {"id": "a", "type": "content", "title": "One", "board": [{"id": "a-i", "kind": "bullet", "text": "We use [[Ohm's Law]]."}],
         "beats": [{"id": "a-b", "narration": "A short clean beat.", "board_item_id": "a-i"}]},
        {"id": "b", "type": "content", "title": "Two", "board": [{"id": "b-i", "kind": "bullet", "text": "Then [[Ohm's law]]."}],
         "beats": [{"id": "b-b", "narration": "A short clean beat.", "board_item_id": "b-i"}]},
        {"id": "c", "type": "content", "title": "Three", "board": [{"id": "c-i", "kind": "bullet", "text": "And [[Ohm's law]]."}],
         "beats": [{"id": "c-b", "narration": "A short clean beat.", "board_item_id": "c-i"}]},
    ]})
    report = quality_report(sp)
    json.dumps(report, allow_nan=False)
    assert report["version"] == 1 and set(report["registry"]) == {"terms", "concepts", "abbreviations", "symbols",
                                                                   "code_languages", "figures"}
    keys = {(i.code, i.scene_id, i.message) for i in lint(sp)}
    assert report["repairs"] and all((r["code"], r["scene_id"], r["message"]) in keys for r in report["repairs"])
    edit = report["repairs"][0]["edits"][0]
    assert edit == {"scene_id": "a", "path": ["board", 0, "text"], "before": "We use [[Ohm's Law]].",
                    "after": "We use [[Ohm's law]]."}


def test_a_broken_report_is_empty_never_an_error(monkeypatch):
    from aadhi.pipeline import quality

    def boom(_sp):
        raise RuntimeError("bug")

    monkeypatch.setattr(quality, "analyse", boom)
    assert quality.quality_report(Screenplay()) == {"version": 1, "repairs": [], "registry": {}}
    assert quality.lint_quality(Screenplay()) == []
