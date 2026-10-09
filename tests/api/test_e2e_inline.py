"""End to end through the HTTP API with REAL inline job workers and the offline fake providers.

Exercises the integration of the API with the job queue, the pipeline (fake LLM/TTS, real ffmpeg
audio assembly), the timeline builder and the asset store: upload -> generate -> timeline/media ->
edit -> stale scenes -> build -> exports -> share/watch/analytics -> JSON export/import round trip,
then plan review -> approve -> regenerate scene -> translate -> duplicate.

Marked ``slow`` (real ffmpeg); Manim is disabled (``MANIM_SANDBOX=disabled`` + options) so no
animation is rendered. No network access, no paid APIs.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.api.conftest import Api
from tests.api.e2e import (
    FAST_OPTIONS,
    SOURCE_MARKDOWN,
    assert_succeeded,
    figure_pdf,
    require_job_handlers,
    wait_job,
)

pytestmark = pytest.mark.slow


@pytest.fixture()
def inline_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment for an API process that runs its own worker threads (read by ``app_env``)."""
    monkeypatch.setenv("WORKER_MODE", "inline")
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("WORKER_KINDS", "generate_lecture,regenerate_scene,build_assets,translate,cleanup")
    monkeypatch.setenv("JOB_POLL_INTERVAL_SECONDS", "0.2")
    monkeypatch.setenv("MANIM_SANDBOX", "disabled")


@pytest.fixture()
def live_api(inline_env: None, app_env: Any) -> Iterator[Api]:
    """App with its lifespan running: tables, admin bootstrap and inline workers started/stopped."""
    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    assert app_env.worker_mode == "inline"
    require_job_handlers("generate_lecture", "regenerate_scene", "build_assets", "translate")
    reset_rate_limits()
    harness = Api(app=create_app(app_env), settings=app_env)
    lifespan = harness.client(browser=False)
    with lifespan:
        yield harness
        for c in harness.clients:
            if c is not lifespan:
                c.close()
    reset_rate_limits()


def create_project(c: Any, *, options: dict[str, Any] | None = None, title: str = "Ohm's Law") -> dict[str, Any]:
    r = c.post(
        "/api/projects",
        files={"file": ("ohm.md", SOURCE_MARKDOWN.encode("utf-8"), "text/markdown")},
        data={"options": json.dumps({**FAST_OPTIONS, **(options or {})}), "title": title},
    )
    assert r.status_code == 201, r.text
    return r.json()


def narrated_scene(screenplay: dict[str, Any]) -> dict[str, Any]:
    """First board scene with narration (editable without touching ids)."""
    for scene in screenplay["scenes"]:
        if scene["type"] in ("content", "example", "summary", "key_takeaway", "recap") and scene["beats"]:
            return scene
    raise AssertionError("the generated screenplay has no narrated board scene")


