"""AnthropicLLM with a fake ``client.beta.messages.stream`` (and the real SDK over a mock transport)."""

from __future__ import annotations

import copy
import json
import logging
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2  # the HTTP client used by anthropic 1.x
import pytest
from pydantic import BaseModel, Field

from aadhi.providers._retry import ErrorInfo
from aadhi.providers.base import (
    ContentBlocked,
    FileInput,
    ImageInput,
    ProviderError,
    ProviderNotConfigured,
    RateLimited,
    Usage,
)
from aadhi.providers.llm import anthropic as anthropic_llm
from aadhi.providers.llm.anthropic import (
    FALLBACK_BETA,
    JSON_PROMPT,
    AnthropicLLM,
    effort_for,
    max_tokens_for,
    resolve_model,
    supports_fallbacks,
)
from aadhi.providers.llm.structured import Turn
from aadhi.usage.pricing import estimate_cost

from .conftest import no_sleep

ANTHROPIC_KEY = "sk-ant-test-key-0123456789abcdef"
_URL = "https://api.anthropic.com/v1/messages"
_SAMPLING = ("temperature", "top_p", "top_k", "thinking")


class Summary(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    bullets: list[str] = Field(min_length=1)


GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})


# --- fakes ------------------------------------------------------------------------------------


def claude_message(text: str = GOOD, *, stop: str = "end_turn", model: str = "claude-opus-5-5", input_tokens: int = 50,
                   output_tokens: int = 20, cache_write: int = 0, cache_read: int = 0, category: str | None = None,
                   blocks: list[Any] | None = None, iterations: list[Any] | None = None) -> SimpleNamespace:
    """Object shaped like ``BetaMessage`` (only the fields the adapter reads)."""
    return SimpleNamespace(
        id="msg_test", role="assistant", model=model, stop_reason=stop,
        content=blocks if blocks is not None else [SimpleNamespace(type="text", text=text)],
        stop_details=SimpleNamespace(type="refusal", category=category, explanation="") if stop == "refusal" else None,
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_creation_input_tokens=cache_write, cache_read_input_tokens=cache_read,
                              iterations=iterations),
    )


def hop(kind: str, model: str | None, *, input_tokens: int = 0, output_tokens: int = 0, cache_write: int = 0,
        cache_read: int = 0) -> SimpleNamespace:
    """One ``usage.iterations`` entry (``BetaMessageIterationUsage`` / ``BetaFallbackMessageIterationUsage``)."""
    return SimpleNamespace(type=kind, model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                           cache_creation_input_tokens=cache_write, cache_read_input_tokens=cache_read)


def claude_error(cls: type, status: int, message: str, *, kind: str = "invalid_request_error",
                 headers: dict[str, str] | None = None) -> Exception:
    response = httpx2.Response(status, request=httpx2.Request("POST", _URL), headers=headers or {})
    return cls(message, response=response, body={"type": "error", "error": {"type": kind, "message": message}})


class FakeStream:
    """``BetaAsyncMessageStreamManager`` stand-in: the request happens on ``__aenter__`` like the SDK."""

    def __init__(self, item: Any) -> None:
        self.item = item

    async def __aenter__(self) -> FakeStream:
        if isinstance(self.item, BaseException):
            raise self.item
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def get_final_message(self) -> Any:
        return self.item


class FakeMessages:
    def __init__(self, script: list[Any]) -> None:
        self.script = script
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(copy.deepcopy(kwargs))
        return FakeStream(self.script.pop(0))


class FakeAnthropicClient:
    def __init__(self, script: list[Any]) -> None:
        self.beta = SimpleNamespace(messages=FakeMessages(script))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self.beta.messages.calls


@pytest.fixture()
def settings(make_settings):
    return make_settings(llm_provider="anthropic", anthropic_api_key=ANTHROPIC_KEY)


def make_llm(settings, script: list[Any], **kw: Any) -> tuple[AnthropicLLM, FakeAnthropicClient]:
    client = FakeAnthropicClient(script)
    return AnthropicLLM(settings, client_factory=lambda: client, sleep=kw.pop("sleep", no_sleep), **kw), client


def breakpoints(call: dict[str, Any]) -> int:
    blocks = list(call.get("system") or [])
    for msg in call["messages"]:
        if isinstance(msg["content"], list):
            blocks += msg["content"]
    return sum(1 for b in blocks if "cache_control" in b) + (1 if "cache_control" in call else 0)


