"""The lecture's AI engine (``GenerationOptions.llm_provider``) drives every LLM call and model pick.

The provider seam ``integrations.get_llm`` is replaced by a recorder that answers with the offline
scripted LLM and notes which engine each stage asked for, so no real model is ever called.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from aadhi.db import session_scope
from aadhi.models import ProjectVersion
from aadhi.pipeline import integrations, orchestrator
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.pipeline.brief import brief_model
from aadhi.pipeline.critic import critic_model
from aadhi.pipeline.plan import plan_model
from aadhi.pipeline.script import script_model
from aadhi.pipeline.translate import translate_model
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

CLAUDE = {"plan": "claude-plan-test", "script": "claude-script-test", "critic": "claude-critic-test",
          "fast": "claude-fast-test"}
# stage function that asked for a provider -> stage
CALLERS = {"build_brief": "brief", "generate_plan": "plan", "write_scenes_detailed": "script", "_write_scene": "script",
           "critique": "critic", "repair_detailed": "repair", "rewrite_scene": "repair", "generate_practice": "companion",
           "translate_screenplay": "translate"}


def claude_settings(settings: Any) -> Any:
    return settings.model_copy(update={f"anthropic_model_{tier}": model for tier, model in CLAUDE.items()})


# --- model routing ----------------------------------------------------------------------------------


def test_llm_engine_and_llm_model(app_env) -> None:
    s = claude_settings(app_env).model_copy(update={"llm_provider": "gemini", "llm_model_plan": "gemini-2.5-pro"})
    assert integrations.llm_engine(None, s) == "gemini"
    assert integrations.llm_engine(GenerationOptions(), s) == "gemini"
    claude = GenerationOptions(llm_provider="anthropic")
    assert integrations.llm_engine(claude, s) == "anthropic"
    assert {t: integrations.llm_model(s, t, claude) for t in CLAUDE} == CLAUDE
    assert integrations.llm_model(s, "plan") == "gemini-2.5-pro"
    # an admin override wins only for the lecture's own engine
    assert integrations.llm_model(s, "plan", claude, override="claude-override") == "claude-override"
    assert integrations.llm_model(s, "plan", claude, override="gpt-5") == CLAUDE["plan"]
    assert integrations.llm_model(s, "plan", GenerationOptions(llm_provider="openai"), override="gpt-5") == "gpt-5"
    assert integrations.llm_model(s, "script", None, override="gemini-2.5-pro") == "gemini-2.5-pro"
    # the offline engine takes any override and reports LLM_MODEL_<TIER> otherwise
    fake = app_env.model_copy(update={"llm_provider": "fake"})
    assert integrations.llm_model(fake, "plan", None, override="anything") == "anything"
    assert integrations.llm_model(fake, "critic") == fake.llm_model_critic


def test_stage_model_helpers_follow_the_engine(app_env) -> None:
    s = claude_settings(app_env)
    opts = GenerationOptions(llm_provider="anthropic", llm_model_plan="claude-admin-plan", llm_model_script="gpt-5")
    assert plan_model(opts, s) == "claude-admin-plan"
    assert brief_model(opts, s) == "claude-admin-plan"  # the brief honours the planning override
    assert script_model(opts, s) == CLAUDE["script"]  # a GPT override does not apply to Claude
    assert translate_model(opts, s) == CLAUDE["script"]
    assert critic_model(s, opts) == CLAUDE["critic"]
    plain = GenerationOptions(llm_provider="anthropic")
    assert brief_model(plain, s) == CLAUDE["fast"] and plan_model(plain, s) == CLAUDE["plan"]
    default = GenerationOptions()  # server default engine (fake in tests): LLM_MODEL_<TIER>
    assert (plan_model(default, s), script_model(default, s), critic_model(s)) == (
        s.llm_model_plan, s.llm_model_script, s.llm_model_critic)


def test_get_llm_passes_the_engine_to_the_factory(app_env, monkeypatch) -> None:
    from aadhi.providers import factory

    seen: list[tuple[Any, str | None]] = []
    real = factory.get_llm

    def spy(settings: Any = None, engine: str | None = None) -> Any:
        seen.append((settings, engine))
        return real(settings, None)  # always the offline engine

    monkeypatch.setattr(factory, "get_llm", spy)
    llm = integrations.get_llm(app_env, "anthropic")
    assert llm.name == "fake" and seen == [(app_env, "anthropic")]
    integrations.get_llm(app_env)
    assert seen[-1] == (app_env, None)


# --- the pipeline -----------------------------------------------------------------------------------


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch)
    asked: list[tuple[str, str | None]] = []

    def recording_get_llm(settings: Any, engine: str | None = None) -> Any:
        caller = next((f.function for f in inspect.stack()[1:6] if f.function in CALLERS), "?")
        asked.append((CALLERS.get(caller, caller), engine))
        return providers.llm

    monkeypatch.setattr(integrations, "get_llm", recording_get_llm)
    job_ctx.settings = claude_settings(job_ctx.settings)
    options = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2, llm_provider="anthropic")
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=options.model_dump(mode="json"))
    job_ctx.project_id, job_ctx.version_id = seeded.project_id, seeded.version_id
    return {"ctx": job_ctx, "providers": providers, "seeded": seeded, "asked": asked, "options": options}


def run(ctx: Any, handler: Any, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.kind, ctx.payload = kind, payload
    return asyncio.run(handler(ctx))


def generate(world: dict[str, Any]) -> None:
    s = world["seeded"]
    run(world["ctx"], orchestrator.generate_lecture, "generate_lecture",
        {"source_document_id": s.source_id, "options": world["options"].model_dump(mode="json"), "base_revision": 1})


def test_lecture_with_anthropic_uses_it_for_every_llm_stage(world) -> None:
    generate(world)
    asked = world["asked"]
    stages = {stage for stage, _ in asked}
    assert {"brief", "plan", "script", "critic", "companion"} <= stages, asked
    assert {engine for _, engine in asked} == {"anthropic"}
    calls = world["providers"].llm.calls
    models = {c["schema"]: c["model"] for c in calls}
    assert models["GenBrief"] == CLAUDE["fast"] and models["GenPlan"] == CLAUDE["plan"]
    assert models["GenCritique"] == CLAUDE["critic"] and models["GenPractice"] == CLAUDE["script"]
    scene_models = {c["model"] for c in calls if c["schema"].startswith("Gen") and c["schema"].endswith("Scene")}
    assert scene_models == {CLAUDE["script"]}
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready"
    assert v.generation_meta["options"]["llm_provider"] == "anthropic"
    assert v.generation_meta["models"] == {"brief": CLAUDE["fast"], "plan": CLAUDE["plan"], "script": CLAUDE["script"],
                                           "critic": CLAUDE["critic"], "provider": "anthropic"}
    assert all(r.llm_provider == "anthropic" for r in world["providers"].renderer.requests)


def test_default_engine_is_recorded_when_none_is_chosen(world) -> None:
    world["options"] = world["options"].model_copy(update={"llm_provider": None})
    generate(world)
    assert {engine for _, engine in world["asked"]} == {"fake"}  # the server default, resolved per stage
    meta = get_version(world["seeded"].version_id).generation_meta
    assert meta["models"]["provider"] == "fake" and meta["models"]["plan"] == world["ctx"].settings.llm_model_plan


def test_regenerate_scene_and_translation_keep_the_versions_engine(world) -> None:
    generate(world)
    s = world["seeded"]
    sp = Screenplay.model_validate(get_version(s.version_id).screenplay)
    target = next(x for x in sp.scenes if x.type == "content")
    world["asked"].clear()
    v = get_version(s.version_id)
    run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
        {"scene_id": target.id, "instructions": "Shorter.", "base_revision": v.revision})
    assert world["asked"] and {e for _, e in world["asked"]} == {"anthropic"}
    assert {stage for stage, _ in world["asked"]} >= {"repair", "critic"}

    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1,
                             source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    world["asked"].clear()
    world["ctx"].version_id = new_id
    run(world["ctx"], orchestrator.translate_job, "translate",
        {"source_version_id": s.version_id, "target_language": "ta-IN", "translate_board": False})
    assert world["asked"] == [("translate", "anthropic")]
    assert {c["model"] for c in world["providers"].llm.calls if c["schema"] == "GenTranslation"} == {CLAUDE["script"]}
    meta = get_version(new_id).generation_meta
    assert meta["models"] == {"translate": CLAUDE["script"], "provider": "anthropic"}
    assert meta["options"]["llm_provider"] == "anthropic" and meta["options"]["language"] == "ta-IN"


def test_manim_requests_carry_the_lecture_engine(job_ctx, providers, fast_audio) -> None:
    seeded = seed_project(job_ctx.assets.storage, b"source", "text/plain", "s.txt")
    job_ctx.project_id = seeded.project_id
    sp = Screenplay.model_validate({"language": "en-IN", "scenes": [
        {"id": "sim", "type": "simulation", "title": "Animated",
         "manim": {"code": "class A(AadhiScene):\n    def construct(self):\n        self.finish()"},
         "beats": [{"id": "sim-b1", "narration": "Watch the first step.", "visual_cue": "dot"}]},
    ]})
    asyncio.run(build_assets_detailed(job_ctx, sp, GenerationOptions(llm_provider="anthropic")))
    asyncio.run(build_assets_detailed(job_ctx, sp, GenerationOptions()))
    assert [r.llm_provider for r in providers.renderer.requests] == ["anthropic", None]
