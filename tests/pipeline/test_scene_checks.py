"""scene_checks: language, redundancy, quiz length, worked-example fading, signalling."""

from __future__ import annotations

import asyncio

from aadhi.pipeline.base import PlannedScene
from aadhi.pipeline.gen_models import GenAIVideo, GenBoardScene, GenQuiz
from aadhi.pipeline.scene_checks import language_problem, pedagogy_problems, script_share


def planned(**kw) -> PlannedScene:
    base = {"id": "s1", "type": "content", "goal": "Teach resistance"}
    base.update(kw)
    return PlannedScene(**base)


def board(beats, **kw) -> GenBoardScene:
    return GenBoardScene.model_validate({"title": "Resistance", "beats": beats, **kw})


def test_script_share_and_language_problem():
    tamil = "மின்தடை என்பது மின்னோட்டத்தை எதிர்க்கும் பண்பு ஆகும். Resistance என்று சொல்கிறோம்."
    assert script_share(tamil, "ta-IN") > 0.6
    assert script_share("short", "ta-IN") is None  # too little text to judge
    assert language_problem(tamil, "ta-IN", "narration") is None
    english = "Resistance is the opposition a material offers to the flow of current in a wire."
    problem = language_problem(english, "ta-IN", "narration")
    assert problem and "Tamil" in problem and "keep_in_english" in problem
    assert language_problem(tamil, "en-IN", "on-screen text") is not None
    assert language_problem(english, "en-IN", "narration") is None


def test_wrong_narration_language_is_reported():
    out = board([{"narration": "Resistance is the opposition a material offers to the flow of current here."},
                 {"narration": "Copper wires resist very little while rubber resists almost completely."}])
    problems = pedagogy_problems(planned(), out, language="hi-IN", board_language="en-IN")
    assert problems == ["narration must be written in Hindi; keep only glossary terms marked keep_in_english in English"]


def test_redundant_board_item():
    sentence = "Long thin and hot conductors resist the current more than short thick cold ones."
    out = board([
        {"narration": sentence, "board": {"kind": "bullet", "text": sentence}},
        {"narration": "Copper wires resist very little, so we use them for house wiring.",
         "board": {"kind": "bullet", "text": "Copper: low resistance"}},
    ])
    problems = pedagogy_problems(planned(), out)
    assert len(problems) == 1 and problems[0].startswith("beat 1: the board item repeats the narration")


def test_quiz_answer_must_not_stand_out():
    def quiz(correct: str) -> GenQuiz:
        return GenQuiz.model_validate({
            "question": "If R doubles at fixed V, what happens to I?", "correct": correct,
            "distractors": [{"text": "It doubles", "why_wrong": "inverse"}, {"text": "It stays", "why_wrong": "no"}],
        })

    long_answer = "It halves, because the current is inversely proportional to the resistance at fixed voltage"
    assert any("stands out" in p for p in pedagogy_problems(planned(type="quiz_checkpoint"), quiz(long_answer)))
    assert pedagogy_problems(planned(type="quiz_checkpoint"), quiz("It halves")) == []


def test_worked_example_needs_steps_and_fading():
    p = planned(type="example")
    no_steps = board([{"narration": "We compute the current from the voltage and the resistance now.",
                       "board": {"kind": "bullet", "text": "I = V / R"}}])
    assert any("example_step" in x for x in pedagogy_problems(p, no_steps))
    steps = [{"narration": f"Step {n} follows from the previous line of the working.",
              "board": {"kind": "example_step", "text": f"step {n}", "justification": "why"}} for n in range(1, 4)]
    assert any("fade the worked example" in x for x in pedagogy_problems(p, board(steps)))
    assert pedagogy_problems(p, board(steps), depth="overview") == []  # overviews need no fading
    steps[2]["board"]["blank"] = True
    assert pedagogy_problems(p, board(steps)) == []


def test_visuals_must_be_referred_to():
    beats = [{"narration": "Resistance opposes the flow of charge in every conductor."}]
    panel = {"kind": "image", "image_prompt": "a resistor", "rationale": "real part"}
    p = planned(side_panel_kind="image", visual_rationale="real part")
    assert any("never refers to the visual" in x for x in pedagogy_problems(p, board(beats, side_panel=panel)))
    pointed = [{"narration": "Look at the resistor in the picture: its colour bands encode the value."}]
    assert pedagogy_problems(p, board(pointed, side_panel=panel)) == []
    tamil = [{"narration": "மின்தடை என்பது மின்னோட்டத்தை எதிர்க்கும் பண்பு ஆகும் என்று நாம் அறிவோம்."}]
    # signalling is only checked for English narration (other languages are left to the critic)
    assert pedagogy_problems(p, board(tamil, side_panel=panel), language="ta-IN", board_language="en-IN") == []
    video = GenAIVideo.model_validate({"video_prompt": "a lathe cutting metal",
                                       "beats": [{"narration": "Machining removes metal layer by layer."}]})
    assert any("visual" in x for x in pedagogy_problems(planned(type="ai_video"), video))


def test_fake_content_scenes_pass_first_time(job_ctx, providers, sample_ingest, options):
    """The offline demo content follows the same rules, so it needs no re-asks."""
    from aadhi.pipeline.plan import generate_plan
    from aadhi.pipeline.script import write_scenes_detailed

    plan = asyncio.run(generate_plan(job_ctx, sample_ingest, options))
    asyncio.run(write_scenes_detailed(job_ctx, plan.plan, sample_ingest, options, lexicon=plan.lexicon))
    reasked = [c["schema"] for c in providers.llm.calls if c["attempt"] > 0 and c["schema"] != "GenPlan"]
    assert reasked == []
