"""Cross-user access: user B gets 404 (never 403) on everything that belongs to user A."""

from __future__ import annotations

from aadhi.models import Render
from tests.api.factories import add_job, add_project, screenplay_dict, tiny_png


def test_other_users_objects_are_not_found(api):
    alice, a = api.editor("alice")
    _, b = api.editor("bob")
    with api.db() as db:
        project, version = add_project(db, alice)
        job = add_job(db, kind="build_assets", status="failed", user=alice, project=project, version=version)
        render = Render(version_id=version.id, status="succeeded", video_asset_key="render-x")
        db.add(render)
        db.commit()
        db.refresh(render)
    share = a.post(f"/api/projects/{project.id}/shares", json={}).json()["token"]
    pid, vid = project.id, version.id
    requests = [
        ("GET", f"/api/projects/{pid}", None),
        ("PATCH", f"/api/projects/{pid}", {"title": "pwned"}),
        ("DELETE", f"/api/projects/{pid}", None),
        ("POST", f"/api/projects/{pid}/regenerate", {}),
        ("GET", f"/api/projects/{pid}/shares", None),
        ("POST", f"/api/projects/{pid}/shares", {}),
        ("DELETE", f"/api/shares/{share}", None),
        ("GET", f"/api/projects/{pid}/analytics", None),
        ("GET", f"/api/usage/projects/{pid}", None),
        ("GET", f"/api/versions/{vid}", None),
        ("GET", f"/api/versions/{vid}/timeline", None),
        ("POST", f"/api/versions/{vid}/timeline/preview", {"screenplay": screenplay_dict()}),
        ("PUT", f"/api/versions/{vid}/screenplay", {"screenplay": screenplay_dict(), "revision": 1}),
        ("POST", f"/api/versions/{vid}/lint", {"screenplay": screenplay_dict()}),
        ("POST", f"/api/versions/{vid}/build", {}),
        ("POST", f"/api/versions/{vid}/scenes/s1/regenerate", {}),
        ("POST", f"/api/versions/{vid}/plan", {"plan": {}}),
        ("POST", f"/api/versions/{vid}/approve-plan", None),
        ("POST", f"/api/versions/{vid}/duplicate", {}),
        ("POST", f"/api/versions/{vid}/translate", {"target_language": "ta-IN"}),
        ("POST", f"/api/versions/{vid}/render", {}),
        ("GET", f"/api/versions/{vid}/renders", None),
        ("GET", f"/api/versions/{vid}/companion.md", None),
        ("GET", f"/api/versions/{vid}/companion.html", None),
        ("GET", f"/api/versions/{vid}/export.json", None),
        ("GET", f"/api/versions/{vid}/chapters.txt", None),
        ("GET", f"/api/renders/{render.id}/download", None),
        ("GET", f"/api/jobs/{job.id}", None),
        ("GET", f"/api/jobs/{job.id}/events", None),
        ("GET", f"/api/jobs/{job.id}/stream", None),
        ("POST", f"/api/jobs/{job.id}/cancel", None),
        ("POST", f"/api/jobs/{job.id}/retry", None),
        ("GET", f"/api/jobs?project_id={pid}", None),
    ]
    for method, path, body in requests:
        r = b.request(method, path, json=body)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert r.json()["code"] == "not_found"
    # The same requests leave A's data untouched.
    assert a.get(f"/api/projects/{pid}").json()["project"]["title"] == "Ohm's Law"
    assert a.get(f"/api/public/watch/{share}").status_code == 200
    assert b.get("/api/jobs").json()["total"] == 0
    upload = b.post(
        "/api/uploads",
        files={"file": ("f.png", tiny_png(), "image/png")},
        data={"purpose": "figure", "project_id": str(pid)},
    )
    assert upload.status_code == 404
    ingest = b.post(
        "/api/analytics/events", json={"version_id": vid, "viewer_id": "v" * 20, "events": [{"event": "pause", "t": 1}]}
    )
    assert ingest.status_code == 404


def test_admin_can_access_everything(api):
    alice, _ = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
    api.user("root", role="admin")
    admin = api.login("root")
    assert admin.get(f"/api/projects/{project.id}").status_code == 200
    assert admin.get(f"/api/versions/{version.id}").status_code == 200
    assert admin.get(f"/api/projects/{project.id}/analytics").status_code == 200


def test_huge_ids_are_not_found(api):
    _, c = api.editor("alice")
    huge = 2**70
    for path in (
        f"/api/projects/{huge}",
        f"/api/versions/{huge}",
        f"/api/jobs/{huge}",
        f"/api/renders/{huge}/download",
    ):
        assert c.get(path).status_code == 404, path
