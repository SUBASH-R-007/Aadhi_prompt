"""Version detail, screenplay edits (revision CAS, busy, asset keys), lint, timeline + ETag, preview."""

from __future__ import annotations

import copy

from aadhi.models import ProjectVersion
from aadhi.schemas.manifest import AssetManifest
from tests.api.factories import add_asset_ref, add_job, add_project, make_screenplay, screenplay_dict


def scene_hashes(sp) -> dict[str, str]:
    from aadhi.pipeline.assets import scene_hash

    try:
        return {s.id: scene_hash(s, sp.lexicon) for s in sp.scenes}
    except TypeError:  # implementations without the lexicon parameter
        return {s.id: scene_hash(s) for s in sp.scenes}


def setup_version(api, **kw):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, **kw)
    return alice, c, project, version


def test_get_version_returns_summary_and_documents(api):
    _, c, project, version = setup_version(api)
    r = c.get(f"/api/versions/{version.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == version.id and body["project_id"] == project.id
    assert body["revision"] == 1 and body["built_revision"] == 1 and body["timeline_stale"] is False
    assert body["has_timeline"] is True
    assert body["issue_counts"] == {"error": 0, "warning": 0, "info": 0}
    assert [s["id"] for s in body["screenplay"]["scenes"]] == ["s1", "s2", "q1"]
    assert body["stale_scenes"] == ["s1", "s2", "q1"]  # no asset manifest yet
    assert body["plan"] is None


def test_stale_scenes_follow_the_manifest_hashes(api):
    _, c, project, version = setup_version(api)
    sp = make_screenplay()
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.set_manifest(AssetManifest(scene_hashes=scene_hashes(sp)))
        db.commit()
    assert c.get(f"/api/versions/{version.id}").json()["stale_scenes"] == []
    edited = screenplay_dict()
    edited["scenes"][1]["beats"][1]["narration"] = "A different sentence now."
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 200, r.text
    assert r.json()["stale_scenes"] == ["s2"]
    assert c.get(f"/api/versions/{version.id}").json()["stale_scenes"] == ["s2"]


def test_put_screenplay_bumps_revision_and_marks_timeline_stale(api):
    _, c, project, version = setup_version(api)
    edited = screenplay_dict(title="Ohm's Law, revised")
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"]["revision"] == 2
    assert body["version"]["timeline_stale"] is True
    assert isinstance(body["issues"], list)
    stored = c.get(f"/api/versions/{version.id}").json()
    assert stored["screenplay"]["session_title"] == "Ohm's Law, revised"
    assert stored["revision"] == 2


def test_put_screenplay_revision_conflict(api):
    _, c, project, version = setup_version(api)
    first = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    assert first.status_code == 200
    stale = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"
    assert stale.json()["current_revision"] == 2


def test_put_screenplay_refused_while_a_job_mutates_the_version(api):
    alice, c, project, version = setup_version(api)
    with api.db() as db:
        job = add_job(db, kind="build_assets", status="running", user=alice, project=project, version=version)
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    assert r.status_code == 409
    assert r.json()["code"] == "version_busy"
    assert r.json()["job_id"] == job.id


def test_render_jobs_do_not_make_the_version_busy(api):
    alice, c, project, version = setup_version(api)
    with api.db() as db:
        add_job(db, kind="render_video", status="running", user=alice, project=project, version=version)
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    assert r.status_code == 200


def test_put_screenplay_validation_errors(api):
    _, c, project, version = setup_version(api)
    bad = screenplay_dict()
    bad["scenes"][0]["beats"][0]["board_item_id"] = "missing-item"
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": bad, "revision": 1})
    assert r.status_code == 422
    assert r.json()["code"] == "validation"
    missing_rev = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict()})
    assert missing_rev.status_code == 422


def _with_figure(key: str) -> dict:
    sp = screenplay_dict()
    sp["figures"] = [{"id": "f1", "caption": "Circuit", "asset_key": key}]
    return sp


def _with_panel(key: str) -> dict:
    sp = screenplay_dict()
    sp["scenes"][0]["side_panel"] = {"kind": "image", "override_asset_key": key, "rationale": "shows it"}
    return sp


