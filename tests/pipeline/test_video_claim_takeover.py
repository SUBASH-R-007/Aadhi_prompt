"""A paid AI video (Veo) started by a job that then died is collected by the *next job* that needs the same
clip: the operation is recorded on the asset's generation claim, and the job that takes the claim over polls
it instead of paying again. A submission whose answer was never saved still needs a person. Offline only."""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

import pytest
from sqlalchemy import select

from aadhi.db import session_scope
from aadhi.models import AssetClaim
from aadhi.pipeline import integrations
from aadhi.pipeline.assets import build_assets_detailed
from aadhi.pipeline.base import GenerationOptions
from aadhi.providers.health import reset_provider_health
from aadhi.providers.operations import PAYLOAD_KEY
from aadhi.schemas.screenplay import Screenplay
from aadhi.storage.assets import AssetStore, ClaimOwner, claim_owner
from tests.helpers import FakeJobContext
from tests.providers.media_fakes import Crash, OperationService, ResumableVideo

SCENE = {"id": "lab", "type": "ai_video", "title": "lab", "video_prompt": "a lathe cutting metal", "rationale": "real",
         "fallback_image_prompt": "a lathe", "beats": [{"id": "lab-b1", "narration": "See the lathe."}]}


def screenplay() -> Screenplay:
    return Screenplay.model_validate({"language": "en-IN", "scenes": [SCENE]})


def job_row() -> int:
    from aadhi.jobs.queue import enqueue

    with session_scope() as db:
        return enqueue(db, "build_assets", payload={}).id


def payload(job_id: int) -> dict[str, Any]:
    from aadhi.models import Job

    with session_scope() as db:
        return dict(db.get(Job, job_id).payload or {})


def claims() -> list[AssetClaim]:
    with session_scope() as db:
        rows = list(db.execute(select(AssetClaim)).scalars())
        for r in rows:
            db.expunge(r)
        return rows


def build(ctx: Any):
    return asyncio.run(build_assets_detailed(ctx, screenplay(), GenerationOptions(allow_ai_video=True)))


@pytest.fixture()
def veo(job_ctx, providers, fast_audio, monkeypatch):
    reset_provider_health()
    service = OperationService()
    video = ResumableVideo(service)
    monkeypatch.setattr(integrations, "get_video", lambda settings: video)
    job_ctx.settings = job_ctx.settings.model_copy(update={"video_provider": "veo"})
    yield service, video
    reset_provider_health()


def die_holding_the_claim(ctx: FakeJobContext, video: ResumableVideo, monkeypatch, **crash: bool) -> None:
    """Job A is killed mid-generation: no ``finally`` runs, so its claim stays behind (and its job lease is gone)."""

    async def killed(self, claim) -> None:
        return None

    with monkeypatch.context() as m:
        m.setattr(AssetStore, "_release_claim_quietly", killed)
        for name, value in crash.items():
            setattr(video, name, value)
        with claim_owner(ClaimOwner(worker_id="host:9:boot:dead", job_id=ctx.job_id, attempt=1)), \
                pytest.raises(Crash):
            build(ctx)
        for name in crash:
            setattr(video, name, False)


def another_job(ctx: FakeJobContext, **kw: Any) -> FakeJobContext:
    return FakeJobContext(settings=ctx.settings, assets=ctx.assets, job_id=job_row(), **kw)


def test_the_next_job_collects_a_dead_jobs_paid_clip_instead_of_paying_again(job_ctx, veo, monkeypatch):
    service, video = veo
    job_ctx.job_id = job_row()
    die_holding_the_claim(job_ctx, video, monkeypatch, crash_after_checkpoint=True)
    (claim,) = claims()
    assert claim.operation["status"] == "submitted" and claim.operation["operation"] == "operations/op-1"
    assert "AIza" not in str(claim.operation) and claim.job_id == job_ctx.job_id

    other = another_job(job_ctx)  # e.g. another lecture's build asking for the same clip
    res = build(other)
    media = res.manifest.media["lab"]
    assert media.main is not None and media.main.source == "veo" and not media.main_is_fallback
    assert service.submits == 1 and video.resumes == ["operations/op-1"]  # polled, not paid again
    assert [u.meta["status"] for u in video.usage] == ["generated"]  # billed once
    assert other.assets.get(media.main.asset_key).meta["resumed"] is True
    assert next(iter(payload(other.job_id)[PAYLOAD_KEY].values()))["status"] == "completed"
    assert claims() == []


