"""Visual Review in the asset stage: "new AI version" variants (keys unchanged at 0), the Pollinations seed hint,
confirmed paid retries, teacher choices never replaced silently, library pictures instead of generated ones
(``prefer_library_visuals``) and generated media saved to the owner's library. Offline stand-ins only."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.pipeline import assets as assets_mod
from aadhi.pipeline.assets import (
    CONFIRM_PAID_KEY,
    build_assets_detailed,
    carry_visual_variant,
    scene_hash,
)
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers.operations import PAYLOAD_KEY
from aadhi.schemas.screenplay import Screenplay
from aadhi.storage.assets import Produced, bytes_key, compute_key, media_key
from tests.helpers import FakeJobContext
from tests.pipeline import test_assets_chain as chain_tests
from tests.pipeline.dbutil import seed_project
from tests.pipeline.fakes import png_bytes
from tests.providers.media_fakes import Crash

# The paid, resumable video stand-ins and their restart helpers (shared with the provider chain tests).
chain, veo, _clean_health = chain_tests.chain, chain_tests.veo, chain_tests._clean_health
_payload, _restart = chain_tests._payload, chain_tests._restart


def beat(i: str, text: str) -> dict[str, Any]:
    return {"id": i, "narration": text}


def panel_scene(sid: str = "a", prompt: str = "a transistor on a breadboard", **panel: Any) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": f"Scene {sid}", "beats": [beat(f"{sid}-b1", "Look at this.")],
            "side_panel": {"kind": "image", "image_prompt": prompt, "rationale": "a real part", **panel}}


def video_scene(sid: str = "lab", **extra: Any) -> dict[str, Any]:
    return {"id": sid, "type": "ai_video", "title": "Lab", "video_prompt": "a lathe cutting metal", "rationale": "real",
            "fallback_image_prompt": "a lathe", "beats": [beat(f"{sid}-b1", "See the lathe.")], **extra}


def screenplay(*scenes: dict[str, Any]) -> Screenplay:
    return Screenplay.model_validate({"language": "en-IN", "scenes": list(scenes)})


def build(ctx: Any, sp: Screenplay, opts: GenerationOptions | None = None, **kw: Any):
    return asyncio.run(build_assets_detailed(ctx, sp, opts or GenerationOptions(), **kw))


@pytest.fixture()
def env(job_ctx, providers, fast_audio):
    return {"ctx": job_ctx, "providers": providers}


# --- variants: keys ------------------------------------------------------------------------------------


def test_variant_zero_keeps_every_existing_key_and_scene_hash(env):
    """Golden: without a variant nothing changes (no image of any lecture is generated or billed again)."""
    sp = screenplay(panel_scene())
    res = build(env["ctx"], sp)
    key = res.manifest.media["a"].side_panel.asset_key
    full = f"a transistor on a breadboard. {env['ctx'].settings.image_style_suffix}".strip()
    legacy = compute_key("image", {"prompt": full, "provider": "fake", "model": "", "aspect": "4:3"})
    assert key == legacy == media_key("image", full, provider="fake", model="", aspect="4:3")
    dumped = sp.scenes[0].model_dump(mode="json")
    assert "variant" not in dumped["side_panel"]  # left out of stored screenplays and scene hashes
    explicit = screenplay(panel_scene(variant=0))
    assert scene_hash(explicit.scenes[0]) == scene_hash(sp.scenes[0])
    assert "variant" not in env["ctx"].assets.get(key).meta


def test_a_new_image_version_is_a_new_asset_and_keeps_the_old_one(env):
    first = build(env["ctx"], screenplay(panel_scene())).manifest
    old_key = first.media["a"].side_panel.asset_key
    edited = screenplay(panel_scene(variant=1))
    assert scene_hash(edited.scenes[0]) != first.scene_hashes["a"]  # only that scene is stale
    res = build(env["ctx"], edited, previous=first)
    new_key = res.manifest.media["a"].side_panel.asset_key
    assert res.built == ["a"] and new_key != old_key
    assert len(env["providers"].image.prompts) == 2  # generated again, same prompt
    assert env["ctx"].assets.get(old_key) is not None  # the earlier version stays cached
    assert env["ctx"].assets.get(new_key).meta["variant"] == 1
    full = f"a transistor on a breadboard. {env['ctx'].settings.image_style_suffix}".strip()
    assert new_key == compute_key("image", {"prompt": full, "provider": "fake", "model": "", "aspect": "4:3",
                                            "variant": 1})
    # back to the first version: cached, nothing generated
    again = build(env["ctx"], screenplay(panel_scene()), previous=res.manifest)
    assert again.manifest.media["a"].side_panel.asset_key == old_key and len(env["providers"].image.prompts) == 2


def test_a_new_video_version_changes_the_clip_key_and_its_still(env):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"video_provider": "fake"})
    opts = GenerationOptions(allow_ai_video=True, max_ai_videos=1)
    first = build(ctx, screenplay(video_scene()), opts).manifest
    res = build(ctx, screenplay(video_scene(variant=2)), opts, previous=first)
    old, new = first.media["lab"].main, res.manifest.media["lab"].main
    assert old.source == new.source == "veo" and old.asset_key != new.asset_key
    assert ctx.assets.get(new.asset_key).meta["variant"] == 2
    assert len(env["providers"].video.prompts) == 2
    # AI video off for the lecture: the generated still is a new version too
    still_0 = build(ctx, screenplay(video_scene()), GenerationOptions()).manifest.media["lab"].main
    still_2 = build(ctx, screenplay(video_scene(variant=2)), GenerationOptions()).manifest.media["lab"].main
    assert still_0.source == still_2.source == "fallback" and still_0.asset_key != still_2.asset_key


def test_variant_is_validated_and_never_negative():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        screenplay(panel_scene(variant=-1))
    with pytest.raises(ValidationError):
        screenplay(video_scene(variant=100))


def test_carry_visual_variant_keeps_a_new_version_when_the_request_is_unchanged():
    old = screenplay(panel_scene(variant=3), video_scene(variant=2))
    same = screenplay(panel_scene(), video_scene())
    other = screenplay(panel_scene(prompt="a diode"), video_scene(video_prompt="a drill"))
    assert carry_visual_variant(old.scenes[0], same.scenes[0]).side_panel.variant == 3
    assert carry_visual_variant(old.scenes[1], same.scenes[1]).variant == 2
    assert carry_visual_variant(old.scenes[0], other.scenes[0]).side_panel.variant == 0
    assert carry_visual_variant(old.scenes[1], other.scenes[1]).variant == 0


def test_a_rewritten_scene_keeps_its_new_version():
    """``regenerate_scene`` and repair rewrites go through ``repair.carry_over``: an unchanged request keeps the
    teacher's new version (the rewrite never brings the first picture back); a changed request starts at 0."""
    from aadhi.pipeline.repair import carry_over

    old = screenplay(panel_scene(variant=3), video_scene(variant=2))
    same = screenplay(panel_scene(prompt="  a transistor   on a breadboard "), video_scene())  # same once normalised
    other = screenplay(panel_scene(prompt="a diode"), video_scene(video_prompt="a drill"))
    assert carry_over(old.scenes[0], same.scenes[0]).side_panel.variant == 3
    assert carry_over(old.scenes[1], same.scenes[1]).variant == 2
    assert carry_over(old.scenes[0], other.scenes[0]).side_panel.variant == 0
    assert carry_over(old.scenes[1], other.scenes[1]).variant == 0
    # without a new version nothing changes (default output)
    plain = screenplay(panel_scene())
    kept = carry_over(plain.scenes[0], same.scenes[0])
    assert kept.side_panel.variant == 0 and "variant" not in kept.model_dump(mode="json")["side_panel"]


