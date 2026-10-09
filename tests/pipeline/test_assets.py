"""build_assets with fake providers (+ fake renderer): manifest, fallbacks, incremental reuse."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.compose.base import SCENE_LEAD_SECONDS, SCENE_TAIL_SECONDS
from aadhi.pipeline.assets import build_assets, build_assets_detailed, scene_hash, stale_scene_ids
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers.base import ProviderError
from aadhi.schemas.manifest import AssetManifest
from aadhi.schemas.screenplay import Screenplay
from aadhi.storage.assets import Produced, bytes_key
from tests.pipeline.dbutil import asset_refs, seed_project
from tests.pipeline.fakes import png_bytes


def beat(i: str, text: str, **kw: Any) -> dict[str, Any]:
    return {"id": i, "narration": text, **kw}


def make_screenplay(fig_key: str | None, upload_key: str | None = None) -> Screenplay:
    return Screenplay.model_validate({
        "language": "en-IN",
        "lexicon": [{"written": "BJT", "spoken": "B J T"}],
        "figures": [{"id": "fig-1", "caption": "A circuit", "asset_key": fig_key}],
        "scenes": [
            {"id": "intro", "type": "content", "title": "Intro",
             "board": [{"id": "intro-i1", "kind": "figure", "figure_id": "fig-1", "caption": "Circuit"}],
             "beats": [beat("intro-b1", "Look at the BJT circuit here.", board_item_id="intro-i1", pause_after=1.0),
                       beat("intro-b2", "It has three terminals.")],
             "side_panel": {"kind": "image", "image_prompt": "a transistor on a breadboard", "rationale": "real part"}},
            {"id": "card", "type": "chapter_card", "title": "Part two", "beats": []},
            {"id": "sim", "type": "simulation", "title": "Animated",
             "manim": {"code": "class A(AadhiScene):\n    def construct(self):\n        self.finish()"},
             "beats": [beat("sim-b1", "Watch the first step.", visual_cue="dot"),
                       beat("sim-b2", "Now the second step.", visual_cue="line")]},
            {"id": "panel", "type": "content", "title": "Panel",
             "beats": [beat("panel-b1", "First we set the scene."), beat("panel-b2", "Then the animation starts.")],
             "side_panel": {"kind": "manim", "rationale": "motion", "show_from_beat_id": "panel-b2",
                            "manim": {"code": "class B(AadhiScene):\n    pass"}}},
            {"id": "quiz", "type": "quiz_checkpoint", "title": "Check", "question": "Which?", "options": ["a", "b"],
             "correct_index": 1, "countdown_seconds": 6,
             "beats": [beat("quiz-b1", "Which one is right?")], "reveal_beats": [beat("quiz-b2", "It is b.")]},
            {"id": "video1", "type": "ai_video", "title": "Lab", "video_prompt": "a lathe cutting metal",
             "rationale": "real", "fallback_image_prompt": "a lathe", "beats": [beat("video1-b1", "See the lathe.")]},
            {"id": "video2", "type": "ai_video", "title": "Plant", "video_prompt": "a power plant", "rationale": "real",
             "fallback_figure_id": "fig-1", "beats": [beat("video2-b1", "See the plant.")]},
            {"id": "play", "type": "interactive", "title": "Play", "p5_code": "function setup(){}",
             "poster_override_asset_key": upload_key, "beats": [beat("play-b1", "Try the slider.")]},
            {"id": "gif", "type": "content", "title": "Fun",
             "beats": [beat("gif-b1", "A little animation.")],
             "side_panel": {"kind": "gif", "gif_query": "electricity", "rationale": "fun"}},
        ],
    })


@pytest.fixture()
def env(job_ctx, providers, fast_audio):
    seeded = seed_project(job_ctx.assets.storage, b"source", "text/plain", "s.txt")
    job_ctx.project_id = seeded.project_id
    png = png_bytes(300, 200, "fig")
    fig = job_ctx.assets.put(bytes_key("figure", png), "figure", Produced(data=png, mime="image/png", width=300, height=200))
    up = png_bytes(64, 64, "poster")
    upload = job_ctx.assets.put(bytes_key("upload", up), "upload", Produced(data=up, mime="image/png", width=64, height=64))
    opts = GenerationOptions(allow_ai_video=True, max_ai_videos=1, allow_gifs=True)
    job_ctx.settings = job_ctx.settings.model_copy(update={"video_provider": "fake"})
    return {"ctx": job_ctx, "providers": providers, "sp": make_screenplay(fig.key, upload.key), "opts": opts,
            "project_id": seeded.project_id, "fig_key": fig.key, "upload_key": upload.key}


def build(env: dict[str, Any], sp: Screenplay | None = None, **kw: Any):
    return asyncio.run(build_assets_detailed(env["ctx"], sp or env["sp"], env["opts"], **kw))


def test_full_manifest(env):
    res = build(env)
    m, p = res.manifest, env["providers"]
    AssetManifest.model_validate(m.model_dump(mode="json"))
    sp = env["sp"]
    assert list(m.audio) == [s.id for s in sp.scenes]
    assert m.audio["card"].asset_key is None and m.audio["card"].duration == 0.0
    intro = m.audio["intro"]
    from aadhi.pipeline import integrations

    assert intro.asset_key.startswith("scene_audio-") and intro.provider == "fake"
    assert intro.voice == integrations.default_voice("fake", "en-IN", p.tts)
    assert [b.beat_id for b in intro.beats] == ["intro-b1", "intro-b2"]
    assert intro.beats[0].spoken_text.startswith("Look at the B J T circuit")  # lexicon applied for TTS only
    assert intro.beats[0].words[3].text == "BJT"  # captions keep the written tokens
    assert intro.beats[1].offset > intro.beats[0].offset + intro.beats[0].speech_duration + 1.0
    quiz = m.audio["quiz"]
    assert quiz.countdown_seconds == 6 and quiz.countdown_start is not None
    assert [b.phase for b in quiz.beats] == ["main", "reveal"]
    # media
    assert m.media["intro"].figures["intro-i1"].asset_key == env["fig_key"]
    assert m.media["intro"].side_panel.source == "image" and p.image.prompts[0].startswith("a transistor on a breadboard.")
    sim_req = p.renderer.requests[[r.target for r in p.renderer.requests].index("fullscreen")]
    sim_audio = m.audio["sim"]
    assert sim_req.beat_times == pytest.approx([SCENE_LEAD_SECONDS + b.offset for b in sim_audio.beats], abs=1e-3)
    assert sim_req.total_duration == pytest.approx(SCENE_LEAD_SECONDS + sim_audio.duration + SCENE_TAIL_SECONDS, abs=1e-3)
    assert m.media["sim"].main.source == "manim" and m.media["sim"].main.kind == "video"
    panel_req = next(r for r in p.renderer.requests if r.target == "panel")
    assert panel_req.beat_times[0] == 0.0 and len(panel_req.beat_times) == 1  # panel video starts at show time
    assert m.media["panel"].side_panel.source == "manim"
    assert m.media["video1"].main.source == "veo" and not m.media["video1"].main_is_fallback
    v2 = m.media["video2"]
    assert v2.main_is_fallback and v2.main.source == "fallback" and v2.main.asset_key == env["fig_key"]
    assert any("limit" in w for w in v2.warnings)
    assert m.media["play"].poster.asset_key == env["upload_key"]
    gif = m.media["gif"].side_panel
    assert gif.kind == "gif" and gif.external_url.startswith("https://") and gif.asset_key is None
    # bookkeeping
    assert set(m.scene_hashes) == {s.id for s in sp.scenes} and m.stale_scenes == []
    assert m.scene_hashes["intro"] == scene_hash(sp.scene_by_id("intro"), sp.lexicon)
    refs = asset_refs(env["project_id"])
    assert set(res.asset_keys) <= refs and intro.asset_key in refs and intro.beats[0].clip_asset_key in refs
    assert sorted(res.built) == sorted(s.id for s in sp.scenes) and not res.reused
    assert any(u.operation == "tts" for u in env["ctx"].usages)


def test_incremental_reuse_and_partial_rebuild(env):
    first = build(env).manifest
    p = env["providers"]
    n_tts, n_render = len(p.tts.texts), len(p.renderer.requests)
    again = build(env, previous=first)
    assert sorted(again.reused) == sorted(first.media) and not again.built
    assert len(p.tts.texts) == n_tts and len(p.renderer.requests) == n_render
    assert again.manifest.audio == first.audio
    # edit one beat of one scene: only that scene rebuilds, only the changed beat is synthesised
    data = env["sp"].model_dump(mode="json")
    data["scenes"][0]["beats"][1]["narration"] = "It has exactly three terminals."
    edited = Screenplay.model_validate(data)
    assert stale_scene_ids(edited, first) == ["intro"]
    third = build(env, sp=edited, previous=first)
    assert third.built == ["intro"]
    assert p.tts.texts[n_tts:] == ["It has exactly three terminals."]
    assert third.manifest.audio["intro"].beats[0].clip_asset_key == first.audio["intro"].beats[0].clip_asset_key


def test_scene_ids_limit_and_stale_marking(env):
    first = build(env).manifest
    data = env["sp"].model_dump(mode="json")
    data["scenes"][0]["beats"][1]["narration"] = "Changed narration for intro."
    data["scenes"][2]["beats"][0]["narration"] = "Changed narration for the simulation."
    edited = Screenplay.model_validate(data)
    res = build(env, sp=edited, previous=first, scene_ids=["sim"])
    assert res.built == ["sim"]
    assert res.manifest.stale_scenes == ["intro"]
    assert stale_scene_ids(edited, res.manifest) == ["intro"]


def test_voice_change_rebuilds(env):
    first = build(env).manifest
    env["opts"] = env["opts"].model_copy(update={"tts_voice": "other-voice"})
    res = build(env, previous=first)
    beats_scenes = [s.id for s in env["sp"].scenes if s.all_beats()]
    assert sorted(res.built) == sorted(beats_scenes)
    assert res.manifest.audio["intro"].voice == "other-voice"


def test_missing_reused_asset_triggers_rebuild(env):
    first = build(env).manifest
    stale_key = first.audio["quiz"].asset_key
    from aadhi.db import session_scope
    from aadhi.models import Asset

    with session_scope() as db:
        db.query(Asset).filter(Asset.key == stale_key).delete()
    res = build(env, previous=first)
    assert res.built == ["quiz"]


def test_failures_become_warnings_and_issues(env):
    env["providers"].renderer.fail = True
    env["providers"].video.fail = True

    async def broken_tts(text, **kw):
        raise RuntimeError("tts down")

    env["providers"].tts.synthesize = broken_tts  # type: ignore[method-assign]
    res = build(env)
    m = res.manifest
    codes = {(i.code, i.scene_id) for i in res.issues}
    assert ("assets.tts_failed", "intro") in codes and ("manim.render_failed", "sim") in codes
    assert m.media["sim"].main is None and any("animation" in w.lower() for w in m.media["sim"].warnings)
    assert m.media["video1"].main_is_fallback and m.media["video1"].main.source == "fallback"
    assert any("AI video failed" in w for w in m.media["video1"].warnings)
    assert "intro" not in m.audio and "intro" not in m.scene_hashes  # rebuilt next time
    assert all(i.severity in ("error", "warning", "info") for i in res.issues)


def test_manim_failure_issue_says_why_in_plain_words_and_carries_the_category(env):
    from aadhi.manim.base import FAILURE_MESSAGES

    env["providers"].renderer.fail = True
    env["providers"].renderer.fail_category = "timeout"
    res = build(env)
    failed = [i for i in res.issues if i.code == "manim.render_failed"]
    assert failed
    for issue in failed:
        assert FAILURE_MESSAGES["timeout"] in issue.message and "render crashed" not in issue.message
        assert issue.data is not None and issue.data.category == "timeout"
        assert issue.model_dump(mode="json")["data"] == {"category": "timeout"}
    others = [i.model_dump(mode="json") for i in res.issues if i.code != "manim.render_failed"]
    assert others and all("data" not in i for i in others)  # issues without detail keep their stored shape


def test_budget_exceeded_falls_back_to_still(env):
    env["ctx"].budget_usd = 0.0
    env["ctx"].cost_usd = 0.0

    def ensure(cost, provider=None):
        from aadhi.jobs.base import BudgetExceeded

        raise BudgetExceeded("too expensive")

    env["ctx"].ensure_budget = ensure  # type: ignore[method-assign]
    env["ctx"].budget_usd = None
    m = build(env).manifest
    assert m.media["video1"].main_is_fallback and any("budget" in w for w in m.media["video1"].warnings)
    assert env["providers"].video.prompts == []


def test_disabled_media_and_contract_entrypoint(env):
    env["opts"] = GenerationOptions(allow_manim=False, allow_generated_images=False, allow_ai_video=False)
    m = asyncio.run(build_assets(env["ctx"], env["sp"], env["opts"]))
    assert isinstance(m, AssetManifest)
    assert m.media["sim"].main is None and any("disabled" in w for w in m.media["sim"].warnings)
    assert m.media["intro"].side_panel is None
    # no AI video, no generated stills: the scene degrades to its content (warning), figures still work
    assert m.media["video1"].main is None and m.media["video1"].main_is_fallback is True
    assert any("No still image" in w for w in m.media["video1"].warnings)
    assert m.media["video2"].main.asset_key == env["fig_key"]
    assert env["providers"].renderer.requests == []


def test_scene_hash_semantics(env):
    sp = env["sp"]
    intro = sp.scene_by_id("intro")
    quiz = sp.scene_by_id("quiz")
    assert scene_hash(quiz) == scene_hash(quiz, sp.lexicon)  # no lexicon term applies
    assert scene_hash(intro) != scene_hash(intro, sp.lexicon)  # BJT applies
    assert scene_hash(intro, voice={"voice": "x"}) != scene_hash(intro)
    noted = intro.model_copy(update={"notes": "teacher note"})
    assert scene_hash(noted) == scene_hash(intro)
    assert stale_scene_ids(sp, None) == [s.id for s in sp.scenes]


def test_unconfigured_tts_provider_falls_back_to_server_default(env, monkeypatch):
    from aadhi.pipeline import integrations
    from aadhi.providers.base import ProviderNotConfigured

    fake_tts = env["providers"].tts
    asked: list[str | None] = []

    def get_tts(name, settings):
        asked.append(name)
        if name == "elevenlabs":
            raise ProviderNotConfigured("no key", provider="elevenlabs")
        return fake_tts

    monkeypatch.setattr(integrations, "get_tts", get_tts)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "elevenlabs", "tts_voice": "Rachel"})
    res = build(env)
    assert asked == ["elevenlabs", None]
    fallback = [i for i in res.issues if i.code == "assets.tts_fallback"]
    assert len(fallback) == 1 and fallback[0].scene_id is None and "elevenlabs" in fallback[0].message
    intro = res.manifest.audio["intro"]
    assert intro.provider == "fake" and intro.voice != "Rachel"  # the requested voice belongs to the other provider


def test_cached_ai_video_is_reused_without_a_budget_check(env):
    first = build(env).manifest
    assert first.media["video1"].main.source == "veo"

    def ensure(cost, provider=None):
        from aadhi.jobs.base import BudgetExceeded

        raise BudgetExceeded("too expensive")

    env["ctx"].ensure_budget = ensure  # type: ignore[method-assign]
    data = env["sp"].model_dump(mode="json")
    data["scenes"][5]["beats"][0]["narration"] = "See the lathe cutting metal."  # rebuild the scene, same clip
    res = build(env, sp=Screenplay.model_validate(data), previous=first)
    assert res.built == ["video1"]
    assert res.manifest.media["video1"].main == first.media["video1"].main
    assert len(env["providers"].video.prompts) == 1


@pytest.mark.slow
def test_build_assets_with_real_ffmpeg_and_provider_fake_tts(job_ctx, monkeypatch):
    """No audio shortcuts: the providers area's fake TTS, real ffmpeg trimming/joining, ffprobe check."""
    import json
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg not installed")
    from aadhi.pipeline import integrations

    pytest.importorskip("aadhi.providers.tts.fake")
    monkeypatch.setattr(integrations, "render_manim_fn", lambda: None)
    sp = Screenplay.model_validate({"scenes": [
        {"id": "a", "type": "content", "title": "A", "beats": [
            beat("a-b1", "Resistance opposes the flow of current.", pause_after=0.5),
            beat("a-b2", "Copper resists very little.")]},
        {"id": "q", "type": "quiz_checkpoint", "title": "Q", "question": "Which?", "options": ["x", "y"],
         "correct_index": 0, "countdown_seconds": 4, "beats": [beat("q-b1", "Which one is right?")],
         "reveal_beats": [beat("q-b2", "The first one is right.")]},
    ]})
    res = asyncio.run(build_assets_detailed(job_ctx, sp, GenerationOptions()))
    assert not [i for i in res.issues if i.severity == "error"], res.issues
    a, q = res.manifest.audio["a"], res.manifest.audio["q"]
    assert a.provider == "fake" and a.beats[1].offset > a.beats[0].offset + a.beats[0].speech_duration + 0.5
    assert q.countdown_start is not None and q.beats[1].offset >= q.countdown_start + 4
    for audio in (a, q):
        path = job_ctx.assets.local_copy(audio.asset_key, job_ctx.settings.data_dir)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                               capture_output=True, text=True, check=True)
        assert float(json.loads(probe.stdout)["format"]["duration"]) == pytest.approx(audio.duration, abs=0.12)


