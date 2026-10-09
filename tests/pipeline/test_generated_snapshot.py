"""The generated snapshot (``aadhi.changes``): kept by ``generate_lecture`` and ``translate``, never replaced by scene
regeneration, teacher edits or builds, and never a reason for a job to fail."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi import changes
from aadhi.db import compare_and_set, session_scope
from aadhi.models import ProjectVersion
from aadhi.pipeline import fake_content, orchestrator
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import get_version, seed_project
from tests.pipeline.fakes import install_providers
from tests.pipeline.sources import SAMPLE_MARKDOWN

OPTIONS = GenerationOptions(target_minutes=6, quiz_every_n_concepts=2)


@pytest.fixture()
def world(job_ctx, monkeypatch, fast_audio):
    changes.clear_cache()
    providers = install_providers(monkeypatch, real_timeline=True)
    seeded = seed_project(job_ctx.assets.storage, SAMPLE_MARKDOWN.encode("utf-8"), "text/markdown", "ohm.md",
                          options=OPTIONS.model_dump(mode="json"))
    job_ctx.project_id = seeded.project_id
    job_ctx.version_id = seeded.version_id
    yield {"ctx": job_ctx, "providers": providers, "seeded": seeded}
    changes.clear_cache()


def run(ctx: Any, handler, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.kind = kind
    ctx.payload = payload
    return asyncio.run(handler(ctx))


def generate(world: dict[str, Any]) -> dict[str, Any]:
    s = world["seeded"]
    body = {"source_document_id": s.source_id, "options": OPTIONS.model_dump(mode="json"), "base_revision": 1}
    return run(world["ctx"], orchestrator.generate_lecture, "generate_lecture", body)


def snapshot_of(world: dict[str, Any], version: ProjectVersion) -> Screenplay | None:
    return changes.load_generated(world["ctx"].assets, version.generation_meta)


def test_generate_keeps_the_screenplay_as_written(world):
    generate(world)
    v = get_version(world["seeded"].version_id)
    key = v.generation_meta[changes.SNAPSHOT_META_KEY]
    asset = world["ctx"].assets.get(key)
    assert asset.kind == "snapshot" and asset.storage_key.startswith("private/") and asset.mime == "application/json"
    assert snapshot_of(world, v).model_dump(mode="json") == v.screenplay
    assert "generated" not in v.screenplay  # kept out of the screenplay: hashes and cache keys are unchanged
    assert changes.changes(snapshot_of(world, v), Screenplay.model_validate(v.screenplay), v.generation_meta)[
        "available"]


def test_regeneration_edits_and_builds_keep_the_first_snapshot(world):
    generate(world)
    s = world["seeded"]
    v = get_version(s.version_id)
    key = v.generation_meta[changes.SNAPSHOT_META_KEY]
    sp = Screenplay.model_validate(v.screenplay)
    target = next(x for x in sp.scenes if x.type == "content")

    def responder(prompt, schema):
        out = fake_content.board_responder(prompt, schema)
        if "Teacher's instructions for this rewrite" in prompt:
            out["title"] = "Regenerated scene"
        return out

    world["providers"].llm.on("GenBoardScene", responder)
    run(world["ctx"], orchestrator.regenerate_scene, "regenerate_scene",
        {"scene_id": target.id, "instructions": "Use a cricket example.", "base_revision": 2})
    v = get_version(s.version_id)
    assert v.generation_meta[changes.SNAPSHOT_META_KEY] == key
    rows = {r["scene_id"]: r for r in changes.changes(snapshot_of(world, v), Screenplay.model_validate(v.screenplay),
                                                      v.generation_meta)["scenes"]}
    assert rows[target.id]["status"] == "edited" and rows[target.id]["history"] == 1
    data = v.screenplay
    data["scenes"][0]["title"] = "Teacher title"
    with session_scope() as db:  # the API's PUT /screenplay
        assert compare_and_set(db, ProjectVersion, v.id, {"revision": 3}, {"screenplay": data, "revision": 4})
    run(world["ctx"], orchestrator.build_assets_job, "build_assets", {"scene_ids": None, "base_revision": 4})
    assert get_version(s.version_id).generation_meta[changes.SNAPSHOT_META_KEY] == key


def test_translate_keeps_a_snapshot_of_the_new_version(world):
    generate(world)
    s = world["seeded"]
    source_key = get_version(s.version_id).generation_meta[changes.SNAPSHOT_META_KEY]
    with session_scope() as db:
        new = ProjectVersion(project_id=s.project_id, number=2, status="generating", revision=1,
                             source_version_id=s.version_id)
        db.add(new)
        db.flush()
        new_id = new.id
    world["ctx"].version_id = new_id
    run(world["ctx"], orchestrator.translate_job, "translate",
        {"source_version_id": s.version_id, "target_language": "ta-IN", "translate_board": False})
    v = get_version(new_id)
    key = v.generation_meta[changes.SNAPSHOT_META_KEY]
    assert key != source_key and snapshot_of(world, v).model_dump(mode="json") == v.screenplay
    assert snapshot_of(world, v).language == "ta-IN"


def test_a_storage_failure_never_fails_the_lecture(world, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(changes, "store_snapshot", broken)
    generate(world)
    v = get_version(world["seeded"].version_id)
    assert v.status == "ready" and changes.SNAPSHOT_META_KEY not in v.generation_meta