# --- Pollinations: the variant is a seed hint ----------------------------------------------------------


def test_pollinations_seed_changes_only_with_a_variant(app_env, monkeypatch):
    from aadhi.providers._common import stable_seed, style_prompt
    from aadhi.providers.image import pollinations
    from tests.providers.media_fakes import gradient_png

    provider = pollinations.PollinationsImage(app_env)
    seen: list[dict[str, Any]] = []

    async def fetch(url: str, params: dict[str, Any]) -> bytes:
        seen.append(dict(params))
        return gradient_png(256, 192)

    monkeypatch.setattr(provider, "_fetch", fetch)
    asyncio.run(provider.generate("a resistor", aspect="4:3"))
    asyncio.run(provider.generate("a resistor", aspect="4:3", variant=1))
    styled = style_prompt("a resistor", app_env)
    assert seen[0]["seed"] == stable_seed(styled)  # unchanged default
    assert seen[1]["seed"] != seen[0]["seed"]
    assert assets_mod._accepts_variant(provider) is True


def test_only_providers_taking_a_variant_get_one(env, monkeypatch):
    from aadhi.pipeline import integrations

    received: list[Any] = []

    class WithVariant:
        name = "fake"

        async def generate(self, prompt: str, *, aspect: str = "16:9", on_usage: Any = None, variant: int = 0) -> Any:
            received.append(variant)
            return await env["providers"].image.generate(prompt, aspect=aspect, on_usage=on_usage)

    # the default fake takes no variant: it is called without one (no TypeError)
    res = build(env["ctx"], screenplay(panel_scene(variant=1)))
    assert res.manifest.media["a"].side_panel is not None and len(env["providers"].image.prompts) == 1
    monkeypatch.setattr(integrations, "get_image", lambda settings: WithVariant())
    build(env["ctx"], screenplay(panel_scene("b", "a capacitor", variant=4), panel_scene("c", "a coil")))
    assert sorted(received) == [0, 4]


