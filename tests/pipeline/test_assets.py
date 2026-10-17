"""build_assets with fake providers (+ fake renderer): manifest, fallbacks, incremental reuse."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import SecretStr

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


def test_a_voice_change_in_the_options_keeps_the_built_voice_and_a_revoice_rebuilds(env):
    """O17: options changed after the build do not re-voice a built lecture; the teacher's explicit re-voice does."""
    first = build(env).manifest
    n = len(env["providers"].tts.texts)
    env["opts"] = env["opts"].model_copy(update={"tts_voice": "other-voice"})
    kept = build(env, previous=first)
    assert not kept.built and kept.manifest.audio == first.audio and len(env["providers"].tts.texts) == n
    res = build(env, previous=first, revoice=True)
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


# --- a voice model the provider no longer serves (design §10.3 H0) ---------------------------------------------------

RETIRED_MODEL = "gemini-2.5-flash-preview-tts"


class RetiredTTS:
    """A TTS provider whose voice model is no longer served: every request is refused (HTTP 404), nothing billed."""

    supports_word_timings = False

    def __init__(self, name: str = "gemini") -> None:
        self.name = name
        self.texts: list[str] = []

    def default_voice(self, language: str) -> str:
        return "Kore"

    def voices(self, language: str | None = None) -> list[dict[str, str]]:
        return []

    async def synthesize(self, text: str, *, voice: str, language: str, rate: str = "+0%", on_usage=None):
        from aadhi.providers.base import ModelUnavailable

        self.texts.append(text)
        await asyncio.sleep(0)  # answered later: the build's other scenes are in flight meanwhile
        raise ModelUnavailable(f"{self.name}: the speech model {RETIRED_MODEL} is not available (shut down or renamed; "
                               "GEMINI_TTS_MODEL) (HTTP 404)", status=404, provider=self.name)


def retired_voice(env: dict[str, Any], monkeypatch, gemini: Any) -> dict[str, Any]:
    """The lecture asks for Gemini voice Kore, served by ``providers["gemini"]`` (a ``RetiredTTS`` or a working
    fake); ``providers["edge"]`` is an offline Edge stand-in. Returns the providers (tests may swap one)."""
    from aadhi.pipeline import integrations
    from tests.pipeline.fakes import FakeTTS

    providers = {"gemini": gemini, "edge": FakeTTS(name="edge")}
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: providers[name or "gemini"])
    env["ctx"].settings = env["ctx"].settings.model_copy(update={"gemini_tts_model": RETIRED_MODEL})
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "gemini", "tts_voice": "Kore"})
    return providers


def clip_keys(manifest: AssetManifest, scene_id: str) -> list[str | None]:
    return [b.clip_asset_key for b in manifest.audio[scene_id].beats]


def test_a_retired_tts_model_narrates_with_edge_and_warns(env, monkeypatch):
    retired = RetiredTTS()
    edge = retired_voice(env, monkeypatch, retired)["edge"]
    res = build(env)
    m, sp = res.manifest, env["sp"]
    voiced = [s.id for s in sp.scenes if s.all_beats()]
    assert retired.texts and edge.texts  # the chosen voice was tried, then Edge narrated
    assert {(m.audio[sid].provider, m.audio[sid].voice) for sid in voiced} == {("edge", "en-IN-NeerjaNeural")}
    assert m.audio["intro"].beats[0].words  # Edge word timings reach the captions
    assert set(m.scene_hashes) == {s.id for s in sp.scenes} and m.stale_scenes == []  # complete: nothing to retry
    fallback = [i for i in res.issues if i.code == "assets.tts_fallback"]
    assert len(fallback) == 1 and fallback[0].scene_id is None and fallback[0].severity == "warning"
    assert f"'{RETIRED_MODEL}'" in fallback[0].message and "'gemini'" in fallback[0].message
    assert "'en-IN-NeerjaNeural'" in fallback[0].message
    assert not [i for i in res.issues if i.code == "assets.tts_failed"]
    logs = [e for e in env["ctx"].events if e["type"] == "log" and e["level"] == "warning"]
    assert any("free Edge voice en-IN-NeerjaNeural" in e["message"] and RETIRED_MODEL in e["message"] for e in logs)
    # the Edge clips are keyed exactly like an Edge lecture's: one made with Edge chosen reuses every clip
    n_edge = len(edge.texts)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge", "tts_voice": None})
    chosen = build(env)
    assert len(edge.texts) == n_edge  # nothing synthesised (or paid) again
    assert all(clip_keys(chosen.manifest, sid) == clip_keys(m, sid) for sid in voiced)
    assert not [i for i in chosen.issues if i.code == "assets.tts_fallback"]


