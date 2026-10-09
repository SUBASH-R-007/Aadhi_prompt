"""Template library: registry, params validation, step counts, generated sources, validate_spec."""

from __future__ import annotations

import ast
import copy
import json
from typing import Any

import pytest

from aadhi.manim import validate_spec
from aadhi.manim.base import Template, TemplateInfo
from aadhi.manim.guard import check_template_source
from aadhi.manim.templates import get_template, list_templates, registry, validate_params
from aadhi.schemas.screenplay import ManimSpec

EXPECTED = {
    "equation_steps", "function_plot", "vector_forces", "block_diagram", "circuit_basic", "bar_compare",
    "timeline_steps", "geometry", "matrix_ops", "graph_traversal", "wave", "truth_table",
}
NAMES = sorted(EXPECTED)


def example(name: str) -> dict[str, Any]:
    return copy.deepcopy(registry[name].example_params)


def test_registry_has_all_templates() -> None:
    assert set(registry) == EXPECTED
    for name, template in registry.items():
        assert isinstance(template, Template)
        assert template.name == name


def test_list_and_get_templates() -> None:
    infos = list_templates()
    assert [i.name for i in infos] == list(registry)
    assert all(isinstance(i, TemplateInfo) and i.description and i.steps_hint for i in infos)
    assert get_template("wave") is registry["wave"]
    with pytest.raises(KeyError, match="available"):
        get_template("nope")


@pytest.mark.parametrize("name", NAMES)
def test_info_is_complete_and_llm_compatible(name: str) -> None:
    info = registry[name].info()
    assert len(info.description) > 80 and info.title
    schema_text = json.dumps(info.params_schema)
    for bad in ('"oneOf"', '"prefixItems"', '"additionalProperties": true', '"discriminator"'):
        assert bad not in schema_text
    model, problems = validate_params(name, info.example_params)
    assert problems == [] and model is not None
    try:
        from aadhi.providers.llm.schema import assert_llm_compatible
    except ImportError:  # providers module not available yet
        return
    assert_llm_compatible(registry[name].params_model)


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("target", ["fullscreen", "panel"])
def test_render_source_compiles_and_passes_guard(name: str, target: str) -> None:
    template = registry[name]
    model, _ = validate_params(name, example(name))
    source = template.render_source(model, target=target, language="ta-IN")
    tree = ast.parse(source)
    assert isinstance(tree.body[0], ast.Assign) and tree.body[0].targets[0].id == "PARAMS"  # type: ignore[attr-defined]
    assert check_template_source(source) == []
    # PARAMS is a pure literal (data only)
    ast.literal_eval(tree.body[0].value)  # type: ignore[attr-defined]


def test_render_source_accepts_dict_params() -> None:
    src = registry["wave"].render_source(example("wave"), target="fullscreen", language="en-IN")  # type: ignore[arg-type]
    assert "class WaveScene(AadhiScene)" in src


EXPECTED_STEPS = {
    "equation_steps": 4, "function_plot": 6, "vector_forces": 4, "block_diagram": 4, "circuit_basic": 5,
    "bar_compare": 5, "timeline_steps": 4, "geometry": 4, "matrix_ops": 4, "graph_traversal": 5, "wave": 4,
    "truth_table": 4,
}


@pytest.mark.parametrize("name", NAMES)
def test_step_count_of_examples(name: str) -> None:
    model, _ = validate_params(name, example(name))
    assert registry[name].step_count(model) == EXPECTED_STEPS[name]
    assert registry[name].step_count(example(name)) == EXPECTED_STEPS[name]  # type: ignore[arg-type]


def test_step_count_variants() -> None:
    vf = example("vector_forces")
    vf["show_resultant"] = False
    assert registry["vector_forces"].step_count(validate_params("vector_forces", vf)[0]) == 3
    bars = example("bar_compare")
    bars["highlight_max"] = False
    for b in bars["bars"]:
        b["step"] = 0
    assert registry["bar_compare"].step_count(validate_params("bar_compare", bars)[0]) == 1
    tt = example("truth_table")
    tt["rows_per_step"] = 2
    assert registry["truth_table"].step_count(validate_params("truth_table", tt)[0]) == 2
    tt.update({"gate": "XOR", "inputs": ["A", "B", "C"], "rows_per_step": 3})
    assert registry["truth_table"].step_count(validate_params("truth_table", tt)[0]) == 3  # 8 rows / 3


