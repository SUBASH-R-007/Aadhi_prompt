"""Concept brief: prompt contents, validation re-ask, normalisation, cache and orchestrator wiring."""

from __future__ import annotations

import asyncio
import json
import re
import typing
from typing import Any

import pytest

from aadhi.jobs.base import AwaitingReview
from aadhi.pipeline import brief as brief_mod
from aadhi.pipeline import orchestrator, plan_state, prompting
from aadhi.pipeline.base import ConceptBrief, ExcludedCategory, ExcludedItem, GenerationOptions, IngestResult
from aadhi.pipeline.brief import (
    BriefUnavailable,
    brief_from_gen,
    brief_problems,
    build_brief,
    build_brief_prompt,
    fake_brief_responder,
    person_values,
    teaching_order,
)
from aadhi.pipeline.chunking import chunk_markdown
from aadhi.pipeline.docx_extract import docx_to_markdown
from aadhi.pipeline.gen_models import GenBrief, GenExcludedCategory
from aadhi.pipeline.source_scope import resolve_visual_notes, scope_source
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import ScriptedLLM, install_providers
from tests.pipeline.fixtures.sme_sources import header_table_docx, sample_template_bytes
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def scoped_ingest(markdown: str) -> IngestResult:
    """IngestResult the way ingest builds it (scope -> chunks -> visual notes), without storage."""
    scope = scope_source(markdown)
    chunks = chunk_markdown(scope.markdown)
    return IngestResult(
        markdown=scope.markdown, chunks=chunks, document_meta=scope.document_meta,
        visual_notes=resolve_visual_notes(scope.visual_notes_raw, chunks), excluded=scope.excluded,
        source_format=scope.source_format, source_mime="text/markdown",
    )


@pytest.fixture(scope="module")
def template_ingest() -> IngestResult:
    return scoped_ingest(docx_to_markdown(sample_template_bytes()).markdown)


