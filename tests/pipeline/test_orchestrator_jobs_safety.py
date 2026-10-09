"""Orchestrator job safety: a retried job reuses the LLM stages it already paid for (job-scoped
checkpoints), and work stops before paying when the version changed (still-wanted gate)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select, update

from aadhi.db import compare_and_set, session_scope
from aadhi.models import Asset, Project, ProjectVersion
from aadhi.pipeline import orchestrator
from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.checkpoint import StageCheckpoints, stage_digest
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)
SCENE_SCHEMAS = ("GenBoardScene", "GenChapterCard", "GenQuiz", "GenSimulation", "GenAIVideo", "GenInteractive")


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch, real_timeline=True)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded}


def run(ctx: Any, handler, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.kind = kind
    ctx.payload = payload
    return asyncio.run(handler(ctx))


def generate(world: dict[str, Any], **payload: Any) -> dict[str, Any]:
    s = world["seeded"]
    body = {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1, **payload}
    return run(world["ctx"], orchestrator.generate_lecture, "generate_lecture", body)


def fail_first_critique(monkeypatch) -> dict[str, int]:
    """The critic crashes on the first attempt (after the plan and every scene were paid for)."""
    real = orchestrator.critique
    state = {"calls": 0}

    async def flaky(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("critic crashed")
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "critique", flaky)
    return state


def spy_scene_writing(monkeypatch) -> list[Screenplay]:
    real = orchestrator.write_scenes_detailed
    written: list[Screenplay] = []

    async def spy(*args, **kwargs):
        result = await real(*args, **kwargs)
        written.append(result.screenplay)
        return result

    monkeypatch.setattr(orchestrator, "write_scenes_detailed", spy)
    return written


def llm_schemas(world: dict[str, Any], start: int = 0) -> list[str]:
    return [c["schema"] for c in world["providers"].llm.calls[start:]]


def checkpoint_assets() -> list[Asset]:
    with session_scope() as db:
        rows = db.execute(select(Asset).where(Asset.kind == "intermediate")).scalars().all()
        return [a for a in rows if (a.meta or {}).get("checkpoint")]


# --- LLM stage checkpoints ------------------------------------------------------------------------


def test_retry_reuses_the_plan_and_scenes_it_already_paid_for(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 61, 1
    critic = fail_first_critique(monkeypatch)
    written = spy_scene_writing(monkeypatch)
    with pytest.raises(RuntimeError, match="critic crashed"):
        generate(world)
    s = world["seeded"]
    assert get_version(s.version_id).status == "generating"  # left for the retry
    assert {a.meta["checkpoint"] for a in checkpoint_assets()} == {"plan", "scene", "script"}  # + one per scene
    first = len(world["providers"].llm.calls)
    assert "GenPlan" in llm_schemas(world) and len(written) == 1

    ctx.attempt = 2  # the worker runs the same job (same id and payload) again
    result = generate(world)
    assert "skipped" not in result and critic["calls"] == 2
    again = llm_schemas(world, first)
    assert not {"GenPlan", "GenBrief"} & set(again)  # no re-planning
    assert len(written) == 1  # the scenes were not written again
    v = get_version(s.version_id)
    assert v.status == "ready" and v.revision == 2
    assert v.generation_meta["reused_stages"] == ["plan", "script"]
    sp = Screenplay.model_validate(v.screenplay)
    assert [x.id for x in sp.scenes] == [x.id for x in written[0].scenes]  # continues from the paid-for scenes
    logs = [e["message"] for e in ctx.events if e["type"] == "log"]
    assert any("Reusing the concept brief and the plan" in m for m in logs)
    assert any("written scenes from this job's previous attempt" in m for m in logs)
    assert {a.meta["checkpoint"] for a in checkpoint_assets()} == {"plan", "scene", "script", "validate", "companion"}


def test_retry_after_the_companion_reuses_every_llm_stage(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 64, 1
    real = orchestrator._cas
    state = {"n": 0}

    async def flaky_cas(c, vid, expected, values):
        if "screenplay" in values and state["n"] == 0:  # the screenplay save fails once (e.g. DB hiccup)
            state["n"] += 1
            raise RuntimeError("database went away")
        return await real(c, vid, expected, values)

    monkeypatch.setattr(orchestrator, "_cas", flaky_cas)
    with pytest.raises(RuntimeError):
        generate(world)
    first = len(world["providers"].llm.calls)
    ctx.attempt = 2
    generate(world)
    assert llm_schemas(world, first) == []  # nothing was asked of the model again
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and v.generation_meta["reused_stages"] == ["plan", "script", "validate", "companion"]


def test_checkpoints_are_scoped_to_the_job(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 62, 1
    fail_first_critique(monkeypatch)
    with pytest.raises(RuntimeError):
        generate(world)
    first = len(world["providers"].llm.calls)
    ctx.job_id, ctx.attempt = 63, 2  # another job (e.g. "generate again"): a new lecture, not a copy
    generate(world)
    assert "GenPlan" in llm_schemas(world, first)
    assert "reused_stages" not in get_version(world["seeded"].version_id).generation_meta


def test_changed_inputs_between_attempts_run_the_stage_again(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 65, 1
    fail_first_critique(monkeypatch)
    with pytest.raises(RuntimeError):
        generate(world)
    with session_scope() as db:  # the teacher renamed the session before the retry ran
        db.execute(update(Project).where(Project.id == world["seeded"].project_id)
                   .values(session_title="Ohm's law in practice"))
    first = len(world["providers"].llm.calls)
    ctx.attempt = 2
    generate(world)
    assert "GenPlan" in llm_schemas(world, first)


def test_checkpoints_can_be_switched_off(world, monkeypatch):
    ctx = world["ctx"]
    ctx.settings.llm_stage_checkpoints = False
    ctx.job_id, ctx.attempt = 66, 1
    fail_first_critique(monkeypatch)
    with pytest.raises(RuntimeError):
        generate(world)
    assert checkpoint_assets() == []
    first = len(world["providers"].llm.calls)
    ctx.attempt = 2
    generate(world)
    assert "GenPlan" in llm_schemas(world, first) and checkpoint_assets() == []


def test_contexts_without_a_job_id_never_checkpoint(world):
    assert world["ctx"].job_id == 0
    generate(world)
    assert checkpoint_assets() == []


def test_unreadable_checkpoint_runs_the_stage_again(world, monkeypatch):
    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 67, 1
    fail_first_critique(monkeypatch)
    with pytest.raises(RuntimeError):
        generate(world)
    for a in checkpoint_assets():  # corrupt every stored checkpoint (in the test's temporary storage)
        ctx.assets.storage.delete(a.storage_key)
        ctx.assets.storage.put_bytes(a.storage_key, b"{not json", "application/json")
    first = len(world["providers"].llm.calls)
    ctx.attempt = 2
    assert "skipped" not in generate(world)
    assert "GenPlan" in llm_schemas(world, first)


def stop_at_scene(monkeypatch, llm, n: int) -> dict[str, Any]:
    """The ``n``-th scene call hits an exhausted quota (a job-level stop: the attempt ends, the job is retried)."""
    from aadhi.providers.base import RateLimited

    real = llm.generate_json
    state = {"scenes": 0, "on": True}

    async def flaky(**kwargs):
        if state["on"] and kwargs["schema"].__name__.startswith(SCENE_SCHEMAS):
            state["scenes"] += 1
            await asyncio.sleep(0)  # a real provider answers later: the other scenes wait for the LLM slot
            if state["scenes"] == n:
                raise RateLimited("quota exhausted", provider="fake")
        return await real(**kwargs)

    monkeypatch.setattr(llm, "generate_json", flaky)
    return state


def count_scene_writes(monkeypatch) -> list[str]:
    from aadhi.pipeline import script

    real = script.write_scene
    ids: list[str] = []

    async def spy(ctx, lc, pos, **kwargs):
        ids.append(pos.planned.id)
        return await real(ctx, lc, pos, **kwargs)

    monkeypatch.setattr(script, "write_scene", spy)
    return ids


def test_retry_after_a_stop_while_writing_scenes_reuses_the_finished_scenes(world, monkeypatch):
    from aadhi.jobs.base import RetryableJobError

    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 68, 1
    ctx.settings.llm_max_parallel = 1  # one scene after another: a deterministic stopping point
    stop = stop_at_scene(monkeypatch, world["providers"].llm, 3)
    writes = count_scene_writes(monkeypatch)
    with pytest.raises(RetryableJobError):
        generate(world)
    # the two scenes before the stop are kept (a scene already in flight when the attempt ended may be too)
    kept = len([a for a in checkpoint_assets() if a.meta["checkpoint"] == "scene"])
    assert kept >= 2 and {a.meta["checkpoint"] for a in checkpoint_assets()} == {"plan", "scene"}
    first_writes, finished, stopped = len(writes), writes[:2], writes[2]

    stop["on"] = False
    ctx.attempt = 2
    generate(world)
    v = get_version(world["seeded"].version_id)
    sp = Screenplay.model_validate(v.screenplay)
    again = writes[first_writes:]
    assert len(again) == len(sp.scenes) - kept and stopped in again  # only the unfinished scenes
    assert not set(finished) & set(again)
    assert v.status == "ready" and v.generation_meta["reused_stages"] == ["plan", "scenes"]
    logs = [e["message"] for e in ctx.events if e["type"] == "log"]
    assert any(f"Reusing {kept} of the {len(sp.scenes)} scenes" in m for m in logs)


def test_scene_checkpoints_are_scoped_to_the_job(world, monkeypatch):
    from aadhi.jobs.base import RetryableJobError

    ctx = world["ctx"]
    ctx.job_id, ctx.attempt = 69, 1
    ctx.settings.llm_max_parallel = 1
    stop = stop_at_scene(monkeypatch, world["providers"].llm, 3)
    writes = count_scene_writes(monkeypatch)
    with pytest.raises(RetryableJobError):
        generate(world)
    stop["on"] = False
    first_writes = len(writes)
    ctx.job_id, ctx.attempt = 70, 2  # another job ("generate again"): every scene is written anew
    generate(world)
    sp = Screenplay.model_validate(get_version(world["seeded"].version_id).screenplay)
    assert len(writes) - first_writes == len(sp.scenes)


def test_stage_digest_ignores_key_order_and_json_round_trips():
    assert stage_digest({"a": 1, "b": [1, 2]}, "x") == stage_digest({"b": [1, 2], "a": 1}, "x")
    assert stage_digest({"a": 1}) != stage_digest({"a": 2})


def test_checkpoint_keys_depend_on_job_stage_and_inputs(job_ctx):
    job_ctx.job_id = 5
    a = StageCheckpoints(job_ctx)
    assert a.enabled and not a.resumable  # first attempt: saves, never loads
    job_ctx.attempt = 2
    b = StageCheckpoints(job_ctx)
    assert b.resumable and a.key("plan", "d") == b.key("plan", "d")
    assert len({b.key("plan", "d"), b.key("script", "d"), b.key("plan", "e")}) == 3
    job_ctx.job_id = 6
    assert StageCheckpoints(job_ctx).key("plan", "d") != b.key("plan", "d")


def test_translate_retry_reuses_the_translation(world, monkeypatch):
    generate(world)
    s = world["seeded"]
    ctx = world["ctx"]
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1,
                             source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    ctx.version_id, ctx.job_id, ctx.attempt = new_id, 81, 1
    real = orchestrator._load_ingest
    state = {"n": 0}

    async def flaky(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("storage hiccup")
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "_load_ingest", flaky)
    body = {"source_version_id": s.version_id, "target_language": "hi-IN", "translate_board": False}
    with pytest.raises(RuntimeError, match="storage hiccup"):
        run(ctx, orchestrator.translate_job, "translate", body)
    n_translate = len(world["providers"].llm.calls_for("GenTranslation"))
    assert n_translate > 0
    ctx.attempt = 2
    assert run(ctx, orchestrator.translate_job, "translate", body) == {"version_id": new_id}
    assert len(world["providers"].llm.calls_for("GenTranslation")) == n_translate
    v = get_version(new_id)
    assert v.status == "ready" and v.language == "hi-IN"


# --- still-wanted gate ------------------------------------------------------------------------------


def test_generation_stops_before_planning_when_the_version_changed(world, monkeypatch):
    real = orchestrator.ingest_source
    s = world["seeded"]

    async def ingest_then_edit(*args, **kwargs):
        result = await real(*args, **kwargs)
        with session_scope() as db:  # the version changed while the source was being read
            assert compare_and_set(db, ProjectVersion, s.version_id, {"revision": 1}, {"revision": 2})
        return result

    monkeypatch.setattr(orchestrator, "ingest_source", ingest_then_edit)
    result = generate(world)
    assert result["skipped"] == "revision_conflict"
    assert world["providers"].llm.calls == []  # no brief, no plan: nothing paid
    assert any("changed while this job ran" in e["message"] for e in world["ctx"].events if e["type"] == "log")


def _with_new_paid_image(screenplay: dict[str, Any]) -> dict[str, Any]:
    """The teacher's edit: a scene gets a generated image panel (a paid generation on the next build)."""
    for scene in screenplay["scenes"]:
        if "side_panel" in type(Screenplay.model_validate(screenplay).scene_by_id(scene["id"])).model_fields:
            scene["side_panel"] = {"kind": "image", "image_prompt": "A copper wire glowing as current flows, edited",
                                   "rationale": "Makes the current visible."}
            return screenplay
    raise AssertionError("no scene can show a side panel")


