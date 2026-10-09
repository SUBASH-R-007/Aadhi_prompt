"""v1 ``side_panel`` objects -> v2 ``SidePanel`` kwargs (validated; unusable panels are dropped)."""

from __future__ import annotations

import math
import re
from typing import Any

from pydantic import ValidationError

from ..schemas.screenplay import GraphFunction, SidePanel

__all__ = ["adapt_manim_code", "convert_side_panel", "js_to_mathjs", "model_3d_primitives"]

_CHART_TYPES = {"bar", "line", "pie", "doughnut", "radar"}
_CHART_ALIASES = {"polararea": "pie", "horizontalbar": "bar", "area": "line", "column": "bar"}

_ATOM = [
    {"shape": "sphere", "position": (0.0, 0.0, 0.0), "size": [0.6], "color": "#ffd700", "label": "Nucleus"},
    {"shape": "torus", "size": [1.4, 0.03], "color": "#b026ff"},
    {"shape": "torus", "size": [2.1, 0.03], "color": "#b026ff"},
    {"shape": "torus", "size": [2.8, 0.03], "color": "#b026ff"},
    {"shape": "sphere", "position": (1.4, 0.0, 0.0), "size": [0.15], "color": "#00e5ff", "label": "Electron"},
    {"shape": "sphere", "position": (0.0, -2.1, 0.0), "size": [0.15], "color": "#00e5ff"},
    {"shape": "sphere", "position": (-1.98, 1.98, 0.0), "size": [0.15], "color": "#00e5ff"},
]
_MODELS: dict[str, list[dict[str, Any]]] = {
    "atom": _ATOM,
    "torus": [{"shape": "torus", "size": [1.5, 0.5], "color": "#b026ff"}],
    "box": [{"shape": "box", "size": [2.0, 2.0, 2.0], "color": "#b026ff"}],
    "cube": [{"shape": "box", "size": [2.0, 2.0, 2.0], "color": "#b026ff"}],
}


def model_3d_primitives(model_name: str) -> list[dict[str, Any]] | None:
    """Primitive approximation of a v1 built-in 3D model (atom = Bohr-style shells), or None."""
    prims = _MODELS.get((model_name or "").strip().lower())
    return [dict(p) for p in prims] if prims else None


def js_to_mathjs(src: str) -> str | None:
    """Convert a v1 JavaScript graph function to the v2 math.js allow-list syntax, or None if it fails.

    ``Math.sin`` -> ``sin``, ``Math.PI`` -> ``pi``, ``Math.pow(a, b)`` -> ``pow(a, b)``, ``**`` -> ``^``;
    a leading ``y =`` / ``f(x) =`` / ``return`` is dropped.
    """
    s = (src or "").strip()
    s = re.sub(r"^\s*return\s+", "", s)
    s = re.sub(r"^\s*(?:y|f\s*\(\s*x\s*\))\s*=(?!=)\s*", "", s).strip().rstrip(";").strip()
    if not s:
        return None
    s = re.sub(r"\bMath\.PI\b", "pi", s)
    s = re.sub(r"\bMath\.E\b", "e", s)
    s = re.sub(r"\bMath\.LN2\b", "log(2)", s)
    s = re.sub(r"\bMath\.LN10\b", "log(10)", s)
    s = re.sub(r"\bMath\.SQRT2\b", "sqrt(2)", s)
    s = re.sub(r"\bMath\.([a-z][A-Za-z0-9]*)\b", r"\1", s)
    s = s.replace("**", "^")
    s = re.sub(r"\s+", " ", s)
    try:
        return GraphFunction(expr=s).expr
    except ValidationError:
        return None


def adapt_manim_code(code: str) -> tuple[str, bool]:
    """Re-base v1 ``class X(Scene)`` onto ``AadhiScene``. Returns ``(code, rebased)``."""
    code = (code or "").replace("\r\n", "\n")
    new, n = re.subn(r"^(\s*class\s+\w+\s*\(\s*)Scene(\s*\)\s*:)", r"\1AadhiScene\2", code, flags=re.MULTILINE)
    return new, n > 0