# ---------------------------------------------------------------------------
# regressions (review round 2)
# ---------------------------------------------------------------------------


def _card(sid: str, text: str) -> dict[str, Any]:
    return {"id": sid, "type": "chapter_card", "title": sid, "beats": [beat(f"{sid}-b1", text)]}


def test_shared_narration_keeps_each_scenes_beat_ids(env):
    """Two scenes with identical narration share the cached scene audio but keep their own beat ids."""
    same = "Let's move on to part two of today's lecture."
    sp = Screenplay.model_validate({"scenes": [_card("card-a", same), _card("card-b", same)]})
    m = build(env, sp=sp).manifest
    a, b = m.audio["card-a"], m.audio["card-b"]
    assert a.asset_key == b.asset_key  # one cached narration file
    assert [x.beat_id for x in a.beats] == ["card-a-b1"] and [x.beat_id for x in b.beats] == ["card-b-b1"]
    # another lecture reusing the same narration (cross-project cache hit) gets its own ids too
    other = Screenplay.model_validate({"scenes": [_card("intro-card", same)]})
    assert [x.beat_id for x in build(env, sp=other).manifest.audio["intro-card"].beats] == ["intro-card-b1"]
    build_timeline = pytest.importorskip("aadhi.compose.timeline").build_timeline
    tl = build_timeline(sp, m, settings=env["ctx"].settings)
    scenes = {s.scene_id: s for s in tl.scenes}
    assert scenes["card-b"].audio_asset_key == b.asset_key and scenes["card-b"].audio_duration > 0