def test_a_lecture_the_fallback_narrated_wholly_keeps_edge_until_the_teacher_revoices_it(env, monkeypatch):
    """O17: made with Edge (its model was retired at the first build), the lecture keeps Edge: the next build calls
    no voice at all. The teacher's re-voice tries the chosen voice again (unbilled), and its Edge clips come from
    the cache."""
    retired = RetiredTTS()
    edge = retired_voice(env, monkeypatch, retired)["edge"]
    first = build(env).manifest
    assert stand_ins(first, env) == {None}  # a new lecture's fallback is the voice it is made with, not a stand-in
    n_edge, n_retired = len(edge.texts), len(retired.texts)
    again = build(env, previous=first)
    assert len(retired.texts) == n_retired and len(edge.texts) == n_edge and not again.built
    assert again.manifest.audio == first.audio
    assert not [i for i in again.issues if i.code == "assets.tts_fallback"]
    note = [i for i in again.issues if i.code == "assets.voice_kept"]
    assert len(note) == 1 and "'en-IN-NeerjaNeural'" in note[0].message and "Google Gemini TTS" in note[0].message
    revoiced = build(env, previous=first, revoice=True)
    assert len(retired.texts) > n_retired and len(edge.texts) == n_edge  # tried again (unbilled), Edge from the cache
    assert revoiced.manifest.audio == first.audio
    assert len([i for i in revoiced.issues if i.code == "assets.tts_fallback"]) == 1  # still visible


def test_a_scene_is_narrated_in_one_voice_when_the_model_retires_mid_lecture(env, monkeypatch):
    from tests.pipeline.fakes import FakeTTS

    providers = retired_voice(env, monkeypatch, FakeTTS(name="gemini"))  # the model still works at first
    edge = providers["edge"]
    first = build(env).manifest
    assert first.audio["intro"].provider == "gemini" and first.audio["intro"].voice == "Kore"
    data = env["sp"].model_dump(mode="json")
    data["scenes"][0]["beats"][1]["narration"] = "It has exactly three terminals."
    edited = Screenplay.model_validate(data)
    providers["gemini"] = RetiredTTS()  # Google shuts the model down; the first beat's clip is still cached
    res = build(env, sp=edited, previous=first)
    assert res.built == ["intro"]
    intro = res.manifest.audio["intro"]
    assert (intro.provider, intro.voice) == ("edge", "en-IN-NeerjaNeural")
    # both beats in Edge, in either order: clip lookups run in threads, so synthesis order is not fixed (beat order
    # is pinned by the manifest's clip keys above)
    assert sorted(edge.texts) == sorted(["Look at the B J T circuit here.", "It has exactly three terminals."])
    assert clip_keys(res.manifest, "intro")[0] != clip_keys(first, "intro")[0]
    assert res.manifest.audio["quiz"] == first.audio["quiz"]  # unchanged scenes keep their narration
    assert len([i for i in res.issues if i.code == "assets.tts_fallback"]) == 1
    # the lecture's voice is still Gemini's (Edge only stood in for one scene): the next build tries it there again,
    # unbilled, and the Edge clips come from the cache
    n_edge, n_retired = len(edge.texts), len(providers["gemini"].texts)
    again = build(env, sp=edited, previous=res.manifest)
    assert again.built == ["intro"] and len(providers["gemini"].texts) > n_retired and len(edge.texts) == n_edge
    assert again.manifest.audio == res.manifest.audio


def test_the_edge_voice_follows_the_servers_edge_voice_and_the_lectures_language(env):
    from types import SimpleNamespace

    from aadhi.pipeline.assets import fallback_voice_config

    edge = SimpleNamespace(name="edge", default_voice=lambda language: "unused")
    base = env["ctx"].settings.model_copy(update={"default_language": "en-IN"})
    server_edge = base.model_copy(update={"tts_provider": "edge", "tts_voice": "en-IN-PrabhatNeural"})
    v = fallback_voice_config(server_edge, "en-IN", edge, "+10%")
    assert (v.provider, v.voice, v.rate, v.model, v.language) == ("edge", "en-IN-PrabhatNeural", "+10%", "", "en-IN")
    assert fallback_voice_config(server_edge, "ta-IN", edge, "+0%").voice == "ta-IN-PallaviNeural"
    server_gemini = base.model_copy(update={"tts_provider": "gemini", "tts_voice": "Kore"})
    assert fallback_voice_config(server_gemini, "en-IN", edge, "+0%").voice == "en-IN-NeerjaNeural"  # never "Kore"
    assert fallback_voice_config(server_gemini, "hi-IN", edge, "+0%").voice == "hi-IN-SwaraNeural"
    # an Edge TTS_VOICE kept from a server that ran on Edge before OpenAI TTS became the default still applies to Edge
    server_openai = base.model_copy(update={"tts_provider": "openai", "tts_voice": "en-IN-PrabhatNeural"})
    assert fallback_voice_config(server_openai, "en-IN", edge, "+0%").voice == "en-IN-PrabhatNeural"
    assert fallback_voice_config(server_openai, "ta-IN", edge, "+0%").voice == "ta-IN-PallaviNeural"
    own = base.model_copy(update={"tts_provider": "openai", "tts_voice": "onyx"})
    assert fallback_voice_config(own, "en-IN", edge, "+0%").voice == "en-IN-NeerjaNeural"  # never OpenAI's "onyx"


