"""Chunk accounting: the concept brief cites or sets aside every chunk, and set-aside chunks never reach the
planner, the scene writers, repair or regeneration. Also: scenes keep their narrative role and bridge."""

from __future__ import annotations

import asyncio
import typing
from typing import Any

import pytest

from aadhi.pipeline import fake_content, orchestrator, prompting
from aadhi.pipeline.base import (
    BriefConcept,
    BriefFact,
    ConceptBrief,
    GenerationOptions,
    IngestResult,
    LecturePlan,
    PlannedChapter,
    PlannedScene,
    SkippedChunk,
    SkipReason,
    SourceChunk,
)
from aadhi.pipeline.brief import (
    MAX_SKIPPED_SHARE,
    brief_from_gen,
    brief_problems,
    build_brief,
    build_brief_prompt,
    make_validator,
    teaching_signal,
)
from aadhi.pipeline.canonicalize import canonicalize_scene, planned_from_scene
from aadhi.pipeline.gen_models import GenBoardScene, GenBrief, GenPlan, GenSkipReason
from aadhi.pipeline.plan import TeacherMeta, about_source, build_plan_prompt, plan_context
from aadhi.pipeline.plan_rules import PlanContext, normalize_plan, plan_problems
from aadhi.pipeline.scene_context import LectureContext, positions, relevant_chunks, scene_prompt_sections
from aadhi.pipeline.screenplay_ops import lecture_context, plan_from_screenplay, position_of
from aadhi.pipeline.script import fallback_scene
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import ScriptedLLM, install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)
TITLE_LINE = "Basic Electrical and Electronics Engineering - Unit 1: Electric Circuits - Session 2"
CHUNK_TEXTS = [
    TITLE_LINE,
    "Voltage is the electrical pressure that pushes charge around a circuit. It is measured in volts.",
    "Resistance is the opposition a material offers to the flow of current. It is measured in ohms.",
    "Ohm's law states that V = I × R for a metallic conductor at a constant temperature.",
    "Electrical power is the rate at which energy is converted. P = V × I for any device in the circuit.",
]


def ingest_of(texts: list[str] = CHUNK_TEXTS) -> IngestResult:
    chunks = [SourceChunk(id=f"c{n:04d}", heading_path=["Ohm's law", f"Part {n}"], text=t) for n, t in enumerate(texts, 1)]
    return IngestResult(markdown="\n\n".join(texts), chunks=chunks, source_format="notes", source_mime="text/markdown")


def gen_brief(**overrides: Any) -> dict[str, Any]:
    data = {
        "topic": "Ohm's law",
        "concepts": [
            {"key": "voltage", "name": "Voltage", "must_explain": ["voltage pushes charge"],
             "key_facts": [{"text": "Voltage is measured in volts.", "source_refs": ["c0002"]}], "source_refs": ["c0002"]},
            {"key": "ohms_law", "name": "Ohm's law", "must_explain": ["V = IR"], "prerequisites": ["voltage"],
             "key_facts": [{"text": "V = I R", "source_refs": ["c0004"]}], "source_refs": ["c0003", "c0004"]},
        ],
        "teaching_order": ["voltage", "ohms_law"],
        "source_questions": [{"text": "What is the unit of power? Answer: the watt.", "source_refs": ["c0005"]}],
        "skipped_chunks": [{"chunk_id": "c0001", "reason": "administrative"}],
    }
    data.update(overrides)
    return data


def brief_of(skipped: dict[str, str], cited: list[str]) -> ConceptBrief:
    return ConceptBrief(
        topic="Ohm's law",
        concepts=[BriefConcept(key="ohms_law", name="Ohm's law", source_refs=cited,
                               key_facts=[BriefFact(text="V = I R", source_refs=cited[:1])])],
        teaching_order=["ohms_law"],
        skipped_chunks=[SkippedChunk(chunk_id=cid, reason=reason) for cid, reason in skipped.items()],
    )


# ---------------------------------------------------------------------------
# contract and prompt
# ---------------------------------------------------------------------------