# --- confirmed paid retry of an ambiguous clip -----------------------------------------------------------


def test_a_confirmed_scene_submits_an_ambiguous_paid_clip_again(chain, veo):
    sp = screenplay(video_scene())
    chain.primary_video.crash_before_answer = True
    with pytest.raises(Crash):
        build(chain.ctx, sp, GenerationOptions(allow_ai_video=True))
    chain.primary_video.crash_before_answer = False
    # unconfirmed: never submitted again (the existing guard)
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    res = build(ctx2, sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 1 and ("video.ambiguous_submission", "lab") in {(i.code, i.scene_id) for i in res.issues}
    # confirmed for another scene only: still not submitted
    ctx3 = _restart(chain.ctx, chain.ctx.job_id)
    ctx3.payload[CONFIRM_PAID_KEY] = ["other"]
    build(ctx3, sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 1
    # the consent is for an earlier job's submission: within the job that made it, it does not apply
    ctx_same = _restart(chain.ctx, chain.ctx.job_id)
    ctx_same.payload[CONFIRM_PAID_KEY] = ["lab"]
    build(ctx_same, sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 1
    # the teacher confirmed this scene: a new build job (Visual Review) that sees the earlier job's record
    # (here copied, as a taken-over claim or a retry carries it) submits once more and shows the clip
    earlier = _payload(chain.ctx.job_id)[PAYLOAD_KEY]
    job_b = chain_tests._job_row({PAYLOAD_KEY: earlier, CONFIRM_PAID_KEY: ["lab"]})
    ctx4 = FakeJobContext(settings=chain.ctx.settings, assets=chain.ctx.assets, job_id=job_b, attempt=1,
                          payload=_payload(job_b), user_id=chain.ctx.user_id)
    res = build(ctx4, sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 2 and not res.manifest.media["lab"].main_is_fallback
    assert not [i for i in res.issues if i.code == "video.ambiguous_submission"]
    assert next(iter(_payload(job_b)[PAYLOAD_KEY].values()))["status"] in ("submitted", "completed")
    assert any("teacher confirmed" in e["message"] for e in ctx4.events if e["type"] == "log")


def test_a_confirmed_submission_that_stops_again_needs_a_new_confirmation(chain, veo):
    """The consent covers the earlier job's submission once: when the confirmed submission itself stops before its
    answer, the job's next attempt does not pay again unasked, and a retry of the job does not carry the consent."""
    from aadhi.db import session_scope
    from aadhi.jobs.queue import ONE_JOB_PAYLOAD_KEYS, retry_job
    from aadhi.models import Job

    assert CONFIRM_PAID_KEY in ONE_JOB_PAYLOAD_KEYS
    sp = screenplay(video_scene())
    chain.primary_video.crash_before_answer = True
    with pytest.raises(Crash):
        build(chain.ctx, sp, GenerationOptions(allow_ai_video=True))
    job_b = chain_tests._job_row({PAYLOAD_KEY: _payload(chain.ctx.job_id)[PAYLOAD_KEY], CONFIRM_PAID_KEY: ["lab"]})
    ctx_b = FakeJobContext(settings=chain.ctx.settings, assets=chain.ctx.assets, job_id=job_b, attempt=1,
                           payload=_payload(job_b), user_id=chain.ctx.user_id)
    with pytest.raises(Crash):  # the confirmed submission stops before its answer too
        build(ctx_b, sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 2
    chain.primary_video.crash_before_answer = False
    res = build(_restart(ctx_b, job_b), sp, GenerationOptions(allow_ai_video=True))
    assert veo.submits == 2 and ("video.ambiguous_submission", "lab") in {(i.code, i.scene_id) for i in res.issues}
    with session_scope() as db:
        db.get(Job, job_b).status = "failed"
    with session_scope() as db:
        retried = retry_job(db, job_b, user_id=None)
        assert CONFIRM_PAID_KEY not in (retried.payload or {}) and PAYLOAD_KEY in (retried.payload or {})


# --- teacher choices are never replaced silently ---------------------------------------------------------


def test_a_missing_upload_is_reported_as_override_missing(env):
    sp = Screenplay.model_validate({"language": "en-IN", "scenes": [
        panel_scene(override_asset_key="upload-gone"),
        {"id": "sim", "type": "simulation", "title": "Sim", "override_asset_key": "upload-gone2",
         "manim": {"code": "class A(AadhiScene):\n    pass"}, "beats": [beat("sim-b1", "Watch.")]},
        {"id": "play", "type": "interactive", "title": "Play", "p5_code": "function setup(){}",
         "poster_override_asset_key": "upload-gone3", "beats": [beat("play-b1", "Try it.")]},
    ]})
    res = build(env["ctx"], sp)
    missing = {(i.scene_id, i.severity) for i in res.issues if i.code == "assets.override_missing"}
    assert missing == {("a", "warning"), ("sim", "warning"), ("play", "warning")}
    assert not [i for i in res.issues if i.code == "assets.media_degraded" and "uploaded" in i.message]
    assert res.manifest.media["a"].side_panel.source == "image"  # the fallback itself is kept


# --- library pictures (prefer_library_visuals) and saving generated media ---------------------------------


def _library_item(ctx: Any, user_id: int, title: str) -> str:
    from aadhi import library
    from aadhi.db import session_scope

    data = png_bytes(seed=title)
    asset = ctx.assets.put(bytes_key("upload", data), "upload", Produced(data=data, mime="image/png", width=64,
                                                                         height=48))
    with session_scope() as db:
        library.add_item(db, user_id=user_id, asset_key=asset.key, kind="image", source="upload", title=title)
    return asset.key


@pytest.fixture()
def owned(env):
    seeded = seed_project(env["ctx"].assets.storage, b"source", "text/plain", "s.txt")
    env["ctx"].user_id, env["ctx"].project_id = seeded.user_id, seeded.project_id
    return seeded


def _score(monkeypatch: Any, value: float) -> list[str]:
    """The real per-user ``library.match`` with its score replaced by ``value`` (the threshold is ours to test,
    the scoring is the library's)."""
    from aadhi import library

    texts: list[str] = []
    real = library.match

    def match(db: Any, user_id: int, text: str, kind: str | None, **kw: Any) -> Any:
        texts.append(text)
        return [(item, value) for item, _ in real(db, user_id, text, kind, **kw)]

    monkeypatch.setattr(library, "match", match)
    return texts


def test_a_good_library_match_replaces_the_generated_picture(env, owned, monkeypatch):
    key = _library_item(env["ctx"], owned.user_id, "transistor on a breadboard")
    texts = _score(monkeypatch, 0.8)
    sp = screenplay(panel_scene())
    res = build(env["ctx"], sp, GenerationOptions(prefer_library_visuals=True))
    panel = res.manifest.media["a"].side_panel
    assert panel.asset_key == key and panel.source == "upload"
    assert env["providers"].image.prompts == []  # nothing generated
    used = [i for i in res.issues if i.code == "assets.library_visual_used"]
    assert len(used) == 1 and used[0].severity == "info" and "transistor on a breadboard" in used[0].message
    assert texts and "breadboard" in texts[0]
    # off by default: generated as before
    res = build(env["ctx"], sp)
    assert res.manifest.media["a"].side_panel.source == "image" and len(env["providers"].image.prompts) == 1


def test_a_weak_match_or_a_requested_new_version_is_generated(env, owned, monkeypatch):
    _library_item(env["ctx"], owned.user_id, "transistor on a breadboard")
    _score(monkeypatch, 0.59)
    opts = GenerationOptions(prefer_library_visuals=True)
    res = build(env["ctx"], screenplay(panel_scene()), opts)
    assert res.manifest.media["a"].side_panel.source == "image"
    _score(monkeypatch, 0.95)
    res = build(env["ctx"], screenplay(panel_scene(variant=1)), opts)
    assert res.manifest.media["a"].side_panel.source == "image"  # the teacher asked for a new AI version
    assert not [i for i in res.issues if i.code == "assets.library_visual_used"]


def test_another_users_library_is_never_used(env, owned, monkeypatch):
    from aadhi.db import session_scope
    from aadhi.models import User

    with session_scope() as db:
        other = User(username="someone-else", password_hash="x", role="editor")
        db.add(other)
        db.flush()
        other_id = other.id
    _library_item(env["ctx"], other_id, "transistor on a breadboard")
    _score(monkeypatch, 0.99)
    res = build(env["ctx"], screenplay(panel_scene()), GenerationOptions(prefer_library_visuals=True))
    assert res.manifest.media["a"].side_panel.source == "image"


def test_prefer_library_visuals_is_left_out_of_stored_options():
    assert "prefer_library_visuals" not in GenerationOptions().model_dump(mode="json")
    assert GenerationOptions(prefer_library_visuals=True).model_dump(mode="json")["prefer_library_visuals"] is True


def _library_rows(user_id: int) -> list[Any]:
    from sqlalchemy import select

    from aadhi.db import session_scope
    from aadhi.models import LibraryItem

    with session_scope() as db:
        rows = list(db.execute(select(LibraryItem).where(LibraryItem.user_id == user_id)).scalars())
        for r in rows:
            db.expunge(r)
        return rows


def test_generated_media_is_saved_to_the_owners_library(env, owned):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"video_provider": "fake", "library_auto_save_generated": True})
    sp = screenplay(panel_scene(), video_scene())
    res = build(ctx, sp, GenerationOptions(allow_ai_video=True, max_ai_videos=1))
    rows = {r.asset_key: r for r in _library_rows(owned.user_id)}
    image = rows[res.manifest.media["a"].side_panel.asset_key]
    clip = rows[res.manifest.media["lab"].main.asset_key]
    assert (image.kind, image.source, image.origin_project_id, image.origin_scene_id) == (
        "image", "generated", owned.project_id, "a")
    assert image.prompt == "a transistor on a breadboard" and image.provider == "fake"
    assert (clip.kind, clip.origin_scene_id, clip.prompt) == ("video", "lab", "a lathe cutting metal")


def test_saving_to_the_library_is_off_with_the_setting_and_never_fails_the_build(env, owned, monkeypatch):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"library_auto_save_generated": False})
    build(ctx, screenplay(panel_scene()))
    assert _library_rows(owned.user_id) == []
    ctx.settings = ctx.settings.model_copy(update={"library_auto_save_generated": True})

    from aadhi import library

    def broken(*a: Any, **kw: Any) -> None:
        raise RuntimeError("library down")

    monkeypatch.setattr(library, "record_generated", broken)
    res = build(ctx, screenplay(panel_scene("b", "a diode")))
    assert res.manifest.media["b"].side_panel is not None and "b" in res.manifest.scene_hashes


def test_uploads_and_figures_are_not_saved_as_generated(env, owned):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"library_auto_save_generated": True})
    data = png_bytes(seed="mine")
    up = ctx.assets.put(bytes_key("upload", data), "upload", Produced(data=data, mime="image/png", width=64, height=48))
    build(ctx, screenplay(panel_scene(override_asset_key=up.key)))
    assert _library_rows(owned.user_id) == []