def test_edge_failing_the_same_way_is_a_narration_failure_not_a_loop(env, monkeypatch):
    from aadhi.pipeline import integrations
    from aadhi.providers.base import ProviderNotConfigured

    retired_edge = RetiredTTS(name="edge")
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: retired_edge)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})
    res = build(env)
    assert ("assets.tts_failed", "intro") in {(i.code, i.scene_id) for i in res.issues}
    assert not [i for i in res.issues if i.code == "assets.tts_fallback"] and "intro" not in res.manifest.audio

    def no_edge(name, settings):
        if name == "edge":
            raise ProviderNotConfigured("edge: off", provider="edge")
        return RetiredTTS()

    monkeypatch.setattr(integrations, "get_tts", no_edge)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "gemini"})
    res = build(env)
    assert ("assets.tts_failed", "intro") in {(i.code, i.scene_id) for i in res.issues}
    assert not [i for i in res.issues if i.code == "assets.tts_fallback"]


def test_a_retired_openai_speech_model_narrates_with_edge_too(env, monkeypatch):
    """Every OpenAI /v1/audio/speech model retires on 2027-01-06 (docs/OPERATIONS.md section 3.3). The real adapter
    over the real SDK turns OpenAI's answer (404 model_not_found, also its answer for a model the key has no access
    to) into ModelUnavailable, so the build narrates with Edge and warns instead of leaving every scene silent."""
    import httpx
    import openai

    from aadhi.pipeline import integrations
    from aadhi.providers.tts.openai import OpenAITTS
    from tests.pipeline.fakes import FakeTTS

    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404, json={"error": {
            "message": "The model `tts-1` does not exist or you do not have access to it.",
            "type": "invalid_request_error", "param": None, "code": "model_not_found"}})

    client = openai.AsyncOpenAI(api_key="sk-test-123456789", max_retries=0,
                                http_client=httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    env["ctx"].settings = env["ctx"].settings.model_copy(update={"openai_tts_model": "tts-1"})
    edge = FakeTTS(name="edge")
    providers = {"openai": OpenAITTS(env["ctx"].settings, client_factory=lambda: client), "edge": edge}
    monkeypatch.setattr(integrations, "get_tts", lambda name, settings: providers[name or "openai"])
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "openai", "tts_voice": "nova"})
    try:
        res = build(env)
    finally:
        asyncio.run(client.close())
    m = res.manifest
    voiced = [s.id for s in env["sp"].scenes if s.all_beats()]
    assert requests and {r.url.path for r in requests} == {"/v1/audio/speech"} and edge.texts
    assert {(m.audio[sid].provider, m.audio[sid].voice) for sid in voiced} == {("edge", "en-IN-NeerjaNeural")}
    assert not [u for u in env["ctx"].usages if u.provider == "openai"]  # every refused request was free
    fallback = [i for i in res.issues if i.code == "assets.tts_fallback"]
    assert len(fallback) == 1 and "'tts-1'" in fallback[0].message and "'openai'" in fallback[0].message
    assert "the API key has no access to it" in fallback[0].message  # the same 404: not necessarily retired
    assert not [i for i in res.issues if i.code == "assets.tts_failed"]
    logs = [e["message"] for e in env["ctx"].events if e["type"] == "log" and e["level"] == "warning"]
    switch = [msg for msg in logs if "free Edge voice en-IN-NeerjaNeural" in msg]
    assert len(switch) == 1 and "'tts-1'" in switch[0] and "model_not_found" in switch[0]  # OpenAI's reason kept


# --- OpenAI TTS is the primary voice; without an OpenAI key the free Edge voice narrates (owner, 2026-10-17) ----------


def keyed_voices(env: dict[str, Any], monkeypatch, **settings: Any) -> dict[str, Any]:
    """The provider seam as the real factory decides it (``factory.tts_configured``: a keyed provider needs its key in
    the job's settings, which carry the owner's personal key), with offline fakes in place of the real adapters. The
    server's voice provider is the built-in default (``TTS_PROVIDER`` unset): OpenAI TTS."""
    from aadhi.config import Settings
    from aadhi.pipeline import integrations
    from aadhi.providers import factory
    from aadhi.providers.base import ProviderNotConfigured
    from tests.pipeline.fakes import FakeTTS

    voices = {name: FakeTTS(name=name) for name in ("openai", "gemini", "edge")}

    def get_tts(name, settings):
        name = name or settings.tts_provider
        if not factory.tts_configured(name, settings):
            raise ProviderNotConfigured(f"{name}: TTS provider is not configured", provider=name)
        return voices[name]

    monkeypatch.setattr(integrations, "get_tts", get_tts)
    env["ctx"].settings = env["ctx"].settings.model_copy(update={
        "tts_provider": Settings.model_fields["tts_provider"].default, "tts_voice": "", "openai_api_key": SecretStr(""), "gemini_api_key": SecretStr(""),
        "gemini_api_keys": [], **settings})
    return voices


def voiced_by(res: Any, env: dict[str, Any]) -> set[tuple[str, str]]:
    return {(res.manifest.audio[s.id].provider, res.manifest.audio[s.id].voice) for s in env["sp"].scenes
            if s.all_beats()}


def tts_fallbacks(res: Any) -> list[Any]:
    return [i for i in res.issues if i.code == "assets.tts_fallback"]


