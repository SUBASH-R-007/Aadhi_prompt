"""``chart_build`` and its chart engine on the Python side (DYNAMIC_VIDEO_DESIGN §4.3 ``chart_build``; owner decision 1
of 2026-10-16): ``aadhi/compose/dynamic/charts.py`` refuses exactly the panels ``web/js/dynamic/templates/_charts.js
chartPlan`` refuses (run side by side on one corpus through node: the CG-2 rule at its root, since the template's fit
asks ``chart_problem``), its slot rows are ``chart_build.js slotRows()``, ``mark_targets`` names the §1.7 targets, and
the fixtures (``make_chart_build_fixtures.py``) are current, drawable and inside the 1x / 3x / reverse gate. Once the
integrator registers the template (``templates.py`` ``implemented=True``), its fit and slots are checked against the
page too; before that those two tests skip.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from aadhi.compose.dynamic import charts
from aadhi.compose.dynamic import templates as tp
from aadhi.config import ROOT_DIR
from aadhi.schemas.timeline import Timeline
from tests.compose.dynamic import make_chart_build_fixtures as cbf
from tests.compose.dynamic.template_fixtures import FIXTURE_DIR

JS = ROOT_DIR / "web" / "js" / "dynamic" / "templates"
GATE_FRAMES = 300  # tests/determinism/test_template_fixtures.py FRAMES
KINDS = {"draw", "count", "look_here"}
FORMS = ("panel", "panel:bar", "panel:point")


def _node(script: str, payload: Any, tmp_path: Path) -> Any:
    """Run an ES module ``script`` under node with ``payload`` (JSON, through a file) as ``input``; its stdout as
    JSON."""
    src = tmp_path / "in.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    code = ("const fs = await import('node:fs/promises');\n"
            "const input = JSON.parse(await fs.readFile(process.argv[1], 'utf8'));\n" + script)
    proc = subprocess.run(["node", "--input-type=module", "-e", code, str(src)], cwd=ROOT_DIR, capture_output=True,
                          text=True, encoding="utf-8", timeout=180, check=False)
    if proc.returncode != 0:
        pytest.fail(f"node failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


# --- the corpus: panels on both sides of every limit ------------------------------------------------------------------

def _chart(ctype: str | None, n: int, ns: int, *, label_len: int = 3, values: Any = None, series_label: Any = "s",
           x_label: Any = None, y_label: Any = None, word: bool = False) -> dict[str, Any]:
    labels = [(f"w{i} " + "x" * max(0, label_len - 4)) if word else ("c" * label_len + str(i))[-label_len:] or "c"
              for i in range(n)]
    data = values if values is not None else [float(i + 1) for i in range(n)]
    spec: dict[str, Any] = {"labels": labels, "datasets": [{"label": series_label, "data": list(data)}
                                                            for _ in range(ns)]}
    if ctype is not None:
        spec["chart_type"] = ctype
    if x_label is not None:
        spec["x_label"] = x_label
    if y_label is not None:
        spec["y_label"] = y_label
    return {"kind": "chart", "title": "T", "chart": spec}


def _graph(**over: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {"functions": [{"expr": "x^2"}], "points": [], "x_range": [-5, 5]}
    spec.update(over)
    return {"kind": "graph", "graph": spec}


def corpus() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ctype in ("bar", "line", None, "pie", "doughnut", "radar"):
        for n in (1, 2, 3, 4, 5, 6, 7, 12, 13, 16, 17):
            for ns in (1, 2, 3, 4, 5):
                out.append(_chart(ctype, n, ns))
    for fmt_len in (2, 5, 8, 9, 12, 13, 18, 19, 30):
        for n in (2, 4, 6, 10):
            out.append(_chart("bar", n, 1, label_len=fmt_len))
            out.append(_chart("line", n, 1, label_len=fmt_len))
            out.append(_chart("line", n, 1, label_len=fmt_len, word=True))
    out += [
        _chart("bar", 3, 1, values=[1.0, -2.0, 3.0]), _chart("bar", 3, 1, values=[0.0, 0.0, 0.0]),
        _chart("bar", 3, 1, values=[1, "2", 3]), _chart("bar", 3, 1, values=[1, None, 3]),
        _chart("bar", 3, 1, values=[1, True, 3]), _chart("bar", 3, 1, values=[1, 2]),
        _chart("bar", 2, 1, series_label="s" * 14), _chart("bar", 2, 1, series_label="s" * 15),
        _chart("bar", 2, 1, series_label="s" * 28), _chart("bar", 2, 1, series_label="s" * 29),
        _chart("bar", 2, 1, series_label=None), _chart("bar", 2, 1, series_label=3),
        _chart("bar", 2, 1, x_label="a" * 18), _chart("bar", 2, 1, x_label="a" * 19),
        _chart("bar", 2, 1, y_label="a" * 100), _chart("bar", 2, 1, y_label="a" * 101),
        _chart("bar", 2, 1, y_label="𝑥" * 18), _chart("bar", 2, 1, y_label="𝑥" * 19),
        {"kind": "chart", "chart": {"labels": [], "datasets": [{"data": []}]}},
        {"kind": "chart", "chart": {"labels": ["a", 3], "datasets": [{"data": [1, 2]}]}},
        {"kind": "chart", "chart": None}, {"kind": "chart"}, {"kind": "image", "title": "x"}, {"kind": "model_3d"},
        _graph(), _graph(functions=[], points=[]), _graph(functions=[], points=[{"x": 1, "y": 2}]),
        _graph(x_range=[2, 1]), _graph(x_range=[1, 1]), _graph(x_range=None), _graph(x_range=[0, "1"]),
        _graph(y_range=[0, 5]), _graph(y_range=[5, 0]), _graph(y_range=None),
        _graph(functions=[{"expr": "x" * 24}]), _graph(functions=[{"expr": "x" * 25}]),
        _graph(functions=[{"expr": "x" * 10}]), _graph(functions=[{"expr": "x" * 11}]),
        _graph(functions=[{"expr": "x", "label": "l" * 28}]), _graph(functions=[{"expr": "x", "label": "l" * 29}]),
        _graph(functions=[{"expr": "x", "label": ""}]), _graph(functions=[{"expr": ""}]),
        _graph(functions=[{"expr": "x"}] * 4), _graph(functions=[{"expr": "x"}] * 5),
        _graph(points=[{"x": 1, "y": 1, "label": "p" * 12}]), _graph(points=[{"x": 1, "y": 1, "label": "p" * 13}]),
        _graph(points=[{"x": 1, "y": 1, "label": "p" * 25}]), _graph(points=[{"x": 1, "y": "1"}]),
        _graph(points=[{"x": 1, "y": 1}] * 20), _graph(points=[{"x": 1, "y": 1}] * 21),
        _graph(x_label="x" * 18), _graph(x_label="x" * 19),
    ]
    return out


def test_chart_problem_refuses_exactly_what_chart_plan_refuses(tmp_path: Path) -> None:
    """The CG-2 rule at its root: for every panel of the corpus and both formats, Python draws it exactly when the page
    does."""
    panels = corpus()
    js = _node(f"""