def test_gen_brief_carries_skipped_chunks_and_question_refs():
    schema_mod = pytest.importorskip("aadhi.providers.llm.schema")
    schema_mod.assert_llm_compatible(GenBrief)
    for provider in ("gemini", "openai"):
        props = schema_mod.to_provider_schema(GenBrief, provider)["properties"]
        assert "skipped_chunks" in props and "source_refs" in props["source_questions"]["items"]["properties"]
    assert set(typing.get_args(GenSkipReason)) == set(typing.get_args(SkipReason))
    gen = GenBrief.model_validate({"source_questions": ["What is a volt?", {"text": "Why?", "source_refs": ["c0002"]}]})
    assert [(q.text, q.source_refs) for q in gen.source_questions] == [("What is a volt?", []), ("Why?", ["c0002"])]


def test_brief_prompt_asks_to_account_for_every_chunk():
    raw = (prompting.PROMPTS_DIR / "brief.md").read_text(encoding="utf-8")
    assert raw.startswith("<!-- PROMPT_VERSION: 4 -->")
    text = prompting.load_prompt("brief").text
    assert "# Account for every chunk" in text and "When in doubt, cite it." in text
    for reason in typing.get_args(SkipReason):
        assert f"`{reason}`" in text, reason
    _, user = build_brief_prompt(ingest_of(), OPTIONS)
    task = user.split("## Task", 1)[1]
    assert "Account for all 5 chunk ids" in task and "skipped_chunks" in task


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def test_a_fully_accounted_brief_has_no_problems():
    texts = {c.id: c.text for c in ingest_of().chunks}
    assert brief_problems(GenBrief.model_validate(gen_brief()), texts) == ([], [])


def test_unaccounted_unknown_and_contradictory_chunks_are_fed_back():
    texts = {c.id: c.text for c in ingest_of().chunks}
    data = gen_brief(source_questions=[], skipped_chunks=[
        {"chunk_id": "c0001", "reason": "administrative"}, {"chunk_id": "c0009", "reason": "duplicate"},
        {"chunk_id": "c0003", "reason": "scaffolding"},
    ])
    hard, soft = brief_problems(GenBrief.model_validate(data), texts)
    assert not hard
    first = soft[0]  # accounting comes first, so the problem cap never drops it
    assert "['c0005'] are neither cited nor skipped" in first
    text = " ".join(soft)
    assert "unknown chunk ids ['c0009']" in text and "['c0003'] are both cited and skipped" in text


def test_skipped_teaching_prose_is_questioned_but_a_title_line_is_not():
    texts = {c.id: c.text for c in ingest_of().chunks}
    data = gen_brief(skipped_chunks=[{"chunk_id": "c0001", "reason": "administrative"}])
    data["concepts"][1]["source_refs"] = ["c0003"]
    data["concepts"][1]["key_facts"] = []
    data["skipped_chunks"].append({"chunk_id": "c0004", "reason": "off_topic"})
    _, soft = brief_problems(GenBrief.model_validate(data), texts)
    flagged = [p for p in soft if "is skipped as" in p]
    assert flagged == ["chunk c0004 is skipped as off_topic but it contains a formula: cite it from the concept it "
                       "teaches unless it really holds no teaching content."]
    assert teaching_signal(TITLE_LINE) == ""
    assert teaching_signal("Resistance is the opposition a material offers to the flow of current.") == "a definition"
    assert teaching_signal("Thank you for watching. See you in the next video.") == ""
    # a set of ids (no texts) still checks the accounting
    _, soft = brief_problems(GenBrief.model_validate(gen_brief(skipped_chunks=[])), set(texts))
    assert "['c0001'] are neither cited nor skipped" in soft[0]


def test_accounting_is_asked_once_then_left_to_normalisation():
    validate = make_validator({c.id: c.text for c in ingest_of().chunks})
    gen = GenBrief.model_validate(gen_brief(skipped_chunks=[]))
    assert any("neither cited nor skipped" in p for p in validate(gen))
    assert validate(gen) == []  # later passes only insist on a non-empty brief


def test_brief_from_gen_keeps_valid_skips_and_question_refs():
    ingest = ingest_of()
    data = gen_brief(skipped_chunks=[
        {"chunk_id": "c0001", "reason": "administrative"}, {"chunk_id": "c0001", "reason": "production"},
        {"chunk_id": "c0002", "reason": "duplicate"},  # also cited: never skipped
        {"chunk_id": "c0042", "reason": "off_topic"},  # unknown
    ])
    brief = brief_from_gen(GenBrief.model_validate(data), ingest)
    assert [(s.chunk_id, s.reason) for s in brief.skipped_chunks] == [("c0001", "administrative")]
    assert brief.source_questions == ["What is the unit of power? Answer: the watt."]
    assert brief.source_question_refs == ["c0005"]
    assert brief.cited_chunk_ids() == {"c0002", "c0003", "c0004", "c0005"}
    assert brief.skipped_chunk_ids() == {"c0001"}