def test_paid_media_is_not_generated_after_the_version_changed_mid_build(world, monkeypatch):
    generate(world)
    s = world["seeded"]
    data = _with_new_paid_image(get_version(s.version_id).screenplay)
    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, s.version_id, {"revision": 2}, {"screenplay": data, "revision": 3})
    providers = world["providers"]
    paid_before = (len(providers.video.prompts), len(providers.image.prompts))
    manifest_before = get_version(s.version_id).asset_manifest
    real = orchestrator.build_assets_detailed

    async def edited_again(*args, **kwargs):
        with session_scope() as db:  # another edit lands while this build runs
            assert compare_and_set(db, ProjectVersion, s.version_id, {"revision": 3}, {"revision": 4})
        return await real(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "build_assets_detailed", edited_again)
    result = run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 3})
    assert result["skipped"] == "revision_conflict"
    assert (len(providers.video.prompts), len(providers.image.prompts)) == paid_before  # nothing paid
    v = get_version(s.version_id)
    assert v.revision == 4 and v.asset_manifest == manifest_before  # nothing written (the worker settles the status)
    logs = [e["message"] for e in world["ctx"].events if e["type"] == "log"]
    assert any("changed while this job ran" in m and "image not generated" in m for m in logs)


def test_paid_media_is_generated_while_the_version_is_unchanged(world):
    """Control for the test above: the same edit without a concurrent change builds the image."""
    generate(world)
    s = world["seeded"]
    data = _with_new_paid_image(get_version(s.version_id).screenplay)
    with session_scope() as db:
        assert compare_and_set(db, ProjectVersion, s.version_id, {"revision": 2}, {"screenplay": data, "revision": 3})
    n_images = len(world["providers"].image.prompts)
    assert run(world["ctx"], orchestrator.build_assets_job, "build_assets",
               {"scene_ids": None, "base_revision": 3}) == {"version_id": s.version_id}
    assert len(world["providers"].image.prompts) == n_images + 1
    assert get_version(s.version_id).status == "ready"


def test_production_guard_rechecks_at_most_every_few_seconds(job_ctx, monkeypatch):
    calls = {"n": 0}
    revision = {"value": 3}

    def fake_revision(db, vid):
        calls["n"] += 1
        return revision["value"]

    monkeypatch.setattr(orchestrator, "_revision_of", fake_revision)
    clock = {"t": 100.0}
    guard = orchestrator.production_guard(job_ctx, 1, 3)
    guard.clock = lambda: clock["t"]  # type: ignore[attr-defined]
    guard("video", "k1")
    guard("image", "k2")
    assert calls["n"] == 1  # cached
    clock["t"] += orchestrator.GUARD_RECHECK_SECONDS + 0.1
    revision["value"] = 4
    with pytest.raises(orchestrator.VersionChanged):
        guard("video", "k3")
    with pytest.raises(orchestrator.VersionChanged):  # stays refused without another query
        guard("video", "k4")
    assert calls["n"] == 2
