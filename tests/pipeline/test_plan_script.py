"""plan + script + canonicalize with the FakeLLM and fake_content -> valid Screenplay."""

from __future__ import annotations

import asyncio

import pytest

from aadhi.pipeline import fake_content
from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.gen_models import GenPlan
from aadhi.pipeline.plan import build_plan_prompt, generate_plan, lexicon_seed, plan_context
from aadhi.pipeline.plan_rules import normalize_plan, plan_problems
from aadhi.pipeline.script import write_scenes, write_scenes_detailed
from aadhi.pipeline.validate import lint
from aadhi.schemas.screenplay import BoardScene, ChapterCardScene, QuizScene, Screenplay, SimulationScene


def _plan(job_ctx, ingest, options):
    return asyncio.run(generate_plan(job_ctx, ingest, options))


def test_fake_plan_is_valid_and_follows_rules(job_ctx, providers, templates, sample_ingest, options):
    res = _plan(job_ctx, sample_ingest, options)
    plan = res.plan
    scenes = plan.all_scenes()
    assert scenes[0].type == "title"
    assert all(ch.scenes[0].type == "chapter_card" for ch in plan.chapters[1:])
    assert any(s.type == "quiz_checkpoint" for s in scenes)
    assert any(s.type == "simulation" and s.manim_template == "equation_steps" for s in scenes)
    assert scenes[-1].type == "summary"
    total = sum(s.est_seconds for s in scenes)
    assert 0.75 * 360 <= total <= 1.25 * 360
    chunk_ids = {c.id for c in sample_ingest.chunks}
    assert all(r in chunk_ids for s in scenes for r in s.source_refs)
    # first validation pass was clean: the model was asked exactly once
    assert len(providers.llm.calls_for("GenPlan")) == 1
    assert {o.id for o in plan.learning_objectives} <= {o for s in scenes for o in s.objective_ids}


def test_plan_prompt_contains_context(job_ctx, templates, sample_ingest):
    opts = GenerationOptions(previous_session_summary="We studied charge.", extra_instructions="Use cricket examples.",
                             subject_name="BEE")
    pc = plan_context(sample_ingest, opts, job_ctx.settings)
    from aadhi.pipeline.plan import TeacherMeta

    prompt = build_plan_prompt(sample_ingest, opts, pc, TeacherMeta(unit_name="Unit 2"))
    assert "Use cricket examples." in prompt and "We studied charge." in prompt
    assert '"subject_name": "BEE"' in prompt and '"unit_name": "Unit 2"' in prompt
    assert "equation_steps" in prompt and "fig-p1-1" in prompt and "c0001" in prompt


def test_previous_summary_gives_recap_second(job_ctx, providers, templates, sample_ingest):
    opts = GenerationOptions(target_minutes=6, previous_session_summary="Last time we met electric charge.")
    plan = _plan(job_ctx, sample_ingest, opts).plan
    assert [s.type for s in plan.all_scenes()[:2]] == ["title", "recap"]


def test_teacher_metadata_overrides_model(job_ctx, providers, sample_ingest):
    opts = GenerationOptions(target_minutes=6, subject_name="Basic Electrical", session_title="Ohm's law day")
    plan = _plan(job_ctx, sample_ingest, opts).plan
    assert plan.subject_name == "Basic Electrical"
    assert plan.session_title == "Ohm's law day"


