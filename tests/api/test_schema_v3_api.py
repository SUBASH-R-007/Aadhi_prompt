"""Schema v3 at the API (design §6.7; B0): ``DYNAMIC_FEATURES`` write gates (422 ``feature_unavailable``) on
``PUT /screenplay``, the preview, lint and import; the ``schema_version`` marker of stored screenplays; and the 409
``newer_document`` refusal of screenplays and timelines written by a newer build (sent or stored).
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from aadhi.models import ProjectVersion
from aadhi.schemas.features import DYNAMIC_FEATURES
from aadhi.schemas.screenplay import Screenplay
from tests.api.factories import add_project
from tests.schemas.v3_fixtures import classic_screenplay_dict, with_feature


def setup_version(api, screenplay: Screenplay | None = None):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, screenplay=screenplay)
    return c, project, version


def enable(api, *names: str) -> None:
    api.settings.dynamic_features = list(names)


def put(c, vid: int, sp: dict[str, Any], revision: int = 1):
    return c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": sp, "revision": revision})


def stored(api, vid: int) -> dict[str, Any]:
    with api.db() as db:
        return copy.deepcopy(db.get(ProjectVersion, vid).screenplay)


def _import(c, doc: dict[str, Any]):
    return c.post("/api/projects/import", files={"file": ("lecture.json", json.dumps(doc).encode(), "application/json")})


# --- write gates -----------------------------------------------------------------------------------------------------


def test_no_feature_is_enabled_by_default(api) -> None:
    assert api.settings.dynamic_features == []


@pytest.mark.parametrize("feature", DYNAMIC_FEATURES)
def test_put_refuses_a_feature_that_is_not_enabled(api, feature: str) -> None:
    c, _, version = setup_version(api)
    r = put(c, version.id, with_feature(feature))
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["code"] == "feature_unavailable" and body["features"] == [feature]
    assert [e["type"] for e in body["detail"]] == ["feature_unavailable"]
    assert body["detail"][0]["loc"][:2] == ["body", "screenplay"]
    assert feature in body["detail"][0]["msg"]
    assert c.get(f"/api/versions/{version.id}").json()["revision"] == 1  # nothing was saved


def test_enabled_features_are_saved_with_schema_version_3(api) -> None:
    c, _, version = setup_version(api)
    enable(api, "presentation", "items_v3", "cuts")
    doc = with_feature("presentation", with_feature("items_v3", with_feature("cuts")))
    r = put(c, version.id, doc)
    assert r.status_code == 200, r.text
    saved = stored(api, version.id)
    assert saved["schema_version"] == 3
    assert saved["presentation"]["theme_family"] == "teal_coral"
    assert saved["scenes"][0]["beats"][1]["cuts"] == [{"start": 0, "end": 2, "text": "Let us", "mode": "splice"}]
    assert saved["scenes"][0]["board"][-1]["kind"] == "stat"
    served = c.get(f"/api/versions/{version.id}").json()["screenplay"]
    assert served == saved
    # saved back unchanged (the editor's round trip)
    assert put(c, version.id, served, 2).status_code == 200
    assert stored(api, version.id) == saved


def test_only_the_disabled_features_are_reported(api) -> None:
    c, _, version = setup_version(api)
    enable(api, "presentation")
    r = put(c, version.id, with_feature("presentation", with_feature("clip", with_feature("cuts"))))
    assert r.status_code == 422
    assert sorted(r.json()["features"]) == ["clip", "cuts"]


def test_classic_saves_stay_version_2_without_v3_keys(api) -> None:
    c, _, version = setup_version(api)
    edited = classic_screenplay_dict()
    edited["session_title"] = "Ohm's law, revised"
    assert put(c, version.id, edited).status_code == 200
    saved = stored(api, version.id)
    assert saved["schema_version"] == 2
    assert saved == Screenplay.model_validate(edited).model_dump(mode="json")
    for key in ("presentation", "look", "cuts", "overlays", "speaker", "builds_on"):
        assert f'"{key}"' not in json.dumps(saved)


def test_a_feature_the_lecture_already_uses_stays_editable(api) -> None:
    """Switching a feature off again never locks teachers out of the lectures that already use it."""
    c, _, version = setup_version(api, Screenplay.model_validate(with_feature("presentation")))
    assert stored(api, version.id)["schema_version"] == 3
    doc = with_feature("presentation")
    doc["session_title"] = "Edited while the feature is off"
    r = put(c, version.id, doc)
    assert r.status_code == 200, r.text
    r = put(c, version.id, with_feature("presentation", with_feature("cuts")), 2)  # a new feature is still refused
    assert r.status_code == 422 and r.json()["features"] == ["cuts"]


def test_preview_and_lint_refuse_disabled_features_too(api) -> None:
    c, _, version = setup_version(api)
    doc = with_feature("clip")
    r = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": doc})
    assert r.status_code == 422 and r.json()["code"] == "feature_unavailable"
    r = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": doc})
    assert r.status_code == 422 and r.json()["features"] == ["clip"]
    enable(api, "items_v3")
    r = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": with_feature("items_v3")})
    assert r.status_code == 200, r.text


def test_import_refuses_disabled_features_and_marks_enabled_ones(api) -> None:
    _, c = api.editor("alice")
    r = _import(c, with_feature("cuts"))
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "feature_unavailable" and r.json()["detail"][0]["loc"][0] == "file"
    enable(api, "cuts")
    r = _import(c, with_feature("cuts"))
    assert r.status_code == 201, r.text
    saved = stored(api, r.json()["version"]["id"])
    assert saved["schema_version"] == 3 and saved["scenes"][0]["beats"][1]["cuts"][0]["text"] == "Let us"


# --- newer documents ---------------------------------------------------------------------------------------------------


def _assert_newer(r, document: str = "screenplay", version: int = 4) -> None:
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "newer_document"
    assert (body["document"], body["schema_version"], body["reader_version"]) == (document, version, 3)


def test_a_newer_screenplay_sent_by_a_client_is_refused(api) -> None:
    c, _, version = setup_version(api)
    doc = {**classic_screenplay_dict(), "schema_version": 4, "from_the_future": True}
    _assert_newer(put(c, version.id, doc))
    _assert_newer(c.post(f"/api/versions/{version.id}/lint", json={"screenplay": doc}))
    assert c.get(f"/api/versions/{version.id}").json()["revision"] == 1


def test_a_newer_stored_screenplay_is_never_read_or_overwritten(api) -> None:
    c, _, version = setup_version(api)
    newer = {**classic_screenplay_dict(), "schema_version": 4, "from_the_future": {"kept": True}}
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.screenplay = newer
        db.commit()
    _assert_newer(c.get(f"/api/versions/{version.id}"))
    _assert_newer(put(c, version.id, classic_screenplay_dict()))
    _assert_newer(c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": with_feature("cuts")}))
    assert stored(api, version.id) == newer  # untouched, unknown field included


def test_a_newer_stored_manifest_refuses_the_edit_before_saving(api) -> None:
    c, _, version = setup_version(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.asset_manifest = {**(v.asset_manifest or {}), "schema_version": 4}
        db.commit()
    before = stored(api, version.id)
    edited = classic_screenplay_dict()
    edited["session_title"] = "Not saved"
    _assert_newer(put(c, version.id, edited), document="asset manifest")
    assert stored(api, version.id) == before
    with api.db() as db:
        assert db.get(ProjectVersion, version.id).revision == 1


def test_a_newer_stored_timeline_is_refused(api) -> None:
    c, _, version = setup_version(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.timeline = {**v.timeline, "schema_version": 7}
        db.commit()
    _assert_newer(c.get(f"/api/versions/{version.id}/timeline"), document="timeline", version=7)


def test_import_refuses_a_newer_screenplay(api) -> None:
    _, c = api.editor("alice")
    _assert_newer(_import(c, {**classic_screenplay_dict(), "schema_version": 4}))
    exported = {"screenplay": {**classic_screenplay_dict(), "schema_version": 5}}  # an export bundle
    _assert_newer(_import(c, exported), version=5)
