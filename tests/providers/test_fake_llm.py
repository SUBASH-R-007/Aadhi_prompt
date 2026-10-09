"""FakeLLM + the shared structured-output loop (parse / validate / semantic hook / re-ask)."""

from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import BaseModel, Field, ValidationError

from aadhi.providers.base import ImageInput, ProviderError, Usage
from aadhi.providers.llm.fake import FakeLLM, current_attempt
from aadhi.providers.llm.structured import (
    Completion,
    format_validation_error,
    parse_json_text,
    reask_message,
    run_structured,
    strip_code_fences,
)


class Answer(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    points: list[str] = Field(min_length=1, max_length=5)
    score: int = Field(ge=0, le=10)


class Concepts(BaseModel):
    ids: list[str] = Field(min_length=1)


# --- structured helpers ------------------------------------------------------------------


def test_strip_fences_and_parse() -> None:
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences("```\n{}\n```") == "{}"
    assert parse_json_text('Here you go:\n{"a": [1, 2]}\nThanks!') == {"a": [1, 2]}
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(ValueError, match="not valid JSON"):
        parse_json_text('{"a": ')
    with pytest.raises(ValueError, match="empty"):
        parse_json_text("   ")


def test_format_validation_error_paths() -> None:
    with pytest.raises(ValidationError) as info:
        Answer.model_validate({"title": "", "points": [1], "score": 99})
    lines = format_validation_error(info.value)
    assert any(line.startswith("title:") for line in lines)
    assert any(line.startswith("points[0]:") for line in lines)
    assert any(line.startswith("score:") for line in lines)
    assert "- a problem" in reask_message(["a problem"])


@pytest.mark.asyncio
async def test_run_structured_reasks_with_previous_output_and_problems() -> None:
    seen: list[list] = []
    outputs = ['{"title": "x"}', '{"title": "x", "points": ["p"], "score": 3}']

    async def complete(followups):
        seen.append(list(followups))
        return Completion(text=outputs[len(seen) - 1], usage=Usage("fake", "m", "llm", 1, 1))

    usages: list[Usage] = []
    obj = await run_structured(complete=complete, schema=Answer, validate=None, validation_retries=2,
                               on_usage=usages.append, provider="fake", settings=None)
    assert obj.score == 3
    assert len(usages) == 2
    assert seen[0] == []
    model_turn, user_turn = seen[1]
    assert model_turn.role == "model" and model_turn.text == outputs[0]
    assert user_turn.role == "user" and "points" in user_turn.text and "score" in user_turn.text


@pytest.mark.asyncio
async def test_run_structured_truncation_hint_and_exhaustion() -> None:
    async def complete(_followups):
        return Completion(text='{"title": "cut', truncated=True)

    with pytest.raises(ProviderError) as info:
        await run_structured(complete=complete, schema=Answer, validate=None, validation_retries=1,
                             on_usage=None, provider="fake", settings=None)
    assert "after 2 attempt" in str(info.value)
    assert info.value.provider == "fake"


@pytest.mark.asyncio
async def test_async_usage_sink_and_budget_error_propagates() -> None:
    from aadhi.jobs.base import BudgetExceeded

    llm = FakeLLM()

    def sink(_usage: Usage) -> None:
        raise BudgetExceeded("over budget")

    with pytest.raises(BudgetExceeded):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Answer, on_usage=sink)

    recorded: list[Usage] = []

    async def async_sink(usage: Usage) -> None:
        await asyncio.sleep(0)
        recorded.append(usage)

    await llm.generate_json(model="m", system="s", prompt="p", schema=Answer, on_usage=async_sink)
    assert recorded and recorded[0].provider == "fake"


# --- FakeLLM ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesised_output_is_valid_and_deterministic() -> None:
    llm = FakeLLM()
    usages: list[Usage] = []
    a = await llm.generate_json(model="fake-pro", system="sys", prompt="Explain Ohm's law", schema=Answer,
                                on_usage=usages.append)
    b = await llm.generate_json(model="fake-pro", system="sys", prompt="Explain Ohm's law", schema=Answer)
    assert a == b
    assert 1 <= len(a.points) <= 5 and 0 <= a.score <= 10
    assert usages[0].provider == "fake" and usages[0].model == "fake-pro" and usages[0].operation == "llm"
    assert usages[0].input_tokens > 0 and usages[0].output_tokens > 0


@pytest.mark.asyncio
async def test_lecture_plan_synthesis() -> None:
    from aadhi.pipeline.base import LecturePlan

    plan = await FakeLLM().generate_json(model="m", system="s", prompt="plan", schema=LecturePlan)
    assert isinstance(plan, LecturePlan)


@pytest.mark.asyncio
async def test_validate_hook_walks_through_richer_candidates() -> None:
    from aadhi.pipeline.base import LecturePlan

    def needs_scenes(plan: LecturePlan) -> list[str]:
        return [] if plan.all_scenes() else ["the plan must contain at least one scene"]

    llm = FakeLLM()
    plan = await llm.generate_json(model="m", system="s", prompt="plan", schema=LecturePlan, validate=needs_scenes)
    assert plan.all_scenes()
    attempts = [c["attempt"] for c in llm.calls]
    assert attempts == [0, 1]


