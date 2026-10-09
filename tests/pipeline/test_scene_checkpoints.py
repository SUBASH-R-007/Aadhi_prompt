"""Per-scene checkpoints of the scene writer (``script.write_scenes_detailed(checkpoints=...)``): a retry
reuses finished scenes, writes only the others, and the lecture comes out exactly as without the hook."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from aadhi.pipeline import script
from aadhi.pipeline.checkpoint import SceneCheckpoints, StageCheckpoints
from aadhi.pipeline.plan import generate_plan
from aadhi.pipeline.script import scene_result_data, scene_result_from, write_scenes_detailed
from aadhi.providers.base import ProviderError, RateLimited

SCENE_SCHEMAS = ("GenBoardScene", "GenChapterCard", "GenQuiz", "GenSimulation", "GenAIVideo", "GenInteractive")


class MemoryCheckpoints:
    """The hook, in memory (stored as JSON, like the real checkpoints)."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, bool], str] = {}

    async def load(self, scene_id: str, attached: bool) -> dict[str, Any] | None:
        raw = self.store.get((scene_id, attached))
        return None if raw is None else json.loads(raw)

    async def save(self, scene_id: str, attached: bool, data: dict[str, Any]) -> None:
        self.store[(scene_id, attached)] = json.dumps(data, allow_nan=False)


def stop_at_scene(monkeypatch, llm, n: int, exc: Exception) -> dict[str, Any]:
    real = llm.generate_json
    state = {"scenes": 0, "on": True}

    async def flaky(**kwargs):
        if state["on"] and kwargs["schema"].__name__.startswith(SCENE_SCHEMAS):
            state["scenes"] += 1
            await asyncio.sleep(0)  # a real provider answers later
            if state["scenes"] == n:
                raise exc
        return await real(**kwargs)

    monkeypatch.setattr(llm, "generate_json", flaky)
    return state


def count_writes(monkeypatch) -> list[str]:
    real = script.write_scene
    ids: list[str] = []

    async def spy(ctx, lc, pos, **kwargs):
        ids.append(pos.planned.id)
        return await real(ctx, lc, pos, **kwargs)

    monkeypatch.setattr(script, "write_scene", spy)
    return ids


@pytest.fixture()
def planned(job_ctx, providers, templates, sample_ingest, options):
    job_ctx.settings.llm_max_parallel = 1  # one scene after another: a deterministic stopping point
    return asyncio.run(generate_plan(job_ctx, sample_ingest, options))


def write(job_ctx, planned, sample_ingest, options, hook=None):
    return asyncio.run(write_scenes_detailed(job_ctx, planned.plan, sample_ingest, options, lexicon=planned.lexicon,
                                             checkpoints=hook))


def test_a_resumed_scene_stage_gives_the_same_lecture(job_ctx, providers, planned, sample_ingest, options, monkeypatch):
    reference = write(job_ctx, planned, sample_ingest, options)
    n = len(reference.screenplay.scenes)
    hook = MemoryCheckpoints()
    stop = stop_at_scene(monkeypatch, providers.llm, 3, RateLimited("quota exhausted", provider="fake"))
    with pytest.raises(RateLimited):
        write(job_ctx, planned, sample_ingest, options, hook)
    kept = len(hook.store)
    assert 2 <= kept < n

    stop["on"] = False
    writes = count_writes(monkeypatch)
    resumed = write(job_ctx, planned, sample_ingest, options, hook)
    assert len(writes) == n - kept  # the finished scenes were not written (or paid for) again
    assert resumed.screenplay == reference.screenplay
    assert resumed.screenplay.model_dump(mode="json") == reference.screenplay.model_dump(mode="json")
    assert resumed.issues == reference.issues and resumed.fallback_scene_ids == reference.fallback_scene_ids
    assert len(hook.store) == n

    writes.clear()  # a third run finds every scene
    assert write(job_ctx, planned, sample_ingest, options, hook).screenplay == reference.screenplay and writes == []


def test_fallback_scenes_are_never_checkpointed(job_ctx, providers, planned, sample_ingest, options, monkeypatch):
    stop_at_scene(monkeypatch, providers.llm, 2, ProviderError("invalid output after retries", provider="fake"))
    hook = MemoryCheckpoints()
    res = write(job_ctx, planned, sample_ingest, options, hook)
    assert len(res.fallback_scene_ids) == 1
    stored = {scene_id for scene_id, _ in hook.store}
    assert res.fallback_scene_ids[0] not in stored
    assert stored == {s.id for s in res.screenplay.scenes} - set(res.fallback_scene_ids)


def test_unusable_checkpoint_data_is_written_again(job_ctx, providers, planned, sample_ingest, options, monkeypatch):
    reference = write(job_ctx, planned, sample_ingest, options)
    first, second = (s.id for s in reference.screenplay.scenes[:2])
    other = scene_result_data(script.SceneWriteResult(reference.screenplay.scenes[1]))
    hook = MemoryCheckpoints()
    hook.store[(first, False)] = json.dumps(other)  # a scene stored under another id
    hook.store[(second, False)] = json.dumps({"scene": {"id": second, "type": "nonsense"}})
    writes = count_writes(monkeypatch)
    assert write(job_ctx, planned, sample_ingest, options, hook).screenplay == reference.screenplay
    assert first in writes and second in writes
    assert scene_result_from(None, first) is None and scene_result_from({"issues": []}, first) is None


def test_scene_checkpoint_keys_cover_scene_inputs_and_attachment(job_ctx):
    job_ctx.job_id, job_ctx.attempt = 9, 2
    stages = StageCheckpoints(job_ctx)
    a, b = SceneCheckpoints(stages, "inputs-a"), SceneCheckpoints(stages, "inputs-b")
    keys = {stages.key("scene", ck._inputs(sid, att)) for ck in (a, b) for sid in ("s1", "s2") for att in (False, True)}
    assert len(keys) == 8
    assert stages.key("scene", a._inputs("s1", False)) == stages.key("scene", SceneCheckpoints(stages, "inputs-a")._inputs("s1", False))


def test_scene_checkpoints_round_trip_through_the_asset_store(job_ctx, providers, planned, sample_ingest, options):
    reference = write(job_ctx, planned, sample_ingest, options)
    job_ctx.job_id, job_ctx.attempt = 11, 1
    first = SceneCheckpoints(StageCheckpoints(job_ctx), "digest")
    scene = reference.screenplay.scenes[1]
    data = scene_result_data(script.SceneWriteResult(scene))
    asyncio.run(first.save(scene.id, False, data))
    assert asyncio.run(first.load(scene.id, False)) is None  # a first attempt never loads
    job_ctx.attempt = 2
    stages = StageCheckpoints(job_ctx)
    retry = SceneCheckpoints(stages, "digest")
    loaded = asyncio.run(retry.load(scene.id, False))
    assert scene_result_from(loaded, scene.id).scene == scene
    assert retry.reused == [scene.id] and stages.reused == ["scenes"]
    assert asyncio.run(retry.load(scene.id, True)) is None  # written without the original file: not this one
    assert stages.reused == ["scenes"]
