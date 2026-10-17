"""Which ``chart`` and ``graph`` side panels the Dynamic stage draws as real plots, and ``chart_build``'s slots
(DYNAMIC_VIDEO_DESIGN §4.3 ``chart_build``, R-27, R-29; B2 plan §2 "Chart.js"; owner decision 1 of 2026-10-16).

The page side is ``web/js/dynamic/templates/_charts.js``: :func:`chart_problem` refuses exactly the panels its
``chartPlan`` refuses, with the same limits (:data:`LIMITS`) and the same arithmetic, so a template's Python ``fit``
never takes a chart its page hands to ``board_generic`` (B2c review CG-2). ``tests/compose/dynamic/test_chart_build.py``
runs both on one corpus. Every count and text budget is in code points of the stored text (``len``), which is what
the page counts too (``Array.from``), before any markup is stripped.

What the page draws (see ``_charts.js`` for how):

* a ``chart`` panel of type ``bar`` or ``line`` (pie, doughnut and radar are not drawn yet: an open item), within the
  format's counts - 16:9 vertical bars (up to 3 series) and lines (up to 4 series); 9:16 a single-series bar chart of
  non-negative values drawn as a labelled bar list, and lines of up to 6 categories;
* a ``graph`` panel: its functions sampled with the hardened math.js of the classic graph panel and its marked points.

The layout class (``fmt`` below) is ``landscape`` (chart_build's 16:9 box), ``portrait`` (a 9:16 text column:
chart_build's, and a side panel's reel beat) or ``panel`` (a chart in another template's 16:9 side panel box: the
vertical layouts with fewer marks and shorter labels).

The text budgets keep every label inside the room the layout gives it at R-29's minimum sizes, so a chart the director
picks never makes Dynamic preflight block on ``text_overflow`` (the step-4 lesson, B2 review F3): a category label
fits its band on at most two lines - estimated at ``char_px`` per code point over a band of ``plot_w`` / categories,
words split on single spaces - or, when any label does not, every label is short enough to be set on a slant.

Pure and deterministic; imported only inside the Dynamic gate (``templates.py``, ``events.py``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from . import zones as zn
from .zones import Format

#: Side-panel kinds the page draws as plots (``_charts.js CHART_PANEL_KINDS``).
CHART_PANEL_KINDS: Final = ("chart", "graph")
#: ``ChartSpec.chart_type`` values it draws (``_charts.js CHART_TYPES``).
CHART_TYPES: Final = ("bar", "line")


@dataclass(frozen=True)
class ChartLimits:
    """One format's limits (``_charts.js LIMITS``, value for value; B2 proposals pending the owner's review)."""

    bar_categories: int
    bar_series: int
    bars: int
    line_categories: int
    line_series: int
    plot_w: int
    char_px: int
    rotated_chars: int
    category_chars: int
    series_chars: int
    axis_chars: int
    function_chars: int
    point_chars: int


LIMITS: Final[Mapping[str, ChartLimits]] = MappingProxyType({
    "landscape": ChartLimits(bar_categories=12, bar_series=3, bars=24, line_categories=16, line_series=4, plot_w=1000,
                             char_px=28, rotated_chars=12, category_chars=120, series_chars=28, axis_chars=100,
                             function_chars=28, point_chars=24),
    "portrait": ChartLimits(bar_categories=4, bar_series=1, bars=4, line_categories=6, line_series=3, plot_w=420,
                            char_px=30, rotated_chars=8, category_chars=18, series_chars=14, axis_chars=18,
                            function_chars=14, point_chars=12),
    "panel": ChartLimits(bar_categories=6, bar_series=2, bars=8, line_categories=8, line_series=3, plot_w=560,
                         char_px=28, rotated_chars=10, category_chars=120, series_chars=20, axis_chars=40,
                         function_chars=20, point_chars=16),
})
#: The layout classes (``_charts.js`` ChartLayout).
LAYOUTS: Final = ("landscape", "portrait", "panel")


def code_points(text: Any) -> int:
    """Code points of a stored string (0 for anything else): ``Array.from(s).length`` on the page."""
    return len(text) if isinstance(text, str) else 0


def wrap_lines(text: str, per_line: int) -> float:
    """Lines ``text`` takes wrapped greedily at ``per_line`` code points, words split on single spaces; ``inf`` when a
    word is longer than a line (``_charts.js wrapLines``)."""
    words = [len(w) for w in str(text or "").split(" ") if w]
    if not words:
        return 1
    if per_line < 1:
        return math.inf
    lines, used = 1, -1
    for w in words:
        if w > per_line:
            return math.inf
        if used < 0:
            used = w
        elif used + 1 + w <= per_line:
            used += 1 + w
        else:
            lines += 1
            used = w
    return lines


def category_labels_fit(labels: Sequence[str], fmt: str) -> bool:
    """Each label on at most 2 lines of its band, else every label short enough to be slanted
    (``_charts.js categoryLabelsFit``)."""
    lim = LIMITS[fmt]
    n = max(1, len(labels))
    per_line = math.floor(lim.plot_w / n / lim.char_px)
    slanted = any(wrap_lines(label, per_line) > 2 for label in labels)
    return not slanted or all(code_points(label) <= lim.rotated_chars for label in labels)


def _finite(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _text(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _as_dict(panel: Any) -> Any:
    dump = getattr(panel, "model_dump", None)
    return dump(mode="json") if callable(dump) else panel


def chart_problem(panel: Any, fmt: str) -> str | None:
    """Why the page does not draw ``panel`` (a ``SidePanel``, or its JSON) as a plot in layout class ``fmt``
    (``landscape`` / ``portrait``: chart_build's format, ``portrait`` also a 9:16 side panel's reel beat; ``panel``:
    a 16:9 side panel); None when it does.

    The same checks, in the same order, as ``_charts.js chartPlan`` (which returns null exactly when this returns a
    reason)."""
    p = _as_dict(panel)
    if not isinstance(p, Mapping):
        return "not a side panel"
    fmt = fmt if fmt in ("portrait", "panel") else "landscape"
    lim = LIMITS[fmt]
    kind = p.get("kind")
    spec = p.get("chart") if kind == "chart" else p.get("graph")
    titles = (_text(spec.get("x_label")), _text(spec.get("y_label"))) if isinstance(spec, Mapping) else ("", "")
    if any(code_points(t) > lim.axis_chars for t in titles):
        return f"an axis title is longer than {lim.axis_chars} characters"
    if kind == "chart":
        if not isinstance(spec, Mapping):
            return "no chart data"
        ctype = spec.get("chart_type")
        ctype = "bar" if ctype is None else ctype
        if ctype not in CHART_TYPES:
            return f"a {ctype!r} chart is not drawn yet"
        labels = spec.get("labels")
        datasets = spec.get("datasets")
        if not isinstance(labels, list) or not labels or not all(isinstance(lb, str) for lb in labels):
            return "no category labels"
        if not isinstance(datasets, list) or not datasets:
            return "no dataset"
        n, ns = len(labels), len(datasets)
        for ds in datasets:
            data = ds.get("data") if isinstance(ds, Mapping) else None
            if not isinstance(data, list) or len(data) != n or not all(_finite(v) for v in data):
                return "a dataset is not one finite value per label"
            label = ds.get("label")
            if label is not None and not isinstance(label, str):
                return "a dataset label is not text"
            if code_points(label) > lim.series_chars:
                return f"a series label is longer than {lim.series_chars} characters"
        horizontal = ctype == "bar" and fmt == "portrait"
        if ctype == "bar":
            if n > lim.bar_categories or ns > lim.bar_series or n * ns > lim.bars:
                return f"more bars than a {fmt} chart holds"
            if horizontal:
                if any(v < 0 for ds in datasets for v in ds["data"]):
                    return "a 9:16 bar list has no negative values"
                if any(code_points(lb) > lim.category_chars for lb in labels):
                    return f"a category label is longer than {lim.category_chars} characters"
        elif n > lim.line_categories or ns > lim.line_series:
            return f"more points or lines than a {fmt} line chart holds"
        if not horizontal and not category_labels_fit(labels, fmt):
            return "the category labels do not fit their bands"
        return None
    if kind == "graph":
        if not isinstance(spec, Mapping):
            return "no graph data"
        fns = spec.get("functions") if isinstance(spec.get("functions"), list) else []
        pts = spec.get("points") if isinstance(spec.get("points"), list) else []
        if not fns and not pts:
            return "a graph without a function or a point"
        if len(fns) > 4 or len(pts) > 20:
            return "more functions or points than a graph holds"
        xr = spec.get("x_range")
        xr = [-5, 5] if xr is None else xr
        if not isinstance(xr, list) or len(xr) != 2 or not all(_finite(v) for v in xr) or not xr[1] > xr[0]:
            return "a bad x range"
        yr = spec.get("y_range")
        if yr is not None and (not isinstance(yr, list) or len(yr) != 2 or not all(_finite(v) for v in yr)
                               or not yr[1] > yr[0]):
            return "a bad y range"
        for fn in fns:
            expr = fn.get("expr") if isinstance(fn, Mapping) else None
            if not isinstance(expr, str) or not expr:
                return "a function without an expression"
            label = fn.get("label")
            if label is not None and not isinstance(label, str):
                return "a function label is not text"
            shown = label if label else f"y = {expr}"
            if code_points(shown) > lim.function_chars:
                return f"a function's label is longer than {lim.function_chars} characters"
        for pt in pts:
            if not isinstance(pt, Mapping) or not _finite(pt.get("x")) or not _finite(pt.get("y")):
                return "a point without finite coordinates"
            label = pt.get("label")
            if label is not None and not isinstance(label, str):
                return "a point label is not text"
            if code_points(label) > lim.point_chars:
                return f"a point label is longer than {lim.point_chars} characters"
        return None
    return f"a {kind!r} panel is not a chart"


def draws_chart(panel: Any, fmt: str) -> bool:
    """True when the page draws ``panel`` as a plot in layout class ``fmt`` (``_charts.js drawsChart``)."""
    return chart_problem(panel, fmt) is None


def mark_targets(panel: Any) -> list[tuple[str, list[str]]]:
    """The marks the narration can name, as ``(spoken label, [targets])`` in reading order (plan §1.7): a bar chart's
    category -> ``panel:bar:<s>:<i>`` for every series, a line chart's -> ``panel:point:<s>:<i>``, a graph's labelled
    point j -> ``panel:point:<functions>:<j>`` (the marked points are the series after the functions). Empty labels
    are left out. For events.py's ``draw`` events (the mark is revealed on the beat that names it)."""
    p = _as_dict(panel)
    if not isinstance(p, Mapping):
        return []
    out: list[tuple[str, list[str]]] = []
    if p.get("kind") == "chart" and isinstance(p.get("chart"), Mapping):
        spec = p["chart"]
        form = "panel:bar" if (spec.get("chart_type") or "bar") == "bar" else "panel:point"
        ns = len(spec.get("datasets") or [])
        for i, label in enumerate(spec.get("labels") or []):
            if isinstance(label, str) and label.strip():
                out.append((label, [f"{form}:{s}:{i}" for s in range(ns)]))
    if p.get("kind") == "graph" and isinstance(p.get("graph"), Mapping):
        spec = p["graph"]
        f = len(spec.get("functions") or [])
        for j, pt in enumerate(spec.get("points") or []):
            label = pt.get("label") if isinstance(pt, Mapping) else None
            if isinstance(label, str) and label.strip():
                out.append((label, [f"panel:point:{f}:{j}"]))
    return out