def test_generate_edit_build_share_and_reimport(live_api: Api) -> None:
    api = live_api
    api.user("alice")
    c = api.login("alice")

    created = create_project(c)
    pid, vid = created["project"]["id"], created["version"]["id"]
    assert created["version"]["status"] == "generating" and created["job"]["kind"] == "generate_lecture"
    job = wait_job(c, created["job"]["id"])
    assert_succeeded(c, job)
    assert job["cost_usd"] >= 0 and job["progress"] == pytest.approx(1.0)

    # Version + project views after generation.
    detail = c.get(f"/api/versions/{vid}").json()
    assert detail["status"] == "ready" and detail["has_timeline"] is True
    assert detail["timeline_stale"] is False and detail["stale_scenes"] == []
    assert detail["plan"] is None and "plan" not in detail["generation_meta"]
    screenplay = detail["screenplay"]
    assert screenplay["scenes"]
    project = c.get(f"/api/projects/{pid}").json()
    assert project["project"]["current_version"]["id"] == vid
    assert project["sources"][0]["filename"] == "ohm.md" and project["jobs"][0]["status"] == "succeeded"

    # Timeline with serve-time URLs; the media they point at is served (Range-capable, immutable).
    tl = c.get(f"/api/versions/{vid}/timeline")
    assert tl.status_code == 200 and tl.headers["etag"].startswith('"r')
    timeline = tl.json()
    assert timeline["version_id"] == vid and timeline["screenplay_revision"] == detail["revision"]
    audio_urls = [s["audio_url"] for s in timeline["scenes"] if s.get("audio_asset_key")]
    assert audio_urls and all(u.startswith("/media/assets/") for u in audio_urls)
    clip = c.get(audio_urls[0], headers={"Range": "bytes=0-15"})
    assert clip.status_code == 206 and clip.headers["content-type"].startswith("audio/")
    assert c.get(f"/api/versions/{vid}/timeline", headers={"If-None-Match": tl.headers["etag"]}).status_code == 304

    # A finished job's stream replays its events and ends.
    with c.stream("GET", f"/api/jobs/{job['id']}/stream") as stream:
        text = stream.read().decode()
    assert "event: job_event" in text and "event: end" in text

    for name in ("companion.md", "companion.html", "export.json", "chapters.txt"):
        assert c.get(f"/api/versions/{vid}/{name}").status_code == 200, name

    # Edit one scene: only it becomes stale; rendering is refused until it is rebuilt.
    edited = copy.deepcopy(screenplay)
    scene = narrated_scene(edited)
    scene["beats"][0]["narration"] += " Always check the units."
    put = c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": edited, "revision": detail["revision"]})
    assert put.status_code == 200, put.text
    saved = put.json()
    assert saved["stale_scenes"] == [scene["id"]]
    assert saved["version"]["revision"] == detail["revision"] + 1 and saved["version"]["timeline_stale"] is True
    lost = c.put(f"/api/versions/{vid}/screenplay", json={"screenplay": edited, "revision": detail["revision"]})
    assert lost.status_code == 409 and lost.json()["current_revision"] == detail["revision"] + 1
    stale = c.post(f"/api/versions/{vid}/render", json={})
    assert stale.status_code == 409 and stale.json()["code"] == "timeline_stale"

    build = c.post(f"/api/versions/{vid}/build", json={"scene_ids": None})
    assert build.status_code == 202, build.text
    assert_succeeded(c, wait_job(c, build.json()["job"]["id"]))
    rebuilt = c.get(f"/api/versions/{vid}").json()
    assert rebuilt["status"] == "ready" and rebuilt["timeline_stale"] is False and rebuilt["stale_scenes"] == []
    new_tl = c.get(f"/api/versions/{vid}/timeline", headers={"If-None-Match": tl.headers["etag"]})
    assert new_tl.status_code == 200 and new_tl.json()["screenplay_revision"] == rebuilt["revision"]

    # Share link: anonymous watch + analytics ingest, reflected in the teacher dashboard.
    share = c.post(f"/api/projects/{pid}/shares", json={"expires_in_days": 7})
    assert share.status_code == 201
    token = share.json()["token"]
    anon = api.client(browser=False)
    watch = anon.get(f"/api/public/watch/{token}")
    assert watch.status_code == 200 and watch.json()["project"]["title"] == "Ohm's Law"
    first = watch.json()["timeline"]["scenes"][0]["scene_id"]
    events = [
        {"event": "session_start", "t": 0},
        {"event": "scene_enter", "scene_id": first, "t": 0.5},
        {"event": "scene_complete", "scene_id": first, "t": 9.0},
    ]
    ingest = anon.post(
        "/api/analytics/events", json={"share_token": token, "viewer_id": "viewer-0123456789ab", "events": events}
    )
    assert ingest.status_code == 202, ingest.text
    dashboard = c.get(f"/api/projects/{pid}/analytics").json()
    assert dashboard["summary"]["viewers"] == 1 and dashboard["summary"]["sessions"] == 1
    first_row = next(s for s in dashboard["scenes"] if s["scene_id"] == first)
    assert (first_row["enters"], first_row["completes"], first_row["dropoff_rate"]) == (1, 1, 0.0)
    assert c.get(f"/api/projects/{pid}/shares").json()["items"][0]["view_count"] == 1

    # Export -> import round trip: a new project whose build reuses the cached assets.
    exported = c.get(f"/api/versions/{vid}/export.json")
    imported = c.post("/api/projects/import", files={"file": ("lecture.json", exported.content, "application/json")})
    assert imported.status_code == 201, imported.text
    assert imported.json()["job"]["kind"] == "build_assets"
    assert_succeeded(c, wait_job(c, imported.json()["job"]["id"]))
    copy_vid = imported.json()["version"]["id"]
    copy_detail = c.get(f"/api/versions/{copy_vid}").json()
    assert copy_detail["status"] == "ready" and copy_detail["has_timeline"] and not copy_detail["timeline_stale"]
    assert [s["id"] for s in copy_detail["screenplay"]["scenes"]] == [s["id"] for s in edited["scenes"]]

    usage = c.get("/api/usage/me").json()
    assert usage["total_usd"] >= 0 and "by_operation" in usage
    assert c.get(f"/api/usage/projects/{pid}").json()["by_job"]


