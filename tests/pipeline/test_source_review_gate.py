"""The source-review gate of ``generate_lecture`` (payload ``review_source``): pause after ingest + concept brief,
the teacher's corrections, the continuation (and its retries), and set-aside parts never reaching a later stage."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.db import compare_and_set, session_scope
from aadhi.jobs.base import AwaitingReview
from aadhi.models import ProjectVersion
from aadhi.pipeline import orchestrator, plan_state
from aadhi.pipeline import source_review as sr
from aadhi.pipeline.base import GenerationOptions, LecturePlan
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)
SET_ASIDE = "geyser converts electrical energy"  # only in the "Power in circuits" part (c0004)
SCENE_SCHEMAS = ("GenBoardScene", "GenChapterCard", "GenQuiz", "GenSimulation", "GenAIVideo", "GenInteractive")


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch, real_timeline=True)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded}


def generate(world: dict[str, Any], options: GenerationOptions = OPTIONS, **payload: Any) -> dict[str, Any]:
    s = world["seeded"]
    ctx = world["ctx"]
    ctx.kind = "generate_lecture"
    ctx.payload = {"source_document_id": s.source_id, "options": options.model_dump(mode="json"), "base_revision": 1,
                   **payload}
    return asyncio.run(orchestrator.generate_lecture(ctx))


def pause(world: dict[str, Any], **kw: Any) -> dict[str, Any]:
    with pytest.raises(AwaitingReview) as exc:
        generate(world, review_source=True, **kw)
    return exc.value.state


def correct_and_approve(world: dict[str, Any], **overrides: Any) -> None:
    """What PUT /source-review + POST /approve-source do to the version."""
    v = get_version(world["seeded"].version_id)
    meta = dict(v.generation_meta)
    meta[sr.OVERRIDES_KEY] = sr.SourceOverrides(ingest_key=meta["ingest_key"], **overrides).model_dump(mode="json")
    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, v.id, {"status": "awaiting_review"},
                               {"status": "generating", "generation_meta": meta})


def prompts(world: dict[str, Any], *schemas: str, start: int = 0) -> list[str]:
    return [c["prompt"] for c in world["providers"].llm.calls[start:] if c["schema"] in schemas]


def test_pause_after_the_brief_then_continue_with_the_teachers_corrections(world):
    s = world["seeded"]
    state = pause(world)
    assert state["stage"] == sr.RESUME_STAGE and state["source_document_id"] == s.source_id
    v = get_version(s.version_id)
    assert v.status == "awaiting_review" and v.screenplay is None
    assert v.generation_meta[sr.REVIEW_STAGE_KEY] == sr.REVIEW_STAGE_SOURCE
    assert plan_state.load_brief(v) is not None and plan_state.load_plan(v) is None
    assert [c["schema"] for c in world["providers"].llm.calls] == ["GenBrief"]  # nothing planned or written yet
    keys = [c.key for c in plan_state.load_brief(v).concepts]
    assert keys == ["voltage_and_current", "resistance", "ohm_s_law", "power_in_circuits"]

    correct_and_approve(world, excluded_chunk_ids=["c0004"], concept_names={"resistance": "Resistance of materials"})
    start = len(world["providers"].llm.calls)
    generate(world, review_source=True, resume_state=state)
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 2
    assert sr.REVIEW_STAGE_KEY not in v.generation_meta
    assert "GenBrief" not in [c["schema"] for c in world["providers"].llm.calls[start:]]  # the checked brief is reused
    plan_prompt = prompts(world, "GenPlan", start=start)[0]
    assert "Resistance of materials" in plan_prompt and SET_ASIDE not in plan_prompt
    later = prompts(world, *SCENE_SCHEMAS, "GenCritique", "GenPractice", "GenRepair", start=start)
    assert later and not any(SET_ASIDE in p for p in later)
    brief = plan_state.load_brief(v)
    assert {"chunk_id": "c0004", "reason": sr.TEACHER_SKIP_REASON} in [s.model_dump() for s in brief.skipped_chunks]
    assert [c.key for c in brief.concepts] == ["voltage_and_current", "resistance", "ohm_s_law"]
    sp = Screenplay.model_validate(v.screenplay)
    cited = {r for sc in sp.scenes for b in sc.all_beats() for r in b.source_refs}
    assert "c0004" not in cited
    assert v.generation_meta[sr.OVERRIDES_KEY]["excluded_chunk_ids"] == ["c0004"]  # kept as the record


def test_without_review_source_nothing_changes(world):
    generate(world)
    meta = get_version(world["seeded"].version_id).generation_meta
    assert sr.REVIEW_STAGE_KEY not in meta and sr.OVERRIDES_KEY not in meta
    assert "GenPlan" in [c["schema"] for c in world["providers"].llm.calls]


def test_continuation_retry_reuses_its_stages_and_keeps_the_corrections(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 81, 1
    state = pause(world)
    correct_and_approve(world, excluded_chunk_ids=["c0004"])
    real = orchestrator.critique
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("critic crashed")
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "critique", flaky)
    ctx.job_id, ctx.attempt = 82, 1  # the continuation job
    with pytest.raises(RuntimeError, match="critic crashed"):
        generate(world, review_source=True, resume_state=state)
    assert get_version(world["seeded"].version_id).status == "generating"  # left for the retry
    start = len(world["providers"].llm.calls)
    ctx.attempt = 2  # the worker runs the same continuation again
    generate(world, review_source=True, resume_state=state)
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and v.generation_meta["reused_stages"] == ["plan", "script"]
    assert not {"GenPlan", "GenBrief"} & {c["schema"] for c in world["providers"].llm.calls[start:]}
    assert not any(SET_ASIDE in p for p in prompts(world, "GenCritique", "GenPractice", start=start))


def test_a_new_extract_since_the_review_ignores_the_corrections(world):
    s = world["seeded"]
    state = pause(world)
    correct_and_approve(world, excluded_chunk_ids=["c0004"])
    v = get_version(s.version_id)
    meta = dict(v.generation_meta)
    meta["ingest_key"] = "extract-from-an-older-reader"  # the source was read with another INGEST_VERSION since
    meta[sr.OVERRIDES_KEY] = {**meta[sr.OVERRIDES_KEY], "ingest_key": "extract-from-an-older-reader"}
    with session_scope() as db:
        db.get(ProjectVersion, v.id).generation_meta = meta
    start = len(world["providers"].llm.calls)
    generate(world, review_source=True, resume_state=state)
    assert get_version(s.version_id).status == "ready"
    assert SET_ASIDE in prompts(world, "GenPlan", start=start)[0]  # the ids may name other text: not applied
    logs = [e["message"] for e in world["ctx"].events if e["type"] == "log"]
    assert any("corrections could not be applied" in m for m in logs)


def test_source_review_then_plan_review(world):
    s = world["seeded"]
    options = OPTIONS.model_copy(update={"review_plan": True})
    state = pause(world, options=options)
    correct_and_approve(world, excluded_chunk_ids=["c0004"])
    with pytest.raises(AwaitingReview) as exc:
        generate(world, options=options, review_source=True, resume_state=state)
    assert exc.value.state["stage"] == "plan_review"
    v = get_version(s.version_id)
    assert v.status == "awaiting_review" and sr.REVIEW_STAGE_KEY not in v.generation_meta
    plan = plan_state.load_plan(v)
    assert isinstance(plan, LecturePlan) and "c0004" not in {r for x in plan.all_scenes() for r in x.source_refs}
    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, v.id, {"status": "awaiting_review"}, {"status": "generating"})
    start = len(world["providers"].llm.calls)
    generate(world, options=options, review_source=True, resume_state=exc.value.state)
    assert get_version(s.version_id).status == "ready"
    assert not any(SET_ASIDE in p for p in prompts(world, *SCENE_SCHEMAS, start=start))


def test_regenerating_a_scene_keeps_the_set_aside_parts_out(world):
    state = pause(world)
    correct_and_approve(world, excluded_chunk_ids=["c0004"])
    generate(world, review_source=True, resume_state=state)
    v = get_version(world["seeded"].version_id)
    sp = Screenplay.model_validate(v.screenplay)
    target = next(x for x in sp.scenes if x.type == "content")
    start = len(world["providers"].llm.calls)
    ctx = world["ctx"]
    ctx.kind, ctx.payload = "regenerate_scene", {"scene_id": target.id, "instructions": "Shorter.", "base_revision": 2}
    asyncio.run(orchestrator.regenerate_scene(ctx))
    rewritten = prompts(world, *SCENE_SCHEMAS, start=start)
    assert rewritten and not any(SET_ASIDE in p for p in rewritten)


def test_teacher_scoped_ingest_needs_matching_ids(sample_ingest):
    meta = {"ingest_key": "k", sr.OVERRIDES_KEY: sr.SourceOverrides(ingest_key="k", excluded_chunk_ids=["c0002"])
            .model_dump(mode="json")}
    assert [c.id for c in orchestrator._teacher_scoped(sample_ingest, meta).chunks] == ["c0001", "c0003", "c0004"]
    other = {**meta, sr.OVERRIDES_KEY: {**meta[sr.OVERRIDES_KEY], "excluded_chunk_ids": ["c0099"]}}
    assert orchestrator._teacher_scoped(sample_ingest, other) is sample_ingest
    assert orchestrator._teacher_scoped(sample_ingest, {"ingest_key": "k"}) is sample_ingest
