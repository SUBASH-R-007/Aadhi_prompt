"""Media provider chain in build_assets: fallback, error taxonomy, cooldowns, provenance, and paid video
checkpoints (resume after a restart, never re-submit an ambiguous paid job). Offline stand-ins only."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from aadhi.pipeline import assets as assets_mod
from aadhi.pipeline import integrations
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers.base import ContentBlocked, ProviderError, ProviderNotConfigured, ProviderUnavailable, RateLimited
from aadhi.providers.health import provider_health, reset_provider_health
from aadhi.providers.operations import PAYLOAD_KEY
from aadhi.schemas.screenplay import Screenplay
from tests.helpers import FakeJobContext
from tests.providers.media_fakes import Crash, OperationService, ResumableVideo, ScriptedImage, ScriptedVideo

_REAL_BACKUP = assets_mod._backup_provider


def beat(i: str, text: str) -> dict[str, Any]:
    return {"id": i, "narration": text}


def panel_scene(sid: str, prompt: str) -> dict[str, Any]:
    return {"id": sid, "type": "content", "title": sid, "beats": [beat(f"{sid}-b1", f"Look at {prompt}.")],
            "side_panel": {"kind": "image", "image_prompt": prompt, "rationale": "real part"}}


def video_scene(sid: str = "lab", prompt: str = "a lathe cutting metal") -> dict[str, Any]:
    return {"id": sid, "type": "ai_video", "title": sid, "video_prompt": prompt, "rationale": "real",
            "fallback_image_prompt": "a lathe", "beats": [beat(f"{sid}-b1", "See the lathe.")]}


def screenplay(*scenes: dict[str, Any]) -> Screenplay:
    return Screenplay.model_validate({"language": "en-IN", "scenes": list(scenes)})


def http402(provider: str = "pollinations") -> ProviderUnavailable:
    return ProviderUnavailable(f"{provider}: the image endpoint asks for payment or an account (HTTP 402)",
                               status=402, provider=provider)


@pytest.fixture(autouse=True)
def _clean_health():
    reset_provider_health()
    yield
    reset_provider_health()


@pytest.fixture()
def chain(job_ctx, providers, fast_audio, monkeypatch):
    """job_ctx + fake TTS; ``chain.primary`` / ``chain.backups`` drive the image and video chains."""

    class Chain:
        ctx = job_ctx
        primary_image: Any = ScriptedImage("pollinations")
        primary_video: Any = ScriptedVideo("veo", paid=True)
        backups: dict[str, Any] = {}

    c = Chain()
    c.backups = {}
    monkeypatch.setattr(integrations, "get_image", lambda settings: c.primary_image)
    monkeypatch.setattr(integrations, "get_video", lambda settings: c.primary_video)

    def backup(kind: str, name: str, settings: Any) -> Any:
        if name not in c.backups:
            raise ProviderNotConfigured(f"{name}: not configured", provider=name)
        return c.backups[name]

    monkeypatch.setattr(assets_mod, "_backup_provider", backup)
    return c


def configure(ctx: Any, **settings: Any) -> None:
    ctx.settings = ctx.settings.model_copy(update=settings)


def build(ctx: Any, sp: Screenplay, opts: GenerationOptions | None = None, **kw: Any):
    return asyncio.run(build_assets_detailed(ctx, sp, opts or GenerationOptions(allow_ai_video=True), **kw))


def codes(res: Any) -> set[tuple[str, str | None]]:
    return {(i.code, i.scene_id) for i in res.issues}


# --- images -----------------------------------------------------------------------------------------


def test_payment_refusal_falls_back_to_the_backup_with_provenance(chain):
    chain.primary_image.failures = [http402(), http402()]
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    configure(chain.ctx, image_fallback_providers=["gemini"])
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    m = res.manifest
    assert m.media["a"].side_panel is not None
    asset = chain.ctx.assets.get(m.media["a"].side_panel.asset_key)
    assert asset.meta["provider"] == "gemini" and asset.meta["requested_provider"] == "pollinations"
    assert asset.meta["fallback_from"] == ["pollinations: payment or account problem"]
    assert isinstance(asset.meta["generation_ms"], int) and "prompt" in asset.meta
    fallback = [i for i in res.issues if i.code == "assets.image_fallback"]
    assert len(fallback) == 1 and fallback[0].scene_id == "a" and fallback[0].severity == "info"
    assert "a" in m.scene_hashes  # made by the backup: complete
    assert any("trying the next configured provider" in e["message"] for e in chain.ctx.events if e["type"] == "log")
    assert provider_health().cooling("pollinations", "server").category == "unavailable"
    # the next scenes try the backup first while the paywalled provider cools down
    res = build(chain.ctx, screenplay(panel_scene("b", "a diode")))
    assert res.manifest.media["b"].side_panel is not None and ("assets.image_fallback", "b") in codes(res)
    assert len(chain.primary_image.calls) == 1 and len(chain.backups["gemini"].calls) == 2


def test_payment_refusal_without_a_backup_is_not_retried_every_build(chain):
    chain.primary_image.failures = [http402()]
    sp = screenplay(panel_scene("a", "a transistor"))
    first = build(chain.ctx, sp).manifest
    assert first.media["a"].side_panel is None and any("payment" in w for w in first.media["a"].warnings)
    assert "a" in first.scene_hashes  # permanent like a safety refusal: the scene keeps its hash
    again = build(chain.ctx, sp, previous=first)
    assert "a" in again.reused and len(chain.primary_image.calls) == 1


def test_a_safety_refusal_is_never_sent_to_another_provider(chain):
    chain.primary_image.failures = [ContentBlocked("pollinations: blocked", provider="pollinations")]
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    configure(chain.ctx, image_fallback_providers=["gemini"])
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    assert res.manifest.media["a"].side_panel is None and "a" in res.manifest.scene_hashes
    assert chain.backups["gemini"].calls == []


def test_a_refused_personal_key_fails_the_job_instead_of_falling_back(chain):
    chain.primary_image = ScriptedImage("gemini", paid=True, failures=[
        ProviderError("gemini: image generation failed (HTTP 403)", status=403, provider="gemini")])
    chain.backups["pollinations"] = ScriptedImage("pollinations")
    configure(chain.ctx, image_provider="gemini", image_fallback_providers=["pollinations"])
    chain.ctx.key_sources = {"gemini": "personal"}
    with pytest.raises(ProviderError) as info:
        build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    assert info.value.status == 403 and chain.backups["pollinations"].calls == []
    assert provider_health().cooling("gemini", "server") is None  # a user's key never demotes the server's provider


def test_when_every_provider_fails_a_transient_failure_keeps_the_scene_stale(chain):
    chain.primary_image.failures = [http402()]
    chain.backups["gemini"] = ScriptedImage("gemini", failures=[
        ProviderError("gemini: image generation failed (HTTP 503)", status=503, provider="gemini")])
    configure(chain.ctx, image_fallback_providers=["gemini"])
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    m = res.manifest
    assert m.media["a"].side_panel is None and "a" not in m.scene_hashes  # the next build retries
    assert any("HTTP 503" in w for w in m.media["a"].warnings)


def test_an_image_a_backup_made_is_reused_rather_than_paid_again(chain):
    chain.primary_image.failures = [http402()]
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    configure(chain.ctx, image_fallback_providers=["gemini"])
    sp = screenplay(panel_scene("a", "a transistor"))
    first = build(chain.ctx, sp).manifest
    reset_provider_health()  # the preferred provider works again...
    again = build(chain.ctx, sp, previous=first, scene_ids=["a"])
    assert again.manifest.media["a"].side_panel.asset_key == first.media["a"].side_panel.asset_key
    assert len(chain.primary_image.calls) == 1 and len(chain.backups["gemini"].calls) == 1  # ...but nothing is paid twice


def test_rate_limits_fall_back_only_when_a_backup_exists(chain):
    chain.primary_image.failures = [RateLimited("pollinations: rate limited", status=429, provider="pollinations")]
    with pytest.raises(RateLimited):  # unchanged single-provider behaviour: the job is retried later
        build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    reset_provider_health()
    chain.primary_image.failures = [RateLimited("pollinations: rate limited", status=429, provider="pollinations")]
    chain.backups["fake"] = ScriptedImage("fake")
    configure(chain.ctx, image_fallback_providers=["fake"])
    res = build(chain.ctx, screenplay(panel_scene("b", "a diode")))
    assert res.manifest.media["b"].side_panel is not None
    assert provider_health().cooling("pollinations", "server").category == "rate_limited"


def test_an_unconfigured_backup_is_skipped_and_the_original_failure_reported(chain, monkeypatch):
    monkeypatch.setattr(assets_mod, "_backup_provider", _REAL_BACKUP)  # the real factory: no Gemini key in tests
    chain.primary_image.failures = [http402()]
    configure(chain.ctx, image_fallback_providers=["gemini"])
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    assert res.manifest.media["a"].side_panel is None and any("payment" in w for w in res.manifest.media["a"].warnings)


def test_quality_findings_become_a_warning_issue(chain):
    chain.primary_image.warnings = ["the generated picture is a single flat colour"]
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    suspect = [i for i in res.issues if i.code == "assets.media_suspect"]
    assert len(suspect) == 1 and suspect[0].scene_id == "a" and "flat colour" in suspect[0].message
    asset = chain.ctx.assets.get(res.manifest.media["a"].side_panel.asset_key)
    assert asset.meta["warnings"] == ["the generated picture is a single flat colour"]
    assert asset.meta["provider"] == "pollinations" and "requested_provider" not in asset.meta


def test_a_lectures_own_image_provider_goes_first(chain):
    class Options(GenerationOptions):  # GenerationOptions.image_provider (pipeline owners add the field)
        image_provider: str | None = None

    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")), Options(image_provider="gemini"))
    assert chain.ctx.assets.get(res.manifest.media["a"].side_panel.asset_key).meta["provider"] == "gemini"
    assert chain.primary_image.calls == [] and not [i for i in res.issues if i.code.endswith("_fallback")]
    res = build(chain.ctx, screenplay(panel_scene("b", "a diode")), Options(image_provider="unknown"))
    fallback = [i for i in res.issues if i.code == "assets.image_provider_fallback"]
    assert len(fallback) == 1 and "unknown" in fallback[0].message
    assert res.manifest.media["b"].side_panel is not None


# --- AI video: fallback ------------------------------------------------------------------------------


def test_video_falls_back_to_the_backup(chain):
    chain.primary_video.failures = [ProviderError("veo: video generation failed (HTTP 503)", status=503, provider="veo")]
    chain.backups["fake"] = ScriptedVideo("fake")
    configure(chain.ctx, video_provider="veo", video_fallback_providers=["fake"])
    res = build(chain.ctx, screenplay(video_scene()))
    main = res.manifest.media["lab"].main
    assert main.source == "veo" and not res.manifest.media["lab"].main_is_fallback
    asset = chain.ctx.assets.get(main.asset_key)
    assert asset.meta["provider"] == "fake" and asset.meta["requested_provider"] == "veo"
    assert ("assets.video_fallback", "lab") in codes(res)


# --- AI video: checkpoints ----------------------------------------------------------------------------


def _job_row(payload: dict[str, Any] | None = None) -> int:
    from aadhi.db import session_scope
    from aadhi.jobs.queue import enqueue

    with session_scope() as db:
        return enqueue(db, "build_assets", payload=payload or {}).id


def _payload(job_id: int) -> dict[str, Any]:
    from aadhi.db import session_scope
    from aadhi.models import Job

    with session_scope() as db:
        return dict(db.get(Job, job_id).payload or {})


def _restart(ctx: FakeJobContext, job_id: int) -> FakeJobContext:
    """The worker claims the same job again: next attempt, payload reloaded from the jobs row."""
    return FakeJobContext(settings=ctx.settings, assets=ctx.assets, job_id=job_id, attempt=ctx.attempt + 1,
                          payload=_payload(job_id), user_id=ctx.user_id)


@pytest.fixture()
def veo(chain):
    service = OperationService()
    chain.primary_video = ResumableVideo(service)
    configure(chain.ctx, video_provider="veo")
    chain.ctx.job_id = _job_row()
    return service


def test_a_restart_while_polling_resumes_the_same_job_and_pays_once(chain, veo):
    chain.primary_video.crash_after_checkpoint = True
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    record = next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))
    assert record["status"] == "submitted" and record["operation"] == "operations/op-1"
    assert record["key_fingerprint"] == "fp-server" and "AIza" not in str(record)

    chain.primary_video.crash_after_checkpoint = False
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    res = build(ctx2, screenplay(video_scene()))
    main = res.manifest.media["lab"].main
    assert main.source == "veo" and not res.manifest.media["lab"].main_is_fallback
    assert veo.submits == 1 and chain.primary_video.resumes == ["operations/op-1"]
    assert [u.meta["status"] for u in chain.primary_video.usage] == ["generated"]  # billed once
    assert ctx2.assets.get(main.asset_key).meta["resumed"] is True
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "completed"
    assert any("Resuming the AI video" in e["message"] for e in ctx2.events if e["type"] == "log")
    assert "lab" in res.manifest.scene_hashes


def test_an_unconfirmed_paid_submission_is_not_resubmitted(chain, veo):
    chain.primary_video.crash_before_answer = True
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "submitting"

    chain.primary_video.crash_before_answer = False
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    res = build(ctx2, screenplay(video_scene()))
    media = res.manifest.media["lab"]
    assert veo.submits == 1, "a job that may already be billed is never submitted again automatically"
    assert media.main_is_fallback and media.main is not None and media.main.source == "fallback"
    assert ("video.ambiguous_submission", "lab") in codes(res)
    assert "lab" in res.manifest.scene_hashes  # not retried by every build
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "ambiguous"
    assert any("may already have been billed" in e["message"] for e in ctx2.events if e["type"] == "log")

    # An explicit rebuild is a new job (fresh payload): it generates the clip once.
    ctx3 = FakeJobContext(settings=ctx2.settings, assets=ctx2.assets, job_id=_job_row())
    again = build(ctx3, screenplay(video_scene()), previous=res.manifest, scene_ids=["lab"])
    assert veo.submits == 2 and not again.manifest.media["lab"].main_is_fallback


def test_a_vanished_job_shows_the_still_with_an_issue(chain, veo):
    chain.primary_video.crash_after_checkpoint = True
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    chain.primary_video.crash_after_checkpoint = False
    chain.primary_video.mode = "vanish"
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    res = build(ctx2, screenplay(video_scene()))
    assert veo.submits == 1 and res.manifest.media["lab"].main_is_fallback
    assert ("video.operation_lost", "lab") in codes(res)
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "lost"


def test_a_failure_in_a_live_process_is_not_ambiguous(chain, veo):
    chain.primary_video.mode = "fail"
    first = build(chain.ctx, screenplay(video_scene()))
    assert first.manifest.media["lab"].main_is_fallback and "lab" not in first.manifest.scene_hashes
    # accepted, then failed while this live process followed it: finished, nothing to resume
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "abandoned"
    chain.primary_video.mode = ""
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)  # e.g. a retry of the same job
    res = build(ctx2, screenplay(video_scene()), previous=first.manifest)
    assert veo.submits == 2 and not res.manifest.media["lab"].main_is_fallback


def test_personal_key_jobs_are_never_resumed_in_another_scope(chain, veo):
    chain.ctx.user_id = 5
    chain.ctx.key_sources = {"gemini": "personal"}
    chain.primary_video.crash_after_checkpoint = True
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    (ident, record), = _payload(chain.ctx.job_id)[PAYLOAD_KEY].items()
    assert record["scope"] == "user:5" and ident.endswith("#user:5")
    chain.primary_video.crash_after_checkpoint = False
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    ctx2.key_sources = {"gemini": "server"}  # the user removed their key: the server key pays now
    res = build(ctx2, screenplay(video_scene()))
    assert chain.primary_video.resumes == [] and veo.submits == 2  # the user's job is not followed with the server key
    assert not res.manifest.media["lab"].main_is_fallback


# --- media identity and earlier results (integration of storage.assets.media_key / first_existing) -----


def test_image_prompts_are_normalised_before_they_are_keyed_and_sent(chain):
    from aadhi.storage.assets import media_key

    res = build(chain.ctx, screenplay(panel_scene("a", "a  transistor\n on a\tbench")))
    sent = chain.primary_image.calls[0]
    assert "  " not in sent and "\n" not in sent and "\t" not in sent and sent.startswith("a transistor on a bench.")
    key = res.manifest.media["a"].side_panel.asset_key
    assert key == media_key("image", sent, provider="pollinations", model="", aspect=assets_mod.PANEL_IMAGE_ASPECT)


def test_media_cached_under_the_pre_normalisation_key_is_reused_not_paid_again(chain):
    from aadhi.storage.assets import Produced, compute_key
    from tests.providers.media_fakes import gradient_png

    prompt = "a  transistor\n on a bench"
    full = f"{prompt.strip().rstrip('.')}. {chain.ctx.settings.image_style_suffix}".strip()
    legacy = compute_key("image", {"prompt": full, "provider": "pollinations", "model": "",
                                   "aspect": assets_mod.PANEL_IMAGE_ASPECT})
    chain.ctx.assets.put(legacy, "image", Produced(data=gradient_png(), mime="image/png", width=64, height=48))
    res = build(chain.ctx, screenplay(panel_scene("a", prompt)))
    assert res.manifest.media["a"].side_panel.asset_key == legacy
    assert chain.primary_image.calls == []
    assert ("assets.image_fallback", "a") not in codes(res)  # made by the requested provider: no provenance note


def test_images_made_earlier_are_used_when_generation_is_off_on_the_server(chain):
    sp = screenplay(panel_scene("a", "a transistor"))
    first = build(chain.ctx, sp).manifest.media["a"].side_panel
    assert first is not None and len(chain.primary_image.calls) == 1
    chain.primary_image = None  # IMAGE_PROVIDER=none: the integrations seam returns no provider
    configure(chain.ctx, image_provider="none")
    res = build(chain.ctx, sp)
    panel = res.manifest.media["a"].side_panel
    assert panel is not None and panel.asset_key == first.asset_key
    cached = [i for i in res.issues if i.code == "assets.cached_media_used"]
    assert len(cached) == 1 and cached[0].severity == "info" and cached[0].scene_id == "a"
    # the teacher's own opt-out always wins: nothing generated earlier is used
    off = build(chain.ctx, sp, GenerationOptions(allow_generated_images=False))
    assert off.manifest.media["a"].side_panel is None
    assert not any(i.code == "assets.cached_media_used" for i in off.issues)


def test_clips_made_earlier_are_used_when_ai_video_is_off_on_the_server(chain):
    sp = screenplay(video_scene())
    first = build(chain.ctx, sp).manifest.media["lab"].main
    assert first is not None and first.source == "veo"
    made_by = chain.primary_video
    chain.primary_video = None
    configure(chain.ctx, video_provider="none")
    res = build(chain.ctx, sp)
    media = res.manifest.media["lab"]
    assert media.main is not None and media.main.asset_key == first.asset_key and not media.main_is_fallback
    assert ("assets.cached_media_used", "lab") in codes(res)
    assert len(made_by.calls) == 1  # generated once, by the first build
    off = build(chain.ctx, sp, GenerationOptions(allow_ai_video=False))
    assert off.manifest.media["lab"].main is None or off.manifest.media["lab"].main_is_fallback


def test_collecting_an_already_submitted_clip_skips_the_still_wanted_guard(chain, veo):
    from aadhi.jobs.base import JobCancelled
    from aadhi.storage.assets import generation_guard

    def refuse(kind: str, key: str) -> None:
        raise JobCancelled("the version changed")

    chain.primary_video.crash_after_checkpoint = True
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    chain.primary_video.crash_after_checkpoint = False
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)

    async def run() -> Any:
        with generation_guard(refuse):  # the version changed meanwhile: nothing new may be paid for...
            return await build_assets_detailed(ctx2, screenplay(video_scene()), GenerationOptions(allow_ai_video=True))

    res = asyncio.run(run())
    assert veo.submits == 1 and chain.primary_video.resumes == ["operations/op-1"]  # ...but a billed clip is kept
    assert not res.manifest.media["lab"].main_is_fallback

    # control: a clip that was never submitted is refused by the same guard before anything is paid
    async def fresh() -> Any:
        with generation_guard(refuse):
            ctx3 = FakeJobContext(settings=ctx2.settings, assets=ctx2.assets, job_id=_job_row())
            return await build_assets_detailed(ctx3, screenplay(video_scene("other", "a drill press")),
                                               GenerationOptions(allow_ai_video=True))

    with pytest.raises(JobCancelled):
        asyncio.run(fresh())
    assert veo.submits == 1


# --- regressions: budget on resume, explicit image choice, storage failures, cooldown events, sends ----------


def test_a_clip_submitted_before_a_graceful_release_is_collected_despite_the_budget(chain, veo):
    """Veo accepted the clip, then a deploy released the worker while it polled: the abandoned estimate (4 USD)
    was recorded. Collecting the clip costs nothing new, so the budget pre-check (4 + 4 > 5) must not refuse it."""
    chain.primary_video.mode = "release"
    chain.ctx.budget_usd = 5.0
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    assert chain.ctx.cost_usd == pytest.approx(4.0)
    record = next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))
    assert record["status"] == "submitted" and record["usage_recorded"] is True

    chain.primary_video.mode = ""
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    ctx2.cost_usd, ctx2.budget_usd = chain.ctx.cost_usd, 5.0  # the job's spend so far is carried over
    res = build(ctx2, screenplay(video_scene()))
    assert chain.primary_video.resumes == ["operations/op-1"] and veo.submits == 1
    assert not res.manifest.media["lab"].main_is_fallback
    assert ctx2.cost_usd == pytest.approx(4.0)  # nothing billed twice
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "completed"
    # control: a clip never submitted is still refused up front by the same budget
    ctx3 = FakeJobContext(settings=ctx2.settings, assets=ctx2.assets, job_id=_job_row(), budget_usd=5.0,
                          cost_usd=4.0)
    other = build(ctx3, screenplay(video_scene("other", "a drill press")))
    assert other.manifest.media["other"].main_is_fallback and veo.submits == 1


def test_a_lectures_explicit_image_choice_is_not_shadowed_by_the_defaults_cached_image(chain):
    sp = screenplay(panel_scene("a", "a transistor"))
    first = build(chain.ctx, sp, GenerationOptions()).manifest.media["a"].side_panel
    assert len(chain.primary_image.calls) == 1
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    res = build(chain.ctx, sp, GenerationOptions(image_provider="gemini"))
    panel = res.manifest.media["a"].side_panel
    assert len(chain.backups["gemini"].calls) == 1 and panel.asset_key != first.asset_key
    assert chain.ctx.assets.get(panel.asset_key).meta["provider"] == "gemini"
    assert not [i for i in res.issues if i.code == "assets.image_fallback"]


def test_the_defaults_cached_image_is_reused_when_the_explicit_choice_fails(chain):
    sp = screenplay(panel_scene("a", "a transistor"))
    first = build(chain.ctx, sp, GenerationOptions()).manifest.media["a"].side_panel
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True, failures=[
        ProviderError("gemini: image generation failed (HTTP 503)", status=503, provider="gemini")])
    res = build(chain.ctx, sp, GenerationOptions(image_provider="gemini"))
    assert res.manifest.media["a"].side_panel.asset_key == first.asset_key
    assert len(chain.primary_image.calls) == 1, "the cached image is reused, not paid again"
    fallback = [i for i in res.issues if i.code == "assets.image_fallback"]
    assert len(fallback) == 1 and "'pollinations' instead of 'gemini'" in fallback[0].message


@pytest.mark.parametrize("error", [
    OSError(28, "No space left on device"),
    "db",
])
def test_a_storage_failure_after_generation_is_not_paid_again_by_the_backup(chain, monkeypatch, error):
    from sqlalchemy.exc import OperationalError

    if error == "db":
        error = OperationalError("INSERT INTO assets", {}, Exception("database is locked"))
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    configure(chain.ctx, image_fallback_providers=["gemini"])
    real_put = chain.ctx.assets.put

    def put(key, kind, produced, **kw):
        if kind == "image":
            raise error
        return real_put(key, kind, produced, **kw)

    monkeypatch.setattr(chain.ctx.assets, "put", put)
    res = build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    assert len(chain.primary_image.calls) == 1 and chain.backups["gemini"].calls == []
    assert res.manifest.media["a"].side_panel is None and "a" not in res.manifest.scene_hashes
    assert provider_health().cooling("pollinations", "server") is None
    assert not any("trying the next configured provider" in e["message"]
                   for e in chain.ctx.events if e["type"] == "log")


def test_chain_failures_log_their_cooldown_for_the_status_panel(chain):
    chain.primary_image.failures = [http402()]
    chain.backups["gemini"] = ScriptedImage("gemini", paid=True)
    configure(chain.ctx, image_fallback_providers=["gemini"])
    build(chain.ctx, screenplay(panel_scene("a", "a transistor")))
    (event,) = [e for e in chain.ctx.events if e["type"] == "log" and "trying the next" in e["message"]]
    assert event["data"]["scope"] == "server" and event["data"]["kind"] == "image"
    assert event["data"]["cooldown_seconds"] == chain.ctx.settings.media_provider_auth_cooldown_seconds
    assert event["data"]["provider"] == "pollinations" and event["data"]["category"] == "unavailable"
    reset_provider_health()
    chain.primary_image.failures = [http402()]  # the last provider of the chain: no next one, still a cooldown
    configure(chain.ctx, image_fallback_providers=[])
    chain.ctx.events.clear()
    build(chain.ctx, screenplay(panel_scene("b", "a diode")))
    (event,) = [e for e in chain.ctx.events if e["type"] == "log" and "tried after the others" in e["message"]]
    assert event["data"]["cooldown_seconds"] > 0 and event["data"]["scope"] == "server"


# --- a real VeoVideo (fake SDK client): only a request without an answer is ambiguous -----------------------


def _veo(chain, monkeypatch, script, ops=(), sleep=None):
    from aadhi.providers.gemini_common import reset_key_pools
    from aadhi.providers.media import MediaInfo
    from aadhi.providers.video import veo as veo_mod
    from tests.providers.fakes import gemini_factory

    async def probe(data, settings=None):
        return MediaInfo(duration=8.0, width=1280, height=720, has_video=True)

    async def no_sleep(_s):
        return None

    reset_key_pools()
    monkeypatch.setattr(veo_mod, "probe_media", probe)
    key = "AIzaTESTKEY-0001-abcdef"
    configure(chain.ctx, video_provider="veo", gemini_api_key=type(chain.ctx.settings.gemini_api_key)(key),
              gemini_api_keys=[])
    factory_, clients = gemini_factory({key: list(script)}, ops={key: list(ops)})
    factory_(key).aio.files.download_bytes = b"\x00\x00\x00\x18ftypmp42veo"
    chain.primary_video = veo_mod.VeoVideo(chain.ctx.settings, client_factory=factory_, sleep=sleep or no_sleep,
                                           poll_interval_s=0, max_attempts=3)
    return clients[key]


def _done_op(name="operations/abc"):
    from types import SimpleNamespace

    from google.genai import types as gtypes

    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    response = SimpleNamespace(generated_videos=[SimpleNamespace(video=video)])
    return SimpleNamespace(name=name, done=True, error=None, response=response, result=None)


def _killing_sleep(after: int):
    calls = {"n": 0}

    async def sleep(_s):
        calls["n"] += 1
        if calls["n"] >= after:
            raise Crash("killed during the retry wait")

    return sleep


def test_a_restart_during_a_429_retry_wait_submits_normally(chain, monkeypatch):
    from tests.providers.fakes import gemini_error

    chain.ctx.job_id = _job_row()
    _veo(chain, monkeypatch, [gemini_error(429, "quota", "RESOURCE_EXHAUSTED")], sleep=_killing_sleep(1))
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "pending"

    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    client = _veo(chain, monkeypatch, [_done_op()])
    res = build(ctx2, screenplay(video_scene()))
    assert len(client.models.calls) == 1, "submitted exactly once after the restart"
    assert ("video.ambiguous_submission", "lab") not in codes(res)
    assert not res.manifest.media["lab"].main_is_fallback
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "completed"


def test_a_kill_while_the_request_is_outstanding_stays_ambiguous(chain, monkeypatch):
    chain.ctx.job_id = _job_row()
    _veo(chain, monkeypatch, [Crash("killed while Veo was answering")])
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "submitting"
    ctx2 = _restart(chain.ctx, chain.ctx.job_id)
    client = _veo(chain, monkeypatch, [_done_op()])
    res = build(ctx2, screenplay(video_scene()))
    assert client.models.calls == [] and ("video.ambiguous_submission", "lab") in codes(res)


def test_an_unanswered_attempt_keeps_the_record_ambiguous_through_later_error_answers(chain, monkeypatch):
    from tests.providers.fakes import gemini_error

    chain.ctx.job_id = _job_row()
    _veo(chain, monkeypatch, [TimeoutError("no answer"), gemini_error(429, "quota", "RESOURCE_EXHAUSTED")],
         sleep=_killing_sleep(2))
    with pytest.raises(Crash):
        build(chain.ctx, screenplay(video_scene()))
    assert next(iter(_payload(chain.ctx.job_id)[PAYLOAD_KEY].values()))["status"] == "submitting"