def test_skips_that_would_set_aside_most_of_the_source_are_ignored():
    crew = "Bring the spare microphones to the second floor studio before nine and book the room for Friday. " * 6
    ingest = ingest_of([TITLE_LINE, crew, *CHUNK_TEXTS[1:3]])
    data = gen_brief(source_questions=[], skipped_chunks=[
        {"chunk_id": "c0001", "reason": "administrative"}, {"chunk_id": "c0002", "reason": "production"}])
    data["concepts"] = [{"key": "voltage", "name": "Voltage", "must_explain": ["voltage"], "source_refs": ["c0003", "c0004"]}]
    data["teaching_order"] = ["voltage"]
    texts = {c.id: c.text for c in ingest.chunks}
    share = sum(len(texts[c]) for c in ("c0001", "c0002")) / sum(len(t) for t in texts.values())
    assert share > MAX_SKIPPED_SHARE
    assert brief_from_gen(GenBrief.model_validate(data), ingest).skipped_chunks == []
    data["skipped_chunks"] = data["skipped_chunks"][:1]  # a small share stands
    assert brief_from_gen(GenBrief.model_validate(data), ingest).skipped_chunk_ids() == {"c0001"}


TEACHING_LINES = [
    "Voltage is the electric potential difference between two points in a circuit.",
    "Current: the rate of flow of electric charge, measured in amperes (A).",
    "Kirchhoff's current law: the sum of currents entering a node equals the sum leaving it.",
    "Example: a 12 V battery across a 4 Ω resistor drives a current of 3 A.",
    "Series resistors add: the total resistance is R1 + R2 + R3.",
    "Newton's second law says that force equals mass times acceleration.",
    "For instance, a 60 W bulb running for 5 hours uses 0.3 kWh of energy.",
    "A node is a point where two or more circuit elements meet.",
]
PACKAGING_LINES = [
    TITLE_LINE,
    "Thank you for watching. See you in the next video.",
    "SME Name: Dr. Meena Raghavan",
    "Video Duration: 12 minutes",
    "Coming up next: the voltage law and a worked example.",
    "This video is the first in a series of five on network analysis.",
    "Studio booking reference ZQX-77 for the recording crew. Bring the spare microphones to the second floor studio.",
]


@pytest.mark.parametrize("line", TEACHING_LINES)
def test_definitions_laws_and_worked_examples_read_as_teaching(line):
    assert teaching_signal(line, prose=False), line


@pytest.mark.parametrize("line", PACKAGING_LINES)
def test_packaging_lines_do_not_read_as_teaching(line):
    assert teaching_signal(line) == "", line


KCL_TEXTS = [
    TITLE_LINE,
    "Voltage is the electric potential difference between two points in a circuit.",
    "Kirchhoff's current law: the sum of currents entering a node equals the sum leaving it.",
    "Example: a 12 V battery across a 4 Ω resistor drives a current of 3 A.",
    "Resistance is the opposition a material offers to the flow of current. It is measured in ohms.",
    "Ohm's law states that V = I × R for a metallic conductor at a constant temperature.",
    "Electrical power is the rate at which energy is converted. P = V × I for any device in the circuit.",
]


def _kcl_brief(skips: dict[str, str]) -> dict[str, Any]:
    cited = [f"c{n:04d}" for n in range(1, 8) if f"c{n:04d}" not in skips]
    return {"topic": "Circuit laws", "teaching_order": ["circuits"], "concepts": [
        {"key": "circuits", "name": "Circuit laws", "must_explain": ["V = IR"], "source_refs": cited}],
        "skipped_chunks": [{"chunk_id": cid, "reason": reason} for cid, reason in skips.items()]}


