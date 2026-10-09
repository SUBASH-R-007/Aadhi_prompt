"""canonicalize: ids, blanks/fills, highlights, quiz shuffle, panels, strict vs lenient."""

from __future__ import annotations

import pytest

from aadhi.pipeline.base import PlannedScene
from aadhi.pipeline.canonicalize import (
    CanonicalizationError,
    canonicalize_scene,
    child_id,
    planned_from_scene,
    scene_problems,
    shuffle_order,
)
from aadhi.pipeline.gen_models import (
    GenAIVideo,
    GenBoardScene,
    GenChapterCard,
    GenInteractive,
    GenQuiz,
    GenSimulationCode,
    model_for_scene,
)
from aadhi.schemas.screenplay import (
    AIVideoScene,
    BoardScene,
    ChapterCardScene,
    InteractiveScene,
    QuizScene,
    SimulationScene,
)


def planned(**kw) -> PlannedScene:
    base = {"id": "ohm", "type": "example", "goal": "Work an Ohm's law example", "key_points": ["V = IR"],
            "source_refs": ["c0001"], "concept_id": "ohm", "objective_ids": ["o1"]}
    base.update(kw)
    return PlannedScene(**base)


def board_out(**kw) -> GenBoardScene:
    data = {
        "title": "Worked example",
        "beats": [
            {"narration": "Here is the problem.", "board": {"kind": "callout_info", "text": "Find $I$"}},
            {"narration": "Step one uses the formula.", "board": {"kind": "example_step", "text": "$I = V/R$",
                                                                   "justification": "Ohm's law"}},
            {"narration": "Try step two yourself.", "board": {"kind": "example_step", "text": "$I = 2$", "blank": True},
             "pause_after": 3},
            {"narration": "Try step three too.", "board": {"kind": "example_step", "text": "$P = 8$", "blank": True},
             "pause_after": 2},
            {"narration": "Step three is eight watts.", "fill_previous_blank": True, "highlight_steps": [2]},
            {"narration": "And step two is two amps.", "fill_previous_blank": True, "highlight_steps": [1, 2]},
        ],
    }
    data.update(kw)
    return GenBoardScene.model_validate(data)


def test_board_ids_fills_and_highlights():
    scene = canonicalize_scene(planned(), board_out(), chapter_id="part-1", known_chunks={"c0001"})
    assert isinstance(scene, BoardScene)
    assert [b.id for b in scene.beats] == [f"ohm-b{n}" for n in range(1, 7)]
    assert [i.id for i in scene.board] == [f"ohm-i{n}" for n in range(1, 5)]
    assert scene.beats[0].board_item_id == "ohm-i1"
    # latest unfilled blank first: step three (i4), then step two (i3)
    assert scene.beats[4].fill_item_id == "ohm-i4"
    assert scene.beats[5].fill_item_id == "ohm-i3"
    assert scene.beats[4].highlight_item_ids == ["ohm-i2"]
    assert scene.beats[5].highlight_item_ids == ["ohm-i1", "ohm-i2"]
    assert scene.chapter_id == "part-1" and scene.concept_id == "ohm" and scene.objective_ids == ["o1"]
    assert scene.intent.goal == "Work an Ohm's law example" and scene.intent.source_refs == ["c0001"]
    assert scene.mascot_position == "left"


def test_fill_without_blank_is_a_problem():
    out = board_out(beats=[{"narration": "Nothing to fill.", "fill_previous_blank": True}])
    problems = scene_problems(planned(), out)
    assert any("no earlier blank" in p for p in problems)
    # lenient mode drops the dangling fill
    scene = canonicalize_scene(planned(), out, strict=False)
    assert scene.beats[0].fill_item_id is None


def test_blank_revealed_and_filled_in_same_beat_is_not_allowed():
    out = board_out(beats=[{"narration": "Reveal and fill.", "board": {"kind": "example_step", "text": "x", "blank": True},
                            "fill_previous_blank": True}])
    assert any("no earlier blank" in p for p in scene_problems(planned(), out))