def test_plan_reask_then_normalise(job_ctx, providers, sample_ingest, options):
    """A sloppy plan is re-asked once; whatever stays wrong is fixed deterministically."""
    bad = {
        "session_title": "Ohm",
        "concept_map": [{"key": "Ohm's Law!", "title": "Ohm's law", "depends_on": ["ghost", "Ohm's Law!"]},
                        {"key": "power", "title": "Power"}],
        "learning_objectives": [{"key": "o1", "text": "Apply Ohm's law", "concept_keys": ["Ohm's Law!"]}],
        "misconceptions": [{"key": "m1", "concept_key": "power", "statement": "x", "correction": "y"}],
        "chapters": [
            {"key": "a", "title": "A", "scenes": [
                {"key": "wrap", "type": "summary", "goal": "wrap up first?!"},
                {"key": "s1", "type": "content", "goal": "teach ohm", "concept_key": "Ohm's Law!", "source_refs": ["c0001", "c9999"]},
                {"key": "s1", "type": "interactive", "goal": "play", "concept_key": "power"},
            ]},
            {"key": "b", "title": "B", "scenes": [
                {"key": "s3", "type": "content", "goal": "power", "concept_key": "power", "objective_keys": ["nope"],
                 "manim_template": "unknown_tpl", "side_panel_kind": "image"},
            ]},
        ],
    }
    calls = {"n": 0}

    def responder(prompt, schema):
        calls["n"] += 1
        return bad

    providers.llm.on("GenPlan", responder)
    res = _plan(job_ctx, sample_ingest, options)
    assert calls["n"] == 2  # soft problems reported once, then accepted and normalised
    assert "## Problems with your previous answer" in providers.llm.calls_for("GenPlan")[1]["prompt"]
    plan = res.plan
    scenes = plan.all_scenes()
    ids = [s.id for s in scenes]
    assert len(ids) == len(set(ids))
    assert scenes[0].type == "title"  # hook inserted
    assert plan.chapters[1].scenes[0].type == "chapter_card"
    assert all(s.type != "interactive" for s in scenes)  # disallowed type converted
    assert all("c9999" not in s.source_refs for s in scenes)
    assert any(s.type == "quiz_checkpoint" for s in scenes)
    assert any("o1" in s.objective_ids for s in scenes if s.type == "quiz_checkpoint")
    assert res.notes


def test_plan_hard_problem_fails(job_ctx, providers, sample_ingest, options):
    from aadhi.providers.base import ProviderError

    providers.llm.on("GenPlan", lambda p, s: {"chapters": []})
    with pytest.raises(ProviderError):
        _plan(job_ctx, sample_ingest, options)


def test_plan_rules_quiz_gaps_and_cycles(sample_ingest):
    from aadhi.pipeline.plan_rules import PlanContext, quiz_gaps

    gen = GenPlan.model_validate({
        "concept_map": [{"key": k, "title": k, "depends_on": d} for k, d in (("a", ["c"]), ("b", ["a"]), ("c", ["b"]))],
        "chapters": [{"key": "x", "title": "X", "scenes": [
            {"key": f"s{i}", "type": "content", "goal": "g", "concept_key": c} for i, c in enumerate("abc")
        ]}],
    })
    assert quiz_gaps(gen, 2) == [(2, ["a", "b"])]
    pc = PlanContext(options=GenerationOptions(), chunk_ids={c.id for c in sample_ingest.chunks})
    hard, soft = plan_problems(gen, pc)
    assert not hard and any("quiz_checkpoint" in s for s in soft)
    fixed, rep = normalize_plan(gen, pc)
    from aadhi.pipeline.plan import TeacherMeta, to_lecture_plan

    plan = to_lecture_plan(fixed, GenerationOptions(), TeacherMeta())  # acyclic after normalisation
    assert any(s.type == "quiz_checkpoint" for s in plan.all_scenes())


def test_plan_rules_misconception_coverage(sample_ingest):
    from aadhi.pipeline.plan_rules import PlanContext

    gen = GenPlan.model_validate({
        "concept_map": [{"key": "ohm", "title": "Ohm"}],
        "misconceptions": [{"key": "used_up", "concept_key": "ohm", "statement": "Current is used up.",
                            "correction": "Charge is conserved."}],
        "chapters": [{"key": "x", "title": "X", "scenes": [
            {"key": "hook", "type": "title", "goal": "g", "concept_key": "ohm"},
            {"key": "teach", "type": "content", "goal": "g", "concept_key": "ohm"},
        ]}],
    })
    pc = PlanContext(options=GenerationOptions(include_quizzes=False))
    _, soft = plan_problems(gen, pc)
    assert any("misconception 'used_up' is not confronted" in s for s in soft)
    fixed, rep = normalize_plan(gen, pc)
    teach = next(s for s in fixed.chapters[0].scenes if s.key == "teach")
    assert teach.misconception_keys == ["used_up"] and any("used_up" in n for n in rep.notes)
    assert not any("not confronted" in s for s in plan_problems(fixed, pc)[1])


