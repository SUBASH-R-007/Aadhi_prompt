"""Hidden scenes and minimum durations through the version routes (save, stale scenes, lint, preview), and the
lint route running the quality analysis once."""

from __future__ import annotations

from aadhi.models import ProjectVersion
from aadhi.schemas.manifest import AssetManifest
from tests.api.factories import add_project, make_screenplay, screenplay_dict


def setup_version(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    return c, version


def test_save_hidden_and_min_seconds_and_stale_scenes_skip_hidden(api):
    from aadhi.pipeline.assets import scene_hash

    c, version = setup_version(api)
    sp = make_screenplay()
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.set_manifest(AssetManifest(scene_hashes={s.id: scene_hash(s, sp.lexicon) for s in sp.scenes if s.id != "s2"}))
        db.commit()
    assert c.get(f"/api/versions/{version.id}").json()["stale_scenes"] == ["s2"]
    edited = screenplay_dict()
    edited["scenes"][1]["hidden"] = True
    edited["scenes"][0]["min_seconds"] = 25
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_scenes"] == [] and body["version"]["timeline_stale"] is True
    assert any(i["code"] == "scene.hidden" and i["scene_id"] == "s2" for i in body["issues"])
    saved = c.get(f"/api/versions/{version.id}").json()["screenplay"]["scenes"]
    assert saved[1]["hidden"] is True and saved[0]["min_seconds"] == 25
    assert "hidden" not in saved[0] and "min_seconds" not in saved[1]  # defaults stay out of the stored JSON
    edited["scenes"][1]["hidden"] = False  # shown again: stale until built
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 2})
    assert r.status_code == 200 and r.json()["stale_scenes"] == ["s2"]


def test_every_scene_hidden_saves_with_a_warning(api):
    c, version = setup_version(api)
    edited = screenplay_dict()
    for s in edited["scenes"]:
        s["hidden"] = True
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 200, r.text
    warning = [i for i in r.json()["issues"] if i["code"] == "lecture.all_scenes_hidden"]
    assert len(warning) == 1 and warning[0]["severity"] == "warning"


def test_min_seconds_out_of_range_is_refused(api):
    c, version = setup_version(api)
    for bad in (0.5, 601, "1e999"):
        edited = screenplay_dict()
        edited["scenes"][0]["min_seconds"] = bad
        r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
        assert r.status_code == 422, (bad, r.text)


def test_preview_leaves_hidden_scenes_out_and_holds_the_scene(api):
    c, version = setup_version(api)
    sp = screenplay_dict()
    plain = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": sp}).json()
    sp["scenes"][1]["hidden"] = True
    sp["scenes"][0]["min_seconds"] = 60
    tl = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": sp}).json()
    assert [s["scene_id"] for s in tl["scenes"]] == ["s1", "q1"] and [s["index"] for s in tl["scenes"]] == [0, 1]
    first = tl["scenes"][0]
    assert first["duration"] == 60 and first["hold_seconds"] == round(60 - plain["scenes"][0]["duration"], 3)
    assert first["beats"] == plain["scenes"][0]["beats"]
    assert "hold_seconds" not in tl["scenes"][1]
    assert all("scene 2" not in cue["text"] for cue in tl["captions"])


def test_lint_runs_the_quality_analysis_once(api, monkeypatch):
    from aadhi.pipeline import quality

    c, version = setup_version(api)
    calls = []
    analyse = quality.analyse

    def counted(sp):
        calls.append(1)
        return analyse(sp)

    monkeypatch.setattr(quality, "analyse", counted)
    sp = screenplay_dict()
    sp["scenes"][0]["board"][0]["text"] = "We use [[Ohm's Law]]."
    sp["scenes"][1]["board"][0]["text"] = "Then [[Ohm's law]] again."
    r = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": sp})
    assert r.status_code == 200, r.text
    assert len(calls) == 1
    body = r.json()
    keys = {(i["code"], i["scene_id"], i["message"]) for i in body["issues"]}
    assert body["quality"]["repairs"] and all((p["code"], p["scene_id"], p["message"]) in keys
                                              for p in body["quality"]["repairs"])
    assert body["quality"]["registry"]["terms"]


