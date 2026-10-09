"""OpenAILLM streaming: stalled requests are retried early, healthy slow answers are never cut off,
and the answer (text, usage, incomplete reason, refusals) comes from the final event."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx2  # the HTTP client used by openai 3.x
import openai
import pytest
from pydantic import BaseModel, Field, ValidationError

from aadhi.config import Settings
from aadhi.providers._notify import ProviderNotice, provider_notices
from aadhi.providers.base import ContentBlocked, ProviderError, RateLimited, Usage
from aadhi.providers.llm.openai import OpenAILLM

from .conftest import no_sleep
from .fakes import (
    STALL,
    FakeOpenAIClient,
    FakeOpenAIStream,
    openai_chat_chunks,
    openai_event,
    openai_response,
    openai_stream_events,
)

OPENAI_KEY = "sk-test-openai-key-123456789"
GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})
FIRST_S = 0.15  # tiny stall limits (the settings' minimums are 10 s / 30 s)
IDLE_S = 0.25


class Summary(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


@pytest.fixture()
def settings(make_settings) -> Settings:
    base = make_settings(llm_provider="openai", openai_api_key=OPENAI_KEY)
    return base.model_copy(update={"llm_first_response_timeout_seconds": FIRST_S, "llm_idle_timeout_seconds": IDLE_S})


def make_llm(settings: Settings, script: list[Any]) -> tuple[OpenAILLM, FakeOpenAIClient]:
    client = FakeOpenAIClient(script)
    return OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep), client


async def generate(llm: OpenAILLM, **kw: Any) -> Summary:
    return await llm.generate_json(model="gpt-4.1-mini", system="s", prompt="p", schema=Summary, **kw)


# --- stalls are retried --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_without_any_answer_is_retried_after_the_first_response_limit(settings) -> None:
    llm, client = make_llm(settings, [STALL, openai_response(GOOD)])
    notices: list[ProviderNotice] = []
    loop = asyncio.get_running_loop()
    started = loop.time()
    with provider_notices(notices.append):
        out = await generate(llm)
    assert out.title == "Ohm's law" and len(client.responses.calls) == 2
    assert loop.time() - started < 5  # not LLM_TIMEOUT_SECONDS (600 s)
    assert [n.message for n in notices] == [
        "The AI service (OpenAI) didn't respond within 1 s; trying again (attempt 2 of 4)."]
    assert notices[0].level == "warning" and notices[0].provider == "openai"


@pytest.mark.asyncio
async def test_stream_silent_before_its_first_event_is_retried(settings) -> None:
    stalled = FakeOpenAIStream([STALL])  # headers arrived, then nothing
    llm, client = make_llm(settings, [stalled, openai_response(GOOD)])
    assert (await generate(llm)).bullets == ["V = IR"]
    assert stalled.closed and len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_stream_that_stops_mid_answer_is_retried(settings) -> None:
    events = openai_stream_events(openai_response(GOOD))
    stalled = FakeOpenAIStream([*events[:2], STALL])  # created + item added, then silence
    llm, client = make_llm(settings, [stalled, openai_response(GOOD)])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        assert (await generate(llm)).title == "Ohm's law"
    assert stalled.delivered == 2 and stalled.closed
    assert notices[0].message == (
        "The AI service (OpenAI) stopped sending its answer for 1 s; trying again (attempt 2 of 4).")


@pytest.mark.asyncio
async def test_every_attempt_stalling_fails_with_a_timeout_after_the_retry_budget(settings) -> None:
    llm, client = make_llm(settings, [STALL, STALL, STALL, STALL])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append), pytest.raises(ProviderError, match="timed out"):
        await llm.generate_text(model="gpt-4.1-mini", system="s", prompt="p")
    assert len(client.responses.calls) == 4  # the existing budget: 4 attempts
    assert [n.attempt for n in notices] == [2, 3, 4]


@pytest.mark.asyncio
async def test_a_dropped_or_unfinished_stream_is_retried(settings) -> None:
    events = openai_stream_events(openai_response(GOOD))
    dropped = FakeOpenAIStream([*events[:2], openai.APIConnectionError(request=httpx2.Request("POST", "https://x"))])
    ended = FakeOpenAIStream(events[:-1])  # the connection closed before the final event
    llm, client = make_llm(settings, [dropped, ended, openai_response(GOOD)])
    assert (await generate(llm)).title
    assert len(client.responses.calls) == 3


# --- healthy answers are never cut off ---------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_but_steady_answer_is_not_cut_off(settings) -> None:
    # 1 s of streaming (far beyond both limits), never silent for longer than 0.1 s
    deltas = [openai_event("response.output_text.delta", output_index=0, delta="x") for _ in range(10)]
    events = openai_stream_events(openai_response(GOOD))
    steady = FakeOpenAIStream([x for ev in [*events[:2], *deltas, *events[2:]] for x in (0.1, ev)])
    llm, client = make_llm(settings, [steady])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=60):
        assert (await generate(llm)).title == "Ohm's law"
    assert len(client.responses.calls) == 1 and notices == []


@pytest.mark.asyncio
async def test_reasoning_model_may_think_silently_longer_than_the_idle_limit(settings) -> None:
    reasoning = SimpleNamespace(type="reasoning", id="rs_1", summary=[])
    final = openai_response(GOOD, reasoning=900)
    message = final.output[0]
    thinking = FakeOpenAIStream([
        openai_event("response.created", response=SimpleNamespace(status="in_progress", output=[])),
        openai_event("response.output_item.added", output_index=0, item=reasoning),
        IDLE_S * 3,  # thinking: no events at all
        openai_event("response.output_item.done", output_index=0, item=reasoning),
        openai_event("response.output_item.added", output_index=1, item=message),
        openai_event("response.output_item.done", output_index=1, item=message),
        openai_event("response.completed", response=final),
    ])
    llm, client = make_llm(settings, [thinking])
    usages: list[Usage] = []
    out = await llm.generate_json(model="o4-mini", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law" and len(client.responses.calls) == 1
    assert usages[0].meta == {"reasoning_tokens": 900}


@pytest.mark.asyncio
async def test_large_request_gets_an_upload_allowance(settings, monkeypatch) -> None:
    from aadhi.providers import _stream

    monkeypatch.setattr(_stream, "UPLOAD_ALLOWANCE_BYTES_PER_S", 1000)  # 1 kB/s: a 500-byte prompt adds 0.5 s
    slow_start = FakeOpenAIStream([0.4, *openai_stream_events(openai_response(GOOD))])  # > FIRST_S, < allowance
    llm, client = make_llm(settings, [slow_start])
    assert (await llm.generate_json(model="gpt-4.1-mini", system="s", prompt="p" * 500, schema=Summary)).title
    assert len(client.responses.calls) == 1


# --- "still waiting" vs. a stall ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stall_is_reported_once_as_a_retry_not_as_still_waiting_first(settings) -> None:
    # the "still waiting" timer is due exactly when the first-response limit fires (60 s / 60 s by default)
    llm, client = make_llm(settings, [STALL, FakeOpenAIStream([STALL]), openai_response(GOOD)])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=FIRST_S):
        assert (await generate(llm)).title == "Ohm's law"
    assert len(client.responses.calls) == 3
    assert [(n.kind, n.level) for n in notices] == [("retry", "warning"), ("retry", "warning")]
    assert notices[0].message == "The AI service (OpenAI) didn't respond within 1 s; trying again (attempt 2 of 4)."


@pytest.mark.asyncio
async def test_a_slow_answer_that_started_still_gets_its_still_waiting_notice(settings) -> None:
    events = openai_stream_events(openai_response(GOOD))
    slow = FakeOpenAIStream([events[0], IDLE_S * 0.8, *events[1:]])  # created at once, then a pause < IDLE_S
    llm, client = make_llm(settings, [slow])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=IDLE_S * 0.4):
        assert (await generate(llm)).title == "Ohm's law"
    assert len(client.responses.calls) == 1
    assert [n.message for n in notices] == [
        "Still waiting for the AI service (OpenAI) to answer (1 s so far). Long answers can take a few minutes."]


# --- keys that may not stream a model --------------------------------------------------------------


def stream_refused(param: str | None = "stream", message: str = "Your organization must be verified to stream this "
                   "model. Please go to: https://platform.openai.com/settings/organization/general") -> Exception:
    response = httpx2.Response(400, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    body = {"message": message, "type": "invalid_request_error", "param": param, "code": "unsupported_value"}
    return openai.BadRequestError(f"Error code: 400 - {{'error': {body}}}", response=response, body=body)


@pytest.mark.asyncio
async def test_a_model_the_key_may_not_stream_is_sent_without_streaming(settings) -> None:
    resp = openai_response(GOOD, input_tokens=70, output_tokens=30, reasoning=12)
    resp.usage.input_tokens_details.cached_tokens = 64
    llm, client = make_llm(settings, [stream_refused(), resp, openai_response(GOOD), openai_response(GOOD)])
    usages: list[Usage] = []
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        out = await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law" and notices == []  # one attempt: nothing was retried
    first, plain = client.responses.calls
    assert first["stream"] is True and "stream" not in plain and plain["text"] == first["text"]
    assert usages == [Usage(provider="openai", model="o3", operation="llm", input_tokens=70, output_tokens=30,
                            meta={"reasoning_tokens": 12, "cached_tokens": 64})]
    # remembered for this key: the next call to o3 is not streamed; other models still are
    assert (await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary)).title
    assert "stream" not in client.responses.calls[2]
    assert (await generate(llm)).title and client.responses.calls[3]["stream"] is True


@pytest.mark.asyncio
async def test_unstreamed_answers_are_read_like_streamed_ones(settings) -> None:
    cut = openai_response('{"title": "Oh', incomplete="max_output_tokens")
    llm, client = make_llm(settings, [stream_refused(), cut, openai_response(GOOD)])
    assert (await llm.generate_json(model="gpt-5", system="s", prompt="p", schema=Summary)).title == "Ohm's law"
    assert [c.get("stream") for c in client.responses.calls] == [True, None, None]  # truncated -> re-asked
    assert "cut off at the length limit" in client.responses.calls[2]["input"][2]["content"]
    llm, _ = make_llm(settings, [stream_refused(), openai_response("", refusal="I can't help with that")])
    with pytest.raises(ContentBlocked, match="refused: I can't help with that"):
        await llm.generate_json(model="gpt-5", system="s", prompt="p", schema=Summary)
    llm, _ = make_llm(settings, [stream_refused(), openai_response("", incomplete="content_filter")])
    with pytest.raises(ContentBlocked):
        await llm.generate_json(model="gpt-5", system="s", prompt="p", schema=Summary)


@pytest.mark.asyncio
async def test_other_bad_requests_still_fail_without_a_retry(settings) -> None:
    llm, client = make_llm(settings, [stream_refused(param="temperature", message="Unsupported parameter")])
    with pytest.raises(ProviderError):
        await generate(llm)
    assert len(client.responses.calls) == 1 and llm._no_stream == set()
    # a refused stream whose plain request then fails is not retried as a stall either
    llm, client = make_llm(settings, [stream_refused(), stream_refused(param=None, message="Invalid schema")])
    with pytest.raises(ProviderError):
        await generate(llm)
    assert len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_real_sdk_stream_refusal_then_plain_response(settings) -> None:
    requests: list[dict[str, Any]] = []
    final = _sse_answer(GOOD)[-1]["response"]

    async def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        requests.append(body)
        if body.get("stream"):
            return httpx2.Response(400, json={"error": {
                "message": "Your organization must be verified to stream this model. Please go to: "
                           "https://platform.openai.com/settings/organization/general and click on Verify "
                           "Organization.", "type": "invalid_request_error", "param": "stream",
                "code": "unsupported_value"}})
        return httpx2.Response(200, json=final)

    client = openai.AsyncOpenAI(api_key=OPENAI_KEY, max_retries=0,
                                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    out = await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law" and [r.get("stream") for r in requests] == [True, None]
    assert usages[0].input_tokens == 50 and usages[0].output_tokens == 20 and usages[0].meta == {"cached_tokens": 10}
    await client.close()


# --- the answer comes from the final event ------------------------------------------------------


@pytest.mark.asyncio
async def test_text_usage_and_request_come_from_the_stream(settings) -> None:
    resp = openai_response(GOOD, input_tokens=70, output_tokens=30, reasoning=4)
    resp.usage.input_tokens_details.cached_tokens = 64
    llm, client = make_llm(settings, [resp])
    usages: list[Usage] = []
    await generate(llm, on_usage=usages.append, max_output_tokens=900)
    call = client.responses.calls[0]
    assert call["stream"] is True and call["max_output_tokens"] == 900 and call["text"]["format"]["name"] == "Summary"
    assert usages == [Usage(provider="openai", model="gpt-4.1-mini", operation="llm", input_tokens=70,
                            output_tokens=30, meta={"reasoning_tokens": 4, "cached_tokens": 64})]


@pytest.mark.asyncio
async def test_incomplete_answers_are_truncated_or_blocked(settings) -> None:
    cut = openai_response('{"title": "Oh', incomplete="max_output_tokens")
    llm, client = make_llm(settings, [cut, openai_response(GOOD)])
    assert (await generate(llm)).title == "Ohm's law"  # truncated -> re-asked like before
    reask = client.responses.calls[1]["input"]
    assert [i["role"] for i in reask] == ["user", "assistant", "user"] and reask[1]["content"] == '{"title": "Oh'
    assert "cut off at the length limit" in reask[2]["content"]
    llm, _ = make_llm(settings, [openai_response("", incomplete="content_filter")])
    with pytest.raises(ContentBlocked):
        await generate(llm)
    llm, _ = make_llm(settings, [openai_response("", refusal="I can't help with that")])
    with pytest.raises(ContentBlocked, match="refused: I can't help with that"):
        await generate(llm)


@pytest.mark.asyncio
async def test_final_event_without_output_is_rebuilt_from_the_items(settings) -> None:
    resp = openai_response(GOOD)
    events = openai_stream_events(resp)
    events[-1] = openai_event("response.completed", response=SimpleNamespace(
        status="completed", incomplete_details=None, output=None, usage=resp.usage))
    llm, _ = make_llm(settings, [FakeOpenAIStream(events)])
    assert (await generate(llm)).title == "Ohm's law"

    refused = openai_response("", refusal="no")
    events = openai_stream_events(refused)
    events[-1] = openai_event("response.completed", response=SimpleNamespace(
        status="completed", incomplete_details=None, output=None, usage=refused.usage))
    llm, _ = make_llm(settings, [FakeOpenAIStream(events)])
    with pytest.raises(ContentBlocked, match="refused: no"):
        await generate(llm)


@pytest.mark.asyncio
async def test_errors_inside_the_stream(settings) -> None:
    created = openai_event("response.created", response=SimpleNamespace(status="in_progress", output=[]))
    server = FakeOpenAIStream([created, openai_event("error", code="server_error", message="try again")])
    failed = FakeOpenAIStream([created, openai_event("response.failed", response=SimpleNamespace(
        error=SimpleNamespace(code="server_error", message="The model failed")))])
    llm, client = make_llm(settings, [server, failed, openai_response(GOOD)])
    assert (await generate(llm)).title  # server errors inside the stream are retried
    assert len(client.responses.calls) == 3

    invalid = FakeOpenAIStream([created, openai_event("response.failed", response=SimpleNamespace(
        error=SimpleNamespace(code="invalid_prompt", message=f"bad prompt {OPENAI_KEY}")))])
    llm, client = make_llm(settings, [invalid])
    with pytest.raises(ProviderError) as info:
        await generate(llm)
    assert len(client.responses.calls) == 1 and "invalid_prompt" in str(info.value)
    assert OPENAI_KEY not in str(info.value)  # redacted

    limited = [FakeOpenAIStream([created, openai_event("error", code="rate_limit_exceeded", message="slow")])
               for _ in range(4)]
    llm, client = make_llm(settings, limited)
    with pytest.raises(RateLimited):
        await generate(llm)
    assert len(client.responses.calls) == 4


# --- chat-completions fallback -----------------------------------------------------------------


class ChatOnlyClient:
    """A client without the Responses API: ``chat.completions.create`` answers from ``script`` (a
    list of chunks is streamed; an exception is raised; a request without ``stream`` gets the item)."""

    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

        async def create(**kwargs: Any) -> Any:
            self.calls.append(kwargs)
            item = self.script.pop(0)
            if item is STALL:
                await asyncio.sleep(3600)
            if isinstance(item, BaseException):
                raise item
            if not kwargs.get("stream"):
                return item
            return item if isinstance(item, FakeOpenAIStream) else FakeOpenAIStream(item)

        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


@pytest.mark.asyncio
async def test_chat_fallback_streams_retries_stalls_and_reads_the_final_chunks(settings) -> None:
    client = ChatOnlyClient([STALL, [*openai_chat_chunks(GOOD)[:2], STALL], openai_chat_chunks(GOOD, pieces=4)])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    out = await generate(llm, on_usage=usages.append)
    assert out.title == "Ohm's law" and len(client.calls) == 3
    assert client.calls[0]["stream_options"] == {"include_usage": True}
    assert usages == [Usage(provider="openai", model="gpt-4.1-mini", operation="llm", input_tokens=11,
                            output_tokens=7)]

    client = ChatOnlyClient([openai_chat_chunks("", refusal="not this")])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    with pytest.raises(ContentBlocked, match="refused: not this"):
        await generate(llm)
    client = ChatOnlyClient([openai_chat_chunks('{"title": "O', finish="length"), openai_chat_chunks(GOOD)])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    assert (await generate(llm)).title and len(client.calls) == 2  # truncated -> re-asked

    # a slow but steady stream (~0.8 s, never silent for 0.25 s) is not cut off
    client = ChatOnlyClient([[x for c in openai_chat_chunks(GOOD, pieces=6) for x in (0.08, c)]])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    assert (await generate(llm)).title and len(client.calls) == 1

    # a reasoning model may send its first chunk only after thinking: no first-response limit
    client = ChatOnlyClient([[IDLE_S * 2, *openai_chat_chunks(GOOD)]])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    assert (await llm.generate_json(model="o4-mini", system="s", prompt="p", schema=Summary)).title
    assert len(client.calls) == 1


def chat_completion(text: str, *, finish: str = "stop", refusal: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(role="assistant", content=text or None, refusal=refusal)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)],
                           usage=SimpleNamespace(prompt_tokens=13, completion_tokens=9))


@pytest.mark.asyncio
async def test_chat_fallback_sends_a_model_the_key_may_not_stream_without_streaming(settings) -> None:
    client = ChatOnlyClient([stream_refused(), chat_completion(GOOD), chat_completion(GOOD)])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        out = await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law" and notices == []
    assert client.calls[0]["stream"] is True
    assert "stream" not in client.calls[1] and "stream_options" not in client.calls[1]
    assert usages == [Usage(provider="openai", model="o3", operation="llm", input_tokens=13, output_tokens=9)]
    assert (await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary)).title
    assert len(client.calls) == 3 and "stream" not in client.calls[2]  # remembered

    client = ChatOnlyClient([stream_refused(), chat_completion("", refusal="not this")])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    with pytest.raises(ContentBlocked, match="refused: not this"):
        await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary)
    client = ChatOnlyClient([stream_refused(), chat_completion('{"title": "O', finish="length"),
                             chat_completion(GOOD)])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    assert (await llm.generate_json(model="o3", system="s", prompt="p", schema=Summary)).title
    assert len(client.calls) == 3  # truncated -> re-asked, without streaming

    client = ChatOnlyClient([stream_refused(param="response_format", message="Invalid schema")])
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    with pytest.raises(ProviderError):
        await generate(llm)
    assert len(client.calls) == 1  # any other 400 still fails at once


# --- the real SDK over a mock transport (SSE parsing, no network) ---------------------------------


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _sse_answer(text: str) -> list[dict[str, Any]]:
    item = {"id": "msg_1", "type": "message", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}]}
    response = {"id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-4.1-mini",
                "parallel_tool_calls": True, "tool_choice": "auto", "tools": []}
    usage = {"input_tokens": 50, "input_tokens_details": {"cached_tokens": 10}, "output_tokens": 20,
             "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 70}
    return [
        {"type": "response.created", "sequence_number": 0, "response": {**response, "status": "in_progress", "output": []}},
        {"type": "response.output_item.added", "sequence_number": 1, "output_index": 0,
         "item": {**item, "status": "in_progress", "content": []}},
        {"type": "response.output_text.delta", "sequence_number": 2, "item_id": "msg_1", "output_index": 0,
         "content_index": 0, "delta": text},
        {"type": "response.output_item.done", "sequence_number": 3, "output_index": 0, "item": item},
        {"type": "response.completed", "sequence_number": 4,
         "response": {**response, "status": "completed", "output": [item], "usage": usage}},
    ]


@pytest.mark.asyncio
async def test_real_sdk_stream_with_a_stalled_connection_then_a_good_answer(settings) -> None:
    requests: list[dict[str, Any]] = []
    events = _sse_answer(GOOD)

    async def stalled_body():
        yield _sse(events[0])
        await asyncio.sleep(3600)
        yield b""

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            await asyncio.sleep(3600)  # no response headers at all (dead pooled connection)
        if len(requests) == 2:
            return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=stalled_body())
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(*events))

    client = openai.AsyncOpenAI(api_key=OPENAI_KEY, max_retries=0,
                                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        out = await generate(llm, on_usage=usages.append)
    assert out.title == "Ohm's law" and len(requests) == 3
    assert requests[0]["stream"] is True and requests[0]["text"]["format"]["type"] == "json_schema"
    assert usages[0].input_tokens == 50 and usages[0].output_tokens == 20 and usages[0].meta == {"cached_tokens": 10}
    assert [n.message.split(";")[0] for n in notices] == [
        "The AI service (OpenAI) didn't respond within 1 s",
        "The AI service (OpenAI) stopped sending its answer for 1 s"]
    await client.close()


@pytest.mark.asyncio
async def test_real_sdk_incomplete_response_is_read_from_the_final_event(settings) -> None:
    events = _sse_answer('{"title": "Ohm')
    final = events[-1]
    final["type"] = "response.incomplete"
    final["response"]["status"] = "incomplete"
    final["response"]["incomplete_details"] = {"reason": "max_output_tokens"}

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(*events))

    client = openai.AsyncOpenAI(api_key=OPENAI_KEY, max_retries=0,
                                http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="cut off at the length limit"):
        await generate(llm, on_usage=usages.append, validation_retries=0)
    assert len(usages) == 1 and usages[0].output_tokens == 20  # billed although cut off
    await client.close()


# --- settings ----------------------------------------------------------------------------------


def test_stall_timeout_settings_defaults_and_bounds(make_settings) -> None:
    s = make_settings()
    assert (s.llm_first_response_timeout_seconds, s.llm_idle_timeout_seconds, s.llm_timeout_seconds) == (60, 240, 600)
    assert make_settings(llm_first_response_timeout_seconds=10, llm_idle_timeout_seconds=1800).llm_idle_timeout_seconds == 1800
    for bad in ({"llm_first_response_timeout_seconds": 9}, {"llm_first_response_timeout_seconds": 601},
                {"llm_idle_timeout_seconds": 29}, {"llm_idle_timeout_seconds": 1801}):
        with pytest.raises(ValidationError):
            make_settings(**bad)
