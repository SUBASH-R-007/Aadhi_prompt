"""llm.synth: schema-valid instance synthesis for FakeLLM."""

from __future__ import annotations

import re
from typing import Literal

import pytest
from pydantic import BaseModel, Field, model_validator

from aadhi.providers.llm.synth import candidates, example_for_pattern, synthesize


class Distractor(BaseModel):
    text: str = Field(min_length=3, max_length=40)
    why_wrong: str = Field(min_length=1)


class Quiz(BaseModel):
    question: str = Field(min_length=10, max_length=300)
    correct: str
    distractors: list[Distractor] = Field(min_length=2, max_length=4)
    bloom: Literal["remember", "understand", "apply"]
    seconds: int = Field(ge=3, le=30)
    weight: float = Field(gt=0.5, le=2.0)
    source_refs: list[str] = Field(default_factory=list)
    hint: str | None = None


class Graph(BaseModel):
    expr: str | None = None
    points: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _non_empty(self) -> Graph:
        if not self.expr and not self.points:
            raise ValueError("needs expr or points")
        return self


class Options(BaseModel):
    options: list[str] = Field(min_length=3, max_length=5)
    chunk_ids: list[str] = Field(default_factory=list)
    code: str = Field(default="", pattern=r"^[a-z][a-z0-9_]{2,10}$")

    @model_validator(mode="after")
    def _distinct(self) -> Options:
        if len(set(self.options)) != len(self.options):
            raise ValueError("options must be distinct")
        return self


@pytest.mark.parametrize(
    ("pattern", "check"),
    [
        (r"^c\d{4,5}$", "c0001"),
        (r"^[A-Za-z0-9_\-]{1,64}$", None),
        (r"^[+-]\d{1,2}%$", None),
        (r"^#[0-9a-fA-F]{6}$", None),
        (r"^(foo|bar)-\d+$", "foo-1"),
        (r"^[a-z0-9][a-z0-9_\-]{0,63}$", None),
        (r"^\w+@\w+\.com$", None),
        (r"[^abc]{3}", None),
    ],
)
def test_example_for_pattern(pattern: str, check: str | None) -> None:
    value = example_for_pattern(pattern)
    assert value is not None and re.search(pattern, value)
    if check is not None:
        assert value == check


def test_pattern_examples_are_distinct_per_index() -> None:
    values = [example_for_pattern(r"^c\d{4,5}$", index=i) for i in range(5)]
    assert values == ["c0001", "c0002", "c0003", "c0004", "c0005"]


def test_pattern_respects_lengths() -> None:
    value = example_for_pattern(r"^[a-z]+$", min_length=5)
    assert value is not None and len(value) >= 5
    assert example_for_pattern(r"^[a-z]{10}$", max_length=3) is None
    assert example_for_pattern(r"(unclosed") is None


def test_minimal_respects_constraints() -> None:
    data = synthesize(Quiz, "minimal")
    obj = Quiz.model_validate(data)
    assert len(obj.distractors) == 2
    assert obj.bloom == "remember"
    assert obj.seconds == 3
    assert 0.5 < obj.weight <= 2.0
    assert obj.hint is None and "hint" not in data
    assert len(obj.question) >= 10


def test_rich_fills_everything() -> None:
    data = synthesize(Quiz, "rich")
    obj = Quiz.model_validate(data)
    assert obj.hint and obj.source_refs == ["c0001"]


def test_candidates_skip_invalid_levels() -> None:
    options = candidates(Graph)  # minimal ({}) violates the validator; filled/rich don't
    assert options and all(Graph.model_validate(o) for o in options)
    assert options[0] != {}


def test_list_items_distinct_and_patterns() -> None:
    obj = Options.model_validate(candidates(Options)[0])
    assert len(set(obj.options)) == 3
    rich = Options.model_validate(synthesize(Options, "rich"))
    assert re.match(r"^[a-z][a-z0-9_]{2,10}$", rich.code)
    assert rich.chunk_ids == ["chunk-1"]


def test_lecture_plan_candidates_validate_and_cross_reference() -> None:
    from aadhi.pipeline.base import LecturePlan

    options = candidates(LecturePlan)
    assert len(options) >= 2
    plans = [LecturePlan.model_validate(o) for o in options]
    rich = plans[-1]
    assert rich.chapters and rich.chapters[0].scenes
    scene = rich.chapters[0].scenes[0]
    assert scene.objective_ids == [rich.learning_objectives[0].id]
    assert scene.misconception_ids == [rich.misconceptions[0].id]
    assert scene.source_refs == ["c0001"]


def test_pipeline_models_synthesise() -> None:
    from aadhi.pipeline.base import GenerationOptions, Issue, PlannedScene, SourceChunk

    for model in (GenerationOptions, PlannedScene, SourceChunk, Issue):
        options = candidates(model)
        assert options
        for option in options:
            model.model_validate(option)


def test_field_validator_repair() -> None:
    from aadhi.pipeline.base import Issue

    # Issue.code is checked by a field validator (not a schema pattern): the repair pass finds a shape
    issue = Issue.model_validate(candidates(Issue)[0])
    assert "." in issue.code


def test_unsatisfiable_model_falls_back_to_minimal() -> None:
    class Impossible(BaseModel):
        a: int = 0

        @model_validator(mode="after")
        def _never(self) -> "Impossible":
            raise ValueError("never valid")

    assert candidates(Impossible) == [{}]


def test_pipeline_gen_models_are_llm_compatible_and_synthesisable() -> None:
    """Contract check against the pipeline's LLM response models (skipped if not installed)."""
    import inspect

    from google.genai import types as gtypes

    from aadhi.providers.llm.schema import assert_llm_compatible, to_provider_schema

    gen_models = pytest.importorskip("aadhi.pipeline.gen_models")
    models = [obj for obj in vars(gen_models).values()
              if inspect.isclass(obj) and issubclass(obj, BaseModel) and obj.__module__ == gen_models.__name__]
    assert models
    for model in models:
        assert_llm_compatible(model)
        schema = to_provider_schema(model, "gemini")
        gtypes.Schema.from_json_schema(json_schema=gtypes.JSONSchema.model_validate(schema),
                                       raise_error_on_unsupported_field=True)
        for option in candidates(model):
            model.model_validate(option)


def test_pipeline_fake_content_registers_on_fake_llm() -> None:
    from aadhi.providers.llm.fake import FakeLLM

    fake_content = pytest.importorskip("aadhi.pipeline.fake_content")
    fake_content.register_fake_responders()
    assert FakeLLM.registered()