def test_highlight_out_of_range_and_self():
    out = board_out(beats=[
        {"narration": "First item.", "board": {"kind": "bullet", "text": "a"}, "highlight_steps": [1]},
        {"narration": "Second.", "highlight_steps": [3]},
    ])
    problems = scene_problems(planned(), out)
    assert sum("highlight step" in p for p in problems) == 2
    scene = canonicalize_scene(planned(), out, strict=False)
    assert all(not b.highlight_item_ids for b in scene.beats)


def test_narration_markup_unknown_refs_and_figures():
    out = board_out(beats=[
        {"narration": "Use $V = IR$ and **this**.", "source_refs": ["c0001", "c0042"],
         "board": {"kind": "figure", "figure_id": "fig-9", "caption": "Circuit"}},
    ])
    problems = scene_problems(planned(), out, known_chunks={"c0001"}, known_figures={"fig-1"})
    assert any("plain spoken text" in p for p in problems)
    assert any("c0042" in p for p in problems)
    assert any("fig-9" in p for p in problems)
    scene = canonicalize_scene(planned(), out, strict=False, known_chunks={"c0001"}, known_figures={"fig-1"})
    assert scene.beats[0].source_refs == ["c0001"]
    assert scene.board[0].kind.value == "paragraph"  # unknown figure degraded


def test_kind_field_mapping():
    out = board_out(beats=[
        {"narration": "A definition.", "board": {"kind": "definition", "term": "Resistance", "text": "opposition to current"}},
        {"narration": "A formula.", "board": {"kind": "formula", "latex": "$V = IR$", "text": "Ohm's law",
                                              "variables": [{"symbol_latex": "V", "meaning": "voltage", "unit": "V"}]}},
        {"narration": "Some code.", "board": {"kind": "code", "code_language": "Python 3", "code": "print(1)"}},
        {"narration": "A table.", "board": {"kind": "table", "table_headers": ["a", "b"], "table_rows": [["1", "2"]]}},
        {"narration": "A misconception.", "board": {"kind": "misconception", "text": "Current is used up",
                                                    "justification": "It is conserved", "misconception_key": "Used Up"}},
    ])
    scene = canonicalize_scene(planned(type="content"), out, known_misconceptions={"used-up"})
    items = scene.board
    assert items[0].term == "Resistance"
    assert items[1].latex == "V = IR" and items[1].variables[0].meaning == "voltage"
    assert items[2].language == "python3"
    assert items[3].headers == ["a", "b"] and items[3].rows == [["1", "2"]]
    assert items[4].misconception_id == "used-up"


def test_ragged_table_problem_then_padding():
    out = board_out(beats=[{"narration": "A table.", "board": {"kind": "table", "table_headers": ["a", "b"],
                                                               "table_rows": [["1"], ["1", "2", "3"]]}}])
    assert any("table row" in p for p in scene_problems(planned(), out))
    scene = canonicalize_scene(planned(), out, strict=False)
    assert scene.board[0].rows == [["1", ""], ["1", "2"]]


def test_side_panel_follows_plan():
    p = planned(type="content", side_panel_kind="graph", visual_rationale="see the curve")
    out = board_out(beats=[{"narration": "Look at the graph."}, {"narration": "See the curve rise."}],
                    side_panel={"kind": "graph", "graph_functions": [{"expr": "x**2"}], "graph_x_min": 0, "graph_x_max": 4,
                                "show_from_beat": 2})
    scene = canonicalize_scene(p, out)
    assert scene.side_panel.kind == "graph"
    assert scene.side_panel.graph.functions[0].expr == "x^2"
    assert scene.side_panel.graph.x_range == (0.0, 4.0)
    assert scene.side_panel.show_from_beat_id == "ohm-b2"
    assert scene.side_panel.rationale == "see the curve"
    # missing planned panel is a problem; an unplanned panel is ignored
    assert any("side panel" in x for x in scene_problems(p, board_out(beats=[{"narration": "Hi there friends."}])))
    unplanned = canonicalize_scene(planned(type="content"), out)
    assert unplanned.side_panel is None


