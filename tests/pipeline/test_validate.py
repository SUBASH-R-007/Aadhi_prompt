"""validate.lint: every documented code is triggered by a minimal screenplay."""

from __future__ import annotations

from typing import Any

import pytest

from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.validate import lint, scenes_needing_repair
from aadhi.schemas.screenplay import Screenplay


def beat(i: str, text: str = "This is a short, clean beat of narration.", **kw: Any) -> dict[str, Any]:
    return {"id": i, "narration": text, **kw}


def board_scene(sid: str, **kw: Any) -> dict[str, Any]:
    d = {"id": sid, "type": "content", "title": "T", "concept_id": "a", "objective_ids": ["o1"],
         "board": [{"id": f"{sid}-i1", "kind": "bullet", "text": "one point"}],
         "beats": [beat(f"{sid}-b1", board_item_id=f"{sid}-i1")]}
    d.update(kw)
    return d


def quiz_scene(sid: str, correct: int = 0, **kw: Any) -> dict[str, Any]:
    d = {"id": sid, "type": "quiz_checkpoint", "title": "Q", "concept_id": "a", "objective_ids": ["o1"],
         "question": "Which?", "options": ["x", "y", "z"], "correct_index": correct,
         "option_misconception_ids": [None, "m1", None],
         "beats": [beat(f"{sid}-b1")], "reveal_beats": [beat(f"{sid}-b2")]}
    d.update(kw)
    return d


def screenplay(scenes: list[dict[str, Any]], **kw: Any) -> Screenplay:
    data = {
        "concept_map": [{"id": "a", "title": "A"}, {"id": "pre", "title": "Pre", "kind": "prerequisite"}],
        "learning_objectives": [{"id": "o1", "text": "Do A"}],
        "misconceptions": [{"id": "m1", "statement": "s", "correction": "c"}],
        "figures": [{"id": "fig-1", "caption": "c"}],
        "scenes": scenes,
    }
    data.update(kw)
    return Screenplay.model_validate(data)


def codes(sp: Screenplay, options: GenerationOptions | None = None, **kw: Any) -> set[str]:
    return {i.code for i in lint(sp, options, **kw)}


def test_clean_screenplay_has_no_warnings():
    sp = screenplay([board_scene("s1"), quiz_scene("q1")])
    issues = lint(sp, GenerationOptions(target_minutes=3), chunk_ids=set())
    assert {i.code for i in issues if i.severity != "info"} == {"lecture.duration_mismatch"}


@pytest.mark.parametrize("code,scenes,extra", [
    ("board.too_many_items", [board_scene("s1", board=[{"id": f"s1-i{n}", "kind": "bullet", "text": "p"} for n in range(1, 9)])], {}),
    ("board.item_too_long", [board_scene("s1", board=[{"id": "s1-i1", "kind": "bullet", "text": "word " * 60}])], {}),
    ("board.item_too_long", [board_scene("s1", board=[{"id": "s1-i1", "kind": "code", "code": "x = 1\n" * 30}])], {}),
    ("board.item_too_long", [board_scene("s1", board=[{"id": "s1-i1", "kind": "table", "headers": list("abcde"),
                                                       "rows": [list("12345")]}])], {}),
    ("board.item_unrevealed", [board_scene("s1", beats=[beat("s1-b1")])], {}),
    ("beat.too_long", [board_scene("s1", beats=[beat("s1-b1", "word " * 80, board_item_id="s1-i1")])], {}),
    ("beat.too_short", [board_scene("s1", beats=[beat("s1-b1", "Hi.", board_item_id="s1-i1")])], {}),
    ("scene.too_long", [board_scene("s1", beats=[beat(f"s1-b{n}", "word " * 55, **({"board_item_id": "s1-i1"} if n == 1 else {}))
                                                 for n in range(1, 10)])], {}),
    ("panel.missing_rationale", [board_scene("s1", side_panel={"kind": "image", "image_prompt": "a resistor"})], {}),
    ("panel.missing_rationale", [{"id": "v", "type": "ai_video", "video_prompt": "lab", "beats": [beat("v-b1")]}], {}),
    ("figure.unknown", [board_scene("s1", side_panel={"kind": "figure", "figure_id": "fig-9", "rationale": "r"})], {}),
    ("figure.unknown", [{"id": "v", "type": "ai_video", "video_prompt": "lab", "rationale": "r",
                         "fallback_figure_id": "fig-9", "beats": [beat("v-b1")]}], {}),
    ("example.blank_never_filled", [board_scene("s1", board=[{"id": "s1-i1", "kind": "example_step", "text": "x", "blank": True}])], {}),
    ("example.fill_without_pause", [board_scene("s1", board=[{"id": "s1-i1", "kind": "example_step", "text": "x", "blank": True}],
                                                beats=[beat("s1-b1", board_item_id="s1-i1"), beat("s1-b2", fill_item_id="s1-i1")])], {}),
    ("narration.markup", [board_scene("s1", beats=[beat("s1-b1", "We use $V = IR$ and **bold** words here.", board_item_id="s1-i1")])], {}),
    ("narration.markup", [board_scene("s1", beats=[beat("s1-b1", "Fine words for the captions here.", spoken="x^2 \\frac{a}{b}",
                                                        board_item_id="s1-i1")])], {}),
    ("source.unknown_ref", [board_scene("s1", beats=[beat("s1-b1", board_item_id="s1-i1", source_refs=["c0001", "c0099"])])],
     {"chunk_ids": {"c0001"}}),
    ("source.unknown_ref", [board_scene("s1", beats=[beat("s1-b1", board_item_id="s1-i1", source_refs=["chunk-7"])])], {}),
    ("manim.code_forbidden", [{"id": "sim", "type": "simulation", "manim": {"code": "import os\nclass A(AadhiScene):\n    pass"},
                               "beats": [beat("sim-b1")]}], {}),
])
def test_scene_level_codes(code, scenes, extra):
    assert code in codes(screenplay(scenes), None, **extra)


