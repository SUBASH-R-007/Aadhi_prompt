"""Source report (GET /api/projects/{id}/versions/{vid}/source-report), the source review (PUT /source-review,
POST /approve-source, plan endpoints refused meanwhile) and lectures started from pasted notes."""

from __future__ import annotations

import json

import pytest

from aadhi.models import Job, ProjectVersion
from aadhi.pipeline import source_review as sr
from aadhi.pipeline.base import BriefConcept, ConceptBrief, IngestResult, SkippedChunk
from aadhi.pipeline.chunking import chunk_markdown
from aadhi.storage.assets import Produced
from tests.api.factories import add_job, add_project

MARKDOWN = ("## Voltage\n\nVoltage pushes charge around a circuit and is measured in volts.\n\n"
            "## Coming up next\n\nIn the next video we look at power.\n\n"
            "## Power\n\nPower is P = V × I where P is the power in watts. The SME Ravi Kumar recorded this part.\n")
EXTRACT_KEY = "extract-source-report-test"


@pytest.fixture(autouse=True)
def _fresh_caches():
    from aadhi.api.routers.projects import clear_source_analysis_cache
    from aadhi.api.screenplay_service import clear_chunk_cache

    clear_chunk_cache()
    clear_source_analysis_cache()
    yield
    clear_chunk_cache()
    clear_source_analysis_cache()


def ingest() -> IngestResult:
    from aadhi.pipeline.base import ExcludedItem

    return IngestResult(markdown=MARKDOWN, chunks=chunk_markdown(MARKDOWN), source_mime="text/markdown",
                        detected_language="en-IN",
                        excluded=[ExcludedItem(category="person", text="SME Name: Ravi Kumar", reason="author")])


def brief() -> ConceptBrief:
    return ConceptBrief(concepts=[BriefConcept(key="voltage", name="Voltage", source_refs=["c0001"]),
                                  BriefConcept(key="power", name="Power", source_refs=["c0003"])],
                        teaching_order=["voltage", "power"],
                        skipped_chunks=[SkippedChunk(chunk_id="c0002", reason="scaffolding")])


def setup(api, asset_store, *, awaiting: bool = False, user: str = "alice"):
    asset_store.put(EXTRACT_KEY, "extract", Produced(data=ingest().model_dump_json().encode(), mime="application/json"))
    alice, c = api.editor(user)
    with api.db() as db:
        project, version = add_project(db, alice, built=False, status="awaiting_review" if awaiting else "ready")
        v = db.get(ProjectVersion, version.id)
        meta = {"ingest_key": EXTRACT_KEY, "brief": brief().model_dump(mode="json", exclude_defaults=True)}
        if awaiting:
            meta[sr.REVIEW_STAGE_KEY] = sr.REVIEW_STAGE_SOURCE
            v.screenplay = None
        v.generation_meta = meta
        db.commit()
        job = None
        if awaiting:
            job = add_job(db, kind="generate_lecture", status="awaiting_review", user=alice, project=project,
                          version=version, payload={"source_document_id": 7, "options": {"language": "en-IN"},
                                                    "base_revision": 1, "review_source": True},
                          result={"stage": sr.RESUME_STAGE, "version_id": version.id, "base_revision": 1})
    return alice, c, project, version, job


def report_url(project, version) -> str:
    return f"/api/projects/{project.id}/versions/{version.id}/source-report"


def test_source_report_shows_how_the_source_was_read(api, asset_store):
    _, c, project, version, _ = setup(api, asset_store)
    r = c.get(report_url(project, version))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] and body["review"] == {"active": False, "editable": False}
    report = body["report"]
    assert [s["title"] for s in report["outline"]["sections"]] == ["Voltage", "Coming up next", "Power"]
    assert {ch["id"]: ch["status"] for ch in report["chunks"]} == {
        "c0001": "teaching", "c0002": "set_aside", "c0003": "teaching"}
    assert report["accounting"]["skipped_by_reason"] == {"scaffolding": 1}
    assert report["scope"] == [{"category": "person", "count": 1, "label": sr.SCOPE_LABELS["person"], "source": "rules"}]
    # "P = V × I" inside a sentence is inventoried (inline formulas, REVIEW_VERSION 3): V and I are never explained
    assert [(f["expression"], f["undefined_symbols"]) for f in report["inventory"]["formulas"]] == [
        ("P = V × I", ["V", "I"])]
    assert report["readiness"]["verdict"] == "missing_context"
    assert len(report["scenes"]) == 3  # the version's screenplay: which parts each scene cites
    assert "Ravi" not in r.text and "SME Name" not in r.text  # removed values are never served