def good_brief(ingest: IngestResult, **overrides: Any) -> dict[str, Any]:
    ids = [c.id for c in ingest.chunks]
    data = {
        "topic": "Ohm's law",
        "concepts": [
            {"key": "voltage_current", "name": "Voltage and current", "why_it_matters": "Everything else builds on it.",
             "must_explain": ["voltage pushes charge", "current is the flow rate"],
             "key_facts": [{"text": "Current is measured in amperes.", "source_refs": [ids[0]]}],
             "examples": [], "prerequisites": [], "source_refs": ids[:-1]},  # every chunk is accounted for
            {"key": "ohms_law", "name": "Ohm's law", "must_explain": ["V is proportional to I"],
             "key_facts": [{"text": "V = I R", "source_refs": [ids[-1]]}], "examples": ["A 12 V cell and a 4 ohm resistor"],
             "prerequisites": ["voltage_current"], "source_refs": [ids[-1]]},
        ],
        "teaching_order": ["voltage_current", "ohms_law"],
        "source_questions": [],
        "excluded": [],
        "notes": "",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------


def test_brief_prompt_holds_only_teaching_content(template_ingest):
    system, user = build_brief_prompt(template_ingest, OPTIONS)
    assert system == prompting.load_prompt("brief").text
    request = prompting.extract_json(user, "Request")
    assert request == {"audience": OPTIONS.audience, "depth": "standard", "narration_language": "English (India) (en-IN)",
                       "source_format": "sme_script", "visual_suggestions_set_aside": len(template_ingest.visual_notes)}
    chunks = prompting.extract_json(user, "Source chunks")
    assert [c["id"] for c in chunks] == [c.id for c in template_ingest.chunks]
    assert "video script written by a subject-matter expert" in user
    for leaked in ("Estimated duration", "SUBJECT NAME", "UNIT NAME", "TITLE CARD", "COMING UP NEXT", "Aadhi speaks",
                   "ANIMATION", "two switches", "golden glow", "0:45", "Digital System Design"):
        assert leaked not in user, leaked
    for kept in ("DE MORGAN'S THEOREMS", "Who developed Boolean Algebra?", "Y = A + AB"):
        assert kept in user
    assert build_brief_prompt(template_ingest, OPTIONS) == (system, user)  # pure


def test_header_values_never_reach_the_brief_prompt():
    ingest = scoped_ingest(docx_to_markdown(header_table_docx()).markdown)
    assert any("Kavitha" in e.text for e in ingest.excluded)  # recorded for the teacher...
    system, user = build_brief_prompt(ingest, OPTIONS)
    for value in ("Kavitha", "Anand", "EE3301", "12/03/2024", "6 minutes", "Electrical and Electronics Engineering"):
        assert value not in system + user  # ...but never sent to the model, not even as "things to avoid"


def test_brief_prompt_file_is_versioned_and_calm():
    raw = (prompting.PROMPTS_DIR / "brief.md").read_text(encoding="utf-8")
    assert raw.startswith("<!-- PROMPT_VERSION: 4 -->")  # v4: instructions inside the source are content
    text = prompting.load_prompt("brief").text
    assert not set(re.findall(r"\b[A-Z]{4,}\b", text)) - {"JSON", "ASCII"}
    assert not re.search(r"(?i)\b(fatal|crash|or else|you will be penalized)\b", text)
    for phrase in ("teaching_order", "source_questions", "skipped_chunks", "excluded", "LaTeX", "one continuous lecture"):
        assert phrase in text


def test_gen_brief_is_llm_compatible():
    schema_mod = pytest.importorskip("aadhi.providers.llm.schema")
    schema_mod.assert_llm_compatible(GenBrief)
    for provider in ("gemini", "openai"):
        schema_mod.to_provider_schema(GenBrief, provider)
    assert set(typing.get_args(GenExcludedCategory)) == set(typing.get_args(ExcludedCategory))


# ---------------------------------------------------------------------------
# validation and normalisation
# ---------------------------------------------------------------------------


def test_brief_problems(sample_ingest):
    ids = {c.id for c in sample_ingest.chunks}
    assert brief_problems(GenBrief.model_validate(good_brief(sample_ingest)), ids) == ([], [])
    hard, _ = brief_problems(GenBrief(), ids)
    assert hard and "empty" in hard[0]
    bad = good_brief(sample_ingest)
    bad["concepts"][0]["source_refs"] = ["c9999"]
    bad["concepts"][0]["prerequisites"] = ["ohms_law", "magic"]
    bad["teaching_order"] = ["ohms_law"]
    hard, soft = brief_problems(GenBrief.model_validate(bad), ids)
    text = " ".join(soft)
    assert not hard
    for needle in ("c9999", "magic", "cycle", "missing ['voltage_current']"):
        assert needle in text, needle
    # the chunks only the first concept cited (its key fact still cites the first one) are now unaccounted:
    # asked first, before the other problems
    assert soft[0].startswith(f"chunks {sorted(ids)[1:-1][:15]} are neither cited nor skipped")


def test_teaching_order_respects_prerequisites_and_breaks_cycles():
    order, kept = teaching_order(["c", "b", "a"], {"a": [], "b": ["a"], "c": ["b"]}, ["c", "b", "a"])
    assert order == ["a", "b", "c"] and kept["c"] == ["b"]
    order, kept = teaching_order(["x", "y"], {"x": ["y"], "y": ["x"]}, ["x", "y"])
    assert order == ["x", "y"] and kept == {"x": [], "y": ["x"]}


def test_brief_from_gen_normalises(sample_ingest):
    ingest = sample_ingest.model_copy(update={"excluded": [
        ExcludedItem(category="person", text="SME Name: Dr. Kavitha Raman", reason="x"),
        ExcludedItem(category="person", text="Department: Electrical Engineering", reason="x"),
    ]})
    data = good_brief(ingest, notes="Prepared with Kavitha Raman's notes.", teaching_order=["ohms_law"])
    data["concepts"].append({"key": "Ohm's Law", "name": "Ohm's law again", "must_explain": ["repeat"],
                             "prerequisites": ["ohms_law", "ohms_law_2", "unknown"], "source_refs": ["c9999"]})
    data["concepts"][0]["prerequisites"] = ["ohms_law"]  # a cycle with ohms_law -> voltage_current
    data["excluded"] = [{"category": "person", "text": "the reviewer Kavitha Raman", "reason": "header"}]
    out = brief_from_gen(GenBrief.model_validate(data), ingest)
    keys = [c.key for c in out.concepts]
    assert keys == ["voltage_current", "ohms_law", "ohms_law_2"]
    assert sorted(out.teaching_order) == sorted(keys)
    pos = {k: i for i, k in enumerate(out.teaching_order)}
    for c in out.concepts:
        assert all(pos[d] < pos[c.key] for d in c.prerequisites), c.key
        assert all(r in {x.id for x in ingest.chunks} for r in c.source_refs)
    assert out.concepts[2].prerequisites == ["ohms_law"] and out.concepts[2].source_refs == []
    assert "Kavitha" not in out.model_dump_json()  # author names are scrubbed
    assert out.excluded[0].source == "brief" and out.excluded[0].category == "person"
    assert person_values(ingest.excluded) == ["Kavitha Raman"]  # departments are not names


# ---------------------------------------------------------------------------
# build_brief: re-ask loop, cache, model, failure modes
# ---------------------------------------------------------------------------


def test_build_brief_reasks_on_invalid_refs_then_caches(job_ctx, monkeypatch, sample_ingest):
    llm = ScriptedLLM(use_fake_content=False)
    answers = []

    def responder(prompt: str, schema: Any) -> dict[str, Any]:
        bad = good_brief(sample_ingest)
        bad["concepts"][1]["source_refs"] = ["c0042"]
        answers.append(prompt)
        return bad if len(answers) == 1 else good_brief(sample_ingest)

    llm.on("GenBrief", responder)
    install_providers(monkeypatch, llm)
    out = asyncio.run(build_brief(job_ctx, sample_ingest, OPTIONS))
    assert isinstance(out, ConceptBrief) and [c.key for c in out.concepts] == ["voltage_current", "ohms_law"]
    calls = llm.calls_for("GenBrief")
    assert [c["attempt"] for c in calls] == [0, 1]
    assert "c0042" in calls[1]["prompt"] and "Problems with your previous answer" in calls[1]["prompt"]
    assert calls[0]["model"] == job_ctx.settings.llm_model_fast
    assert job_ctx.usages  # the call is billed to the job
    # the same source + options again: served from the cache, no new model call
    again = asyncio.run(build_brief(job_ctx, sample_ingest, OPTIONS))
    assert again == out and len(llm.calls_for("GenBrief")) == 2
    # a different depth is a different brief
    asyncio.run(build_brief(job_ctx, sample_ingest, OPTIONS.model_copy(update={"depth": "deep"})))
    assert len(llm.calls_for("GenBrief")) == 3
    key = brief_mod.brief_cache_key(*build_brief_prompt(sample_ingest, OPTIONS), job_ctx.settings.llm_model_fast)
    assert job_ctx.assets.get(key).kind == "intermediate"


def test_build_brief_uses_the_admin_plan_model_override(job_ctx, monkeypatch, sample_ingest):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenBrief", lambda p, s: good_brief(sample_ingest))
    install_providers(monkeypatch, llm)
    asyncio.run(build_brief(job_ctx, sample_ingest, OPTIONS.model_copy(update={"llm_model_plan": "big-model"})))
    assert llm.calls_for("GenBrief")[0]["model"] == "big-model"


def test_build_brief_failure_modes(job_ctx, monkeypatch, sample_ingest):
    llm = ScriptedLLM(use_fake_content=False)
    llm.on("GenBrief", lambda p, s: {"concepts": []})
    install_providers(monkeypatch, llm)
    with pytest.raises(ProviderError):
        asyncio.run(build_brief(job_ctx, sample_ingest, OPTIONS))
    with pytest.raises(BriefUnavailable):
        asyncio.run(build_brief(job_ctx, IngestResult(markdown="Tiny."), OPTIONS))


def test_fake_brief_responder_is_valid_for_the_sme_template(template_ingest):
    _, user = build_brief_prompt(template_ingest, OPTIONS)
    gen = GenBrief.model_validate(fake_brief_responder(user, GenBrief))
    hard, _ = brief_problems(gen, {c.id for c in template_ingest.chunks})
    assert not hard and len(gen.concepts) >= 5
    assert any("Who developed Boolean Algebra?" in q.text and q.source_refs for q in gen.source_questions)
    assert brief_problems(gen, {c.id: c.text for c in template_ingest.chunks}) == ([], [])  # every chunk accounted
    out = brief_from_gen(gen, template_ingest)
    assert out.teaching_order[0] == out.concepts[0].key


# ---------------------------------------------------------------------------
# orchestrator wiring
# ---------------------------------------------------------------------------


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch, real_timeline=True)
    providers.llm.on("GenBrief", fake_brief_responder)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    seen: list[Any] = []
    real = orchestrator.generate_plan

    async def spy(ctx, ingest, options, meta=None, *, brief=None):
        seen.append({"brief": brief, "meta": meta})
        if brief is not None and orchestrator._accepts_kw(real, "brief"):
            return await real(ctx, ingest, options, meta, brief=brief)
        return await real(ctx, ingest, options, meta)

    monkeypatch.setattr(orchestrator, "generate_plan", spy)
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded, "plan_calls": seen}