def test_without_a_job_owner_nothing_is_saved(job_ctx, providers, fast_audio):
    assert FakeJobContext(settings=job_ctx.settings, assets=job_ctx.assets).user_id is None
    res = build(job_ctx, screenplay(panel_scene()))  # FakeJobContext without a user: no library writes
    assert res.manifest.media["a"].side_panel is not None



# --- libraries are personal; automatic use is precise and planned once per build ------------------------


def _admin(name: str = "an-admin") -> int:
    from aadhi.db import session_scope
    from aadhi.models import User

    with session_scope() as db:
        admin = User(username=name, password_hash="x", role="admin")
        db.add(admin)
        db.flush()
        return admin.id


def test_an_admin_build_of_another_users_lecture_neither_uses_nor_fills_a_library(env, owned, monkeypatch):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"library_auto_save_generated": True})
    admin_id = _admin()
    _library_item(ctx, admin_id, "transistor on a breadboard")
    _library_item(ctx, owned.user_id, "transistor on a breadboard, the teacher's")
    _score(monkeypatch, 0.99)
    ctx.user_id = admin_id  # the admin enqueued the build (DBJobContext user_id = job.user_id)
    res = build(ctx, screenplay(panel_scene()), GenerationOptions(prefer_library_visuals=True))
    assert res.manifest.media["a"].side_panel.source == "image"
    assert not [i for i in res.issues if i.code == "assets.library_visual_used"]
    assert [r.source for r in _library_rows(admin_id)] == ["upload"]  # nothing generated was saved
    assert [r.source for r in _library_rows(owned.user_id)] == ["upload"]


