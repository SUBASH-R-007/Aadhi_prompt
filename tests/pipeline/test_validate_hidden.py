"""Lint of hidden scenes and minimum durations (``SceneBase.hidden`` / ``min_seconds``), the first-reveal rule of the
sync lint, and the carry-over of both teacher fields through a scene rewrite."""

from __future__ import annotations

from typing import Any

from aadhi.pipeline.assets import scene_hash
from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.sync_lint import lint_sync
from aadhi.pipeline.timing import lecture_seconds
from aadhi.pipeline.validate import lecture_estimate, lint, scenes_needing_repair
from aadhi.schemas.screenplay import Screenplay


def lecture(edits: dict[str, dict[str, Any]] | None = None, **top: Any) -> Screenplay:
    edits = edits or {}
    data: dict[str, Any] = {
        "language": "en-IN",
        "learning_objectives": [{"id": "o1", "text": "Apply Ohm's law"}, {"id": "o2", "text": "Use power"}],
        "concept_map": [{"id": "ohm", "title": "Ohm's law"}, {"id": "power", "title": "Power"}],
        "misconceptions": [{"id": "m1", "statement": "Current is used up", "correction": "It is conserved"}],
        "scenes": [
            {"id": "intro", "type": "title", "title": "Ohm's law", "concept_id": "ohm",
             "board": [{"id": "t", "kind": "heading", "text": "Ohm's law"}],
             "beats": [{"id": "intro-b", "narration": "Welcome to this short session on circuits.", "board_item_id": "t"}]},
            {"id": "law", "type": "content", "title": "The law", "concept_id": "ohm", "objective_ids": ["o1"],
             "board": [{"id": "p", "kind": "bullet", "text": "V is proportional to I"},
                       {"id": "mc", "kind": "misconception", "text": "Current is used up",
                        "justification": "It is conserved", "misconception_id": "m1"}],
             "beats": [{"id": "law-b1", "narration": "Voltage is proportional to the current.", "board_item_id": "p"},
                       {"id": "law-b2", "narration": "Current is never used up in a circuit.", "board_item_id": "mc"}]},
            {"id": "power", "type": "content", "title": "Power", "concept_id": "power", "objective_ids": ["o2"],
             "board": [{"id": "q", "kind": "bullet", "text": "P = V I"}],
             "beats": [{"id": "power-b1", "narration": "Power is voltage times the current.", "board_item_id": "q"}]},
            {"id": "check", "type": "quiz_checkpoint", "title": "Check", "concept_id": "power", "objective_ids": ["o1", "o2"],
             "question": "Which grows with I?", "options": ["V", "R"], "correct_index": 0,
             "beats": [{"id": "check-b", "narration": "Here is a quick question for you."}],
             "reveal_beats": [{"id": "check-r", "narration": "The voltage grows with the current."}]},
        ],
    }
    data.update(top)
    for s in data["scenes"]:
        s.update(edits.get(s["id"], {}))
    return Screenplay.model_validate(data)


def keyed(issues) -> list[tuple[str, str, str | None]]:
    return [(i.code, i.severity, i.scene_id) for i in issues]


def test_nothing_hidden_lints_exactly_as_before() -> None:
    sp = lecture()
    explicit = lecture({s.id: {"hidden": False, "min_seconds": None} for s in sp.scenes})
    assert lint(explicit, GenerationOptions()) == lint(sp, GenerationOptions())
    assert lecture_estimate(sp) == lecture_seconds(sp)
    assert not {i.code for i in lint(sp)} & {"scene.hidden", "lecture.all_scenes_hidden", "chapter.all_scenes_hidden"}


def test_a_hidden_scenes_findings_become_notes_and_never_trigger_a_rewrite() -> None:
    bad = {"beats": [{"id": "law-b1", "narration": "Here $V = IR$ is written with markup.", "board_item_id": "p"},
                     {"id": "law-b2", "narration": "Current is never used up in a circuit.", "board_item_id": "mc"}]}
    shown = lint(lecture({"law": bad}))
    assert ("narration.markup", "error", "law") in keyed(shown) and "law" in scenes_needing_repair(shown)
    hidden = lint(lecture({"law": {**bad, "hidden": True}}))
    assert ("narration.markup", "info", "law") in keyed(hidden)
    assert all(i.severity == "info" for i in hidden if i.scene_id == "law")
    assert "law" not in scenes_needing_repair(hidden)
    note = next(i for i in hidden if i.code == "scene.hidden")
    assert note.scene_id == "law" and note.severity == "info" and not note.fixable and "left out" in note.message
    # the messages are unchanged, so the quality report's repairs still name them
    assert {i.message for i in shown if i.scene_id == "law"} <= {i.message for i in hidden if i.scene_id == "law"}


def test_coverage_counts_only_the_scenes_shown() -> None:
    issues = lint(lecture({"law": {"hidden": True}}))
    msgs = {i.code: i.message for i in issues if i.scene_id is None}
    assert "only by hidden scenes" in msgs["objective.untaught"] and "'o1'" in msgs["objective.untaught"]
    assert "only in hidden scenes" in msgs["misconception.untargeted"]
    assert "concept.unused" not in msgs  # the title scene still teaches ohm
    quiz_hidden = lint(lecture({"check": {"hidden": True}}))
    unassessed = [i.message for i in quiz_hidden if i.code == "objective.unassessed"]
    assert len(unassessed) == 2 and all("only by hidden quizzes" in m for m in unassessed)
    clean = {i.code for i in lint(lecture())}
    assert not clean & {"objective.untaught", "objective.unassessed", "misconception.untargeted", "concept.unused"}