# --- request shape ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_request_shape_and_usage(settings) -> None:
    llm, client = make_llm(settings, [claude_message(input_tokens=40, cache_write=900, cache_read=3000)])
    usages: list[Usage] = []
    out = await llm.generate_json(
        model="claude-opus-5-5", system="Teach", prompt="Summarise", schema=Summary, temperature=0.3,
        max_output_tokens=1000, on_usage=usages.append,
        files=[FileInput(data=b"%PDF-1.7", mime="application/pdf", name="notes.pdf"),
               FileInput(data=b"# Notes\nOhm", mime="text/markdown", name="notes.md")],
        images=[ImageInput(data=b"\x89PNG", mime="image/png")],
    )
    assert out.title == "Ohm's law"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["max_tokens"] == 64000  # configured floor beats the caller's 1000 (thinking counts toward it)
    assert not set(_SAMPLING) & set(call)  # sampling params 400 on current models; thinking stays adaptive
    assert call["system"] == [{"type": "text", "text": "Teach", "cache_control": {"type": "ephemeral"}}]
    assert call["output_config"]["effort"] == "high"
    fmt = call["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert "maxLength" not in json.dumps(fmt) and "1 to 80 characters." in json.dumps(fmt)
    assert call["betas"] == [FALLBACK_BETA] and call["fallbacks"] == "default"
    content = call["messages"][0]["content"]
    assert [m["role"] for m in call["messages"]] == ["user"]
    assert content[0] == {"type": "document",
                          "source": {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjc="}}
    assert content[1] == {"type": "text", "text": "[notes.md]\n# Notes\nOhm"}
    assert content[2]["type"] == "image" and content[2]["source"]["media_type"] == "image/png"
    assert content[2]["cache_control"] == {"type": "ephemeral"}  # attachments shared by every scene
    assert content[3] == {"type": "text", "text": "Summarise", "cache_control": {"type": "ephemeral"}}
    assert breakpoints(call) == 3
    assert usages == [Usage(provider="anthropic", model="claude-opus-5-5", operation="vision", input_tokens=3940,
                            output_tokens=20, meta={"cache_read_tokens": 3000, "cache_write_tokens": 900})]
    # 40 uncached at $4 + 900 written at 1.25x + 3000 read at the documented $0.20/M; 20 out at $20/M.
    expected = (40 * 4.0 + 900 * 5.0 + 3000 * 0.20 + 20 * 20.0) / 1_000_000
    assert estimate_cost(usages[0], settings) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_reask_reuses_first_turn_and_ends_with_user(settings) -> None:
    llm, client = make_llm(settings, [claude_message('{"title": ""}'), claude_message()])
    out = await llm.generate_json(model="claude-opus-5-5", system="", prompt="p", schema=Summary)
    assert out.bullets == ["V = IR"]
    first, second = client.calls
    assert "system" not in first  # empty system prompts are not sent
    assert second["messages"][0] == first["messages"][0]  # identical cached prefix
    assert [m["role"] for m in second["messages"]] == ["user", "assistant", "user"]
    assert second["messages"][1]["content"] == '{"title": ""}'
    assert "bullets" in second["messages"][2]["content"]
    assert breakpoints(second) == 1


def test_trailing_assistant_turn_is_never_sent(settings) -> None:
    llm, _ = make_llm(settings, [])
    kwargs = llm._request(model="claude-opus-5-5", system="s", content=[{"type": "text", "text": "p"}],
                          followups=[Turn("model", ""), Turn("user", "fix"), Turn("model", "{}")],
                          max_output_tokens=None, json_schema=None)
    assert [m["role"] for m in kwargs["messages"]] == ["user", "assistant", "user"]
    assert kwargs["messages"][1]["content"] == "(empty response)"  # empty text blocks are rejected


@pytest.mark.parametrize(
    ("model", "effort", "expected"),
    [
        ("claude-opus-5-5", "high", "high"),
        ("claude-opus-5-5", "max", "max"),
        ("claude-fable-5-1", "xhigh", "xhigh"),
        ("claude-sonnet-5-5", "low", "low"),
        ("claude-opus-4-7", "xhigh", "xhigh"),
        ("claude-opus-4-6", "xhigh", "high"),
        ("claude-sonnet-4-6", "xhigh", "high"),
        ("claude-opus-4-5", "max", "high"),
        ("claude-opus-4-5-20251101", "medium", "medium"),
        ("claude-mythos-5-1", "max", "max"),
        ("claude-haiku-4-5", "high", None),
        ("claude-sonnet-4-5", "high", None),
        ("claude-3-7-sonnet-latest", "high", None),
        ("claude-opus-4-1", "high", None),
    ],
)
def test_effort_gating(model: str, effort: str, expected: str | None) -> None:
    assert effort_for(model, effort) == expected


@pytest.mark.asyncio
async def test_effort_and_fallbacks_omitted_where_unsupported(make_settings) -> None:
    s = make_settings(anthropic_api_key=ANTHROPIC_KEY, anthropic_effort="xhigh")
    llm, client = make_llm(s, [claude_message(model="claude-haiku-4-5"), claude_message(model="claude-sonnet-4-6")])
    await llm.generate_json(model="claude-haiku-4-5", system="s", prompt="p", schema=Summary)
    assert set(client.calls[0]["output_config"]) == {"format"}
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]
    await llm.generate_text(model="claude-sonnet-4-6", system="s", prompt="p")
    assert client.calls[1]["output_config"] == {"effort": "high"}  # xhigh is not available on Sonnet 4.6


@pytest.mark.parametrize(
    ("model", "expected"),
    [("claude-opus-5-5", True), ("claude-opus-5", True), ("claude-fable-5-1", True), ("claude-fable-5", True),
     ("claude-sonnet-5-5", True), ("claude-sonnet-5", False), ("claude-opus-4-8", False), ("claude-haiku-4-5", False)],
)
def test_fallback_gating(model: str, expected: bool) -> None:
    assert supports_fallbacks(model) is expected


@pytest.mark.asyncio
async def test_fallbacks_can_be_disabled(make_settings) -> None:
    s = make_settings(anthropic_api_key=ANTHROPIC_KEY, anthropic_refusal_fallback=False)
    llm, client = make_llm(s, [claude_message()])
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert "betas" not in client.calls[0] and "fallbacks" not in client.calls[0]


@pytest.mark.asyncio
async def test_fallback_rejection_retries_without_and_is_remembered(settings) -> None:
    err = claude_error(anthropic.BadRequestError, 400, "fallbacks: this model does not support fallback models")
    llm, client = make_llm(settings, [err, claude_message(), claude_message()])
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert client.calls[0]["fallbacks"] == "default"
    assert "fallbacks" not in client.calls[1] and "betas" not in client.calls[1]
    assert client.calls[1]["output_config"]["format"]  # only the rejected feature is dropped
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert len(client.calls) == 3 and "fallbacks" not in client.calls[2]


@pytest.mark.asyncio
async def test_schema_rejection_switches_to_prompt_mode_once(settings, caplog) -> None:
    err = claude_error(anthropic.BadRequestError, 400,
                       f"output_config.format.schema: Schema is too complex for compilation (key {ANTHROPIC_KEY})")
    llm, client = make_llm(settings, [err, claude_message(), claude_message()])
    with caplog.at_level(logging.WARNING, logger="aadhi.providers.llm.anthropic"):
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="Summarise", schema=Summary)
    retry = client.calls[1]
    assert retry["output_config"] == {"effort": "high"}
    prompt = retry["messages"][0]["content"][-1]
    assert prompt["cache_control"] == {"type": "ephemeral"}
    head, schema_text = prompt["text"].split(JSON_PROMPT)
    assert head == "Summarise\n\n"
    assert json.loads(schema_text)["additionalProperties"] is False and ": " not in schema_text  # compact
    assert "prompt" in caplog.text and ANTHROPIC_KEY not in caplog.text
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="Summarise", schema=Summary)
    assert len(client.calls) == 3 and "format" not in client.calls[2]["output_config"]  # straight to prompt mode