def test_wrong_skips_of_teaching_chunks_are_asked_about_and_then_ignored(job_ctx):
    """Repro b3: a definition skipped as a duplicate, a law as off topic and a worked example as scaffolding."""
    ingest = ingest_of(KCL_TEXTS)
    skips = {"c0001": "administrative", "c0002": "duplicate", "c0003": "off_topic", "c0004": "scaffolding"}
    gen = GenBrief.model_validate(_kcl_brief(skips))
    first = make_validator({c.id: c.text for c in ingest.chunks})(gen)
    for cid in ("c0002", "c0003", "c0004"):
        assert any(p.startswith(f"chunk {cid} is skipped as") for p in first), cid
    brief = brief_from_gen(gen, ingest)
    assert [(s.chunk_id, s.reason) for s in brief.skipped_chunks] == [("c0001", "administrative")]
    prompt = build_plan_prompt(ingest, OPTIONS, plan_context(ingest, OPTIONS, job_ctx.settings, brief), TeacherMeta(),
                               brief=brief)
    assert [c["id"] for c in prompting.extract_json(prompt, "Source chunks")] == [f"c{n:04d}" for n in range(2, 8)]
    for needle in ("Kirchhoff", "potential difference", "12 V battery"):
        assert needle in prompt, needle


@pytest.mark.parametrize("first_answer", ["skip", "broken"])
def test_a_repeated_wrong_skip_after_the_reask_is_still_ignored(job_ctx, monkeypatch, first_answer):
    """Repro b and d2: the model repeats the skip on the second pass (or spent the re-ask on a parse error)."""
    llm = ScriptedLLM(use_fake_content=False)
    answers: list[str] = []

    def responder(prompt: str, schema: Any) -> Any:
        answers.append(prompt)
        if first_answer == "broken" and len(answers) == 1:
            return {"concepts": "not a list"}
        return _kcl_brief({"c0001": "administrative", "c0005": "off_topic"})

    llm.on("GenBrief", responder)
    install_providers(monkeypatch, llm)
    brief = asyncio.run(build_brief(job_ctx, ingest_of(KCL_TEXTS), OPTIONS))
    assert len(answers) == (2 if first_answer == "skip" else 3)  # the skip of the definition was asked about
    assert "chunk c0005 is skipped as off_topic" in answers[-1]
    assert brief.skipped_chunk_ids() == {"c0001"}  # ...and the repeated skip is ignored all the same


def test_a_duplicate_skip_stands_only_when_the_chunk_repeats_a_cited_one():
    texts = [TITLE_LINE, CHUNK_TEXTS[1], "Voltage is the electrical pressure that pushes charge around a circuit.",
             CHUNK_TEXTS[2], "Resistance is measured in ohms, and a larger resistance lets less current through."]
    ingest = ingest_of(texts)
    data = {"topic": "Circuits", "teaching_order": ["circuits"], "concepts": [
        {"key": "circuits", "name": "Circuits", "must_explain": ["voltage"], "source_refs": ["c0002", "c0004"]}],
        "skipped_chunks": [{"chunk_id": "c0001", "reason": "administrative"},
                           {"chunk_id": "c0003", "reason": "duplicate"},  # contained in c0002: stands
                           {"chunk_id": "c0005", "reason": "duplicate"}]}  # new content: ignored
    brief = brief_from_gen(GenBrief.model_validate(data), ingest)
    assert [(s.chunk_id, s.reason) for s in brief.skipped_chunks] == [("c0001", "administrative"),
                                                                      ("c0003", "duplicate")]
    # a repeat of a chunk that was itself set aside stands too
    ingest = ingest_of([TITLE_LINE, " ".join(CHUNK_TEXTS[1:]), TITLE_LINE.upper()])
    data = {"topic": "Voltage", "teaching_order": ["voltage"], "concepts": [
        {"key": "voltage", "name": "Voltage", "must_explain": ["voltage"], "source_refs": ["c0002"]}],
        "skipped_chunks": [{"chunk_id": "c0003", "reason": "duplicate"}, {"chunk_id": "c0001", "reason": "administrative"}]}
    assert brief_from_gen(GenBrief.model_validate(data), ingest).skipped_chunk_ids() == {"c0001", "c0003"}


def test_build_brief_reasks_when_chunks_are_unaccounted(job_ctx, monkeypatch):
    llm = ScriptedLLM(use_fake_content=False)
    answers: list[str] = []

    def responder(prompt: str, schema: Any) -> dict[str, Any]:
        answers.append(prompt)
        return gen_brief(skipped_chunks=[]) if len(answers) == 1 else gen_brief()

    llm.on("GenBrief", responder)
    install_providers(monkeypatch, llm)
    brief = asyncio.run(build_brief(job_ctx, ingest_of(), OPTIONS))
    assert len(answers) == 2 and "['c0001'] are neither cited nor skipped" in answers[1]
    assert brief.skipped_chunk_ids() == {"c0001"}