def generate(world: dict[str, Any], **payload: Any) -> dict[str, Any]:
    s = world["seeded"]
    ctx = world["ctx"]
    ctx.kind = "generate_lecture"
    ctx.payload = {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1,
                   **payload}
    return asyncio.run(orchestrator.generate_lecture(ctx))


def test_brief_is_stored_and_passed_to_the_planner(world):
    generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready"
    calls = world["plan_calls"]
    assert len(calls) == 1 and isinstance(calls[0]["brief"], ConceptBrief) and calls[0]["brief"].concepts
    stored = plan_state.load_brief(v)
    assert stored is not None and [c.key for c in stored.concepts] == [c.key for c in calls[0]["brief"].concepts]
    meta = v.generation_meta
    assert meta["models"]["brief"] == world["ctx"].settings.llm_model_fast
    assert meta["prompt_versions"]["brief"] == "4"
    assert meta["ingest"]["source_format"] == "notes" and meta["ingest"]["excluded"] == {}
    progress = [e for e in world["ctx"].events if e["type"] == "progress" and e["stage"] == "plan"]
    messages = [e["message"] for e in progress]
    assert messages.index("Extracting the concepts to teach") < messages.index("Planning the lecture")
    assert progress[0]["progress"] == pytest.approx(0.06) and progress[1]["progress"] == pytest.approx(0.10)