def test_lexicon_seed():
    gen = GenPlan.model_validate({"glossary_terms": [
        {"written": "BJT", "spoken": "B J T", "keep_in_english": True},
        {"written": "Voltage", "spoken": "voltage"},
        {"written": "bjt", "spoken": "dup"},
    ]})
    seed = lexicon_seed(gen, "en-IN")
    assert [(e.written, e.spoken, e.keep_in_english) for e in seed] == [("BJT", "B J T", True)]


def test_script_produces_valid_screenplay(job_ctx, providers, templates, sample_ingest, options):
    plan_res = _plan(job_ctx, sample_ingest, options)
    res = asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, sample_ingest, options, lexicon=plan_res.lexicon))
    sp = res.screenplay
    assert isinstance(sp, Screenplay)
    assert not res.fallback_scene_ids, res.issues
    Screenplay.model_validate(sp.model_dump(mode="json"))  # round-trips
    assert [s.id for s in sp.scenes] == [s.id for s in plan_res.plan.all_scenes()]
    assert {c.id for c in sp.chapters} == {c.id for c in plan_res.plan.chapters}
    for s in sp.scenes:
        assert s.intent is not None and s.intent.goal
        for n, b in enumerate(s.all_beats(), 1):
            assert b.id == f"{s.id}-b{n}"
        if isinstance(s, BoardScene):
            assert [i.id for i in s.board] == [f"{s.id}-i{n}" for n in range(1, len(s.board) + 1)]
    kinds = {type(s) for s in sp.scenes}
    assert {BoardScene, QuizScene, ChapterCardScene, SimulationScene} <= kinds
    sim = next(s for s in sp.scenes if isinstance(s, SimulationScene))
    assert sim.manim.template == "equation_steps" and len(sim.beats) == len(sim.manim.params["steps"])
    example = next(s for s in sp.scenes if isinstance(s, BoardScene) and s.type == "example")
    assert any(i.blank for i in example.board) and any(b.fill_item_id for b in example.beats)
    errors = [i for i in lint(sp, options, chunk_ids={c.id for c in sample_ingest.chunks}) if i.severity == "error"]
    assert not errors, errors
    # per-scene prompts carry neighbours, sources and the family prompt
    board_call = providers.llm.calls_for("GenBoardScene")[0]
    assert "## Relevant source" in board_call["prompt"] and "## Neighbouring scenes" in board_call["prompt"]
    assert "Your task: write one board scene" in board_call["system"]


def test_write_scenes_contract_returns_screenplay(job_ctx, providers, sample_ingest, options):
    plan = _plan(job_ctx, sample_ingest, options).plan
    sp = asyncio.run(write_scenes(job_ctx, plan, sample_ingest, options))
    assert isinstance(sp, Screenplay)


def test_scene_failure_becomes_fallback_and_issue(job_ctx, providers, sample_ingest, options):
    from aadhi.providers.base import ProviderError

    plan = _plan(job_ctx, sample_ingest, options).plan

    def broken(prompt, schema):
        return ProviderError("model exploded key=sk-123456", provider="fake")

    providers.llm.on("GenQuiz", broken)
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, options))
    quiz_ids = [s.id for s in plan.all_scenes() if s.type == "quiz_checkpoint"]
    assert set(res.fallback_scene_ids) == set(quiz_ids)
    failed = [i for i in res.issues if i.code == "scene.generation_failed"]
    assert {i.scene_id for i in failed} == set(quiz_ids)
    assert all("sk-123456" not in i.message for i in failed)
    assert all(res.screenplay.scene_by_id(q).type == "content" for q in quiz_ids)