def test_asset_keys_must_be_referenced_by_the_project(api):
    alice, c, project, version = setup_version(api)
    bob = api.user("bob")
    with api.db() as db:
        other, _ = add_project(db, bob)
        add_asset_ref(db, project, "upload-ok", kind="upload", mime="image/png")
        add_asset_ref(db, other, "upload-bob", kind="upload", mime="image/png")
        add_asset_ref(db, project, "tts-audio", kind="tts", mime="audio/mpeg")
        add_asset_ref(db, project, "upload-video", kind="upload", mime="video/mp4")
    ok = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_figure("upload-ok"), "revision": 1})
    assert ok.status_code == 200, ok.text
    foreign = c.put(
        f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_figure("upload-bob"), "revision": 2}
    )
    assert foreign.status_code == 422
    assert foreign.json()["detail"][0]["loc"] == ["body", "screenplay", "figures", 0, "asset_key"]
    unknown = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_panel("nope"), "revision": 2})
    assert unknown.status_code == 422
    wrong_kind = c.put(
        f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_panel("tts-audio"), "revision": 2}
    )
    assert wrong_kind.status_code == 422
    assert wrong_kind.json()["detail"][0]["type"] == "asset_key.kind"
    video_figure = c.put(
        f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_figure("upload-video"), "revision": 2}
    )
    assert video_figure.status_code == 422
    panel_video = c.put(
        f"/api/versions/{version.id}/screenplay", json={"screenplay": _with_panel("upload-video"), "revision": 2}
    )
    assert panel_video.status_code == 200


def test_preview_also_authorises_asset_keys(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": _with_panel("upload-elsewhere")})
    assert r.status_code == 422


def test_json_body_limit_is_enforced(api):
    _, c, project, version = setup_version(api)
    api.settings.max_json_body_mb = 1
    huge = {"screenplay": screenplay_dict(), "revision": 1, "padding": "x" * (2 * 1024 * 1024)}
    r = c.put(f"/api/versions/{version.id}/screenplay", json=huge)
    assert r.status_code == 413
    assert r.json()["code"] == "too_large"


def test_lint_returns_issues_for_unsaved_screenplay(api):
    _, c, project, version = setup_version(api)
    sp = screenplay_dict()
    sp["scenes"][0]["board"] = [{"id": f"i{n}", "kind": "bullet", "text": f"Point number {n}"} for n in range(12)]
    sp["scenes"][0]["beats"][0]["board_item_id"] = "i0"
    r = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": sp})
    assert r.status_code == 200
    codes = {i["code"] for i in r.json()["issues"]}
    assert "board.too_many_items" in codes
    assert c.get(f"/api/versions/{version.id}").json()["revision"] == 1  # nothing saved


def test_timeline_etag_and_conditional_get(api):
    _, c, project, version = setup_version(api)
    r = c.get(f"/api/versions/{version.id}/timeline")
    assert r.status_code == 200, r.text
    etag = r.headers["ETag"]
    assert etag.startswith('"r1-b1-') and len(etag.strip('"').split("-")[-1]) == 12
    assert r.json()["scenes"][0]["scene_id"] == "s1"
    again = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers["ETag"] == etag
    weak = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": f"W/{etag}"})
    assert weak.status_code == 304
    other = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": '"r0-b0-000000000000"'})
    assert other.status_code == 200
    c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    changed = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["ETag"].startswith('"r2-b1-')


def test_timeline_etag_changes_when_a_rebuild_rewrites_the_timeline(api):
    """A rebuild at the same revision (e.g. one scene re-rendered) still yields a new tag."""
    from sqlalchemy import update

    _, c, project, version = setup_version(api)
    etag = c.get(f"/api/versions/{version.id}/timeline").headers["ETag"]
    timeline = make_timeline_dict(version.id)
    timeline["scenes"][0]["title"] = "Rebuilt"
    with api.db() as db:  # how the pipeline writes: a Core UPDATE (updated_at bumped by onupdate)
        db.execute(update(ProjectVersion).where(ProjectVersion.id == version.id).values(timeline=timeline))
        db.commit()
    rebuilt = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": etag})
    assert rebuilt.status_code == 200
    assert rebuilt.headers["ETag"] != etag and rebuilt.headers["ETag"].startswith('"r1-b1-')
    assert rebuilt.json()["scenes"][0]["title"] == "Rebuilt"


def test_conditional_timeline_get_does_not_load_the_document(api):
    """304s are computed from cheap columns: the (deferred) timeline column is never selected."""
    from sqlalchemy import event

    from aadhi.db import get_engine

    _, c, project, version = setup_version(api)
    etag = c.get(f"/api/versions/{version.id}/timeline").headers["ETag"]
    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        r = c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": etag})
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert r.status_code == 304
    selected = [s for s in statements if "project_versions" in s and s.lstrip().upper().startswith("SELECT")]
    assert selected and not any("project_versions.timeline" in s for s in selected)


def make_timeline_dict(version_id: int) -> dict:
    from tests.api.factories import make_timeline

    return make_timeline(make_screenplay(), version_id=version_id, revision=1).model_dump(mode="json")


def test_put_screenplay_cannot_change_the_language(api):
    _, c, project, version = setup_version(api)
    edited = screenplay_dict()
    edited["language"] = "hi-IN"
    r = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail[0]["loc"] == ["body", "screenplay", "language"] and "translate" in detail[0]["msg"]
    with api.db() as db:
        stored = db.get(ProjectVersion, version.id)
        assert stored.revision == 1 and stored.language == "en-IN"
        assert stored.get_screenplay().language == "en-IN"
    preview = c.post(f"/api/versions/{version.id}/timeline/preview", json={"screenplay": edited})
    assert preview.status_code == 422 and preview.json()["detail"][0]["loc"] == ["body", "screenplay", "language"]
    same = screenplay_dict()
    same["language"] = "en-IN"
    assert c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": same, "revision": 1}).status_code == 200


def test_put_screenplay_keeps_a_non_default_version_language(api):
    alice, c = api.editor("alice")
    sp = make_screenplay()
    with api.db() as db:
        project, version = add_project(
            db, alice, screenplay=sp.model_copy(update={"language": "ta-IN"}), language="ta-IN"
        )
    edited = screenplay_dict()
    edited["language"] = "ta-IN"
    assert (
        c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 1}).status_code == 200
    )
    edited["language"] = "en-IN"  # the schema default would silently switch it back
    assert (
        c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": edited, "revision": 2}).status_code == 422
    )