def test_side_panel_kinds_payloads():
    beats = [{"narration": "Look at this."}]
    cases = [
        ("figure", {"figure_id": "fig-1"}, lambda sp: sp.figure_id == "fig-1"),
        ("image", {"image_prompt": "a resistor"}, lambda sp: sp.image_prompt == "a resistor"),
        ("chart", {"chart_labels": ["a", "b"], "chart_datasets": [{"label": "x", "data": [1, 2]}]},
         lambda sp: sp.chart.datasets[0].data == [1, 2]),
        ("model_3d", {"model_primitives": [{"shape": "box", "x": 1, "size": [1, 2, 3]}]},
         lambda sp: sp.model_3d.primitives[0].position == (1.0, 0.0, 0.0)),
        ("terminal", {"terminal_command": "ls", "terminal_output": "a\nb"}, lambda sp: sp.terminal.command == "ls"),
        ("quiz", {"quiz_question": "Q?", "quiz_options": ["a", "b"], "quiz_correct_index": 1},
         lambda sp: sp.quiz.correct_index == 1),
        ("gif", {"gif_query": "electricity"}, lambda sp: sp.gif_query == "electricity"),
        ("manim", {"manim_code": "class A(AadhiScene):\n    pass"}, lambda sp: sp.manim.code.startswith("class")),
        ("skill_tree", {}, lambda sp: sp.kind == "skill_tree"),
    ]
    for kind, fields, check in cases:
        p = planned(type="content", side_panel_kind=kind, visual_rationale="why")
        scene = canonicalize_scene(p, board_out(beats=beats, side_panel={"kind": kind, **fields}), known_figures={"fig-1"})
        assert check(scene.side_panel), kind


def test_quiz_shuffle_is_deterministic_and_aligned():
    p = planned(id="quiz-1", type="quiz_checkpoint")
    out = GenQuiz.model_validate({
        "title": "Quick check", "question": "If R doubles at fixed V, the current…", "correct": "halves",
        "distractors": [
            {"text": "doubles", "why_wrong": "That is the inverse trap.", "misconception_key": "inverse"},
            {"text": "stays the same", "why_wrong": "Current depends on R."},
            {"text": "becomes zero", "why_wrong": "Only for infinite R."},
        ],
        "explanation": "I = V/R", "countdown_seconds": 50,
        "question_beats": [{"narration": "Here is the question."}],
        "reveal_beats": [{"narration": "It halves."}, {"narration": "Because I equals V over R."}],
    })
    a = canonicalize_scene(p, out, known_misconceptions={"inverse"})
    b = canonicalize_scene(p, out, known_misconceptions={"inverse"})
    assert isinstance(a, QuizScene)
    assert a.options == b.options and a.correct_index == b.correct_index
    assert a.options[a.correct_index] == "halves"
    assert a.feedback_wrong[a.correct_index] == ""
    i_doubles = a.options.index("doubles")
    assert a.feedback_wrong[i_doubles] == "That is the inverse trap."
    assert a.option_misconception_ids[i_doubles] == "inverse"
    assert a.countdown_seconds == 30
    assert [x.id for x in a.beats] == ["quiz-1-b1"]
    assert [x.id for x in a.reveal_beats] == ["quiz-1-b2", "quiz-1-b3"]
    assert sorted(shuffle_order("quiz-1", 4)) == [0, 1, 2, 3]
    positions = {canonicalize_scene(planned(id=f"q{i}", type="quiz_checkpoint"), out).correct_index for i in range(30)}
    assert len(positions) > 1  # the seed varies with the scene id


def test_quiz_duplicate_and_unknown_misconception():
    p = planned(id="q", type="quiz_checkpoint")
    out = GenQuiz.model_validate({
        "title": "T", "question": "Q?", "correct": "A",
        "distractors": [{"text": "a", "why_wrong": "dup"}, {"text": "B", "why_wrong": "x", "misconception_key": "ghost"}],
        "question_beats": [{"narration": "Question time."}], "reveal_beats": [{"narration": "Answer time."}],
    })
    problems = scene_problems(p, out, known_misconceptions={"real"})
    assert any("duplicates" in x for x in problems) and any("ghost" in x for x in problems)
    scene = canonicalize_scene(p, out, strict=False, known_misconceptions={"real"})
    assert len(scene.options) == 2 and all(m is None for m in scene.option_misconception_ids)


