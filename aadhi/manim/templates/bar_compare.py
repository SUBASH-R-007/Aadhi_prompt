"""bar_compare: animated bar chart comparing quantities, bars revealed step by step."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from ._base import ColorName, Finite, NoteText, SceneTemplate, TemplateParams, TitleText


class Bar(TemplateParams):
    label: str = Field(min_length=1, max_length=20, description="category name under the bar")
    value: Finite = Field(ge=-1e12, le=1e12)
    value_text: str = Field(default="", max_length=16, description="text shown on the bar ('' = the number + unit)")
    color: ColorName = "cyan"
    step: int = Field(default=0, ge=0, le=9, description="step (beat index) at which the bar grows")
    note: NoteText = ""


class BarCompareParams(TemplateParams):
    title: TitleText = ""
    unit: str = Field(default="", max_length=12, description="unit appended to values, e.g. %, kg, ms")
    y_label: str = Field(default="", max_length=30)
    bars: list[Bar] = Field(min_length=1, max_length=10)
    highlight_max: bool = Field(default=False, description="extra final step that highlights the largest bar")
    highlight_note: NoteText = ""

    @model_validator(mode="after")
    def _semantics(self) -> BarCompareParams:
        used = {b.step for b in self.bars}
        missing = sorted(set(range(max(used) + 1)) - used)
        if missing:
            raise ValueError(f"bar steps must be consecutive from 0: no bar at step(s) {missing}")
        if all(b.value == 0 for b in self.bars):
            raise ValueError("at least one bar needs a non-zero value")
        return self


def format_value(value: float, unit: str) -> str:
    """Compact human-readable number with an optional unit."""
    if abs(value) >= 1e9:
        text = f"{value / 1e9:.3g}B"
    elif abs(value) >= 1e6:
        text = f"{value / 1e6:.3g}M"
    elif abs(value) >= 1e4:
        text = f"{value / 1e3:.3g}k"
    else:
        text = f"{value:.4g}"
    return f"{text} {unit}".strip() if unit and unit != "%" else f"{text}{unit}"


class BarCompareTemplate(SceneTemplate):
    name = "bar_compare"
    title = "Bar comparison"
    description = (
        "Bar chart that grows bars one step at a time to compare quantities (e.g. resistivity of metals, "
        "algorithm running times, population, efficiency), with value labels and an optional final highlight of "
        "the largest bar. Use when the comparison itself is the point."
    )
    steps_hint = "steps = highest bar `step` + 1, plus one when highlight_max is true; several bars may share a step"
    params_model = BarCompareParams
    scene_file = "bar_compare.py"
    example_params = {
        "title": "Resistivity of common metals",
        "unit": "nΩ·m",
        "y_label": "Resistivity",
        "bars": [
            {"label": "Silver", "value": 15.9, "color": "cyan", "step": 0, "note": "Silver conducts best"},
            {"label": "Copper", "value": 16.8, "color": "gold", "step": 1, "note": "Copper is almost as good"},
            {"label": "Aluminium", "value": 26.5, "color": "pink", "step": 2, "note": "Aluminium is lighter but resists more"},
            {"label": "Iron", "value": 97.1, "color": "orange", "step": 3, "note": "Iron is a poor conductor"},
        ],
        "highlight_max": True,
        "highlight_note": "Higher resistivity means more opposition to current",
    }

    def step_count(self, params: BaseModel) -> int:
        model = self.coerce(params)
        return max(b.step for b in model.bars) + 1 + (1 if model.highlight_max else 0)

    def scene_data(self, params: BarCompareParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        for bar in data["bars"]:
            bar["display"] = bar["value_text"] or format_value(bar["value"], params.unit)
        values = [b.value for b in params.bars]
        data["v_max"] = max(max(values), 0.0)
        data["v_min"] = min(min(values), 0.0)
        data["max_index"] = max(range(len(values)), key=lambda i: values[i])
        data["bar_steps"] = max(b.step for b in params.bars) + 1
        return data
