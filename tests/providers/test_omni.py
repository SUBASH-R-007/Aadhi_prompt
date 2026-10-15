"""Gemini Omni video adapter (``aadhi.providers.video.omni``): Interactions API request, polling, billing,
paid-job checkpoints and resume, error translation. Offline: an in-memory Interactions client, the SDK's own
response model and error classes, and (once) the real SDK over a mock HTTP transport."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import interactions as gi

from aadhi.providers._common import fingerprint
from aadhi.providers.base import (
    ContentBlocked,
    ImageInput,
    InvalidMediaOutput,
    OperationLost,
    PaidSubmissionUncertain,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    Usage,
)
from aadhi.providers.gemini_common import classify_gemini_error
from aadhi.providers.health import classify_failure
from aadhi.providers.video import omni as omni_mod
from aadhi.providers.video.omni import (
    ESTIMATED_SECONDS,
    EXCLUSIONS,
    VIDEO_TOKENS_PER_SECOND,
    OmniVideo,
    interactions_of,
    translate_error,
)

from .conftest import GEMINI_KEYS, no_sleep

MP4 = b"\x00\x00\x00\x18ftypmp42omni-clip"
URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


# --- offline stand-ins ----------------------------------------------------------------------------------


def interaction(status: str = "completed", *, ident: str = "int-1", video: bytes | None = MP4, tokens: int = 0,
                input_tokens: int = 25, errors: list[str] | None = None, text: str = "", uri: str = "",
                model: str = "gemini-omni-1.1-flash") -> Any:
    """An ``Interaction`` as the SDK parses it (``output_video`` comes from the model-output step)."""
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    if video is not None or uri:
        block: dict[str, Any] = {"type": "video", "mime_type": "video/mp4"}
        if video is not None:
            block["data"] = base64.b64encode(video).decode("ascii")
        if uri:
            block["uri"] = uri
        content.append(block)
    data: dict[str, Any] = {"id": ident, "status": status, "model": model}
    if (content and status == "completed") or tokens:
        data["steps"] = [{"type": "model_output", "content": content}] if content else []
    if tokens or input_tokens:
        data["usage"] = {"total_input_tokens": input_tokens, "total_output_tokens": tokens,
                         "output_tokens_by_modality": [{"modality": "video", "tokens": tokens}] if tokens else []}
    if errors:
        data["errors"] = [{"code": "error", "message": m} for m in errors]
    return gi.Interaction.model_validate(data)


def running(status: str = "in_progress", ident: str = "int-1") -> Any:
    return gi.Interaction.model_validate({"id": ident, "status": status, "model": "gemini-omni-1.1-flash"})


def sdk_error(status: int, message: str = "error", *, retry_delay: str | None = None) -> Exception:
    """An Interactions SDK HTTP error (the SDK's own class for ``status``)."""
    from google.genai._gaos.lib import compat_errors as ce

    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay}] if retry_delay else []
    body = {"error": {"code": status, "message": message, "status": "X", "details": details}}
    response = httpx.Response(status, request=httpx.Request("POST", URL))
    return ce.APIError.generate(status, body, f"Error code: {status} - {body}", response)


def sdk_timeout() -> Exception:
    from google.genai._gaos.lib import compat_errors as ce

    return ce.APITimeoutError(request=httpx.Request("POST", URL))


class FakeInteractions:
    """``client.aio.interactions``: ``create`` / ``get`` answer from scripts (exceptions are raised)."""

    def __init__(self, creates: list[Any], gets: list[Any]) -> None:
        self.creates, self.gets = creates, gets
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.cancels: list[str] = []
        self.sdk_configuration = SimpleNamespace(retry_config="sdk default")

    @staticmethod
    def _next(script: list[Any]) -> Any:
        item = script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def create(self, **body: Any) -> Any:
        self.create_calls.append(body)
        return self._next(self.creates)

    async def get(self, id: str, **kw: Any) -> Any:  # noqa: A002 - the SDK's parameter name
        self.get_calls.append(id)
        return self._next(self.gets)

    async def cancel(self, id: str, **kw: Any) -> Any:  # noqa: A002
        self.cancels.append(id)
        return running("cancelled", id)