@pytest.mark.asyncio
async def test_registered_responder_dict_and_model() -> None:
    FakeLLM.register("Answer", lambda prompt, schema: {"title": prompt[:10], "points": ["a"], "score": 7})
    llm = FakeLLM()
    out = await llm.generate_json(model="m", system="s", prompt="Ohm's law intro", schema=Answer)
    assert out.title == "Ohm's law " and out.score == 7

    FakeLLM.register("Answer", lambda prompt, schema: schema(title="model", points=["x"], score=1))
    out = await llm.generate_json(model="m", system="s", prompt="p", schema=Answer)
    assert out.title == "model"
    assert "Answer" in FakeLLM.registered()
    FakeLLM.unregister("Answer")
    assert "Answer" not in FakeLLM.registered()


@pytest.mark.asyncio
async def test_responder_gets_attempt_and_problems_on_reask() -> None:
    calls: list[tuple[int, list[str], int]] = []

    def responder(prompt: str, schema: type[BaseModel], attempt: int, problems: list[str]):
        calls.append((attempt, problems, current_attempt()))
        if attempt == 0:
            return {"ids": ["unknown-concept"]}
        return {"ids": ["ohm"]}

    FakeLLM.register("Concepts", responder)

    def validate(obj: Concepts) -> list[str]:
        return [f"unknown concept id {i}" for i in obj.ids if i != "ohm"]

    out = await FakeLLM().generate_json(model="m", system="s", prompt="p", schema=Concepts, validate=validate)
    assert out.ids == ["ohm"]
    assert calls[0] == (0, [], 0)
    assert calls[1][0] == 1 and calls[1][1] == ["unknown concept id unknown-concept"] and calls[1][2] == 1


@pytest.mark.asyncio
async def test_invalid_scripted_output_raises_after_retries() -> None:
    FakeLLM.register("Answer", lambda prompt, schema: "not json at all")
    llm = FakeLLM()
    usages: list[Usage] = []
    with pytest.raises(ProviderError, match="after 3 attempt"):
        await llm.generate_json(model="m", system="s", prompt="p", schema=Answer, on_usage=usages.append,
                                validation_retries=2)
    assert len(usages) == 3


@pytest.mark.asyncio
async def test_async_responder_and_kwargs_passthrough() -> None:
    async def responder(prompt, schema, **kwargs):
        assert {"attempt", "problems", "system", "model", "images", "files"} <= set(kwargs)
        return {"title": kwargs["model"], "points": [str(len(kwargs["images"]))], "score": 0}

    FakeLLM.register("Answer", responder)
    out = await FakeLLM().generate_json(model="vision-x", system="s", prompt="p", schema=Answer,
                                        images=[ImageInput(data=b"\x89PNG", mime="image/png")])
    assert out.title == "vision-x" and out.points == ["1"]


@pytest.mark.asyncio
async def test_vision_usage_operation() -> None:
    usages: list[Usage] = []
    await FakeLLM().generate_json(model="m", system="s", prompt="p", schema=Answer,
                                  images=[ImageInput(data=b"img")], on_usage=usages.append)
    assert usages[0].operation == "vision"


@pytest.mark.asyncio
async def test_incompatible_schema_rejected_like_real_providers() -> None:
    from aadhi.providers.llm.schema import LLMSchemaError
    from aadhi.schemas.screenplay import Screenplay

    with pytest.raises(LLMSchemaError):
        await FakeLLM().generate_json(model="m", system="s", prompt="p", schema=Screenplay)


@pytest.mark.asyncio
async def test_generate_text_deterministic_and_scriptable() -> None:
    llm = FakeLLM()
    usages: list[Usage] = []
    a = await llm.generate_text(model="m", system="sys", prompt="Summarise the lecture please", on_usage=usages.append)
    b = await llm.generate_text(model="m", system="sys", prompt="Summarise the lecture please")
    assert a == b and a.startswith("Fake response") and "Summarise" in a
    assert usages[0].provider == "fake"
    FakeLLM.register_text(lambda prompt, system: f"[{system}] {prompt.upper()}")
    assert await llm.generate_text(model="m", system="S", prompt="hi") == "[S] HI"
    FakeLLM.register_text(None)
    assert (await llm.generate_text(model="m", system="S", prompt="hi")).startswith("Fake response")


def test_registry_is_class_level() -> None:
    FakeLLM.register("X", lambda p, s: {})
    assert "X" in FakeLLM().registered()
    FakeLLM.clear_registry()
    assert FakeLLM.registered() == []


def test_calls_recorded_json_serialisable() -> None:
    llm = FakeLLM()
    asyncio.run(llm.generate_json(model="m", system="s", prompt="p", schema=Answer))
    assert json.dumps(list(llm.calls))



@pytest.mark.asyncio
async def test_call_records_are_clipped_and_bounded() -> None:
    import hashlib

    from aadhi.providers.llm import fake as fake_mod

    llm = FakeLLM()
    big = "source text " * 50_000  # ~600k characters, like a pipeline prompt with source chunks
    await llm.generate_text(model="m", system="s" * 5000, prompt=big)
    record = llm.calls[-1]
    assert len(record["prompt"]) <= fake_mod.MAX_RECORDED_CHARS + 3 and len(record["system"]) <= 2003
    assert record["prompt_chars"] == len(big)
    assert record["prompt_sha256"] == hashlib.sha256(big.encode()).hexdigest()[:16]
    for _ in range(fake_mod.MAX_CALL_RECORDS + 20):
        await llm.generate_text(model="m", system="s", prompt="short")
    assert len(llm.calls) == fake_mod.MAX_CALL_RECORDS
    assert llm.calls[-1]["prompt"] == "short" and llm.calls[-1]["kind"] == "text"