def test_openai_narrates_by_default_with_the_owners_or_the_servers_key(env, monkeypatch):
    voices = keyed_voices(env, monkeypatch, openai_api_key=SecretStr("sk-test-owner-or-server"))
    res = build(env)
    assert voiced_by(res, env) == {("openai", "nova")}  # en-IN: OpenAI's voice for the language
    assert voices["openai"].texts and not voices["edge"].texts
    assert not tts_fallbacks(res)
    # an explicit per-lecture choice of another configured provider still wins
    voices = keyed_voices(env, monkeypatch, openai_api_key=SecretStr("sk-test-owner-or-server"),
                          gemini_api_key=SecretStr("test-gemini-key"))
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "gemini", "tts_voice": "Puck"})
    gemini = build(env)
    assert voiced_by(gemini, env) == {("gemini", "Puck")} and not tts_fallbacks(gemini)
    assert voices["gemini"].texts and not voices["openai"].texts and not voices["edge"].texts
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge", "tts_voice": None})
    edge = build(env)  # Edge chosen while an OpenAI key exists: Edge, no warning
    assert voiced_by(edge, env) == {("edge", "en-IN-NeerjaNeural")} and not tts_fallbacks(edge)


def test_without_an_openai_key_the_free_edge_voice_narrates_with_one_warning(env, monkeypatch):
    voices = keyed_voices(env, monkeypatch, stored_api_keys_enabled=True, user_api_keys_enabled=True)
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-NeerjaNeural")}  # Edge's own voice for the lecture's language
    assert voices["edge"].texts and not voices["openai"].texts
    assert not [i for i in res.issues if i.code == "assets.tts_failed"]
    assert set(res.manifest.scene_hashes) == {s.id for s in env["sp"].scenes}  # complete: the job did not fail
    fallback = tts_fallbacks(res)
    assert len(fallback) == 1 and fallback[0].scene_id is None and fallback[0].severity == "warning"
    message = fallback[0].message
    assert message.startswith("OpenAI TTS (the server's voice provider 'openai') has no API key")
    assert "the free Edge voice 'en-IN-NeerjaNeural' was used instead" in message
    assert "your own under API keys, or the server's OPENAI_API_KEY" in message
    # the Edge clips are keyed exactly like an Edge lecture's: choosing Edge later synthesises nothing again
    n_edge = len(voices["edge"].texts)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})
    chosen = build(env)
    assert len(voices["edge"].texts) == n_edge and not tts_fallbacks(chosen)
    assert chosen.manifest.audio == res.manifest.audio


def test_an_explicit_openai_choice_without_a_key_does_not_fail_either(env, monkeypatch):
    keyed_voices(env, monkeypatch, user_api_keys_enabled=False)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "openai", "tts_voice": "onyx", "tts_rate": "+10%"})
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-NeerjaNeural")}  # never OpenAI's "onyx" sent to Edge
    assert {res.manifest.audio["intro"].provider} == {"edge"}
    assert len(tts_fallbacks(res)) == 1 and "OpenAI TTS" in tts_fallbacks(res)[0].message
    assert "(the server's OPENAI_API_KEY)" in tts_fallbacks(res)[0].message  # personal keys are off on this server


def test_an_edge_voice_picked_while_openai_has_no_key_is_kept(env, monkeypatch):
    keyed_voices(env, monkeypatch)
    env["opts"] = env["opts"].model_copy(update={"tts_voice": "en-IN-PrabhatNeural"})
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-PrabhatNeural")}
    assert "'en-IN-PrabhatNeural' was used instead" in tts_fallbacks(res)[0].message


def test_an_unconfigured_choice_and_a_keyless_openai_fall_to_edge_with_one_warning(env, monkeypatch):
    keyed_voices(env, monkeypatch)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "gemini", "tts_voice": "Puck"})
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-NeerjaNeural")}
    fallback = tts_fallbacks(res)
    assert len(fallback) == 1
    assert fallback[0].message.startswith("The voice provider 'gemini' is not configured on this server, and OpenAI "
                                          "TTS (the server's voice provider 'openai') has no API key")


def test_a_saved_edge_voice_on_server_default_is_not_sent_to_openai(env, monkeypatch):
    """A lecture saved on "Server default" with an Edge voice (the Studio listed Edge voices there while Edge was the
    default, and still does while OpenAI has no key): once the job has an OpenAI key, OpenAI narrates with its own
    voice for the language (its adapter would refuse the Edge name on every beat), with an info note saying so."""
    voices = keyed_voices(env, monkeypatch, openai_api_key=SecretStr("sk-test-owner-or-server"))
    env["opts"] = env["opts"].model_copy(update={"tts_voice": "en-IN-PrabhatNeural"})
    res = build(env)
    assert voiced_by(res, env) == {("openai", "nova")}
    assert voices["openai"].texts and not voices["edge"].texts
    assert not [i for i in res.issues if i.code == "assets.tts_failed"] and not tts_fallbacks(res)
    note = [i for i in res.issues if i.code == "assets.tts_voice_replaced"]
    assert len(note) == 1 and note[0].severity == "info" and note[0].scene_id is None
    assert "'en-IN-PrabhatNeural' is not a voice of OpenAI TTS" in note[0].message and "'nova'" in note[0].message
    # an OpenAI voice saved on "Server default" is OpenAI's: kept, no note
    env["opts"] = env["opts"].model_copy(update={"tts_voice": "onyx"})
    onyx = build(env)
    assert voiced_by(onyx, env) == {("openai", "onyx")}
    assert not [i for i in onyx.issues if i.code == "assets.tts_voice_replaced"]