def test_source_report_authorization_and_imports(api, asset_store):
    alice, c, project, version, _ = setup(api, asset_store)
    _, bob = api.editor("bob")
    assert bob.get(report_url(project, version)).status_code == 404
    with api.db() as db:
        other, _ = add_project(db, api.user("carol"), title="Other")
        imported_project, imported = add_project(db, alice, title="Imported")  # no extract (like an import)
    assert c.get(f"/api/projects/{other.id}/versions/{version.id}/source-report").status_code == 404
    body = c.get(report_url(imported_project, imported)).json()
    assert body["available"] is False and body["reason"] == "no_source" and body["report"] is None


def test_source_report_of_a_translation_follows_its_source_version(api, asset_store):
    _, c, project, version, _ = setup(api, asset_store)
    with api.db() as db:
        tr = ProjectVersion(project_id=project.id, number=2, status="ready", language="ta-IN", revision=1,
                generation_meta={"source_version_id": version.id})
        db.add(tr)
        db.commit()
        tid = tr.id
    body = c.get(f"/api/projects/{project.id}/versions/{tid}/source-report").json()
    assert body["available"] and len(body["report"]["chunks"]) == 3


def test_save_source_review_validates_and_stores_corrections(api, asset_store):
    _, c, project, version, _ = setup(api, asset_store, awaiting=True)
    url = f"/api/versions/{version.id}/source-review"
    body = c.get(report_url(project, version)).json()
    assert body["review"] == {"active": True, "editable": True}
    r = c.put(url, json={"excluded_chunk_ids": ["c0003"], "restored_chunk_ids": ["c0002"],
                         "concept_names": {"voltage": "Electric voltage"}})
    assert r.status_code == 200, r.text
    assert r.json()["source_overrides"]["excluded_chunk_ids"] == ["c0003"]
    with api.db() as db:
        stored = db.get(ProjectVersion, version.id).generation_meta[sr.OVERRIDES_KEY]
    assert stored["ingest_key"] == EXTRACT_KEY and stored["concept_names"] == {"voltage": "Electric voltage"}
    report = c.get(report_url(project, version)).json()["report"]
    assert {ch["id"]: ch["status"] for ch in report["chunks"]} == {
        "c0001": "teaching", "c0002": "restored", "c0003": "set_aside_by_you"}
    assert [x["name"] for x in report["concepts"]] == ["Electric voltage", "Power"]
    assert report["concepts"][1]["dropped"] is True
    for bad in ({"excluded_chunk_ids": ["c0099"]}, {"excluded_chunk_ids": ["c0001", "c0002", "c0003"]},
                {"restored_chunk_ids": ["c0001"]}, {"concept_names": {"nope": "X"}},
                {"concept_names": {"voltage": "x" * 161}}, {"excluded_chunk_ids": ["../x"]}, {"surprise": 1}):
        assert c.put(url, json=bad).status_code == 422, bad


def test_source_review_endpoints_refuse_other_states(api, asset_store):
    _, c, project, version, _ = setup(api, asset_store)
    r = c.put(f"/api/versions/{version.id}/source-review", json={})
    assert r.status_code == 409 and r.json()["code"] == "not_awaiting_review"
    assert c.post(f"/api/versions/{version.id}/approve-source").status_code == 409
    _, bob = api.editor("bob")
    assert bob.put(f"/api/versions/{version.id}/source-review", json={}).status_code == 404


