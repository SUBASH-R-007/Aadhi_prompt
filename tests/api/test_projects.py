"""Projects: create (upload), import, list/search, detail, patch, delete, regenerate, options policy."""

from __future__ import annotations

import json

from sqlalchemy import select

from aadhi.models import AssetRef, Job, Project, ShareLink, SourceDocument, UsageEvent
from tests.api.factories import add_asset_ref, add_job, add_project, screenplay_dict

TXT = ("notes.txt", b"Ohm's law: V = I R. Resistance is measured in ohms.\n" * 4, "text/plain")


def create(c, *, options: dict | None = None, file=TXT, title: str | None = None):
    data = {}
    if options is not None:
        data["options"] = json.dumps(options)
    if title is not None:
        data["title"] = title
    return c.post("/api/projects", files={"file": file}, data=data)


def test_create_project_uploads_source_and_enqueues_generation(api):
    alice, c = api.editor("alice")
    r = create(c, options={"language": "en-IN", "target_minutes": 10, "session_title": "Ohm's Law"})
    assert r.status_code == 201, r.text
    body = r.json()
    project, version, job = body["project"], body["version"], body["job"]
    assert project["title"] == "Ohm's Law"
    assert project["owner"] == {"id": alice.id, "username": "alice"}
    assert project["current_version"]["id"] == version["id"]
    assert version["number"] == 1 and version["status"] == "generating" and version["revision"] == 1
    assert job["kind"] == "generate_lecture" and job["status"] == "queued" and job["version_id"] == version["id"]
    with api.db() as db:
        src = db.execute(select(SourceDocument).where(SourceDocument.project_id == project["id"])).scalar_one()
        assert src.storage_key.startswith("private/source/")
        assert src.filename == "notes.txt" and src.mime == "text/plain"
        payload = db.get(Job, job["id"]).payload
        assert payload["source_document_id"] == src.id
        assert payload["base_revision"] == 1
        assert payload["options"]["target_minutes"] == 10
        refs = db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project["id"])).scalars().all()
        assert len(refs) == 1 and refs[0].startswith("source-")


def test_create_title_defaults_to_filename(api):
    _, c = api.editor("alice")
    r = create(c)
    assert r.json()["project"]["title"] == "notes"