class FakeFiles:
    def __init__(self) -> None:
        self.downloads: list[str] = []
        self.data = MP4

    async def download(self, *, file: Any, config: Any = None) -> bytes:
        self.downloads.append(file)
        return self.data


class FakeClient:
    def __init__(self, creates: list[Any], gets: list[Any]) -> None:
        self.aio = SimpleNamespace(interactions=FakeInteractions(creates, gets), files=FakeFiles())

    @property
    def interactions(self) -> FakeInteractions:
        return self.aio.interactions


def omni_factory(creates: dict[str, list[Any]] | None = None, gets: dict[str, list[Any]] | None = None):
    clients: dict[str, FakeClient] = {}

    def factory(key: str) -> FakeClient:
        if key not in clients:
            clients[key] = FakeClient((creates or {}).setdefault(key, []), (gets or {}).setdefault(key, []))
        return clients[key]

    return factory, clients


class Checkpoint:
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    async def submitted(self, operation: str, key_fingerprint: str) -> None:
        self.events.append(("submitted", (operation, key_fingerprint)))

    async def usage_recorded(self) -> None:
        self.events.append(("usage_recorded", None))

    async def sending(self) -> None:
        self.events.append(("sending", None))

    async def not_sent(self) -> None:
        self.events.append(("not_sent", None))


@pytest.fixture()
def fake_probe(monkeypatch):
    from aadhi.providers.media import MediaInfo

    state = {"duration": 8.0, "width": 1280, "height": 720}

    async def probe(data, settings=None):
        return MediaInfo(duration=state["duration"], width=state["width"], height=state["height"], has_video=True)

    monkeypatch.setattr(omni_mod, "probe_media", probe)
    return state


def provider(settings, factory, **kw: Any) -> OmniVideo:
    return OmniVideo(settings, client_factory=factory, sleep=no_sleep, poll_interval_s=0, **kw)


# --- request, polling, usage ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_polls_with_the_creating_key_and_bills_video_tokens(gemini_settings, fake_probe) -> None:
    eight_seconds = 8 * VIDEO_TOKENS_PER_SECOND
    factory, clients = omni_factory(
        creates={GEMINI_KEYS[0]: [sdk_error(429, "quota", retry_delay="3s")], GEMINI_KEYS[1]: [running("queued")]},
        gets={GEMINI_KEYS[1]: [sdk_error(503, "flaky"), running(), interaction(tokens=eight_seconds)]},
    )
    usages: list[Usage] = []
    res = await provider(gemini_settings, factory).generate("ocean waves at dawn", on_usage=usages.append)
    assert res.data == MP4 and res.mime == "video/mp4" and (res.duration, res.width) == (8.0, 1280) and not res.warnings
    second = clients[GEMINI_KEYS[1]].interactions
    assert second.create_calls == [{
        "model": "gemini-omni-1.1-flash",
        "input": f"ocean waves at dawn\n\n{EXCLUSIONS}",  # no negative prompt on Omni: the exclusions go in the prompt
        "background": True,
        "response_format": {"type": "video", "aspect_ratio": "16:9", "resolution": "720p"},
    }]
    assert second.get_calls == ["int-1"] * 3  # the flaky poll was retried with the key that created the job
    assert clients[GEMINI_KEYS[0]].interactions.get_calls == []
    assert second.sdk_configuration.retry_config.strategy == "none"  # the SDK never re-sends a paid create
    assert usages == [Usage(provider="omni", model="gemini-omni-1.1-flash", operation="video", input_tokens=25,
                            output_tokens=eight_seconds, seconds=8.0, units=1,
                            meta={"status": "generated", "estimated": False, "video_tokens": eight_seconds})]