def _generated_item(ctx: Any, user_id: int, prompt: str, project_id: int | None, scene_id: str = "x") -> str:
    from aadhi import library
    from aadhi.db import session_scope

    data = png_bytes(seed=f"gen:{prompt}:{project_id}")
    asset = ctx.assets.put(bytes_key("image", data), "image", Produced(data=data, mime="image/png", width=64,
                                                                       height=48))
    with session_scope() as db:
        library.add_item(db, user_id=user_id, asset_key=asset.key, kind="image", source="generated", prompt=prompt,
                         title=library.title_from_prompt(prompt, "image"), origin_project_id=project_id,
                         origin_scene_id=scene_id)
    return asset.key


def test_an_edited_prompt_makes_a_new_picture_even_with_the_old_one_in_the_library(env, owned):
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"library_auto_save_generated": True})
    opts = GenerationOptions(prefer_library_visuals=True)
    first = build(ctx, screenplay(panel_scene(prompt="Parallel circuit with a battery and two bulbs")), opts)
    old = first.manifest.media["a"].side_panel.asset_key
    assert [r.asset_key for r in _library_rows(owned.user_id)] == [old]  # saved by the build
    edited = screenplay(panel_scene(prompt="Parallel circuit with three bulbs and a switch"))
    res = build(ctx, edited, opts, previous=first.manifest)
    panel = res.manifest.media["a"].side_panel
    assert panel.asset_key != old and panel.source == "image" and len(env["providers"].image.prompts) == 2
    assert not [i for i in res.issues if i.code == "assets.library_visual_used"]
    # a narration-only edit: the same picture comes back from the cache, still an AI picture
    data = panel_scene(prompt="Parallel circuit with three bulbs and a switch")
    data["beats"][0]["narration"] = "Look at this circuit closely."
    again = build(ctx, screenplay(data), opts, previous=res.manifest)
    assert again.built == ["a"] and again.manifest.media["a"].side_panel.asset_key == panel.asset_key
    assert again.manifest.media["a"].side_panel.source == "image" and len(env["providers"].image.prompts) == 2