# ---------------------------------------------------------------------------
# gating: plan, plan rules, scene context
# ---------------------------------------------------------------------------


def test_plan_prompt_shows_only_chunks_the_brief_did_not_set_aside(job_ctx):
    ingest = ingest_of()
    brief = brief_of({"c0001": "administrative"}, ["c0002", "c0003"])  # c0004 and c0005: neither cited nor skipped
    pc = plan_context(ingest, OPTIONS, job_ctx.settings, brief)
    prompt = build_plan_prompt(ingest, OPTIONS, pc, TeacherMeta(), brief=brief)
    chunks = prompting.extract_json(prompt, "Source chunks")
    assert [c["id"] for c in chunks] == ["c0002", "c0003", "c0004", "c0005"]  # the safety net keeps c0004 / c0005
    assert TITLE_LINE not in prompt.split("## Request", 1)[1].split("## Concepts to teach", 1)[1]
    about = prompt.split("## About the source", 1)[1].split("##", 1)[0]
    assert "The concept brief set aside 1 section of the source as non-teaching material" in about
    assert "administrative" not in about  # a count, never the reasons or the text
    assert TITLE_LINE not in pc.source_text and pc.skipped_chunk_ids == {"c0001"}
    # without a brief nothing is gated
    plain = build_plan_prompt(ingest, OPTIONS, plan_context(ingest, OPTIONS, job_ctx.settings), TeacherMeta())
    assert [c["id"] for c in prompting.extract_json(plain, "Source chunks")] == [c.id for c in ingest.chunks]
    assert "set aside" not in about_source(ingest, False)
    two = brief_of({"c0001": "administrative", "c0005": "off_topic"}, ["c0002"])
    assert "set aside 2 sections of the source" in about_source(ingest, False, two)


def test_visual_suggestions_next_to_set_aside_chunks_stay_out_of_the_plan_prompt(job_ctx):
    from aadhi.pipeline.base import VisualNote

    ingest = ingest_of().model_copy(update={"visual_notes": [
        VisualNote(id="v0001", near_chunk_id="c0001", text="Logo sting with the department crest."),
        VisualNote(id="v0002", near_chunk_id="c0004", text="Animate V, I and R on a triangle."),
    ]})
    brief = brief_of({"c0001": "administrative"}, ["c0002", "c0003", "c0004", "c0005"])
    prompt = build_plan_prompt(ingest, OPTIONS, plan_context(ingest, OPTIONS, job_ctx.settings, brief), TeacherMeta(),
                               brief=brief)
    assert [n["id"] for n in prompting.extract_json(prompt, "Author's visual suggestions")] == ["v0002"]
    assert "Logo sting" not in prompt


def _plan_with_refs(refs: list[str]) -> GenPlan:
    return GenPlan.model_validate({
        "session_title": "Ohm's law",
        "concept_map": [{"key": "ohms_law", "title": "Ohm's law"}],
        "learning_objectives": [{"key": "obj_ohm", "text": "Apply Ohm's law", "concept_keys": ["ohms_law"]}],
        "chapters": [{"key": "part_1", "title": "Ohm's law", "concept_keys": ["ohms_law"], "scenes": [
            {"key": "cold_open", "type": "title", "goal": "Why circuits obey a rule.", "narrative_role": "hook",
             "source_refs": ["c0002"]},
            {"key": "ohm_intro", "type": "content", "goal": "Explain Ohm's law.", "concept_key": "ohms_law",
             "objective_keys": ["obj_ohm"], "narrative_role": "concept", "bridge_in": "Voltage pushes; now how much?",
             "source_refs": refs},
        ]}],
    })


def test_plan_rules_drop_refs_to_set_aside_chunks_with_a_note():
    pc = PlanContext(options=OPTIONS.model_copy(update={"include_quizzes": False}),
                     chunk_ids={f"c000{i}" for i in range(1, 6)}, skipped_chunk_ids={"c0001"})
    gen = _plan_with_refs(["c0001", "c0004"])
    _, soft = plan_problems(gen, pc)
    assert any("cites ['c0001'], which hold no teaching content" in p for p in soft)
    fixed, report = normalize_plan(gen, pc)
    scene = next(s for ch in fixed.chapters for s in ch.scenes if s.key == "ohm_intro")
    assert scene.source_refs == ["c0004"]
    assert any("ohm_intro: dropped 1 source ref(s) to sections the concept brief set aside" in n for n in report.notes)


