"""Build, regenerate scene, plan review (save/approve), duplicate, translate."""

from __future__ import annotations

import pytest

from aadhi.models import Job, ProjectVersion
from aadhi.pipeline.base import LecturePlan
from tests.api.factories import add_job, add_project


def setup_version(api, **kw):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, **kw)
    return alice, c, project, version


def payload_of(api, job_id: int) -> dict:
    with api.db() as db:
        return dict(db.get(Job, job_id).payload)


def test_build_enqueues_build_assets_with_base_revision(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": None})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["kind"] == "build_assets" and job["version_id"] == version.id
    assert payload_of(api, job["id"]) == {"scene_ids": None, "base_revision": 1}
    dup = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s1"]})
    assert dup.status_code == 409
    assert dup.json()["code"] == "job_in_progress"
    assert dup.json()["job_id"] == job["id"]


def test_build_specific_scenes_validates_ids(api):
    _, c, project, version = setup_version(api)
    bad = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s1", "nope"]})
    assert bad.status_code == 422
    empty = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": []})
    assert empty.status_code == 422
    ok = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": ["s2", "s1", "s2"]})
    assert ok.status_code == 202
    assert payload_of(api, ok.json()["job"]["id"])["scene_ids"] == ["s2", "s1"]


def test_build_without_screenplay_is_a_conflict(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, built=False)
        db.get(ProjectVersion, version.id).screenplay = None
        db.commit()
    r = c.post(f"/api/versions/{version.id}/build", json={})
    assert r.status_code == 409 and r.json()["code"] == "no_screenplay"


def test_regenerate_scene(api):
    _, c, project, version = setup_version(api)
    r = c.post(
        f"/api/versions/{version.id}/scenes/s2/regenerate", json={"instructions": "  Use a water-pipe analogy. "}
    )
    assert r.status_code == 202, r.text
    payload = payload_of(api, r.json()["job"]["id"])
    assert payload == {"scene_id": "s2", "instructions": "Use a water-pipe analogy.", "base_revision": 1}
    assert c.post(f"/api/versions/{version.id}/scenes/zzz/regenerate", json={}).status_code == 404
    too_long = c.post(f"/api/versions/{version.id}/scenes/s1/regenerate", json={"instructions": "x" * 5000})
    assert too_long.status_code == 422


PLAN = {
    "session_title": "Ohm's Law",
    "learning_objectives": [{"id": "o1", "text": "Apply V = IR"}],
    "concept_map": [{"id": "ohm", "title": "Ohm's law"}],
    "chapters": [
        {
            "id": "ch1",
            "title": "Basics",
            "scenes": [{"id": "s1", "type": "content", "goal": "Explain V = IR", "concept_id": "ohm"}],
        }
    ],
}


def _awaiting(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, built=False, status="awaiting_review")
        v = db.get(ProjectVersion, version.id)
        v.screenplay = None
        v.generation_meta = {"plan": LecturePlan.model_validate(PLAN).model_dump(mode="json"), "prompt_version": "p1"}
        db.commit()
        job = add_job(
            db,
            kind="generate_lecture",
            status="awaiting_review",
            user=alice,
            project=project,
            version=version,
            payload={"source_document_id": 7, "options": {"language": "en-IN"}, "base_revision": 1},
            result={"stage": "plan", "ingest_key": "private/extract/x.json"},
        )
    return alice, c, project, version, job


def test_version_detail_exposes_plan_while_awaiting_review(api):
    _, c, project, version, job = _awaiting(api)
    body = c.get(f"/api/versions/{version.id}").json()
    assert body["status"] == "awaiting_review"
    assert body["plan"]["chapters"][0]["scenes"][0]["id"] == "s1"
    assert "plan" not in body["generation_meta"]
    assert body["generation_meta"]["prompt_version"] == "p1"


def test_save_plan_while_awaiting_review(api):
    _, c, project, version, job = _awaiting(api)
    edited = dict(PLAN, session_title="Ohm's Law (edited)")
    r = c.post(f"/api/versions/{version.id}/plan", json={"plan": edited})
    assert r.status_code == 200, r.text
    assert c.get(f"/api/versions/{version.id}").json()["plan"]["session_title"] == "Ohm's Law (edited)"
    invalid = dict(
        PLAN,
        chapters=[
            {"id": "c", "title": "t", "scenes": [{"id": "a", "type": "content", "goal": "g", "concept_id": "unknown"}]}
        ],
    )
    assert c.post(f"/api/versions/{version.id}/plan", json={"plan": invalid}).status_code == 422


def test_save_plan_checks_manim_templates(api):
    try:
        from aadhi.manim.templates import registry  # noqa: F401
    except ImportError:
        pytest.skip("aadhi.manim.templates.registry not available yet")
    _, c, project, version, job = _awaiting(api)
    bad_template = dict(
        PLAN,
        chapters=[
            {
                "id": "c",
                "title": "t",
                "scenes": [{"id": "a", "type": "simulation", "goal": "g", "manim_template": "no_such_template"}],
            }
        ],
    )
    r = c.post(f"/api/versions/{version.id}/plan", json={"plan": bad_template})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][-1] == "manim_template"