def test_media_generated_for_this_lecture_never_stands_in_but_another_lectures_does(env, owned):
    from aadhi.db import session_scope
    from aadhi.models import Project

    ctx = env["ctx"]
    with session_scope() as db:
        other = Project(owner_id=owned.user_id, title="Another lecture")
        db.add(other)
        db.flush()
        other_id = other.id
    mine = _generated_item(ctx, owned.user_id, "a transistor on a breadboard", owned.project_id, "z")
    opts = GenerationOptions(prefer_library_visuals=True)
    res = build(ctx, screenplay(panel_scene()), opts)
    assert res.manifest.media["a"].side_panel.asset_key != mine and len(env["providers"].image.prompts) == 1
    theirs = _generated_item(ctx, owned.user_id, "a transistor on a breadboard", other_id)
    res = build(ctx, screenplay(panel_scene("b", "a transistor on a breadboard")), opts)
    assert res.manifest.media["b"].side_panel.asset_key == theirs and len(env["providers"].image.prompts) == 1


def test_one_library_picture_serves_one_scene_and_the_matcher_is_loaded_once(env, owned, monkeypatch):
    from aadhi import library

    key = _library_item(env["ctx"], owned.user_id, "transistor on a breadboard")
    loads: list[int] = []
    real = library.LibraryMatcher.load.__func__

    def load(cls: Any, *a: Any, **kw: Any) -> Any:
        loads.append(1)
        return real(cls, *a, **kw)

    monkeypatch.setattr(library.LibraryMatcher, "load", classmethod(load))
    sp = screenplay(panel_scene("a"), panel_scene("b"), panel_scene("c", "a transistor on a breadboard, closer"))
    res = build(env["ctx"], sp, GenerationOptions(prefer_library_visuals=True))
    media = res.manifest.media
    assert media["a"].side_panel.asset_key == key  # the first scene in lecture order gets it
    assert media["b"].side_panel.source == "image" and media["c"].side_panel.source == "image"
    assert len([i for i in res.issues if i.code == "assets.library_visual_used"]) == 1
    assert loads == [1]