def test_timeline_404_when_never_built(api):
    _, c, project, version = setup_version(api, built=False, status="draft")
    r = c.get(f"/api/versions/{version.id}/timeline")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_timeline_urls_are_resolved_at_serve_time(api, asset_store):
    from aadhi.storage.assets import Produced

    _, c, project, version = setup_version(api)
    asset = asset_store.put("tts-scene-audio-1", "scene_audio", Produced(data=b"ID3fake-mp3", mime="audio/mpeg"))
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        tl = copy.deepcopy(v.timeline)
        tl["scenes"][0]["audio_asset_key"] = asset.key
        v.timeline = tl
        db.commit()
    body = c.get(f"/api/versions/{version.id}/timeline").json()
    assert body["scenes"][0]["audio_url"] == f"/media/{asset.storage_key}"
    with api.db() as db:
        assert db.get(ProjectVersion, version.id).timeline["scenes"][0].get("audio_url") is None  # never stored


def test_preview_timeline_is_estimated(api):
    _, c, project, version = setup_version(api)
    r = c.post(
        f"/api/versions/{version.id}/timeline/preview", json={"screenplay": screenplay_dict(n_content=1, quiz=False)}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estimated"] is True
    assert [s["scene_id"] for s in body["scenes"]] == ["s1"]


def test_source_refs_are_checked_against_the_generation_extract(api, asset_store):
    import json

    from aadhi.api.screenplay_service import clear_chunk_cache
    from aadhi.storage.assets import Produced

    clear_chunk_cache()
    chunks = [{"id": "c0001", "text": "Ohm"}, {"id": "c0002", "text": "Law"}]
    payload = json.dumps({"markdown": "# Ohm", "chunks": chunks}).encode()
    asset_store.put("extract-api-test", "extract", Produced(data=payload, mime="application/json"))
    alice, c, project, version = setup_version(api)
    with api.db() as db:
        db.get(ProjectVersion, version.id).generation_meta = {"ingest_key": "extract-api-test"}
        db.commit()
        _, imported = add_project(db, alice, title="Imported")  # no extract (like an imported lecture)
    sp = screenplay_dict()
    sp["scenes"][0]["intent"] = {"goal": "Introduce the law", "source_refs": ["c0001", "c0042"]}

    def unknown_refs(issues):
        return [i["message"] for i in issues if i["code"] == "source.unknown_ref"]

    linted = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": sp}).json()["issues"]
    assert len(unknown_refs(linted)) == 1 and "c0042" in unknown_refs(linted)[0]
    assert "c0001" not in unknown_refs(linted)[0]
    saved = c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": sp, "revision": 1})
    assert saved.status_code == 200 and len(unknown_refs(saved.json()["issues"])) == 1
    # Without a known extract (imports) only the ref format can be checked.
    assert unknown_refs(c.post(f"/api/versions/{imported.id}/lint", json={"screenplay": sp}).json()["issues"]) == []
    sp["scenes"][0]["intent"]["source_refs"] = ["page-3"]
    assert len(unknown_refs(c.post(f"/api/versions/{imported.id}/lint", json={"screenplay": sp}).json()["issues"])) == 1


def test_chunk_id_cache_reads_each_extract_once(api, asset_store, monkeypatch):
    import json

    from aadhi.api import screenplay_service
    from aadhi.storage.assets import Produced

    screenplay_service.clear_chunk_cache()
    payload = json.dumps({"markdown": "", "chunks": [{"id": "c0007", "text": "x"}]}).encode()
    asset_store.put("extract-cache-test", "extract", Produced(data=payload, mime="application/json"))
    reads: list[str] = []
    original = asset_store.storage.get_bytes
    monkeypatch.setattr(asset_store.storage, "get_bytes", lambda key: reads.append(key) or original(key))
    meta = {"ingest_key": "extract-cache-test"}
    assert screenplay_service.source_chunk_ids(asset_store, meta) == {"c0007"}
    assert screenplay_service.source_chunk_ids(asset_store, meta) == {"c0007"}
    assert len(reads) == 1
    assert screenplay_service.source_chunk_ids(asset_store, {"ingest_key": "extract-missing"}) is None
    assert screenplay_service.source_chunk_ids(asset_store, {}) is None
    screenplay_service.clear_chunk_cache()


def test_teacher_edit_reintroducing_a_removed_header_value_is_flagged(api, asset_store):
    """Values the source-scoping step removed (e.g. the SME's name) must not creep back via edits."""
    import json

    from aadhi.api.screenplay_service import clear_chunk_cache
    from aadhi.storage.assets import Produced

    clear_chunk_cache()
    extract = {
        "markdown": "# Ohm's law\nVoltage equals current times resistance.",
        "chunks": [{"id": "c0001", "text": "Voltage equals current times resistance."}],
        "excluded": [{"category": "person", "text": "SME Name: Dr. Ramesh Kumar", "reason": "author", "source": "rules"}],
    }
    asset_store.put("extract-leak-test", "extract", Produced(data=json.dumps(extract).encode(), mime="application/json"))
    _, c, _, version = setup_version(api)
    with api.db() as db:
        db.get(ProjectVersion, version.id).generation_meta = {"ingest_key": "extract-leak-test"}
        db.commit()
    sp = screenplay_dict()
    sp["scenes"][0]["beats"][0]["narration"] = "Welcome! This lesson was prepared for you by Dr. Ramesh Kumar."
    issues = c.post(f"/api/versions/{version.id}/lint", json={"screenplay": sp}).json()["issues"]
    leaks = [i for i in issues if i["code"] == "content.admin_leak"]
    assert leaks, issues
    assert all("Ramesh" not in i["message"] for i in leaks)  # the message never repeats the personal value
