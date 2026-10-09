"""Manim template library: typed, tested, parameterised scenes (preferred over free-form code).

    registry: dict[str, Template]
    list_templates() -> list[TemplateInfo]
    get_template(name) -> Template                       (KeyError for unknown names)
    validate_params(name, params) -> (model | None, problems)

Each template advances exactly one step per narration beat (``step_count(params) == len(beats)``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from ..base import Template, TemplateInfo
from .bar_compare import BarCompareTemplate
from .block_diagram import BlockDiagramTemplate
from .circuit_basic import CircuitBasicTemplate
from .equation_steps import EquationStepsTemplate
from .function_plot import FunctionPlotTemplate
from .geometry import GeometryTemplate
from .graph_traversal import GraphTraversalTemplate
from .matrix_ops import MatrixOpsTemplate
from .timeline_steps import TimelineStepsTemplate
from .truth_table import TruthTableTemplate
from .vector_forces import VectorForcesTemplate
from .wave import WaveTemplate

__all__ = ["registry", "list_templates", "get_template", "validate_params", "format_validation_error"]

registry: dict[str, Template] = {
    t.name: t
    for t in (
        EquationStepsTemplate(),
        FunctionPlotTemplate(),
        VectorForcesTemplate(),
        BlockDiagramTemplate(),
        CircuitBasicTemplate(),
        BarCompareTemplate(),
        TimelineStepsTemplate(),
        GeometryTemplate(),
        MatrixOpsTemplate(),
        GraphTraversalTemplate(),
        WaveTemplate(),
        TruthTableTemplate(),
    )
}


def list_templates() -> list[TemplateInfo]:
    """Descriptions of every template (for planner/script prompts and the API)."""
    return [t.info() for t in registry.values()]


def get_template(name: str) -> Template:
    """Template by name; raises ``KeyError`` with the available names."""
    try:
        return registry[name]
    except KeyError:
        raise KeyError(f"unknown manim template {name!r} (available: {', '.join(sorted(registry))})") from None


def format_validation_error(exc: ValidationError) -> list[str]:
    """Pydantic errors as short ``path: message`` strings suitable for an LLM re-ask."""
    problems = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        problems.append(f"{loc}: {msg}" if loc else msg)
    return problems


def validate_params(name: str, params: dict[str, Any] | BaseModel) -> tuple[BaseModel | None, list[str]]:
    """Validate ``params`` for template ``name``: ``(model, [])`` or ``(None, problems)``."""
    template = registry.get(name)
    if template is None:
        return None, [f"unknown manim template {name!r} (available: {', '.join(sorted(registry))})"]
    if isinstance(params, BaseModel):
        params = params.model_dump(mode="json")
    if not isinstance(params, dict):
        return None, ["params must be an object"]
    try:
        model = template.params_model.model_validate(params)
    except ValidationError as exc:
        return None, [f"{name}: {p}" for p in format_validation_error(exc)]
    return model, []
