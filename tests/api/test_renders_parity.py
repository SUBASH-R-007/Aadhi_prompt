"""Render admission limits, preflight, soft-caption option, capabilities, and Render-row settlement."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from aadhi.models import Job, ProjectVersion, Render
from tests.api.factories import add_job, add_project


def setup_version(api, username: str = "alice", **kw):
    user, c = api.editor(username)
    with api.db() as db:
        project, version = add_project(db, user, **kw)
    return user, c, project, version


def add_versions(api, user, n: int) -> list[int]:
    ids = []
    with api.db() as db:
        for _ in range(n):
            _, version = add_project(db, user)
            ids.append(version.id)
    return ids


# --- admission ----------------------------------------------------------------------------------


def test_per_user_render_limit(api):
    user, c, _, version = setup_version(api)
    api.settings.render_max_per_user = 2
    others = add_versions(api, user, 2)
    assert c.post(f"/api/versions/{version.id}/render", json={}).status_code == 202
    assert c.post(f"/api/versions/{others[0]}/render", json={}).status_code == 202
    busy = c.post(f"/api/versions/{others[1]}/render", json={})
    assert busy.status_code == 429 and busy.json()["code"] == "render_busy"
    assert busy.headers["Retry-After"] == "60" and busy.json()["limit"] == 2
    with api.db() as db:  # nothing was created for the refused request
        assert db.execute(select(Render).where(Render.version_id == others[1])).first() is None
    # another teacher is not affected; finishing a render frees a slot
    bob, cb, _, bob_version = setup_version(api, "bob")
    assert cb.post(f"/api/versions/{bob_version.id}/render", json={}).status_code == 202
    with api.db() as db:
        job = db.execute(select(Job).where(Job.version_id == version.id)).scalar_one()
        job.status = "succeeded"
        db.commit()
    assert c.post(f"/api/versions/{others[1]}/render", json={}).status_code == 202


def test_global_render_queue_limit_and_zero_disables(api):
    user, c, _, version = setup_version(api)
    api.settings.render_max_per_user = 0
    api.settings.render_max_queued = 1
    other = add_versions(api, user, 1)[0]
    assert c.post(f"/api/versions/{version.id}/render", json={}).status_code == 202
    full = c.post(f"/api/versions/{other}/render", json={})
    assert full.status_code == 429 and full.json()["code"] == "render_queue_full"
    api.settings.render_max_queued = 0
    assert c.post(f"/api/versions/{other}/render", json={}).status_code == 202


def test_one_render_per_version_still_wins(api):
    _, c, _, version = setup_version(api)
    api.settings.render_max_per_user = 1
    assert c.post(f"/api/versions/{version.id}/render", json={}).status_code == 202
    again = c.post(f"/api/versions/{version.id}/render", json={})
    assert again.status_code == 409 and again.json()["code"] == "job_in_progress"


# --- preflight / options --------------------------------------------------------------------------


def _degrade(api, version_id: int) -> None:
    with api.db() as db:
        v = db.get(ProjectVersion, version_id)
        tl = v.get_timeline()
        v.set_timeline(tl.model_copy(update={"meta": {**tl.meta, "fallbacks": [
            {"scene_id": "s2", "reason": "no_audio"}, {"scene_id": "s1", "reason": "ai_video_without_media"}]}}))
        db.commit()


def test_preflight_endpoint(api):
    _, c, _, version = setup_version(api)
    clean = c.get(f"/api/versions/{version.id}/render/preflight").json()
    assert clean == {"version_id": version.id, "has_timeline": True, "timeline_stale": False, "blocking": False,
                     "items": [], "quality": []}
    _degrade(api, version.id)
    body = c.get(f"/api/versions/{version.id}/render/preflight").json()
    assert body["blocking"] is True
    assert [(i["scene_id"], i["reason"], i["blocking"]) for i in body["items"]] == [
        ("s1", "ai_video_without_media", False), ("s2", "no_audio", True)]
    assert body["items"][1]["title"] == "Scene 2" and "silent" in body["items"][1]["message"]
    _, cb = api.editor("mallory")
    assert cb.get(f"/api/versions/{version.id}/render/preflight").status_code == 404


def test_preflight_of_an_unbuilt_version(api):
    _, c, _, version = setup_version(api, built=False, status="draft")
    body = c.get(f"/api/versions/{version.id}/render/preflight").json()
    assert body["has_timeline"] is False and body["timeline_stale"] is True and body["items"] == []


def test_preflight_lists_open_quality_issues_apart_from_the_video_items(api):
    _, c, _, version = setup_version(api)
    with api.db() as db:
        v = db.get(ProjectVersion, version.id)
        v.issues = [
            {"severity": "info", "code": "terminology.casing", "message": "A note.", "scene_id": "s1", "source": "lint"},
            {"severity": "warning", "code": "abbreviation_conflict", "message": "AC means two things.",
             "scene_id": "s2", "source": "lint"},
            {"severity": "error", "code": "board.too_many_items", "message": "Too many items.", "scene_id": "s1",
             "source": "lint"},
            {"severity": "warning", "code": "assets.media_suspect", "message": "Blank picture.", "scene_id": "s1",
             "source": "assets"},
        ]
        db.commit()
    body = c.get(f"/api/versions/{version.id}/render/preflight").json()
    assert body["items"] == [] and body["blocking"] is False  # the video itself is unaffected
    assert [(q["code"], q["severity"], q["scene_id"], q["reason"], q["blocking"]) for q in body["quality"]] == [
        ("board.too_many_items", "error", "s1", "quality", False),
        ("abbreviation_conflict", "warning", "s2", "quality", False),
    ]
    assert body["quality"][1]["title"] == "Scene 2" and body["quality"][1]["scene_index"] == 1
    _, unbuilt_client, _, unbuilt = setup_version(api, "carol", built=False, status="draft")
    with api.db() as db:
        db.get(ProjectVersion, unbuilt.id).issues = [
            {"severity": "warning", "code": "x", "message": "Check this.", "scene_id": "s1", "source": "lint"}]
        db.commit()
    quality = unbuilt_client.get(f"/api/versions/{unbuilt.id}/render/preflight").json()["quality"]
    assert [(q["scene_id"], q["title"], q["scene_index"]) for q in quality] == [("s1", "s1", None)]


def test_allow_degraded_false_refuses_silent_scenes(api):
    _, c, _, version = setup_version(api)
    _degrade(api, version.id)
    refused = c.post(f"/api/versions/{version.id}/render", json={"allow_degraded": False})
    assert refused.status_code == 409 and refused.json()["code"] == "render_preflight"
    assert [i["reason"] for i in refused.json()["items"]] == ["ai_video_without_media", "no_audio"]
    with api.db() as db:
        assert db.execute(select(Render)).first() is None
    ok = c.post(f"/api/versions/{version.id}/render", json={})  # the default renders anyway
    assert ok.status_code == 202


def test_soft_subtitles_option_is_stored_only_when_set(api):
    _, c, _, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/render", json={"soft_subtitles": False, "allow_degraded": True})
    assert r.status_code == 202, r.text
    with api.db() as db:
        row = db.get(Render, r.json()["render"]["id"])
        assert row.options == {"burn_captions": False, "include_intro": True, "soft_subtitles": False}
        assert db.get(Job, r.json()["job"]["id"]).payload["soft_subtitles"] is False
    assert c.post(f"/api/versions/{version.id}/render", json={"bogus": 1}).status_code == 422


# --- capabilities ---------------------------------------------------------------------------------


def _caps(ok: bool):
    return SimpleNamespace(ok=ok, reasons=[] if ok else ["ffmpeg was not found ('ffmpeg')."],
                           checks={"ffmpeg": ok}, burn_captions=ok)


def test_capabilities_endpoint(api, monkeypatch):
    from aadhi.api.routers import renders

    seen = []

    def fake(settings, *, browser=True):
        seen.append(browser)
        return _caps(False)

    monkeypatch.setattr(renders, "host_render_capabilities", fake)
    _, c = api.editor("alice")
    body = c.get("/api/renders/capabilities").json()
    assert body["available"] is False and body["advisory"] is True and "checks" not in body
    assert body["reasons"] == ["Video rendering is not set up on this server; ask an administrator."]
    assert seen == [False]  # external workers: no Chromium probe on the API host
    _, admin = api.editor("root", role="admin")
    full = admin.get("/api/renders/capabilities").json()
    assert full["reasons"] == ["ffmpeg was not found ('ffmpeg')."] and full["checks"] == {"ffmpeg": False}


def test_inline_mode_refuses_renders_the_host_cannot_do(api, monkeypatch):
    from aadhi.api.routers import renders

    _, c, _, version = setup_version(api)
    monkeypatch.setattr(renders, "host_render_capabilities", lambda settings, browser=True: _caps(False))
    assert c.post(f"/api/versions/{version.id}/render", json={}).status_code == 202  # external: never refused
    with api.db() as db:
        for job in db.execute(select(Job)).scalars():
            job.status = "succeeded"
        db.commit()
    monkeypatch.setattr(api.settings, "worker_mode", "inline")
    r = c.post(f"/api/versions/{version.id}/render", json={})
    assert r.status_code == 503 and r.json()["code"] == "render_unavailable"
    # tool names and configured paths only for admins (as GET /api/renders/capabilities)
    assert r.json()["detail"] == "Video rendering is not set up on this server; ask an administrator."
    _, admin = api.editor("root", role="admin")
    r = admin.post(f"/api/versions/{version.id}/render", json={})
    assert r.status_code == 503 and "ffmpeg" in r.json()["detail"]


# --- Render rows settle when their job ends outside the handler -----------------------------------


@pytest.fixture()
def render_job(api):
    user, c, project, version = setup_version(api)
    r = c.post(f"/api/versions/{version.id}/render", json={})
    assert r.status_code == 202
    return SimpleNamespace(user=user, client=c, render_id=r.json()["render"]["id"], job_id=r.json()["job"]["id"],
                           version=version, project=project)


def render_status(api, rid: int) -> str:
    with api.db() as db:
        return db.get(Render, rid).status


def test_cancelling_a_queued_render_settles_its_row(api, render_job):
    r = render_job.client.post(f"/api/jobs/{render_job.job_id}/cancel")
    assert r.status_code in (200, 202) and r.json()["job"]["status"] == "cancelled", r.text
    assert render_status(api, render_job.render_id) == "cancelled"
    listing = render_job.client.get(f"/api/versions/{render_job.version.id}/renders").json()["items"]
    assert listing[0]["status"] == "cancelled"
    retried = render_job.client.post(f"/api/jobs/{render_job.job_id}/retry")
    assert retried.status_code in (200, 201, 202), retried.text
    assert render_status(api, render_job.render_id) == "queued"  # the retry resets it


def test_worker_side_failure_and_reaper_settle_the_row(api, render_job):
    import datetime as dt

    from aadhi.db import session_scope
    from aadhi.jobs.lease import Lease
    from aadhi.jobs.queue import claim_next, fail_job, reap_stale

    with session_scope() as db:
        job_id, attempt = claim_next(db, "host:boot:1", ["render_video"])
    with session_scope() as db:
        assert fail_job(db, Lease(job_id, "host:boot:1", attempt), error="no handler", error_code="unknown_kind")
    assert render_status(api, render_job.render_id) == "failed"

    with api.db() as db:  # a second render whose worker died with no attempts left
        _, version = add_project(db, render_job.user)
    r2 = render_job.client.post(f"/api/versions/{version.id}/render", json={})
    rid2, jid2 = r2.json()["render"]["id"], r2.json()["job"]["id"]
    with api.db() as db:
        job = db.get(Job, jid2)
        job.status, job.locked_by, job.attempts, job.max_attempts = "running", "host:boot:2", 2, 2
        job.heartbeat_at = job.locked_at = dt.datetime.now(dt.UTC) - dt.timedelta(hours=1)
        db.get(Render, rid2).status = "running"
        db.commit()
    with session_scope() as db:
        out = reap_stale(db, stale_seconds=60)
    assert out["failed"] == [jid2]
    assert render_status(api, rid2) == "failed"


def test_settle_never_touches_succeeded_or_other_jobs_renders(api, render_job):
    from aadhi.db import session_scope
    from aadhi.jobs.queue import JobRef, settle_render

    with api.db() as db:
        db.get(Render, render_job.render_id).status = "succeeded"
        other = add_job(db, kind="render_video", status="failed", user=render_job.user)
        db.commit()
        other_id = other.id
    with session_scope() as db:
        assert settle_render(db, JobRef(render_job.job_id, "render_video", None), "cancelled") is False
        assert settle_render(db, JobRef(other_id, "render_video", None), "failed") is False
        assert settle_render(db, JobRef(render_job.job_id, "build_assets", None), "failed") is False
        assert settle_render(db, JobRef(render_job.job_id, "render_video", None), "succeeded") is False
    assert render_status(api, render_job.render_id) == "succeeded"


# --- a retried render passes the same admission as a new one ------------------------------------


def _failed_render_job(api, user, project, version) -> int:
    with api.db() as db:
        render = Render(version_id=version.id, status="failed")
        db.add(render)
        db.commit()
        return add_job(db, kind="render_video", status="failed", user=user, project=project, version=version,
                       payload={"render_id": render.id, "burn_captions": False, "include_intro": True}).id


def test_retrying_a_render_respects_the_render_limits(api):
    user, c, project, version = setup_version(api)
    failed = _failed_render_job(api, user, project, version)
    api.settings.render_max_per_user = 1
    other = add_versions(api, user, 1)[0]
    assert c.post(f"/api/versions/{other}/render", json={}).status_code == 202
    busy = c.post(f"/api/jobs/{failed}/retry")
    assert busy.status_code == 429 and busy.json()["code"] == "render_busy" and busy.headers["Retry-After"] == "60"
    api.settings.render_max_per_user = 0
    api.settings.render_max_queued = 1
    full = c.post(f"/api/jobs/{failed}/retry")
    assert full.status_code == 429 and full.json()["code"] == "render_queue_full"
    api.settings.render_max_queued = 0
    assert c.post(f"/api/jobs/{failed}/retry").status_code == 202  # refusals did not use up the single retry


def test_retrying_a_render_while_the_version_renders_is_job_in_progress(api):
    user, c, project, version = setup_version(api)
    failed = _failed_render_job(api, user, project, version)
    running = c.post(f"/api/versions/{version.id}/render", json={}).json()["job"]["id"]
    r = c.post(f"/api/jobs/{failed}/retry")
    assert r.status_code == 409 and r.json()["code"] == "job_in_progress" and r.json()["job_id"] == running


# --- /api/meta features.render ------------------------------------------------------------------


def test_meta_render_feature_uses_the_full_check_with_inline_workers(api, monkeypatch):
    from aadhi.api.routers import meta
    from aadhi.compose import capabilities

    _, c = api.editor("alice")
    seen = []

    def fake(settings, *, browser=True, force=False):
        seen.append(browser)
        return _caps(False)

    monkeypatch.setattr(capabilities, "render_capabilities", fake)
    monkeypatch.setattr(meta, "_ffmpeg_available", lambda path: True)
    assert c.get("/api/meta").json()["features"]["render"] is True  # external workers: advisory ffmpeg check only
    assert seen == []
    monkeypatch.setattr(api.settings, "worker_mode", "inline")
    assert c.get("/api/meta").json()["features"]["render"] is False  # exactly when POST /render answers 503
    assert seen == [True]


def test_render_admission_takes_a_postgres_advisory_lock_before_counting():
    """Concurrent requests for different versions must not all pass the per-user / queue caps on PostgreSQL."""
    from types import SimpleNamespace

    from sqlalchemy.dialects import postgresql

    from aadhi.api.routers import renders
    from aadhi.config import Settings

    class FakeSession:
        def __init__(self, dialect: str) -> None:
            self.dialect = dialect
            self.sql: list[str] = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

        def execute(self, stmt):
            self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
            return SimpleNamespace(scalar_one=lambda: 0)

    settings = Settings(_env_file=None, render_max_per_user=2, render_max_queued=10)
    pg = FakeSession("postgresql")
    renders.check_admission(pg, 1, settings)
    assert "pg_advisory_xact_lock" in pg.sql[0] and all("count" in q for q in pg.sql[1:]) and len(pg.sql) == 3
    lite = FakeSession("sqlite")
    renders.check_admission(lite, 1, settings)
    assert not any("advisory" in q for q in lite.sql)
    off = FakeSession("postgresql")
    renders.check_admission(off, 1, Settings(_env_file=None, render_max_per_user=0, render_max_queued=0))
    assert off.sql == []  # no limits: nothing to serialise
    assert renders.RENDER_ADMISSION_LOCK_KEY != 0x0AAD_C1EA  # the cleanup scheduler's key
