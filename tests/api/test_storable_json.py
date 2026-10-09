"""Unstorable JSON (NaN / Infinity / 1e999, lone surrogates) is refused with a 422 before anything is written,
and documents stored before that check still load (sanitised) instead of failing with a 500."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from aadhi.models import Project, ProjectVersion
from tests.api.factories import add_project, screenplay_dict

JSON_HEADERS = {"Content-Type": "application/json"}


def setup_version(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    return alice, c, project, version


def with_panel(panel: dict) -> dict:
    sp = screenplay_dict()
    sp["scenes"][0]["side_panel"] = panel
    return sp


def raw_body(screenplay: dict, *, revision: int | None = 1, **literals: str) -> bytes:
    """JSON text of the body with placeholder strings replaced by raw literals (``NaN``, ``1e999`` ...)."""
    body: dict = {"screenplay": screenplay}
    if revision is not None:
        body["revision"] = revision
    text = json.dumps(body)
    for name, literal in literals.items():
        text = text.replace(f'"@{name}"', literal)
    return text.encode("utf-8")


CHART = {"kind": "chart", "chart": {"labels": ["a", "b"], "datasets": [{"label": "x", "data": [1, "@v"]}]}}
GRAPH_POINT = {"kind": "graph", "graph": {"points": [{"x": "@v", "y": 1}]}}
GRAPH_RANGE = {"kind": "graph", "graph": {"functions": [{"expr": "x^2"}], "x_range": ["@v", 5]}}
MODEL_3D = {"kind": "model_3d", "model_3d": {"primitives": [{"shape": "sphere", "position": [0, "@v", 0]}]}}
MANIM = {"kind": "manim", "manim": {"template": "equation_steps", "params": {"amplitude": "@v"}}}


@pytest.mark.parametrize("panel", [CHART, GRAPH_POINT, GRAPH_RANGE, MODEL_3D, MANIM],
                         ids=["chart", "graph_point", "graph_range", "model_3d", "manim_params"])
@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_put_screenplay_refuses_non_finite_numbers(api, panel, literal):
    _, c, _, version = setup_version(api)
    r = c.put(f"/api/versions/{version.id}/screenplay", content=raw_body(with_panel(panel), v=literal),
              headers=JSON_HEADERS)
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "validation"
    assert body["detail"][0]["loc"][:2] == ["body", "screenplay"]
    detail = c.get(f"/api/versions/{version.id}")
    assert detail.status_code == 200
    assert detail.json()["revision"] == 1 and detail.json()["screenplay"]["scenes"][0]["side_panel"] is None


def test_put_screenplay_refuses_lone_surrogates(api):
    _, c, _, version = setup_version(api)
    sp = screenplay_dict()
    sp["scenes"][0]["title"] = "@t"
    sp["scenes"][1]["side_panel"] = {"kind": "manim", "manim": {"template": "equation_steps", "params": {"label": "@m"}}}
    for literals in ({"t": '"Ohm \\ud800"', "m": '"x"'}, {"t": '"Ohm"', "m": '"\\udfff"'}):
        r = c.put(f"/api/versions/{version.id}/screenplay", content=raw_body(sp, **literals), headers=JSON_HEADERS)
        assert r.status_code == 422, r.text
        assert r.json()["code"] == "validation"
    detail = c.get(f"/api/versions/{version.id}")
    assert detail.status_code == 200 and detail.json()["revision"] == 1
    # a valid surrogate pair is an ordinary character
    sp["scenes"][0]["title"] = "Ohm \U0001f600"
    del sp["scenes"][1]["side_panel"]
    ok = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1})
    assert ok.status_code == 200, ok.text
    assert c.get(f"/api/versions/{version.id}").json()["screenplay"]["scenes"][0]["title"] == "Ohm \U0001f600"


@pytest.mark.parametrize("path", ["lint", "timeline/preview"])
def test_lint_and_preview_refuse_unstorable_values(api, path):
    _, c, _, version = setup_version(api)
    url = f"/api/versions/{version.id}/{path}"
    nan = c.post(url, content=raw_body(with_panel(CHART), revision=None, v="NaN"), headers=JSON_HEADERS)
    assert nan.status_code == 422 and nan.json()["code"] == "validation"
    sp = with_panel(MANIM)
    surrogate = c.post(url, content=raw_body(sp, revision=None, v='"\\ud83d"'), headers=JSON_HEADERS)
    assert surrogate.status_code == 422 and "surrogate" in json.dumps(surrogate.json())


def test_version_action_bodies_refuse_lone_surrogates(api):
    _, c, project, version = setup_version(api)
    build = c.post(f"/api/versions/{version.id}/build", content=b'{"scene_ids": ["\\ud800"]}', headers=JSON_HEADERS)
    assert build.status_code == 422 and build.json()["code"] == "validation"
    regen = c.post(f"/api/versions/{version.id}/scenes/s1/regenerate", content=b'{"instructions": "x\\udc00"}',
                   headers=JSON_HEADERS)
    assert regen.status_code == 422
    dup = c.post(f"/api/versions/{version.id}/duplicate", content=b'{"label": "\\ud800"}', headers=JSON_HEADERS)
    assert dup.status_code == 422
    plan = c.post(f"/api/versions/{version.id}/plan", content=b'{"plan": {"session_title": "\\ud800"}}',
                  headers=JSON_HEADERS)
    assert plan.status_code == 422
    with api.db() as db:
        assert len(db.execute(select(ProjectVersion).where(ProjectVersion.project_id == project.id)).all()) == 1


def test_options_refuse_unstorable_values(api):
    _, c = api.editor("alice")
    created = c.post(
        "/api/projects",
        files={"file": ("notes.txt", b"Ohm's law: V = I R.\n" * 4, "text/plain")},
        data={"options": '{"extra_instructions": "Use \\ud800"}'},
    )
    assert created.status_code == 422, created.text
    assert created.json()["detail"][0]["loc"] == ["options", "extra_instructions"]
    nan = c.post(
        "/api/projects",
        files={"file": ("notes.txt", b"Ohm's law: V = I R.\n" * 4, "text/plain")},
        data={"options": '{"target_minutes": NaN}'},
    )
    assert nan.status_code == 422
    with api.db() as db:
        assert db.execute(select(Project)).first() is None


def test_regenerate_options_refuse_lone_surrogates(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, _ = add_project(db, alice)
    r = c.post(f"/api/projects/{project.id}/regenerate", content=b'{"options": {"session_title": "\\ud800"}}',
               headers=JSON_HEADERS)
    assert r.status_code == 422 and r.json()["code"] == "validation"


def test_import_refuses_lone_surrogates(api):
    _, c = api.editor("alice")
    text = json.dumps(screenplay_dict(title="@t")).replace('"@t"', '"Ohm \\ud800"').encode()
    r = c.post("/api/projects/import", files={"file": ("lecture.json", text, "application/json")})
    assert r.status_code == 422, r.text
    assert r.json()["detail"][0]["loc"] == ["file", "session_title"]
    with api.db() as db:
        assert db.execute(select(Project)).first() is None


# ---------------------------------------------------------------------------
# documents stored before the check (only possible on SQLite) still load
# ---------------------------------------------------------------------------


def _store_legacy(api, version_id: int, *, screenplay: dict | None = None, timeline_patch=None, settings=None) -> None:
    with api.db() as db:
        v = db.get(ProjectVersion, version_id)
        if screenplay is not None:
            v.screenplay = screenplay
        if timeline_patch is not None:
            timeline = json.loads(json.dumps(v.timeline))
            timeline_patch(timeline)
            v.timeline = timeline
        if settings is not None:
            db.get(Project, v.project_id).settings = settings
        db.commit()


def test_legacy_non_finite_numbers_and_surrogates_load_sanitised(api):
    _, c, project, version = setup_version(api)
    legacy = with_panel({"kind": "chart", "chart": {"labels": ["a", "b"],
                                                    "datasets": [{"label": "x", "data": [1.0, float("nan")]}]}})
    legacy["scenes"][1]["title"] = "Scene \ud800"

    def patch(timeline: dict) -> None:
        timeline["scenes"][0]["audio_duration"] = float("inf")
        timeline["scenes"][1]["title"] = "Scene \udc00"

    _store_legacy(api, version.id, screenplay=legacy, timeline_patch=patch,
                  settings={"language": "en-IN", "extra_instructions": "Keep \ud800 it short"})
    detail = c.get(f"/api/versions/{version.id}")
    assert detail.status_code == 200, detail.text
    scenes = detail.json()["screenplay"]["scenes"]
    assert scenes[0]["side_panel"]["chart"]["datasets"][0]["data"] == [1.0, None]
    assert scenes[1]["title"] == "Scene �"
    exported = c.get(f"/api/versions/{version.id}/export.json")
    assert exported.status_code == 200 and exported.json()["scenes"][1]["title"] == "Scene �"
    assert c.get(f"/api/versions/{version.id}/companion.md").status_code == 200
    timeline = c.get(f"/api/versions/{version.id}/timeline")
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["scenes"][0]["audio_duration"] == 0.0
    assert timeline.json()["scenes"][1]["title"] == "Scene �"
    assert c.get(f"/api/versions/{version.id}/chapters.txt").status_code in (200, 404)
    project_detail = c.get(f"/api/projects/{project.id}")
    assert project_detail.status_code == 200
    assert project_detail.json()["options"]["extra_instructions"] == "Keep � it short"
    # the teacher can save the sanitised screenplay again once the gap is filled
    edited = detail.json()["screenplay"]
    edited["scenes"][0]["side_panel"]["chart"]["datasets"][0]["data"] = [1.0, 2.0]
    saved = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert saved.status_code == 200, saved.text