def test_invalid_scene_reasked_with_problems(job_ctx, providers, sample_ingest, options):
    plan = _plan(job_ctx, sample_ingest, options).plan
    target = next(s for s in plan.all_scenes() if s.type == "content")
    attempts = {"n": 0}

    def responder(prompt, schema):
        if '"type": "content"' in prompt and target.goal in prompt and attempts["n"] == 0:
            attempts["n"] += 1
            return {"title": "T", "beats": [
                {"narration": "Use $V = IR$ here.", "board": {"kind": "bullet", "text": "x"}, "highlight_steps": [5]},
                {"narration": "Fill it.", "fill_previous_blank": True},
            ]}
        return fake_content.board_responder(prompt, schema)

    providers.llm.on("GenBoardScene", responder)
    asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, options))
    retry = [c for c in providers.llm.calls_for("GenBoardScene") if c["attempt"] == 1]
    assert retry, "the invalid scene must be re-asked"
    problems = retry[0]["prompt"].split("## Problems with your previous answer")[1]
    assert "plain spoken text" in problems and "highlight step 5" in problems and "fill_previous_blank" in problems


def test_original_file_goes_to_planner_and_scene_writers(job_ctx, providers, sample_ingest, options):
    """attach_original (scanned / maths-heavy PDFs): Gemini reads the original in planning AND scene writing."""
    from aadhi.storage.assets import Produced, bytes_key

    pdf = b"%PDF-1.4 scanned lecture notes"
    asset = job_ctx.assets.put(bytes_key("source", pdf), "source", Produced(data=pdf, mime="application/pdf"))
    ingest = sample_ingest.model_copy(update={"attach_original": True, "source_storage_key": asset.storage_key,
                                              "source_mime": "application/pdf"})
    providers.llm.name = "gemini"
    plan_res = _plan(job_ctx, ingest, options)
    asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, ingest, options))
    plan_calls, scene_calls = providers.llm.calls_for("GenPlan"), providers.llm.calls_for("GenBoardScene")
    assert plan_calls[0]["files"] == 1 and scene_calls and all(c["files"] == 1 for c in scene_calls)
    assert "The original document is attached" in scene_calls[0]["prompt"]
    # other providers work from the extracted text only, and are not told about an attachment
    providers.llm.name = "openai"
    providers.llm.calls.clear()
    asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, ingest, options))
    scene_calls = providers.llm.calls_for("GenBoardScene")
    assert all(c["files"] == 0 for c in scene_calls) and "original document is attached" not in scene_calls[0]["prompt"]


# ---------------------------------------------------------------------------
# regressions (review round 2)
# ---------------------------------------------------------------------------


def _manual_plan(scenes_per_chapter: list[list[dict]], **meta):
    from aadhi.pipeline.base import LecturePlan

    return LecturePlan.model_validate({
        **meta,
        "chapters": [{"id": f"ch{n}", "title": f"Chapter {n}", "scenes": scenes}
                     for n, scenes in enumerate(scenes_per_chapter, 1)],
    })


def _content(sid: str) -> dict:
    return {"id": sid, "type": "content", "goal": f"Explain idea {sid}.", "key_points": [f"idea {sid}"]}


def test_overlong_simulation_code_falls_back_instead_of_failing(job_ctx, providers, sample_ingest):
    from aadhi.pipeline.gen_models import GenSimulationCode
    from aadhi.pipeline.script import code_problems

    plan = _manual_plan([[_content("intro"), {"id": "sim", "type": "simulation", "goal": "Animate a dot."}]])
    huge = {"title": "Sim", "code": "x = 1\n" * 4000, "beats": [{"narration": "Watch the dot.", "visual_cue": "dot"}]}
    providers.llm.on("GenSimulationCode", lambda p, s: huge)
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))
    assert res.fallback_scene_ids == ["sim"]
    failed = [i for i in res.issues if i.code == "scene.generation_failed"]
    assert [i.scene_id for i in failed] == ["sim"]
    assert res.screenplay.scene_by_id("intro").type == "content"  # the other scene is unaffected
    asked = providers.llm.calls_for("GenSimulationCode")
    assert len(asked) == 3 and "keep it under 20000" in asked[1]["prompt"]
    planned_sim = plan.all_scenes()[1]
    problems = code_problems(planned_sim, GenSimulationCode.model_validate(huge))
    assert problems[0].startswith("code: the animation code is") and "keep it under 20000" in problems[0]