def test_a_voice_of_another_provider_never_reaches_the_lectures_chosen_provider(env, monkeypatch):
    """The Translate dialog offered the server default's voices: 'nova' saved on a lecture that chose Edge narrates
    with Edge's voice for the language (Edge refuses 'nova'), not silently."""
    keyed_voices(env, monkeypatch, openai_api_key=SecretStr("sk-test-owner-or-server"))
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge", "tts_voice": "nova"})
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-NeerjaNeural")}
    assert len([i for i in res.issues if i.code == "assets.tts_voice_replaced"]) == 1


def test_without_an_openai_key_an_edge_tts_voice_still_narrates(env, monkeypatch):
    """An upgraded server that ran on Edge with TTS_VOICE set and has no OpenAI key: its default-language lectures keep
    that voice (the same clips as before), other languages Edge's voice for the language."""
    voices = keyed_voices(env, monkeypatch, tts_voice="en-IN-PrabhatNeural", default_language="en-IN")
    res = build(env)
    assert voiced_by(res, env) == {("edge", "en-IN-PrabhatNeural")}
    assert "the free Edge voice 'en-IN-PrabhatNeural' was used instead" in tts_fallbacks(res)[0].message
    n_edge = len(voices["edge"].texts)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})  # as on the server before the upgrade
    before = build(env)
    assert before.manifest.audio == res.manifest.audio and len(voices["edge"].texts) == n_edge
    # an OpenAI voice saved on "Server default" while OpenAI had a key: Edge's voice instead, never "onyx" sent to Edge
    env["opts"] = env["opts"].model_copy(update={"tts_provider": None, "tts_voice": "onyx"})
    assert voiced_by(build(env), env) == {("edge", "en-IN-PrabhatNeural")}


def test_an_edge_tts_voice_is_not_sent_to_openai(env):
    """TTS_VOICE set for Edge before OpenAI became the default: OpenAI speaks its own voice for the language."""
    from types import SimpleNamespace

    from aadhi.pipeline.assets import voice_config

    openai = SimpleNamespace(name="openai", default_voice=lambda language: "unused")
    settings = env["ctx"].settings.model_copy(update={"tts_provider": "openai", "default_language": "en-IN",
                                                      "tts_voice": "en-IN-PrabhatNeural"})
    opts = GenerationOptions()
    assert voice_config(settings, opts, "en-IN", openai).voice == "nova"
    assert voice_config(settings, opts, "en-GB", openai).voice == "nova"  # as before OpenAI became the default
    own = settings.model_copy(update={"tts_voice": "onyx"})
    assert voice_config(own, opts, "en-IN", openai).voice == "onyx"  # an OpenAI TTS_VOICE is used
    edge = SimpleNamespace(name="edge", default_voice=lambda language: "unused")
    assert voice_config(settings, opts, "en-IN", edge).voice == "en-IN-PrabhatNeural"  # still Edge's own


# --- O17 (owner, 2026-10-17): a built lecture keeps the voice it was made with ------------------------------------


OPENAI_KEY = SecretStr("sk-test-owner-or-server")


def voiced_ids(env: dict[str, Any]) -> list[str]:
    return [s.id for s in env["sp"].scenes if s.all_beats()]


def calls(voices: dict[str, Any]) -> dict[str, int]:
    return {name: len(tts.texts) for name, tts in voices.items() if tts.texts}


def edited_intro(env: dict[str, Any]) -> Screenplay:
    data = env["sp"].model_dump(mode="json")
    data["scenes"][0]["beats"][1]["narration"] = "It has exactly three terminals."
    return Screenplay.model_validate(data)


@pytest.mark.parametrize("built_on", ["old Edge default", "keyless", "keyless, OpenAI chosen"])
def test_a_lecture_built_on_edge_keeps_edge_when_an_openai_key_appears(env, monkeypatch, built_on):
    """(a) Built on Edge (the old default, or keyless under the new one, even with OpenAI chosen): rebuilt with an
    OpenAI key, it makes NO voice call and keeps Edge; an info note says how to change the voice."""
    if built_on == "old Edge default":
        keyed_voices(env, monkeypatch, tts_provider="edge")
    else:
        keyed_voices(env, monkeypatch)
    if built_on.endswith("OpenAI chosen"):
        env["opts"] = env["opts"].model_copy(update={"tts_provider": "openai"})
    first = build(env)
    assert voiced_by(first, env) == {("edge", "en-IN-NeerjaNeural")}
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)  # the owner adds the key; OpenAI is default
    again = build(env, previous=first.manifest)
    assert calls(voices) == {}  # zero TTS calls, nothing paid
    assert not again.built and sorted(again.reused) == sorted(first.manifest.media)
    assert again.manifest.audio == first.manifest.audio
    assert not tts_fallbacks(again)
    note = [i for i in again.issues if i.code == "assets.voice_kept"]
    assert len(note) == 1 and note[0].severity == "info" and note[0].scene_id is None
    assert "Microsoft Edge (free) voice 'en-IN-NeerjaNeural'" in note[0].message and "OpenAI TTS" in note[0].message
    # the same with only some scenes named, as the editor's scene rebuild sends them
    scoped = build(env, previous=first.manifest, scene_ids=["card"])
    assert calls(voices) == {} and scoped.manifest.audio == first.manifest.audio


