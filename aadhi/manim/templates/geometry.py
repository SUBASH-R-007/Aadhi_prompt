"""geometry: points, segments, lines, rays, polygons, circles and angles built step by step."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ._base import ColorName, Finite, NoteText, SceneTemplate, TemplateParams, TitleText, tex_validator

COORD = 50.0
ARITY = {"segment": (2, 2), "line": (2, 2), "ray": (2, 2), "polygon": (3, 8), "circle": (1, 2),
         "angle": (3, 3), "right_angle": (3, 3)}


class GPoint(TemplateParams):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9']{0,3}$", description="point name, e.g. A, B, O, P1")
    x: Finite = Field(ge=-COORD, le=COORD)
    y: Finite = Field(ge=-COORD, le=COORD)
    show_label: bool = True
    step: int = Field(default=0, ge=0, le=11, description="step (beat index) at which the point appears")


class GShape(TemplateParams):
    kind: Literal["segment", "line", "ray", "polygon", "circle", "angle", "right_angle"] = Field(
        description="segment/line/ray: 2 points; polygon: 3-8 points; circle: centre + radius, or centre + a point "
        "on it; angle/right_angle: 3 points [A, vertex, C]"
    )
    points: list[str] = Field(min_length=1, max_length=8, description="point names")
    radius: Finite = Field(default=0.0, ge=0, le=COORD, description="circle radius when only the centre is given")
    label: str = Field(default="", max_length=20, description="plain-text label, e.g. 5 cm or 60°")
    label_latex: str = Field(default="", max_length=60, description="LaTeX label instead, e.g. \\theta")
    color: ColorName = "cyan"
    fill: bool = False
    dashed: bool = False
    step: int = Field(default=0, ge=0, le=11)

    _tex = field_validator("label_latex")(tex_validator)


class GeometryParams(TemplateParams):
    title: TitleText = ""
    points: list[GPoint] = Field(min_length=1, max_length=16)
    shapes: list[GShape] = Field(default_factory=list, max_length=16)
    notes: list[NoteText] = Field(default_factory=list, max_length=12, description="optional caption per step")

    @model_validator(mode="after")
    def _semantics(self) -> GeometryParams:
        names = [p.name for p in self.points]
        if len(set(names)) != len(names):
            raise ValueError("point names must be unique")
        pts = {p.name: p for p in self.points}
        for k, shape in enumerate(self.shapes):
            lo, hi = ARITY[shape.kind]
            if not lo <= len(shape.points) <= hi:
                raise ValueError(f"shapes[{k}] ({shape.kind}) needs {lo}-{hi} points, got {len(shape.points)}")
            for name in shape.points:
                if name not in pts:
                    raise ValueError(f"shapes[{k}] uses unknown point {name!r}")
                if pts[name].step > shape.step:
                    raise ValueError(f"shapes[{k}] appears before its point {name!r}")
            coords = [(pts[n].x, pts[n].y) for n in shape.points]
            if shape.kind == "circle" and len(coords) == 1 and shape.radius <= 0:
                raise ValueError(f"shapes[{k}] (circle) needs a radius > 0 or a second point on the circle")
            pairs: list[tuple[int, int]] = []
            if shape.kind in ("segment", "line", "ray") or (shape.kind == "circle" and len(coords) == 2):
                pairs = [(0, 1)]
            elif shape.kind in ("angle", "right_angle"):
                pairs = [(0, 1), (2, 1)]
            if any(math.dist(coords[a], coords[b]) < 1e-9 for a, b in pairs):
                raise ValueError(f"shapes[{k}] ({shape.kind}) uses coincident points")
        used = {p.step for p in self.points} | {s.step for s in self.shapes}
        total = max(used) + 1
        missing = sorted(set(range(total)) - used)
        if missing:
            raise ValueError(f"steps must be consecutive from 0: nothing appears at step(s) {missing}")
        if len(self.notes) > total:
            raise ValueError(f"notes has {len(self.notes)} entries but there are only {total} steps")
        return self


def _clip_ray(origin: tuple[float, float], direction: tuple[float, float], box: tuple[float, float, float, float]) -> tuple[float, float]:
    """Point where the ray from ``origin`` along ``direction`` leaves ``box`` (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    ts = []
    for d, o, lo, hi in ((direction[0], origin[0], x0, x1), (direction[1], origin[1], y0, y1)):
        if abs(d) > 1e-12:
            ts.extend(t for t in ((lo - o) / d, (hi - o) / d) if t > 0)
    t = min(ts) if ts else 1.0
    return origin[0] + direction[0] * t, origin[1] + direction[1] * t


class GeometryTemplate(SceneTemplate):
    name = "geometry"
    title = "Geometry construction"
    description = (
        "Geometric figure built from coordinates step by step: points, segments, lines, rays, polygons, circles, "
        "angles and right-angle marks with labels (lengths, angles). Use for theorems (Pythagoras, angle sums), "
        "constructions, coordinate geometry, trigonometry triangles, optics ray diagrams."
    )
    steps_hint = "number of steps = highest `step` of any point/shape + 1; every step from 0 must add something"
    params_model = GeometryParams
    scene_file = "geometry.py"
    example_params = {
        "title": "Pythagoras' theorem",
        "points": [
            {"name": "A", "x": 0, "y": 0, "step": 0},
            {"name": "B", "x": 4, "y": 0, "step": 0},
            {"name": "C", "x": 4, "y": 3, "step": 0},
        ],
        "shapes": [
            {"kind": "polygon", "points": ["A", "B", "C"], "color": "cyan", "fill": True, "step": 0},
            {"kind": "right_angle", "points": ["A", "B", "C"], "color": "gold", "step": 1},
            {"kind": "segment", "points": ["A", "B"], "label": "4", "color": "gold", "step": 1},
            {"kind": "segment", "points": ["B", "C"], "label": "3", "color": "gold", "step": 1},
            {"kind": "segment", "points": ["A", "C"], "label": "5", "color": "pink", "step": 2},
            {"kind": "angle", "points": ["B", "A", "C"], "label_latex": "\\theta", "color": "green", "step": 3},
        ],
        "notes": ["A triangle with vertices A, B and C", "The angle at B is a right angle",
                  "The hypotenuse AC is the longest side", "theta is the angle at A"],
    }

    def step_count(self, params: BaseModel) -> int:
        model = self.coerce(params)
        return max([p.step for p in model.points] + [s.step for s in model.shapes]) + 1

    def scene_data(self, params: GeometryParams, *, target: str, language: str) -> dict[str, Any]:  # type: ignore[override]
        data = params.model_dump(mode="json")
        pts = {p.name: (p.x, p.y) for p in params.points}
        xs = [p.x for p in params.points]
        ys = [p.y for p in params.points]
        for shape in params.shapes:
            if shape.kind == "circle":
                cx0, cy0 = pts[shape.points[0]]
                r = math.dist(pts[shape.points[0]], pts[shape.points[1]]) if len(shape.points) == 2 else shape.radius
                xs.extend([cx0 - r, cx0 + r])
                ys.extend([cy0 - r, cy0 + r])
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        pad = span * 0.12
        box = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        data["bbox"] = [round(v, 5) for v in box]
        cxm, cym = sum(p.x for p in params.points) / len(params.points), sum(p.y for p in params.points) / len(params.points)
        data["centroid"] = [round(cxm, 5), round(cym, 5)]
        extra = []
        for shape in params.shapes:
            info: dict[str, Any] = {}
            coords = [pts[n] for n in shape.points]
            if shape.kind == "line":
                (ax, ay), (bx, by) = coords
                d = (bx - ax, by - ay)
                info["ends"] = [list(_clip_ray((ax, ay), (-d[0], -d[1]), box)), list(_clip_ray((ax, ay), d, box))]
            elif shape.kind == "ray":
                (ax, ay), (bx, by) = coords
                info["ends"] = [[ax, ay], list(_clip_ray((ax, ay), (bx - ax, by - ay), box))]
            elif shape.kind == "circle":
                info["radius"] = (
                    math.dist(coords[0], coords[1]) if len(coords) == 2 else shape.radius
                )
            elif shape.kind in ("angle", "right_angle"):
                (ax, ay), (bx, by), (qx, qy) = coords
                cross = (ax - bx) * (qy - by) - (ay - by) * (qx - bx)
                info["swap"] = cross < 0
            extra.append(info)
        data["extra"] = extra
        data["step_total"] = self.step_count(params)
        return data