def test_validator_exception_becomes_a_problem(job_ctx, providers, sample_ingest, monkeypatch):
    from aadhi.pipeline import script

    def broken_check(*a, **k):
        raise RuntimeError("checker bug")

    monkeypatch.setattr(script, "pedagogy_problems", broken_check)
    plan = _manual_plan([[_content("intro")]])
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))
    assert not res.fallback_scene_ids  # the second (lenient) pass accepted the scene
    calls = providers.llm.calls_for("GenBoardScene")
    assert len(calls) == 2 and "could not be checked (RuntimeError: checker bug)" in calls[1]["prompt"]


def test_unexpected_scene_error_falls_back_but_job_conditions_propagate(job_ctx, providers, sample_ingest):
    from aadhi.jobs.base import BudgetExceeded

    plan = _manual_plan([[_content("intro"), {"id": "quiz", "type": "quiz_checkpoint", "goal": "Check ideas."}]])
    providers.llm.on("GenQuiz", lambda p, s: KeyError("unexpected bug"))
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))
    assert res.fallback_scene_ids == ["quiz"]
    msg = next(i.message for i in res.issues if i.code == "scene.generation_failed")
    assert "KeyError" in msg
    providers.llm.on("GenQuiz", lambda p, s: BudgetExceeded("job budget"))
    with pytest.raises(BudgetExceeded):
        asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))


def test_long_plan_values_are_clamped_before_assembly(job_ctx, providers, sample_ingest):
    from aadhi.pipeline.base import LecturePlan
    from aadhi.pipeline.plan import TeacherMeta, to_lecture_plan

    plan = _manual_plan([[_content("intro")], [{"id": "card2", "type": "chapter_card", "goal": "Part two."},
                                               _content("more")]],
                        session_title="S" * 400, session_number="N" * 80, subject_name="B" * 300, unit_name="U" * 300)
    data = plan.model_dump(mode="json")
    data["chapters"][1]["title"] = ("A very long chapter title about resistance " * 6)[:239]
    plan = LecturePlan.model_validate(data)  # what POST /plan accepts (LecturePlan limits only)
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))
    sp = res.screenplay
    assert len(sp.chapters[1].title) <= 160 and sp.chapters[1].title.startswith("A very long chapter")
    assert len(sp.session_title) == 300 and len(sp.session_number) == 60
    assert len(sp.subject_name) == 240 and len(sp.unit_name) == 240
    assert any(i.code == "plan.truncated" for i in res.issues) and res.plan is not None
    # the planner's own output is clamped too (GenPlan chapter titles / scene lists are unbounded)
    gen = GenPlan.model_validate({
        "concept_map": [{"key": "ohm", "title": "Ohm"}],
        "chapters": [{"key": "x", "title": "T" * 239, "scenes": [
            {"key": f"s{i}", "type": "content", "goal": "g"} for i in range(70)]}],
    })
    lp = to_lecture_plan(gen, GenerationOptions(), TeacherMeta())
    assert len(lp.chapters[0].title) == 160 and len(lp.chapters[0].scenes) == 60


