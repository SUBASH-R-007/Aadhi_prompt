"""GeminiLLM with a mocked google-genai client: success, re-ask, 429 rotation, blocks, redaction."""

from __future__ import annotations

import json

import pytest
from google.genai import types as gtypes
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
from aadhi.providers.llm import gemini as gemini_llm
from aadhi.providers.llm.gemini import GeminiLLM

from .conftest import GEMINI_KEYS, no_sleep
from .fakes import gemini_error, gemini_factory, gemini_prompt_blocked, gemini_text_response


class Summary(BaseModel):
    title: str = Field(min_length=1)
    bullets: list[str] = Field(min_length=1, max_length=3)


GOOD = json.dumps({"title": "Ohm's law", "bullets": ["V = IR"]})


def make_llm(settings, scripts, **kw):
    factory, clients = gemini_factory(scripts)
    return GeminiLLM(settings, client_factory=factory, sleep=no_sleep, **kw), clients


@pytest.mark.asyncio
async def test_success_request_shape_and_usage(gemini_settings) -> None:
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("```json\n" + GOOD + "\n```")]})
    usages: list[Usage] = []
    out = await llm.generate_json(
        model="gemini-2.5-flash", system="You are a teacher.", prompt="Summarise", schema=Summary,
        files=[FileInput(data=b"%PDF-1.7 small", mime="application/pdf", name="notes.pdf")],
        images=[ImageInput(data=b"\x89PNG...", mime="image/png")], temperature=0.2, max_output_tokens=2048,
        on_usage=usages.append,
    )
    assert out.title == "Ohm's law"
    call = clients[GEMINI_KEYS[0]].models.calls[0]
    assert call["model"] == "gemini-2.5-flash"
    cfg: gtypes.GenerateContentConfig = call["config"]
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_json_schema["properties"]["bullets"]["maxItems"] == 3
    assert cfg.system_instruction == "You are a teacher."
    assert cfg.temperature == 0.2 and cfg.max_output_tokens == 2048
    parts = call["contents"][0].parts
    assert parts[0].inline_data.mime_type == "application/pdf"
    assert parts[1].inline_data.mime_type == "image/png"
    assert parts[-1].text == "Summarise"
    assert usages == [Usage(provider="gemini", model="gemini-2.5-flash", operation="vision", input_tokens=100,
                            output_tokens=50, meta={"thoughts_tokens": 10, "cached_tokens": 0})]


@pytest.mark.asyncio
async def test_invalid_then_valid_reask(gemini_settings) -> None:
    bad = json.dumps({"title": "", "bullets": []})
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response(bad), gemini_text_response(GOOD)]})
    usages: list[Usage] = []
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.bullets == ["V = IR"]
    assert len(usages) == 2
    second = clients[GEMINI_KEYS[0]].models.calls[1]["contents"]
    assert [c.role for c in second] == ["user", "model", "user"]
    assert second[1].parts[0].text == bad
    assert "title" in second[2].parts[0].text and "bullets" in second[2].parts[0].text


@pytest.mark.asyncio
async def test_semantic_validator_problems_are_fed_back(gemini_settings) -> None:
    other = json.dumps({"title": "Kirchhoff", "bullets": ["KCL"]})
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response(other), gemini_text_response(GOOD)]})
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Summary,
                                  validate=lambda o: [] if "Ohm" in o.title else ["title must mention Ohm"])
    assert out.title == "Ohm's law"
    assert "title must mention Ohm" in clients[GEMINI_KEYS[0]].models.calls[1]["contents"][2].parts[0].text


@pytest.mark.asyncio
async def test_validation_exhaustion_raises(gemini_settings) -> None:
    llm, _ = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("{}")] * 2})
    with pytest.raises(ProviderError, match="after 2 attempt"):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, validation_retries=1)


@pytest.mark.asyncio
async def test_429_rotates_to_next_key(gemini_settings) -> None:
    limited = gemini_error(429, "Resource exhausted", "RESOURCE_EXHAUSTED", retry_delay="7s")
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [limited], GEMINI_KEYS[1]: [gemini_text_response(GOOD)]})
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert out.title == "Ohm's law"
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 1
    assert len(clients[GEMINI_KEYS[1]].models.calls) == 1
    # the limited key cools down: the next request starts on key 2
    clients[GEMINI_KEYS[1]].models.script.append(gemini_text_response(GOOD))
    await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 1


@pytest.mark.asyncio
async def test_all_keys_limited_raises_rate_limited(gemini_settings) -> None:
    scripts = {k: [gemini_error(429, "quota", "RESOURCE_EXHAUSTED") for _ in range(5)] for k in GEMINI_KEYS}
    llm, clients = make_llm(gemini_settings, scripts, max_attempts=4)
    with pytest.raises(RateLimited) as info:
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert info.value.status == 429 and info.value.provider == "gemini"
    assert sum(len(c.models.calls) for c in clients.values()) == 4


