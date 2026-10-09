"""AnthropicLLM: a request that gets no answer is retried early; a long (thinking) answer is not cut
off; the client's read timeout is the idle limit (keep-alive pings keep a thinking stream alive)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel, Field

from aadhi.config import Settings
from aadhi.providers._notify import ProviderNotice, provider_notices
from aadhi.providers.anthropic_common import AnthropicCaller
from aadhi.providers.base import ProviderError, Usage
from aadhi.providers.llm.anthropic import AnthropicLLM

from .conftest import no_sleep
from .test_anthropic_llm import ANTHROPIC_KEY, FakeAnthropicClient, FakeStream, _sse_message, claude_message

GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})
FIRST_S = 0.15


class Summary(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


class SlowStream(FakeStream):
    """The request is answered after ``start_s`` (headers), the final message after ``answer_s``."""

    def __init__(self, item: Any, *, start_s: float = 0.0, answer_s: float = 0.0) -> None:
        super().__init__(item)
        self.start_s = start_s
        self.answer_s = answer_s

    async def __aenter__(self) -> SlowStream:
        await asyncio.sleep(self.start_s)
        return self

    async def get_final_message(self) -> Any:
        await asyncio.sleep(self.answer_s)
        return self.item


class Messages:
    def __init__(self, streams: list[FakeStream]) -> None:
        self.streams = streams
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        return self.streams.pop(0)


@pytest.fixture()
def settings(make_settings) -> Settings:
    base = make_settings(llm_provider="anthropic", anthropic_api_key=ANTHROPIC_KEY)
    return base.model_copy(update={"llm_first_response_timeout_seconds": FIRST_S})


def make_llm(settings: Settings, streams: list[FakeStream]) -> tuple[AnthropicLLM, Messages]:
    client = FakeAnthropicClient([])
    client.beta.messages = Messages(streams)
    return AnthropicLLM(settings, client_factory=lambda: client, sleep=no_sleep), client.beta.messages


@pytest.mark.asyncio
async def test_request_without_an_answer_is_retried_after_the_first_response_limit(settings) -> None:
    llm, messages = make_llm(settings, [SlowStream(claude_message(), start_s=3600), FakeStream(claude_message())])
    notices: list[ProviderNotice] = []
    usages: list[Usage] = []
    with provider_notices(notices.append):
        out = await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary,
                                      on_usage=usages.append)
    assert out.title == "Ohm's law" and len(messages.calls) == 2
    assert [n.message for n in notices] == [
        "The AI service (Claude) didn't respond within 1 s; trying again (attempt 2 of 4)."]
    assert len(usages) == 1  # only the answered attempt is billed


@pytest.mark.asyncio
async def test_long_thinking_answer_is_not_cut_off(settings) -> None:
    # the request is answered at once, then Claude thinks for 4x the first-response limit
    llm, messages = make_llm(settings, [SlowStream(claude_message(), start_s=0.02, answer_s=FIRST_S * 4)])
    assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert len(messages.calls) == 1


@pytest.mark.asyncio
async def test_a_stall_is_reported_once_as_a_retry_and_a_thinking_answer_gets_still_waiting(settings) -> None:
    # the "still waiting" timer is due when the first-response limit fires: only the retry is reported
    llm, messages = make_llm(settings, [SlowStream(claude_message(), start_s=3600), FakeStream(claude_message())])
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=FIRST_S):
        assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert [n.message for n in notices] == [
        "The AI service (Claude) didn't respond within 1 s; trying again (attempt 2 of 4)."]

    # answered at once, then Claude thinks: the notice comes on time
    llm, messages = make_llm(settings, [SlowStream(claude_message(), answer_s=FIRST_S * 2)])
    notices.clear()
    with provider_notices(notices.append, waiting_after_s=FIRST_S * 0.6):
        assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert len(messages.calls) == 1
    assert [n.message for n in notices] == [
        "Still waiting for the AI service (Claude) to answer (1 s so far). Long answers can take a few minutes."]


@pytest.mark.asyncio
async def test_every_attempt_stalling_ends_as_a_timeout(settings) -> None:
    llm, messages = make_llm(settings, [SlowStream(claude_message(), start_s=3600) for _ in range(4)])
    with pytest.raises(ProviderError, match="timed out"):
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p")
    assert len(messages.calls) == 4


@pytest.mark.asyncio
async def test_real_sdk_request_without_headers_is_retried(settings) -> None:
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if len(requests) == 1:
            await asyncio.sleep(3600)  # sent into a dead connection: no response headers
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse_message(GOOD))

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, max_retries=0,
                                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = AnthropicLLM(settings, client_factory=lambda: client, sleep=no_sleep)
    out = await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert out.title == "Ohm's law" and len(requests) == 2
    await client.close()


@pytest.mark.asyncio
async def test_read_timeout_is_the_idle_limit_and_stays_within_the_call_cap(make_settings) -> None:
    s = make_settings(anthropic_api_key=ANTHROPIC_KEY, llm_idle_timeout_seconds=300)
    client = await AnthropicLLM(s)._caller.client()
    assert client.timeout == anthropic.Timeout(1800, read=300)
    await client.close()
    caller = AnthropicCaller(s, timeout_s=120, idle_timeout_s=300)  # never longer than the whole call
    client = await caller.client()
    assert client.timeout == anthropic.Timeout(120, read=120)
    await client.close()
    client = await AnthropicCaller(s, timeout_s=120).client()  # no idle limit given: one timeout as before
    assert client.timeout == 120
    await client.close()