def mutate(name: str, fn) -> list[str]:
    params = example(name)
    fn(params)
    model, problems = validate_params(name, params)
    assert model is None
    return problems


@pytest.mark.parametrize(
    ("name", "mutation", "needle"),
    [
        ("equation_steps", lambda p: p.update(steps=[]), "steps"),
        ("equation_steps", lambda p: p["steps"][0].update(latex=r"\input{/etc/passwd}"), "forbidden"),
        ("equation_steps", lambda p: p["steps"][0].update(latex=r"\frac{a}{b"), "unmatched"),
        ("equation_steps", lambda p: p.update(layout="spiral"), "layout"),
        ("equation_steps", lambda p: p.update(unknown_key=1), "unknown_key"),
        ("equation_steps", lambda p: p.update(steps=[{"latex": "x"}] * 11), "steps"),
        ("function_plot", lambda p: p["functions"][0].update(expr="__import__('os')"), "functions.0.expr"),
        ("function_plot", lambda p: p["functions"][0].update(expr="2x"), "2*x"),
        ("function_plot", lambda p: p.update(x_min=5, x_max=1), "x_min must be smaller"),
        ("function_plot", lambda p: p.update(y_min=3, y_max=1), "y_min must be smaller"),
        ("function_plot", lambda p: p["functions"][0].update(expr="sqrt(-1-x^2)"), "undefined on most"),
        ("function_plot", lambda p: p["steps"][1].update(function_index=2), "has no function"),
        ("function_plot", lambda p: p["steps"][1].update(x=50), "within [x_min, x_max]"),
        ("function_plot", lambda p: p["steps"][5].update(x_end=-1), "x < x_end"),
        ("function_plot", lambda p: p.update(x_min=float("inf")), "x_min"),
        ("function_plot", lambda p: p["functions"][0].update(expr="1/x"), None),
        ("vector_forces", lambda p: p.update(forces=[]), "forces"),
        ("vector_forces", lambda p: p["forces"][0].update(magnitude=0), "magnitude"),
        ("vector_forces", lambda p: p["forces"][0].update(angle_deg=720), "angle_deg"),
        ("vector_forces", lambda p: p.update(incline_deg=89), "incline_deg"),
        ("vector_forces", lambda p: p["forces"][0].update(label_latex=r"\def\x{1}"), "forbidden"),
        ("block_diagram", lambda p: p["nodes"][1].update(id="src"), "unique"),
        ("block_diagram", lambda p: p["edges"][0].update(target="ghost"), "unknown node"),
        ("block_diagram", lambda p: p["edges"][0].update(step=0, target="gen"), "before one of its boxes"),
        ("block_diagram", lambda p: (p["nodes"][3].update(step=7), p["edges"][2].update(step=7)), "consecutive"),
        ("block_diagram", lambda p: p["nodes"][0].update(column=0, row=0), "every node"),
        ("block_diagram", lambda p: p["edges"][0].update(target="src"), "itself"),
        ("block_diagram", lambda p: p.update(notes=["a"] * 6), "notes"),
        ("block_diagram", lambda p: p["nodes"][0].update(id="bad id!"), "id"),
        ("circuit_basic", lambda p: p["steps"][2].update(index=3), "has no resistor"),
        ("circuit_basic", lambda p: p["steps"][3].update(latex=""), "needs `latex`"),
        ("circuit_basic", lambda p: p["steps"].append({"action": "draw"}), "first step"),
        ("circuit_basic", lambda p: p.update(resistors=[]), "resistors"),
        ("circuit_basic", lambda p: p.update(topology="mesh"), "topology"),
        ("circuit_basic", lambda p: p.update(steps=[{"action": "equation", "latex": "x"}] * 5), "at most 4"),
        ("bar_compare", lambda p: p["bars"][3].update(step=6), "consecutive"),
        ("bar_compare", lambda p: [b.update(value=0) for b in p["bars"]], "non-zero"),
        ("bar_compare", lambda p: p["bars"][0].update(value=float("nan")), "value"),
        ("timeline_steps", lambda p: p.update(events=[]), "events"),
        ("timeline_steps", lambda p: p["events"][0].update(marker=""), "marker"),
        ("geometry", lambda p: p["points"][1].update(name="A"), "unique"),
        ("geometry", lambda p: p["shapes"][0].update(points=["A", "Z"]), "polygon"),
        ("geometry", lambda p: p["shapes"][2].update(points=["A", "Q"]), "unknown point"),
        ("geometry", lambda p: p["shapes"][1].update(points=["A", "B"]), "needs 3-3 points"),
        ("geometry", lambda p: p["points"][2].update(step=3), "appears before its point"),
        ("geometry", lambda p: p["points"][1].update(x=0, y=0), "coincident"),
        ("geometry", lambda p: p["shapes"].append({"kind": "circle", "points": ["A"], "step": 0}), "radius"),
        ("geometry", lambda p: p["points"][0].update(x=999), "x"),
        ("matrix_ops", lambda p: p["matrices"][0]["rows"][0].update(values=["1"]), "same number"),
        ("matrix_ops", lambda p: p.update(operators=["×"]), "exactly one symbol"),
        ("matrix_ops", lambda p: p["steps"][1].update(row=4), "outside the result"),
        ("matrix_ops", lambda p: (p["matrices"].pop(), p["operators"].pop()), "three matrices"),
        ("matrix_ops", lambda p: p["matrices"][0]["rows"][0].update(values=[r"\input{x}", "2"]), "forbidden"),
        ("matrix_ops", lambda p: p["steps"].append({"action": "highlight_row", "matrix": 0, "row": 3}), "outside matrix"),
        ("graph_traversal", lambda p: p["nodes"].append({"id": "A"}), "unique"),
        ("graph_traversal", lambda p: p["edges"].append({"source": "A", "target": "Z"}), "unknown node"),
        ("graph_traversal", lambda p: p["steps"][1].update(via="E"), "no edge"),
        ("graph_traversal", lambda p: p["steps"][1].update(visit=""), "needs `visit`"),
        ("graph_traversal", lambda p: p["steps"][0].update(frontier=["Q"]), "unknown node"),
        ("graph_traversal", lambda p: p["edges"].append({"source": "A", "target": "A"}), "self-loops"),
        ("graph_traversal", lambda p: p.update(start="Z"), "unknown node"),
        ("wave", lambda p: p["steps"][0].update(frequency=100), "cycles"),
        ("wave", lambda p: [s.update(amplitude=0) for s in p["steps"]], "amplitude > 0"),
        ("wave", lambda p: p.update(steps=[]), "steps"),
        ("truth_table", lambda p: p.update(gate="NOT"), "exactly one input"),
        ("truth_table", lambda p: p.update(gate="AND", inputs=["A"]), "at least two"),
        ("truth_table", lambda p: p.update(gate="NONE"), "give the rows"),
        ("truth_table", lambda p: p.update(rows=[{"inputs": [1, 1], "output": 0}]), "wrong for AND"),
        ("truth_table", lambda p: p.update(rows=[{"inputs": [1], "output": 0}]), "expected 2"),
        ("truth_table", lambda p: p.update(rows=[{"inputs": [2, 1], "output": 0}]), "inputs"),
    ],
)
def test_invalid_params_are_reported(name: str, mutation, needle: str | None) -> None:
    params = example(name)
    mutation(params)
    model, problems = validate_params(name, params)
    if needle is None:  # valid edge case
        assert problems == [] and model is not None
        return
    assert model is None and problems, "expected validation problems"
    assert all(p.startswith(f"{name}: ") for p in problems)
    assert any(needle in p for p in problems), problems