def test_approve_source_continues_generation(api, asset_store):
    _, c, project, version, job = setup(api, asset_store, awaiting=True)
    # the plan endpoints cannot approve (or replace the plan of) a version waiting for its source review
    assert c.post(f"/api/versions/{version.id}/approve-plan").status_code == 409
    plan = {"session_title": "x", "chapters": []}
    assert c.post(f"/api/versions/{version.id}/plan", json={"plan": plan}).status_code == 409
    detail = c.get(f"/api/projects/{project.id}").json()
    assert detail["versions"][0]["review_stage"] == "source"
    r = c.post(f"/api/versions/{version.id}/approve-source")
    assert r.status_code == 202, r.text
    new_job = r.json()["job"]
    assert new_job["kind"] == "generate_lecture" and new_job["id"] != job.id
    with api.db() as db:
        payload = dict(db.get(Job, new_job["id"]).payload)
        assert db.get(Job, job.id).status == "succeeded"
        assert db.get(ProjectVersion, version.id).status == "generating"
    assert payload["resume_state"]["stage"] == sr.RESUME_STAGE and payload["source_document_id"] == 7
    assert c.post(f"/api/versions/{version.id}/approve-source").status_code == 409
    assert "review_stage" not in c.get(f"/api/projects/{project.id}").json()["versions"][0]


def test_every_version_summary_says_what_the_review_waits_for(api, asset_store):
    # The projects list (current_version) and the version itself link to the right review page.
    _, c, project, version, _ = setup(api, asset_store, awaiting=True)
    listed = {p["id"]: p for p in c.get("/api/projects").json()["items"]}
    assert listed[project.id]["current_version"]["review_stage"] == "source"
    assert c.get(f"/api/versions/{version.id}").json()["review_stage"] == "source"
    with api.db() as db:
        db.get(ProjectVersion, version.id).status = "ready"
        db.commit()
    listed = {p["id"]: p for p in c.get("/api/projects").json()["items"]}
    assert "review_stage" not in listed[project.id]["current_version"]
    assert "review_stage" not in c.get(f"/api/versions/{version.id}").json()


def test_a_plan_review_is_reported_as_such(api, asset_store):
    _, c, project, version, _ = setup(api, asset_store)
    with api.db() as db:
        db.get(ProjectVersion, version.id).status = "awaiting_review"
        db.commit()
    assert c.get(f"/api/projects/{project.id}").json()["versions"][0]["review_stage"] == "plan"
    assert c.post(f"/api/versions/{version.id}/approve-source").status_code == 409


# --- pasted notes and review_source on create / regenerate ------------------------------------------------

PASTED = "Photosynthesis\n\n1. Light reactions\n\nChlorophyll absorbs light. ஒளிச்சேர்க்கை தாவரங்களில் நடக்கிறது.\n"


def create(c, text: str, *, name: str = "photosynthesis.txt", **data):
    return c.post("/api/projects", files={"file": (name, text.encode("utf-8"), "text/plain")},
                  data={"options": json.dumps({"language": "en-IN"}), **data})


def test_pasted_notes_start_a_lecture_through_the_normal_upload(api):
    _, c = api.editor("alice")
    r = create(c, PASTED, title="Photosynthesis")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["project"]["title"] == "Photosynthesis"
    with api.db() as db:
        payload = dict(db.get(Job, body["job"]["id"]).payload)
    assert "review_source" not in payload  # unchanged payload unless asked
    r = create(c, PASTED, review_source="true")
    with api.db() as db:
        assert db.get(Job, r.json()["job"]["id"]).payload["review_source"] is True


def test_pasted_notes_with_control_characters_or_markup_are_refused(api):
    _, c = api.editor("alice")
    r = create(c, "Notes\x07 with a bell")
    assert r.status_code in (415, 422) and "control characters" in r.text
    r = create(c, "<html><body>notes</body></html>")
    assert r.status_code in (415, 422) and "HTML" in r.text


def test_regenerate_can_ask_for_a_source_review(api):
    _, c = api.editor("alice")
    created = create(c, PASTED).json()
    pid = created["project"]["id"]
    r = c.post(f"/api/projects/{pid}/regenerate", json={"review_source": True})
    assert r.status_code == 201, r.text
    with api.db() as db:
        assert db.get(Job, r.json()["job"]["id"]).payload["review_source"] is True
    r = c.post(f"/api/projects/{pid}/regenerate", json={})
    with api.db() as db:
        assert "review_source" not in db.get(Job, r.json()["job"]["id"]).payload