def test_every_scene_hidden_is_a_warning_and_the_length_check_stays_quiet() -> None:
    sp = lecture({s: {"hidden": True} for s in ("intro", "law", "power", "check")})
    issues = lint(sp, GenerationOptions(target_minutes=10))
    warning = next(i for i in issues if i.code == "lecture.all_scenes_hidden")
    assert warning.severity == "warning" and warning.scene_id is None and not warning.fixable
    assert "lecture.duration_mismatch" not in {i.code for i in issues}
    assert "chapter.all_scenes_hidden" not in {i.code for i in issues}


def test_a_chapter_left_without_shown_scenes_is_a_note() -> None:
    chapters = [{"id": "c1", "title": "Basics", "scene_ids": ["intro", "law"]},
                {"id": "c2", "title": "Power", "scene_ids": ["power"]}]
    issues = lint(lecture({"power": {"hidden": True}}, chapters=chapters))
    note = next(i for i in issues if i.code == "chapter.all_scenes_hidden")
    assert note.severity == "info" and note.scene_id == "power" and "'Power'" in note.message
    assert "only scene" in note.message
    assert "chapter.all_scenes_hidden" not in {i.code for i in lint(lecture({"law": {"hidden": True}}, chapters=chapters))}
    # chapter cards without Screenplay.chapters: the card and the scenes after it
    card = {"id": "card", "type": "chapter_card", "title": "Part two", "beats": []}
    data = lecture().model_dump(mode="json")
    data["scenes"].insert(2, card)
    for s in data["scenes"][2:]:
        s["hidden"] = True
    issues = lint(Screenplay.model_validate(data))
    note = next(i for i in issues if i.code == "chapter.all_scenes_hidden")
    assert note.scene_id == "card" and "all 3 of its scenes" in note.message


def test_the_lecture_length_skips_hidden_scenes_and_counts_minimum_durations() -> None:
    sp = lecture()
    base = lecture_seconds(sp)
    power = sp.scene_by_id("power")
    from aadhi.pipeline.timing import scene_seconds

    assert lecture_estimate(lecture({"power": {"hidden": True}})) == base - scene_seconds(power)
    held = lecture({"power": {"min_seconds": 300}})
    assert lecture_estimate(held) == base - scene_seconds(power) + 300
    target = round(lecture_estimate(held) / 60)
    assert "lecture.duration_mismatch" not in {i.code for i in lint(held, GenerationOptions(target_minutes=max(1, target)))}
    # a deliberate hold is not "split this scene" advice
    assert "scene.too_long" not in {i.code for i in lint(lecture({"power": {"min_seconds": 600}}))}


def test_a_hidden_repeated_introduction_is_not_reported() -> None:
    twice = {"id": "intro2", "type": "title", "title": "Ohm's law again",
             "board": [{"id": "t2", "kind": "heading", "text": "Ohm's law"}],
             "beats": [{"id": "intro2-b", "narration": "Welcome back to this session on circuits.", "board_item_id": "t2"}]}
    data = lecture().model_dump(mode="json")
    data["scenes"].insert(2, twice)
    dup = lint(Screenplay.model_validate(data))
    assert ("content.duplicate_intro", "warning", "intro2") in keyed(dup)
    twice["hidden"] = True
    data["scenes"][2] = twice
    assert "content.duplicate_intro" not in {i.code for i in lint(Screenplay.model_validate(data))}


def test_scene_hash_ignores_the_teacher_fields() -> None:
    sp = lecture()
    marked = lecture({s.id: {"hidden": True, "min_seconds": 42} for s in sp.scenes})
    assert [scene_hash(s, sp.lexicon) for s in sp.scenes] == [scene_hash(s, sp.lexicon) for s in marked.scenes]


def test_a_rewrite_keeps_hidden_and_min_seconds() -> None:
    from aadhi.pipeline.repair import carry_over

    current = lecture({"law": {"hidden": True, "min_seconds": 20}}).scene_by_id("law")
    rewritten = lecture().scene_by_id("law").model_copy(update={"title": "The law, rewritten"})
    kept = carry_over(current, rewritten)
    assert kept.hidden is True and kept.min_seconds == 20 and kept.title == "The law, rewritten"
    plain = carry_over(lecture().scene_by_id("law"), rewritten)
    assert plain.hidden is False and plain.min_seconds is None
    assert "hidden" not in plain.model_dump(mode="json") and "min_seconds" not in plain.model_dump(mode="json")


def test_sync_lint_reads_the_first_beat_revealing_a_formula_like_the_timeline() -> None:
    sp = Screenplay.model_validate({"scenes": [{
        "id": "s1", "type": "content", "title": "Dynamics",
        "board": [{"id": "f", "kind": "formula", "latex": "F = m a",
                   "variables": [{"symbol_latex": "F", "meaning": "Force", "beat_id": "b1"}]}],
        "beats": [{"id": "b0", "narration": "First a word about units."},
                  {"id": "b1", "narration": "Here is the second law of motion.", "board_item_id": "f"},
                  {"id": "b2", "narration": "Force is mass times acceleration."}],
    }]})
    scene = sp.scenes[0]
    assert lint_sync(scene) == []
    # a second reveal of the same item (refused by the schema, built here directly): the first one counts, as in
    # aadhi.compose.sync, so the anchor on b1 stays valid
    scene.beats[2] = scene.beats[2].model_copy(update={"board_item_id": "f"})
    assert lint_sync(scene) == []