def test_scene_context_never_adds_set_aside_chunks():
    chunks = ingest_of().chunks
    skip = frozenset({"c0001", "c0003"})
    # neighbours of a cited chunk: c0001 and c0003 are set aside, so only the cited chunk remains
    assert [c.id for c in relevant_chunks(chunks, ["c0002"], "", skip=skip)] == ["c0002"]
    assert [c.id for c in relevant_chunks(chunks, ["c0002"], "")] == ["c0001", "c0002", "c0003"]
    # keyword fallback: the title line matches "electrical engineering" best, but it is set aside
    query = "Basic Electrical Electronics Engineering Electric Circuits resistance"
    assert "c0001" in [c.id for c in relevant_chunks(chunks, [], query)]
    assert "c0001" not in [c.id for c in relevant_chunks(chunks, [], query, skip=skip)]


def test_scene_prompt_leaves_set_aside_chunks_out_of_nearby_source():
    ingest = ingest_of()
    plan = LecturePlan(session_title="Ohm's law", chapters=[PlannedChapter(id="part-1", title="Ohm's law", scenes=[
        PlannedScene(id="cold-open", type="title", goal="Why circuits obey a rule.", narrative_role="hook"),
        PlannedScene(id="voltage", type="content", goal="Explain voltage.", source_refs=["c0002"], narrative_role="concept",
                     bridge_in="Every circuit starts with a push."),
    ])])
    pos = positions(plan)[1]
    gated = LectureContext(plan=plan, ingest=ingest, options=OPTIONS, skipped_chunk_ids=frozenset({"c0001"}))
    text = "\n".join(scene_prompt_sections(gated, pos))
    assert TITLE_LINE not in text and CHUNK_TEXTS[2] in text  # c0003 is still a nearby chunk
    ungated = "\n".join(scene_prompt_sections(LectureContext(plan=plan, ingest=ingest, options=OPTIONS), pos))
    assert TITLE_LINE in ungated


# ---------------------------------------------------------------------------
# offline brief
# ---------------------------------------------------------------------------


def test_offline_brief_accounts_for_every_chunk():
    texts = [TITLE_LINE, "- State Ohm's law.\n- Apply V = IR.", *CHUNK_TEXTS[1:], "What is the unit of power?"]
    headings = ["Ohm's law", "Learning objectives", "Voltage", "Resistance", "Ohm's law", "Power", "Quick quiz"]
    chunks = [SourceChunk(id=f"c{n:04d}", heading_path=["Ohm's law", h] if n > 1 else [h], text=t)
              for n, (h, t) in enumerate(zip(headings, texts, strict=True), 1)]
    ingest = IngestResult(markdown="\n\n".join(texts), chunks=chunks, source_format="notes")
    _, user = build_brief_prompt(ingest, OPTIONS)
    gen = GenBrief.model_validate(fake_content.brief_responder(user, GenBrief))
    assert [(s.chunk_id, s.reason) for s in gen.skipped_chunks] == [("c0001", "administrative")]
    assert brief_problems(gen, {c.id: c.text for c in chunks}) == ([], [])
    brief = brief_from_gen(gen, ingest)
    assert brief.cited_chunk_ids() == {c.id for c in chunks[1:]}
    assert brief.source_question_refs == ["c0007"]
    assert fake_content.skip_reason({"heading": "Title card", "text": "Ohm's law"}) == "scaffolding"
    assert fake_content.skip_reason({"heading": "Voltage", "text": CHUNK_TEXTS[1]}) is None


# ---------------------------------------------------------------------------
# end to end: generate + regenerate never see a set-aside chunk
# ---------------------------------------------------------------------------

MARKER = "Studio booking reference ZQX-77"
SOURCE = SAMPLE_MARKDOWN.replace("## Resistance", "## Crew notes\n\n" + MARKER + " for the recording crew. "
                                 "Bring the spare microphones to the second floor studio before nine.\n\n## Resistance")