@pytest.mark.asyncio
async def test_prompt_mode_is_not_remembered_when_it_fails_too(settings) -> None:
    schema_err = claude_error(anthropic.BadRequestError, 400, "output_config.format: invalid schema")
    image_err = claude_error(anthropic.BadRequestError, 400, "messages.0.content.0: invalid image")
    llm, client = make_llm(settings, [schema_err, image_err])
    with pytest.raises(ProviderError):
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert len(client.calls) == 2 and "format" not in client.calls[1]["output_config"]
    assert not llm._prompt_mode  # the next call tries structured outputs again


@pytest.mark.asyncio
async def test_unrelated_bad_request_is_not_downgraded(settings) -> None:
    err = claude_error(anthropic.BadRequestError, 400, "output_config.effort: 'xhigh' is not supported on this model")
    llm, client = make_llm(settings, [err])
    with pytest.raises(ProviderError) as info:
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert info.value.status == 400 and "api.anthropic.com" in str(info.value)
    assert len(client.calls) == 1 and not llm._prompt_mode


# --- responses --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refusal_raises_content_blocked_after_usage(settings) -> None:
    refusal = claude_message('{"title": "partial', stop="refusal", category="bio", input_tokens=12)
    llm, client = make_llm(settings, [refusal])
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked, match="refused: bio"):
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary,
                                on_usage=usages.append)
    assert len(client.calls) == 1 and usages[0].input_tokens == 12