def _num(value: Any) -> float | None:
    """A finite float for a v1 chart value, else None (NaN/Infinity/overflow are not numbers here:
    they cannot be rendered as JSON responses nor stored in PostgreSQL JSONB)."""
    if isinstance(value, bool):
        return None
    number: float
    try:
        if isinstance(value, (int, float)):
            number = float(value)
        elif isinstance(value, str):
            number = float(value.strip().replace(",", ""))
        else:
            return None
    except (ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _chart(sp: dict[str, Any], warn: list[str]) -> dict[str, Any] | None:
    data = sp.get("data") if isinstance(sp.get("data"), dict) else {}
    ctype = str(sp.get("chart_type") or data.get("type") or "bar").strip().lower()
    ctype = _CHART_ALIASES.get(ctype, ctype)
    if ctype in ("scatter", "bubble"):
        warn.append(f"{ctype} chart converted to a bar chart")
        ctype = "bar"
    elif ctype not in _CHART_TYPES:
        warn.append(f"unsupported chart type {ctype!r} converted to a bar chart")
        ctype = "bar"
    labels = [str(x)[:120] for x in (data.get("labels") or []) if x is not None]
    raw_sets = [d for d in (data.get("datasets") or []) if isinstance(d, dict)]
    if len(raw_sets) > 6:
        warn.append("chart cut to 6 datasets")
        raw_sets = raw_sets[:6]
    datasets: list[dict[str, Any]] = []
    bad_values = False
    for ds in raw_sets:
        values: list[float] = []
        points_x: list[str] = []
        for v in ds.get("data") or []:
            if isinstance(v, dict):
                points_x.append(str(v.get("x", "")))
                v = v.get("y", v.get("value"))
            num = _num(v)
            if num is None:
                bad_values = True
                num = 0.0
            values.append(num)
        if not labels and points_x:
            labels = points_x
        datasets.append({"label": str(ds.get("label") or "")[:120], "data": values})
    if not datasets or not any(d["data"] for d in datasets):
        return None
    if not labels:
        labels = [str(i + 1) for i in range(max(len(d["data"]) for d in datasets))]
    if len(labels) > 64:
        warn.append("chart cut to 64 labels")
        labels = labels[:64]
    n = len(labels)
    for d in datasets:
        if len(d["data"]) != n:
            bad_values = True
            d["data"] = (d["data"] + [0.0] * n)[:n]
    if bad_values:
        warn.append("chart data had missing or non-numeric values (set to 0)")
    out: dict[str, Any] = {"chart_type": ctype, "labels": labels, "datasets": datasets}
    for key in ("x_label", "y_label"):
        if isinstance(sp.get(key), str) and sp[key].strip():
            out[key] = sp[key].strip()[:120]
    return out


def convert_side_panel(sp: Any, *, scene_title: str, warnings: list[str], where: str) -> dict[str, Any] | None:
    """Convert one v1 side panel; returns validated ``SidePanel`` kwargs or None (with a warning)."""
    if not isinstance(sp, dict):
        return None
    kind = str(sp.get("type") or "").strip().lower().replace("-", "_")
    title = sp.get("title") if isinstance(sp.get("title"), str) and sp.get("title").strip() else None
    panel: dict[str, Any] = {"title": title[:160].strip() if title else None}
    notes: list[str] = []
    if kind in ("skill_tree", "concept_map"):
        panel["kind"] = "skill_tree"
    elif kind == "image":
        prompt = str(sp.get("prompt") or scene_title or "").strip()
        if not prompt:
            warnings.append(f"{where}: image side panel without a prompt dropped")
            return None
        panel.update(kind="image", image_prompt=prompt[:800])
    elif kind == "chart":
        chart = _chart(sp, notes)
        if chart is None:
            warnings.append(f"{where}: chart side panel without usable data dropped")
            return None
        panel.update(kind="chart", chart=chart)
    elif kind in ("3d_model", "model_3d"):
        name = str(sp.get("model_name") or "").strip()
        prims = model_3d_primitives(name)
        if prims is None:
            warnings.append(f"{where}: unknown 3D model {name!r} dropped")
            return None
        panel.update(kind="model_3d", model_3d={"primitives": prims, "auto_rotate": True})
        notes.append(f"3D model {name!r} approximated with primitives")
    elif kind == "quiz":
        options = [str(o).strip()[:240] for o in (sp.get("options") or []) if str(o).strip()]
        panel.update(
            kind="quiz",
            quiz={
                "question": str(sp.get("question") or "").strip()[:400],
                "options": options[:5],
                "correct_index": sp.get("correct_index", 0),
            },
        )
    elif kind == "terminal":
        panel.update(
            kind="terminal",
            terminal={
                "command": str(sp.get("command") or "").strip()[:300],
                "output": str(sp.get("output") or "")[:3000],
            },
        )
    elif kind == "graph":
        expr = js_to_mathjs(str(sp.get("function") or sp.get("expr") or ""))
        if expr is None:
            warnings.append(
                f"{where}: graph function {str(sp.get('function') or '')[:60]!r} is not supported by the "
                "math.js allow-list; graph panel dropped"
            )
            return None
        panel.update(kind="graph", graph={"functions": [{"expr": expr, "label": None}]})
    elif kind == "manim":
        code, _ = adapt_manim_code(str(sp.get("manim_code") or sp.get("code") or ""))
        if not code.strip():
            warnings.append(f"{where}: manim side panel without code dropped")
            return None
        panel.update(kind="manim", manim={"code": code})
        notes.append("v1 Manim code is not synchronised to the narration beats; review it")
    elif kind == "gif":
        query = str(sp.get("query") or "").strip()
        if not query:
            warnings.append(f"{where}: GIF side panel without a query dropped")
            return None
        panel.update(kind="gif", gif_query=query[:80])
    else:
        warnings.append(f"{where}: side panel type {kind or '?'!r} is not supported in v2; dropped")
        return None
    try:
        validated = SidePanel.model_validate(panel)
    except ValidationError as exc:
        first = exc.errors()[0].get("msg", "invalid") if exc.errors() else "invalid"
        warnings.append(f"{where}: {kind} side panel dropped ({first})")
        return None
    warnings.extend(f"{where}: {n}" for n in notes)
    return validated.model_dump(mode="json", exclude_none=True)