def test_chapter_card_and_simulation_and_media_scenes():
    card = canonicalize_scene(planned(id="c2", type="chapter_card"),
                              GenChapterCard.model_validate({"title": "Power", "beats": []}), chapter_label="Part 2")
    assert isinstance(card, ChapterCardScene) and card.chapter_label == "Part 2" and card.mascot_position == "center"
    sim = canonicalize_scene(planned(id="sim", type="simulation"), GenSimulationCode.model_validate({
        "title": "Animated", "code": "class A(AadhiScene):\n    pass",
        "beats": [{"narration": "Watch the dot.", "visual_cue": "dot appears"}],
    }))
    assert isinstance(sim, SimulationScene) and sim.manim.code and sim.mascot_position == "right"
    assert any("visual_cue" in p for p in scene_problems(planned(id="sim", type="simulation"), GenSimulationCode.model_validate(
        {"title": "A", "code": "x", "beats": [{"narration": "No cue here."}]})))
    vid = canonicalize_scene(planned(id="v", type="ai_video", visual_rationale="real"), GenAIVideo.model_validate({
        "title": "Lab", "video_prompt": "A lathe", "fallback_figure_id": "fig-x", "beats": [{"narration": "Look."}],
    }), strict=False, known_figures=set())
    assert isinstance(vid, AIVideoScene) and vid.fallback_figure_id is None and vid.rationale == "real"
    inter = canonicalize_scene(planned(id="i", type="interactive"), GenInteractive.model_validate({
        "title": "Play", "p5_code": "function setup(){}", "beats": [{"narration": "Try it."}]}))
    assert isinstance(inter, InteractiveScene)


def test_non_board_scene_rejects_board_fields():
    out = GenAIVideo.model_validate({"title": "x", "video_prompt": "y",
                                     "beats": [{"narration": "Look.", "board": {"kind": "bullet", "text": "b"}}]})
    assert any("has no board" in p for p in scene_problems(planned(id="v", type="ai_video"), out))


def test_schema_errors_are_reported():
    out = board_out(beats=[{"narration": "A formula without latex.", "board": {"kind": "formula", "text": "x"}}])
    with pytest.raises(CanonicalizationError) as exc:
        canonicalize_scene(planned(), out)
    assert any("latex" in p for p in exc.value.problems)


def test_template_scene_model_and_child_ids(monkeypatch, templates):
    model = model_for_scene("simulation", "equation_steps")
    assert model.__name__ == "GenSimulation_equation_steps" and "params" in model.model_fields
    assert model_for_scene("simulation", None) is GenSimulationCode
    assert model_for_scene("content", "equation_steps").__name__ == "GenBoardScene_equation_steps"
    long = "x" * 64
    assert len(child_id(long, "b", 12)) == 64


def test_planned_from_scene_roundtrip():
    scene = canonicalize_scene(planned(type="content", side_panel_kind="graph", visual_rationale="r"), board_out(
        beats=[{"narration": "Hello there friends."}],
        side_panel={"kind": "graph", "graph_functions": [{"expr": "x"}]}))
    p = planned_from_scene(scene, est_seconds=40)
    assert p.id == "ohm" and p.type == "content" and p.side_panel_kind == "graph"
    assert p.goal == "Work an Ohm's law example" and p.source_refs == ["c0001"] and p.est_seconds == 40


def test_invalid_board_item_strict_problem_then_lenient_degrade():
    beats = [
        {"narration": "The formula is coming.", "board": {"kind": "formula", "text": "Ohm's law in symbols"}},
        {"narration": "An empty table follows.", "board": {"kind": "table"}},
        {"narration": "Then a normal bullet.", "board": {"kind": "bullet", "text": "Copper resists little"}},
    ]
    p = planned(type="content")
    problems = scene_problems(p, board_out(beats=beats))
    assert any("beat 1: board item (formula)" in x and "latex" in x for x in problems), problems
    assert any("beat 2: board item (table)" in x for x in problems), problems
    scene = canonicalize_scene(p, board_out(beats=beats), strict=False)
    # the formula without LaTeX degrades to a bullet; the empty table is dropped (its beat reveals nothing)
    assert [(i.id, i.kind.value, i.text) for i in scene.board] == [
        ("ohm-i1", "bullet", "Ohm's law in symbols"), ("ohm-i2", "bullet", "Copper resists little")]
    assert [b.board_item_id for b in scene.beats] == ["ohm-i1", None, "ohm-i2"]


