"""Batch-4 regression list, server routes (design §10.3, B0 item 11): one test for each fix listed under "Batch 4:
review fixes" in ``docs/INTEGRATION_ANDRYAN.md``, each from an angle the batch's own tests do not take. The job,
render and source-report fixes are in ``tests/pipeline/test_batch4_regressions.py``; the Studio fixes in
``web/tests/studio/batch4Regressions.test.js``."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from sqlalchemy import func, select

from aadhi import changes
from aadhi.models import Job, ProjectVersion
from tests.api.factories import add_project


@pytest.fixture(autouse=True)
def _fresh_cache():
    changes.clear_cache()
    yield
    changes.clear_cache()


def stored_issue(code: str, severity: str, source: str, scene_id: str | None) -> dict[str, Any]:
    return {"code": code, "severity": severity, "source": source, "scene_id": scene_id, "fixable": source == "critic",
            "message": f"{code} on {scene_id}"}


def generated_lecture(api, asset_store, screenplay: dict[str, Any] | None = None, issues: list[dict] | None = None):
    """A built lecture whose generated snapshot is its screenplay at revision 1 (as ``generate_lecture`` stores it)."""
    from aadhi.schemas.screenplay import Screenplay

    alice, c = api.editor("alice")
    with api.db() as db:
        sp = Screenplay.model_validate(screenplay) if screenplay is not None else None
        project, version = add_project(db, alice, screenplay=sp)
        v = db.get(ProjectVersion, version.id)
        v.generation_meta = {changes.SNAPSHOT_META_KEY: changes.store_snapshot(asset_store, dict(v.screenplay))}
        if issues is not None:
            v.set_issues(issues)
        db.commit()
    return c, version


def put(c, vid: int, screenplay: dict[str, Any], revision: int) -> dict[str, Any]:
    r = c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": screenplay, "revision": revision})
    assert r.status_code == 200, r.text
    return r.json()


# --- "every issue of a hidden scene is served and counted as a note ... (PUT /screenplay, revert, the version detail,
# the orchestrator's issue_counts) and stored at its real severity" --------------------------------------------------


def test_revert_serves_and_counts_a_hidden_scenes_issues_as_notes_and_stores_them_as_they_are(api, asset_store):
    kept = [stored_issue("critic.factual", "error", "critic", "s2"),
            stored_issue("assets.tts_failed", "error", "assets", "s2"),
            stored_issue("system.translate_failed", "warning", "system", "s2")]
    c, version = generated_lecture(api, asset_store, issues=kept)
    sp = copy.deepcopy(c.get(f"/api/versions/{version.id}").json()["screenplay"])
    sp["scenes"][1]["hidden"] = True  # s2 skipped in the video
    sp["scenes"][0]["title"] = "Teacher title"  # s1 edited, to be put back
    put(c, version.id, sp, 1)
    r = c.post(f"/api/versions/{version.id}/scenes/s1/revert", json={"revision": 2})
    assert r.status_code == 200, r.text
    body = r.json()
    served = {(i["code"], i["scene_id"]): i["severity"] for i in body["issues"] if i["source"] != "lint"}
    assert served == {("critic.factual", "s2"): "info", ("assets.tts_failed", "s2"): "info",
                      ("system.translate_failed", "s2"): "info"}
    # counted as served: the hidden scene's error and warning are notes (a lecture-wide length warning may remain)
    tally = {sev: sum(1 for i in body["issues"] if i["severity"] == sev) for sev in ("error", "warning", "info")}
    assert body["version"]["issue_counts"] == tally and tally["error"] == 0
    assert not [i for i in body["issues"] if i["severity"] != "info" and i["scene_id"] == "s2"]
    detail = c.get(f"/api/versions/{version.id}").json()
    assert detail["screenplay"]["scenes"][0]["title"] == "Scene 1" and detail["screenplay"]["scenes"][1]["hidden"]
    assert all(i["severity"] == "info" for i in detail["issues"] if i["scene_id"] == "s2")
    project_row = next(p for p in c.get("/api/projects").json()["items"] if p["id"] == detail["project_id"])
    assert project_row["current_version"]["issue_counts"]["error"] == 0  # the project list counts them as notes too
    with api.db() as db:  # stored at their real severity: showing the scene again brings them back
        stored = {(i["code"], i["scene_id"]): i["severity"] for i in db.get(ProjectVersion, version.id).issues}
    assert stored[("critic.factual", "s2")] == "error" and stored[("system.translate_failed", "s2")] == "warning"


# --- "the pacing run of dense boards and the abbreviation first-use / spelled-out checks skip hidden scenes (labels keep
# the editor's numbering); board.reading_time counts min_seconds" -----------------------------------------------------


def scene(sid: str, title: str, texts: list[str], narration: str = "This is a short clean beat.",
          **kw: Any) -> dict[str, Any]:
    board = [{"id": f"{sid}-i{n}", "kind": "bullet", "text": t} for n, t in enumerate(texts, 1)]
    beats = [{"id": f"{sid}-b1", "narration": narration, "board_item_id": board[0]["id"]}]
    return {"id": sid, "type": "content", "title": title, "board": board, "beats": beats, **kw}


def lint(c, vid: int, scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    r = c.post(f"/api/versions/{vid}/lint", json={"screenplay": {"session_title": "Pacing", "scenes": scenes}})
    assert r.status_code == 200, r.text
    return r.json()["issues"]


def test_the_lint_route_skips_hidden_scenes_in_pacing_and_abbreviation_checks_and_counts_the_hold(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        _, version = add_project(db, alice)
    dense = [f"point {n}" for n in range(5)]
    issues = lint(c, version.id, [scene("d1", "One", dense), scene("lh", "Light", ["one point"], hidden=True),
                                  scene("d2", "Two", dense)])
    runs = [i for i in issues if i["code"] == "pacing.dense_run"]
    assert [i["scene_id"] for i in runs] == ["d2"], runs  # the skipped light scene does not break the run
    assert runs[0]["message"].startswith("Scenes 1 and 3 are dense boards")  # numbered as the editor numbers them
    assert all(i["severity"] == "info" for i in issues if i["scene_id"] == "lh")
    shown = lint(c, version.id, [scene("d1", "One", dense), scene("lh", "Light", ["one point"]), scene("d2", "Two", dense)])
    assert not [i for i in shown if i["code"] == "pacing.dense_run"]

    spelled = scene("h", "Basics", ["Machine Learning (ML) finds patterns in data."], hidden=True)
    used = scene("s", "Models", ["ML models improve with more examples."])
    undefined = [i for i in lint(c, version.id, [spelled, used]) if i["code"] == "terminology.abbreviation_undefined"]
    assert [i["scene_id"] for i in undefined] == ["s"]  # spelled out only where nothing plays

    wordy = [" ".join(["word"] * 20)] * 5  # about 30 s of reading on a board narrated in one word
    held = lint(c, version.id, [scene("w", "Wordy", wordy, narration="Short.", min_seconds=60)])
    assert not [i for i in held if i["code"] == "board.reading_time"]
    plain = lint(c, version.id, [scene("w", "Wordy", wordy, narration="Short.")])
    assert [i["scene_id"] for i in plain if i["code"] == "board.reading_time"] == ["w"]


# --- "POST /build naming only hidden scenes is 409 action_unavailable (a regeneration of a hidden scene is still
# allowed)" ------------------------------------------------------------------------------------------------------------


def test_building_only_hidden_scenes_is_refused_before_any_job_while_regenerating_one_is_allowed(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        _, version = add_project(db, alice)
    sp = copy.deepcopy(c.get(f"/api/versions/{version.id}").json()["screenplay"])
    sp["scenes"][1]["hidden"] = True
    sp["scenes"][2]["hidden"] = True
    put(c, version.id, sp, 1)

    def jobs() -> int:
        with api.db() as db:
            return int(db.execute(select(func.count()).select_from(Job).where(Job.version_id == version.id)).scalar_one())

    for named in (["s2"], ["q1", "s2"], ["s2", "s2"]):
        r = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": named})
        assert r.status_code == 409 and r.json()["code"] == "action_unavailable", (named, r.text)
        assert "skipped in the video" in r.json()["detail"]
    assert jobs() == 0  # nothing queued, nothing charged
    unknown = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s2", "nope"]})
    assert unknown.status_code == 422  # an unknown scene is still a validation error first
    regen = c.post(f"/api/versions/{version.id}/scenes/s2/regenerate", json={"instructions": "Shorter."})
    assert regen.status_code == 202, regen.text  # rewrites the text the teacher may show again
    assert regen.json()["job"]["kind"] == "regenerate_scene" and jobs() == 1


# --- "Revert in place puts a scene back in its chapter's list when its chapter_id differs (one chapter lists it)" ----


def chaptered() -> dict[str, Any]:
    from tests.api.factories import screenplay_dict

    data = screenplay_dict(n_content=3)
    data["chapters"] = [{"id": "ch1", "title": "Basics", "scene_ids": ["s1", "s2"]},
                        {"id": "ch2", "title": "Practice", "scene_ids": ["s3", "q1"]}]
    for s in data["scenes"]:
        s["chapter_id"] = "ch1" if s["id"] in ("s1", "s2") else "ch2"
    return data


def test_reverting_through_the_route_lists_the_scene_in_its_generated_chapter_once(api, asset_store):
    c, version = generated_lecture(api, asset_store, screenplay=chaptered())
    moved = copy.deepcopy(c.get(f"/api/versions/{version.id}").json()["screenplay"])
    next(s for s in moved["scenes"] if s["id"] == "s2")["chapter_id"] = "ch2"  # the editor's "Change chapter"
    moved["chapters"][0]["scene_ids"].remove("s2")
    moved["chapters"][1]["scene_ids"].insert(0, "s2")
    put(c, version.id, moved, 1)
    rows = {r["scene_id"]: r for r in c.get(f"/api/versions/{version.id}/changes").json()["scenes"]}
    assert rows["s2"]["status"] == "edited" and rows["s2"]["fields_changed"] == ["chapter_id"]
    r = c.post(f"/api/versions/{version.id}/scenes/s2/revert", json={"revision": 2, "to": "generated"})
    assert r.status_code == 200, r.text
    saved = c.get(f"/api/versions/{version.id}").json()["screenplay"]
    assert [(ch["id"], ch["scene_ids"]) for ch in saved["chapters"]] == [("ch1", ["s1", "s2"]), ("ch2", ["s3", "q1"])]
    assert next(s for s in saved["scenes"] if s["id"] == "s2")["chapter_id"] == "ch1"
    listed = [sid for ch in saved["chapters"] for sid in ch["scene_ids"]]
    assert sorted(listed) == sorted(s["id"] for s in saved["scenes"])  # every scene listed exactly once
    assert all(row["status"] == "unchanged" for row in c.get(f"/api/versions/{version.id}/changes").json()["scenes"])