def test_validate_params_edge_inputs() -> None:
    assert validate_params("nope", {})[1][0].startswith("unknown manim template 'nope'")
    assert validate_params("wave", [])[1] == ["params must be an object"]  # type: ignore[arg-type]
    model, _ = validate_params("wave", example("wave"))
    again, problems = validate_params("wave", model)
    assert problems == [] and again == model


def test_function_plot_scene_data_samples_and_ranges() -> None:
    template = registry["function_plot"]
    params = example("function_plot")
    model, _ = validate_params("function_plot", params)
    data = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert len(data["curves"][0]["xs"]) == 240
    assert data["y_min"] <= 0 < data["y_max"]
    tangent = data["details"][2]
    assert tangent["slope"] == pytest.approx(-4.0, rel=1e-3)
    assert data["details"][5]["polygon"][0][0] == 0
    params["functions"][0]["expr"] = "1/x"
    params["y_min"], params["y_max"] = -5, 5
    params["steps"] = [{"action": "show_function"}]
    model, _ = validate_params("function_plot", params)
    data = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert None in data["curves"][0]["ys"]  # asymptote breaks the curve


def test_block_diagram_auto_layout_and_panel_transpose() -> None:
    template = registry["block_diagram"]
    model, _ = validate_params("block_diagram", example("block_diagram"))
    wide = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    tall = template.scene_data(model, target="panel", language="en-IN")  # type: ignore[attr-defined]
    assert (wide["grid_w"], wide["grid_h"]) == (4, 1)
    assert (tall["grid_w"], tall["grid_h"]) == (1, 4)
    cyc = example("block_diagram")
    cyc["edges"].append({"source": "gen", "target": "src", "step": 3})
    model, problems = validate_params("block_diagram", cyc)
    assert not problems
    data = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert data["layout"]["src"]["gx"] == 0 and data["layout"]["gen"]["gx"] == 3