@pytest.mark.asyncio
async def test_5xx_and_timeouts_are_retried(gemini_settings) -> None:
    import httpx

    script = [gemini_error(503, "overloaded", "UNAVAILABLE"), httpx.ReadTimeout("slow"), gemini_text_response(GOOD)]
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: script})
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert out.title == "Ohm's law"
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 3


@pytest.mark.asyncio
async def test_400_is_not_retried(gemini_settings) -> None:
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_error(400, "Invalid schema", "INVALID_ARGUMENT")]})
    with pytest.raises(ProviderError) as info:
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert not isinstance(info.value, (RateLimited, ContentBlocked))
    assert info.value.status == 400
    assert "generativelanguage.googleapis.com" in str(info.value)
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 1


@pytest.mark.asyncio
async def test_safety_blocks(gemini_settings) -> None:
    llm, _ = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_prompt_blocked()]})
    with pytest.raises(ContentBlocked):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    llm, _ = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("", finish="SAFETY")]})
    with pytest.raises(ContentBlocked):
        await llm.generate_text(model="m", system="s", prompt="p")


@pytest.mark.asyncio
async def test_error_messages_are_redacted(gemini_settings) -> None:
    secret = GEMINI_KEYS[0]
    err = gemini_error(400, f"API key {secret} not valid for https://x/?key={secret}", "INVALID_ARGUMENT")
    llm, _ = make_llm(gemini_settings, {secret: [err]})
    with pytest.raises(ProviderError) as info:
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    text = str(info.value)
    assert secret not in text and "[REDACTED]" in text
    assert info.value.__cause__ is None  # the raw SDK exception (with the key) is not chained


@pytest.mark.asyncio
async def test_truncated_output_reasks_with_hint(gemini_settings) -> None:
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response('{"title": "Oh', finish="MAX_TOKENS"),
                                                               gemini_text_response(GOOD)]})
    await llm.generate_json(model="m", system="s", prompt="p", schema=Summary)
    assert "cut off" in clients[GEMINI_KEYS[0]].models.calls[1]["contents"][2].parts[0].text


@pytest.mark.asyncio
async def test_large_files_use_files_api_once_per_key(gemini_settings, monkeypatch) -> None:
    monkeypatch.setattr(gemini_llm, "INLINE_LIMIT_BYTES", 10)
    big = FileInput(data=b"%PDF" + b"x" * 100, mime="application/pdf", name="big.pdf")
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response(GOOD), gemini_text_response(GOOD)]})
    llm._poll_interval_s = 0
    await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, files=[big])
    await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, files=[big])
    files = clients[GEMINI_KEYS[0]].aio.files
    assert len(files.uploads) == 1 and files.uploads[0]["mime"] == "application/pdf"
    assert files.gets == 1  # waited for PROCESSING -> ACTIVE
    part = clients[GEMINI_KEYS[0]].models.calls[0]["contents"][0].parts[0]
    assert part.file_data.file_uri.startswith("https://files.example/")


@pytest.mark.asyncio
async def test_generate_text(gemini_settings) -> None:
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("Hello class")]})
    usages: list[Usage] = []
    assert await llm.generate_text(model="m", system="s", prompt="p", on_usage=usages.append) == "Hello class"
    cfg = clients[GEMINI_KEYS[0]].models.calls[0]["config"]
    assert cfg.response_json_schema is None and cfg.response_mime_type is None
    assert usages[0].operation == "llm"


def test_requires_keys(make_settings) -> None:
    with pytest.raises(ProviderNotConfigured):
        GeminiLLM(make_settings(llm_provider="gemini"))


@pytest.mark.asyncio
async def test_blocked_response_still_reports_usage(gemini_settings) -> None:
    blocked = gemini_text_response("", finish="SAFETY", prompt_tokens=33, out_tokens=0, thoughts=0)
    llm, _ = make_llm(gemini_settings, {GEMINI_KEYS[0]: [blocked]})
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked, match="finish reason safety"):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert [u.input_tokens for u in usages] == [33]


# --- recitation ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recitation_is_reasked_not_blocked(gemini_settings) -> None:
    recited = gemini_text_response('{"title": "Ohm', finish="RECITATION", prompt_tokens=70)
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [recited, gemini_text_response(GOOD)]})
    usages: list[Usage] = []
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, on_usage=usages.append)
    assert out.title == "Ohm's law" and [u.input_tokens for u in usages] == [70, 100]
    reask = clients[GEMINI_KEYS[0]].models.calls[1]["contents"]
    assert [c.role for c in reask] == ["user", "model", "user"]
    assert "own words" in reask[2].parts[0].text


@pytest.mark.asyncio
async def test_recitation_on_every_attempt_raises_content_blocked(gemini_settings) -> None:
    recited = [gemini_text_response("", finish="RECITATION") for _ in range(3)]
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: recited})
    usages: list[Usage] = []
    with pytest.raises(ContentBlocked, match="verbatim"):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Summary, validation_retries=2,
                                on_usage=usages.append)
    assert len(clients[GEMINI_KEYS[0]].models.calls) == 3 and len(usages) == 3