const {{ chartPlan }} = await import('{(JS / "_charts.js").as_uri()}');
const out = input.map((p) => ['landscape', 'portrait', 'panel'].map((f) => chartPlan(p, f) !== null));
process.stdout.write(JSON.stringify(out));
""", panels, tmp_path)
    mismatches = []
    drawn = {layout: 0 for layout in charts.LAYOUTS}
    for panel, pages in zip(panels, js, strict=True):
        for layout, page in zip(charts.LAYOUTS, pages, strict=True):
            py = charts.chart_problem(panel, layout) is None
            drawn[layout] += py
            if py != page:
                mismatches.append(f"{layout}: python {py}, page {page}: {json.dumps(panel)[:160]}")
    assert mismatches == []
    assert len(panels) > 400
    assert drawn["landscape"] > 80 and drawn["portrait"] > 30, drawn
    assert drawn["landscape"] < len(panels) and drawn["portrait"] < drawn["landscape"], drawn
    assert drawn["panel"] < drawn["landscape"], drawn


def test_limits_and_text_rules_mirror_the_page(tmp_path: Path) -> None:
    js = _node(f"""
const m = await import('{(JS / "_charts.js").as_uri()}');
const wrap = input.wrap.map(([t, n]) => {{ const v = m.wrapLines(t, n); return Number.isFinite(v) ? v : -1; }});
const fit = input.fit.map(([labels, f]) => m.categoryLabelsFit(labels, f));
process.stdout.write(JSON.stringify({{ limits: m.LIMITS, wrap, fit }}));
""", {"wrap": [["LED Bulb", 8], ["LED Bulb", 5], ["abc defghij klmn", 10], ["Refrigerators", 8], ["  a  b ", 1],
               ["", 3], ["a", 0]],
      "fit": [[["LED Bulb", "Fan", "Fridge", "AC", "Geyser"], "landscape"], [["2019", "2020"], "portrait"],
              [["Jan 2019", "September"], "portrait"], [["Fan"] * 11 + ["Air conditioner unit"], "landscape"]]},
        tmp_path)
    keys = {"bar_categories": "barCategories", "bar_series": "barSeries", "bars": "bars",
            "line_categories": "lineCategories", "line_series": "lineSeries", "plot_w": "plotW", "char_px": "charPx",
            "rotated_chars": "rotatedChars", "category_chars": "categoryChars", "series_chars": "seriesChars",
            "axis_chars": "axisChars", "function_chars": "functionChars", "point_chars": "pointChars"}
    assert sorted(js["limits"]) == sorted(charts.LAYOUTS)
    for fmt in charts.LAYOUTS:
        lim = charts.LIMITS[fmt]
        assert {k: getattr(lim, k) for k in keys} == {k: js["limits"][fmt][v] for k, v in keys.items()}, fmt
    py_wrap = [charts.wrap_lines("LED Bulb", 8), charts.wrap_lines("LED Bulb", 5),
               charts.wrap_lines("abc defghij klmn", 10), charts.wrap_lines("Refrigerators", 8),
               charts.wrap_lines("  a  b ", 1), charts.wrap_lines("", 3), charts.wrap_lines("a", 0)]
    assert [(-1 if v == float("inf") else v) for v in py_wrap] == js["wrap"]
    assert [charts.category_labels_fit(lb, f) for lb, f in [
        (["LED Bulb", "Fan", "Fridge", "AC", "Geyser"], "landscape"), (["2019", "2020"], "portrait"),
        (["Jan 2019", "September"], "portrait"), (["Fan"] * 11 + ["Air conditioner unit"], "landscape")]] == js["fit"]


def test_slot_rows_are_the_page_s(tmp_path: Path) -> None:
    js = _node(f"""