def test_explicit_rebuild_retries_failed_animation(env):
    """A ManimError keeps the scene hash (no paid auto-retry) but scene_ids forces the rebuild."""
    p = env["providers"]
    p.renderer.fail = True
    first = build(env).manifest
    assert first.media["sim"].main is None and "sim" in first.scene_hashes
    p.renderer.fail = False
    n = len(p.renderer.requests)
    plain = build(env, previous=first)
    assert "sim" in plain.reused and len(p.renderer.requests) == n
    forced = build(env, previous=first, scene_ids=["sim"])
    assert forced.built == ["sim"] and "sim" not in forced.reused
    assert forced.manifest.media["sim"].main is not None and forced.manifest.media["sim"].main.source == "manim"
    assert not [i for i in forced.issues if i.code == "manim.render_failed"]


def test_transient_media_failure_is_retried_by_the_next_build(env):
    p = env["providers"]
    real = p.image.generate
    state = {"fail": True}

    async def flaky(prompt, **kw):
        if state["fail"]:
            raise RuntimeError("image backend timeout")
        return await real(prompt, **kw)

    p.image.generate = flaky  # type: ignore[method-assign]
    first = build(env).manifest
    assert first.media["intro"].side_panel is None and any("failed" in w for w in first.media["intro"].warnings)
    assert "intro" not in first.scene_hashes  # transient: shown stale
    assert "intro" in stale_scene_ids(env["sp"], first)
    state["fail"] = False
    second = build(env, previous=first)
    assert "intro" in second.built and second.manifest.media["intro"].side_panel is not None
    assert "intro" in second.manifest.scene_hashes and stale_scene_ids(env["sp"], second.manifest) == []