@pytest.mark.asyncio
async def test_max_tokens_stop_is_reasked_as_truncated(settings) -> None:
    llm, client = make_llm(settings, [claude_message('{"title": "Ohm', stop="max_tokens"), claude_message()])
    assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert "cut off at the length limit" in client.calls[1]["messages"][2]["content"]


@pytest.mark.asyncio
async def test_text_skips_thinking_and_fallback_blocks_and_reports_served_model(settings) -> None:
    blocks = [SimpleNamespace(type="thinking", thinking="", signature="x"), SimpleNamespace(type="text", text="Hello "),
              SimpleNamespace(type="fallback"), SimpleNamespace(type="text", text="world")]
    llm, client = make_llm(settings, [claude_message(blocks=blocks, model="claude-opus-4-8"),
                                      claude_message(blocks=[])])
    usages: list[Usage] = []
    text = await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p", temperature=0.9,
                                   max_output_tokens=200_000, on_usage=usages.append)
    assert text == "Hello world"
    assert client.calls[0]["max_tokens"] == 128_000 and "temperature" not in client.calls[0]
    assert "output_config" in client.calls[0] and "format" not in client.calls[0]["output_config"]
    assert usages[0].model == "claude-opus-4-8" and usages[0].meta == {"requested_model": "claude-opus-5-5"}
    with pytest.raises(ProviderError, match="no text"):
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p")


# --- refusal fallbacks: every billed attempt is costed (usage.iterations) --------------------------


@pytest.mark.asyncio
async def test_declined_attempt_before_a_fallback_is_reported_at_its_own_price(settings) -> None:
    # Opus 5.5 declines mid-stream after 8k output tokens; the server re-runs the request on Opus 5.
    # The top-level usage covers only the serving attempt (the API contract for fallbacks).
    iterations = [hop("message", "claude-opus-5-5", input_tokens=150_000, output_tokens=8_000),
                  hop("fallback_message", "claude-opus-5", input_tokens=150_000, output_tokens=6_000)]
    message = claude_message(model="claude-opus-5", input_tokens=150_000, output_tokens=6_000, iterations=iterations)
    llm, _ = make_llm(settings, [message])
    usages: list[Usage] = []
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert usages == [
        Usage(provider="anthropic", model="claude-opus-5-5", operation="llm", input_tokens=150_000,
              output_tokens=8_000, meta={"declined": True}),
        Usage(provider="anthropic", model="claude-opus-5", operation="llm", input_tokens=150_000, output_tokens=6_000,
              meta={"requested_model": "claude-opus-5-5"}),
    ]
    total = sum(estimate_cost(u, settings) for u in usages)
    assert total == pytest.approx((150_000 * 4 + 8_000 * 20 + 150_000 * 5 + 6_000 * 25) / 1_000_000)  # ~$1.66


