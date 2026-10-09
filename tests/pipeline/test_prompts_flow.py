"""Concept focus + flow: what the planner, scene writers, critic and repair see (and never see)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.pipeline import prompting
from aadhi.pipeline.base import (
    BriefConcept,
    BriefFact,
    ConceptBrief,
    ExcludedItem,
    GenerationOptions,
    IngestResult,
    SourceMeta,
    VisualNote,
)
from aadhi.pipeline.critic import critique, verify_findings
from aadhi.pipeline.gen_models import GenFinding, GenPlan, GenPlannedScene
from aadhi.pipeline.plan import (
    TeacherMeta,
    build_plan_prompt,
    generate_plan,
    plan_context,
    to_lecture_plan,
    visual_note_payload,
    with_document_meta,
)
from aadhi.pipeline.plan_rules import PlanContext, clean_title, flow_problems, normalize_plan, plan_problems
from aadhi.pipeline.prompting import extract_json
from aadhi.pipeline.repair import rewrite_scene
from aadhi.pipeline.scene_context import LectureContext, positions, scene_prompt_sections, scene_visual_notes
from aadhi.pipeline.script import write_scenes_detailed
from aadhi.schemas.screenplay import BoardScene

SME = "Dr. Ramesh Kumar"
REVIEWER = "Prof. Lakshmi Narayanan"
COURSE_CODE = "EE3251"
SECRETS = (SME, "Ramesh Kumar", REVIEWER, "Lakshmi Narayanan", COURSE_CODE)


@pytest.fixture()
def scoped_ingest(sample_ingest) -> IngestResult:
    """The sample source as source scoping leaves it: header data excluded, visual notes split out."""
    first = sample_ingest.chunks[0].id
    return sample_ingest.model_copy(update={
        "source_format": "sme_script",
        "document_meta": SourceMeta(subject_name="Basic Electrical Engineering", unit_name="DC circuits"),
        "excluded": [
            ExcludedItem(category="person", text=f"SME Name: {SME}", reason="author"),
            ExcludedItem(category="admin", text=f"Course Code: {COURSE_CODE}"),
            ExcludedItem(category="duration", text="Estimated duration: 3 minutes"),
            ExcludedItem(category="timecode", text="[0:10 - 1:15]"),
        ],
        "visual_notes": [
            VisualNote(id="v0001", near_chunk_id=first,
                       text="Show a cell pushing charges around a loop; the current arrow grows as the voltage rises. "
                            "Add a soft gold glow."),
            VisualNote(id="v0002", near_chunk_id=None, text="Title text appears over a dark circuit background."),
        ],
        "warnings": [f"Removed the header line 'SME Name: {SME}'.", "Two pages had no extractable text."],
    })


@pytest.fixture()
def brief(scoped_ingest) -> ConceptBrief:
    ids = [c.id for c in scoped_ingest.chunks]
    return ConceptBrief(
        topic="Ohm's law and electrical power",
        concepts=[
            BriefConcept(key="ohms_law", name="Ohm's law", why_it_matters="Predicts current in every circuit.",
                         must_explain=["V = I R holds at constant temperature"],
                         key_facts=[BriefFact(text="V = I \\times R", source_refs=ids[2:3])],
                         examples=["A phone charger"], prerequisites=["voltage_current"], source_refs=ids[2:3]),
            BriefConcept(key="voltage_current", name="Voltage and current", must_explain=["voltage pushes charge"],
                         source_refs=ids[:1]),
        ],
        teaching_order=["voltage_current", "ohms_law"],
        source_questions=["What is the unit of resistance?"],
        excluded=[ExcludedItem(category="person", text=f"Reviewed by: {REVIEWER}", source="brief")],
        notes=f"The header named {REVIEWER} as reviewer.",
    )


def _no_secrets(text: str) -> None:
    for secret in SECRETS:
        assert secret.lower() not in text.lower(), secret


# ---------------------------------------------------------------------------
# planner prompt
# ---------------------------------------------------------------------------


def test_plan_prompt_has_brief_notes_and_source_note_but_no_excluded_text(job_ctx, templates, scoped_ingest, brief):
    opts = GenerationOptions(target_minutes=6)
    pc = plan_context(scoped_ingest, opts, job_ctx.settings)
    assert pc.visual_note_ids == {"v0001", "v0002"}
    prompt = build_plan_prompt(scoped_ingest, opts, pc, TeacherMeta(), brief=brief)
    system = prompting.system_prompt("style", "plan")
    _no_secrets(system + prompt)
    concepts = extract_json(prompt, "Concepts to teach")
    assert [c["key"] for c in concepts["concepts"]] == ["voltage_current", "ohms_law"]  # teaching order
    assert concepts["concepts"][1]["must_explain"] and concepts["source_questions"]
    assert "excluded" not in concepts and "notes" not in concepts  # the notes named a removed person
    notes = extract_json(prompt, "Author's visual suggestions")
    assert [n["id"] for n in notes] == ["v0001", "v0002"] and notes[0]["near_chunk"] == scoped_ingest.chunks[0].id
    about = prompt.split("## About the source\n", 1)[1].split("\n## ", 1)[0]
    assert "one continuous session" in about and "target_minutes" in about
    assert "Two pages had no extractable text." in about  # other warnings still reach the model
    assert "## Source chunks" in prompt and "dependency order" in prompt
    # section order: context first, evidence last
    assert prompt.index("## About the source") < prompt.index("## Concepts to teach") < prompt.index("## Source chunks")


def test_plan_prompt_without_brief_or_notes(job_ctx, sample_ingest):
    opts = GenerationOptions(target_minutes=6)
    prompt = build_plan_prompt(sample_ingest, opts, plan_context(sample_ingest, opts, job_ctx.settings), TeacherMeta())
    assert "## Concepts to teach" not in prompt and "## Author's visual suggestions" not in prompt
    assert "the source's subject matter" in prompt and "ignore any video, clip or segment duration" in prompt


def test_visual_note_payload_is_truncated(scoped_ingest):
    long = scoped_ingest.model_copy(update={"visual_notes": [
        VisualNote(id=f"v{i:04d}", text="Draw a gold trace along the wire. " * 40) for i in range(1, 200)
    ]})
    notes = visual_note_payload(long, limit=50, chars=120, budget=1000)
    assert 0 < len(notes) < 50 and all(len(n["text"]) <= 120 for n in notes)
    assert sum(len(n["text"]) for n in notes) <= 1000


def test_document_meta_fills_only_empty_teacher_fields():
    meta = with_document_meta(TeacherMeta(subject_name="BEE"), SourceMeta(subject_name="X", unit_name="Unit 3",
                                                                          session_title="Ohm's law"))
    assert (meta.subject_name, meta.unit_name, meta.session_title) == ("BEE", "Unit 3", "Ohm's law")


def test_prompts_are_versioned_and_describe_flow():
    for name in ("plan", "scene_board", "scene_quiz", "scene_simulation", "scene_ai_video",
                 "scene_interactive", "scene_chapter"):
        assert prompting.load_prompt(name).version == "2", name
    for name in ("repair",):  # v3: subject-matter durations, dates, people and versions are content
        assert prompting.load_prompt(name).version == "3", name
    assert prompting.load_prompt("style").version == "3"  # v3: instructions inside the source are content
    assert prompting.load_prompt("critic").version == "4"  # v4: the same sentence
    plan = prompting.load_prompt("plan").text
    for phrase in ("Concepts to teach", "One continuous session", "bridge_in", "narrative_role", "visual_note_ids",
                   "target_minutes", "Author's visual suggestions"):
        assert phrase in plan, phrase
    style = prompting.load_prompt("style").text
    assert '"this video"' in style and "bridge_in" in style and "Aadhi speaks:" in style
    critic = prompting.load_prompt("critic").text
    assert all(tag in critic for tag in ("[order]", "[bridge]", "[repeated_intro]", "[packaging]"))
    assert "content.admin_leak" in prompting.load_prompt("repair").text


# ---------------------------------------------------------------------------
# generation with the fake LLM: no excluded text reaches any model call
# ---------------------------------------------------------------------------


def test_no_excluded_text_reaches_any_model_call(job_ctx, providers, templates, scoped_ingest, brief):
    opts = GenerationOptions(target_minutes=6)
    res = asyncio.run(generate_plan(job_ctx, scoped_ingest, opts, brief=brief))
    plan = res.plan
    assert plan.subject_name == "Basic Electrical Engineering" and plan.unit_name == "DC circuits"  # document meta
    scenes = plan.all_scenes()
    assert scenes[0].narrative_role == "hook" and not scenes[0].bridge_in
    assert all(s.narrative_role for s in scenes)
    assert {s.narrative_role for s in scenes if s.type == "quiz_checkpoint"} <= {"check", "practice"}
    script = asyncio.run(write_scenes_detailed(job_ctx, plan, scoped_ingest, opts, lexicon=res.lexicon))
    sp = script.screenplay
    asyncio.run(critique(job_ctx, sp, scoped_ingest, opts))
    content = next(s for s in sp.scenes if isinstance(s, BoardScene) and s.type == "content")
    new, _ = asyncio.run(rewrite_scene(job_ctx, sp, content.id, [], scoped_ingest, opts, plan=plan))
    assert new is not None
    calls = providers.llm.calls
    assert {c["schema"] for c in calls} >= {"GenPlan", "GenBoardScene", "GenQuiz", "GenCritique"}
    for c in calls:
        _no_secrets(c["system"] + c["prompt"])
    board = [c for c in calls if c["schema"].startswith("GenBoardScene")]
    this = extract_json(board[1]["prompt"], "This scene")
    assert "narrative_role" in this and "bridge_in" in this
    neighbours = extract_json(board[1]["prompt"], "Neighbouring scenes")
    assert neighbours["previous"]["goal"] and "bridge_in" in neighbours["previous"]
    assert any("## Author's visual suggestions" in c["prompt"] and "v0001" in c["prompt"] for c in board)
    assert all("narration as raw material" in c["prompt"] for c in board)  # the source is a video script


# ---------------------------------------------------------------------------
# plan fields, rules and normalisation
# ---------------------------------------------------------------------------


def _gen(scenes: list[dict[str, Any]], **kw: Any) -> GenPlan:
    return GenPlan.model_validate({
        "session_title": kw.pop("session_title", "Ohm's law"),
        "concept_map": [{"key": "ohm", "title": "Ohm's law"}, {"key": "power", "title": "Power", "depends_on": ["ohm"]}],
        "learning_objectives": [{"key": "o1", "text": "Apply Ohm's law", "concept_keys": ["ohm"]}],
        "chapters": [{"key": "a", "title": kw.pop("chapter_title", "Ohm's law"), "scenes": scenes}],
        **kw,
    })


def _pc(quizzes: bool = False, **kw: Any) -> PlanContext:
    return PlanContext(options=GenerationOptions(include_quizzes=quizzes, target_minutes=3), **kw)


def test_flow_fields_are_planned_and_mapped(job_ctx, providers, scoped_ingest):
    ids = [c.id for c in scoped_ingest.chunks]
    plan_json = _gen([
        {"key": "hook", "type": "title", "narrative_role": "hook", "goal": "Why chargers get warm.", "est_seconds": 40},
        {"key": "teach", "type": "content", "narrative_role": "concept", "concept_key": "ohm", "objective_keys": ["o1"],
         "bridge_in": "You have seen chargers warm up; now we find the rule behind the current.", "goal": "Teach V = IR.",
         "source_refs": ids[:1], "visual_note_ids": ["v0001", "v9999", "v0001"], "est_seconds": 80},
        {"key": "use", "type": "example", "narrative_role": "example", "concept_key": "power", "objective_keys": ["o1"],
         "bridge_in": "With V = IR in hand, compute the power a device draws.", "goal": "Worked power example.",
         "est_seconds": 60},
    ]).model_dump(mode="json")
    providers.llm.on("GenPlan", lambda prompt, schema: plan_json)
    opts = GenerationOptions(target_minutes=3, include_quizzes=False)
    res = asyncio.run(generate_plan(job_ctx, scoped_ingest, opts))
    by_id = {s.id: s for s in res.plan.all_scenes()}
    assert by_id["teach"].visual_note_ids == ["v0001"]  # unknown and duplicate ids dropped
    assert by_id["teach"].bridge_in.startswith("You have seen chargers")
    assert [by_id[k].narrative_role for k in ("hook", "teach", "use")] == ["hook", "concept", "example"]
    first_prompt = providers.llm.calls_for("GenPlan")[0]["prompt"]
    assert "## Author's visual suggestions" in first_prompt
    asked_again = providers.llm.calls_for("GenPlan")[1]["prompt"]
    assert "unknown visual_note_ids ['v9999']" in asked_again  # soft problem reported once


def test_flow_problems_and_normalisation():
    gen = _gen([
        {"key": "hook", "type": "title", "narrative_role": "concept", "goal": "Hook."},
        {"key": "clip2_title", "type": "title", "narrative_role": "hook", "goal": "Title card for clip 2.",
         "visual_note_ids": ["v0007"]},
        {"key": "teach", "type": "content", "concept_key": "ohm", "goal": "Teach Ohm's law [0:10 - 1:15].",
         "bridge_in": "  Now   the rule. "},
        {"key": "check", "type": "quiz_checkpoint", "narrative_role": "concept", "goal": "Check.",
         "bridge_in": "Let's check."},
    ], session_title="Clip 1 Script - Ohm's law", chapter_title="Segment 2 - Ohm's law [0:10 - 1:15]")
    pc = _pc(quizzes=True, visual_note_ids={"v0001"})
    soft = flow_problems(gen, pc)
    text = "\n".join(soft)
    assert "2 title scenes" in text and "'hook'" in text and "only the first scene is the hook" in text
    assert "is a quiz" in text and "unknown visual_note_ids ['v0007']" in text
    assert "have no bridge_in" in text and "'clip2_title'" in text  # flow fields in use -> complete them
    assert "packaging" in text and "clip 2" in text and "0:10 - 1:15" in text
    assert set(soft) <= set(plan_problems(gen, pc)[1])
    fixed, rep = normalize_plan(gen, pc)
    scenes = [s for ch in fixed.chapters for s in ch.scenes]
    assert [s.type for s in scenes] == ["title", "content", "content", "quiz_checkpoint"]
    assert [s.narrative_role for s in scenes] == ["hook", "context", "concept", "check"]
    assert scenes[1].visual_note_ids == [] and scenes[2].bridge_in == "Now the rule."
    assert fixed.session_title == "Ohm's law" and fixed.chapters[0].title == "Ohm's law"
    assert any("second title scene" in n for n in rep.notes)
    plan = to_lecture_plan(fixed, GenerationOptions(), TeacherMeta())
    assert plan.all_scenes()[0].narrative_role == "hook"


def test_plan_without_flow_fields_is_not_nagged():
    gen = _gen([
        {"key": "hook", "type": "title", "goal": "Hook.", "est_seconds": 60},
        {"key": "teach", "type": "content", "concept_key": "ohm", "objective_keys": ["o1"], "goal": "Teach.",
         "est_seconds": 120},
    ])
    assert flow_problems(gen, _pc()) == []
    fixed, _ = normalize_plan(gen, _pc())
    assert [s.narrative_role for s in fixed.chapters[0].scenes] == ["hook", "concept"]


def test_inserted_scenes_get_roles_and_bridges():
    gen = GenPlan.model_validate({
        "concept_map": [{"key": k, "title": k.title()} for k in ("a", "b", "c")],
        "chapters": [
            {"key": "one", "title": "One", "scenes": [
                {"key": "s1", "type": "content", "concept_key": "a", "goal": "g"},
                {"key": "s2", "type": "content", "concept_key": "b", "goal": "g"},
                {"key": "s3", "type": "content", "concept_key": "c", "goal": "g"}]},
            {"key": "two", "title": "Two", "scenes": [{"key": "s4", "type": "summary", "goal": "g"}]},
        ],
    })
    pc = PlanContext(options=GenerationOptions(quiz_every_n_concepts=2, previous_session_summary="We met charge."))
    fixed, _ = normalize_plan(gen, pc)
    scenes = [s for ch in fixed.chapters for s in ch.scenes]
    roles = {s.type: s.narrative_role for s in scenes}
    assert scenes[0].key == "s1" and scenes[0].narrative_role == "hook" and not scenes[0].bridge_in
    assert roles["recap"] == "context" and roles["chapter_card"] == "transition" and roles["quiz_checkpoint"] == "check"
    assert all(s.bridge_in for s in scenes if s.type in ("recap", "chapter_card", "quiz_checkpoint"))


def test_clean_title():
    assert clean_title("CLIP 3 SCRIPT - BOOLEAN POSTULATES") == "BOOLEAN POSTULATES"
    assert clean_title("Segment 2 - Hook [0:10 - 1:15]") == "Hook"
    assert clean_title("Ohm's law") == "Ohm's law" and clean_title("Part 2") == "Part 2"


def test_gen_plan_schema_is_llm_compatible_with_flow_fields():
    schema_mod = pytest.importorskip("aadhi.providers.llm.schema")
    schema_mod.assert_llm_compatible(GenPlan)
    for provider in ("gemini", "openai"):
        schema_mod.to_provider_schema(GenPlan, provider)
    fields = GenPlannedScene.model_fields
    assert {"narrative_role", "bridge_in", "visual_note_ids"} <= set(fields)
    assert all(fields[f].description for f in ("narrative_role", "bridge_in", "visual_note_ids"))


# ---------------------------------------------------------------------------
# scene context and critic
# ---------------------------------------------------------------------------


def test_scene_visual_notes_prefer_plan_ids(job_ctx, providers, templates, scoped_ingest):
    opts = GenerationOptions(target_minutes=6)
    plan = asyncio.run(generate_plan(job_ctx, scoped_ingest, opts)).plan
    lc = LectureContext(plan=plan, ingest=scoped_ingest, options=opts)
    pos = next(p for p in positions(plan) if p.planned.type == "content")
    picked = pos.planned.model_copy(update={"visual_note_ids": ["v0002"]})
    assert [v.id for v in scene_visual_notes(scoped_ingest.visual_notes, picked, [])] == ["v0002"]
    quiz = next(p for p in positions(plan) if p.planned.type == "quiz_checkpoint")
    assert scene_visual_notes(scoped_ingest.visual_notes, quiz.planned.model_copy(update={"visual_note_ids": ["v0002"]}),
                              []) == []
    sections = "\n".join(scene_prompt_sections(lc, pos))
    assert "## Outline" in sections and f"{pos.planned.id} (content, " in sections


def test_scene_source_separates_its_own_chunks_from_its_neighbours():
    from aadhi.pipeline.base import LecturePlan, PlannedChapter, PlannedScene, SourceChunk

    texts = [("Hook", "Every phone makes millions of logical decisions each second."),
             ("Boolean variables", "A Boolean variable takes only the values 0 and 1."),
             ("Boolean operations", "The basic Boolean operations are AND, OR and NOT."),
             ("Postulates", "Closure: combining two Boolean values gives a Boolean value.")]
    chunks = [SourceChunk(id=f"c000{i}", heading_path=[h], text=t) for i, (h, t) in enumerate(texts, 1)]
    ingest = IngestResult(markdown="x", chunks=chunks)
    plan = LecturePlan(chapters=[PlannedChapter(id="ch1", title="Basics", scenes=[
        PlannedScene(id="hook", type="title", goal="Hook the learner", source_refs=["c0001"]),
        PlannedScene(id="variables", type="content", goal="Explain Boolean variables", source_refs=["c0002"]),
        PlannedScene(id="operations", type="content", goal="Explain the operations", source_refs=["c0003"]),
        PlannedScene(id="practice", type="content", goal="Practise the basic Boolean operations AND, OR and NOT"),
        PlannedScene(id="check", type="quiz_checkpoint", goal="Check variables", source_refs=["c0002", "c0003"]),
    ])])
    lc = LectureContext(plan=plan, ingest=ingest, options=GenerationOptions())
    prompt = "\n".join(scene_prompt_sections(lc, positions(plan)[1]))
    relevant = extract_json(prompt, "Relevant source")
    own = relevant["chunks"]
    assert [c["id"] for c in own] == ["c0002"] and "taught_in" not in own[0]
    assert "cover only this scene's part" in relevant["note"]
    nearby = extract_json(prompt, "Nearby source (context only)")
    assert [c["id"] for c in nearby["chunks"]] == ["c0001", "c0003"]
    # a cold open (title) that cites a chunk hooks with it but does not teach it
    assert [c.get("taught_in") for c in nearby["chunks"]] == [None, ["operations"]]
    assert "do not teach it again" in nearby["note"]
    # a scene that cites nothing owns its keyword matches, each labelled with the scenes that teach it
    prompt = "\n".join(scene_prompt_sections(lc, positions(plan)[3]))
    own = {c["id"]: c for c in extract_json(prompt, "Relevant source")["chunks"]}
    assert own["c0003"]["taught_in"] == ["operations"] and own["c0002"]["taught_in"] == ["variables"]
    assert extract_json(prompt, "Nearby source (context only)") is None


def _small_lecture() -> tuple[LectureContext, Any]:
    from aadhi.pipeline.base import LecturePlan, PlannedChapter, PlannedScene, SourceChunk
    from aadhi.schemas.screenplay import SourceFigure

    texts = [("Objectives", "State Ohm's law and apply it."), ("Voltage", "Voltage is the push that drives charge."),
             ("Ohm's law", "Ohm's law: V = I R for a metallic conductor."), ("Power", "Power is P = V I in any device."),
             ("Quiz", "Q1: What is the unit of voltage? Answer: the volt. Q2: What is P = V I? Answer: power.")]
    chunks = [SourceChunk(id=f"c000{i}", heading_path=[h], text=t) for i, (h, t) in enumerate(texts, 1)]
    ingest = IngestResult(markdown="x", chunks=chunks)
    goal = ("Hook the learner with why Boolean Postulates, Laws, and Minimization of Boolean Expressions matters, then "
            "introduce the session and its objectives in one calm sentence that sets up the first part")
    plan = LecturePlan(chapters=[
        PlannedChapter(id="part-1", title="Voltage", scenes=[
            PlannedScene(id="cold-open", type="title", goal=goal, source_refs=["c0001", "c0002"]),
            PlannedScene(id="voltage", type="content", goal="Explain voltage", source_refs=["c0002"]),
            PlannedScene(id="check", type="quiz_checkpoint", goal="Check voltage units", source_refs=["c0005"]),
            PlannedScene(id="check-2", type="quiz_checkpoint", goal="Check what voltage drives"),
        ]),
        PlannedChapter(id="part-2", title="Ohm's law", scenes=[
            PlannedScene(id="part-2-card", type="chapter_card", goal="Open part 2", key_points=["Ohm's law"]),
            PlannedScene(id="ohm", type="content", goal="Explain Ohm's law and power", source_refs=["c0003"]),
            PlannedScene(id="takeaway", type="key_takeaway", goal="Restate Ohm's law", source_refs=["c0003"]),
        ]),
    ])
    figures = [SourceFigure(id=f"fig-{i}", caption=f"Figure {i}") for i in range(1, 4)]
    return LectureContext(plan=plan, ingest=ingest, options=GenerationOptions(), figures=figures), plan


def test_chapter_cards_and_quizzes_get_only_the_source_they_need():
    lc, plan = _small_lecture()
    pos = {p.planned.id: p for p in positions(plan)}
    card = "\n".join(scene_prompt_sections(lc, pos["part-2-card"]))
    for name in ("## Relevant source", "## Nearby source", "## Figures", "## Author's visual suggestions"):
        assert name not in card, name
    assert "## Outline" in card and "## This scene" in card
    quiz = "\n".join(scene_prompt_sections(lc, pos["check"]))
    assert [c["id"] for c in extract_json(quiz, "Relevant source")["chunks"]] == ["c0005"]
    assert "## Nearby source" not in quiz and "## Figures" not in quiz
    assert "Ask only about what the scenes up to this one have taught" in quiz
    # a quiz that cites nothing matches keywords among what was taught before it, never later sections
    loose = extract_json("\n".join(scene_prompt_sections(lc, pos["check-2"])), "Relevant source")
    assert {c["id"] for c in loose["chunks"]} <= {"c0001", "c0002", "c0005"}
    content = "\n".join(scene_prompt_sections(lc, pos["voltage"]))
    assert "## Figures" in content and "## Nearby source" in content


def test_a_cold_open_or_takeaway_does_not_count_as_teaching_a_chunk():
    lc, _ = _small_lecture()
    assert lc.taught_in("c0002") == ["voltage"]  # not the cold open that also cites it
    assert lc.taught_in("c0003") == ["ohm"]  # not the key takeaway


def test_the_outline_cuts_goals_at_a_word_boundary():
    lc, _ = _small_lecture()
    first = lc.outline()[0]
    assert first.startswith("[1] cold-open (title): Hook the learner") and first.endswith("…")
    assert "obje…" not in first and not first.endswith(" …") and len(first) < 200


def test_the_critic_sees_board_reveals_without_empty_fields(job_ctx, providers, templates, sample_ingest):
    from aadhi.pipeline.critic import build_critic_prompt, chapter_groups

    opts = GenerationOptions(target_minutes=6)
    res = asyncio.run(generate_plan(job_ctx, sample_ingest, opts))
    sp = asyncio.run(write_scenes_detailed(job_ctx, res.plan, sample_ingest, opts)).screenplay
    prompts = [build_critic_prompt(sp, title, scenes, sample_ingest)[0] for title, scenes in chapter_groups(sp)]
    reveals = [b["reveals"] for p in prompts for s in extract_json(p, "Scenes") for b in s["beats"] if "reveals" in b]
    assert reveals and all(None not in r.values() for r in reveals)
    assert all("null" not in p.split("## Scenes", 1)[1].split("## ", 1)[0] for p in prompts)


def test_flow_findings_get_flow_codes():
    from aadhi.schemas.screenplay import Screenplay

    sp = Screenplay.model_validate({"scenes": [{"id": "a", "type": "content", "beats": [
        {"id": "a-b1", "narration": "In this video we jump straight to the commutative law."}]}]})
    scene = sp.scenes[0]
    findings = [
        GenFinding(scene_id="a", category="flow", message="[bridge] Starts abruptly.", claim="jump straight"),
        GenFinding(scene_id="a", category="flow", message="packaging: mentions the video.", claim="In this video"),
        GenFinding(scene_id="a", category="flow", message="[repeated_intro] Re-introduces the session."),
        GenFinding(scene_id="a", category="flow", message="[order] Uses AND before teaching it."),
        GenFinding(scene_id="a", category="flow", message="Feels rushed."),
    ]
    issues = verify_findings(findings, {"a": scene}, {})
    assert [i.code for i in issues] == ["flow.bridge", "flow.packaging", "flow.repeated_intro", "flow.order",
                                        "flow.issue"]
    assert all(i.fixable and i.source == "critic" for i in issues)
    assert issues[0].message.startswith("Starts abruptly.")


def test_critic_prompt_has_outline_and_previous_scene(job_ctx, providers, templates, sample_ingest):
    from aadhi.pipeline.critic import build_critic_prompt, chapter_groups

    opts = GenerationOptions(target_minutes=6)
    res = asyncio.run(generate_plan(job_ctx, sample_ingest, opts))
    sp = asyncio.run(write_scenes_detailed(job_ctx, res.plan, sample_ingest, opts)).screenplay
    groups = chapter_groups(sp)
    assert len(groups) >= 2
    title, scenes = groups[1]
    prompt, _ = build_critic_prompt(sp, title, scenes, sample_ingest)
    assert "## Lecture outline" in prompt and f"{scenes[0].id} ({scenes[0].type})" in prompt
    before = extract_json(prompt, "Scene before this chapter")
    assert before["id"] == groups[0][1][-1].id and before["last_beat"]
    first_prompt, _ = build_critic_prompt(sp, groups[0][0], groups[0][1], sample_ingest)
    assert "## Scene before this chapter" not in first_prompt
