"""MP4 renders (stale guard, list, download) and version exports."""

from __future__ import annotations

from aadhi.models import Job, ProjectVersion, Render
from aadhi.storage.assets import Produced
from tests.api.factories import add_project, screenplay_dict


def setup_version(api, **kw):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, **kw)
    return alice, c, project, version


def test_render_requires_a_current_timeline(api):
    _, c, project, version = setup_version(api)
    c.put(f"/api/versions/{version.id}/screenplay", json={"screenplay": screenplay_dict(), "revision": 1})
    r = c.post(f"/api/versions/{version.id}/render", json={})
    assert r.status_code == 409 and r.json()["code"] == "timeline_stale"


def test_render_never_built_is_stale(api):
    _, c, project, version = setup_version(api, built=False, status="draft")
    assert c.post(f"/api/versions/{version.id}/render", json={}).json()["code"] == "timeline_stale"


def test_render_creates_render_and_job(api):
    _, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/render", json={"burn_captions": True, "include_intro": False})
    assert r.status_code == 202, r.text
    render, job = r.json()["render"], r.json()["job"]
    assert render["status"] == "queued" and render["built_revision"] == 1 and render["downloads"] == {}
    assert job["kind"] == "render_video"
    with api.db() as db:
        row = db.get(Render, render["id"])
        assert row.job_id == job["id"] and row.options == {"burn_captions": True, "include_intro": False}
        assert db.get(Job, job["id"]).payload == {
            "render_id": render["id"],
            "burn_captions": True,
            "include_intro": False,
        }
    again = c.post(f"/api/versions/{version.id}/render", json={})
    assert again.status_code == 409 and again.json()["code"] == "job_in_progress"
    listing = c.get(f"/api/versions/{version.id}/renders").json()["items"]
    assert [x["id"] for x in listing] == [render["id"]]
    assert listing[0]["job"]["id"] == job["id"]


