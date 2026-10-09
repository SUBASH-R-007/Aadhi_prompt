"""circuit_basic: a series or parallel DC circuit (battery + resistors) explained step by step."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ._base import NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator

MAX_EQUATIONS = 4


class Resistor(TemplateParams):
    label: str = Field(min_length=1, max_length=8, description="name, e.g. R1")
    value: str = Field(default="", max_length=16, description="value text, e.g. 4 Ω")


class CircuitStep(TemplateParams):
    action: Literal["draw", "source", "resistor", "current", "voltage_drop", "equation", "note"] = Field(
        description="draw: draw the circuit; source: highlight the battery and its value; resistor: highlight "
        "resistors[index] and its value; current: show current flowing (+ label `text`); voltage_drop: label the "
        "voltage across resistors[index] with `text`; equation: write `latex` beside the circuit; note: caption only"
    )
    index: int = Field(default=0, ge=0, le=3, description="resistor index for resistor / voltage_drop")
    text: str = Field(default="", max_length=24, description="label for current / voltage_drop, e.g. I = 3 A")
    latex: str = Field(default="", max_length=200, description="equation for action=equation, e.g. I = \\frac{V}{R}")
    note: NoteText = ""

    _tex = field_validator("latex")(tex_validator)


class CircuitBasicParams(TemplateParams):
    title: TitleText = ""
    topology: Literal["series", "parallel"] = "series"
    source_label: str = Field(default="V", max_length=12, description="battery name, e.g. V or E")
    source_value: str = Field(default="", max_length=16, description="battery value, e.g. 12 V")
    resistors: list[Resistor] = Field(min_length=1, max_length=4)
    steps: list[CircuitStep] = Field(min_length=1, max_length=10, description="one step per beat")

    @model_validator(mode="after")
    def _semantics(self) -> CircuitBasicParams:
        equations = 0
        for i, step in enumerate(self.steps):
            if step.action in ("resistor", "voltage_drop") and step.index >= len(self.resistors):
                raise ValueError(f"steps[{i}].index {step.index} has no resistor (there are {len(self.resistors)})")
            if step.action == "equation":
                equations += 1
                if not step.latex:
                    raise ValueError(f"steps[{i}] (equation) needs `latex`")
            if step.action == "draw" and i != 0:
                raise ValueError("`draw` may only be the first step (otherwise the circuit is shown from the start)")
        if equations > MAX_EQUATIONS:
            raise ValueError(f"at most {MAX_EQUATIONS} equation steps fit beside the circuit")
        return self


class CircuitBasicTemplate(SceneTemplate):
    name = "circuit_basic"
    title = "Basic DC circuit"
    description = (
        "A series or parallel DC circuit (battery + 1-4 resistors) explained step by step: draw it, highlight "
        "the source and each resistor with its value, animate current flow, label voltage drops and write the "
        "equations beside it. Ideal for Ohm's law, series/parallel resistance, voltage and current division, KVL/KCL."
    )
    steps_hint = "one step per item of `steps`; `draw` (optional) must be first"
    params_model = CircuitBasicParams
    scene_file = "circuit_basic.py"
    example_params = {
        "title": "Ohm's law in a circuit",
        "topology": "series",
        "source_label": "V",
        "source_value": "12 V",
        "resistors": [{"label": "R", "value": "4 Ω"}],
        "steps": [
            {"action": "draw", "note": "A battery connected to a resistor"},
            {"action": "source", "note": "The battery provides 12 volts"},
            {"action": "resistor", "index": 0, "note": "The resistor opposes the flow: 4 ohms"},
            {"action": "equation", "latex": r"I = \frac{V}{R} = \frac{12}{4}", "note": "Ohm's law gives the current"},
            {"action": "current", "text": "I = 3 A", "note": "3 amperes flow around the loop"},
        ],
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)
