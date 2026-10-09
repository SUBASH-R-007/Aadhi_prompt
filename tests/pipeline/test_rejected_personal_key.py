"""A provider refusing the job owner's personal API key fails the job instead of being degraded.

Scene rewrites (``write_scene``) and translations normally turn a provider error into a fallback
scene or kept source text. For a refused personal key every call would fail the same way, so the
error propagates: ``orchestrator._guard`` marks the version failed (or restores it) and the worker
maps the wrapped ``ProviderError`` to ``personal_key_rejected`` (``credentials.personal_key_rejection``).
A refused server key keeps the old degrading behaviour. FakeJobContext + DB rows, no network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.credentials import personal_key_rejection
from aadhi.db import session_scope
from aadhi.jobs.base import FatalJobError
from aadhi.models import ProjectVersion
from aadhi.pipeline import orchestrator
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers.base import ProviderError
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)
PERSONAL = {"anthropic": "personal"}
SERVER = {"anthropic": "server"}


def refused(prompt: str, schema: Any) -> ProviderError:
    return ProviderError("anthropic: request failed (HTTP 401): invalid x-api-key", status=401, provider="anthropic")


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    providers = install_providers(monkeypatch, real_timeline=True)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    ctx = job_ctx
    ctx.kind, ctx.payload = "generate_lecture", {
        "source_document_id": seeded.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1}
    asyncio.run(orchestrator.generate_lecture(ctx))  # a ready lecture (revision 2) to work on
    return {"ctx": ctx, "providers": providers, "seeded": seeded}


def run(ctx: Any, handler, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.kind = kind
    ctx.payload = payload
    return asyncio.run(handler(ctx))


def content_scene_id(version_id: int) -> str:
    sp = Screenplay.model_validate(get_version(version_id).screenplay)
    return next(x.id for x in sp.scenes if x.type == "content")


def regenerate(world: dict[str, Any], sources: dict[str, str]) -> tuple[pytest.ExceptionInfo[FatalJobError], int]:
    """Regenerate a content scene with every scene call refused; returns the error and the scene calls made."""
    s = world["seeded"]
    llm = world["providers"].llm
    world["ctx"].key_sources = sources
    llm.on("GenBoardScene", refused)
    before = len(llm.calls_for("GenBoardScene"))
    with pytest.raises(FatalJobError) as info:
        run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
            {"scene_id": content_scene_id(s.version_id), "instructions": "Simpler.", "base_revision": 2})
    return info, len(llm.calls_for("GenBoardScene")) - before


def test_regenerate_scene_with_a_refused_personal_key_reports_the_key(world):
    info, calls = regenerate(world, PERSONAL)
    assert info.value.code == "provider"  # not "generation_failed" ("try different instructions")
    message = personal_key_rejection(info.value, PERSONAL)  # what the worker turns into personal_key_rejected
    assert message and "personal Anthropic Claude API key was rejected (HTTP 401)" in message
    assert calls == 1  # stopped at the first refusal
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and v.revision == 2  # the in-place job restores the version


def test_regenerate_scene_with_a_refused_server_key_still_falls_back(world):
    info, _ = regenerate(world, SERVER)
    assert info.value.code == "generation_failed"
    assert personal_key_rejection(info.value, SERVER) is None
    assert get_version(world["seeded"].version_id).status == "ready"


def translate(world: dict[str, Any], sources: dict[str, str]) -> tuple[int, Any]:
    s = world["seeded"]
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1,
                             source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    world["ctx"].version_id = new_id
    world["ctx"].key_sources = sources
    world["providers"].llm.on("GenTranslation", refused)
    payload = {"source_version_id": s.version_id, "target_language": "ta-IN", "translate_board": True}
    try:
        return new_id, run(world["ctx"], orchestrator.translate_job, "translate", payload)
    except FatalJobError as exc:
        return new_id, exc


def test_translate_with_a_refused_personal_key_fails_instead_of_shipping_source_text(world):
    new_id, outcome = translate(world, PERSONAL)
    assert isinstance(outcome, FatalJobError) and outcome.code == "provider"
    assert personal_key_rejection(outcome, PERSONAL)
    v = get_version(new_id)
    assert v.status == "failed" and v.screenplay is None  # never a "ready" untranslated version


def test_translate_with_a_refused_server_key_keeps_the_source_text_with_issues(world):
    new_id, outcome = translate(world, SERVER)
    assert outcome == {"version_id": new_id}
    v = get_version(new_id)
    assert v.status == "ready" and v.language == "ta-IN"
    codes = {i["code"] for i in v.issues}
    assert "translate.scene_failed" in codes and "translate.sheet_failed" in codes