@pytest.mark.asyncio
async def test_portrait_and_reference_image(make_settings, fake_probe) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], omni_model="gemini-omni-flash-preview")
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [interaction(model="gemini-omni-flash-preview")]})
    fake_probe.update(width=720, height=1280)
    ref = ImageInput(data=b"\x89PNG-reference", mime="image/png")
    usages: list[Usage] = []
    res = await provider(s, factory).generate("a lathe", aspect="9:16", reference_image=ref, on_usage=usages.append)
    body = clients[GEMINI_KEYS[0]].interactions.create_calls[0]
    assert body["model"] == "gemini-omni-flash-preview"
    assert body["response_format"]["aspect_ratio"] == "9:16"
    assert body["input"] == [{"type": "image", "data": base64.b64encode(ref.data).decode(), "mime_type": "image/png"},
                             {"type": "text", "text": f"a lathe\n\n{EXCLUSIONS}"}]
    assert clients[GEMINI_KEYS[0]].interactions.get_calls == []  # finished at once: nothing to poll
    assert (res.width, res.height) == (720, 1280) and res.warnings == []
    # no token usage reported: the estimate is billed (and marked as one)
    assert [(u.seconds, u.meta) for u in usages] == [(ESTIMATED_SECONDS, {"status": "generated", "estimated": True})]
    other = await provider(s, omni_factory(creates={GEMINI_KEYS[0]: [interaction()]})[0]).generate("x", aspect="4:3")
    assert other.data == MP4  # an aspect Omni does not serve falls back to 16:9


def test_capabilities_and_estimate(gemini_settings) -> None:
    p = OmniVideo(gemini_settings, client_factory=omni_factory()[0])
    assert (p.name, p.paid, p.resumable, p.reports_sending) == ("omni", True, True, True)
    assert p.aspects == frozenset({"16:9", "9:16"}) and p.max_prompt_chars is None
    assert p.model == "gemini-omni-1.1-flash" and p.billed_seconds() == ESTIMATED_SECONDS


# --- errors -----------------------------------------------------------------------------------------------


def test_sdk_errors_are_translated_for_the_gemini_plumbing() -> None:
    from google.genai import errors as genai_errors

    limited = translate_error(sdk_error(429, "quota", retry_delay="7s"))
    assert isinstance(limited, genai_errors.ClientError) and limited.code == 429
    info = classify_gemini_error(limited)
    assert info.retry and info.rate_limited and info.retry_after == 7.0
    server = translate_error(sdk_error(503, "busy"))
    assert isinstance(server, genai_errors.ServerError) and classify_gemini_error(server).retry
    bad = translate_error(sdk_error(400, "API key not valid. Please pass a valid API key."))
    assert bad.code == 400 and "API key not valid" in bad.message and not classify_gemini_error(bad).retry
    assert isinstance(translate_error(sdk_timeout()), TimeoutError)
    from google.genai._gaos.lib import compat_errors as ce

    assert isinstance(translate_error(ce.APIConnectionError(request=httpx.Request("GET", URL))), ConnectionError)
    other = RuntimeError("not an SDK error")
    assert translate_error(other) is other


def test_real_sdk_resource_has_its_own_retries_switched_off() -> None:
    from google import genai

    client = genai.Client(api_key="AIzaTEST-not-a-real-key-000000000000")  # building a client sends nothing
    resource = interactions_of(client)
    assert resource.sdk_configuration.retry_config.strategy == "none"


@pytest.mark.asyncio
async def test_only_unanswered_creates_stay_outstanding(gemini_settings, fake_probe) -> None:
    single = gemini_settings.model_copy(update={"gemini_api_keys": []})
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [sdk_error(429, "quota"), running()]},
                              gets={GEMINI_KEYS[0]: [interaction(tokens=VIDEO_TOKENS_PER_SECOND * 4)]})
    cp = Checkpoint()
    await provider(single, factory, max_attempts=4).generate("x", checkpoint=cp)
    assert [e[0] for e in cp.events] == ["sending", "not_sent", "sending", "submitted", "usage_recorded"]
    assert cp.events[3][1] == ("int-1", fingerprint(GEMINI_KEYS[0]))

    # a create without an answer (a timeout) may have started (and billed) a job: it is never sent again,
    # the record stays outstanding, and the call ends as uncertain for a person to decide
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [sdk_timeout(), sdk_error(503, "busy"), running()]},
                                    gets={GEMINI_KEYS[0]: [interaction()]})
    cp = Checkpoint()
    with pytest.raises(PaidSubmissionUncertain):
        await provider(single, factory, max_attempts=4).generate("x", checkpoint=cp)
    assert [e[0] for e in cp.events] == ["sending"]
    assert len(clients[GEMINI_KEYS[0]].interactions.create_calls) == 1

    # a gateway that gave up waiting (504) is just as uncertain, also on the last attempt
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [sdk_error(504, "deadline")]})
    cp = Checkpoint()
    with pytest.raises(PaidSubmissionUncertain):
        await provider(single, factory, max_attempts=1).generate("x", checkpoint=cp)
    assert [e[0] for e in cp.events] == ["sending"]
    assert len(clients[GEMINI_KEYS[0]].interactions.create_calls) == 1


