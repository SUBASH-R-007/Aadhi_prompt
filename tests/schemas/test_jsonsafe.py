"""Storable JSON: schemas refuse NaN / Infinity / 1e999; helpers find, sanitise and repair unstorable values."""

from __future__ import annotations

import json
import math

import pytest
from pydantic import ValidationError

from aadhi.schemas.jsonsafe import (
    NON_FINITE_MESSAGE,
    SURROGATE_MESSAGE,
    check_storable,
    json_safe,
    unstorable,
    validate_stored,
)
from aadhi.schemas.screenplay import (
    ChartDataset,
    GraphPoint,
    GraphSpec,
    ManimSpec,
    Primitive3D,
    Screenplay,
    SidePanel,
)
from aadhi.schemas.timeline import Timeline

NAN, INF = float("nan"), float("inf")


def chart_screenplay(data: list) -> dict:
    return {
        "scenes": [{
            "id": "s1", "type": "content", "title": "Scene",
            "beats": [{"id": "b1", "narration": "Look at the chart."}],
            "side_panel": {"kind": "chart", "chart": {"labels": ["a", "b"], "datasets": [{"label": "x", "data": data}]}},
        }],
    }


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_float_fields_refuse_non_finite_numbers_from_json(raw):
    """Python's json reads these as nan / inf; every typed float refuses them with ``finite_number``."""
    cases = [
        (GraphPoint, f'{{"x": {raw}, "y": 1}}'),
        (ChartDataset, f'{{"data": [1, {raw}]}}'),
        (GraphSpec, f'{{"points": [{{"x": 1, "y": 2}}], "x_range": [{raw}, 5]}}'),
        (GraphSpec, f'{{"points": [{{"x": 1, "y": 2}}], "y_range": [0, {raw}]}}'),
        (Primitive3D, f'{{"shape": "box", "position": [0, {raw}, 0]}}'),
        (Primitive3D, f'{{"shape": "box", "size": [{raw}]}}'),
    ]
    for model, text in cases:
        with pytest.raises(ValidationError) as exc:
            model.model_validate(json.loads(text))
        assert {e["type"] for e in exc.value.errors()} == {"finite_number"}, (model.__name__, text)


def test_finite_values_still_validate():
    assert GraphPoint.model_validate({"x": -1e308, "y": 0}).x == -1e308
    assert ChartDataset(data=[0.5, 2, -3]).data == [0.5, 2.0, -3.0]
    spec = GraphSpec.model_validate({"functions": [{"expr": "x^2"}], "x_range": [-2, 2], "y_range": None})
    assert spec.x_range == (-2.0, 2.0) and spec.y_range is None


def test_screenplay_and_timeline_refuse_non_finite_numbers():
    with pytest.raises(ValidationError) as exc:
        Screenplay.model_validate(chart_screenplay([1.0, NAN]))
    err = exc.value.errors()[0]
    assert err["type"] == "finite_number" and err["loc"][-2:] == ("data", 1)
    assert Screenplay.model_validate(chart_screenplay([1.0, 2.0])).scenes[0].side_panel.chart.datasets[0].data == [1.0, 2.0]
    with pytest.raises(ValidationError):
        Timeline.model_validate({"total_duration": INF})


def test_manim_params_refuse_non_finite_numbers_and_lone_surrogates():
    ok = ManimSpec(template="equation_steps", params={"steps": [{"latex": "a"}], "scale": 1.5})
    assert ok.params["scale"] == 1.5
    for params in ({"scale": NAN}, {"steps": [{"value": INF}]}, {"label": "\ud800"}, {"\udc00": 1}):
        with pytest.raises(ValidationError) as exc:
            ManimSpec(template="equation_steps", params=params)
        assert exc.value.errors()[0]["loc"][0] == "params"  # a bad key is already refused by pydantic itself
    panel = {"kind": "manim", "manim": {"template": "t", "params": json.loads('{"amplitude": NaN}')}}
    with pytest.raises(ValidationError):
        SidePanel.model_validate(panel)


def test_unstorable_finds_the_first_offending_value_with_its_path():
    assert unstorable({"a": [1, 2.5, "text", None, True], "b": {"c": "ok"}}) is None
    assert unstorable({"a": [1, NAN], "b": INF}) == (("a", 1), NON_FINITE_MESSAGE)
    assert unstorable({"t": "x\ud83dy"}) == (("t",), SURROGATE_MESSAGE)
    assert unstorable({"\udfff": 1}) == (("\udfff",), SURROGATE_MESSAGE)
    assert unstorable(json.loads('{"emoji": "\\ud83d\\ude00"}')) is None  # a valid pair decodes to one character
    assert unstorable([[[[-INF]]]]) == ((0, 0, 0, 0), NON_FINITE_MESSAGE)
    deep: list = []
    node = deep
    for _ in range(5000):  # iterative: no recursion limit
        node.append([])
        node = node[0]
    node.append(NAN)
    assert unstorable(deep) is not None
    with pytest.raises(ValueError, match=r"at scenes\.0\.title"):
        check_storable({"scenes": [{"title": "\ud800"}]})