@pytest.mark.asyncio
async def test_a_chain_that_refuses_reports_every_attempt_before_content_blocked(settings) -> None:
    iterations = [hop("message", None, input_tokens=900, output_tokens=40, cache_write=100),
                  hop("message", "claude-opus-4-8", input_tokens=1_000, output_tokens=0, cache_read=500)]
    refusal = claude_message(stop="refusal", category="cyber", model="claude-opus-4-8", input_tokens=1_000,
                             output_tokens=0, cache_read=500, iterations=iterations)
    llm, _ = make_llm(settings, [refusal])
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked, match="refused: cyber"):
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p", on_usage=usages.append)
    assert [(u.model, u.input_tokens, u.output_tokens) for u in usages] == [
        ("claude-opus-5-5", 1_000, 40),  # no model on the entry: the requested model declined first
        ("claude-opus-4-8", 1_500, 0),
    ]
    assert usages[0].meta == {"cache_write_tokens": 100, "declined": True}
    assert usages[1].meta == {"cache_read_tokens": 500, "requested_model": "claude-opus-5-5"}


@pytest.mark.asyncio
async def test_a_single_iteration_is_counted_once(settings) -> None:
    single = [hop("message", "claude-opus-5-5", input_tokens=50, output_tokens=20)]
    sticky = [hop("fallback_message", "claude-opus-5", input_tokens=50, output_tokens=20)]  # served by the fallback
    llm, _ = make_llm(settings, [claude_message(iterations=single),
                                 claude_message(model="claude-opus-5", iterations=sticky)])
    usages: list[Usage] = []
    await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p", on_usage=usages.append)
    await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p", on_usage=usages.append)
    assert usages == [
        Usage(provider="anthropic", model="claude-opus-5-5", operation="llm", input_tokens=50, output_tokens=20),
        Usage(provider="anthropic", model="claude-opus-5", operation="llm", input_tokens=50, output_tokens=20,
              meta={"requested_model": "claude-opus-5-5"}),
    ]


# --- retries and errors -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_and_overload_are_retried(settings) -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    script = [
        claude_error(anthropic.RateLimitError, 429, "slow down", kind="rate_limit_error", headers={"retry-after": "7"}),
        claude_error(anthropic.OverloadedError, 529, "Overloaded", kind="overloaded_error"),
        claude_error(anthropic.InternalServerError, 500, "boom", kind="api_error"),
        claude_message(),
    ]
    llm, client = make_llm(settings, script, sleep=record)
    assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert len(client.calls) == 4 and len(waits) == 3 and waits[0] >= 7  # Retry-After honoured


