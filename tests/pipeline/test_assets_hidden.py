"""Hidden scenes cost nothing in the asset stage: no provider call, never stale, earlier assets kept
(``SceneBase.hidden``, ``aadhi.pipeline.assets``)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.pipeline.assets import build_assets_detailed, scene_hash, stale_scene_ids
from aadhi.pipeline.base import GenerationOptions
from aadhi.schemas.screenplay import Screenplay
from tests.pipeline.dbutil import seed_project
from tests.pipeline.test_assets import make_screenplay

# every scene that would call a provider: TTS (all with beats), image panel, Manim, AI video, GIF
COSTLY = ("intro", "sim", "panel", "quiz", "video1", "video2", "play", "gif")


@pytest.fixture()
def env(job_ctx, providers, fast_audio):
    seeded = seed_project(job_ctx.assets.storage, b"source", "text/plain", "s.txt")
    job_ctx.project_id = seeded.project_id
    job_ctx.settings = job_ctx.settings.model_copy(update={"video_provider": "fake"})
    gifs: list[str] = []
    search = providers.gif.search

    async def counted(query: str, **kw: Any):
        gifs.append(query)
        return await search(query, **kw)

    providers.gif.search = counted  # type: ignore[method-assign]
    return {"ctx": job_ctx, "providers": providers, "gifs": gifs,
            "opts": GenerationOptions(allow_ai_video=True, max_ai_videos=2, allow_gifs=True)}


def hide(sp: Screenplay, ids: tuple[str, ...], **edits: Any) -> Screenplay:
    data = sp.model_dump(mode="json")
    for scene in data["scenes"]:
        if scene["id"] in ids:
            scene["hidden"] = True
            scene.update(edits.get(scene["id"], {}))
    return Screenplay.model_validate(data)


def build(env: dict[str, Any], sp: Screenplay, **kw: Any):
    return asyncio.run(build_assets_detailed(env["ctx"], sp, env["opts"], **kw))


def calls(env: dict[str, Any]) -> tuple[int, int, int, int, int]:
    p = env["providers"]
    return len(p.tts.texts), len(p.image.prompts), len(p.video.prompts), len(p.renderer.requests), len(env["gifs"])


def test_a_lecture_whose_costly_scenes_are_hidden_calls_no_provider(env):
    sp = hide(make_screenplay(None), COSTLY)
    res = build(env, sp)
    assert calls(env) == (0, 0, 0, 0, 0)
    assert env["ctx"].usages == []
    assert res.built == ["card"] and res.reused == []  # only the silent chapter card is "built" (nothing to pay)
    m = res.manifest
    assert set(m.audio) == {"card"} and set(m.media) == {"card"} and set(m.scene_hashes) == {"card"}
    assert m.stale_scenes == [] and stale_scene_ids(sp, m) == []


def test_named_hidden_scenes_are_not_built_either(env):
    sp = make_screenplay(None)
    first = build(env, sp).manifest
    before = calls(env)
    res = build(env, hide(sp, ("video1", "sim")), previous=first, scene_ids=["video1", "sim"])  # an explicit rebuild
    assert calls(env) == before and res.built == []
    assert res.manifest.media["video1"] == first.media["video1"] and res.manifest.stale_scenes == []


def test_hiding_keeps_the_built_assets_and_showing_again_needs_no_rebuild(env):
    sp = make_screenplay(None)
    first = build(env, sp).manifest
    before = calls(env)
    hidden = hide(sp, ("sim", "video1", "intro"))
    assert all(scene_hash(a) == scene_hash(b) for a, b in zip(sp.scenes, hidden.scenes, strict=True))
    second = build(env, hidden, previous=first)
    assert calls(env) == before
    assert second.manifest == first  # carried untouched, never stale: the build is complete
    assert sorted(second.built) == [] and sorted(second.reused) == sorted(s.id for s in sp.scenes
                                                                         if s.id not in ("sim", "video1", "intro"))
    third = build(env, sp, previous=second.manifest)  # shown again: everything is reused
    assert calls(env) == before and third.built == [] and third.manifest == first


def test_a_scene_edited_while_hidden_is_rebuilt_only_once_shown(env):
    sp = make_screenplay(None)
    first = build(env, sp).manifest
    n_tts = len(env["providers"].tts.texts)
    data = sp.model_dump(mode="json")
    data["scenes"][0]["beats"][1]["narration"] = "It has exactly three terminals."
    edited = Screenplay.model_validate(data)
    hidden = hide(edited, ("intro",))
    res = build(env, hidden, previous=first)
    assert len(env["providers"].tts.texts) == n_tts and res.built == []
    assert res.manifest.stale_scenes == []  # a hidden scene never makes the build incomplete
    assert res.manifest.audio["intro"] == first.audio["intro"]  # kept with its earlier hash
    assert stale_scene_ids(hidden, res.manifest) == []
    assert stale_scene_ids(edited, res.manifest) == ["intro"]  # shown again: stale until built
    shown = build(env, edited, previous=res.manifest)
    assert shown.built == ["intro"] and env["providers"].tts.texts[n_tts:] == ["It has exactly three terminals."]


def test_hidden_scene_whose_assets_are_gone_is_dropped_not_rebuilt(env):
    sp = make_screenplay(None)
    first = build(env, sp).manifest
    before = calls(env)
    from aadhi.db import session_scope
    from aadhi.models import Asset

    with session_scope() as db:
        db.query(Asset).filter(Asset.key == first.audio["quiz"].asset_key).delete()
    res = build(env, hide(sp, ("quiz",)), previous=first)
    assert calls(env) == before and res.built == []
    assert "quiz" not in res.manifest.audio and "quiz" not in res.manifest.media
    assert res.manifest.stale_scenes == []


def test_min_seconds_is_a_timing_choice_that_never_rebuilds(env):
    sp = make_screenplay(None)
    first = build(env, sp).manifest
    before = calls(env)
    data = sp.model_dump(mode="json")
    data["scenes"][2]["min_seconds"] = 30
    held = Screenplay.model_validate(data)
    assert stale_scene_ids(held, first) == []
    res = build(env, held, previous=first)
    assert calls(env) == before and res.built == [] and res.manifest == first