def test_blocked_media_is_not_retried(env):
    from aadhi.providers.base import ContentBlocked

    async def blocked(prompt, **kw):
        raise ContentBlocked("safety filter", provider="fake")

    env["providers"].image.generate = blocked  # type: ignore[method-assign]
    first = build(env).manifest
    assert first.media["intro"].side_panel is None and "intro" in first.scene_hashes
    assert "intro" in build(env, previous=first).reused


def test_tts_rate_change_rebuilds_narration(env):
    first = build(env).manifest
    p = env["providers"]
    env["opts"] = env["opts"].model_copy(update={"tts_rate": "+10%"})
    n = len(p.tts.texts)
    res = build(env, previous=first)
    beats_scenes = sorted(s.id for s in env["sp"].scenes if s.all_beats())
    assert sorted(res.built) == beats_scenes and len(p.tts.texts) > n
    clip_old = first.audio["intro"].beats[0].clip_asset_key
    assert res.manifest.audio["intro"].beats[0].clip_asset_key != clip_old
    # legacy manifests without clip keys still reuse on provider/voice/text
    data = first.model_dump(mode="json")
    for audio in data["audio"].values():
        for b in audio["beats"]:
            b["clip_asset_key"] = None
    env["opts"] = env["opts"].model_copy(update={"tts_rate": None})
    legacy = build(env, previous=AssetManifest.model_validate(data))
    assert "quiz" in legacy.reused