@pytest.mark.asyncio
async def test_mid_stream_overload_is_retried(settings) -> None:
    response = httpx2.Response(200, request=httpx2.Request("POST", _URL))  # error event after HTTP 200
    err = anthropic.APIStatusError("Overloaded", response=response,
                                   body={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    llm, client = make_llm(settings, [err, claude_message()])
    assert (await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)).title
    assert len(client.calls) == 2


@pytest.mark.parametrize(
    ("cls", "status", "kind", "retry"),
    [
        (anthropic.BadRequestError, 400, "invalid_request_error", False),
        (anthropic.AuthenticationError, 401, "authentication_error", False),
        (anthropic.PermissionDeniedError, 403, "permission_error", False),
        (anthropic.NotFoundError, 404, "not_found_error", False),
        (anthropic.RequestTooLargeError, 413, "request_too_large", False),
        (anthropic.UnprocessableEntityError, 422, "invalid_request_error", False),
        (anthropic.APIStatusError, 408, "timeout_error", True),
        (anthropic.ConflictError, 409, "invalid_request_error", True),
        (anthropic.InternalServerError, 500, "api_error", True),
        (anthropic.ServiceUnavailableError, 503, "api_error", True),
        (anthropic.DeadlineExceededError, 504, "timeout_error", True),
        (anthropic.OverloadedError, 529, "overloaded_error", True),
    ],
)
def test_error_classification(cls: type, status: int, kind: str, retry: bool) -> None:
    from aadhi.providers.anthropic_common import classify_anthropic_error

    info = classify_anthropic_error(claude_error(cls, status, "x", kind=kind))
    assert info.retry is retry and info.status == status and not info.rate_limited
    limited = claude_error(anthropic.RateLimitError, 429, "x", kind="rate_limit_error", headers={"retry-after": "75"})
    assert classify_anthropic_error(limited) == ErrorInfo(retry=True, rate_limited=True, status=429, retry_after=75.0)
    request = httpx2.Request("POST", _URL)
    assert classify_anthropic_error(anthropic.APITimeoutError(request=request)).retry
    assert classify_anthropic_error(anthropic.APIConnectionError(request=request)).retry


@pytest.mark.parametrize(
    ("exc", "retry"),
    [
        (httpx2.RemoteProtocolError("peer closed connection without sending complete message body"), True),
        (httpx2.ReadError("connection reset"), True),
        (httpx2.ReadTimeout("read timed out"), True),
        (httpx2.ProxyError("proxy refused"), False),  # configuration: retrying cannot help
        (httpx2.UnsupportedProtocol("ftp"), False),
    ],
)
def test_raw_transport_errors_from_a_stream_are_classified(exc: Exception, retry: bool) -> None:
    from aadhi.providers.anthropic_common import classify_anthropic_error

    assert classify_anthropic_error(exc).retry is retry


def test_mid_stream_read_timeout_is_reported_as_a_timeout(settings) -> None:
    from aadhi.providers.anthropic_common import anthropic_error

    assert "timed out" in str(anthropic_error(httpx2.ReadTimeout("read timed out"), settings, "text generation"))
    assert "failed" in str(anthropic_error(httpx2.ProxyError("proxy refused"), settings, "text generation"))


@pytest.mark.asyncio
async def test_rate_limit_exhausted(settings) -> None:
    errs = [claude_error(anthropic.RateLimitError, 429, "slow down", kind="rate_limit_error") for _ in range(4)]
    llm, client = make_llm(settings, errs)
    with pytest.raises(RateLimited):
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert len(client.calls) == 4


@pytest.mark.asyncio
async def test_auth_error_not_retried_and_redacted(settings) -> None:
    err = claude_error(anthropic.AuthenticationError, 401, f"invalid x-api-key {ANTHROPIC_KEY}",
                       kind="authentication_error")
    llm, client = make_llm(settings, [err])
    with pytest.raises(ProviderError) as info:
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p")
    assert info.value.status == 401 and len(client.calls) == 1
    assert ANTHROPIC_KEY not in str(info.value) and "[REDACTED]" in str(info.value)


@pytest.mark.asyncio
async def test_content_filtering_error_is_content_blocked(settings) -> None:
    err = claude_error(anthropic.BadRequestError, 400, "Output blocked by content filtering policy")
    llm, _ = make_llm(settings, [err])
    with pytest.raises(ContentBlocked):
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p")


@pytest.mark.asyncio
async def test_unsupported_and_oversized_files_fail_before_any_request(settings, monkeypatch) -> None:
    llm, client = make_llm(settings, [])
    with pytest.raises(ProviderError, match="unsupported file type"):
        await llm.generate_text(model="claude-opus-5-5", system="s", prompt="p",
                                files=[FileInput(data=b"PK", mime="application/zip")])
    monkeypatch.setattr(anthropic_llm, "MAX_REQUEST_BYTES", 100)
    with pytest.raises(ProviderError, match="32 MB"):
        await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary,
                                files=[FileInput(data=b"%PDF" * 30, mime="application/pdf")])
    assert client.calls == []


@pytest.mark.asyncio
async def test_claude_calls_use_their_own_timeout(make_settings) -> None:
    s = make_settings(anthropic_api_key=ANTHROPIC_KEY, llm_timeout_seconds=600)
    assert s.anthropic_timeout_seconds == 1800  # a high-effort 64K answer can stream for over ten minutes
    llm = AnthropicLLM(s)
    assert llm._caller.timeout_s == 1800
    client = await llm._caller.client()  # the real SDK client (no request is made): same per-request timeout,
    # except that a streamed answer silent for LLM_IDLE_TIMEOUT_SECONDS (not even a ping) is a stall
    assert client.timeout == anthropic.Timeout(1800, read=240) and client.max_retries == 0
    await client.close()
    custom = AnthropicLLM(make_settings(anthropic_api_key=ANTHROPIC_KEY, anthropic_timeout_seconds=120,
                                        llm_timeout_seconds=5000))
    assert custom._caller.timeout_s == 120  # LLM_TIMEOUT_SECONDS does not apply to Claude


def test_requires_key(make_settings) -> None:
    with pytest.raises(ProviderNotConfigured, match="ANTHROPIC_API_KEY is not configured"):
        AnthropicLLM(make_settings(llm_provider="anthropic", anthropic_api_key=""))