def test_fit_plan_caps_scene_count(job_ctx, providers, sample_ingest, monkeypatch):
    from aadhi.pipeline import plan_rules
    from aadhi.pipeline.plan_rules import PlanContext, fit_plan, plan_limit_problems, size_problems

    big = _manual_plan([[_content(f"c{k}-s{i}") for i in range(60)] for k in range(4)])  # 240 scenes
    assert any("240 scenes" in p for p in plan_limit_problems(big))
    fitted, notes = fit_plan(big)
    assert sum(len(c.scenes) for c in fitted.chapters) == 200 and len(fitted.chapters) == 4
    assert len(fitted.chapters[3].scenes) == 20 and notes and not plan_limit_problems(fitted)
    small = _manual_plan([[_content("a")]])
    assert fit_plan(small) == (small, [])
    assert size_problems([61, 1]) == ["chapter 1 has 61 scenes; use at most 60 per chapter"]
    gen = GenPlan.model_validate({"concept_map": [{"key": "a", "title": "A"}], "chapters": [
        {"key": "x", "title": "X", "scenes": [{"key": f"s{i}", "type": "content", "goal": "g"} for i in range(61)]}]})
    hard, soft = plan_problems(gen, PlanContext(options=GenerationOptions()))
    assert not hard and any("61 scenes" in s for s in soft)
    # through the scene writers: scenes beyond the limit are never written (no paid call)
    monkeypatch.setattr(plan_rules, "MAX_SCENES", 3)
    plan = _manual_plan([[_content("a"), _content("b")], [_content("c"), _content("d")], [_content("e")]])
    res = asyncio.run(write_scenes_detailed(job_ctx, plan, sample_ingest, GenerationOptions()))
    assert [s.id for s in res.screenplay.scenes] == ["a", "b", "c"]
    assert [c.scene_ids for c in res.screenplay.chapters] == [["a", "b"], ["c"]]
    assert len(providers.llm.calls_for("GenBoardScene")) == 3
    assert next(i for i in res.issues if i.code == "plan.truncated").severity == "warning"


def test_scanned_source_without_attachment_is_refused(job_ctx, providers, sample_ingest, options):
    """A near-textless scan the model cannot read natively fails fast (before any paid call)."""
    from aadhi.pipeline.ingest import IngestError
    from aadhi.storage.assets import Produced, bytes_key

    pdf = b"%PDF-1.4 scanned lecture notes"
    asset = job_ctx.assets.put(bytes_key("source", pdf), "source", Produced(data=pdf, mime="application/pdf"))
    scanned = sample_ingest.model_copy(update={
        "markdown": "<!-- page 1 -->\n[Figure fig-p1-1]\n\n<!-- page 2 -->\n[Figure fig-p2-1]\n",
        "chunks": [], "attach_original": True,
        "source_storage_key": asset.storage_key, "source_mime": "application/pdf", "pages": 2,
    })
    with pytest.raises(IngestError, match="OCR"):
        _plan(job_ctx, scanned, options)
    assert providers.llm.calls_for("GenPlan") == []
    plan = _manual_plan([[_content("intro")]])
    with pytest.raises(IngestError):
        asyncio.run(write_scenes_detailed(job_ctx, plan, scanned, options))
    assert providers.llm.calls_for("GenBoardScene") == []
    providers.llm.name = "gemini"  # a provider that reads the PDF itself is fine
    asyncio.run(write_scenes_detailed(job_ctx, plan, scanned, options))
    call = providers.llm.calls_for("GenBoardScene")[0]
    assert call["files"] == 1 and "The original document is attached" in call["prompt"]


def test_partially_extracted_source_is_reported_honestly(job_ctx, providers, sample_ingest, options):
    from aadhi.pipeline.plan import NOT_ATTACHED_NOTE

    ingest = sample_ingest.model_copy(update={"attach_original": True, "source_mime": "application/pdf",
                                              "warnings": ["2 page(s) look scanned; their text could not be extracted."]})
    plan_res = _plan(job_ctx, ingest, options)
    prompt = providers.llm.calls_for("GenPlan")[0]["prompt"]
    assert NOT_ATTACHED_NOTE in prompt and "given to the model" not in prompt
    assert any(e["level"] == "warning" and "cannot read the original" in e["message"]
               for e in job_ctx.events if e["type"] == "log")
    res = asyncio.run(write_scenes_detailed(job_ctx, plan_res.plan, ingest, options))
    assert [i.code for i in res.issues if i.code.startswith("source.")] == ["source.not_attached"]
    assert not res.source_attached
    assert NOT_ATTACHED_NOTE in providers.llm.calls_for("GenBoardScene")[0]["prompt"]
