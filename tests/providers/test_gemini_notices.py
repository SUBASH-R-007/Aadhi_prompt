"""GeminiLLM keeps its non-streamed request (Gemini 2.5 thinks before its first chunk, so there is no
early sign of life to time out on) and only gains job-log notices: retries and "still waiting"."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from pydantic import BaseModel, Field

from aadhi.providers._notify import ProviderNotice, provider_notices
from aadhi.providers.llm.gemini import GeminiLLM

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import gemini_error, gemini_factory, gemini_text_response

GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})


class Summary(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


@pytest.mark.asyncio
async def test_slow_thinking_call_is_waited_for_and_reported(gemini_settings) -> None:
    factory, clients = gemini_factory({GEMINI_KEYS[0]: []})
    client = factory(GEMINI_KEYS[0])
    calls: list[dict[str, Any]] = []

    async def generate_content(**kwargs: Any) -> Any:  # the only request method the fake has: no streaming
        calls.append(kwargs)
        await asyncio.sleep(0.3)  # thinking: nothing at all arrives for a while
        return gemini_text_response(GOOD)

    client.aio.models.generate_content = generate_content
    llm = GeminiLLM(gemini_settings.model_copy(update={"llm_first_response_timeout_seconds": 0.05,
                                                         "llm_idle_timeout_seconds": 0.05}),
                    client_factory=factory, sleep=no_sleep)
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append, waiting_after_s=0.1, waiting_every_s=10):
        out = await llm.generate_json(model="gemini-2.5-pro", system="s", prompt="p", schema=Summary)
    assert out.title == "Ohm's law" and len(calls) == 1  # never cut off by the OpenAI/Claude stall limits
    assert [(n.kind, n.provider) for n in notices] == [("waiting", "gemini")]
    assert notices[0].message.startswith("Still waiting for the AI service (Gemini) to answer (1 s so far).")


@pytest.mark.asyncio
async def test_retries_are_reported_with_the_gemini_service_name(gemini_settings) -> None:
    factory, clients = gemini_factory({GEMINI_KEYS[0]: [gemini_error(503, "overloaded", "UNAVAILABLE"),
                                                        gemini_text_response(GOOD)]})
    llm = GeminiLLM(gemini_settings, client_factory=factory, sleep=no_sleep)
    notices: list[ProviderNotice] = []
    with provider_notices(notices.append):
        assert (await llm.generate_json(model="gemini-2.5-flash", system="s", prompt="p", schema=Summary)).title
    assert len(notices) == 1 and notices[0].kind == "retry" and notices[0].status == 503
    assert notices[0].message.startswith("The AI service (Gemini) had a temporary problem (HTTP 503); trying again")
    assert notices[0].attempt == 2 and notices[0].max_attempts == llm._caller.max_attempts