@pytest.mark.asyncio
async def test_generate_text_recitation_reask(gemini_settings) -> None:
    script = [gemini_text_response("Ohm's law states", finish="RECITATION"), gemini_text_response("In my words")]
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: script})
    assert await llm.generate_text(model="m", system="s", prompt="p") == "In my words"
    second = clients[GEMINI_KEYS[0]].models.calls[1]["contents"]
    assert second[1].parts[0].text == "Ohm's law states" and "own words" in second[2].parts[0].text

    script = [gemini_text_response("", finish="RECITATION") for _ in range(2)]
    llm, _ = make_llm(gemini_settings, {GEMINI_KEYS[0]: script})
    with pytest.raises(ContentBlocked):
        await llm.generate_text(model="m", system="s", prompt="p")


def test_block_reason_variants() -> None:
    from aadhi.providers.gemini_common import block_reason, is_recitation

    recited = gemini_text_response("", finish="RECITATION")
    assert block_reason(recited) is None and is_recitation(recited)
    assert block_reason(recited, include_recitation=True) == "finish reason recitation"
    assert block_reason(gemini_text_response("", finish="SAFETY")) == "finish reason safety"
    assert block_reason(gemini_prompt_blocked()) == "prompt safety"
    assert block_reason(gemini_text_response("ok")) is None and not is_recitation(gemini_text_response("ok"))


# --- inline budget ------------------------------------------------------------------------------


def test_plan_uploads_counts_base64_images_and_text() -> None:
    from aadhi.providers.llm.gemini import base64_size, plan_uploads

    assert base64_size(0) == 0 and base64_size(1) == 4 and base64_size(3) == 4 and base64_size(4) == 8
    mb = 1_000_000
    assert plan_uploads([5 * mb, 1 * mb], fixed_bytes=10_000) == set()  # ~8 MB encoded: fits
    # an 18 MB PDF alone is ~24 MB once base64 encoded: it must go through the Files API
    assert plan_uploads([18 * mb], fixed_bytes=0) == {0}
    # a 13 MB PDF fits alone (~17.3 MB) but not together with 2 MB of slide images: the PDF moves
    assert plan_uploads([13 * mb], fixed_bytes=0) == set()
    assert plan_uploads([13 * mb, 1 * mb, 1 * mb], fixed_bytes=0) == {0}
    # the prompt counts too
    assert plan_uploads([13 * mb], fixed_bytes=2 * mb) == {0}
    # several images that together exceed the budget: largest first, until the rest fits
    assert plan_uploads([6 * mb, 7 * mb, 5 * mb], fixed_bytes=0, budget=16 * mb) == {1}
    assert plan_uploads([6 * mb, 7 * mb, 5 * mb], fixed_bytes=0, budget=12 * mb) == {0, 1}
    assert plan_uploads([], fixed_bytes=50 * mb) == set()


@pytest.mark.asyncio
async def test_file_near_limit_plus_image_uploads_the_file(gemini_settings, monkeypatch) -> None:
    monkeypatch.setattr(gemini_llm, "INLINE_LIMIT_BYTES", 2000)
    pdf = FileInput(data=b"%PDF" + b"x" * 1200, mime="application/pdf", name="notes.pdf")  # 1204 B -> 1608 encoded
    image = ImageInput(data=b"\x89PNG" + b"y" * 300, mime="image/png")
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("fine")]})
    llm._poll_interval_s = 0
    assert await llm.generate_text(model="m", system="s", prompt="p", files=[pdf], images=[image]) == "fine"
    parts = clients[GEMINI_KEYS[0]].models.calls[0]["contents"][0].parts
    assert parts[0].file_data.file_uri.startswith("https://files.example/")  # the PDF went to the Files API
    assert parts[1].inline_data.mime_type == "image/png" and parts[2].text == "p"
    assert len(clients[GEMINI_KEYS[0]].aio.files.uploads) == 1


@pytest.mark.asyncio
async def test_small_inputs_stay_inline(gemini_settings) -> None:
    pdf = FileInput(data=b"%PDF small", mime="application/pdf")
    llm, clients = make_llm(gemini_settings, {GEMINI_KEYS[0]: [gemini_text_response("fine")]})
    await llm.generate_text(model="m", system="s", prompt="p", files=[pdf], images=[ImageInput(data=b"\x89PNG")])
    assert clients[GEMINI_KEYS[0]].aio.files.uploads == []


def test_upload_locks_of_closed_loops_are_dropped(gemini_settings) -> None:
    import asyncio
    import gc
    import weakref

    llm, _ = make_llm(gemini_settings, {})
    loops: list[weakref.ref] = []

    async def job() -> None:
        loops.append(weakref.ref(asyncio.get_running_loop()))
        lock = llm._lock_for(("k", "digest"))
        async with lock:  # binds the lock to this loop
            await asyncio.sleep(0)

    asyncio.run(job())
    asyncio.run(job())
    assert len(llm._upload_locks) == 1  # only the second (now closed) loop; the first was purged
    gc.collect()
    assert loops[0]() is None  # the first job's loop is no longer pinned
