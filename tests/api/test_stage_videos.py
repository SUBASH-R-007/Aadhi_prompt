"""Lesson stage + next step (project list/detail, version detail), review checkpoints, ``matches_current`` on renders
and the user-wide video history (GET /api/videos)."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import event

from aadhi.models import Job, Project, ProjectVersion, Render, VisualReview
from aadhi.storage.assets import Produced
from tests.api.factories import add_job, add_project, screenplay_dict

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def finished_render(api, asset_store, version_id: int, *, built_revision: int = 1, status: str = "succeeded",
                    key: str | None = None, options: dict | None = None) -> Render:
    video_key = None
    if status == "succeeded":
        video_key = key or f"render-test-{version_id}-{built_revision}-{status}"
        asset_store.put(video_key, "render", Produced(data=MP4, mime="video/mp4", width=1920, height=1080,
                                                      duration_s=12.5))
    with api.db() as db:
        render = Render(version_id=version_id, status=status, built_revision=built_revision, video_asset_key=video_key,
                        duration_s=12.5 if status == "succeeded" else None, options=options or {})
        db.add(render)
        db.commit()
        db.refresh(render)
        return render


def project_row(c, project_id: int) -> dict:
    items = c.get("/api/projects").json()["items"]
    return next(p for p in items if p["id"] == project_id)


# --- stage on the project list / detail ------------------------------------------------------------------------


def test_stage_follows_the_lesson_from_build_to_video(api, asset_store):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    row = project_row(c, project.id)
    assert row["stage"] == "ready_to_render" and row["latest_render"] is None
    assert row["next_step"] == {"action": "render", "label": "Make the video", "href": None}
    render = finished_render(api, asset_store, version.id, built_revision=1)
    row = project_row(c, project.id)
    assert row["stage"] == "video_ready" and row["next_step"]["href"] == "#/videos"
    assert row["latest_render"] == {"id": render.id, "status": "succeeded", "built_revision": 1, "matches_current": True}
    detail = c.get(f"/api/projects/{project.id}").json()
    assert detail["project"]["stage"] == "video_ready"
    # the teacher edits the script: the video is out of date and the next step is to build
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(title="New"), "revision": 1})
    assert r.status_code == 200, r.text
    row = project_row(c, project.id)
    assert row["stage"] == "video_outdated" and row["next_step"]["action"] == "build"
    assert row["latest_render"]["matches_current"] is False
    version_body = c.get(f"/api/versions/{version.id}").json()
    assert version_body["stage"] == "video_outdated" and version_body["next_step"]["action"] == "build"


def test_stage_while_generating_and_reviewing(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, built=False, status="generating")
        job = add_job(db, kind="generate_lecture", status="running", user=alice, project=project, version=version)
        db.get(Job, job.id).stage = "plan"
        db.commit()
    row = project_row(c, project.id)
    assert row["stage"] == "planning" and row["next_step"] == {"action": "wait", "label": "Aadhi is planning the lesson",
                                                               "href": None}
    with api.db() as db:
        db.get(Job, job.id).status = "awaiting_review"
        v = db.get(ProjectVersion, version.id)
        v.status = "awaiting_review"
        v.generation_meta = {"review_stage": "source"}
        db.commit()
    row = project_row(c, project.id)
    assert row["stage"] == "source_review" and row["current_version"]["review_stage"] == "source"
    assert row["next_step"]["href"] == f"#/p/{project.id}/v/{version.id}/source"
    body = c.get(f"/api/versions/{version.id}").json()
    assert body["stage"] == "source_review" and body["checkpoints"]["source"] == {"state": "waiting",
                                                                                  "corrected": False}


def test_stage_of_a_failed_lecture_and_a_running_render(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        failed_project, _ = add_project(db, alice, title="Failed", built=False, status="failed")
        rendering_project, rendering = add_project(db, alice, title="Rendering")
        add_job(db, kind="render_video", status="queued", user=alice, project=rendering_project, version=rendering)
    assert project_row(c, failed_project.id)["stage"] == "failed"
    assert project_row(c, failed_project.id)["next_step"]["action"] == "retry"
    assert project_row(c, rendering_project.id)["stage"] == "rendering"


def test_project_list_stage_lookups_are_batched(api, asset_store):
    alice, c = api.editor("alice")
    with api.db() as db:
        for i in range(2):
            add_project(db, alice, title=f"P{i}")
    counts: list[int] = []

    def count_queries() -> int:
        from aadhi.db import get_engine

        seen: list[str] = []

        def before(conn, cursor, statement, *args):
            seen.append(statement)

        event.listen(get_engine(), "before_cursor_execute", before)
        try:
            assert c.get("/api/projects").status_code == 200
        finally:
            event.remove(get_engine(), "before_cursor_execute", before)
        return len(seen)

    counts.append(count_queries())
    with api.db() as db:
        for i in range(8):
            _, v = add_project(db, alice, title=f"Q{i}")
            db.add(Render(version_id=v.id, status="failed", built_revision=1))
        db.commit()
    counts.append(count_queries())
    assert counts[1] <= counts[0] + 1, counts  # independent of the number of projects (latest-render rows appear)


# --- version detail: checkpoints --------------------------------------------------------------------------------


def test_review_checkpoints_come_from_stored_state(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        v = db.get(ProjectVersion, version.id)
        v.generation_meta = {"ingest_key": "extract-x", "source_overrides": {
            "ingest_key": "extract-x", "excluded_chunk_ids": ["c0002"], "restored_chunk_ids": [], "concept_names": {}}}
        for stage in ("source_review", "plan_review"):
            add_job(db, kind="generate_lecture", status="succeeded", user=alice, project=project, version=version,
                    result={"stage": stage, "version_id": version.id})
        db.commit()
    body = c.get(f"/api/versions/{version.id}").json()
    assert body["checkpoints"]["source"] == {"state": "approved", "corrected": True}
    assert body["checkpoints"]["plan"] == {"state": "approved"}
    assert body["checkpoints"]["visuals"] == {"total": 0, "approved": 0, "pending": 0, "changed": 0, "removed": 0,
                                              "stale": 0}


def test_visual_checkpoints_count_sign_offs_and_stale_approvals(api):
    from aadhi import review

    sp = screenplay_dict(n_content=3, quiz=False)
    for i, scene in enumerate(sp["scenes"]):
        scene["side_panel"] = {"kind": "image", "image_prompt": f"A circuit diagram {i}"}
    alice, c = api.editor("alice")
    from aadhi.schemas.screenplay import Screenplay

    model = Screenplay.model_validate(sp)
    with api.db() as db:
        project, version = add_project(db, alice, screenplay=model)
        fp = review.fingerprint(review.visual_slot(model, model.scenes[0]))
        db.add(VisualReview(version_id=version.id, scene_id="s1", state="approved", fingerprint=fp))
        db.add(VisualReview(version_id=version.id, scene_id="s2", state="approved", fingerprint="0" * 32))
        db.commit()
    visuals = c.get(f"/api/versions/{version.id}").json()["checkpoints"]["visuals"]
    assert visuals == {"total": 3, "approved": 1, "pending": 2, "changed": 0, "removed": 0, "stale": 1}
    plan = c.get(f"/api/versions/{version.id}").json()["checkpoints"]["plan"]
    assert plan == {"state": "not_requested"}


# --- renders: matches_current ------------------------------------------------------------------------------------


def test_renders_say_whether_they_match_the_current_script(api, asset_store):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    old = finished_render(api, asset_store, version.id, built_revision=1)
    items = c.get(f"/api/versions/{version.id}/renders").json()["items"]
    assert [(i["id"], i["matches_current"]) for i in items] == [(old.id, True)]
    started = c.post(f"/api/versions/{version.id}/render", json={})
    assert started.status_code == 202 and started.json()["render"]["matches_current"] is True
    c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(title="Edited"), "revision": 1})
    items = c.get(f"/api/versions/{version.id}/renders").json()["items"]
    assert {i["matches_current"] for i in items} == {False}


# --- GET /api/videos ---------------------------------------------------------------------------------------------


def test_video_history_lists_the_users_own_videos(api, asset_store):
    alice, c = api.editor("alice")
    bob, bob_c = api.editor("bob")
    with api.db() as db:
        p1, v1 = add_project(db, alice, title="Ohm")
        p2, v2 = add_project(db, alice, title="Power")
        gone, v3 = add_project(db, alice, title="Deleted")
        db.get(Project, gone.id).deleted_at = dt.datetime.now(dt.timezone.utc)
        db.commit()
        _, bv = add_project(db, bob, title="Bob's")
    ok = finished_render(api, asset_store, v1.id, options={"qa": {"ok": True}})
    failed = finished_render(api, asset_store, v2.id, status="failed")
    finished_render(api, asset_store, v3.id)
    bobs = finished_render(api, asset_store, bv.id)
    r = c.get("/api/videos")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 2 and [i["render_id"] for i in body["items"]] == [failed.id, ok.id]
    item = body["items"][1]
    assert item["project_id"] == p1.id and item["project_title"] == "Ohm" and item["version_number"] == 1
    assert item["status"] == "succeeded" and item["duration_s"] == 12.5 and item["qa_ok"] is True
    assert item["size_bytes"] == len(MP4) and (item["width"], item["height"]) == (1920, 1080)
    assert item["matches_current"] is True and item["download_url"] == f"/api/renders/{ok.id}/download?file=video"
    assert item["preview_url"].startswith(api.settings.media_url_prefix.rstrip("/") + "/assets/render/")
    assert c.get(item["preview_url"]).status_code == 200  # streamable from the media route
    assert c.get(item["download_url"]).status_code == 200
    first = body["items"][0]
    assert first["status"] == "failed" and first["preview_url"] is None and first["download_url"] is None
    assert first["qa_ok"] is None and first["project_title"] == "Power"
    assert set(item) == {"render_id", "project_id", "project_title", "version_id", "version_number", "created_at",
                         "status", "duration_s", "size_bytes", "width", "height", "matches_current", "download_url",
                         "preview_url", "qa_ok",
                         # the lecture's fields that tell lectures sharing a title apart, as the project list does
                         "subject_name", "unit_name", "session_number", "session_title", "project_language",
                         "project_created_at"}
    with api.db() as db:
        project = db.get(Project, p1.id)
        assert item["project_language"] == project.language == "en-IN"
        assert item["session_number"] == project.session_number and item["subject_name"] == project.subject_name
        assert item["project_created_at"] and item["project_created_at"].startswith(str(project.created_at.year))
    assert [i["render_id"] for i in bob_c.get("/api/videos").json()["items"]] == [bobs.id]


def test_video_history_for_admins_is_their_own(api, asset_store):
    alice, _ = api.editor("alice")
    api.user("root", role="admin")
    admin = api.login("root")
    with api.db() as db:
        _, v = add_project(db, alice)
    finished_render(api, asset_store, v.id)
    assert admin.get("/api/videos").json() == {"items": [], "total": 0}


def test_video_history_pages(api, asset_store):
    alice, c = api.editor("alice")
    with api.db() as db:
        _, v = add_project(db, alice)
    ids = [finished_render(api, asset_store, v.id, built_revision=1, key=f"render-page-{i}").id for i in range(5)]
    page = c.get("/api/videos?limit=2&offset=2").json()
    assert page["total"] == 5 and [i["render_id"] for i in page["items"]] == list(reversed(ids))[2:4]
    assert c.get("/api/videos?limit=0").status_code == 422
    assert c.get("/api/videos?offset=-1").status_code == 422
    assert api.client(browser=False).get("/api/videos").status_code == 401