# --- chart_build's slots ----------------------------------------------------------------------------------------------
# web/js/dynamic/templates/chart_build.js LAYOUT and slotRows() with _charts.js SIZES; chart_build.test.js pins the
# catalogue's rows to them. Widths: 16:9 the critical box (x 96-1824); 9:16 the headline region (x 192-888) for the
# headline and the text column both critical bands hold (x 192-780) for the rest. Plain rows (templates.py wraps them in
# its SlotSpec): {slot: {format: {w, max_lines, size, role, face, weight, line_height, tracking_em, prefer_lines,
# tabular_nums}}}.

#: _charts.js SIZES: (min, max, weight, max lines) per label slot and format.
CHART_LABEL_SIZES: Final[Mapping[Format, Mapping[str, tuple[int, int, int, int]]]] = MappingProxyType({
    "landscape": MappingProxyType({"tick": (40, 44, 400, 2), "value": (40, 44, 700, 1), "axis": (40, 44, 600, 2),
                                   "series": (40, 44, 700, 2)}),
    "portrait": MappingProxyType({"tick": (44, 48, 600, 2), "value": (44, 48, 700, 1), "axis": (44, 48, 600, 2),
                                  "series": (44, 48, 700, 2)}),
})


def chart_build_slot_rows() -> dict[str, dict[Format, dict[str, Any]]]:
    """``chart_build``'s slot rows per slot and format (see the comment above)."""
    widths: dict[Format, tuple[int, int]] = {
        "landscape": (zn.REGIONS["landscape"]["full"].w, zn.REGIONS["landscape"]["full"].w),       # 1728, 1728
        "portrait": (zn.REGIONS["portrait"]["headline"].w, zn.ZONES["portrait"].text_column.w),  # 696, 588
    }
    title = {"landscape": ((64, 96), 600), "portrait": ((56, 88), 700)}
    rows: dict[str, dict[Format, dict[str, Any]]] = {}
    for fmt in ("landscape", "portrait"):
        title_w, col_w = widths[fmt]
        size, caption_weight = title[fmt]
        rows.setdefault("title", {})[fmt] = dict(
            w=title_w, max_lines=2, size=size, role="title", face="display", weight=700, line_height=1.05,
            tracking_em=-0.015, prefer_lines=1, tabular_nums=False)
        rows.setdefault("caption", {})[fmt] = dict(
            w=col_w, max_lines=2, size=(44, 52), role="label", face="body", weight=caption_weight, line_height=1.35,
            tracking_em=0.0, prefer_lines=None, tabular_nums=False)
        for slot, (lo, hi, weight, lines) in CHART_LABEL_SIZES[fmt].items():
            rows.setdefault(slot, {})[fmt] = dict(
                w=col_w, max_lines=lines, size=(lo, hi), role="label", face="body", weight=weight, line_height=1.35,
                tracking_em=0.0, prefer_lines=None, tabular_nums=slot in ("tick", "value"))
    return rows


__all__ = ["CHART_LABEL_SIZES", "CHART_PANEL_KINDS", "CHART_TYPES", "LAYOUTS", "LIMITS", "ChartLimits",
           "category_labels_fit", "chart_build_slot_rows", "chart_problem", "code_points", "draws_chart",
           "mark_targets", "wrap_lines"]