def test_plan_review_regenerate_translate_and_duplicate(live_api: Api) -> None:
    api = live_api
    api.user("alice")
    c = api.login("alice")

    created = create_project(c, options={"review_plan": True})
    vid = created["version"]["id"]
    waiting = wait_job(c, created["job"]["id"])
    assert waiting["status"] == "awaiting_review", waiting
    detail = c.get(f"/api/versions/{vid}").json()
    assert detail["status"] == "awaiting_review" and detail["plan"] is not None
    assert c.get(f"/api/jobs/{waiting['id']}").json()["result"]["stage"] == "plan_review"

    # The teacher edits the plan, then approves it: the waiting job succeeds and a continuation runs.
    plan = copy.deepcopy(detail["plan"])
    plan["session_title"] = "Ohm's Law (reviewed)"
    saved = c.post(f"/api/versions/{vid}/plan", json={"plan": plan})
    assert saved.status_code == 200, saved.text
    assert c.get(f"/api/versions/{vid}").json()["plan"]["session_title"] == "Ohm's Law (reviewed)"
    approved = c.post(f"/api/versions/{vid}/approve-plan")
    assert approved.status_code == 202, approved.text
    assert c.get(f"/api/jobs/{waiting['id']}").json()["status"] == "succeeded"
    assert_succeeded(c, wait_job(c, approved.json()["job"]["id"]))
    ready = c.get(f"/api/versions/{vid}").json()
    assert ready["status"] == "ready" and ready["plan"] is None and ready["has_timeline"]
    assert c.post(f"/api/versions/{vid}/approve-plan").json()["code"] == "not_awaiting_review"

    # Regenerate one scene in place (new revision, timeline rebuilt).
    scene_id = narrated_scene(ready["screenplay"])["id"]
    regen = c.post(
        f"/api/versions/{vid}/scenes/{scene_id}/regenerate", json={"instructions": "Use a water-pipe analogy."}
    )
    assert regen.status_code == 202, regen.text
    assert_succeeded(c, wait_job(c, regen.json()["job"]["id"]))
    after = c.get(f"/api/versions/{vid}").json()
    assert after["revision"] > ready["revision"] and after["status"] == "ready" and not after["timeline_stale"]

    # Translate into a new version.
    tr = c.post(f"/api/versions/{vid}/translate", json={"target_language": "hi-IN"})
    assert tr.status_code == 202, tr.text
    target = tr.json()["version"]
    assert target["status"] == "generating" and target["language"] == "hi-IN" and target["source_version_id"] == vid
    assert_succeeded(c, wait_job(c, tr.json()["job"]["id"]))
    translated = c.get(f"/api/versions/{target['id']}").json()
    assert translated["status"] == "ready" and translated["language"] == "hi-IN" and translated["has_timeline"]
    assert c.get(f"/api/versions/{target['id']}/timeline").json()["language"] == "hi-IN"

    # Duplicate keeps a ready, renderable copy.
    dup = c.post(f"/api/versions/{vid}/duplicate", json={"label": "Backup"})
    assert dup.status_code == 201
    copy_v = dup.json()["version"]
    assert copy_v["status"] == "ready" and copy_v["label"] == "Backup" and not copy_v["timeline_stale"]
    assert c.get(f"/api/versions/{copy_v['id']}/timeline").json()["version_id"] == copy_v["id"]
    versions = c.get(f"/api/projects/{created['project']['id']}").json()["versions"]
    assert [v["number"] for v in versions] == [1, 2, 3]


def test_pdf_figures_are_authorised_only_for_their_project(live_api: Api) -> None:
    api = live_api
    api.user("alice")
    api.user("bob")
    alice = api.login("alice")
    bob = api.login("bob")

    created = alice.post(
        "/api/projects",
        files={"file": ("circuit.pdf", figure_pdf(), "application/pdf")},
        data={"options": json.dumps(FAST_OPTIONS)},
    )
    assert created.status_code == 201, created.text
    vid = created.json()["version"]["id"]
    assert_succeeded(alice, wait_job(alice, created.json()["job"]["id"]))
    detail = alice.get(f"/api/versions/{vid}").json()
    screenplay = detail["screenplay"]
    figures = [f for f in screenplay["figures"] if f.get("asset_key")]
    assert figures, "the PDF's figure was not extracted"
    figure_key = figures[0]["asset_key"]

    # The owner can save the generated screenplay unchanged: extracted figures are referenced.
    same = alice.put(f"/api/versions/{vid}/screenplay", json={"screenplay": screenplay, "revision": detail["revision"]})
    assert same.status_code == 200, same.text

    # Bob importing Alice's export loses the figure keys (with a warning) ...
    exported = alice.get(f"/api/versions/{vid}/export.json").content
    imported = bob.post("/api/projects/import", files={"file": ("lecture.json", exported, "application/json")})
    assert imported.status_code == 201, imported.text
    assert any("Removed unavailable media reference" in w for w in imported.json()["warnings"])
    bob_vid = imported.json()["version"]["id"]
    assert_succeeded(bob, wait_job(bob, imported.json()["job"]["id"]))
    bob_detail = bob.get(f"/api/versions/{bob_vid}").json()
    assert all(not f.get("asset_key") for f in bob_detail["screenplay"]["figures"])

    # ... and cannot put Alice's key back into his screenplay.
    stolen = copy.deepcopy(bob_detail["screenplay"])
    stolen["figures"][0]["asset_key"] = figure_key
    refused = bob.put(
        f"/api/versions/{bob_vid}/screenplay", json={"screenplay": stolen, "revision": bob_detail["revision"]}
    )
    assert refused.status_code == 422 and refused.json()["detail"][0]["type"] == "asset_key.unknown"