def test_quiz_missing_for_concepts():
    sp = screenplay([board_scene(x, concept_id=x) for x in ("a", "b", "c")],
                    concept_map=[{"id": x, "title": x.upper()} for x in ("a", "b", "c")])
    issues = [i for i in lint(sp, GenerationOptions(quiz_every_n_concepts=2)) if i.code == "quiz.missing_for_concepts"]
    assert len(issues) == 1 and issues[0].scene_id == "c"
    assert "quiz.missing_for_concepts" not in codes(sp, GenerationOptions(include_quizzes=False))


def test_quiz_answer_position_skew():
    sp = screenplay([board_scene("s1")] + [quiz_scene(f"q{i}", correct=1) for i in range(3)])
    assert "quiz.answer_position_skew" in codes(sp)
    varied = screenplay([board_scene("s1")] + [quiz_scene(f"q{i}", correct=i) for i in range(3)])
    assert "quiz.answer_position_skew" not in codes(varied)


def test_coverage_codes():
    sp = screenplay([board_scene("s1", concept_id=None, objective_ids=[])],
                    concept_map=[{"id": "a", "title": "A"}, {"id": "pre", "title": "P", "kind": "prerequisite"}])
    found = codes(sp, GenerationOptions())
    assert {"concept.unused", "objective.untaught", "objective.unassessed", "misconception.untargeted"} <= found
    unused = [i for i in lint(sp) if i.code == "concept.unused"]
    assert [i.message.split("'")[1] for i in unused] == ["a"]  # prerequisites are exempt
    targeted = screenplay([board_scene("s1", board=[{"id": "s1-i1", "kind": "misconception", "text": "w",
                                                     "justification": "r", "misconception_id": "m1"}])])
    assert "misconception.untargeted" not in codes(targeted)


def test_duration_mismatch():
    sp = screenplay([board_scene("s1"), quiz_scene("q1")])
    assert "lecture.duration_mismatch" in codes(sp, GenerationOptions(target_minutes=30))
    assert "lecture.duration_mismatch" not in codes(sp, None)


def test_disabled_media_flagged():
    sp = screenplay([{"id": "i", "type": "interactive", "p5_code": "function setup(){}", "beats": [beat("i-b1")]},
                     board_scene("s1", side_panel={"kind": "gif", "gif_query": "x", "rationale": "fun"})])
    issues = [i for i in lint(sp, GenerationOptions()) if i.code == "scene.type_disabled"]
    assert {i.scene_id for i in issues} == {"i", "s1"}


def test_manim_template_checks(templates):
    def sim(params: dict[str, Any], n_beats: int, template: str = "equation_steps") -> dict[str, Any]:
        return {"id": "sim", "type": "simulation", "manim": {"template": template, "params": params},
                "beats": [beat(f"sim-b{n}", visual_cue="c") for n in range(1, n_beats + 1)]}

    assert "manim.template_unknown" in codes(screenplay([sim({}, 1, "nope")]))
    assert "manim.params_invalid" in codes(screenplay([sim({"steps": "not a list"}, 1)]))
    assert "manim.beats_steps_mismatch" in codes(screenplay([sim({"steps": ["a", "b", "c"]}, 2)]))
    assert not {c for c in codes(screenplay([sim({"steps": ["a", "b"]}, 2)])) if c.startswith("manim.")}