@pytest.mark.asyncio
async def test_a_model_that_is_not_served_is_unavailable(gemini_settings) -> None:
    single = gemini_settings.model_copy(update={"gemini_api_keys": []})
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [sdk_error(404, "models/gemini-omni-x is not found")]})
    cp, usages = Checkpoint(), []
    with pytest.raises(ProviderUnavailable) as info:
        await provider(single, factory).generate("x", on_usage=usages.append, checkpoint=cp)
    assert info.value.status == 404 and "OMNI_MODEL" in str(info.value)
    assert classify_failure(info.value) == "unavailable"  # the chain moves on and cools omni down
    assert usages == [] and [e[0] for e in cp.events] == ["sending", "not_sent"]  # no job, nothing billed
    assert len(clients[GEMINI_KEYS[0]].interactions.create_calls) == 1

    factory, _ = omni_factory(creates={k: [sdk_error(429, "quota")] * 3 for k in GEMINI_KEYS})
    with pytest.raises(RateLimited):
        await provider(gemini_settings, factory, max_attempts=3).generate("x")


@pytest.mark.asyncio
async def test_blocked_and_failed_interactions(gemini_settings, fake_probe) -> None:
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [running()]},
                              gets={GEMINI_KEYS[0]: [interaction("failed", video=None, input_tokens=0,
                                                                 errors=["Prompt blocked by safety filters"])]})
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked, match="safety"):
        await provider(gemini_settings, factory).generate("x", on_usage=usages.append)
    assert usages == []  # nothing was made, nothing is billed

    # a failed job that still made video tokens is reported (as failed)
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [running()]},
                              gets={GEMINI_KEYS[0]: [interaction("incomplete", video=None, tokens=5792,
                                                                 errors=["internal error"])]})
    cp = Checkpoint()
    with pytest.raises(ProviderError, match="ended as incomplete") as info:
        await provider(gemini_settings, factory).generate("x", on_usage=usages.append, checkpoint=cp)
    assert "internal error" in str(info.value) and not isinstance(info.value, ContentBlocked)
    assert [(u.seconds, u.meta["status"]) for u in usages] == [(1.0, "failed")]
    assert [e[0] for e in cp.events] == ["sending", "submitted", "usage_recorded"]

    # completed with a refusal text and no video
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [interaction(video=None, text="I can't make that: policy")]})
    with pytest.raises(ContentBlocked):
        await provider(gemini_settings, factory).generate("x")
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [interaction(video=None, text="Here is your idea")]})
    with pytest.raises(ProviderError, match="finished without a video"):
        await provider(gemini_settings, factory).generate("x")


@pytest.mark.asyncio
async def test_the_deadline_cancels_the_job_and_reports_the_estimate(gemini_settings, monkeypatch) -> None:
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(omni_mod, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [running()]}, gets={GEMINI_KEYS[0]: [running()] * 50})
    usages: list[Usage] = []
    cp = Checkpoint()
    with pytest.raises(ProviderError, match="timed out") as info:
        await provider(gemini_settings, factory).generate("x", timeout_s=30, on_usage=usages.append, checkpoint=cp)
    assert classify_failure(info.value) == "transient"
    assert clients[GEMINI_KEYS[0]].interactions.cancels == ["int-1"]  # nobody will collect it
    # the accepted job may still be billed: the estimate is reported and noted before giving up
    assert usages == [Usage(provider="omni", model="gemini-omni-1.1-flash", operation="video", seconds=ESTIMATED_SECONDS,
                            units=1, meta={"status": "abandoned", "estimated": True})]
    assert [e[0] for e in cp.events] == ["sending", "submitted", "usage_recorded"]


