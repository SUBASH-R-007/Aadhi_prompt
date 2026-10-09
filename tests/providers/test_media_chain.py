"""Media provider plumbing: chains and status (factory), failure categories and cooldowns (health),
safe downloads, output validation, Veo checkpoints/resume, operation records, related settings."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from google.genai import types as gtypes
from PIL import Image

from aadhi.providers import factory
from aadhi.providers._common import error_for_status, fingerprint
from aadhi.providers._http import HttpStatusError, ResponseTooLarge, UnsafeRedirect, fetch_limited, host_allowed
from aadhi.providers.base import (
    ContentBlocked,
    InvalidMediaOutput,
    OperationLost,
    ProviderError,
    ProviderNotConfigured,
    ProviderUnavailable,
    RateLimited,
    Usage,
)
from aadhi.providers.health import ProviderHealth, classify_failure, cooldown_seconds, fallback_allowed
from aadhi.providers.image.pollinations import POLLINATIONS_BASE_URL, PollinationsImage
from aadhi.providers.image.verify import MIN_SIDE, inspect_generated, inspect_image_sync, looks_blank, media_warnings
from aadhi.providers.operations import PAYLOAD_KEY, OperationStore
from aadhi.providers.video.veo import VeoVideo

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import gemini_error, gemini_factory
from .media_fakes import flat_png, gradient_png

MP4 = b"\x00\x00\x00\x18ftypmp42fake"


# --- factory: chains and status ----------------------------------------------------------------------


def test_media_chain_and_named_providers(make_settings) -> None:
    s = make_settings(image_provider="pollinations", image_fallback_providers="gemini, pollinations,GEMINI",
                      video_provider="none", video_fallback_providers="fake")
    assert s.image_fallback_providers == ["gemini", "pollinations"]
    assert factory.media_chain("image", s) == ["pollinations", "gemini"]
    assert factory.media_chain("video", s) == []  # "none" switches the medium off, backups included
    assert factory.get_image(s).name == "pollinations" and factory.get_image(s, "fake").name == "fake"
    assert factory.get_video(s) is None and factory.get_video(s, "fake").name == "fake"
    assert factory.media_unavailable_reason("image", "gemini", s) == "no Gemini API key is configured"
    assert factory.media_unavailable_reason("image", "pollinations", s) is None
    assert factory.media_configured("image", s) and not factory.media_configured("video", s)
    with pytest.raises(ProviderNotConfigured):
        factory.get_image(s, "gemini")  # no key
    with pytest.raises(ValueError):
        make_settings(image_fallback_providers="veo")
    with pytest.raises(ValueError):
        make_settings(video_fallback_providers="pollinations")
    defaults = make_settings()
    assert defaults.image_fallback_providers == [] and defaults.video_fallback_providers == []
    assert factory.media_chain("image", defaults) == [defaults.image_provider]  # unchanged single provider


def test_adapters_declare_capabilities() -> None:
    from aadhi.providers.image.fake import FakeImage
    from aadhi.providers.image.gemini import GEMINI_ASPECTS, IMAGEN_ASPECTS, GeminiImage
    from aadhi.providers.video.fake import FakeVideo

    assert (PollinationsImage.paid, GeminiImage.paid, FakeImage.paid) == (False, True, False)
    assert (VeoVideo.paid, VeoVideo.resumable, FakeVideo.resumable) == (True, True, False)
    assert VeoVideo.aspects == frozenset({"16:9", "9:16"}) and "4:3" in PollinationsImage.aspects
    assert PollinationsImage.max_prompt_chars == 1500
    gem = GeminiImage.__new__(GeminiImage)
    gem.settings = SimpleNamespace(image_model="imagen-4.0-generate-001")
    assert gem.aspects == IMAGEN_ASPECTS
    gem.settings = SimpleNamespace(image_model="gemini-2.5-flash-image")
    assert gem.aspects == GEMINI_ASPECTS


def test_status_reports_configuration_only_and_never_secrets(make_settings, monkeypatch) -> None:
    from aadhi.providers.health import provider_health, reset_provider_health

    key = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKE12345"
    s = make_settings(image_provider="pollinations", image_fallback_providers="gemini", video_provider="veo",
                      gemini_api_key=key)
    built: list[str] = []
    monkeypatch.setattr(factory, "_cached", lambda *a, **k: built.append(a[1]))  # nothing may be constructed
    monkeypatch.setattr(httpx.AsyncClient, "__init__", lambda *a, **k: pytest.fail("no HTTP client"))
    reset_provider_health()
    try:
        provider_health().note_failure("pollinations", "server", ProviderUnavailable("402", status=402), s)
        provider_health().note_failure("veo", "user:3", RateLimited("429", status=429), s)  # a user's: not shown
        status = factory.media_provider_status(s)
    finally:
        reset_provider_health()
    assert built == []
    image = status["image"]
    assert image["preferred"] == "pollinations" and image["enabled"] and image["usable"]
    poll, gem = image["providers"]
    assert (poll["state"], poll["role"], poll["paid"]) == ("cooling", "preferred", False)
    assert "payment or account" in poll["reason"] and 0 < poll["cooldown_seconds_left"] <= 600
    assert (gem["state"], gem["role"], gem["paid"], gem["order"]) == ("available", "backup", True, 1)
    assert gem["model"] == s.image_model
    veo = status["video"]["providers"][0]
    assert veo["state"] == "available" and veo["paid"] and veo["model"] == s.veo_model
    assert key not in repr(status) and "AIza" not in repr(status)
    off = factory.media_provider_status(make_settings(video_provider="none"))
    assert off["video"] == {"enabled": False, "configured": "none", "preferred": None, "usable": False, "providers": []}
    nokey = factory.media_provider_status(make_settings(image_provider="gemini"))["image"]
    assert nokey["providers"][0]["state"] == "not_configured" and not nokey["usable"]


# --- failure categories and cooldowns --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "category"),
    [
        (ContentBlocked("x"), "blocked"),
        (ProviderNotConfigured("x"), "not_configured"),
        (ProviderUnavailable("x", status=402), "unavailable"),
        (ProviderError("x", status=403, provider="gemini"), "unavailable"),
        (RateLimited("x", status=429), "rate_limited"),
        (ProviderError("x", status=503), "transient"),
        (ProviderError("veo: video generation timed out after 420s"), "transient"),
        (InvalidMediaOutput("x"), "invalid_output"),
        (OperationLost("x", status=404), "lost"),
        (ProviderError("x", status=400), "failed"),
        (TimeoutError(), "transient"),
        (RuntimeError("adapter bug"), "failed"),
    ],
)
def test_failure_categories(exc: BaseException, category: str) -> None:
    assert classify_failure(exc) == category
    assert fallback_allowed(category) is (category != "blocked")


def test_error_for_status_maps_payment_and_account_refusals() -> None:
    assert error_for_status(402) is ProviderUnavailable and error_for_status(401) is ProviderUnavailable
    assert error_for_status(403) is ProviderUnavailable and error_for_status(429) is RateLimited
    assert error_for_status(400) is ProviderError and error_for_status(None) is ProviderError
    assert issubclass(ProviderUnavailable, ProviderError)  # personal-key detection by status still applies
    from aadhi.credentials import personal_key_rejection

    exc = ProviderUnavailable("gemini: refused (HTTP 403)", status=403, provider="gemini")
    assert personal_key_rejection(exc, {"gemini": "personal"}) is not None
    assert personal_key_rejection(exc, {"gemini": "server"}) is None


def test_cooldowns_reorder_but_never_drop(make_settings) -> None:
    s = make_settings(media_provider_cooldown_seconds=60, media_provider_auth_cooldown_seconds=600)
    now = {"t": 1000.0}
    health = ProviderHealth(clock=lambda: now["t"])
    names = ["pollinations", "gemini", "fake"]
    order = lambda scope: health.order(names, lambda n: (n, scope))  # noqa: E731
    assert health.note_failure("pollinations", "server", ProviderUnavailable("x", status=402), s) == 600
    assert health.note_failure("gemini", "server", ProviderError("x", status=400), s) == 0  # request-specific
    assert order("server") == ["gemini", "fake", "pollinations"]
    assert order("user:7") == names  # scopes are isolated
    assert health.note_failure("fake", "server", RateLimited("x", status=429), s) == 60
    assert order("server") == ["gemini", "pollinations", "fake"]  # every provider cooling: original order, none dropped
    now["t"] += 61
    assert health.cooling("fake", "server") is None and health.cooling("pollinations", "server").seconds_left == 539
    health.note_success("pollinations", "server")
    assert order("server") == names
    assert cooldown_seconds("blocked", s) == 0 and cooldown_seconds("invalid_output", s) == 60
    assert health.snapshot(names) == {}


# --- safe downloads -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_fetch_limited_redirects_and_caps() -> None:
    allowed = ("pollinations.ai",)
    respx.get("https://image.pollinations.ai/p/1").mock(return_value=httpx.Response(
        302, headers={"location": "https://cdn.pollinations.ai/img/1.png?sig=abc"}))
    respx.get("https://cdn.pollinations.ai/img/1.png").mock(return_value=httpx.Response(
        200, content=b"PNGDATA", headers={"content-type": "image/png"}))
    async with httpx.AsyncClient() as client:
        data, ctype = await fetch_limited(client, "https://image.pollinations.ai/p/1", params={"seed": 1},
                                          max_bytes=100, allowed_hosts=allowed, content_type_prefix="image/")
        assert (data, ctype) == (b"PNGDATA", "image/png")

        respx.get("https://image.pollinations.ai/p/2").mock(return_value=httpx.Response(
            302, headers={"location": "https://evil.example/x.png"}))
        with pytest.raises(UnsafeRedirect):
            await fetch_limited(client, "https://image.pollinations.ai/p/2", max_bytes=100, allowed_hosts=allowed)
        respx.get("https://image.pollinations.ai/p/3").mock(return_value=httpx.Response(
            301, headers={"location": "http://image.pollinations.ai/p/3"}))  # https -> http downgrade
        with pytest.raises(UnsafeRedirect):
            await fetch_limited(client, "https://image.pollinations.ai/p/3", max_bytes=100, allowed_hosts=allowed)
        respx.get("https://image.pollinations.ai/loop").mock(return_value=httpx.Response(
            307, headers={"location": "/loop"}))
        with pytest.raises(UnsafeRedirect, match="more than 3"):
            await fetch_limited(client, "https://image.pollinations.ai/loop", max_bytes=100, allowed_hosts=allowed)

        respx.get("https://image.pollinations.ai/big").mock(return_value=httpx.Response(
            200, content=b"x" * 500, headers={"content-type": "image/png"}))
        with pytest.raises(ResponseTooLarge):
            await fetch_limited(client, "https://image.pollinations.ai/big", max_bytes=100, allowed_hosts=allowed)

        async def stream():
            for _ in range(10):
                yield b"y" * 50

        respx.get("https://image.pollinations.ai/stream").mock(return_value=httpx.Response(
            200, content=stream(), headers={"content-type": "image/png"}))  # no Content-Length
        with pytest.raises(ResponseTooLarge):
            await fetch_limited(client, "https://image.pollinations.ai/stream", max_bytes=100, allowed_hosts=allowed)

        respx.get("https://image.pollinations.ai/html").mock(return_value=httpx.Response(
            200, text="<html>", headers={"content-type": "text/html"}))
        with pytest.raises(HttpStatusError) as info:
            await fetch_limited(client, "https://image.pollinations.ai/html", max_bytes=100, allowed_hosts=allowed,
                                content_type_prefix="image/")
        assert info.value.status == 502
        respx.get("https://image.pollinations.ai/limited").mock(return_value=httpx.Response(
            429, text="z" * 5000, headers={"retry-after": "7"}))
        with pytest.raises(HttpStatusError) as info:
            await fetch_limited(client, "https://image.pollinations.ai/limited", max_bytes=100, allowed_hosts=allowed)
        assert info.value.status == 429 and info.value.retry_after == 7.0 and len(info.value.body) == 2000
    assert host_allowed("a.b.pollinations.ai", allowed) and not host_allowed("pollinations.ai.evil.net", allowed)
    assert not host_allowed("", allowed) and host_allowed("POLLINATIONS.AI.", allowed)


@pytest.mark.asyncio
@respx.mock
async def test_pollinations_402_is_not_retried_and_unsafe_redirects_fail(app_env) -> None:
    route = respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(
        402, text='{"error":"payment required"}'))
    with pytest.raises(ProviderUnavailable) as info:
        await PollinationsImage(app_env, sleep=no_sleep, max_attempts=3).generate("x")
    assert route.call_count == 1 and info.value.status == 402 and "payment" in str(info.value)
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(
        302, headers={"location": "https://tracker.example/pixel.png"}))
    before = route.call_count
    with pytest.raises(ProviderError, match="UnsafeRedirect"):
        await PollinationsImage(app_env, sleep=no_sleep, max_attempts=3).generate("x")
    assert route.call_count - before == 1  # never retried


@pytest.mark.asyncio
@respx.mock
async def test_pollinations_flags_a_blank_picture(app_env) -> None:
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(
        200, content=flat_png(1280, 720), headers={"content-type": "image/png"}))
    res = await PollinationsImage(app_env, sleep=no_sleep).generate("x")
    assert res.width == 1280 and len(res.warnings) == 1 and "flat colour" in res.warnings[0]
    respx.get(url__startswith=POLLINATIONS_BASE_URL).mock(return_value=httpx.Response(
        200, content=flat_png(16, 16), headers={"content-type": "image/png"}))
    with pytest.raises(InvalidMediaOutput, match="too small"):
        await PollinationsImage(app_env, sleep=no_sleep).generate("x")


# --- output validation --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generated_image_validation() -> None:
    info, warnings = await inspect_generated(gradient_png(160, 90), provider="p", aspect="16:9", max_bytes=10**6)
    assert (info.width, info.height, info.blank, warnings) == (160, 90, False, [])
    info, warnings = await inspect_generated(flat_png(120, 120), provider="p", aspect="16:9", max_bytes=10**6)
    assert info.blank and len(warnings) == 2 and "120x120" in warnings[1]
    with pytest.raises(InvalidMediaOutput, match="too small"):
        await inspect_generated(gradient_png(MIN_SIDE - 1, 90), provider="p", aspect="16:9", max_bytes=10**6)
    with pytest.raises(InvalidMediaOutput, match="exceeds"):
        await inspect_generated(gradient_png(160, 90), provider="p", aspect="16:9", max_bytes=100)
    small = io.BytesIO()
    Image.new("RGB", (8, 8)).save(small, format="JPEG")
    assert inspect_image_sync(small.getvalue()).width == 8  # the generic check keeps accepting small images
    assert looks_blank(b"garbage") is None and looks_blank(flat_png()) is True and looks_blank(gradient_png()) is False
    assert media_warnings(1280, 720, "16:9") == [] and media_warnings(1024, 768, "16:9")
    assert media_warnings(None, None, "16:9") == [] and media_warnings(100, 100, "") == []


# --- Veo: duration, checkpoints, resume ---------------------------------------------------------------------


def _op(done: bool, video: Any = None, name: str = "operations/abc") -> SimpleNamespace:
    response = SimpleNamespace(generated_videos=[SimpleNamespace(video=video)]) if video is not None else None
    return SimpleNamespace(name=name, done=done, error=None, response=response, result=None)


@pytest.fixture()
def fake_probe(monkeypatch):
    from aadhi.providers.media import MediaInfo
    from aadhi.providers.video import veo as veo_mod

    state = {"duration": 8.0}

    async def probe(data, settings=None):
        return MediaInfo(duration=state["duration"], width=1280, height=720, has_video=True)

    monkeypatch.setattr(veo_mod, "probe_media", probe)
    return state


class Checkpoint:
    def __init__(self, clients: dict[str, Any] | None = None, fail: BaseException | None = None) -> None:
        self.events: list[tuple[str, Any]] = []
        self.clients = clients
        self.fail = fail

    async def submitted(self, operation: str, key_fingerprint: str) -> None:
        polls = sum(c.aio.operations.calls for c in (self.clients or {}).values())
        self.events.append(("submitted", (operation, key_fingerprint, polls)))
        if self.fail is not None:
            raise self.fail

    async def usage_recorded(self) -> None:
        self.events.append(("usage_recorded", None))


@pytest.mark.asyncio
async def test_veo_duration_and_checkpoint_before_polling(make_settings, fake_probe) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], veo_duration_seconds=5)
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory_, clients = gemini_factory({GEMINI_KEYS[0]: [_op(False)]}, ops={GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4
    cp = Checkpoint(clients)
    usages: list[Usage] = []
    res = await VeoVideo(s, client_factory=factory_, sleep=no_sleep, poll_interval_s=0).generate(
        "waves", on_usage=usages.append, checkpoint=cp)
    assert cp.events[0] == ("submitted", ("operations/abc", fingerprint(GEMINI_KEYS[0]), 0))  # before any poll
    # the charge is noted as soon as it is reported (before the download): a restart never reports it again
    assert [e[0] for e in cp.events] == ["submitted", "usage_recorded"]
    assert clients[GEMINI_KEYS[0]].models.calls[0]["config"].duration_seconds == 5
    assert [(u.seconds, u.meta["status"]) for u in usages] == [(5.0, "generated")]
    assert res.warnings == [] and res.duration == 8.0


@pytest.mark.asyncio
async def test_veo_resume_polls_the_same_operation_without_submitting(gemini_settings, fake_probe) -> None:
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory_, clients = gemini_factory({}, ops={GEMINI_KEYS[1]: [_op(False), _op(True, video)]})
    factory_(GEMINI_KEYS[1]).aio.files.download_bytes = MP4
    provider = VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep, poll_interval_s=0)
    usages: list[Usage] = []
    res = await provider.resume("operations/abc", fingerprint(GEMINI_KEYS[1]), on_usage=usages.append)
    assert res.data == MP4 and clients[GEMINI_KEYS[1]].aio.operations.calls == 2
    assert all(c.models.calls == [] for c in clients.values())  # never submits
    assert [u.meta["status"] for u in usages] == ["generated"]
    # the estimate was already reported before the restart: not again
    factory_, _ = gemini_factory({}, ops={GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4
    usages.clear()
    await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep).resume(
        "operations/abc", fingerprint(GEMINI_KEYS[0]), on_usage=usages.append, usage_recorded=True)
    assert usages == []


@pytest.mark.asyncio
async def test_veo_resume_lost_operations(gemini_settings) -> None:
    provider = VeoVideo(gemini_settings, client_factory=gemini_factory({})[0], sleep=no_sleep)
    with pytest.raises(OperationLost, match="no longer configured"):  # the key that started it is gone
        await provider.resume("operations/abc", fingerprint("AIzaSomeOtherKeyThatWasRemoved123"))
    factory_, _ = gemini_factory({}, ops={GEMINI_KEYS[0]: [gemini_error(404, "not found", "NOT_FOUND")]})
    with pytest.raises(OperationLost) as info:
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep).resume(
            "operations/abc", fingerprint(GEMINI_KEYS[0]))
    assert info.value.status == 404


@pytest.mark.asyncio
async def test_veo_cancellation_after_the_checkpoint_notes_the_reported_usage(gemini_settings) -> None:
    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]}, ops={GEMINI_KEYS[0]: [asyncio.CancelledError()]})
    cp = Checkpoint()
    usages: list[Usage] = []
    with pytest.raises(asyncio.CancelledError):
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep, poll_interval_s=0).generate(
            "x", on_usage=usages.append, checkpoint=cp)
    assert [u.meta["status"] for u in usages] == ["abandoned"]
    assert [e[0] for e in cp.events] == ["submitted", "usage_recorded"]

    # a lost lease while saving the checkpoint: the accepted job is reported, the error propagates
    class LeaseLost(Exception):
        pass

    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [_op(False)]})
    usages.clear()
    with pytest.raises(LeaseLost):
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep).generate(
            "x", on_usage=usages.append, checkpoint=Checkpoint(fail=LeaseLost()))
    assert [u.meta["status"] for u in usages] == ["abandoned"]


@pytest.mark.asyncio
async def test_veo_rejects_unusable_video(make_settings, fake_probe) -> None:
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], ai_max_video_bytes=1_000_000)
    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = b"\x00" * 1_000_001
    with pytest.raises(InvalidMediaOutput, match="exceeds"):
        await VeoVideo(s, client_factory=factory_, sleep=no_sleep).generate("x")
    fake_probe["duration"] = 0.2
    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4
    with pytest.raises(InvalidMediaOutput, match="too short"):
        await VeoVideo(s, client_factory=factory_, sleep=no_sleep).generate("x")


# --- operation records -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_operation_store_persists_in_the_job_row(app_env, job_ctx, monkeypatch) -> None:
    from aadhi.db import session_scope
    from aadhi.jobs.queue import enqueue
    from aadhi.models import Job

    store = OperationStore(job_ctx)  # no job row: memory only
    rec = store.new("video-abc", "", "veo", "veo-x")
    await store.save(rec)
    assert store.get("video-abc", "").status == "submitting" and job_ctx.payload[PAYLOAD_KEY]["video-abc"]

    with session_scope() as db:
        job_id = enqueue(db, "build_assets", payload={"scene_ids": ["s1"]}).id
    job_ctx.job_id, job_ctx.attempt, job_ctx.payload = job_id, 2, {"scene_ids": ["s1"]}
    store = OperationStore(job_ctx)
    rec = store.new("video-abc", "user:4", "veo", "veo-x")
    cp = store.checkpoint(rec)
    await cp.submitted("operations/1", "fp")
    await cp.usage_recorded()
    with session_scope() as db:
        payload = db.get(Job, job_id).payload
    assert payload["scene_ids"] == ["s1"]  # the job's own payload is kept
    saved = payload[PAYLOAD_KEY]["video-abc#user:4"]
    assert (saved["status"], saved["operation"], saved["attempt"], saved["usage_recorded"]) == (
        "submitted", "operations/1", 2, True)
    assert store.get("video-abc", "") is None and store.get("video-abc", "user:4").operation == "operations/1"

    class LeaseLost(Exception):
        pass

    def lost(db: Any) -> None:
        raise LeaseLost()

    monkeypatch.setattr(job_ctx, "assert_lease", lost, raising=False)
    with pytest.raises(LeaseLost):  # a worker that lost the job never writes over the new owner's state
        await store.save(rec)

    from sqlalchemy.exc import OperationalError

    def broken_session():
        raise OperationalError("UPDATE jobs", {}, Exception("database is locked"))

    monkeypatch.setattr(job_ctx, "session", broken_session)
    await store.save(rec)  # logged, the job goes on (it already paid)


# --- settings: production guard and redaction ------------------------------------------------------------------


def test_production_refuses_fake_providers(make_settings, tmp_path) -> None:
    base = {"app_env": "production", "jwt_secret": "x" * 40, "base_url": "https://aadhi.example.com",
            "database_url": "postgresql://u:p@db/aadhi", "manim_sandbox": "docker", "worker_mode": "external",
            "storage_local_dir": tmp_path / "storage", "llm_provider": "gemini", "tts_provider": "edge",
            "image_provider": "pollinations", "video_provider": "none"}
    make_settings(**base).validate_for_runtime()
    with pytest.raises(RuntimeError, match="IMAGE_PROVIDER.*fake"):
        make_settings(**{**base, "image_provider": "fake"}).validate_for_runtime()
    with pytest.raises(RuntimeError, match="VIDEO_FALLBACK_PROVIDERS"):
        make_settings(**{**base, "video_fallback_providers": "fake"}).validate_for_runtime()
    make_settings(**{**base, "tts_provider": "fake", "allow_fake_providers": True}).validate_for_runtime()
    make_settings(llm_provider="fake").validate_for_runtime()  # tests / development are unaffected
    assert make_settings(**{**base, "llm_provider": "fake"}).fake_providers() == ["LLM_PROVIDER"]


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("GET https://x.example/v.mp4?X-Goog-Signature=abcdef123456&X-Goog-Credential=svc%40p", "abcdef123456"),
        ("download failed: https://cdn.example/a?sig=Zz9yY8xX7&e=1", "Zz9yY8xX7"),
        ("echo: AIzaSyD-unknown-google-key-0123456789", "AIzaSyD-unknown"),
        ("bad key sk-proj-0123456789abcdefABCDEF", "0123456789abcdef"),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOi"),
        ("headers {'authorization': 'Basic dXNlcjpwYXNzd29yZA=='}", "dXNlcjpwYXNz"),
    ],
)
def test_unknown_secrets_are_masked(make_settings, text: str, leaked: str) -> None:
    out = make_settings().redact(text)
    assert leaked not in out and "[REDACTED]" in out


def test_ordinary_text_is_not_over_redacted(make_settings) -> None:
    s = make_settings()
    for text in ("risk-assessment-for-the-lab-session", "task-management-and-scheduling", "see the authorization page",
                 "sk-short", "AIza is a prefix", "Bearer of good news"):
        assert s.redact(text) == text


@pytest.mark.asyncio
async def test_veo_interrupted_download_never_reports_the_clip_twice(gemini_settings, monkeypatch) -> None:
    from aadhi.providers.video import veo as veo_mod

    async def released(data, settings=None):
        raise asyncio.CancelledError()  # a graceful release (or a kill) during the probe / download

    monkeypatch.setattr(veo_mod, "probe_media", released)
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4
    cp = Checkpoint()
    usages: list[Usage] = []
    with pytest.raises(asyncio.CancelledError):
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep).generate(
            "x", on_usage=usages.append, checkpoint=cp)
    assert [e[0] for e in cp.events] == ["submitted", "usage_recorded"]
    assert [u.meta["status"] for u in usages] == ["generated"]


@pytest.mark.asyncio
async def test_veo_resume_notes_every_charge_on_the_checkpoint(gemini_settings, monkeypatch) -> None:
    from aadhi.providers.video import veo as veo_mod

    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    factory_, _ = gemini_factory({}, ops={GEMINI_KEYS[0]: [_op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4

    async def released(data, settings=None):
        raise asyncio.CancelledError()

    monkeypatch.setattr(veo_mod, "probe_media", released)
    cp = Checkpoint()
    usages: list[Usage] = []
    with pytest.raises(asyncio.CancelledError):  # released again, now during the resumed download
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep).resume(
            "operations/abc", fingerprint(GEMINI_KEYS[0]), on_usage=usages.append, checkpoint=cp)
    assert [u.meta["status"] for u in usages] == ["generated"] and [e[0] for e in cp.events] == ["usage_recorded"]

    # cancelled while polling (no charge reported yet): the abandoned estimate is reported and noted
    factory_, _ = gemini_factory({}, ops={GEMINI_KEYS[0]: [_op(False), asyncio.CancelledError()]})
    cp, usages = Checkpoint(), []
    with pytest.raises(asyncio.CancelledError):
        await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep, poll_interval_s=0).resume(
            "operations/abc", fingerprint(GEMINI_KEYS[0]), on_usage=usages.append, checkpoint=cp)
    assert [u.meta["status"] for u in usages] == ["abandoned"] and [e[0] for e in cp.events] == ["usage_recorded"]


class SendingCheckpoint(Checkpoint):
    async def sending(self) -> None:
        self.events.append(("sending", None))

    async def not_sent(self) -> None:
        self.events.append(("not_sent", None))


@pytest.mark.asyncio
async def test_veo_marks_only_unanswered_requests_as_outstanding(gemini_settings, fake_probe) -> None:
    video = gtypes.Video(uri="https://files.example/v.mp4", mime_type="video/mp4")
    quota = gemini_error(429, "quota", "RESOURCE_EXHAUSTED")
    factory_, _ = gemini_factory({k: [quota] for k in GEMINI_KEYS} | {GEMINI_KEYS[0]: [quota, _op(True, video)]})
    for k in GEMINI_KEYS:
        factory_(k).aio.files.download_bytes = MP4
    cp = SendingCheckpoint()
    await VeoVideo(gemini_settings, client_factory=factory_, sleep=no_sleep, max_attempts=6).generate(
        "x", checkpoint=cp)
    names = [e[0] for e in cp.events]
    assert names[-3:] == ["sending", "submitted", "usage_recorded"]
    assert names[:-3] == ["sending", "not_sent"] * (len(names[:-3]) // 2) and names[:-3]  # each 429 was answered

    # a request without an answer (a timeout) leaves the record outstanding, also through later error answers
    factory_, _ = gemini_factory({GEMINI_KEYS[0]: [TimeoutError("no answer"), quota, _op(True, video)]})
    factory_(GEMINI_KEYS[0]).aio.files.download_bytes = MP4
    cp = SendingCheckpoint()
    single = gemini_settings.model_copy(update={"gemini_api_keys": []})
    await VeoVideo(single, client_factory=factory_, sleep=no_sleep, max_attempts=4).generate("x", checkpoint=cp)
    assert "not_sent" not in [e[0] for e in cp.events]


@pytest.mark.asyncio
async def test_job_checkpoint_pending_and_sending(app_env, job_ctx) -> None:
    from aadhi.providers.operations import PENDING, SUBMITTING

    store = OperationStore(job_ctx)
    rec = store.new("video-abc", "", "veo", "veo-x")
    rec.status = PENDING
    cp = store.checkpoint(rec)
    await cp.sending()
    assert store.get("video-abc", "").status == SUBMITTING
    await cp.not_sent()
    assert store.get("video-abc", "").status == PENDING
    await cp.submitted("operations/x", "fp")
    await cp.not_sent()  # only an outstanding request goes back to pending
    assert store.get("video-abc", "").status == "submitted"