def skipping_brief(prompt: str, schema: Any) -> dict[str, Any]:
    """The offline brief, with the crew-notes chunk set aside as production material (as a real model would)."""
    out = fake_content.brief_responder(prompt, schema)
    target = next(c["id"] for c in prompting.extract_json(prompt, "Source chunks") if MARKER in c["text"])
    for c in out["concepts"]:
        c["source_refs"] = [r for r in c["source_refs"] if r != target]
        for f in c["key_facts"]:
            f["source_refs"] = [r for r in f["source_refs"] if r != target]
    out["concepts"] = [c for c in out["concepts"] if c["source_refs"]]
    keys = {c["key"] for c in out["concepts"]}
    out["teaching_order"] = [k for k in out["teaching_order"] if k in keys]
    for c in out["concepts"]:
        c["prerequisites"] = [p for p in c["prerequisites"] if p in keys]
    out["skipped_chunks"] = [*out["skipped_chunks"], {"chunk_id": target, "reason": "production"}]
    return out


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch)
    providers.llm.on("GenBrief", skipping_brief)
    seeded = seed_project(job_ctx.assets.storage, SOURCE.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded}


def _run(ctx: Any, handler: Any, kind: str, payload: dict[str, Any]) -> Any:
    ctx.kind, ctx.payload = kind, payload
    return asyncio.run(handler(ctx))


def test_set_aside_chunks_never_reach_planner_writers_or_regeneration(world):
    ctx, s, llm = world["ctx"], world["seeded"], world["providers"].llm
    _run(ctx, orchestrator.generate_lecture, "generate_lecture",
         {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1})
    v = get_version(s.version_id)
    assert v.status == "ready"
    stored = ConceptBrief.model_validate(v.generation_meta["brief"])
    assert [x.reason for x in stored.skipped_chunks] == ["production"]
    briefs = llm.calls_for("GenBrief")
    assert briefs and all(MARKER in c["prompt"] for c in briefs)  # the brief decides on it...
    later = [c for c in llm.calls if c["schema"] != "GenBrief"]
    assert later and not [c["schema"] for c in later if MARKER in c["prompt"]]  # ...nobody else ever sees it
    plan_prompt = llm.calls_for("GenPlan")[0]["prompt"]
    assert "The concept brief set aside 1 section of the source" in plan_prompt
    sp = Screenplay.model_validate(v.screenplay)
    assert MARKER not in sp.model_dump_json()
    logs = [e["message"] for e in ctx.events if e["type"] == "log"]
    assert any("set aside 1 section(s) that hold no teaching content" in m for m in logs)
    # regenerate the scene next to the set-aside chunk: its rewrite never sees it either
    target = next(x for x in sp.scenes if x.type == "content")
    n = len(llm.calls)
    _run(ctx, orchestrator.regenerate_scene, "regenerate_scene",
         {"scene_id": target.id, "instructions": "Use a cricket example.", "base_revision": 2})
    rewrites = llm.calls[n:]
    assert rewrites and not [c["schema"] for c in rewrites if MARKER in c["prompt"]]
    assert get_version(s.version_id).status == "ready"


def _translated_version(world: dict[str, Any]) -> int:
    from aadhi.db import session_scope
    from aadhi.models import ProjectVersion

    s, ctx = world["seeded"], world["ctx"]
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1,
                             source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    ctx.version_id = new_id
    _run(ctx, orchestrator.translate_job, "translate",
         {"source_version_id": s.version_id, "target_language": "ta-IN", "translate_board": False})
    return new_id


def _regenerate_next_to_marker(world: dict[str, Any], version_id: int) -> list[dict[str, Any]]:
    ctx, llm = world["ctx"], world["providers"].llm
    v = get_version(version_id)
    sp = Screenplay.model_validate(v.screenplay)
    target = next(x for x in sp.scenes if x.type == "content")
    ctx.version_id = version_id
    n = len(llm.calls)
    _run(ctx, orchestrator.regenerate_scene, "regenerate_scene",
         {"scene_id": target.id, "instructions": "Use a cricket example.", "base_revision": v.revision})
    assert get_version(version_id).status == "ready"
    return llm.calls[n:]


