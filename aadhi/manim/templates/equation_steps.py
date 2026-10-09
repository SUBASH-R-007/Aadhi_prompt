"""equation_steps: a derivation/solution shown one equation per beat."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ._base import ColorName, NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator


class EquationStep(TemplateParams):
    latex: str = Field(min_length=1, max_length=300, description="LaTeX of the full equation at this step, no $ signs")
    annotation: NoteText = ""

    _tex = field_validator("latex")(tex_validator)


class EquationStepsParams(TemplateParams):
    title: TitleText = ""
    steps: list[EquationStep] = Field(min_length=1, max_length=10, description="one equation per beat, in order")
    layout: Literal["transform", "stack"] = Field(
        default="transform", description="transform = morph in place; stack = keep earlier lines above"
    )
    color: ColorName = "white"
    box_final: bool = Field(default=True, description="draw a gold box around the final equation")


class EquationStepsTemplate(SceneTemplate):
    name = "equation_steps"
    title = "Equation steps"
    description = (
        "Step-by-step algebra or derivation: each beat shows the next form of an equation, morphing "
        "matching terms (TransformMatchingTex) with an optional short note. Use for rearranging formulas, "
        "worked solutions and derivations (e.g. V=IR -> I=V/R -> I=12/4 -> I=3 A)."
    )
    steps_hint = "one step per item of `steps` (step i is shown at beat i)"
    params_model = EquationStepsParams
    scene_file = "equation_steps.py"
    example_params = {
        "title": "Solving for current",
        "steps": [
            {"latex": r"V = I R", "annotation": "Ohm's law"},
            {"latex": r"I = \frac{V}{R}", "annotation": "Divide both sides by R"},
            {"latex": r"I = \frac{12}{4}", "annotation": "Substitute V = 12 V and R = 4 ohm"},
            {"latex": r"I = 3\,\mathrm{A}", "annotation": "The current is 3 amperes"},
        ],
        "layout": "transform",
        "color": "white",
        "box_final": True,
    }

    def step_count(self, params: BaseModel) -> int:
        return len(self.coerce(params).steps)