@pytest.mark.asyncio
async def test_cancellation_reports_the_estimate_and_keeps_the_job_resumable(gemini_settings) -> None:
    factory, clients = omni_factory(creates={GEMINI_KEYS[0]: [running()]},
                                    gets={GEMINI_KEYS[0]: [asyncio.CancelledError()]})
    usages: list[Usage] = []
    with pytest.raises(asyncio.CancelledError):
        await provider(gemini_settings, factory).generate("x", on_usage=usages.append)
    assert [u.meta["status"] for u in usages] == ["abandoned"]
    assert clients[GEMINI_KEYS[0]].interactions.cancels == []  # a restart resumes it

    def failing_sink(_usage: Usage) -> None:
        raise RuntimeError("database is down")

    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [running()]}, gets={GEMINI_KEYS[0]: [asyncio.CancelledError()]})
    with pytest.raises(asyncio.CancelledError):  # the cancellation is never swallowed
        await provider(gemini_settings, factory).generate("x", on_usage=failing_sink)


@pytest.mark.asyncio
async def test_a_polling_failure_reports_the_estimate(gemini_settings) -> None:
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [running()]},
                              gets={GEMINI_KEYS[0]: [sdk_error(400, "bad interaction id")]})
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="Omni video polling failed"):
        await provider(gemini_settings, factory).generate("x", on_usage=usages.append)
    assert [u.meta["status"] for u in usages] == ["abandoned"]


@pytest.mark.asyncio
async def test_usage_is_reported_before_the_download_and_probe(gemini_settings, monkeypatch) -> None:
    async def released(data, settings=None):
        raise asyncio.CancelledError()  # a graceful release (or a kill) during the probe

    monkeypatch.setattr(omni_mod, "probe_media", released)
    factory, _ = omni_factory(creates={GEMINI_KEYS[0]: [interaction(tokens=5792 * 6)]})
    cp, usages = Checkpoint(), []
    with pytest.raises(asyncio.CancelledError):
        await provider(gemini_settings, factory).generate("x", on_usage=usages.append, checkpoint=cp)
    assert [(u.seconds, u.meta["status"]) for u in usages] == [(6.0, "generated")]
    assert [e[0] for e in cp.events] == ["sending", "submitted", "usage_recorded"]


@pytest.mark.asyncio
async def test_unusable_video_is_rejected(make_settings, fake_probe) -> None:
    s = make_settings(gemini_api_key=GEMINI_KEYS[0], ai_max_video_bytes=1_000_000)
    huge = interaction(video=b"\x00" * 1_100_000)
    usages: list[Usage] = []
    with pytest.raises(InvalidMediaOutput, match="exceeds"):
        await provider(s, omni_factory(creates={GEMINI_KEYS[0]: [huge]})[0]).generate("x", on_usage=usages.append)
    assert len(usages) == 1  # billed before the checks
    with pytest.raises(InvalidMediaOutput, match="not an MP4"):
        await provider(s, omni_factory(creates={GEMINI_KEYS[0]: [interaction(video=b"RIFF....WEBP")]})[0]).generate("x")
    fake_probe["duration"] = 0.2
    with pytest.raises(InvalidMediaOutput, match="too short"):
        await provider(s, omni_factory(creates={GEMINI_KEYS[0]: [interaction()]})[0]).generate("x")


@pytest.mark.asyncio
async def test_a_uri_delivered_video_is_downloaded(gemini_settings, fake_probe) -> None:
    factory, clients = omni_factory(
        creates={GEMINI_KEYS[0]: [interaction(video=None, uri="https://generativelanguage.googleapis.com/v1beta/files/v1")]})
    res = await provider(gemini_settings, factory).generate("x")
    assert res.data == MP4
    assert clients[GEMINI_KEYS[0]].aio.files.downloads == ["https://generativelanguage.googleapis.com/v1beta/files/v1"]


