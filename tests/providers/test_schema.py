"""llm.schema: pydantic -> provider JSON schema conversion and LLM-compatibility checks."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

import pytest
from google.genai import types as gtypes
from pydantic import AnyUrl, BaseModel, Field

from aadhi.providers.llm.schema import (
    LLMSchemaError,
    assert_llm_compatible,
    constraint_hint,
    llm_compatibility_problems,
    schema_name,
    to_provider_schema,
)


class Level(str, Enum):
    easy = "easy"
    hard = "hard"


class Step(BaseModel):
    """A worked step."""

    text: str = Field(min_length=1, max_length=200, description="what happens")
    why: Optional[str] = Field(default=None, max_length=300)


class Item(BaseModel):
    title: str = Field(default="untitled", title="Shown title")
    kind: Literal["bullet", "formula"] = "bullet"
    fixed: Literal["only"]
    level: Level
    maybe_level: Level | None = None
    steps: list[Step] = Field(default_factory=list, min_length=1, max_length=4)
    best: Step | None = Field(default=None, description="best step")
    count: int = Field(default=1, ge=1, le=9)
    ratio: float = Field(default=0.5, gt=0, lt=1)
    positive: int = Field(default=1, gt=0)
    chunk: str = Field(default="c0001", pattern=r"^c\d{4,5}$")
    tags: list[str] = Field(default_factory=list, examples=[["a"]])


class Wrapper(BaseModel):
    items: list[Item] = Field(min_length=1)
    note: str | None = None


class Bounds(BaseModel):
    """Every constraint keyword the converter handles."""

    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$", description="snake_case name")
    code: str = Field(min_length=3, max_length=3)
    count: int = Field(ge=1, le=6)
    step: int = Field(default=5, multiple_of=5, ge=0)
    ratio: float = Field(gt=0, lt=1)
    weight: float = Field(default=1.0, gt=0)
    tags: list[str] = Field(default_factory=list, min_length=1, max_length=8)
    unique: set[str] = Field(default_factory=set, max_length=3)
    when: datetime
    day: Optional[date] = None
    link: Optional[AnyUrl] = None
    mode: Literal["only"] = "only"
    note: Optional[str] = Field(default=None, max_length=300)


# Constraint keywords Anthropic structured outputs reject (restated in descriptions instead).
ANTHROPIC_STRIPPED = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength",
                      "maxLength", "pattern", "minItems", "maxItems", "uniqueItems"}


class WithUnion(BaseModel):
    value: Union[Step, Level]


class WithTuple(BaseModel):
    pair: tuple[int, int]


class WithDict(BaseModel):
    mapping: dict[str, int]


class WithOpenDict(BaseModel):
    params: dict[str, Any]


class WithAny(BaseModel):
    anything: Any


class A(BaseModel):
    kind: Literal["a"] = "a"
    x: int = 0


class B(BaseModel):
    kind: Literal["b"] = "b"
    y: int = 0


class WithDiscriminator(BaseModel):
    choice: Union[A, B] = Field(discriminator="kind")


class Node(BaseModel):
    name: str
    children: list[Node] = Field(default_factory=list)


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _schema_keywords(schema: dict) -> set[str]:
    """Every keyword used in schema nodes (excluding property names)."""
    found: set[str] = set()

    def visit(node: Any) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            found.add(key)
            if key == "properties":
                for sub in value.values():
                    visit(sub)
            elif key == "items":
                visit(value)
            elif key == "anyOf":
                for sub in value:
                    visit(sub)

    visit(schema)
    return found


@pytest.mark.parametrize("provider", ["gemini", "openai", "anthropic", "fake"])
def test_refs_inlined_and_noise_dropped(provider: str) -> None:
    schema = to_provider_schema(Wrapper, provider)
    text = json.dumps(schema)
    assert "$ref" not in text and "$defs" not in text
    keywords = _schema_keywords(schema)
    dropped_keys = ["title", "default", "examples", "const"]
    if provider != "fake":  # the fake dialect keeps exclusive bounds for the synthesiser
        dropped_keys += ["exclusiveMinimum", "exclusiveMaximum"]
    for dropped in dropped_keys:
        assert dropped not in keywords, dropped
    # property named "title" survives (only the keyword is dropped)
    item = schema["properties"]["items"]["items"]
    assert "title" in item["properties"]


def test_nullable_enum_literal_and_bounds() -> None:
    schema = to_provider_schema(Wrapper, "gemini")
    item = schema["properties"]["items"]["items"]
    props = item["properties"]
    assert props["kind"] == {"enum": ["bullet", "formula"], "type": "string"}
    assert props["fixed"] == {"enum": ["only"], "type": "string"}
    assert props["level"]["enum"] == ["easy", "hard"]
    assert props["maybe_level"]["anyOf"][1] == {"type": "null"}
    assert props["maybe_level"]["anyOf"][0]["enum"] == ["easy", "hard"]
    assert props["best"]["description"] == "best step"  # field description wins over the model docstring
    assert props["best"]["anyOf"][0]["properties"]["text"]["description"] == "what happens"
    assert props["steps"]["minItems"] == 1 and props["steps"]["maxItems"] == 4
    assert props["count"]["minimum"] == 1 and props["count"]["maximum"] == 9
    assert props["positive"]["minimum"] == 1  # exclusiveMinimum 0 on an integer -> minimum 1
    assert props["ratio"]["minimum"] == 0 and props["ratio"]["maximum"] == 1
    assert props["chunk"]["pattern"] == r"^c\d{4,5}$"
    assert set(item["required"]) == {"fixed", "level"}
    assert schema["required"] == ["items"]
    assert "additionalProperties" not in item


def test_openai_objects_are_closed() -> None:
    schema = to_provider_schema(Wrapper, "openai")
    objects = [n for n in _walk(schema) if isinstance(n, dict) and "properties" in n]
    assert objects and all(o.get("additionalProperties") is False for o in objects)


def test_gemini_schema_is_accepted_by_the_sdk_types() -> None:
    from aadhi.pipeline.base import LecturePlan

    for model in (Wrapper, LecturePlan):
        schema = to_provider_schema(model, "gemini")
        js = gtypes.JSONSchema.model_validate(schema)  # extra="forbid": unknown keywords would fail
        converted = gtypes.Schema.from_json_schema(json_schema=js, raise_error_on_unsupported_field=True)
        assert converted.type == gtypes.Type.OBJECT


def test_returns_fresh_copies() -> None:
    a = to_provider_schema(Wrapper, "gemini")
    a["properties"]["mutated"] = {"type": "string"}
    b = to_provider_schema(Wrapper, "gemini")
    assert "mutated" not in b["properties"]


def test_unknown_provider() -> None:
    with pytest.raises(ValueError):
        to_provider_schema(Wrapper, "anthropic-ish")


@pytest.mark.parametrize(
    ("model", "needle"),
    [
        (WithUnion, "unions"),
        (WithTuple, "tuples"),
        (WithDict, "open dicts"),
        (WithOpenDict, "open dicts"),
        (WithAny, "untyped"),
        (WithDiscriminator, "discriminated"),
        (Node, "recursive"),
    ],
)
def test_incompatible_models_rejected(model: type[BaseModel], needle: str) -> None:
    problems = llm_compatibility_problems(model)
    assert any(needle in p for p in problems), problems
    with pytest.raises(LLMSchemaError):
        assert_llm_compatible(model)
    with pytest.raises(LLMSchemaError):
        to_provider_schema(model, "gemini")


def test_screenplay_is_not_an_llm_schema_but_plan_is() -> None:
    from aadhi.pipeline.base import GenerationOptions, LecturePlan
    from aadhi.schemas.screenplay import Screenplay

    assert_llm_compatible(LecturePlan)
    assert_llm_compatible(GenerationOptions)
    with pytest.raises(LLMSchemaError):
        assert_llm_compatible(Screenplay)


def test_compatible_model_passes() -> None:
    assert llm_compatibility_problems(Wrapper) == []
    assert_llm_compatible(Step)


def test_schema_name_sanitised() -> None:
    weird = type("Weird Name!", (BaseModel,), {"__annotations__": {"x": int}})
    assert schema_name(weird) == "Weird_Name_"
    assert schema_name(Wrapper) == "Wrapper"


# sha256 of json.dumps(to_provider_schema(model, provider)) recorded before the Anthropic dialect was
# added: adding a provider must not change a single byte of the existing dialects.
_PRE_ANTHROPIC_DIGESTS = {
    ("gemini", "Wrapper"): "9dd5c0097f8062fa420ab608d85bf3c5da3927611aa3d80587d97323885c4639",
    ("gemini", "Bounds"): "cb62847e89de08e8c0b8000d2fcb0d200bf4058fc3adf1b1bb436c7981e3e4c0",
    ("openai", "Wrapper"): "1b52a5ffa40aae5028970689c3f87561b2fe41ea592086467e5ff9ca53fd600c",
    ("openai", "Bounds"): "323e3c3b8b701a8218ef08fe33097b873b7d30986d0e0f827e0008548b4c0b2f",
    ("fake", "Wrapper"): "e8ca68d3fc5b4c6e59ed29bff67637677531f2b34c91a5e27cbe390f66281bfb",
    ("fake", "Bounds"): "d0049e1d552664550a16bae0738b6c93df5690820d3452352bf630317618f50c",
}


@pytest.mark.parametrize(("provider", "model"), sorted(_PRE_ANTHROPIC_DIGESTS))
def test_existing_dialects_unchanged(provider: str, model: str) -> None:
    schema = to_provider_schema({"Wrapper": Wrapper, "Bounds": Bounds}[model], provider)
    digest = hashlib.sha256(json.dumps(schema).encode("utf-8")).hexdigest()
    assert digest == _PRE_ANTHROPIC_DIGESTS[(provider, model)]


def test_anthropic_constraints_become_description_hints() -> None:
    schema = to_provider_schema(Bounds, "anthropic")
    assert not _schema_keywords(schema) & ANTHROPIC_STRIPPED
    props = schema["properties"]
    assert props["name"]["description"] == (
        "snake_case name. 1 to 40 characters. Must match the regular expression ^[a-z_]+$."
    )
    assert props["code"]["description"] == "Exactly 3 characters."
    assert props["count"]["description"] == "Between 1 and 6."
    assert props["step"]["description"] == "At least 0. A multiple of 5."
    assert props["ratio"]["description"] == "Greater than 0 and less than 1."
    assert props["weight"]["description"] == "Greater than 0."
    assert props["tags"]["description"] == "1 to 8 items."
    assert props["unique"]["description"] == "At most 3 items. Items must be unique."
    assert props["note"]["anyOf"] == [{"type": "string", "description": "At most 300 characters."}, {"type": "null"}]
    assert props["link"]["anyOf"][0]["format"] == "uri"  # uri is an Anthropic format (dropped for OpenAI)
    assert props["when"] == {"format": "date-time", "type": "string"}
    assert props["mode"] == {"enum": ["only"], "type": "string"}
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["name", "code", "count", "ratio", "when"]

    wrapper = to_provider_schema(Wrapper, "anthropic")
    objects = [n for n in _walk(wrapper) if isinstance(n, dict) and "properties" in n]
    assert objects and all(o.get("additionalProperties") is False for o in objects)
    item = wrapper["properties"]["items"]["items"]["properties"]
    assert item["steps"]["description"] == "1 to 4 items."
    assert item["positive"]["description"] == "Greater than 0."  # exclusive bound stays exact (no shift)
    assert item["best"]["anyOf"][0]["properties"]["text"]["description"] == "what happens. 1 to 200 characters."


@pytest.mark.parametrize(
    ("node", "hint"),
    [
        ({}, ""),
        ({"type": "string", "minLength": 1}, "At least 1 character."),
        ({"type": "string", "maxLength": 1}, "At most 1 character."),
        ({"type": "integer", "maximum": 9}, "At most 9."),
        ({"type": "number", "minimum": 0.5, "exclusiveMaximum": 2.0}, "At least 0.5 and less than 2."),
        ({"type": "array", "minItems": 2, "maxItems": 2}, "Exactly 2 items."),
        ({"type": "array", "maxItems": 1, "uniqueItems": True}, "At most 1 item. Items must be unique."),
    ],
)
def test_constraint_hint(node: dict, hint: str) -> None:
    assert constraint_hint(node) == hint


def _pipeline_models() -> list[type[BaseModel]]:
    """Every response model the pipeline, manim QA and the eval judge send to an LLM."""
    from aadhi.evals.judge import JudgeVerdict
    from aadhi.manim.llm import FrameReview, ManimCodeFix
    from aadhi.pipeline import gen_models, integrations

    models: list[type[BaseModel]] = [*gen_models.ALL_STATIC_MODELS, gen_models.GenBrief]
    for t in integrations.list_templates():
        models += [gen_models.simulation_model(t.name), gen_models.board_scene_model(t.name)]
    return [*models, FrameReview, ManimCodeFix, JudgeVerdict]


def test_pipeline_models_convert_for_anthropic() -> None:
    hinted = 0
    for model in _pipeline_models():
        schema = to_provider_schema(model, "anthropic")
        text = json.dumps(schema)
        assert "$ref" not in text and "$defs" not in text, model.__name__
        assert not _schema_keywords(schema) & ANTHROPIC_STRIPPED, model.__name__
        objects = [n for n in _walk(schema) if isinstance(n, dict) and "properties" in n]
        assert all(o.get("additionalProperties") is False for o in objects), model.__name__
        hinted += sum(1 for n in _walk(schema) if isinstance(n, dict)
                      and str(n.get("description", "")).endswith(("characters.", "character.", "items.", "item.")))
    assert hinted > 0  # the stripped constraints are still visible to the model