def test_invalid_side_panel_strict_problem_then_lenient_drop():
    p = planned(type="content", side_panel_kind="graph", visual_rationale="see the curve")
    out = board_out(beats=[{"narration": "Look at the graph."}],
                    side_panel={"kind": "graph", "graph_functions": [{"expr": "import os"}]})
    problems = scene_problems(p, out)
    assert any(x.startswith("side_panel:") for x in problems), problems
    scene = canonicalize_scene(p, out, strict=False)
    assert scene.side_panel is None


# ---------------------------------------------------------------------------
# lenient mode clamps schema limits (review round 2)
# ---------------------------------------------------------------------------


def test_lenient_mode_clamps_over_long_board_scene():
    beats = [{"narration": f"Point {n} " + "x" * (1600 if n == 1 else 10), "spoken": "y" * 2100 if n == 2 else None,
              "visual_cue": "z" * 700, "board": {"kind": "bullet", "text": f"item {n}"}} for n in range(1, 14)]
    beats += [{"narration": f"Extra beat {n}."} for n in range(30)]  # 43 beats in total
    out = GenBoardScene.model_validate({"title": "T" * 241, "subtitle": "s" * 400, "beats": beats})
    p = planned(type="content", source_refs=[])
    with pytest.raises(CanonicalizationError) as exc:
        canonicalize_scene(p, out, strict=True)
    assert any("240" in x for x in exc.value.problems)
    scene = canonicalize_scene(p, out, strict=False)
    assert isinstance(scene, BoardScene)
    assert len(scene.title) == 240 and len(scene.subtitle) == 320
    assert len(scene.board) == 12 and len(scene.beats) == 40
    assert len(scene.beats[0].narration) == 1500 and len(scene.beats[1].spoken) == 2000
    assert all(len(b.visual_cue or "") <= 600 for b in scene.beats)
    assert scene.beats[12].board_item_id is None  # the 13th item was not added; its beat stays
    assert [b.board_item_id for b in scene.beats[:12]] == [i.id for i in scene.board]


def test_lenient_mode_clamps_quiz_and_media_texts():
    out = GenQuiz.model_validate({
        "title": "Check", "question": "q" * 700, "correct": "a" * 300,
        "distractors": [{"text": "b", "why_wrong": "no"}, {"text": "c", "why_wrong": "no"}],
        "explanation": "e" * 1000,
        "question_beats": [{"narration": "Which one?"}],
        "reveal_beats": [{"narration": f"Reveal {n}."} for n in range(12)],
    })
    p = planned(id="quiz", type="quiz_checkpoint", source_refs=[])
    with pytest.raises(CanonicalizationError):
        canonicalize_scene(p, out, strict=True)
    q = canonicalize_scene(p, out, strict=False)
    assert isinstance(q, QuizScene)
    assert len(q.question) == 600 and len(q.explanation) == 900 and max(len(o) for o in q.options) == 240
    assert len(q.reveal_beats) == 10 and q.reveal_beats[0].id == "quiz-b2"
    video = GenAIVideo.model_validate({"title": "Lab", "video_prompt": "v" * 1300, "rationale": "r" * 500,
                                       "fallback_image_prompt": "f" * 900, "beats": [{"narration": "See this."}]})
    v = canonicalize_scene(planned(id="vid", type="ai_video"), video, strict=False)
    assert isinstance(v, AIVideoScene) and len(v.video_prompt) == 1200 and len(v.rationale) == 400
    assert len(v.fallback_image_prompt) == 800


def test_lenient_mode_never_truncates_code():
    sim = GenSimulationCode.model_validate({"title": "Sim", "code": "x" * 20001,
                                            "beats": [{"narration": "Watch.", "visual_cue": "dot"}]})
    with pytest.raises(CanonicalizationError) as exc:
        canonicalize_scene(planned(id="sim", type="simulation"), sim, strict=False)
    assert any("20000" in p for p in exc.value.problems)
    sketch = GenInteractive.model_validate({"title": "Play", "p5_code": "function setup(){}\n//" + "x" * 20001,
                                            "beats": [{"narration": "Try it."}]})
    with pytest.raises(CanonicalizationError):
        canonicalize_scene(planned(id="play", type="interactive"), sketch, strict=False)