def test_a_failed_brief_never_fails_the_lecture(world):
    world["providers"].llm.on("GenBrief", lambda p, s: ProviderError("model overloaded", provider="fake"))
    generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and Screenplay.model_validate(v.screenplay).scenes
    assert world["plan_calls"][0]["brief"] is None and plan_state.BRIEF_KEY not in v.generation_meta
    warnings = [e["message"] for e in world["ctx"].events if e["type"] == "log" and e["level"] == "warning"]
    assert any("planned directly from the source" in w for w in warnings)


def test_plan_review_keeps_the_brief_for_the_resume(world):
    opts = OPTIONS.model_copy(update={"review_plan": True}).model_dump(mode="json")
    with pytest.raises(AwaitingReview) as exc:
        generate(world, options=opts)
    v = get_version(world["seeded"].version_id)
    assert v.status == "awaiting_review" and plan_state.load_brief(v) is not None
    assert v.generation_meta["models"]["brief"]
    from aadhi.db import compare_and_set, session_scope
    from aadhi.models import ProjectVersion

    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, v.id, {"status": "awaiting_review"}, {"status": "generating"})
    n_brief = len(world["providers"].llm.calls_for("GenBrief"))
    generate(world, options=opts, resume_state=exc.value.state)
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and plan_state.load_brief(v) is not None
    assert len(world["providers"].llm.calls_for("GenBrief")) == n_brief  # not extracted again


def test_document_meta_fills_only_empty_teacher_fields():
    from types import SimpleNamespace

    from aadhi.pipeline.base import SourceMeta

    ingest = IngestResult(markdown="x", document_meta=SourceMeta(subject_name="Digital System Design", unit_name="Gates",
                                                                  session_number="Session 2", session_title="Boolean laws"))
    project = SimpleNamespace(subject_name="Digital Electronics", unit_name="", session_number=None, session_title="")
    meta = orchestrator.teacher_meta(project, ingest)
    assert (meta.subject_name, meta.unit_name, meta.session_number, meta.session_title) == (
        "Digital Electronics", "Gates", "Session 2", "Boolean laws")
    assert orchestrator.teacher_meta(None, IngestResult(markdown="x")).subject_name == ""


def test_sme_template_lecture_has_no_admin_leaks(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch)
    providers.llm.on("GenBrief", fake_brief_responder)
    seeded = seed_project(job_ctx.assets.storage, sample_template_bytes(), DOCX, "session2.docx",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    job_ctx.kind = "generate_lecture"
    job_ctx.payload = {"source_document_id": seeded.source_id, "options": OPTIONS.model_dump(mode="json"),
                       "base_revision": 1}
    asyncio.run(orchestrator.generate_lecture(job_ctx))
    v = get_version(seeded.version_id)
    assert v.status == "ready"
    sp = Screenplay.model_validate(v.screenplay)
    # the source's own metadata fills the title-card fields the teacher left empty
    assert (sp.subject_name, sp.session_number) == ("Digital System Design", "Session 2")
    assert sp.session_title == "Boolean Postulates, Laws, and Minimization of Boolean Expressions"
    text = json.dumps([s.model_dump(mode="json") for s in sp.scenes], ensure_ascii=False)
    for leak in ("Estimated duration", "CLIP 1", "TITLE CARD", "COMING UP NEXT", "Aadhi speaks", "BOARD displays",
                 "0:45", "3.5-4 minutes", "DETAILED ANIMATION", "Explanation:", "Board points:"):
        assert leak not in text, leak
    for prompt_call in providers.llm.calls:
        assert "Estimated duration" not in prompt_call["prompt"] and "SUBJECT NAME" not in prompt_call["prompt"]
    assert v.generation_meta["ingest"]["source_format"] == "sme_script"
    assert v.generation_meta["ingest"]["visual_notes"] >= 20
