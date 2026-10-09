"""fake_content against the real FakeLLM (providers area) and its re-ask loop."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.pipeline import fake_content
from aadhi.pipeline.companion import build_sheet
from aadhi.pipeline.critic import critique
from aadhi.pipeline.plan import generate_plan
from aadhi.pipeline.script import write_scenes_detailed
from aadhi.pipeline.validate import lint
from aadhi.schemas.screenplay import QuizScene, Screenplay, SimulationScene

fake_llm = pytest.importorskip("aadhi.providers.llm.fake")


@pytest.fixture()
def registry(monkeypatch):
    """Isolate the class-level FakeLLM registry."""
    monkeypatch.setattr(fake_llm.FakeLLM, "_responders", dict(fake_llm.FakeLLM._responders))
    fake_content.register_fake_responders()
    return fake_llm.FakeLLM


def test_register_fake_responders(registry):
    names = set(registry.registered())
    assert {"GenPlan", "GenBoardScene", "GenQuiz", "GenCritique", "GenTranslation", "GenPractice"} <= names
    assert all(f"GenSimulation_{t}" in names for t in ("equation_steps", "function_plot"))
    assert fake_content.responder_for("GenSimulation_wave") is fake_content.simulation_responder
    assert fake_content.responder_for("GenBoardScene_geometry") is fake_content.board_responder
    assert fake_content.responder_for("Nope") is None


def test_full_offline_lecture_with_real_fake_llm(registry, job_ctx, sample_ingest, options):
    """LLM_PROVIDER=fake end to end: real factory, real FakeLLM, real Manim template registry."""
    from aadhi.pipeline import integrations

    llm = integrations.get_llm(job_ctx.settings)
    assert llm.name == "fake"
    plan_res = asyncio.run(generate_plan(job_ctx, sample_ingest, options))
    res = asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, sample_ingest, options, lexicon=plan_res.lexicon))
    sp = res.screenplay
    assert not res.fallback_scene_ids, res.issues
    Screenplay.model_validate(sp.model_dump(mode="json"))
    sims = [s for s in sp.scenes if isinstance(s, SimulationScene)]
    if integrations.list_templates():
        assert sims and sims[0].manim.template and integrations.manim_spec_problems(sims[0].manim, len(sims[0].beats)) == []
    assert any(isinstance(s, QuizScene) for s in sp.scenes)
    errors = [i for i in lint(sp, options, chunk_ids={c.id for c in sample_ingest.chunks}) if i.severity == "error"]
    assert not errors, errors
    issues = asyncio.run(critique(job_ctx, sp, sample_ingest, options))
    assert all(i.source in ("critic", "system") for i in issues)
    sheet = asyncio.run(build_sheet(job_ctx, sp, sample_ingest, options))
    assert sheet.practice_problems and sheet.key_formulas
    assert any(u.provider == "fake" for u in job_ctx.usages)


def test_fake_content_helpers():
    assert fake_content.find_formula("so V = I × R here") == ("V", "I", "*", "R")
    assert fake_content.find_formula("I = V/R") == ("I", "V", "/", "R")
    assert fake_content.find_formula("x = x y") is None
    f = fake_content.find_formula("P = VI")
    assert fake_content.formula_latex(f) == "P = V \\times I" and fake_content.formula_spoken(f) == "P equals V times I"
    assert fake_content.speakable("a^2 + b_1 = $x$ 5%") == "a to the power 2 + b 1 = x 5 percent"
    assert fake_content.shorten("word " * 40, 30).endswith("…")
    assert fake_content.title_of("Ohm's Law > 2.1 Resistance") == "Resistance"
    assert fake_content.formula_variables(("V", "I", "*", "R"), "where V is the voltage and I is the current") == [
        {"symbol_latex": "V", "meaning": "voltage"}, {"symbol_latex": "I", "meaning": "current"}]


def test_fake_content_copes_with_video_script_sources():
    """Teachers sometimes upload production scripts: labels, timestamps and stage directions are not content."""
    assert fake_content.title_of("SEGMENT 1 - WHAT THIS VIDEO WILL COVER [0:00 – 0:45]") == "What This Video Will Cover"
    assert fake_content.title_of("BOARD Title: THE SECRET LANGUAGE BEHIND COMPUTERS") == "The Secret Language Behind Computers"
    assert fake_content.title_of("Ohm's Law > 2.1 Resistance") == "Resistance"
    assert fake_content.is_metadata_heading("UNIT NAME : LOGIC GATES") and not fake_content.is_metadata_heading("Logic gates")
    text = ("ANIMATION: Bullets appear one by one.\nAadhi speaks: A logic gate makes a decision from its inputs.\n"
            "DETAILED ANIMATION / VISUAL:\nThe AND gate outputs one only when both inputs are one.")
    assert fake_content.sentences(text) == ["A logic gate makes a decision from its inputs.",
                                            "The AND gate outputs one only when both inputs are one."]
    chunks = [{"text": "A BJT has three terminals. The BJT and the LED share a PLC board.\n"
                       "BOARD DISPLAYS THE MOSFET\nThe MOSFET is a NAND driver. Turn it OFF. The board is green.\n"
                       "Use the PLC here. The LED glows. Never say IS loudly."}]
    assert fake_content.find_acronyms(chunks) == ["BJT", "LED", "PLC"]  # MOSFET/NAND are said as words; BOARD is emphasis


def test_formula_animation_uses_the_real_template():
    from aadhi.pipeline import integrations
    from aadhi.pipeline.prompting import json_section

    info = next((t for t in integrations.list_templates() if t.name == "equation_steps"), None)
    if info is None:
        pytest.skip("manim template library not installed")
    tpl = {"name": info.name, "params_schema": info.params_schema, "example_params": info.example_params}
    prompt = "\n".join([
        json_section("This scene", {"type": "simulation", "concept": {"title": "Ohm's law"}, "source_refs": ["c0001"]}),
        json_section("Relevant source", [{"id": "c0001", "text": "In symbols, V = I × R where V is the voltage."}]),
        json_section("Animation template", tpl),
    ])
    from aadhi.pipeline.gen_models import simulation_model

    out = fake_content.simulation_responder(prompt, simulation_model("equation_steps"))
    model, problems = integrations.validate_template_params("equation_steps", out["params"])
    assert model is not None and problems == []
    assert integrations.template_step_count("equation_steps", out["params"]) == len(out["beats"]) == 4
    assert out["params"]["steps"][1]["latex"] == r"I = \frac{V}{R}" and out["params"]["steps"][-1]["latex"] == "I = 3"
    assert all(b["visual_cue"] for b in out["beats"])


# ---------------------------------------------------------------------------
# flow of the offline lecture (the Kirchhoff script)
# ---------------------------------------------------------------------------

KCL = "Kirchhoff’s current law"
KVL = "Kirchhoff’s voltage law"
LECTURE = {"session_title": "Kirchhoff’s laws", "objectives": [],
           "concepts": [{"id": "kcl", "title": KCL}, {"id": "kvl", "title": KVL}]}


def _scene_prompt(this: dict, chunks: list[dict], **extra: object) -> str:
    from aadhi.pipeline.prompting import json_section

    parts = [json_section("Lecture", {**LECTURE, **extra}), json_section("This scene", this),
             json_section("Relevant source", {"note": "n", "chunks": chunks})]
    return "\n".join(parts)


def test_compound_objective_verbs_are_said_once():
    assert fake_content._goals([f"state and apply {KCL}", f"state and apply {KVL}"]) == (
        f"state and apply {KCL} and {KVL}")
    assert fake_content._goals(["explain voltage", "explain current", "apply Ohm's law"]) == (
        "explain voltage and current, and apply Ohm's law")


def test_a_worked_example_starts_with_the_sources_setup():
    text = ("Consider a node where 5 A and 3 A enter and one unknown current I leaves.\nApply KCL:\n5 + 3 = I\n"
            "Therefore:\nI = 8 A\nThe unknown current leaving the node is 8 ampere.")
    prompt = _scene_prompt({"type": "example", "concept": {"title": KCL}, "source_refs": ["c0004"],
                            "bridge_in": "Let us put it to work."}, [{"id": "c0004", "heading": "Worked example",
                                                                      "text": text}])
    beats = [b["narration"] for b in fake_content.board_responder(prompt, None)["beats"]]
    assert beats[0] == "Let us put it to work."
    assert beats[1].startswith("Consider a node where 5 A and 3 A enter")
    assert beats.index(next(b for b in beats if "5 + 3" in b)) > 1


def test_content_beats_cite_the_chunk_that_holds_their_sentence():
    chunks = [{"id": "c0001", "heading": "Introduction", "text": "Gustav Kirchhoff stated these laws in 1845."},
              {"id": "c0003", "heading": KCL, "text": "Kirchhoff’s Current Law states that the algebraic sum of the "
                                                      "currents entering a node is zero."}]
    prompt = _scene_prompt({"type": "content", "concept": {"title": KCL}, "source_refs": ["c0001", "c0003"],
                            "bridge_in": "First, the current law."}, chunks)
    beats = fake_content.board_responder(prompt, None)["beats"]
    by_text = {b["narration"][:20]: b.get("source_refs") for b in beats}
    assert by_text["Gustav Kirchhoff sta"] == ["c0001"] and by_text["Kirchhoff’s Current "] == ["c0003"]


def test_a_quiz_is_titled_after_the_concept_its_question_checks():
    chunks = [{"id": "c0007", "heading": "Quick quiz", "text": "Question 1:\nWhat does Kirchhoff’s Current Law conserve?"
                                                                "\nAnswer:\nElectric charge.\nQuestion 2:\nWhat is the "
                                                                "sum of the voltages around a loop?\nAnswer:\nZero."}]
    this = {"type": "quiz_checkpoint", "concept": {"title": KVL}, "source_refs": ["c0007"],
            "key_points": ["What does Kirchhoff’s Current Law conserve? Answer: Electric charge."]}
    out = fake_content.quiz_responder(_scene_prompt(this, chunks), None)
    assert out["title"] == f"Quick check: {KCL}"
    options = [d["text"] for d in out["distractors"]]
    assert "Electric energy" in options and not any("textbook" in o for o in options)


def test_summary_takeaways_state_the_law_not_its_history():
    from aadhi.pipeline.prompting import json_section

    chunks = [{"id": "c0001", "heading": f"Clip > {KCL}", "text": "Gustav Kirchhoff stated these laws in 1845. A node "
               "is a point where two or more circuit elements meet. Kirchhoff’s Current Law states that the algebraic "
               "sum of the currents entering a node is zero."},
              {"id": "c0002", "heading": "Clip > Summary", "text": "KCL says current into a node equals current out."}]
    brief = {"topic": KCL, "teaching_order": ["kcl"], "concepts": [
        {"key": "kcl", "name": KCL, "must_explain": ["Gustav Kirchhoff stated these laws in 1845."],
         "key_facts": [{"text": "Kirchhoff’s Current Law states that the algebraic sum of the currents entering a node "
                                "is zero.", "source_refs": ["c0001"]}], "source_refs": ["c0001"]}]}
    prompt = "\n".join([json_section("Request", {"target_seconds": 300, "allowed_scene_types": ["title", "content",
                                                                                              "summary"]}),
                        json_section("Concepts to teach", brief), json_section("Source chunks", chunks)])
    plan = fake_content.plan_responder(prompt, None)
    summary = next(s for ch in plan["chapters"] for s in ch["scenes"] if s["type"] == "summary")
    assert summary["key_points"] == [f"{KCL}: Kirchhoff’s Current Law states that the algebraic sum of the currents "
                                     "entering a node is zero."]
    assert "1845" not in plan["misconceptions"][0]["correction"]


def test_objective_statements_are_not_teaching_sentences():
    text = ("We will learn Kirchhoff’s Current Law and how to apply it at a node.\n"
            "A node is a point where two or more circuit elements meet.")
    assert fake_content.prose_sentences(text) == ["A node is a point where two or more circuit elements meet."]
