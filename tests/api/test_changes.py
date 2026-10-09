"""GET /api/versions/{vid}/changes (+ /{scene_id}) and POST .../scenes/{scene_id}/revert: what the teacher changed
since Aadhi wrote the lecture, and putting a scene back (``aadhi.changes``)."""

from __future__ import annotations

import copy

import pytest

from aadhi import changes
from aadhi.models import ProjectVersion
from tests.api.factories import add_job, add_project, make_screenplay


@pytest.fixture(autouse=True)
def _fresh_cache():
    changes.clear_cache()
    yield
    changes.clear_cache()


def setup(api, asset_store, *, snapshot: bool = True, history: dict | None = None, user: str = "alice"):
    """A built lecture whose generated snapshot is its screenplay at revision 1."""
    alice, c = api.editor(user)
    with api.db() as db:
        project, version = add_project(db, alice)
        v = db.get(ProjectVersion, version.id)
        meta: dict = {}
        if snapshot:
            meta[changes.SNAPSHOT_META_KEY] = changes.store_snapshot(asset_store, dict(v.screenplay))
        if history:
            meta["scene_history"] = history
        v.generation_meta = meta
        db.commit()
    return alice, c, project, version


def save(c, vid: int, screenplay: dict, revision: int) -> dict:
    r = c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": screenplay, "revision": revision})
    assert r.status_code == 200, r.text
    return r.json()


def current(c, vid: int) -> dict:
    return c.get(f"/api/versions/{vid}").json()


def test_changes_of_an_edited_lecture(api, asset_store):
    _, c, _, version = setup(api, asset_store)
    sp = copy.deepcopy(current(c, version.id)["screenplay"])
    sp["scenes"][0]["title"] = "Teacher title"
    del sp["scenes"][1]
    save(c, version.id, sp, 1)
    r = c.get(f"/api/versions/{version.id}/changes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert [(s["scene_id"], s["status"]) for s in body["scenes"]] == [
        ("s1", "edited"), ("s2", "removed"), ("q1", "unchanged")]
    assert body["scenes"][0]["fields_changed"] == ["title"]
    assert set(body["scenes"][0]) == {"scene_id", "status", "generated_index", "current_index", "fields_changed",
                                      "history"}
    detail = c.get(f"/api/versions/{version.id}/changes/s2").json()
    assert detail["status"] == "removed" and detail["generated"]["title"] == "Scene 2" and detail["current"] is None
    assert c.get(f"/api/versions/{version.id}/changes/zzz").status_code == 404


def test_imported_lecture_has_no_generated_version(api, asset_store):
    _, c, _, version = setup(api, asset_store, snapshot=False)
    body = c.get(f"/api/versions/{version.id}/changes").json()
    assert body["available"] is False and {s["status"] for s in body["scenes"]} == {"unchanged"}
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 1, "to": "generated"})
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_revert_puts_the_generated_scene_back_as_one_saved_edit(api, asset_store):
    _, c, _, version = setup(api, asset_store)
    sp = copy.deepcopy(current(c, version.id)["screenplay"])
    sp["scenes"][0]["title"] = "Teacher title"
    save(c, version.id, sp, 1)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 2, "to": "generated"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["revision"] == 3 and body["scene_id"] == "s1"
    assert body["version"]["revision"] == 3 and body["version"]["timeline_stale"] is True
    detail = current(c, version.id)
    assert detail["screenplay"]["scenes"][0]["title"] == "Scene 1" and detail["revision"] == 3
    assert c.get(f"/api/versions/{version.id}/changes").json()["scenes"][0]["status"] == "unchanged"


def test_revert_restores_a_removed_scene_at_a_position(api, asset_store):
    _, c, _, version = setup(api, asset_store)
    sp = copy.deepcopy(current(c, version.id)["screenplay"])
    del sp["scenes"][1]
    save(c, version.id, sp, 1)
    r = c.post(f"/api/versions/{version.id}/scenes/s2/revert", json={"revision": 2})
    assert r.status_code == 200, r.text
    assert [s["id"] for s in current(c, version.id)["screenplay"]["scenes"]] == ["s1", "s2", "q1"]
    without_s1 = [s for s in current(c, version.id)["screenplay"]["scenes"] if s["id"] != "s1"]
    save(c, version.id, {**sp, "scenes": without_s1}, 3)  # s1 removed too: put it back past the end (clamped)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 4, "position": 5})
    assert r.status_code == 200, r.text
    assert [s["id"] for s in current(c, version.id)["screenplay"]["scenes"]] == ["s2", "q1", "s1"]


def test_revert_to_a_regenerated_scene_history_entry(api, asset_store):
    older = make_screenplay().scene_by_id("s1").model_dump(mode="json")
    older["title"] = "Before the rewrite"
    history = {"s1": [{"at": "2026-10-01T00:00:00+00:00", "instructions": "shorter", "revision": 1, "scene": older}]}
    _, c, _, version = setup(api, asset_store, history=history)
    rows = c.get(f"/api/versions/{version.id}/changes").json()["scenes"]
    assert rows[0]["history"] == 1
    detail = c.get(f"/api/versions/{version.id}/changes/s1").json()
    assert detail["history"][0]["instructions"] == "shorter" and detail["history"][0]["scene"]["title"]
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 1, "to": "history", "history_index": 0})
    assert r.status_code == 200, r.text
    assert current(c, version.id)["screenplay"]["scenes"][0]["title"] == "Before the rewrite"
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 2, "to": "history", "history_index": 3})
    assert r.status_code == 404


def test_revert_conflicts(api, asset_store):
    alice, c, project, version = setup(api, asset_store)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 9})
    assert r.status_code == 409 and r.json()["code"] == "revision_conflict"
    assert r.json()["current_revision"] == 1
    with api.db() as db:
        job = add_job(db, kind="build_assets", status="running", user=alice, project=project, version=version)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 1})
    assert r.status_code == 409 and r.json()["code"] == "version_busy" and r.json()["job_id"] == job.id
    assert current(c, version.id)["revision"] == 1  # nothing written


def test_revert_validates_its_body(api, asset_store):
    _, c, _, version = setup(api, asset_store)
    for body in ({"revision": 0}, {"revision": 1, "to": "nowhere"}, {"revision": 1, "extra": 1},
                 {"revision": 1, "position": -1}):
        r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json=body)
        assert r.status_code == 422, body
    r = c.post(f"/api/versions/{version.id}/scenes/s9/revert", json={"revision": 1})
    assert r.status_code == 404  # never generated


def test_changes_and_revert_are_private_to_the_owner(api, asset_store):
    _, _, _, version = setup(api, asset_store)
    _, bob = api.editor("bob")
    assert bob.get(f"/api/versions/{version.id}/changes").status_code == 404
    assert bob.get(f"/api/versions/{version.id}/changes/s1").status_code == 404
    assert bob.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 1}).status_code == 404
    api.user("root", role="admin")
    admin = api.login("root")
    assert admin.get(f"/api/versions/{version.id}/changes").status_code == 200


def test_revert_needs_the_csrf_header(api, asset_store):
    _, _, _, version = setup(api, asset_store)
    c = api.login("alice", browser=False)
    c.headers.update({"Origin": "http://testserver"})
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 1})
    assert r.status_code == 403 and r.json()["code"] == "csrf"


def test_snapshot_is_never_served_with_the_version_screenplay(api, asset_store):
    _, c, _, version = setup(api, asset_store)
    body = current(c, version.id)
    assert "generated" not in body and body["screenplay"] == make_screenplay().model_dump(mode="json")