def test_create_rejects_dangerous_or_invalid_files(api):
    _, c = api.editor("alice")
    html = create(c, file=("lecture.html", b"<!doctype html><script>alert(1)</script>", "text/html"))
    assert html.status_code == 415 and html.json()["code"] == "unsupported_type"
    svg = create(c, file=("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "image/svg+xml"))
    assert svg.status_code == 415
    fake_pdf = create(c, file=("x.pdf", b"not a pdf at all", "application/pdf"))
    assert fake_pdf.status_code in (415, 422)
    empty = create(c, file=("x.txt", b"", "text/plain"))
    assert empty.status_code == 422
    with api.db() as db:
        assert db.execute(select(Project)).first() is None


def test_create_rejects_invalid_options(api):
    _, c = api.editor("alice")
    bad_json = c.post("/api/projects", files={"file": TXT}, data={"options": "{not json"})
    assert bad_json.status_code == 422 and bad_json.json()["code"] == "validation"
    bad_value = create(c, options={"target_minutes": 1000})
    assert bad_value.status_code == 422
    assert bad_value.json()["detail"][0]["loc"][0] == "options"
    bad_lang = create(c, options={"language": "xx-XX"})
    assert bad_lang.status_code == 422


def _options_of(api, job_id: int) -> dict:
    with api.db() as db:
        return db.get(Job, job_id).payload["options"]


def test_model_overrides_are_stripped_for_editors(api):
    api.settings.llm_model_allowlist = ["gemini-2.5-pro"]
    _, c = api.editor("alice")
    r = create(c, options={"llm_model_plan": "gemini-2.5-pro", "llm_model_script": "gemini-2.5-pro"})
    opts = _options_of(api, r.json()["job"]["id"])
    assert opts["llm_model_plan"] is None and opts["llm_model_script"] is None


def test_model_overrides_kept_for_admins_only_when_allow_listed(api):
    api.settings.llm_model_allowlist = ["gemini-2.5-pro"]
    api.user("root", role="admin")
    c = api.login("root")
    r = create(c, options={"llm_model_plan": "gemini-2.5-pro", "llm_model_script": "some-unknown-model"})
    opts = _options_of(api, r.json()["job"]["id"])
    assert opts["llm_model_plan"] == "gemini-2.5-pro"
    assert opts["llm_model_script"] is None


def test_generation_rate_limit_per_hour(api):
    api.settings.generation_rate_limit_per_hour = 2
    _, c = api.editor("alice")
    assert create(c).status_code == 201
    assert create(c).status_code == 201
    r = create(c)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert "Retry-After" in r.headers


def test_generation_refused_when_daily_budget_is_spent(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        db.add(
            UsageEvent(
                user_id=alice.id, provider="fake", operation="llm", cost_usd=api.settings.daily_budget_usd_per_user + 1
            )
        )
        db.commit()
    r = create(c)
    assert r.status_code == 429
    assert r.json()["code"] == "budget"
    assert int(r.headers["Retry-After"]) > 0


def test_list_search_and_pagination(api):
    alice, c = api.editor("alice")
    bob = api.user("bob")
    with api.db() as db:
        add_project(db, alice, title="Ohm's Law")
        add_project(db, alice, title="Kirchhoff 100% laws")
        add_project(db, alice, title="Thermodynamics")
        add_project(db, bob, title="Bob's Ohm notes")
    r = c.get("/api/projects").json()
    assert r["total"] == 3 and len(r["items"]) == 3
    assert {p["owner"]["username"] for p in r["items"]} == {"alice"}
    item = r["items"][0]
    assert item["current_version"]["status"] == "ready"
    assert item["active_job"] is None
    assert c.get("/api/projects", params={"q": "ohm"}).json()["total"] == 1
    assert c.get("/api/projects", params={"q": "100%"}).json()["total"] == 1
    assert c.get("/api/projects", params={"q": "%"}).json()["total"] == 1
    page = c.get("/api/projects", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["items"]) == 1
    assert c.get("/api/projects", params={"limit": 0}).status_code == 422
    assert c.get("/api/projects", params={"limit": 500}).status_code == 422


def test_admin_lists_all_projects_unless_mine(api):
    alice = api.user("alice")
    api.user("root", role="admin")
    with api.db() as db:
        add_project(db, alice)
    admin = api.login("root")
    assert admin.get("/api/projects").json()["total"] == 1
    assert admin.get("/api/projects", params={"mine": True}).json()["total"] == 0


def test_project_detail_includes_versions_sources_jobs(api):
    alice, c = api.editor("alice")
    created = create(c).json()
    pid = created["project"]["id"]
    detail = c.get(f"/api/projects/{pid}").json()
    assert detail["project"]["id"] == pid
    assert [v["number"] for v in detail["versions"]] == [1]
    assert detail["sources"][0]["filename"] == "notes.txt"
    assert "storage_key" not in detail["sources"][0]
    assert detail["jobs"][0]["kind"] == "generate_lecture"
    assert detail["project"]["active_job"]["id"] == created["job"]["id"]


def test_patch_project_metadata_and_current_version(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        other, other_version = add_project(db, alice, title="Other")
    r = c.patch(f"/api/projects/{project.id}", json={"title": "Ohm's Law (revised)", "unit_name": "Unit 2"})
    assert r.status_code == 200
    assert r.json()["project"]["title"] == "Ohm's Law (revised)"
    assert r.json()["project"]["unit_name"] == "Unit 2"
    wrong = c.patch(f"/api/projects/{project.id}", json={"current_version_id": other_version.id})
    assert wrong.status_code == 422
    unknown_field = c.patch(f"/api/projects/{project.id}", json={"owner_id": 99})
    assert unknown_field.status_code == 422
    empty_title = c.patch(f"/api/projects/{project.id}", json={"title": "   "})
    assert empty_title.status_code == 422


def test_delete_soft_deletes_cancels_jobs_and_revokes_shares(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        job = add_job(db, kind="build_assets", status="queued", user=alice, project=project, version=version)
    share = c.post(f"/api/projects/{project.id}/shares", json={}).json()
    assert c.get(f"/api/public/watch/{share['token']}").status_code == 200
    assert c.delete(f"/api/projects/{project.id}").status_code == 204
    assert c.get(f"/api/projects/{project.id}").status_code == 404
    assert c.get(f"/api/versions/{version.id}").status_code == 404
    assert c.delete(f"/api/projects/{project.id}").status_code == 404
    assert c.get(f"/api/public/watch/{share['token']}").status_code == 404
    assert c.get("/api/projects").json()["total"] == 0
    with api.db() as db:
        assert db.get(Project, project.id).deleted_at is not None
        assert db.get(Job, job.id).status == "cancelled"
        assert (
            db.execute(select(ShareLink.revoked_at).where(ShareLink.token == share["token"])).scalar_one() is not None
        )


def test_regenerate_creates_next_version_from_latest_source(api):
    _, c = api.editor("alice")
    created = create(c).json()
    pid = created["project"]["id"]
    with api.db() as db:  # let the first generation "finish"
        db.get(Job, created["job"]["id"]).status = "succeeded"
        db.commit()
    r = c.post(f"/api/projects/{pid}/regenerate", json={"options": {"target_minutes": 20}})
    assert r.status_code == 201, r.text
    assert r.json()["version"]["number"] == 2
    assert _options_of(api, r.json()["job"]["id"])["target_minutes"] == 20
    again = c.post(f"/api/projects/{pid}/regenerate")
    assert again.json()["version"]["number"] == 3
    assert _options_of(api, again.json()["job"]["id"])["target_minutes"] == 20  # stored on the project


def test_regenerate_without_source_is_a_conflict(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, _ = add_project(db, alice)
    r = c.post(f"/api/projects/{project.id}/regenerate", json={})
    assert r.status_code == 409 and r.json()["code"] == "no_source"


def _import(c, payload, name: str = "lecture.json"):
    return c.post("/api/projects/import", files={"file": (name, json.dumps(payload).encode(), "application/json")})


def test_import_v2_screenplay_enqueues_build(api):
    _, c = api.editor("alice")
    r = _import(c, screenplay_dict())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["job"]["kind"] == "build_assets"
    assert body["version"]["status"] == "building"
    assert body["project"]["title"] == "Ohm's Law"
    assert body["warnings"] == []
    v = c.get(f"/api/versions/{body['version']['id']}").json()
    assert [s["id"] for s in v["screenplay"]["scenes"]] == ["s1", "s2", "q1"]


def test_import_rejects_an_unsupported_language(api):
    _, c = api.editor("alice")
    sp = screenplay_dict()
    sp["language"] = "xx-YY"
    r = _import(c, sp)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail[0]["loc"] == ["file", "language"] and "xx-YY" in detail[0]["msg"]
    with api.db() as db:
        assert db.execute(select(Project)).scalars().all() == []  # nothing half-created
        assert db.execute(select(Job)).scalars().all() == []


def test_import_language_is_consistent_across_project_version_and_options(api):
    _, c = api.editor("alice")
    sp = screenplay_dict()
    sp["language"] = "ta-IN"
    r = _import(c, sp)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"]["language"] == "ta-IN"
    with api.db() as db:
        project = db.get(Project, body["project"]["id"])
        assert project.language == "ta-IN" and project.settings["language"] == "ta-IN"


def test_import_legacy_v1_json_is_converted(api):
    from aadhi.legacy import is_legacy

    _, c = api.editor("alice")
    legacy = {"title": "Old lecture", "scenes": [{"title": "Intro", "narration": "Hello students, welcome back."}]}
    if not is_legacy(legacy):  # the real converter may need a richer v1 document
        legacy = json.loads((__import__("pathlib").Path("showcase_ohms_law.json")).read_text(encoding="utf-8"))
    r = _import(c, legacy)
    assert r.status_code == 201, r.text
    assert r.json()["warnings"] is not None


def test_import_rejects_invalid_documents(api):
    _, c = api.editor("alice")
    broken = c.post("/api/projects/import", files={"file": ("x.json", b"{nope", "application/json")})
    assert broken.status_code in (415, 422)
    invalid = _import(c, {"schema_version": 2, "scenes": [{"id": "s1", "type": "content", "beats": []}]})
    assert invalid.status_code == 422
    assert invalid.json()["detail"][0]["loc"][0] == "file"
    not_json = c.post("/api/projects/import", files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert not_json.status_code == 415


def test_import_strips_asset_keys_the_user_cannot_use(api):
    alice, c = api.editor("alice")
    bob = api.user("bob")
    with api.db() as db:
        mine, _ = add_project(db, alice)
        theirs, _ = add_project(db, bob)
        add_asset_ref(db, mine, "upload-mine", mime="image/png")
        add_asset_ref(db, theirs, "upload-theirs", mime="image/png")
    sp = screenplay_dict()
    sp["figures"] = [{"id": "f1", "asset_key": "upload-mine"}, {"id": "f2", "asset_key": "upload-theirs"}]
    r = _import(c, sp)
    assert r.status_code == 201, r.text
    assert len(r.json()["warnings"]) == 1
    v = c.get(f"/api/versions/{r.json()['version']['id']}").json()
    figures = {f["id"]: f["asset_key"] for f in v["screenplay"]["figures"]}
    assert figures == {"f1": "upload-mine", "f2": None}
    with api.db() as db:
        refs = (
            db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == r.json()["project"]["id"]))
            .scalars()
            .all()
        )
        assert refs == ["upload-mine"]
