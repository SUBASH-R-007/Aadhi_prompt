"""OpenAILLM with a mocked AsyncOpenAI client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel, Field

from aadhi.providers.base import (
    ContentBlocked,
    FileInput,
    ImageInput,
    ProviderError,
    ProviderNotConfigured,
    RateLimited,
    Usage,
)
from aadhi.providers.llm import openai as openai_llm
from aadhi.providers.llm.openai import OpenAILLM, resolve_model

from .conftest import no_sleep
from .fakes import FakeOpenAIClient, FakeOpenAIStream, openai_chat_chunks, openai_response, openai_status_error

OPENAI_KEY = "sk-test-openai-key-123456789"


class Summary(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1)


GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})


@pytest.fixture()
def settings(make_settings):
    return make_settings(llm_provider="openai", openai_api_key=OPENAI_KEY)


def make_llm(settings, script):
    client = FakeOpenAIClient(script)
    return OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep), client


@pytest.mark.asyncio
async def test_success_and_request_shape(settings) -> None:
    llm, client = make_llm(settings, [openai_response(GOOD, reasoning=3)])
    usages: list[Usage] = []
    out = await llm.generate_json(
        model="gpt-4.1", system="Teach", prompt="Summarise", schema=Summary, temperature=0.3,
        max_output_tokens=1000, on_usage=usages.append,
        files=[FileInput(data=b"%PDF-1.7", mime="application/pdf", name="notes.pdf"),
               FileInput(data=b"# Notes\nOhm", mime="text/markdown", name="notes.md")],
        images=[ImageInput(data=b"\x89PNG", mime="image/png")],
    )
    assert out.title == "Ohm's law"
    call = client.responses.calls[0]
    assert call["model"] == "gpt-4.1" and call["instructions"] == "Teach"
    assert call["temperature"] == 0.3 and call["max_output_tokens"] == 1000
    fmt = call["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["name"] == "Summary" and fmt["strict"] is False
    assert fmt["schema"]["additionalProperties"] is False
    content = call["input"][0]["content"]
    assert content[0]["type"] == "input_file" and content[0]["file_data"].startswith("data:application/pdf;base64,")
    assert content[1] == {"type": "input_text", "text": "[notes.md]\n# Notes\nOhm"}
    assert content[2]["type"] == "input_image" and content[2]["image_url"].startswith("data:image/png;base64,")
    assert content[3] == {"type": "input_text", "text": "Summarise"}
    assert usages[0] == Usage(provider="openai", model="gpt-4.1", operation="vision", input_tokens=50,
                              output_tokens=20, meta={"reasoning_tokens": 3})


@pytest.mark.asyncio
async def test_reask_appends_assistant_and_user_turns(settings) -> None:
    llm, client = make_llm(settings, [openai_response('{"title": ""}'), openai_response(GOOD)])
    out = await llm.generate_json(model="gpt-4.1-mini", system="s", prompt="p", schema=Summary)
    assert out.bullets == ["V = IR"]
    items = client.responses.calls[1]["input"]
    assert [i["role"] for i in items] == ["user", "assistant", "user"]
    assert items[1]["content"] == '{"title": ""}'
    assert "bullets" in items[2]["content"]


@pytest.mark.asyncio
async def test_rate_limit_retry_then_success(settings) -> None:
    err = openai_status_error(openai.RateLimitError, 429, "slow down", headers={"retry-after": "1"})
    llm, client = make_llm(settings, [err, openai_response(GOOD)])
    assert (await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)).title
    assert len(client.responses.calls) == 2


@pytest.mark.asyncio
async def test_rate_limit_exhausted_and_quota(settings) -> None:
    errs = [openai_status_error(openai.RateLimitError, 429, "slow down") for _ in range(4)]
    llm, client = make_llm(settings, errs)
    with pytest.raises(RateLimited):
        await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    assert len(client.responses.calls) == 4

    quota = openai_status_error(openai.RateLimitError, 429, "You exceeded your quota", code="insufficient_quota")
    llm, client = make_llm(settings, [quota])
    with pytest.raises(RateLimited):
        await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    assert len(client.responses.calls) == 1  # quota problems are not retried


@pytest.mark.asyncio
async def test_server_errors_retried_bad_request_not(settings) -> None:
    llm, client = make_llm(settings, [openai_status_error(openai.InternalServerError, 500, "boom"), openai_response(GOOD)])
    await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    assert len(client.responses.calls) == 2

    llm, client = make_llm(settings, [openai_status_error(openai.BadRequestError, 400, "bad schema")])
    with pytest.raises(ProviderError) as info:
        await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    assert info.value.status == 400 and "api.openai.com" in str(info.value)
    assert len(client.responses.calls) == 1


@pytest.mark.asyncio
async def test_content_filter_and_refusal(settings) -> None:
    llm, _ = make_llm(settings, [openai_response("", incomplete="content_filter")])
    with pytest.raises(ContentBlocked):
        await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    llm, _ = make_llm(settings, [openai_response("", refusal="I can't help with that")])
    with pytest.raises(ContentBlocked, match="refused"):
        await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary)
    policy = openai_status_error(openai.BadRequestError, 400, "flagged", code="content_policy_violation")
    llm, _ = make_llm(settings, [policy])
    with pytest.raises(ContentBlocked):
        await llm.generate_text(model="gpt-4.1", system="s", prompt="p")


@pytest.mark.asyncio
async def test_redaction(settings) -> None:
    err = openai_status_error(openai.AuthenticationError, 401, f"Incorrect API key provided: {OPENAI_KEY}")
    llm, _ = make_llm(settings, [err])
    with pytest.raises(ProviderError) as info:
        await llm.generate_text(model="gpt-4.1", system="s", prompt="p")
    assert OPENAI_KEY not in str(info.value) and "[REDACTED]" in str(info.value)


@pytest.mark.asyncio
async def test_reasoning_models_omit_temperature_and_gemini_names_map(settings) -> None:
    llm, client = make_llm(settings, [openai_response(GOOD), openai_response(GOOD)])
    await llm.generate_json(model="o4-mini", system="s", prompt="p", schema=Summary)
    assert "temperature" not in client.responses.calls[0]
    await llm.generate_json(model="gemini-2.5-pro", system="s", prompt="p", schema=Summary)
    assert client.responses.calls[1]["model"] == "gpt-4.1"
    assert resolve_model("gemini-2.5-flash") == "gpt-4.1-mini"
    assert resolve_model("gpt-5") == "gpt-5"


@pytest.mark.asyncio
async def test_large_pdf_uploaded_and_unsupported_type(settings, monkeypatch) -> None:
    monkeypatch.setattr(openai_llm, "INLINE_FILE_LIMIT", 4)
    llm, client = make_llm(settings, [openai_response(GOOD)])
    await llm.generate_json(model="gpt-4.1", system="s", prompt="p", schema=Summary,
                            files=[FileInput(data=b"%PDF-big", mime="application/pdf", name="big.pdf")])
    assert client.files.created == [("big.pdf", 8, "user_data")]
    assert client.responses.calls[0]["input"][0]["content"][0] == {"type": "input_file", "file_id": "file-1"}
    with pytest.raises(ProviderError, match="unsupported file type"):
        await llm.generate_text(model="gpt-4.1", system="s", prompt="p",
                                files=[FileInput(data=b"PK", mime="application/zip")])


@pytest.mark.asyncio
async def test_chat_completions_fallback(settings) -> None:
    class ChatOnly:
        def __init__(self) -> None:
            self.calls: list[dict] = []

            async def create(**kwargs):
                self.calls.append(kwargs)
                return FakeOpenAIStream(openai_chat_chunks(GOOD, prompt_tokens=11, completion_tokens=7))

            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

    client = ChatOnly()
    llm = OpenAILLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    out = await llm.generate_json(model="gpt-4.1", system="sys", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law"
    call = client.calls[0]
    assert call["messages"][0] == {"role": "system", "content": "sys"}
    assert call["response_format"]["json_schema"]["strict"] is False
    assert call["stream"] is True and call["stream_options"] == {"include_usage": True}  # usage in the last chunk
    assert usages[0].input_tokens == 11 and usages[0].output_tokens == 7


def test_requires_key(make_settings) -> None:
    with pytest.raises(ProviderNotConfigured):
        OpenAILLM(make_settings(llm_provider="openai"))


@pytest.mark.asyncio
async def test_refusal_reports_usage_before_raising(settings) -> None:
    llm, _ = make_llm(settings, [openai_response("", refusal="no", input_tokens=12, output_tokens=3)])
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked):
        await llm.generate_text(model="gpt-4.1", system="s", prompt="p", on_usage=usages.append)
    assert usages and usages[0].input_tokens == 12
