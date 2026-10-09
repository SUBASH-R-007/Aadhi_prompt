"""vector_forces: free-body diagram, one labelled force arrow per step, optional resultant."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ._base import ColorName, Finite, NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator

MIN_LEN = 0.22  # shortest arrow as a fraction of the longest (keeps tiny forces visible)


class Force(TemplateParams):
    label: str = Field(min_length=1, max_length=24, description="plain-text name, e.g. Weight, Normal, Friction")
    label_latex: str = Field(default="", max_length=60, description="optional LaTeX symbol shown instead, e.g. F_g")
    magnitude: Finite = Field(gt=0, le=1e6, description="relative size (any unit, arrows are scaled)")
    angle_deg: Finite = Field(
        ge=-360, le=360, description="direction in degrees counter-clockwise from +x (right): up=90, down=-90, left=180"
    )
    value_text: str = Field(default="", max_length=20, description="value shown with the label, e.g. 49 N")
    color: ColorName = "cyan"
    note: NoteText = ""

    _tex = field_validator("label_latex")(tex_validator)


class VectorForcesParams(TemplateParams):
    title: TitleText = ""
    body_label: str = Field(default="", max_length=16, description="text on the body, e.g. m or 5 kg")
    body_shape: Literal["box", "circle"] = "box"
    surface: Literal["none", "ground", "incline"] = "none"
    incline_deg: Finite = Field(default=30.0, ge=5, le=75, description="slope angle when surface = incline")
    forces: list[Force] = Field(min_length=1, max_length=6, description="one force appears per beat, in order")
    show_resultant: bool = Field(default=False, description="add a final step showing the vector sum")
    resultant_label: str = Field(default="Net force", max_length=24)
    resultant_note: NoteText = ""


class VectorForcesTemplate(SceneTemplate):
    name = "vector_forces"
    title = "Vector forces (free-body diagram)"
    description = (
        "Free-body diagram: a body (box or ball, optionally on the ground or an incline) and labelled force "
        "arrows added one per beat, scaled by magnitude, with an optional final resultant (vector sum). Use for "
        "Newton's laws, equilibrium, friction, inclined planes, vector addition."
    )
    steps_hint = "one step per force (in order) + one more step for the resultant when show_resultant is true"
    params_model = VectorForcesParams
    scene_file = "vector_forces.py"
    example_params = {
        "title": "Block on an incline",
        "body_label": "m",
        "body_shape": "box",
        "surface": "incline",
        "incline_deg": 30,
        "forces": [
            {"label": "Weight", "label_latex": "mg", "magnitude": 10, "angle_deg": -90, "value_text": "49 N",
             "color": "gold", "note": "Gravity pulls straight down"},
            {"label": "Normal", "label_latex": "N", "magnitude": 8.66, "angle_deg": 120, "value_text": "42 N",
             "color": "cyan", "note": "The surface pushes back, perpendicular to the slope"},
            {"label": "Friction", "label_latex": "f", "magnitude": 5, "angle_deg": 30, "value_text": "",
             "color": "pink", "note": "Friction acts up the slope"},
        ],
        "show_resultant": True,
        "resultant_label": "Net force",
        "resultant_note": "The forces balance: the block stays at rest",
    }

    def step_count(self, params: BaseModel) -> int:
        model = self.coerce(params)
        return len(model.forces) + (1 if model.show_resultant else 0)

    def scene_data(self, params: VectorForcesParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        biggest = max(f.magnitude for f in params.forces)
        vectors = []
        rx = ry = 0.0
        for f in params.forces:
            ang = math.radians(f.angle_deg)
            ux, uy = math.cos(ang), math.sin(ang)
            scale = max(f.magnitude / biggest, MIN_LEN)
            vectors.append({"dx": round(ux * scale, 5), "dy": round(uy * scale, 5)})
            rx += f.magnitude * ux
            ry += f.magnitude * uy
        norm = math.hypot(rx, ry) / biggest
        data["vectors"] = vectors
        data["resultant"] = {
            "dx": round(rx / biggest, 5),
            "dy": round(ry / biggest, 5),
            "zero": norm < 0.03,
            "magnitude_text": f"{math.hypot(rx, ry):.3g}",
        }
        return data
