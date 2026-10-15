"""Gemini Omni in the video provider chain: the VIDEO_PROVIDERS order and the automatic successor of Veo
(settings), the factory and status panel, the omni price rows, and build_assets end to end with the real Veo
and Omni adapters over in-memory SDK clients (Veo shut down -> Omni, budget pre-check, checkpoints and resume
across a restart, an ambiguous paid create never re-sent). Offline only."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi import config
from aadhi.config import Settings
from aadhi.pipeline import assets as assets_mod
from aadhi.pipeline import integrations
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers import factory
from aadhi.providers.base import ProviderNotConfigured, ProviderUnavailable, Usage
from aadhi.providers.health import classify_failure, provider_health, reset_provider_health
from aadhi.providers.operations import PAYLOAD_KEY
from aadhi.providers.video import omni as omni_mod
from aadhi.providers.video import veo as veo_mod
from aadhi.providers.video.omni import ESTIMATED_SECONDS, VIDEO_TOKENS_PER_SECOND, OmniVideo
from aadhi.providers.video.veo import VeoVideo
from aadhi.schemas.screenplay import Screenplay
from aadhi.usage.pricing import (
    OMNI_VIDEO_TOKENS_PER_SECOND,
    OMNI_VIDEO_USD_PER_MILLION_TOKENS,
    estimate_cost,
    reset_price_cache,
)
from tests.helpers import FakeJobContext
from tests.pipeline.fakes import install_fast_audio, install_providers
from tests.providers.media_fakes import Crash

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import gemini_error, gemini_factory
from .test_omni import Checkpoint, interaction, omni_factory, running, sdk_timeout

KEY = GEMINI_KEYS[0]


# --- settings: the order and the automatic successor ----------------------------------------------------------


def test_video_order_and_the_automatic_successor(make_settings) -> None:
    assert Settings.model_fields["video_provider"].default == "none"  # AI video stays off by default
    defaults = make_settings(video_provider="none")
    assert (defaults.video_fallback_providers, defaults.video_providers) == ([], [])
    assert defaults.omni_model == "gemini-omni-1.1-flash"
    assert factory.media_chain("video", defaults) == []

    # a chain with veo gets omni as its last backup (Veo 3.1's replacement)
    assert factory.media_chain("video", make_settings(video_provider="veo")) == ["veo", "omni"]
    assert factory.media_chain("video", make_settings(video_provider="veo", video_fallback_providers="fake")) == [
        "veo", "fake", "omni"]
    assert factory.media_chain("video", make_settings(video_provider="fake", video_fallback_providers="veo")) == [
        "fake", "veo", "omni"]
    assert factory.media_chain("video", make_settings(video_provider="omni", video_fallback_providers="veo")) == [
        "omni", "veo"]
    assert factory.media_chain("video", make_settings(video_provider="fake")) == ["fake"]  # no veo: unchanged
    assert make_settings(video_provider="none", video_fallback_providers="veo").video_fallback_providers == ["veo"]

    # VIDEO_PROVIDERS is the exact order (no automatic omni) and wins over the two older settings
    s = make_settings(video_providers="omni, VEO,omni", video_provider="fake", video_fallback_providers="fake")
    assert (s.video_provider, s.video_fallback_providers, s.video_providers) == ("omni", ["veo"], ["omni", "veo"])
    assert factory.media_chain("video", make_settings(video_providers="veo")) == ["veo"]
    assert factory.media_chain("video", make_settings(video_providers="none", video_provider="veo")) == []
    fakes = make_settings(video_providers="veo,fake").fake_providers()  # still refused in production
    assert "VIDEO_FALLBACK_PROVIDERS" in fakes and "VIDEO_PROVIDER" not in fakes
    for bad in ("veo,pollinations", "none,veo", "gemini"):
        with pytest.raises(ValueError, match="VIDEO_PROVIDERS"):
            make_settings(video_providers=bad)
    assert make_settings(video_fallback_providers="omni").video_fallback_providers == ["omni"]


def test_factory_and_status_panel(make_settings, monkeypatch) -> None:
    nokey = make_settings(video_provider="veo")
    assert factory.media_unavailable_reason("video", "omni", nokey) == "no Gemini API key is configured"
    with pytest.raises(ProviderNotConfigured):
        factory.get_video(nokey, "omni")
    assert factory.media_class("video", "omni") is OmniVideo and "omni" in factory.VIDEO_PROVIDERS

    s = make_settings(video_provider="veo", gemini_api_key=KEY)
    built = factory.get_video(s, "omni")
    assert isinstance(built, OmniVideo) and built.model == "gemini-omni-1.1-flash"
    assert factory.get_video(make_settings(video_provider="omni", gemini_api_key=KEY)).name == "omni"
    status = factory.media_provider_status(s)["video"]
    veo, omni = status["providers"]
    assert (veo["name"], veo["role"], omni["name"], omni["role"]) == ("veo", "preferred", "omni", "backup")
    assert (omni["label"], omni["state"], omni["paid"], omni["model"]) == (
        "Google Gemini Omni", "available", True, "gemini-omni-1.1-flash")
    assert KEY not in repr(status)


@pytest.mark.asyncio
async def test_a_veo_model_that_is_no_longer_served_is_unavailable(make_settings) -> None:
    s = make_settings(gemini_api_key=KEY, video_provider="veo")
    veo_factory, clients = gemini_factory({KEY: [shut_down()]})
    cp, usages = Checkpoint(), []
    with pytest.raises(ProviderUnavailable) as info:
        await VeoVideo(s, client_factory=veo_factory, sleep=no_sleep).generate("x", on_usage=usages.append,
                                                                               checkpoint=cp)
    assert info.value.status == 404 and "VEO_MODEL" in str(info.value) and "veo-3.1-fast" in str(info.value)
    assert classify_failure(info.value) == "unavailable" and usages == []  # no job was created: nothing billed
    assert [e[0] for e in cp.events] == ["sending", "not_sent"] and len(clients[KEY].models.calls) == 1

    reset_provider_health()
    try:
        provider_health().note_failure("veo", "server", info.value, s)
        veo = factory.media_provider_status(s)["video"]["providers"][0]
        assert veo["state"] == "cooling" and "no longer served" in veo["reason"]
    finally:
        reset_provider_health()


# --- pricing ---------------------------------------------------------------------------------------------------


def test_omni_price_rows(app_env) -> None:
    reset_price_cache()
    assert OMNI_VIDEO_TOKENS_PER_SECOND == VIDEO_TOKENS_PER_SECOND  # adapter and price table agree
    tokens = 8 * VIDEO_TOKENS_PER_SECOND + 1234
    exact = tokens * OMNI_VIDEO_USD_PER_MILLION_TOKENS / 1_000_000
    usage = Usage("omni", "gemini-omni-1.1-flash", "video", seconds=round(tokens / VIDEO_TOKENS_PER_SECOND, 3))
    assert estimate_cost(usage, app_env) == pytest.approx(exact, abs=1e-4)  # per token, as Google bills it
    assert estimate_cost(Usage("omni", "gemini-omni-1.1-flash", "video", seconds=8), app_env) == pytest.approx(0.81088)
    # a model the omni row does not list (e.g. VEO_MODEL) is still priced by the omni row
    estimate = Usage("omni", app_env.veo_model, "video", seconds=ESTIMATED_SECONDS)
    assert estimate_cost(estimate, app_env) == pytest.approx(1.0136)
    assert estimate_cost(Usage("omni", "gemini-omni-flash-preview", "video", seconds=1), app_env) == pytest.approx(0.10136)


# --- build_assets with the real adapters ------------------------------------------------------------------------


def beat(i: str, text: str) -> dict[str, Any]:
    return {"id": i, "narration": text}


def video_scene(sid: str = "lab", prompt: str = "a lathe cutting metal") -> dict[str, Any]:
    return {"id": sid, "type": "ai_video", "title": sid, "video_prompt": prompt, "rationale": "real",
            "fallback_image_prompt": "a lathe", "beats": [beat(f"{sid}-b1", "See the lathe.")]}


def screenplay(*scenes: dict[str, Any]) -> Screenplay:
    return Screenplay.model_validate({"language": "en-IN", "scenes": list(scenes)})


def build(ctx: Any, sp: Screenplay) -> Any:
    return asyncio.run(build_assets_detailed(ctx, sp, GenerationOptions(allow_ai_video=True)))


def codes(res: Any) -> set[tuple[str, str | None]]:
    return {(i.code, i.scene_id) for i in res.issues}


def _job_row() -> int:
    from aadhi.db import session_scope
    from aadhi.jobs.queue import enqueue

    with session_scope() as db:
        return enqueue(db, "build_assets", payload={}).id


def _payload(job_id: int) -> dict[str, Any]:
    from aadhi.db import session_scope
    from aadhi.models import Job

    with session_scope() as db:
        return dict(db.get(Job, job_id).payload or {})


def _restart(ctx: FakeJobContext, job_id: int) -> FakeJobContext:
    """The worker claims the same job again: next attempt, payload reloaded from the jobs row."""
    return FakeJobContext(settings=ctx.settings, assets=ctx.assets, job_id=job_id, attempt=ctx.attempt + 1,
                          payload=_payload(job_id), user_id=ctx.user_id)


class Chain:
    """Real Veo (primary) and Omni (backup) adapters over in-memory SDK clients."""

    def __init__(self, ctx: FakeJobContext, veo_script: list[Any], omni_creates: list[Any], omni_gets: list[Any],
                 veo_ops: list[Any] | None = None):
        self.ctx = ctx
        settings = ctx.settings
        self.veo_factory, self.veo_clients = gemini_factory({KEY: veo_script}, ops={KEY: veo_ops or []})
        self.omni_factory, self.omni_clients = omni_factory(creates={KEY: omni_creates}, gets={KEY: omni_gets})
        self.veo = VeoVideo(settings, client_factory=self.veo_factory, sleep=no_sleep, poll_interval_s=0)
        self.omni = OmniVideo(settings, client_factory=self.omni_factory, sleep=no_sleep, poll_interval_s=0)

    @property
    def omni_api(self) -> Any:
        return self.omni_clients[KEY].interactions if KEY in self.omni_clients else None


@pytest.fixture()
def chain(job_ctx, monkeypatch):
    install_providers(monkeypatch)
    install_fast_audio(monkeypatch)
    reset_provider_health()

    async def probe(data, settings=None):
        from aadhi.providers.media import MediaInfo

        return MediaInfo(duration=8.0, width=1280, height=720, has_video=True)

    monkeypatch.setattr(omni_mod, "probe_media", probe)
    monkeypatch.setattr(veo_mod, "probe_media", probe)

    def configure(**env: str) -> Settings:
        """Real settings of a server configured with ``env`` (validators included: VIDEO_PROVIDER=veo alone gets
        omni as its automatic successor, ``Settings.auto_video_backup``)."""
        for name, value in {"VIDEO_PROVIDER": "veo", "GEMINI_API_KEY": KEY, "GEMINI_API_KEYS": "", **env}.items():
            monkeypatch.setenv(name, value)
        config.get_settings.cache_clear()
        job_ctx.settings = config.get_settings()
        return job_ctx.settings

    settings = configure()
    assert (settings.video_fallback_providers, settings.auto_video_backup) == (["omni"], "omni")
    holder: dict[str, Chain] = {}

    def make(veo_script: list[Any] | None = None, omni_creates: list[Any] | None = None,
             omni_gets: list[Any] | None = None, veo_ops: list[Any] | None = None) -> Chain:
        c = Chain(job_ctx, veo_script or [], omni_creates or [], omni_gets or [], veo_ops)
        holder["chain"] = c
        return c

    make.configure = configure  # type: ignore[attr-defined]

    monkeypatch.setattr(integrations, "get_video", lambda settings: holder["chain"].veo)

    def backup(kind: str, name: str, settings: Any) -> Any:
        if kind == "video" and name == "omni":
            return holder["chain"].omni
        raise ProviderNotConfigured(f"{name}: not configured", provider=name)

    monkeypatch.setattr(assets_mod, "_backup_provider", backup)
    yield make
    reset_provider_health()


def shut_down() -> Exception:
    return gemini_error(404, "models/veo-3.1-fast-generate-preview is not found for API version v1beta", "NOT_FOUND")


def veo_op(done: bool, video: Any = None) -> Any:
    """A Veo long-running operation as the SDK returns it."""
    from types import SimpleNamespace

    response = SimpleNamespace(generated_videos=[SimpleNamespace(video=video)]) if video is not None else None
    return SimpleNamespace(name="operations/1", done=done, error=None, response=response, result=None)


def veo_times_out(monkeypatch) -> None:
    """Every Veo poll happens 100 s after the previous one: an accepted job passes AI_VIDEO_TIMEOUT_SECONDS."""
    from types import SimpleNamespace

    clock = iter(range(0, 1_000_000, 100))
    monkeypatch.setattr(veo_mod, "time", SimpleNamespace(monotonic=lambda: next(clock)))


def test_veo_shut_down_switches_to_omni_automatically(chain) -> None:
    c = chain(veo_script=[shut_down()], omni_creates=[running(ident="int-1"), running(ident="int-2")],
              omni_gets=[interaction(ident="int-1", tokens=VIDEO_TOKENS_PER_SECOND * 8),
                         interaction(ident="int-2", tokens=VIDEO_TOKENS_PER_SECOND * 8)])
    res = build(c.ctx, screenplay(video_scene("a", "a lathe cutting metal")))
    main = res.manifest.media["a"].main
    assert main.source == "veo" and not res.manifest.media["a"].main_is_fallback  # a generated clip
    asset = c.ctx.assets.get(main.asset_key)
    assert asset.meta["provider"] == "omni" and asset.meta["requested_provider"] == "veo"
    assert asset.meta["model"] == c.ctx.settings.omni_model  # provenance names the model that made the clip
    assert ("assets.video_fallback", "a") in codes(res)
    cooling = provider_health().cooling("veo", "server")
    assert cooling is not None and cooling.category == "unavailable"  # Veo now waits behind omni
    assert [u.provider for u in c.ctx.usages if u.operation == "video"] == ["omni"]  # the 404 billed nothing

    # the next clip goes to omni first: the shut-down model is not asked again during the cooldown
    res = build(c.ctx, screenplay(video_scene("b", "a milling machine")))
    assert c.ctx.assets.get(res.manifest.media["b"].main.asset_key).meta["provider"] == "omni"
    assert len(c.veo_clients[KEY].models.calls) == 1
    assert len(c.omni_api.create_calls) == 2


def test_clip_keys_name_the_model_of_their_provider(make_settings) -> None:
    """Omni clips are keyed by OMNI_MODEL (a new Omni model makes new clips); Veo's and the other providers' keys
    still carry VEO_MODEL exactly as before, so no cached clip is orphaned."""
    from types import SimpleNamespace

    from aadhi.pipeline.assets import _Builder
    from aadhi.schemas.screenplay import AIVideoScene
    from aadhi.storage.assets import compute_key

    def keys(settings: Settings, name: str) -> list[str]:
        builder = SimpleNamespace(settings=settings)
        builder._key_models = lambda field: _Builder._key_models(builder, field)
        scene = AIVideoScene.model_validate(video_scene())
        return _Builder._video_keys(builder, scene, name)

    settings = make_settings()
    veo_before = compute_key("video", {"provider": "veo", "model": settings.veo_model, "aspect": "16:9",
                                       "prompt": "a lathe cutting metal"})
    assert keys(settings, "veo")[0] == veo_before
    assert keys(settings, "omni")[0] == compute_key("video", {"provider": "omni", "model": settings.omni_model,
                                                              "aspect": "16:9", "prompt": "a lathe cutting metal"})
    assert keys(settings, "fake")[0] == compute_key("video", {"provider": "fake", "model": settings.veo_model,
                                                              "aspect": "16:9", "prompt": "a lathe cutting metal"})
    newer = make_settings(omni_model="gemini-omni-2-flash")
    assert keys(newer, "omni")[0] != keys(settings, "omni")[0]  # a new Omni model means a new clip
    assert keys(newer, "veo") == keys(settings, "veo")


def test_the_budget_estimate_uses_the_omni_price(chain) -> None:
    # Veo's estimate (8 s x 0.10) fits, omni's (10 s x 0.10136) does not: a still image, nothing sent to omni
    c = chain(veo_script=[shut_down()])
    c.ctx.budget_usd = 0.95
    res = build(c.ctx, screenplay(video_scene()))
    assert res.manifest.media["lab"].main_is_fallback and c.omni_api is None

    c = chain(veo_script=[shut_down()], omni_creates=[running()],
              omni_gets=[interaction(tokens=VIDEO_TOKENS_PER_SECOND * 8)])
    c.ctx.budget_usd = 1.05
    reset_provider_health()
    res = build(c.ctx, screenplay(video_scene("other", "a drill press")))
    assert not res.manifest.media["other"].main_is_fallback
    assert c.ctx.cost_usd == pytest.approx(0.81088)


def test_an_omni_job_resumes_after_a_restart_and_is_paid_once(chain) -> None:
    c = chain(veo_script=[shut_down()], omni_creates=[running(ident="int-5")],
              omni_gets=[Crash("killed while polling"), interaction(ident="int-5", tokens=VIDEO_TOKENS_PER_SECOND * 6)])
    c.ctx.job_id = _job_row()
    with pytest.raises(Crash):
        build(c.ctx, screenplay(video_scene()))
    record = next(r for r in _payload(c.ctx.job_id)[PAYLOAD_KEY].values() if r["provider"] == "omni")
    assert record["status"] == "submitted" and record["operation"] == "int-5" and "AIza" not in str(record)

    ctx2 = _restart(c.ctx, c.ctx.job_id)
    res = build(ctx2, screenplay(video_scene()))
    main = res.manifest.media["lab"].main
    assert not res.manifest.media["lab"].main_is_fallback
    assert ctx2.assets.get(main.asset_key).meta["resumed"] is True
    assert len(c.omni_api.create_calls) == 1 and c.omni_api.get_calls == ["int-5", "int-5"]
    assert [(u.seconds, u.meta["status"]) for u in ctx2.usages if u.provider == "omni"] == [(6.0, "generated")]
    omni_record = next(r for r in _payload(c.ctx.job_id)[PAYLOAD_KEY].values() if r["provider"] == "omni")
    assert omni_record["status"] == "completed"


def test_an_unanswered_omni_create_is_never_sent_again(chain) -> None:
    chain.configure(VIDEO_PROVIDER="omni")  # the preferred provider is omni (no veo: nothing added)
    c = chain(omni_creates=[Crash("killed before the answer"), running()])
    assert (c.ctx.settings.video_fallback_providers, c.ctx.settings.auto_video_backup) == ([], "")
    c.veo = c.omni  # the preferred provider is omni now
    c.ctx.job_id = _job_row()
    with pytest.raises(Crash):
        build(c.ctx, screenplay(video_scene()))
    record = next(iter(_payload(c.ctx.job_id)[PAYLOAD_KEY].values()))
    assert (record["provider"], record["status"]) == ("omni", "submitting")

    res = build(_restart(c.ctx, c.ctx.job_id), screenplay(video_scene()))
    assert res.manifest.media["lab"].main_is_fallback
    assert ("video.ambiguous_submission", "lab") in codes(res)
    assert len(c.omni_api.create_calls) == 1, "a job that may already be billed is never submitted again automatically"


def test_an_omni_create_without_an_answer_is_not_sent_again_in_the_same_job(chain) -> None:
    """A timeout on the create request (no answer; the job may exist and be billed) is not retried in the live
    process either: a still image, the ambiguous issue, and no second create."""
    chain.configure(VIDEO_PROVIDER="omni")
    c = chain(omni_creates=[sdk_timeout(), running()], omni_gets=[interaction(tokens=VIDEO_TOKENS_PER_SECOND * 8)])
    c.veo = c.omni
    c.ctx.job_id = _job_row()
    res = build(c.ctx, screenplay(video_scene()))
    assert res.manifest.media["lab"].main_is_fallback
    assert ("video.ambiguous_submission", "lab") in codes(res)
    assert len(c.omni_api.create_calls) == 1, "a create that may already be billed is never sent again"
    record = next(iter(_payload(c.ctx.job_id)[PAYLOAD_KEY].values()))
    assert (record["provider"], record["status"]) == ("omni", "ambiguous")


# --- the automatic successor is Veo's replacement, not a second paid try ------------------------------------------


def test_a_veo_timeout_never_pays_omni_for_the_same_clip(chain, monkeypatch) -> None:
    """Veo accepted the job (it may still finish and bill) but polling passed the deadline: a still image, as before
    B0, and nothing is sent to the automatic successor. Within Veo's short cooldown the next clip asks Veo again
    (omni is skipped, not moved ahead)."""
    veo_times_out(monkeypatch)
    c = chain(veo_script=[veo_op(False), veo_op(False)], veo_ops=[veo_op(False)] * 50)
    res = build(c.ctx, screenplay(video_scene("a", "a lathe cutting metal")))
    assert res.manifest.media["a"].main_is_fallback
    assert c.omni_api is None  # no Omni client was even made
    assert [(u.provider, u.meta["status"]) for u in c.ctx.usages if u.operation == "video"] == [("veo", "abandoned")]
    cooling = provider_health().cooling("veo", "server")
    assert cooling is not None and cooling.category == "transient"

    res = build(c.ctx, screenplay(video_scene("b", "a milling machine")))
    assert res.manifest.media["b"].main_is_fallback
    assert len(c.veo_clients[KEY].models.calls) == 2 and c.omni_api is None
    assert [(u.provider, u.meta["status"]) for u in c.ctx.usages if u.operation == "video"] == [
        ("veo", "abandoned"), ("veo", "abandoned")]


def test_bad_veo_output_after_billing_never_pays_omni(chain, monkeypatch) -> None:
    from google.genai import types as gtypes

    async def too_short(data, settings=None):
        from aadhi.providers.media import MediaInfo

        return MediaInfo(duration=0.1, width=1280, height=720, has_video=True)

    monkeypatch.setattr(veo_mod, "probe_media", too_short)
    clip = gtypes.Video(video_bytes=b"veo-clip-bytes", mime_type="video/mp4")
    c = chain(veo_script=[veo_op(True, clip)])
    res = build(c.ctx, screenplay(video_scene()))
    assert res.manifest.media["lab"].main_is_fallback and c.omni_api is None
    assert [(u.provider, u.meta["status"]) for u in c.ctx.usages if u.operation == "video"] == [("veo", "generated")]


def test_a_named_omni_backup_is_still_tried_after_a_veo_timeout(chain, monkeypatch) -> None:
    """VIDEO_FALLBACK_PROVIDERS=omni (or VIDEO_PROVIDERS=veo,omni) opts in to Omni after any Veo failure that allows
    a fallback, as configured."""
    for env in ({"VIDEO_FALLBACK_PROVIDERS": "omni"}, {"VIDEO_PROVIDERS": "veo,omni"}):
        settings = chain.configure(**env)
        assert (settings.video_fallback_providers, settings.auto_video_backup) == (["omni"], ""), env
        reset_provider_health()
        veo_times_out(monkeypatch)
        c = chain(veo_script=[veo_op(False)], veo_ops=[veo_op(False)] * 50, omni_creates=[running()],
                  omni_gets=[interaction(tokens=VIDEO_TOKENS_PER_SECOND * 8)])
        scene = video_scene(f"lab{len(env)}", f"a lathe ({next(iter(env))})")
        res = build(c.ctx, screenplay(scene))
        main = res.manifest.media[scene["id"]].main
        assert not res.manifest.media[scene["id"]].main_is_fallback, env
        assert c.ctx.assets.get(main.asset_key).meta["provider"] == "omni"
        assert [(u.provider, u.meta["status"]) for u in c.ctx.usages if u.operation == "video"][-2:] == [
            ("veo", "abandoned"), ("omni", "generated")]
        monkeypatch.delenv(next(iter(env)))
