"""Batch 4 integration: a version's render list carries a streamable ``preview_url`` for finished MP4s (the
project page's "Watch"), a lecture whose every scene is skipped in the video is refused up front, and analytics
mark skipped scenes."""

from __future__ import annotations

from aadhi.models import Job, Render
from tests.api.factories import add_project, make_screenplay
from tests.api.test_stage_videos import finished_render


def test_version_renders_carry_a_preview_url_only_for_finished_videos(api, asset_store):
    alice, c = api.editor("alice")
    with api.db() as db:
        _, version = add_project(db, alice)
    failed = finished_render(api, asset_store, version.id, status="failed")
    ok = finished_render(api, asset_store, version.id, key="render-batch4-ok")
    items = {i["id"]: i for i in c.get(f"/api/versions/{version.id}/renders").json()["items"]}
    assert items[failed.id]["preview_url"] is None
    url = items[ok.id]["preview_url"]
    assert url.startswith(api.settings.media_url_prefix.rstrip("/") + "/assets/render/")
    assert c.get(url).status_code == 200  # streamable from the media route
    videos = {i["render_id"]: i for i in c.get("/api/videos").json()["items"]}
    assert videos[ok.id]["preview_url"] == url  # one way of serving both
    started = c.post(f"/api/versions/{version.id}/render", json={})
    assert started.status_code == 202
    queued = c.get(f"/api/versions/{version.id}/renders").json()["items"][0]
    assert queued["status"] == "queued" and queued["preview_url"] is None


def test_a_lecture_with_every_scene_hidden_is_refused_before_a_job_is_queued(api):
    alice, c = api.editor("alice")
    sp = make_screenplay()
    hidden = sp.model_copy(update={"scenes": [s.model_copy(update={"hidden": True}) for s in sp.scenes]})
    with api.db() as db:
        _, version = add_project(db, alice, screenplay=hidden)
    r = c.post(f"/api/versions/{version.id}/render", json={})
    assert r.status_code == 409 and r.json()["code"] == "all_scenes_hidden", r.text
    with api.db() as db:
        assert db.query(Render).count() == 0 and db.query(Job).count() == 0
    one_shown = sp.model_copy(update={"scenes": [s.model_copy(update={"hidden": i > 0}) for i, s in enumerate(sp.scenes)]})
    with api.db() as db:
        _, other = add_project(db, alice, screenplay=one_shown, title="One scene shown")
    assert c.post(f"/api/versions/{other.id}/render", json={}).status_code == 202


def test_analytics_marks_a_scene_skipped_in_the_video(api):
    alice, c = api.editor("alice")
    sp = make_screenplay()
    one_hidden = sp.model_copy(update={"scenes": [s.model_copy(update={"hidden": s.id == "s2"}) for s in sp.scenes]})
    with api.db() as db:
        project, version = add_project(db, alice, screenplay=one_hidden)
    body = c.get(f"/api/projects/{project.id}/analytics", params={"version_id": version.id}).json()
    rows = {s["scene_id"]: s for s in body["scenes"]}
    assert rows["s2"]["hidden"] is True
    assert all("hidden" not in r for sid, r in rows.items() if sid != "s2")  # left out unless hidden