def test_concurrent_render_requests_start_exactly_one_render(api, monkeypatch):
    """Double click / two tabs: the check-then-insert runs under the version's row (writer) lock."""
    import threading
    import time

    from sqlalchemy import func, select

    from aadhi.api.routers import renders

    _, _, project, version = setup_version(api)
    tabs = [api.login("alice"), api.login("alice")]
    original = renders.active_render_job

    def slow_check(db, version_id):
        found = original(db, version_id)
        time.sleep(0.6)  # widen the race window: without the lock both requests pass this check
        return found

    monkeypatch.setattr(renders, "active_render_job", slow_check)
    barrier = threading.Barrier(len(tabs))
    results: list[tuple[int, str]] = []

    def click(client) -> None:
        barrier.wait()
        r = client.post(f"/api/versions/{version.id}/render", json={})
        results.append((r.status_code, r.json().get("code", "")))

    threads = [threading.Thread(target=click, args=(tab,)) for tab in tabs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [(202, ""), (409, "job_in_progress")], results
    with api.db() as db:
        assert db.execute(select(func.count()).select_from(Render)).scalar_one() == 1
        assert db.execute(select(func.count()).select_from(Job).where(Job.kind == "render_video")).scalar_one() == 1


def test_render_lock_does_not_bump_updated_at(api):
    """The lock is a no-op write: the timeline ETag (derived from updated_at) stays valid."""
    _, c, project, version = setup_version(api)
    etag = c.get(f"/api/versions/{version.id}/timeline").headers["ETag"]
    assert c.post(f"/api/versions/{version.id}/render", json={}).status_code == 202
    assert c.get(f"/api/versions/{version.id}/timeline", headers={"If-None-Match": etag}).status_code == 304


def _finished_render(api, asset_store, version_id: int) -> Render:
    video = asset_store.put(
        "render-test-video", "render", Produced(data=b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, mime="video/mp4")
    )
    srt = asset_store.put(
        "captions-test-srt",
        "captions",
        Produced(data=b"1\n00:00:00,000 --> 00:00:01,000\nHi\n", mime="application/x-subrip"),
    )
    with api.db() as db:
        render = Render(
            version_id=version_id,
            status="succeeded",
            video_asset_key=video.key,
            srt_asset_key=srt.key,
            duration_s=12.5,
            chapters_text="00:00 Intro",
        )
        db.add(render)
        db.commit()
        db.refresh(render)
        return render


def test_download_render_files_named_after_the_project(api, asset_store):
    _, c, project, version = setup_version(api, title='Ohm\'s Law: "V = IR" / basics')
    render = _finished_render(api, asset_store, version.id)
    listing = c.get(f"/api/versions/{version.id}/renders").json()["items"][0]
    assert listing["downloads"] == {
        "video": f"/api/renders/{render.id}/download?file=video",
        "srt": f"/api/renders/{render.id}/download?file=srt",
    }
    r = c.get(listing["downloads"]["video"])
    assert r.status_code == 200
    assert r.headers["content-type"] == "video/mp4"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert 'filename="Ohm\'s Law V IR basics.mp4"' in disposition
    assert r.content.startswith(b"\x00\x00\x00\x18ftyp")
    srt = c.get(listing["downloads"]["srt"])
    assert srt.status_code == 200 and srt.headers["content-disposition"].endswith('.srt"')
    assert c.get(f"/api/renders/{render.id}/download?file=vtt").status_code == 404
    assert c.get(f"/api/renders/{render.id}/download?file=exe").status_code == 422
    assert c.get("/api/renders/999999/download").status_code == 404


def test_unicode_titles_get_rfc6266_filenames(api, asset_store):
    _, c, project, version = setup_version(api, title="ஓம் விதி")
    render = _finished_render(api, asset_store, version.id)
    disposition = c.get(f"/api/renders/{render.id}/download").headers["content-disposition"]
    assert "filename*=UTF-8''" in disposition


def test_companion_markdown_and_html(api):
    _, c, project, version = setup_version(api)
    md = c.get(f"/api/versions/{version.id}/companion.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert md.headers["content-disposition"].startswith("attachment;")
    html = c.get(f"/api/versions/{version.id}/companion.html")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    csp = html.headers["content-security-policy"]
    assert csp.startswith("sandbox allow-scripts;") and "default-src 'none'" in csp


def test_export_json_roundtrips_through_import(api):
    _, c, project, version = setup_version(api)
    r = c.get(f"/api/versions/{version.id}/export.json")
    assert r.status_code == 200
    assert r.headers["content-disposition"].startswith("attachment;")
    exported = r.json()
    assert [s["id"] for s in exported["scenes"]] == ["s1", "s2", "q1"]
    imported = c.post("/api/projects/import", files={"file": ("export.json", r.content, "application/json")})
    assert imported.status_code == 201, imported.text


def test_chapters_txt_from_timeline(api):
    _, c, project, version = setup_version(api)
    r = c.get(f"/api/versions/{version.id}/chapters.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith("00:00")


def test_exports_404_without_documents(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice, built=False, status="generating")
        db.get(ProjectVersion, version.id).screenplay = None
        db.commit()
    for path in ("companion.md", "companion.html", "export.json", "chapters.txt"):
        assert c.get(f"/api/versions/{version.id}/{path}").status_code == 404, path


class RemoteStorage:
    """Wraps local storage but behaves like a remote backend (no local paths, presigned URLs)."""

    name = "s3"

    def __init__(self, inner) -> None:
        self.inner = inner

    def local_path(self, key):
        return None

    def signed_url(self, key, ttl_seconds, *, download_name=None):
        return f"https://bucket.example.com/{key}?ttl={ttl_seconds}&name={download_name}"

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_remote_storage_redirects_to_presigned_urls(api, asset_store):
    from urllib.parse import unquote

    from aadhi.db import get_sessionmaker
    from aadhi.storage.assets import AssetStore

    _, c, project, version = setup_version(api)
    render = _finished_render(api, asset_store, version.id)
    api.app.state.asset_store = AssetStore(RemoteStorage(asset_store.storage), get_sessionmaker())
    r = c.get(f"/api/renders/{render.id}/download", follow_redirects=False)
    assert r.status_code == 302
    location = unquote(r.headers["location"])
    assert location.startswith("https://bucket.example.com/assets/render/")
    assert f"ttl={api.settings.s3_presign_ttl_seconds}" in location
    assert location.endswith("name=Ohm's Law.mp4")
    media_key = asset_store.get("render-test-video").storage_key
    media = c.get(f"/media/{media_key}", follow_redirects=False)
    assert media.status_code == 302 and media.headers["location"].startswith("https://bucket.example.com/")
    assert c.get("/media/assets/render/missing/x.mp4", follow_redirects=False).status_code == 404