def test_json_safe_sanitises_only_when_needed():
    clean = {"a": [1.5, "x"], "b": None}
    assert json_safe(clean) is clean
    dirty = {"a": [1.0, NAN, INF], "t\ud800": "x\udc00y", "n": {"m": -INF}}
    safe = json_safe(dirty)
    assert safe == {"a": [1.0, None, None], "t�": "x�y", "n": {"m": None}}
    json.dumps(safe, ensure_ascii=False, allow_nan=False).encode("utf-8")  # now servable
    assert math.isnan(dirty["a"][1])  # the input is not modified
    assert json_safe("caf\ud800") == "caf�"


def test_validate_stored_reads_legacy_non_finite_numbers_as_zero():
    data = chart_screenplay([1.0, NAN])
    data["scenes"][0]["side_panel"] = {
        "kind": "graph", "graph": {"points": [{"x": INF, "y": 2}], "x_range": [-INF, 5]},
    }
    sp = validate_stored(Screenplay, data)
    graph = sp.scenes[0].side_panel.graph
    assert graph.points[0].x == 0.0 and graph.x_range == (0.0, 5.0)
    assert math.isinf(data["scenes"][0]["side_panel"]["graph"]["points"][0]["x"])  # input untouched
    data["scenes"][0]["title"] = "Scene \ud800"  # lone surrogates (refused by stripped str fields) read as U+FFFD
    data["scenes"][0]["side_panel"]["graph"]["points"][0]["label"] = "p\udc00"
    sp = validate_stored(Screenplay, data)
    assert sp.scenes[0].title == "Scene �" and sp.scenes[0].side_panel.graph.points[0].label == "p�"
    with pytest.raises(ValidationError):  # other errors are not repaired
        validate_stored(Screenplay, {"scenes": [{"id": "s1", "type": "content", "beats": []}]})
    with pytest.raises(ValidationError):  # a repair that still fails validation raises
        validate_stored(GraphSpec, {"points": [{"x": 1, "y": 1}], "x_range": [0, INF]})


def test_llm_output_with_nan_goes_to_repair_then_drops_the_panel():
    """A structured LLM output carrying NaN fails canonicalisation (the re-ask loop sees it); lenient mode
    drops the invalid panel instead of storing it."""
    from aadhi.pipeline.base import PlannedScene
    from aadhi.pipeline.canonicalize import canonicalize_scene, scene_problems
    from aadhi.pipeline.gen_models import GenBoardScene

    planned = PlannedScene(id="ohm", type="content", goal="Show the data", key_points=["trend"], source_refs=["c0001"],
                           concept_id="ohm", objective_ids=["o1"], side_panel_kind="chart", visual_rationale="see it")
    out = GenBoardScene.model_validate(json.loads(
        '{"title": "Data", "beats": [{"narration": "Look at the chart."}], "side_panel": {"kind": "chart",'
        ' "chart_labels": ["a", "b"], "chart_datasets": [{"label": "x", "data": [1, NaN]}]}}'))
    problems = scene_problems(planned, out)
    assert any(p.startswith("side_panel:") for p in problems), problems
    assert canonicalize_scene(planned, out, strict=False).side_panel is None


def test_stored_document_readers_load_legacy_unstorable_values(app_env):
    """Jobs read versions through ``ProjectVersion.get_*`` / ``VersionSnapshot.get_*``: a row stored before
    NaN and lone surrogates were refused still loads (sanitised) instead of failing every job on it."""
    from aadhi.models import ProjectVersion
    from aadhi.pipeline.dbops import VersionSnapshot

    data = chart_screenplay([1.0, 2.0])
    data["scenes"][0]["side_panel"] = {"kind": "graph", "graph": {"points": [{"x": INF, "y": 2}]}}
    data["scenes"][0]["title"] = "Scene \ud800"
    version = ProjectVersion(id=1, project_id=1, number=1, revision=1, screenplay=data,
                             timeline={"scenes": [], "total_duration": NAN})
    sp = version.get_screenplay()
    assert sp.scenes[0].side_panel.graph.points[0].x == 0.0 and sp.scenes[0].title == "Scene �"
    assert version.get_timeline().total_duration == 0.0
    snap = VersionSnapshot(id=1, project_id=1, status="ready", language="en-IN", revision=1, built_revision=None,
                           source_version_id=None, screenplay=data, manifest=None)
    assert snap.get_screenplay().scenes[0].side_panel.graph.points[0].x == 0.0
    with pytest.raises(ValidationError):  # anything else still fails loudly
        ProjectVersion(id=2, project_id=1, number=2, revision=1, screenplay={"scenes": "x"}).get_screenplay()