def test_a_translated_version_keeps_the_brief_so_its_rewrites_never_see_set_aside_chunks(world):
    from aadhi.db import session_scope
    from aadhi.models import ProjectVersion

    ctx, s = world["ctx"], world["seeded"]
    _run(ctx, orchestrator.generate_lecture, "generate_lecture",
         {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1})
    original = get_version(s.version_id).generation_meta["brief"]
    new_id = _translated_version(world)
    assert get_version(new_id).generation_meta["brief"] == original  # carried over by the translation
    rewrites = _regenerate_next_to_marker(world, new_id)
    assert {c["schema"] for c in rewrites} >= {"GenBoardScene", "GenCritique"}
    assert not [c["schema"] for c in rewrites if MARKER in c["prompt"]]
    # a translation made before the brief was carried over falls back to its source version's brief
    with session_scope() as db:
        v = db.get(ProjectVersion, new_id)
        meta = dict(v.generation_meta)
        meta.pop("brief")
        v.generation_meta = meta
    rewrites = _regenerate_next_to_marker(world, new_id)
    assert rewrites and not [c["schema"] for c in rewrites if MARKER in c["prompt"]]


# ---------------------------------------------------------------------------
# flow persistence: narrative role and bridge survive repair / regeneration
# ---------------------------------------------------------------------------


def _board(title: str = "Voltage") -> GenBoardScene:
    return GenBoardScene.model_validate({"title": title, "beats": [
        {"narration": "Voltage pushes charge around a circuit.", "board": {"kind": "bullet", "text": "Voltage pushes charge"}},
    ]})


def test_scene_intent_keeps_role_and_bridge_and_planned_from_scene_restores_them():
    planned = PlannedScene(id="voltage", type="content", goal="Explain voltage.", narrative_role="concept",
                           bridge_in="Every circuit starts with a push.", source_refs=["c0002"])
    scene = canonicalize_scene(planned, _board())
    assert (scene.intent.narrative_role, scene.intent.bridge_in) == ("concept", "Every circuit starts with a push.")
    back = planned_from_scene(scene)
    assert (back.narrative_role, back.bridge_in) == ("concept", "Every circuit starts with a push.")
    assert planned_from_scene(scene.model_copy(update={"intent": None})).bridge_in == ""


def test_fallback_scenes_keep_the_flow_too():
    plan = LecturePlan(session_title="Ohm's law", chapters=[PlannedChapter(id="part-1", title="Ohm's law", scenes=[
        PlannedScene(id="card", type="chapter_card", goal="Open part 1.", narrative_role="transition",
                     bridge_in="Let us begin."),
        PlannedScene(id="voltage", type="content", goal="Explain voltage.", key_points=["Voltage pushes charge"],
                     narrative_role="concept", bridge_in="Every circuit starts with a push."),
    ])])
    lc = LectureContext(plan=plan, ingest=ingest_of(), options=OPTIONS)
    for pos in positions(plan):
        scene = fallback_scene(lc, pos)
        assert scene.intent.narrative_role == pos.planned.narrative_role
        assert scene.intent.bridge_in == pos.planned.bridge_in


def test_a_plan_rebuilt_from_the_screenplay_still_bridges():
    first = PlannedScene(id="cold-open", type="title", goal="Why circuits obey a rule.", narrative_role="hook")
    second = PlannedScene(id="voltage", type="content", goal="Explain voltage.", narrative_role="concept",
                          bridge_in="Every circuit starts with a push.")
    scenes = [canonicalize_scene(first, _board("Why circuits obey a rule"), chapter_id="part-1"),
              canonicalize_scene(second, _board(), chapter_id="part-1")]
    sp = Screenplay.model_validate({"session_title": "Ohm's law", "language": "en-IN", "scenes": [
        x.model_dump(mode="json") for x in scenes], "chapters": [{"id": "part-1", "title": "Ohm's law",
                                                                 "scene_ids": ["cold-open", "voltage"]}]})
    rebuilt = plan_from_screenplay(sp)
    assert [(p.narrative_role, p.bridge_in) for p in rebuilt.all_scenes()] == [
        ("hook", ""), ("concept", "Every circuit starts with a push.")]
    lc = lecture_context(sp, ingest_of(), OPTIONS, brief=brief_of({"c0001": "administrative"}, ["c0002"]))
    assert lc.skipped_chunk_ids == frozenset({"c0001"})
    this = prompting.extract_json("\n".join(scene_prompt_sections(lc, position_of(lc, "voltage"))), "This scene")
    assert this["bridge_in"] == "Every circuit starts with a push." and this["narrative_role"] == "concept"