# --- resume -----------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_polls_the_same_job_without_submitting(gemini_settings, fake_probe) -> None:
    factory, clients = omni_factory(gets={GEMINI_KEYS[1]: [running(ident="int-7"), interaction(ident="int-7",
                                                                                                tokens=5792 * 8)]})
    usages: list[Usage] = []
    cp = Checkpoint()
    res = await provider(gemini_settings, factory).resume("int-7", fingerprint(GEMINI_KEYS[1]), on_usage=usages.append,
                                                          checkpoint=cp)
    assert res.data == MP4 and clients[GEMINI_KEYS[1]].interactions.get_calls == ["int-7", "int-7"]
    assert all(c.interactions.create_calls == [] for c in clients.values())  # never submits
    assert [(u.seconds, u.meta["status"]) for u in usages] == [(8.0, "generated")]
    assert [e[0] for e in cp.events] == ["usage_recorded"]

    # the charge was already reported before the restart: not again
    factory, _ = omni_factory(gets={GEMINI_KEYS[0]: [interaction(tokens=5792 * 8)]})
    usages.clear()
    await provider(gemini_settings, factory).resume("int-1", fingerprint(GEMINI_KEYS[0]), on_usage=usages.append,
                                                    usage_recorded=True)
    assert usages == []


@pytest.mark.asyncio
async def test_resume_of_lost_jobs(gemini_settings) -> None:
    p = provider(gemini_settings, omni_factory()[0])
    with pytest.raises(OperationLost, match="no longer configured"):  # the key that started it is gone
        await p.resume("int-1", fingerprint("AIzaSomeOtherKeyThatWasRemoved123"))
    factory, _ = omni_factory(gets={GEMINI_KEYS[0]: [sdk_error(404, "interaction not found")]})
    with pytest.raises(OperationLost) as info:
        await provider(gemini_settings, factory).resume("int-1", fingerprint(GEMINI_KEYS[0]))
    assert info.value.status == 404

    # cancelled while polling a resumed job: the estimate is reported once and noted
    factory, _ = omni_factory(gets={GEMINI_KEYS[0]: [running(), asyncio.CancelledError()]})
    cp, usages = Checkpoint(), []
    with pytest.raises(asyncio.CancelledError):
        await provider(gemini_settings, factory).resume("int-1", fingerprint(GEMINI_KEYS[0]), on_usage=usages.append,
                                                        checkpoint=cp)
    assert [u.meta["status"] for u in usages] == ["abandoned"] and [e[0] for e in cp.events] == ["usage_recorded"]


# --- the real SDK over a mock transport ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_sdk_round_trip_over_a_mock_transport(make_settings, fake_probe) -> None:
    """Serialisation, parsing and error classes of the installed google-genai, without any network."""
    from google import genai
    from google.genai import types as gtypes

    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.method == "POST" and len(sent) == 1:
            return httpx.Response(429, json={"error": {"code": 429, "message": "quota", "status": "RESOURCE_EXHAUSTED"}})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "int-9", "status": "in_progress", "model": "gemini-omni-1.1-flash"})
        return httpx.Response(200, json={
            "id": "int-9", "status": "completed", "model": "gemini-omni-1.1-flash",
            "steps": [{"type": "model_output", "content": [
                {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(MP4).decode()}]}],
            "usage": {"total_input_tokens": 20, "total_output_tokens": 5792 * 5,
                      "output_tokens_by_modality": [{"modality": "video", "tokens": 5792 * 5}]}})

    def factory(key: str) -> Any:
        transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return genai.Client(api_key=key, http_options=gtypes.HttpOptions(timeout=5000, httpx_async_client=transport))

    s = make_settings(gemini_api_key=GEMINI_KEYS[0])
    usages: list[Usage] = []
    cp = Checkpoint()
    res = await provider(s, factory, max_attempts=3).generate("a waterfall", aspect="9:16", on_usage=usages.append,
                                                               checkpoint=cp)
    assert res.data == MP4
    posts = [r for r in sent if r.method == "POST"]
    assert len(posts) == 2
    # the 429 reached the adapter (the SDK did not retry it silently): answered, so not outstanding, then re-sent
    assert [e[0] for e in cp.events] == ["sending", "not_sent", "sending", "submitted", "usage_recorded"]
    assert cp.events[3][1] == ("int-9", fingerprint(GEMINI_KEYS[0]))
    body = json.loads(posts[-1].content)
    assert body == {"model": "gemini-omni-1.1-flash", "background": True, "input": f"a waterfall\n\n{EXCLUSIONS}",
                    "response_format": {"aspect_ratio": "9:16", "resolution": "720p", "type": "video"}}
    assert [r.url.path for r in sent if r.method == "GET"] == ["/v1beta/interactions/int-9"]
    assert [(u.seconds, u.input_tokens) for u in usages] == [(5.0, 20)]