def test_an_edited_scene_of_an_edge_lecture_is_narrated_in_the_kept_edge_voice(env, monkeypatch):
    """(b) After a script edit to one scene, only that scene is narrated, only its changed beat is synthesised, in the
    kept voice (Edge), never with the OpenAI key; every other scene keeps its narration and timing."""
    keyed_voices(env, monkeypatch, tts_provider="edge", tts_voice="en-IN-PrabhatNeural", default_language="en-IN")
    first = build(env).manifest
    assert {(first.audio[s].provider, first.audio[s].voice) for s in voiced_ids(env)} == {("edge", "en-IN-PrabhatNeural")}
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)  # the upgrade: OpenAI default, TTS_VOICE unset
    edited = edited_intro(env)
    res = build(env, sp=edited, previous=first)
    assert res.built == ["intro"]
    assert calls(voices) == {"edge": 1} and voices["edge"].texts == ["It has exactly three terminals."]
    intro = res.manifest.audio["intro"]
    assert (intro.provider, intro.voice) == ("edge", "en-IN-PrabhatNeural")
    assert clip_keys(res.manifest, "intro")[0] == clip_keys(first, "intro")[0]  # the unchanged beat's clip
    assert all(res.manifest.audio[s] == first.audio[s] for s in voiced_ids(env) if s != "intro")
    assert not tts_fallbacks(res)
    # the editor's scene-scoped rebuild narrates the same way (the new clip is cached by now: still no other voice)
    scoped = build(env, sp=edited, previous=first, scene_ids=["intro"])
    assert scoped.built == ["intro"] and calls(voices) == {"edge": 1}
    assert scoped.manifest.audio == res.manifest.audio


def test_the_teachers_explicit_revoice_moves_a_built_lecture_to_openai(env, monkeypatch):
    """(c) The teacher's re-voice (``revoice=True``, ``POST /versions/{id}/voice``) narrates every scene again in the
    chosen voice; from then on the lecture keeps that one."""
    keyed_voices(env, monkeypatch)
    first = build(env).manifest  # keyless: Edge
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "openai", "tts_voice": "onyx"})
    res = build(env, previous=first, revoice=True)
    assert sorted(res.built) == sorted(voiced_ids(env))
    assert voiced_by(res, env) == {("openai", "onyx")} and set(calls(voices)) == {"openai"}
    assert not [i for i in res.issues if i.code in ("assets.voice_kept", "assets.tts_fallback")]
    # "Server default" re-voices with the server's provider (OpenAI with a key) and its voice for the language
    env["opts"] = env["opts"].model_copy(update={"tts_provider": None, "tts_voice": None})
    default = build(env, previous=first, revoice=True)
    assert voiced_by(default, env) == {("openai", "nova")}
    # kept from now on: options changed to Edge do not move it, a rebuild calls nothing
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})
    again = build(env, previous=res.manifest)
    assert calls(voices) == {} and again.manifest.audio == res.manifest.audio
    # and re-voicing back to the free Edge voice narrates nothing new: its clips are cached
    back = build(env, previous=res.manifest, revoice=True)
    assert voiced_by(back, env) == {("edge", "en-IN-NeerjaNeural")} and calls(voices) == {}
    assert back.manifest.audio == first.audio


def test_a_revoice_to_a_provider_without_a_key_fails_and_never_steps_down(env, monkeypatch):
    """Voices audit R-1: the re-voice endpoint checks the key when it queues the job, but the key can be gone when the
    job runs. The teacher chose OpenAI explicitly, so the job must fail (the lecture keeps its voice) rather than
    narrate with another provider the lecture would then keep."""
    from aadhi.providers.base import ProviderNotConfigured

    keyed_voices(env, monkeypatch)
    first = build(env).manifest  # keyless: Edge
    voices = keyed_voices(env, monkeypatch)  # still no OpenAI key when the re-voice job runs
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "openai", "tts_voice": "onyx"})
    with pytest.raises(ProviderNotConfigured):
        build(env, previous=first, revoice=True)
    assert calls(voices) == {}, "nothing narrated in a voice nobody chose"
    # a plain rebuild still keeps the built Edge voice
    assert voiced_by(build(env, previous=first), env) == {("edge", "en-IN-NeerjaNeural")}


def test_a_new_lecture_narrates_with_openai_when_a_key_exists(env, monkeypatch):
    """(d) Nothing built yet (no previous manifest, or one without any narration): OpenAI TTS with a key, else Edge
    with the keyless warning, which says the lecture then keeps Edge."""
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    res = build(env)
    assert voiced_by(res, env) == {("openai", "nova")} and set(calls(voices)) == {"openai"}
    assert not [i for i in res.issues if i.code == "assets.voice_kept"]
    unnarrated = AssetManifest.model_validate({**res.manifest.model_dump(mode="json"), "audio": {}})
    assert voiced_by(build(env, previous=unnarrated), env) == {("openai", "nova")}
    keyed_voices(env, monkeypatch)
    keyless = build(env)
    assert voiced_by(keyless, env) == {("edge", "en-IN-NeerjaNeural")}
    message = tts_fallbacks(keyless)[0].message
    assert "The lecture keeps the Edge voice when it is edited or rebuilt" in message and "(Voice, in" in message