# --- personal API keys ---------------------------------------------------------------------------


def _refusal(provider: str, status: int = 401):
    async def refuse(*args: Any, **kw: Any):
        raise ProviderError(f"{provider}: request failed (HTTP {status})", status=status, provider=provider)

    return refuse


@pytest.mark.parametrize(
    ("target", "provider", "status", "sources"),
    [
        ("tts.synthesize", "openai", 401, {"openai": "personal"}),
        ("image.generate", "gemini", 403, {"gemini": "personal"}),
        ("video.generate", "veo", 401, {"gemini": "personal"}),  # Veo runs on the Gemini key
    ],
)
def test_a_refused_personal_key_fails_the_build(env, target, provider, status, sources):
    """Voices, images and AI videos on the job owner's refused personal key are not quietly skipped:
    the error propagates (the worker fails the job as personal_key_rejected, no retry)."""
    holder, attr = target.split(".")
    setattr(getattr(env["providers"], holder), attr, _refusal(provider, status))
    env["ctx"].key_sources = sources
    with pytest.raises(ProviderError) as info:
        build(env)
    assert (info.value.status, info.value.provider) == (status, provider)


def test_a_refused_server_key_still_degrades(env):
    env["providers"].tts.synthesize = _refusal("openai")  # type: ignore[method-assign]
    env["ctx"].key_sources = {"openai": "server"}
    res = build(env)
    assert ("assets.tts_failed", "intro") in {(i.code, i.scene_id) for i in res.issues}
    assert "intro" not in res.manifest.scene_hashes  # retried by the next build


def test_media_on_a_personal_key_is_deduplicated_per_user(env, monkeypatch):
    """Work paid with a personal key runs in its own in-flight scope; server-paid work shares one."""
    from aadhi.db import session_scope
    from aadhi.models import Project

    with session_scope() as db:
        owner = db.get(Project, env["project_id"]).owner_id
    env["ctx"].user_id = owner
    env["ctx"].key_sources = {"gemini": "personal"}
    env["providers"].image.name = "gemini"
    store = env["ctx"].assets
    real = store.get_or_create
    scopes: dict[str, set[str]] = {}

    async def spy(key, kind, producer, **kw):
        scopes.setdefault(kind, set()).add(kw.get("inflight_scope", ""))
        return await real(key, kind, producer, **kw)

    monkeypatch.setattr(store, "get_or_create", spy)
    build(env)
    assert scopes["image"] == {f"user:{owner}"}  # Gemini images on the owner's own key
    assert scopes["tts"] == {""} and scopes["video"] == {""} and scopes["scene_audio"] == {""}