# --- one client per job event loop (real SDK, real sockets on 127.0.0.1) --------------------------------------


class _KeepAliveInteractions:
    """A local HTTP/1.1 keep-alive stand-in for the Interactions API: every create answers with a finished clip.

    Real sockets matter here: ``httpx.MockTransport`` opens no connections, so it cannot show a pooled connection
    of a closed event loop being reused by the next job."""

    def __init__(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        posts = self.posts = []

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_: Any) -> None:
                pass

            def do_POST(self) -> None:  # noqa: N802 - http.server's handler name
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                posts.append(self.path)
                body = json.dumps({
                    "id": f"int-{len(posts)}", "status": "completed", "model": "gemini-omni-1.1-flash",
                    "steps": [{"type": "model_output", "content": [
                        {"type": "video", "mime_type": "video/mp4", "data": base64.b64encode(MP4).decode()}]}],
                    "usage": {"total_input_tokens": 20, "total_output_tokens": 5792 * 5,
                              "output_tokens_by_modality": [{"modality": "video", "tokens": 5792 * 5}]},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _jobs_in_their_own_loops(make_settings, caplog, gap_s: float = 0.0, *, jobs: int = 2) -> None:
    import gc
    import logging
    import sys
    import time

    from google import genai
    from google.genai import types as gtypes

    from aadhi.jobs.runner import run_job_loop

    server = _KeepAliveInteractions()
    try:
        def factory(key: str) -> Any:  # the SDK's own httpx.AsyncClient (no httpx_async_client), like production
            return genai.Client(api_key=key, http_options=gtypes.HttpOptions(
                timeout=5000, base_url=f"http://127.0.0.1:{server.port}"))

        omni = OmniVideo(make_settings(gemini_api_key=GEMINI_KEYS[0]), client_factory=factory, sleep=no_sleep,
                         poll_interval_s=0, max_attempts=3)  # one cached adapter for every job, like the factory's
        policies = [None]
        if sys.platform == "win32":  # the selector loop is what Linux and the container use
            policies.append(asyncio.WindowsSelectorEventLoopPolicy())
        with caplog.at_level(logging.ERROR, logger="asyncio"):
            for policy in policies:
                old = asyncio.get_event_loop_policy()
                if policy is not None:
                    asyncio.set_event_loop_policy(policy)
                try:
                    for job in range(jobs):
                        if job and gap_s:
                            time.sleep(gap_s)
                        usages: list[Usage] = []
                        before = len(server.posts)
                        res = run_job_loop(omni.generate("a waterfall", on_usage=usages.append),
                                           name=f"omni-job-{job}")
                        assert res.data == MP4, job
                        assert len(server.posts) - before == 1, job  # one create per job: nothing lost or re-sent
                        assert [(u.provider, u.seconds) for u in usages] == [("omni", 5.0)], job
                        assert omni._caller._clients == {}, job  # closed in the job's own loop when it was done
                finally:
                    asyncio.set_event_loop_policy(old)
            gc.collect()
        # no client of an ended loop is closed on another loop ("Task exception was never retrieved")
        assert not [r for r in caplog.records if "Event loop is closed" in r.getMessage() + str(r.exc_info)]
    finally:
        server.close()


def test_every_job_loop_gets_its_own_interactions_client(make_settings, fake_probe, caplog) -> None:
    """A worker runs each job in a new event loop and closes it afterwards; the cached adapter must not reuse the
    previous job's pooled connection ("Event loop is closed": a lost, possibly billed clip)."""
    _jobs_in_their_own_loops(make_settings, caplog)


@pytest.mark.slow
def test_jobs_after_the_keep_alive_window_get_their_own_client_too(make_settings, fake_probe, caplog) -> None:
    _jobs_in_their_own_loops(make_settings, caplog, gap_s=5.5)


class _ClosingClient:
    def __init__(self) -> None:
        self.closed = 0
        self.aio = SimpleNamespace(aclose=self._aclose)

    async def _aclose(self) -> None:
        self.closed += 1


def test_per_loop_clients_are_closed_by_the_last_lease_in_their_loop(gemini_settings) -> None:
    from aadhi.providers.gemini_common import GeminiCaller

    made: list[_ClosingClient] = []

    def factory(_key: str) -> _ClosingClient:
        made.append(_ClosingClient())
        return made[-1]

    caller = GeminiCaller(gemini_settings, timeout_s=5, client_factory=factory, per_loop=True)

    async def job() -> None:
        async def clip(hold: asyncio.Event | None) -> Any:
            async with caller.loop_lease():
                client = await caller.client(GEMINI_KEYS[0])
                if hold is not None:
                    await hold.wait()
                return client

        release = asyncio.Event()
        long = asyncio.create_task(clip(release))
        await asyncio.sleep(0)
        first = await clip(None)  # a clip that ends while another one of this job still runs
        assert first.closed == 0 and len(caller._clients) == 1
        release.set()
        assert await long is first  # one client per key and loop, shared by the job's concurrent clips
        assert first.closed == 1 and caller._clients == {}  # the last lease closed it, in this loop

    asyncio.run(job())
    asyncio.run(job())
    assert len(made) == 2  # every job loop got its own client


def test_clients_of_closed_loops_are_forgotten(gemini_settings) -> None:
    from aadhi.providers.gemini_common import GeminiCaller

    caller = GeminiCaller(gemini_settings, timeout_s=5, client_factory=lambda key: object(), per_loop=True)

    async def twice(key: str) -> tuple[Any, Any]:
        return await caller.client(key), await caller.client(key)

    a, again = asyncio.run(twice(GEMINI_KEYS[0]))  # no lease: left behind when the loop ends
    assert a is again  # reused within a loop
    b, _ = asyncio.run(twice(GEMINI_KEYS[0]))
    assert b is not a  # a new loop gets its own client
    assert len(caller._clients) == 1  # the closed loop's client was forgotten when the next loop asked
    shared = GeminiCaller(gemini_settings, timeout_s=5, client_factory=lambda key: object())
    assert asyncio.run(shared.client(GEMINI_KEYS[0])) is asyncio.run(shared.client(GEMINI_KEYS[0]))  # one client


# --- the SDK floor ------------------------------------------------------------------------------------------------


def test_the_requirements_floor_is_the_sdk_the_interactions_client_was_verified_against() -> None:
    """``requirements.txt`` never allows a google-genai older than 2.26: Omni's ``client.aio.interactions`` (and the
    ``google.genai._gaos`` SDK behind it) was verified against 2.26, an older SDK fails every clip, and Gemini 3.8
    TTS (the ``GEMINI_TTS_MODEL`` default) needs at least 2.25 (design §10.3 H0). Installs without the lock (a
    plain ``pip install -r requirements.txt``) resolve within this range. The SDK checks name only what this adapter
    uses (the ``interactions`` attribute and the modules it imports by name), never how the SDK implements them."""
    import importlib.util
    import pathlib
    from importlib import metadata

    from google.genai import client as genai_client
    from packaging.requirements import Requirement

    root = pathlib.Path(__file__).resolve().parents[2]
    lines = [ln.split(" #", 1)[0].strip() for ln in (root / "requirements.txt").read_text(encoding="utf-8").splitlines()]
    reqs = {Requirement(ln).name.lower(): Requirement(ln) for ln in lines if ln and not ln.startswith(("#", "-"))}
    spec = reqs["google-genai"].specifier
    assert spec.contains("2.26.0") and not spec.contains("2.25.0") and not spec.contains("2.25.99")
    assert not spec.contains("3.0.0")
    assert spec.contains(metadata.version("google-genai"))  # the installed (locked) SDK is inside the range
    assert hasattr(genai_client.AsyncClient, "interactions")  # client.aio.interactions (interactions_of)
    for module in (omni_mod._NEXTGEN_MODULE, omni_mod._COMPAT_ERRORS_MODULE):  # google.genai._gaos, imported by name
        assert importlib.util.find_spec(module) is not None, module