def test_a_kept_voice_without_its_key_stands_in_with_edge_only_where_narration_is_needed(env, monkeypatch):
    """An OpenAI lecture whose key is gone (the owner removed a personal key): unchanged scenes keep OpenAI, the
    edited scene is narrated by the Edge stand-in with one warning, and the next build with the key narrates that
    scene in OpenAI again (the lecture's voice stays OpenAI)."""
    keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    first = build(env).manifest
    voices = keyed_voices(env, monkeypatch)  # no key now
    unchanged = build(env, previous=first)
    assert calls(voices) == {} and unchanged.manifest.audio == first.audio
    assert not tts_fallbacks(unchanged)  # nothing needed narration: nothing stood in
    edited = edited_intro(env)
    res = build(env, sp=edited, previous=first)
    assert res.built == ["intro"] and set(calls(voices)) == {"edge"}
    assert (res.manifest.audio["intro"].provider, res.manifest.audio["intro"].voice) == ("edge", "en-IN-NeerjaNeural")
    assert all(res.manifest.audio[s] == first.audio[s] for s in voiced_ids(env) if s != "intro")
    fallback = tts_fallbacks(res)
    assert len(fallback) == 1 and fallback[0].message.startswith("This lecture's voice, OpenAI TTS voice 'nova', has no")
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    back = build(env, sp=edited, previous=res.manifest)
    assert back.built == ["intro"] and set(calls(voices)) == {"openai"}
    assert voiced_by(back, env) == {("openai", "nova")}


def stand_ins(manifest: AssetManifest, env: dict[str, Any]) -> set[str | None]:
    return {manifest.audio[s].stand_in_for for s in voiced_ids(env)}


def test_a_stand_in_that_narrated_every_scene_never_becomes_the_kept_voice(env, monkeypatch):
    """O17-1: an OpenAI lecture whose key is gone while a TTS_RATE change narrates every scene again is narrated
    wholly by the Edge stand-in; each scene records the voice it stood in for, so the lecture still keeps OpenAI and
    the next build with the key narrates it in OpenAI again (never "made with Edge")."""
    keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    first = build(env).manifest
    assert stand_ins(first, env) == {None}  # a lecture's own voice stands in for nothing
    voices = keyed_voices(env, monkeypatch)  # the owner removed the key
    env["opts"] = env["opts"].model_copy(update={"tts_rate": "+5%"})  # every clip key changes: every scene narrated
    mid = build(env, previous=first)
    assert sorted(mid.built) == sorted(voiced_ids(env)) and set(calls(voices)) == {"edge"}
    assert voiced_by(mid, env) == {("edge", "en-IN-NeerjaNeural")} and stand_ins(mid.manifest, env) == {"openai:nova"}
    assert len(tts_fallbacks(mid)) == 1
    from aadhi.schemas.manifest import built_voice

    assert built_voice(mid.manifest, "en-IN") == built_voice(mid.manifest.model_dump(mode="json"), "en-IN")         == ("openai", "nova")
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)  # the key is back
    back = build(env, previous=mid.manifest)
    assert voiced_by(back, env) == {("openai", "nova")} and set(calls(voices)) == {"openai"}
    assert stand_ins(back.manifest, env) == {None}
    assert not [i for i in back.issues if i.code in ("assets.voice_kept", "assets.tts_fallback")]


def test_a_misspelt_voice_model_never_moves_a_built_lecture_to_edge(env, monkeypatch):
    """O17-1: a misspelt GEMINI_TTS_MODEL changes every clip key, so every scene of a built Gemini lecture is tried,
    refused (ModelUnavailable) and narrated by Edge. Once the setting is fixed, the next build narrates it in Gemini
    again (its old clips come from the cache) instead of keeping Edge."""
    from tests.pipeline.fakes import FakeTTS

    working = FakeTTS(name="gemini")
    providers = retired_voice(env, monkeypatch, working)
    good = env["ctx"].settings.model_copy(update={"gemini_tts_model": "gemini-3.8-flash-tts"})
    env["ctx"].settings = good
    first = build(env).manifest
    assert {(first.audio[s].provider, first.audio[s].voice) for s in voiced_ids(env)} == {("gemini", "Kore")}
    providers["gemini"] = RetiredTTS()
    env["ctx"].settings = good.model_copy(update={"gemini_tts_model": "gemini-typo"})
    mid = build(env, previous=first)
    assert voiced_by(mid, env) == {("edge", "en-IN-NeerjaNeural")} and stand_ins(mid.manifest, env) == {"gemini:Kore"}
    providers["gemini"], env["ctx"].settings = working, good  # the setting is fixed
    back = build(env, previous=mid.manifest)
    assert voiced_by(back, env) == {("gemini", "Kore")} and stand_ins(back.manifest, env) == {None}
    assert all(clip_keys(back.manifest, s) == clip_keys(first, s) for s in voiced_ids(env))  # the cached clips
    assert not [i for i in back.issues if i.code in ("assets.voice_kept", "assets.tts_fallback")]