def test_resolve_model_and_max_tokens(make_settings) -> None:
    s = make_settings(anthropic_api_key=ANTHROPIC_KEY, anthropic_model_plan="claude-opus-5-5",
                      anthropic_model_fast="claude-sonnet-5-5", anthropic_max_tokens=32000)
    assert resolve_model("gemini-2.5-pro", s) == "claude-opus-5-5"
    assert resolve_model("gpt-4.1-mini", s) == "claude-sonnet-5-5"
    assert resolve_model("claude-haiku-4-5", s) == "claude-haiku-4-5"
    assert max_tokens_for(s, None) == 32000
    assert max_tokens_for(s, 50000) == 50000
    assert max_tokens_for(s, 500_000) == 128_000


# --- the real SDK over a mock transport (request body / headers / SSE parsing; no network) ----------


def _sse(*events: dict[str, Any]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _sse_message(text: str) -> bytes:
    usage = {"input_tokens": 10, "output_tokens": 1, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100}
    return _sse(
        {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
                                              "model": "claude-opus-5-5", "content": [], "stop_reason": None,
                                              "stop_sequence": None, "usage": usage}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    )


@pytest.mark.asyncio
async def test_real_sdk_request_body_and_stream_error_retry(settings) -> None:
    requests: list[httpx2.Request] = []
    bodies = [
        _sse({"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
        _sse_message(GOOD),
    ]

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=bodies.pop(0))

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, max_retries=0,
                                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = AnthropicLLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    out = await llm.generate_json(model="claude-opus-5-5", system="Teach", prompt="Summarise", schema=Summary,
                                  temperature=0.2, on_usage=usages.append)
    assert out.title == "Ohm's law" and len(requests) == 2
    body = json.loads(requests[1].content)
    assert body["stream"] is True and not set(_SAMPLING) & set(body)
    assert body["fallbacks"] == "default" and requests[1].headers["anthropic-beta"] == FALLBACK_BETA
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert usages[-1].input_tokens == 115 and usages[-1].meta == {"cache_read_tokens": 100, "cache_write_tokens": 5}


@pytest.mark.asyncio
async def test_real_sdk_connection_dropped_mid_stream_is_retried(settings) -> None:
    """A dropped connection while the SSE body is read surfaces as a raw httpx2 error, not an
    ``APIConnectionError``: it must still be retried."""
    requests: list[httpx2.Request] = []
    partial = _sse_message(GOOD).split(b"event: content_block_stop")[0]  # message_start + a text delta

    async def dropped():
        yield partial
        raise httpx2.RemoteProtocolError("peer closed connection without sending complete message body")

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        content = dropped() if len(requests) == 1 else _sse_message(GOOD)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=content)

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, max_retries=0,
                                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    llm = AnthropicLLM(settings, client_factory=lambda: client, sleep=record)
    out = await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary)
    assert out.title == "Ohm's law" and len(requests) == 2 and len(waits) == 1


@pytest.mark.asyncio
async def test_real_sdk_reports_fallback_iterations_from_the_stream(settings) -> None:
    """``usage.iterations`` arrives in ``message_delta`` and survives the SDK's stream accumulation."""
    iterations = [
        {"type": "message", "model": "claude-opus-5-5", "input_tokens": 10, "output_tokens": 7,
         "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100},
        {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 10, "output_tokens": 20,
         "cache_creation_input_tokens": 5, "cache_read_input_tokens": 100},
    ]
    delta_usage = json.dumps({"output_tokens": 20, "iterations": iterations}).encode()
    body = (_sse_message(GOOD)
            .replace(b'"model": "claude-opus-5-5"', b'"model": "claude-opus-5"')
            .replace(b'"usage": {"output_tokens": 20}', b'"usage": ' + delta_usage))

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_KEY, max_retries=0,
                                      http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    llm = AnthropicLLM(settings, client_factory=lambda: client, sleep=no_sleep)
    usages: list[Usage] = []
    await llm.generate_json(model="claude-opus-5-5", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert [(u.model, u.input_tokens, u.output_tokens, u.meta.get("declined")) for u in usages] == [
        ("claude-opus-5-5", 115, 7, True), ("claude-opus-5", 115, 20, None)]