const m = await import('{(JS / "chart_build.js").as_uri()}');
process.stdout.write(JSON.stringify(m.slotRows()));
""", {}, tmp_path)
    rows = charts.chart_build_slot_rows()
    assert sorted(rows) == sorted(js)
    for slot, per in rows.items():
        for fmt, row in per.items():
            want = js[slot][fmt]["default"]
            got = {**row, "size": list(row["size"])}
            assert {k: got[k] for k in want} == want, (slot, fmt)


def test_mark_targets_name_the_bars_points_and_marked_points() -> None:
    bar = {"kind": "chart", "chart": {"chart_type": "bar", "labels": ["Fan", "", "AC"],
                                      "datasets": [{"data": [1, 2, 3]}, {"data": [3, 2, 1]}]}}
    assert charts.mark_targets(bar) == [("Fan", ["panel:bar:0:0", "panel:bar:1:0"]),
                                        ("AC", ["panel:bar:0:2", "panel:bar:1:2"])]
    line = {"kind": "chart", "chart": {"chart_type": "line", "labels": ["2019"], "datasets": [{"data": [1]}]}}
    assert charts.mark_targets(line) == [("2019", ["panel:point:0:0"])]
    graph = {"kind": "graph", "graph": {"functions": [{"expr": "x"}, {"expr": "x/2"}],
                                        "points": [{"x": 1, "y": 1}, {"x": 2, "y": 1, "label": "peak"}]}}
    assert charts.mark_targets(graph) == [("peak", ["panel:point:2:1"])]
    assert charts.mark_targets({"kind": "image"}) == []
    for target in [t for _, ts in charts.mark_targets(bar) + charts.mark_targets(graph) for t in ts]:
        assert tp.parse_target(target).form in FORMS


# --- the fixtures ----------------------------------------------------------------------------------------------------

def test_the_committed_fixtures_are_current_and_deterministic() -> None:
    stale = [p.name for p, text in cbf.files().items() if not p.is_file() or p.read_text("utf-8") != text]
    assert stale == [], "run: python -m tests.compose.dynamic.make_chart_build_fixtures"
    assert all(p.parent == FIXTURE_DIR for p in cbf.files())
    assert cbf.main(["--check"]) == 0
    assert cbf.files() == cbf.files()


def test_both_families_in_both_formats_every_kind_and_indic_text() -> None:
    modes: dict[str, set[str]] = {}
    kinds: set[tuple[str, str]] = set()
    languages: set[str] = set()
    for data in cbf.fixtures().values():
        tl = Timeline.model_validate(data)
        assert tl.presentation is not None
        fmt = tl.presentation.format
        modes.setdefault(fmt, set()).add(str(tl.presentation.mode))
        panel = tl.scenes[0].side_panel
        assert panel is not None
        kinds.add((fmt, panel.panel.chart.chart_type if panel.panel.chart else "graph"))
        languages.add(tl.language)
    assert modes == {"landscape": {"dark", "light"}, "portrait": {"dark", "light"}}
    assert kinds == {(f, k) for f in ("landscape", "portrait") for k in ("bar", "line", "graph")}
    assert {"ta-IN", "hi-IN"} <= languages


@pytest.mark.parametrize("case", sorted(cbf.fixtures()))
def test_each_fixture_is_a_chart_build_scene_the_gate_sees_whole(case: str) -> None:
    tl = Timeline.model_validate(cbf.fixtures()[case])
    assert tl.presentation is not None
    fmt = tl.presentation.format
    scene = tl.scenes[0]
    assert scene.look is not None and scene.look.template == "chart_build"
    assert scene.side_panel is not None
    assert charts.chart_problem(scene.side_panel.panel, fmt) is None
    assert [i.kind for i in scene.board] in ([], ["heading"])
    assert round(tl.total_duration * tl.fps) <= GATE_FRAMES
    lo, hi = tp.TEMPLATES["chart_build"].duration[fmt].min, tp.TEMPLATES["chart_build"].duration[fmt].max
    assert lo <= scene.duration <= (hi or scene.duration)
    for e in scene.events:
        assert e.kind in KINDS, e
        assert e.target is not None and tp.parse_target(e.target).form in FORMS, e
        assert tp.TEMPLATES["chart_build"].consumes.accepts(e.kind, e.target), e


# --- once registered ---------------------------------------------------------------------------------------------------

def _registered() -> bool:
    return tp.TEMPLATES["chart_build"].implemented


@pytest.mark.skipif(not _registered(), reason="chart_build is not registered in templates.py yet (the integrator's)")
def test_the_registered_spec_s_slots_are_the_rows() -> None:
    spec = tp.TEMPLATES["chart_build"]
    rows = charts.chart_build_slot_rows()
    assert sorted(spec.slots) == sorted(rows)
    for slot, per in rows.items():
        for fmt, row in per.items():
            got = spec.slots[slot][fmt]["default"]  # type: ignore[index]
            assert {k: getattr(got, k) for k in row} == row, (slot, fmt)


@pytest.mark.skipif(not _registered(), reason="chart_build is not registered in templates.py yet (the integrator's)")
def test_the_registered_fit_draws_what_the_page_draws(tmp_path: Path) -> None:
    """fit > 0 implies drawsScene, on the screenplay shapes a chart scene comes in (a heading or none beside a chart or
    graph panel of the corpus; a bullet beside one), through the real TimedScene the stage receives."""
    from aadhi.schemas.screenplay import BoardScene
    from aadhi.schemas.timeline import ResolvedSidePanel, TimedScene

    scenes = []
    for k, panel in enumerate(p for p in corpus() if p.get("kind") in ("chart", "graph")):
        for board in ([], [{"id": "h1", "kind": "heading", "text": "A heading"}],
                      [{"id": "b1", "kind": "bullet", "text": "A bullet"}]):
            try:
                scene = BoardScene.model_validate({"id": f"s{k}", "type": "content", "title": "T", "board": board,
                                                   "beats": [{"id": "b1", "narration": "Hello."}],
                                                   "side_panel": panel})
            except ValueError:
                continue  # the schema refuses it before any template sees it
            timed = TimedScene(scene_id=scene.id, index=0, type=scene.type, title=scene.title, start=0.0,
                               duration=10.0, board=list(scene.board),
                               side_panel=ResolvedSidePanel(panel=scene.side_panel, media=None, show_at=0.0))
            scenes.append((scene, json.loads(timed.model_dump_json())))
    assert len(scenes) > 200
    draws = _node(f"""
const m = await import('{(JS / "chart_build.js").as_uri()}');
process.stdout.write(JSON.stringify(input.map((s) => [m.drawsScene(s, 'landscape'), m.drawsScene(s, 'portrait')])));
""", [t for _, t in scenes], tmp_path)
    spec = tp.TEMPLATES["chart_build"]
    bad = [f"{fmt}: {scene.side_panel.model_dump_json()[:120]}"
           for (scene, _), (land, port) in zip(scenes, draws, strict=True)
           for fmt, page in (("landscape", land), ("portrait", port))
           if spec.fit(scene, fmt) > 0 and not page]  # type: ignore[arg-type]
    assert bad == []
    assert sum(spec.fit(s, "landscape") > 0 for s, _ in scenes) > 50  # type: ignore[arg-type]
