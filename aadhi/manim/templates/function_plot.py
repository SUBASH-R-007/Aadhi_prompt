"""function_plot: axes + functions of x, with a dot / tangent / area / guide line per step.

Expressions are validated with :mod:`aadhi.manim.safe_expr` (AST allow-list) and sampled on the
host; the scene only receives numbers.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator, model_validator

from ..safe_expr import ExpressionError, derivative, evaluate, parse_expr, sample
from ._base import ColorName, Finite, NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator

SAMPLES = 240
X_LIMIT = 1000.0


class PlotFunction(TemplateParams):
    expr: str = Field(min_length=1, max_length=200, description="expression in x, e.g. 0.5*x^2 - 2, sin(x), exp(-x)")
    label_latex: str = Field(default="", max_length=80, description="LaTeX label such as f(x)=x^2 ('' = none)")
    color: ColorName = "cyan"

    @field_validator("expr")
    @classmethod
    def _expr(cls, v: str) -> str:
        try:
            parse_expr(v)
        except ExpressionError as exc:
            raise ValueError(str(exc)) from None
        return v

    _tex = field_validator("label_latex")(tex_validator)


class PlotStep(TemplateParams):
    action: Literal["show_function", "dot", "tangent", "area", "vline", "note"] = Field(
        description="show_function: draw functions[function_index]; dot: place/move a dot to x on the curve; "
        "tangent: tangent line at x; area: shade under the curve from x to x_end; vline: dashed line at x; "
        "note: caption only"
    )
    function_index: int = Field(default=0, ge=0, le=2)
    x: Finite = 0.0
    x_end: Finite = 0.0
    note: NoteText = ""


class FunctionPlotParams(TemplateParams):
    title: TitleText = ""
    x_min: Finite = Field(default=-5.0, ge=-X_LIMIT, le=X_LIMIT)
    x_max: Finite = Field(default=5.0, ge=-X_LIMIT, le=X_LIMIT)
    y_min: Finite = Field(default=0.0, description="y_min = y_max = 0 means automatic")
    y_max: Finite = 0.0
    x_label: str = Field(default="x", max_length=12)
    y_label: str = Field(default="y", max_length=12)
    functions: list[PlotFunction] = Field(min_length=1, max_length=3)
    steps: list[PlotStep] = Field(min_length=1, max_length=10, description="one step per beat")

    @model_validator(mode="after")
    def _semantics(self) -> FunctionPlotParams:
        if self.x_min >= self.x_max:
            raise ValueError("x_min must be smaller than x_max")
        auto_y = self.y_min == 0 and self.y_max == 0
        if not auto_y and self.y_min >= self.y_max:
            raise ValueError("y_min must be smaller than y_max (or set both to 0 for automatic)")
        for i, fn in enumerate(self.functions):
            _xs, ys = sample(fn.expr, self.x_min, self.x_max, SAMPLES)
            if np.count_nonzero(np.isfinite(ys)) < SAMPLES // 2:
                raise ValueError(f"functions[{i}] ({fn.expr}) is undefined on most of [x_min, x_max]")
        for i, step in enumerate(self.steps):
            if step.action == "note":
                continue
            if step.function_index >= len(self.functions):
                raise ValueError(f"steps[{i}].function_index {step.function_index} has no function")
            if step.action == "show_function":
                continue
            if not self.x_min <= step.x <= self.x_max:
                raise ValueError(f"steps[{i}].x must lie within [x_min, x_max]")
            expr = self.functions[step.function_index].expr
            if step.action in ("dot", "tangent") and not np.isfinite(evaluate(expr, step.x)[0]):
                raise ValueError(f"steps[{i}]: the function is undefined at x={step.x:g}")
            if step.action == "tangent" and not math.isfinite(derivative(expr, step.x)):
                raise ValueError(f"steps[{i}]: the function has no tangent at x={step.x:g}")
            if step.action == "area" and not step.x < step.x_end <= self.x_max:
                raise ValueError(f"steps[{i}]: area needs x < x_end <= x_max")
        return self


def nice_step(span: float, target_ticks: int = 8) -> float:
    """A 1/2/5 x 10^k tick step giving roughly ``target_ticks`` ticks over ``span``."""
    raw = max(span, 1e-9) / target_ticks
    power = 10 ** math.floor(math.log10(raw))
    for mult in (1, 2, 5, 10):
        if mult * power >= raw:
            return float(mult * power)
    return float(10 * power)  # pragma: no cover


def _clean(values: np.ndarray) -> list[float | None]:
    return [round(float(v), 5) if np.isfinite(v) else None for v in values]


class FunctionPlotTemplate(SceneTemplate):
    name = "function_plot"
    title = "Function plot"
    description = (
        "Plot up to three functions of x on labelled axes and build understanding step by step: draw a "
        "curve, move a dot along it, show a tangent (slope), shade an area (integral) or mark x with a "
        "dashed line. Use for graphs in calculus, physics (s-t, v-t), economics curves."
    )
    steps_hint = "one step per item of `steps`; each step does exactly one action"
    params_model = FunctionPlotParams
    scene_file = "function_plot.py"
    example_params = {
        "title": "Slope of a curve",
        "x_min": -3,
        "x_max": 3,
        "y_min": 0,
        "y_max": 0,
        "x_label": "x",
        "y_label": "y",
        "functions": [{"expr": "x^2", "label_latex": "y = x^2", "color": "cyan"}],
        "steps": [
            {"action": "show_function", "function_index": 0, "note": "The parabola y = x squared"},
            {"action": "dot", "function_index": 0, "x": -2, "note": "Pick a point on the curve"},
            {"action": "tangent", "function_index": 0, "x": -2, "note": "Its tangent has slope -4"},
            {"action": "dot", "function_index": 0, "x": 1, "note": "Move the point to x = 1"},
            {"action": "tangent", "function_index": 0, "x": 1, "note": "Now the slope is 2"},
            {"action": "area", "function_index": 0, "x": 0, "x_end": 2, "note": "Area under the curve from 0 to 2"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)

    def scene_data(self, params: FunctionPlotParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        curves = []
        finite_all: list[np.ndarray] = []
        for fn in params.functions:
            xs, ys = sample(fn.expr, params.x_min, params.x_max, SAMPLES)
            finite_all.append(ys[np.isfinite(ys)])
            curves.append({"xs": _clean(xs), "ys": ys})
        if params.y_min == 0 and params.y_max == 0:
            pool = np.concatenate(finite_all) if finite_all else np.array([0.0])
            lo, hi = (float(v) for v in np.percentile(pool, [2, 98])) if pool.size else (0.0, 1.0)
            if 0 < lo < (hi - lo) * 0.5:  # include the x-axis when it is close
                lo = 0.0
            if 0 > hi > -(hi - lo) * 0.5:
                hi = 0.0
            if hi - lo < 1e-6:
                lo, hi = lo - 1.0, hi + 1.0
            pad = (hi - lo) * 0.1
            y_min, y_max = lo - pad, hi + pad
        else:
            y_min, y_max = params.y_min, params.y_max
        span_y = y_max - y_min
        for curve in curves:
            ys = curve["ys"]
            outside = (ys < y_min - span_y * 0.05) | (ys > y_max + span_y * 0.05)
            ys = np.where(outside, np.nan, ys)
            curve["ys"] = _clean(ys)
        data["curves"] = curves
        data["y_min"], data["y_max"] = round(y_min, 5), round(y_max, 5)
        data["x_step"] = nice_step(params.x_max - params.x_min)
        data["y_step"] = nice_step(y_max - y_min, 6)
        base = min(max(0.0, y_min), y_max)
        details = []
        for step in params.steps:
            info: dict[str, Any] = {}
            if step.action not in ("note", "show_function"):
                expr = params.functions[step.function_index].expr
                y0 = float(evaluate(expr, step.x)[0])
                info["y"] = round(y0, 5) if math.isfinite(y0) else None
                if step.action == "dot":
                    slope = derivative(expr, step.x)
                    info["slope"] = round(slope, 5) if math.isfinite(slope) else 0.0
                if step.action == "tangent":
                    slope = derivative(expr, step.x)
                    dx = (params.x_max - params.x_min) * 0.2
                    if abs(slope) > 1e-9:
                        dx = min(dx, span_y * 0.35 / abs(slope))
                    info["slope"] = round(slope, 5)
                    info["p1"] = [round(step.x - dx, 5), round(y0 - slope * dx, 5)]
                    info["p2"] = [round(step.x + dx, 5), round(y0 + slope * dx, 5)]
                if step.action == "area":
                    xs = np.linspace(step.x, step.x_end, 80)
                    ys = np.clip(evaluate(expr, xs), y_min, y_max)
                    ys = np.where(np.isfinite(ys), ys, base)
                    info["polygon"] = (
                        [[round(step.x, 5), base]]
                        + [[round(float(a), 5), round(float(b), 5)] for a, b in zip(xs, ys)]
                        + [[round(step.x_end, 5), base]]
                    )
            details.append(info)
        data["details"] = details
        return data