def test_a_scene_title_alone_never_brings_a_library_picture(env, owned):
    _library_item(env["ctx"], owned.user_id, "Ohm's law")
    data = panel_scene(prompt="A digital multimeter measuring current in a series circuit", title="Ohm's law")
    data["title"] = "Ohm's law"
    res = build(env["ctx"], screenplay(data), GenerationOptions(prefer_library_visuals=True))
    assert res.manifest.media["a"].side_panel.source == "image"


# --- a new version that cannot be made keeps the earlier one ---------------------------------------------


def test_a_new_version_on_a_server_without_image_generation_keeps_the_earlier_picture(env, monkeypatch):
    monkeypatch.setitem(assets_mod._KNOWN_MEDIA_PROVIDERS, "image", ("fake",))  # what made the earlier picture
    ctx = env["ctx"]
    first = build(ctx, screenplay(panel_scene())).manifest
    old = first.media["a"].side_panel.asset_key
    ctx.settings = ctx.settings.model_copy(update={"image_provider": "none"})
    res = build(ctx, screenplay(panel_scene(variant=1)), previous=first)
    media = res.manifest.media["a"]
    assert media.side_panel.asset_key == old and media.warnings == [assets_mod.NEW_VERSION_UNAVAILABLE]
    assert "a" in res.manifest.scene_hashes  # settled: not retried by every build
    # the teacher's own opt-out still shows nothing
    res = build(ctx, screenplay(panel_scene(variant=1)), GenerationOptions(allow_generated_images=False))
    assert res.manifest.media["a"].side_panel is None


def test_a_new_version_whose_provider_fails_keeps_the_earlier_picture(env, monkeypatch):
    from aadhi.pipeline import integrations
    from aadhi.providers.base import ProviderError

    ctx = env["ctx"]
    first = build(ctx, screenplay(panel_scene())).manifest
    old = first.media["a"].side_panel.asset_key

    class Down:
        name = "fake"

        async def generate(self, prompt: str, **kw: Any) -> Any:
            raise ProviderError("image backend down", provider="fake")

    monkeypatch.setattr(integrations, "get_image", lambda settings: Down())
    res = build(ctx, screenplay(panel_scene(variant=1)), previous=first)
    media = res.manifest.media["a"]
    assert media.side_panel.asset_key == old
    assert media.warnings and media.warnings[0].startswith("The new AI version could not be made")
    assert "a" not in res.manifest.scene_hashes  # transient: the next build tries the new version again
    with pytest.raises(Exception):  # without an earlier version the failure shows as before
        asyncio.run(assets_mod._Builder(ctx, screenplay(panel_scene("n", "a diode", variant=1)), GenerationOptions(),
                                        None, None, None).new_image_version("a diode", "4:3", "n", 1, []))


def test_a_new_clip_version_that_cannot_be_made_keeps_the_earlier_clip(env, monkeypatch):
    monkeypatch.setitem(assets_mod._KNOWN_MEDIA_PROVIDERS, "video", ("fake",))  # what made the earlier clip
    ctx = env["ctx"]
    ctx.settings = ctx.settings.model_copy(update={"video_provider": "fake"})
    opts = GenerationOptions(allow_ai_video=True, max_ai_videos=1)
    first = build(ctx, screenplay(video_scene()), opts).manifest
    old = first.media["lab"].main.asset_key
    env["providers"].video.fail = True
    res = build(ctx, screenplay(video_scene(variant=1)), opts, previous=first)
    main = res.manifest.media["lab"]
    assert main.main.asset_key == old and not main.main_is_fallback
    assert main.warnings[0].startswith("The new AI version could not be made")
    ctx.settings = ctx.settings.model_copy(update={"video_provider": "none"})
    res = build(ctx, screenplay(video_scene(variant=1)), opts, previous=first)
    assert res.manifest.media["lab"].main.asset_key == old
    assert assets_mod.NEW_VERSION_UNAVAILABLE in res.manifest.media["lab"].warnings