def test_graph_layouts() -> None:
    template = registry["graph_traversal"]
    for layout in ("layered", "circular", "manual"):
        params = example("graph_traversal")
        params["layout"] = layout
        if layout == "manual":
            for k, node in enumerate(params["nodes"]):
                node.update(x=k, y=k % 2)
        model, problems = validate_params("graph_traversal", params)
        assert not problems
        data = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
        assert set(data["positions"]) == {"A", "B", "C", "D", "E"}


def test_truth_table_generates_rows() -> None:
    from aadhi.manim.templates.truth_table import gate_output

    params = example("truth_table")
    params.update(gate="XNOR", inputs=["A", "B"])
    model, _ = validate_params("truth_table", params)
    data = registry["truth_table"].scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert [r["output"] for r in data["table"]] == [1, 0, 0, 1]
    assert gate_output("NAND", [1, 1]) == 0 and gate_output("NOR", [0, 0]) == 1 and gate_output("NOT", [0]) == 1


def test_vector_resultant_detects_balance() -> None:
    model, _ = validate_params("vector_forces", example("vector_forces"))
    data = registry["vector_forces"].scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert data["resultant"]["zero"] is True
    unbalanced = example("vector_forces")
    unbalanced["forces"] = [{"label": "Push", "magnitude": 5, "angle_deg": 0}]
    model, _ = validate_params("vector_forces", unbalanced)
    data = registry["vector_forces"].scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert data["resultant"]["zero"] is False and data["resultant"]["dx"] == pytest.approx(1.0)


# --- validate_spec --------------------------------------------------------------------------------


def test_validate_spec_template_ok_and_mismatch() -> None:
    spec = ManimSpec(template="equation_steps", params=example("equation_steps"))
    assert validate_spec(spec, 4) == []
    assert validate_spec(spec) == []
    problems = validate_spec(spec, 3)
    assert len(problems) == 1 and problems[0].startswith("manim.beats_steps_mismatch:")


def test_validate_spec_unknown_and_invalid() -> None:
    assert validate_spec(ManimSpec(template="nope"), 2)[0].startswith("manim.template_unknown:")
    bad = ManimSpec(template="wave", params={"steps": []})
    problems = validate_spec(bad, 1)
    assert problems and all(p.startswith("manim.params_invalid:") for p in problems)


def test_validate_spec_freeform() -> None:
    ok = "from manim import *\nclass S(AadhiScene):\n    def construct(self):\n        self.play_step(0, Create(Circle()))\n"
    assert validate_spec(ManimSpec(code=ok), 2) == []
    evil = ok + "open('x')\n"
    assert validate_spec(ManimSpec(code=evil), 2)[0].startswith("manim.code_forbidden:")
    unsynced = "from manim import *\nclass S(AadhiScene):\n    def construct(self):\n        self.play(Create(Circle()))\n"
    assert validate_spec(ManimSpec(code=unsynced), 3)[0].startswith("manim.beats_steps_mismatch:")
    assert validate_spec(ManimSpec(code=unsynced), 1) == []