def test_lint_runs_a_failing_quality_analysis_only_once(api, monkeypatch):
    from aadhi.pipeline import quality

    c, version = setup_version(api)
    calls = []

    def failing(sp):
        calls.append(1)
        raise RuntimeError("extraction failed")

    monkeypatch.setattr(quality, "analyse", failing)
    r = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": screenplay_dict()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(calls) == 1  # None ("it failed") is not taken for "not given, compute it"
    assert body["quality"] == {"version": 1, "repairs": [], "registry": {}}
    assert not any(i["code"].startswith(("terminology.", "pacing.", "board.reading_time")) for i in body["issues"])


def stored_issue(code, severity, source, scene_id):
    return {"code": code, "severity": severity, "source": source, "scene_id": scene_id, "fixable": source == "critic",
            "message": f"{code} on {scene_id}"}


def test_a_hidden_scenes_stored_issues_are_served_and_counted_as_notes(api):
    c, version = setup_version(api)
    kept = [stored_issue("assets.tts_failed", "error", "assets", "s2"),
            stored_issue("critic.factual", "error", "critic", "s2"),
            stored_issue("assets.media_degraded", "warning", "assets", "s1")]
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.set_issues(kept)
        db.commit()
    edited = screenplay_dict()
    edited["scenes"][1]["hidden"] = True
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    served = {(i["code"], i["scene_id"]): i["severity"] for i in body["issues"]}
    assert served[("assets.tts_failed", "s2")] == "info" and served[("critic.factual", "s2")] == "info"
    assert served[("assets.media_degraded", "s1")] == "warning"  # a shown scene keeps its severity
    assert body["version"]["issue_counts"]["error"] == 0
    detail = c.get(f"/api/versions/{version.id}").json()
    assert {(i["code"], i["severity"]) for i in detail["issues"] if i["scene_id"] == "s2" and i["source"] != "lint"} \
        == {("assets.tts_failed", "info"), ("critic.factual", "info")}
    assert detail["issue_counts"]["error"] == 0
    with api.db() as db:  # stored at their real severity
        stored = {(i["code"], i["scene_id"]): i["severity"] for i in db.get(ProjectVersion, version.id).issues}
    assert stored[("assets.tts_failed", "s2")] == "error" and stored[("critic.factual", "s2")] == "error"
    edited["scenes"][1]["hidden"] = False  # shown again: the real severity comes back
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 2})
    assert r.status_code == 200, r.text
    served = {(i["code"], i["scene_id"]): i["severity"] for i in r.json()["issues"]}
    assert served[("assets.tts_failed", "s2")] == "error" and r.json()["version"]["issue_counts"]["error"] == 2


def test_building_only_hidden_scenes_is_refused(api):
    c, version = setup_version(api)
    edited = screenplay_dict()
    edited["scenes"][1]["hidden"] = True
    assert c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1}).status_code == 200
    r = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s2"]})
    assert r.status_code == 409 and r.json()["code"] == "action_unavailable", r.text
    assert c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s1", "s2"]}).status_code == 202


def test_preflight_numbers_scenes_as_the_editor_does_and_leaves_hidden_scenes_issues_out(api):
    from aadhi.compose.timeline import build_timeline

    c, version = setup_version(api)
    sp = make_screenplay()
    hidden = sp.model_copy(update={"scenes": [s.model_copy(update={"hidden": s.id == "s2"}) for s in sp.scenes]})
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.set_screenplay(hidden)
        v.set_timeline(build_timeline(hidden, None, settings=api.settings, version_id=v.id, revision=v.revision))
        v.set_issues([stored_issue("critic.factual", "error", "critic", "s2"),
                      stored_issue("critic.factual", "error", "critic", "q1")])
        db.commit()
    body = c.get(f"/api/versions/{version.id}/render/preflight").json()
    silent = {it["scene_id"]: it for it in body["items"] if it["reason"] == "no_audio"}
    assert set(silent) == {"s1", "q1"}  # no narration was built; the hidden s2 is not in the video
    assert (silent["q1"]["scene_index"], silent["q1"]["scene_number"]) == (1, 3)
    assert (silent["s1"]["scene_index"], silent["s1"]["scene_number"]) == (0, 1)
    assert [(it["scene_id"], it["scene_number"]) for it in body["quality"]] == [("q1", 3)]
    r = c.post(f"/api/versions/{version.id}/render", json={"allow_degraded": False})
    assert r.status_code == 409 and r.json()["code"] == "render_preflight"
    assert {it["scene_id"]: it["scene_number"] for it in r.json()["items"]} == {"s1": 1, "q1": 3}