def test_a_job_cancelled_while_polling_leaves_its_paid_clip_for_the_next_job(job_ctx, veo):
    """A graceful stop (worker shutdown or deploy, lost lease, user cancel) runs the producer's ``finally``: the
    claim row and its submitted operation must survive it, or the next job pays for the same clip again."""
    service, video = veo
    job_ctx.job_id = job_row()
    polling = asyncio.Event()
    original = video.generate

    async def submit_then_poll(prompt: str, **kw: Any):
        name = video.service.submit(prompt)
        await kw["checkpoint"].submitted(name, video.fingerprint)
        polling.set()
        await asyncio.sleep(3600)  # polling Veo until the worker stops this task
        raise AssertionError("not reached")

    video.generate = submit_then_poll

    async def run_and_cancel() -> None:
        task = asyncio.ensure_future(build_assets_detailed(job_ctx, screenplay(), GenerationOptions(allow_ai_video=True)))
        await asyncio.wait_for(polling.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with claim_owner(ClaimOwner(worker_id="host:9:boot:stopping", job_id=job_ctx.job_id, attempt=1)):
        asyncio.run(run_and_cancel())
    video.generate = original
    (claim,) = claims()
    assert claim.operation["status"] == "submitted" and claim.operation["operation"] == "operations/op-1"
    expires = claim.expires_at if claim.expires_at.tzinfo else claim.expires_at.replace(tzinfo=dt.timezone.utc)
    assert expires <= dt.datetime.now(dt.timezone.utc), "left expired for the next job"

    other = another_job(job_ctx)
    res = build(other)
    media = res.manifest.media["lab"]
    assert service.submits == 1 and video.resumes == ["operations/op-1"]  # collected, not paid again
    assert media.main is not None and not media.main_is_fallback
    assert claims() == []


def test_a_finished_record_of_the_takers_own_job_does_not_hide_the_inherited_operation(job_ctx, veo, monkeypatch):
    """Job B's first attempt hit a 429 (its own record: failed); meanwhile job A submitted the clip and was
    killed. B's retry takes A's claim over: it resumes A's billed operation instead of paying again."""
    from aadhi.providers.base import RateLimited

    service, video = veo
    b = another_job(job_ctx)
    original = video.generate

    async def rate_limited(prompt: str, **kw: Any):
        raise RateLimited("veo: rate limited", status=429, provider="veo")

    video.generate = rate_limited
    with pytest.raises(RateLimited):
        build(b)
    video.generate = original
    (record,) = payload(b.job_id)[PAYLOAD_KEY].values()
    assert record["status"] == "failed" and claims() == []

    job_ctx.job_id = job_row()  # job A
    die_holding_the_claim(job_ctx, video, monkeypatch, crash_after_checkpoint=True)
    assert claims()[0].operation["operation"] == "operations/op-1"

    retry = FakeJobContext(settings=b.settings, assets=b.assets, job_id=b.job_id, attempt=2, payload=payload(b.job_id))
    res = build(retry)
    assert service.submits == 1 and video.resumes == ["operations/op-1"]
    assert [u.meta["status"] for u in video.usage] == ["generated"]  # billed once
    assert not res.manifest.media["lab"].main_is_fallback
    assert claims() == []


def test_an_unconfirmed_submission_of_a_dead_job_is_not_paid_again_by_the_next_job(job_ctx, veo, monkeypatch):
    service, video = veo
    job_ctx.job_id = job_row()
    die_holding_the_claim(job_ctx, video, monkeypatch, crash_before_answer=True)
    assert claims()[0].operation["status"] == "submitting"

    res = build(another_job(job_ctx))
    media = res.manifest.media["lab"]
    assert service.submits == 1, "a submission that may already be billed is never repeated automatically"
    assert media.main_is_fallback and ("video.ambiguous_submission", "lab") in {(i.code, i.scene_id) for i in res.issues}
    assert claims() == []

    # an explicit rebuild of the scene (a new job, nothing left on the claim) generates it once
    rebuild = another_job(job_ctx)
    assert not build(rebuild).manifest.media["lab"].main_is_fallback and service.submits == 2


def test_a_record_paid_with_someone_elses_key_is_not_inherited(job_ctx, veo, monkeypatch):
    service, video = veo
    job_ctx.job_id, job_ctx.user_id = job_row(), 5
    job_ctx.key_sources = {"gemini": "personal"}  # user 5 paid with their own key
    die_holding_the_claim(job_ctx, video, monkeypatch, crash_after_checkpoint=True)
    assert claims()[0].operation["scope"] == "user:5"

    res = build(another_job(job_ctx, user_id=7))  # paid with the server key: cannot follow user 5's job
    assert video.resumes == [] and service.submits == 2
    assert not res.manifest.media["lab"].main_is_fallback