# --- edge-case variants ---------------------------------------------------------------------------

from tests.manim.variants import VARIANTS  # noqa: E402


@pytest.mark.parametrize("key", sorted(VARIANTS))
@pytest.mark.parametrize("target", ["fullscreen", "panel"])
def test_variants_validate_compile_and_pass_the_guard(key: str, target: str) -> None:
    name, params = VARIANTS[key]
    model, problems = validate_params(name, copy.deepcopy(params))
    assert problems == [] and model is not None
    assert registry[name].step_count(model) >= 1
    source = registry[name].render_source(model, target=target, language="en-IN")
    ast.parse(source)
    assert check_template_source(source) == []


def test_variant_step_counts() -> None:
    def steps(key: str) -> int:
        name, params = VARIANTS[key]
        return registry[name].step_count(validate_params(name, copy.deepcopy(params))[0])

    assert steps("bar_negative") == 3  # two bar steps + highlight
    assert steps("bar_many") == 10
    assert steps("tt_xor3") == 3 and steps("tt_not") == 2 and steps("tt_majority") == 3 and steps("tt_nand4") == 2
    assert steps("vf_ball") == 7 and steps("timeline_8") == 8 and steps("block_wide") == 6


def test_block_diagram_manual_layout_is_respected() -> None:
    template = registry["block_diagram"]
    name, params = VARIANTS["block_feedback"]
    model, _ = validate_params(name, copy.deepcopy(params))
    wide = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert wide["layout"]["ctl"] == {"gx": 0.0, "gy": 1.0}, "direction does not transpose a manual grid"
    assert wide["layout"]["sense"] == {"gx": 1.0, "gy": 0.0}
    tall = template.scene_data(model, target="panel", language="en-IN")  # type: ignore[attr-defined]
    assert tall["layout"] == wide["layout"], "a square manual grid already fits a panel"
    flat = copy.deepcopy(params)
    for k, node in enumerate(flat["nodes"]):
        node.update(column=k, row=0)
    model, problems = validate_params(name, flat)
    assert not problems
    panel = template.scene_data(model, target="panel", language="en-IN")  # type: ignore[attr-defined]
    assert (panel["grid_w"], panel["grid_h"]) == (1, 4), "a wide manual grid is transposed for portrait panels"
    down = example("block_diagram")
    down["direction"] = "down"
    model, _ = validate_params("block_diagram", down)
    auto = template.scene_data(model, target="fullscreen", language="en-IN")  # type: ignore[attr-defined]
    assert (auto["grid_w"], auto["grid_h"]) == (1, 4), "automatic layouts flow down when asked"


# --- PARAMS is data: labels may look like paths, URLs or dunder names ---------------------------------

NASTY = ["/home/__init__()", "https://a.io/x", "C:\\Users\\x", "../.env", "Python __main__", "{0.gi_frame}"]


def _string_leaves(value: Any, path: tuple = ()) -> list[tuple]:
    if isinstance(value, dict):
        return [leaf for k, v in value.items() for leaf in _string_leaves(v, (*path, k))]
    if isinstance(value, list):
        return [leaf for i, v in enumerate(value) for leaf in _string_leaves(v, (*path, i))]
    return [path] if isinstance(value, str) else []


def _set(data: Any, path: tuple, value: str) -> None:
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value


@pytest.mark.parametrize("name", NAMES)
def test_every_text_field_accepts_path_url_and_dunder_labels(name: str) -> None:
    """Any string the params model accepts must also pass the template-source guard (no silent dead end)."""
    params = example(name)
    replaced = 0
    for leaf in _string_leaves(params):
        for nasty in NASTY:
            trial = copy.deepcopy(params)
            _set(trial, leaf, nasty)
            if validate_params(name, trial)[0] is not None:
                params = trial
                replaced += 1
                break
    model, problems = validate_params(name, params)
    assert model is not None, problems
    assert replaced >= 1, f"{name}: no free-text field found"
    for target in ("fullscreen", "panel"):
        source = registry[name].render_source(model, target=target, language="en-IN")
        assert check_template_source(source) == [], (name, target)
    steps = registry[name].step_count(model)
    assert validate_spec(ManimSpec(template=name, params=params), steps) == []