def test_scenes_needing_repair_selection():
    sp = screenplay([board_scene("s1", beats=[beat("s1-b1", "We use $x$ here in this beat.", board_item_id="s1-i1")]),
                     board_scene("s2", beats=[beat("s2-b1")])])
    issues = lint(sp, GenerationOptions())
    targets = scenes_needing_repair(issues)
    assert "s1" in targets and "s2" not in targets  # unrevealed items (info) do not trigger rewrites
    assert all(i.code != "objective.unassessed" for v in targets.values() for i in v)


def test_lint_is_fast():
    import time

    scenes = [board_scene(f"s{n}") for n in range(150)]
    sp = screenplay(scenes)
    t = time.perf_counter()
    lint(sp, GenerationOptions())
    assert time.perf_counter() - t < 1.0


def test_repair_selection_follows_severity_and_source():
    from aadhi.pipeline.base import Issue

    issues = [
        Issue(code="beat.too_long", severity="warning", scene_id="lint_warn", source="lint", message="m"),
        Issue(code="example.blank_never_filled", severity="error", scene_id="lint_err", source="lint", message="m"),
        Issue(code="beat.too_long", severity="warning", scene_id="lint_err", source="lint", message="m"),
        Issue(code="pedagogy.issue", severity="warning", scene_id="critic_warn", source="critic", message="m"),
        Issue(code="flow.issue", severity="info", scene_id="critic_info", source="critic", message="m"),
        Issue(code="factual.error", severity="error", scene_id="unfixable", source="critic", message="m", fixable=False),
        Issue(code="scene.generation_failed", severity="error", scene_id="failed", source="system", message="m"),
        Issue(code="objective.unassessed", severity="warning", source="lint", message="lecture-level"),
    ]
    targets = scenes_needing_repair(issues)
    assert sorted(targets) == ["critic_warn", "failed", "lint_err"]
    # a selected scene's other fixable warnings travel with it so the rewrite fixes them too
    assert [i.code for i in targets["lint_err"]] == ["example.blank_never_filled", "beat.too_long"]
    narration = [i for i in lint(screenplay([board_scene("s1", beats=[beat("s1-b1", "Say $x$ aloud here please.",
                                                                            board_item_id="s1-i1")])]))
                 if i.code == "narration.markup"]
    assert narration and narration[0].severity == "error"


def test_manim_panel_steps_match_the_beats_it_is_shown_for(templates):
    panel = {"kind": "manim", "rationale": "see the algebra", "manim": {"template": "equation_steps",
                                                                         "params": {"steps": ["V = IR", "I = V/R"]}}}
    beats = [beat("s1-b1", board_item_id="s1-i1"), beat("s1-b2"), beat("s1-b3")]
    shown_late = board_scene("s1", beats=beats, side_panel={**panel, "show_from_beat_id": "s1-b2"})
    assert not [i for i in lint(screenplay([shown_late])) if i.code.startswith("manim.")]
    shown_all = board_scene("s1", beats=beats, side_panel=panel)
    codes = [(i.code, i.severity) for i in lint(screenplay([shown_all])) if i.code.startswith("manim.")]
    assert codes == [("manim.beats_steps_mismatch", "error")]


def test_scene_writer_checks_panel_steps(templates):
    from aadhi.pipeline.base import PlannedScene
    from aadhi.pipeline.gen_models import model_for_scene
    from aadhi.pipeline.script import template_step_problem

    planned = PlannedScene(id="s1", type="content", goal="g", side_panel_kind="manim", manim_template="equation_steps",
                           visual_rationale="why")
    model = model_for_scene("content", "equation_steps")
    beats = [{"narration": "One."}, {"narration": "Two."}, {"narration": "Three."}]
    panel = {"kind": "manim", "manim_params": {"steps": ["a", "b"]}}
    assert template_step_problem(planned, model.model_validate({"beats": beats, "side_panel": {**panel, "show_from_beat": 2}})) is None
    problem = template_step_problem(planned, model.model_validate({"beats": beats, "side_panel": panel}))
    assert problem and "2 steps" in problem and "3 beats" in problem
