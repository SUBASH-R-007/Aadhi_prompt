"""POST /api/projects/import also takes lessons exported from the friend's fork of v1 (origin/andryan): same response
shape, hidden scenes and minimum durations kept, everything else reported in ``warnings``."""

from __future__ import annotations

import json
from pathlib import Path

from aadhi.models import ProjectVersion

FIXTURE = Path(__file__).parents[1] / "core" / "fixtures" / "andryan_lesson.json"


def test_import_a_lesson_from_the_fork(api):
    _, c = api.editor("alice")
    data = FIXTURE.read_bytes()
    r = c.post("/api/projects/import", files={"file": ("lesson.json", data, "application/json")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body) == {"project", "version", "job", "warnings"}
    assert body["project"]["title"] == "Stress in One Minute" and body["job"]["kind"] == "build_assets"
    assert body["project"]["stage"] == "building"
    assert "scene s3: hidden in the source app; imported as a hidden scene" in body["warnings"]
    assert any("Studio records" in w for w in body["warnings"])
    with api.db() as db:
        sp = db.get(ProjectVersion, body["version"]["id"]).get_screenplay()
    assert [s.hidden for s in sp.scenes] == [False, False, True, False, False]
    assert sp.scene_by_id("s1").min_seconds == 6.0


def test_fork_lesson_saved_from_their_project_api_imports(api):
    """Their GET /api/projects/{id} answers the lesson document itself, so a saved response imports as is."""
    _, c = api.editor("alice")
    lesson = json.loads(FIXTURE.read_text(encoding="utf-8"))
    lesson["scenes"] = lesson["scenes"][:2]
    r = c.post("/api/projects/import", files={"file": ("lesson.json", json.dumps(lesson).encode(), "application/json")})
    assert r.status_code == 201, r.text
    assert len(c.get(f"/api/versions/{r.json()['version']['id']}").json()["screenplay"]["scenes"]) == 2