def test_a_revoice_to_the_stand_in_voice_keeps_no_stand_in_mark(env, monkeypatch):
    """A scene the Edge stand-in narrated for OpenAI, reused by the teacher's re-voice to that same Edge voice, is
    in the lecture's own voice now: it stands in for nothing, so the lecture keeps Edge (not OpenAI) afterwards."""
    keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    first = build(env).manifest
    keyed_voices(env, monkeypatch)
    edited = edited_intro(env)
    stood = build(env, sp=edited, previous=first)
    assert stood.manifest.audio["intro"].stand_in_for == "openai:nova"
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})
    revoiced = build(env, sp=edited, previous=stood.manifest, revoice=True)
    assert "intro" in revoiced.reused and voiced_by(revoiced, env) == {("edge", "en-IN-NeerjaNeural")}
    assert stand_ins(revoiced.manifest, env) == {None}
    env["opts"] = env["opts"].model_copy(update={"tts_provider": None})
    n = calls(voices)
    again = build(env, sp=edited, previous=revoiced.manifest)
    assert calls(voices) == n and again.manifest.audio == revoiced.manifest.audio  # kept Edge: nothing narrated


def test_built_voice_reads_what_the_manifest_was_narrated_with():
    from aadhi.schemas.manifest import built_voice

    def audio(provider: str, voice: str, key: str | None = "scene_audio-x", language: str = "en-IN") -> dict[str, Any]:
        return {"scene_id": "s", "asset_key": key, "provider": provider, "voice": voice, "language": language}

    def manifest(*entries: dict[str, Any]) -> dict[str, Any]:
        return {"audio": {f"s{i}": e for i, e in enumerate(entries)}}

    assert built_voice(None) is None and built_voice({}) is None and built_voice(manifest()) is None
    edge, nova = audio("edge", "en-IN-NeerjaNeural"), audio("openai", "nova")
    assert built_voice(manifest(edge, edge)) == ("edge", "en-IN-NeerjaNeural")
    # Edge next to another provider's voice is a stand-in (keyless or retired-model scenes): the other one wins
    assert built_voice(manifest(edge, edge, nova)) == ("openai", "nova")
    # the most scenes, ties to the earliest; scenes without narration, other languages and skipped scenes never count
    onyx = audio("openai", "onyx")
    assert built_voice(manifest(onyx, nova, nova)) == ("openai", "nova")
    assert built_voice(manifest(onyx, nova)) == ("openai", "onyx")
    assert built_voice(manifest(audio("openai", "nova", key=None), edge)) == ("edge", "en-IN-NeerjaNeural")
    assert built_voice(manifest(audio("openai", "nova", language="ta-IN"), edge), "en-IN") == ("edge", "en-IN-NeerjaNeural")
    assert built_voice(manifest(nova, edge), skip={"s0"}) == ("edge", "en-IN-NeerjaNeural")
    # the model and its stored JSON give the same answer (a missing language is the schema's default, en-IN)
    raw = manifest({k: v for k, v in nova.items() if k != "language"}, edge)
    assert built_voice(AssetManifest.model_validate(raw), "en-IN") == built_voice(raw, "en-IN") == ("openai", "nova")
    # a scene the Edge stand-in narrated counts as the voice it stood in for, even when no other scene is left
    stood = {**edge, "stand_in_for": "gemini:Kore"}
    assert built_voice(manifest(stood, stood)) == ("gemini", "Kore")
    assert built_voice(AssetManifest.model_validate(manifest(stood, edge, stood)), "en-IN") == ("gemini", "Kore")
    # an unreadable mark is ignored (the scene counts as what narrated it)
    assert built_voice(manifest({**edge, "stand_in_for": "gemini"}, {**edge, "stand_in_for": 3})) == (
        "edge", "en-IN-NeerjaNeural")


def test_a_hidden_scene_never_decides_the_kept_voice(env, monkeypatch):
    """A scene hidden during a re-voice is not narrated (hidden scenes are never built) and its narration in the old
    voice is dropped: the lecture's voice is the one its shown scenes were re-voiced to, and showing the scene again
    narrates it in that voice."""
    keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    first = build(env).manifest  # OpenAI
    data = env["sp"].model_dump(mode="json")
    data["scenes"][0]["hidden"] = True
    hidden = Screenplay.model_validate(data)
    env["opts"] = env["opts"].model_copy(update={"tts_provider": "edge"})
    revoiced = build(env, sp=hidden, previous=first, revoice=True)
    assert "intro" not in revoiced.manifest.audio and "intro" not in revoiced.manifest.scene_hashes  # never decides
    assert revoiced.manifest.media["intro"] == first.media["intro"]  # its visuals are carried as they were
    assert {revoiced.manifest.audio[s].provider for s in voiced_ids(env) if s != "intro"} == {"edge"}
    assert stale_scene_ids(hidden, revoiced.manifest) == [] and stale_scene_ids(env["sp"], revoiced.manifest) == ["intro"]
    voices = keyed_voices(env, monkeypatch, openai_api_key=OPENAI_KEY)
    again = build(env, sp=hidden, previous=revoiced.manifest)
    assert calls(voices) == {} and again.manifest.audio == revoiced.manifest.audio
    shown = build(env, sp=env["sp"], previous=revoiced.manifest)
    assert shown.built == ["intro"] and set(calls(voices)) == {"edge"}
    assert voiced_by(shown, env) == {("edge", "en-IN-NeerjaNeural")}


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
