"""Shared building blocks for Manim templates.

A template = an LLM-compatible params model (lists of small objects; no tuples, open dicts or
non-null unions) + a static scene file in ``templates/scenes/`` that reads a ``PARAMS`` literal.
``render_source`` emits ``PARAMS = <repr of validated, JSON-safe data>`` followed by the scene file,
so model output only ever reaches the sandbox as *data* (and the whole source still passes the
AST guard as defence in depth).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..base import TemplateInfo
from ..texcheck import check_tex

SCENES_DIR = Path(__file__).resolve().parent / "scenes"

ColorName = Literal["gold", "purple", "cyan", "white", "muted", "green", "red", "orange", "pink", "blue"]
Finite = Annotated[float, Field(allow_inf_nan=False)]
TitleText = Annotated[str, Field(max_length=60, description="optional heading shown at the top ('' = none)")]
NoteText = Annotated[str, Field(max_length=110, description="short caption shown at the bottom during this step")]


def tex_validator(value: str) -> str:
    """Field validator: reuse the screenplay forbidden-macro check plus Manim's stricter TeX rules."""
    if value:
        check_tex(value)
    return value


class TemplateParams(BaseModel):
    """Base for params models: strict keys (typos are reported back to the model), trimmed strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@lru_cache(maxsize=32)
def scene_file_source(filename: str) -> str:
    """Text of ``templates/scenes/<filename>``."""
    return (SCENES_DIR / filename).read_text(encoding="utf-8")


class SceneTemplate:
    """Base class implementing the ``aadhi.manim.base.Template`` protocol."""

    name: ClassVar[str]
    title: ClassVar[str]
    description: ClassVar[str]
    steps_hint: ClassVar[str]
    params_model: ClassVar[type[TemplateParams]]
    example_params: ClassVar[dict[str, Any]]
    scene_file: ClassVar[str]

    def step_count(self, params: BaseModel) -> int:  # pragma: no cover - overridden
        """Number of animation steps (== number of beats the scene must have)."""
        raise NotImplementedError

    def coerce(self, params: BaseModel | dict[str, Any]) -> TemplateParams:
        """Validated params model from a model instance or a raw dict."""
        if isinstance(params, self.params_model):
            return params
        if isinstance(params, BaseModel):
            params = params.model_dump(mode="json")
        return self.params_model.model_validate(params)

    def scene_data(self, params: TemplateParams, *, target: str, language: str) -> dict[str, Any]:
        """JSON-safe data embedded as ``PARAMS`` (templates may precompute layout/samples here)."""
        return params.model_dump(mode="json")

    def info(self) -> TemplateInfo:
        """Planner/script-facing description of the template."""
        return TemplateInfo(
            name=self.name,
            title=self.title,
            description=self.description,
            params_schema=self.params_model.model_json_schema(),
            example_params=self.example_params,
            steps_hint=self.steps_hint,
        )

    def render_source(self, params: BaseModel, *, target: str, language: str) -> str:
        """Scene source: ``PARAMS = {...}`` + the template's static scene class."""
        model = self.coerce(params)
        data = self.scene_data(model, target=target, language=language)
        return f"PARAMS = {data!r}\n\n" + scene_file_source(self.scene_file)