def test_save_plan_refused_when_not_awaiting_review(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/plan", json={"plan": PLAN})
    assert r.status_code == 409 and r.json()["code"] == "not_awaiting_review"
    assert c.post(f"/api/versions/{version.id}/approve-plan").status_code == 409


def test_approve_plan_resumes_generation(api):
    _, c, project, version, job = _awaiting(api)
    r = c.post(f"/api/versions/{version.id}/approve-plan")
    assert r.status_code == 202, r.text
    new_job = r.json()["job"]
    assert new_job["kind"] == "generate_lecture" and new_job["id"] != job.id
    payload = payload_of(api, new_job["id"])
    assert payload["resume_state"] == {"stage": "plan", "ingest_key": "private/extract/x.json"}
    assert payload["source_document_id"] == 7
    with api.db() as db:
        assert db.get(Job, job.id).status == "succeeded"
        assert db.get(ProjectVersion, version.id).status == "generating"
    again = c.post(f"/api/versions/{version.id}/approve-plan")
    assert again.status_code == 409


def test_duplicate_copies_documents_into_a_new_version(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/duplicate", json={"label": "Variant B"})
    assert r.status_code == 201, r.text
    new = r.json()["version"]
    assert new["number"] == 2 and new["label"] == "Variant B" and new["status"] == "ready"
    assert new["revision"] == 1 and new["built_revision"] == 1 and new["timeline_stale"] is False
    detail = c.get(f"/api/versions/{new['id']}").json()
    assert detail["screenplay"] == c.get(f"/api/versions/{version.id}").json()["screenplay"]
    timeline = c.get(f"/api/versions/{new['id']}/timeline").json()
    assert timeline["version_id"] == new["id"]
    default_label = c.post(f"/api/versions/{version.id}/duplicate", json={}).json()["version"]
    assert default_label["number"] == 3 and default_label["label"] == "Copy of v1"


def test_duplicate_of_stale_version_is_draft(api):
    _, c, project, version = setup_version(api)
    from tests.api.factories import screenplay_dict

    c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    new = c.post(f"/api/versions/{version.id}/duplicate", json={}).json()["version"]
    assert new["status"] == "draft" and new["timeline_stale"] is True


def test_translate_creates_target_version_and_job(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/translate", json={"target_language": "ta-IN", "translate_board": True})
    assert r.status_code == 202, r.text
    new, job = r.json()["version"], r.json()["job"]
    assert new["language"] == "ta-IN" and new["status"] == "generating" and new["source_version_id"] == version.id
    assert job["kind"] == "translate" and job["version_id"] == new["id"]
    assert payload_of(api, job["id"]) == {
        "source_version_id": version.id,
        "target_language": "ta-IN",
        "translate_board": True,
        "tts_voice": None,
    }


def test_translate_validation(api):
    _, c, project, version = setup_version(api)
    assert c.post(f"/api/versions/{version.id}/translate", json={"target_language": "en-IN"}).status_code == 422
    assert c.post(f"/api/versions/{version.id}/translate", json={"target_language": "fr-FR"}).status_code == 422
    bad_voice = c.post(f"/api/versions/{version.id}/translate", json={"target_language": "hi-IN", "tts_voice": "a b"})
    assert bad_voice.status_code == 422


def _finish(api, job_id: int) -> None:
    with api.db() as db:
        db.get(Job, job_id).status = "succeeded"
        db.commit()


def _count(api, model, *where) -> int:
    from sqlalchemy import func, select

    with api.db() as db:
        return int(db.execute(select(func.count()).select_from(model).where(*where)).scalar_one())


def test_refused_regenerate_does_not_use_up_the_generation_quota(api):
    """409s while a build runs must not spend hourly generation tokens (no lockout without a job)."""
    api.settings.generation_rate_limit_per_hour = 2
    alice, c, project, version = setup_version(api)
    with api.db() as db:
        build = add_job(db, kind="build_assets", status="running", user=alice, project=project, version=version)
    url = f"/api/versions/{version.id}/scenes/s1/regenerate"
    for _ in range(4):
        busy = c.post(url, json={})
        assert busy.status_code == 409 and busy.json()["code"] == "job_in_progress", busy.text
    _finish(api, build.id)
    for _ in range(2):  # the full quota is still there
        r = c.post(url, json={})
        assert r.status_code == 202, r.text
        _finish(api, r.json()["job"]["id"])
    limited = c.post(url, json={})
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert "Retry-After" in limited.headers
    # The job flushed before the 429 was rolled back with the request's transaction.
    assert _count(api, Job, Job.kind == "regenerate_scene") == 2


def test_rate_limited_translate_leaves_no_orphan_version(api):
    api.settings.generation_rate_limit_per_hour = 1
    _, c, project, version = setup_version(api)
    assert c.post(f"/api/versions/{version.id}/translate", json={"target_language": "ta-IN"}).status_code == 202
    limited = c.post(f"/api/versions/{version.id}/translate", json={"target_language": "hi-IN"})
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert _count(api, ProjectVersion, ProjectVersion.project_id == project.id) == 2  # original + ta-IN
    assert _count(api, Job, Job.kind == "translate") == 1
