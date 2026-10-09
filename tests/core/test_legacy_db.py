"""Import of a synthetic v1 projects.db (read-only) and the import_legacy job."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import bcrypt
import pytest
from sqlalchemy import select

from aadhi.auth.passwords import verify_password
from aadhi.jobs.base import FatalJobError, get_handler
from aadhi.legacy.jobs import import_legacy_job, screenplay_from_json
from aadhi.legacy.legacy_db import UNUSABLE_HASH, import_legacy_db
from aadhi.models import Project, ProjectVersion, User
from aadhi.storage.assets import Produced, bytes_key

V1_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(50) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject_name VARCHAR(255) NOT NULL, unit_name VARCHAR(255),
    session_number VARCHAR(50), session_title VARCHAR(255), json_data TEXT NOT NULL, created_at DATETIME,
    updated_at DATETIME
);
"""
ADMIN_PW = "v1-admin-password-leaked"
TEACHER_PW = "Teacher-Pass-123"


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=4, prefix=b"2b")).decode()


@pytest.fixture()
def v1_db(tmp_path: Path, showcase_json, demo_slides_json) -> Path:
    path = tmp_path / "v1" / "projects.db"
    path.parent.mkdir()
    con = sqlite3.connect(path)
    con.executescript(V1_SCHEMA)
    con.executemany(
        "INSERT INTO users (id, username, password_hash) VALUES (?, ?, ?)",
        [(1, "admin", _hash(ADMIN_PW)), (2, "teacher1", _hash(TEACHER_PW)), (3, "oddball", "plaintext-pw")],
    )
    rows = [
        (
            1,
            2,
            "Basic Electrical Engineering",
            "Electric Circuits",
            "Session 2",
            "Ohm's Law",
            json.dumps(showcase_json),
            "2025-06-24 23:03:00.123456",
        ),
        (2, 1, "Strength of Materials", None, None, None, json.dumps(demo_slides_json), "2025-06-25 10:00:00"),
        (3, 2, "Broken", "", "", "", "{not json", None),
        (
            4,
            99,
            "Orphan",
            "",
            "",
            "Orphan session",
            json.dumps({"scenes": [{"type": "title", "narration": "Hi"}]}),
            None,
        ),
        (5, 2, "Not a lecture", "", "", "", json.dumps({"title": "nothing"}), None),
    ]
    con.executemany(
        "INSERT INTO projects (id, user_id, subject_name, unit_name, session_number, session_title, json_data, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    con.commit()
    con.close()
    return path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_import_users_and_projects(app_env, db_session, v1_db):
    before = _digest(v1_db)
    files_before = sorted(p.name for p in v1_db.parent.iterdir())
    result = import_legacy_db(db_session, v1_db, app_env)
    assert _digest(v1_db) == before and sorted(p.name for p in v1_db.parent.iterdir()) == files_before

    assert sorted(result["users_created"]) == ["admin", "oddball", "teacher1"]
    users = {u.username: u for u in db_session.execute(select(User)).scalars()}
    admin, teacher, odd = users["admin"], users["teacher1"], users["oddball"]
    assert admin.role == "admin" and admin.must_change_password is True
    assert teacher.role == "editor" and teacher.must_change_password is False
    assert verify_password(TEACHER_PW, teacher.password_hash)
    # The v1 admin password is public: it is never imported and nobody can sign in until it is reset.
    assert admin.password_hash == UNUSABLE_HASH and not verify_password(ADMIN_PW, admin.password_hash)
    assert len(result["action_required"]) == 1 and "set-password admin" in result["action_required"][0]
    assert not any(e.get("legacy_user_id") == 1 for e in result["errors"])
    assert (
        odd.password_hash == UNUSABLE_HASH
        and odd.must_change_password
        and not verify_password("plaintext-pw", odd.password_hash)
    )

    assert result["projects_imported"] == 3 and len(result["project_ids"]) == 3
    errors = {e.get("legacy_project_id"): e["error"] for e in result["errors"] if "legacy_project_id" in e}
    assert set(errors) == {3, 5} and "conversion failed" in errors[3]
    projects = {p.settings["legacy_project_id"]: p for p in db_session.execute(select(Project)).scalars()}
    assert set(projects) == {1, 2, 4}
    ohm = projects[1]
    assert ohm.owner_id == teacher.id and ohm.title == "Ohm's Law" and ohm.session_number == "Session 2"
    assert ohm.created_at.year == 2025 and ohm.next_version_number == 2
    assert projects[2].owner_id == admin.id and projects[2].title == "Strength of Materials"
    assert projects[4].owner_id == admin.id  # orphan -> owner_fallback

    version = db_session.execute(select(ProjectVersion).where(ProjectVersion.project_id == ohm.id)).scalar_one()
    assert ohm.current_version_id == version.id
    assert (version.number, version.status, version.revision, version.built_revision) == (1, "draft", 1, None)
    sp = version.get_screenplay()
    assert sp is not None and len(sp.scenes) == 16 and sp.session_title.startswith("Ohm's Law")
    assert version.generation_meta["import"]["warning_count"] == len(version.issues)
    assert all(i["code"] == "legacy.import_warning" and i["severity"] == "info" for i in version.issues)
    assert version.issue_counts["info"] == len(version.issues)
    assert any(i["scene_id"] == "s7" for i in version.issues)


def test_leaked_v1_admin_password_never_signs_in(app_env, db_session, v1_db):
    """Deployment order `migrate -> import-legacy -> start`: the imported admin is the only admin, so
    the bootstrap is skipped and the publicly known v1 password must not work."""
    from aadhi.auth.bootstrap import ensure_admin
    from aadhi.auth.service import authenticate

    import_legacy_db(db_session, v1_db, app_env)
    ensure_admin(db_session, app_env)
    admins = db_session.execute(select(User).where(User.role == "admin")).scalars().all()
    assert [a.username for a in admins] == ["admin"]
    assert authenticate(db_session, "admin", ADMIN_PW) is None
    assert authenticate(db_session, "admin", "") is None
    assert authenticate(db_session, "teacher1", TEACHER_PW) is not None


def test_import_is_idempotent(app_env, db_session, v1_db):
    first = import_legacy_db(db_session, v1_db, app_env)
    second = import_legacy_db(db_session, v1_db, app_env)
    assert second["projects_imported"] == 0 and second["project_ids"] == []
    assert sorted(second["projects_skipped"]) == [1, 2, 4]
    assert second["users_created"] == [] and sorted(second["users_existing"]) == ["admin", "oddball", "teacher1"]
    assert db_session.query(Project).count() == len(first["project_ids"]) == 3


def test_existing_v2_admin_is_kept(app_env, db_session, v1_db, make_user):
    v2_admin = make_user("admin", password="Fresh-V2-Admin-77", role="admin")
    result = import_legacy_db(db_session, v1_db, app_env)
    assert "admin" in result["users_existing"]
    db_session.refresh(v2_admin)
    # The leaked v1 password is NOT imported over the existing account.
    assert verify_password("Fresh-V2-Admin-77", v2_admin.password_hash)
    assert not verify_password(ADMIN_PW, v2_admin.password_hash)
    owned = db_session.execute(select(Project).where(Project.owner_id == v2_admin.id)).scalars().all()
    assert {p.settings["legacy_project_id"] for p in owned} == {2, 4}


def test_missing_fallback_owner_reports_error(app_env, db_session, v1_db):
    result = import_legacy_db(db_session, v1_db, app_env, owner_fallback="nobody")
    assert any(e.get("legacy_project_id") == 4 and "no owner" in e["error"] for e in result["errors"])


def test_rejects_non_v1_database(app_env, db_session, tmp_path):
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    with pytest.raises(ValueError, match="not a v1"):
        import_legacy_db(db_session, other, app_env)
    with pytest.raises(FileNotFoundError):
        import_legacy_db(db_session, tmp_path / "missing.db", app_env)


def test_progress_callback(app_env, db_session, v1_db):
    seen: list[tuple[int, int]] = []
    import_legacy_db(db_session, v1_db, app_env, on_progress=lambda i, n: seen.append((i, n)))
    assert seen == [(i, 5) for i in range(1, 6)]


# --- job ----------------------------------------------------------------------------------


def test_job_handler_registered(monkeypatch):
    import aadhi.jobs.base as jobs_base

    # Load only this area's handler module so the check does not depend on other areas importing.
    monkeypatch.setattr(jobs_base, "HANDLER_MODULES", ("aadhi.legacy.jobs",))
    monkeypatch.setattr(jobs_base, "_LOADED", False)
    assert get_handler("import_legacy") is import_legacy_job


def test_job_imports_db(job_ctx, v1_db, db_session):
    job_ctx.payload = {"legacy_db_path": str(v1_db)}
    result = asyncio.run(import_legacy_job(job_ctx))
    assert len(result["project_ids"]) == 3
    assert job_ctx.events[-1]["progress"] == 1.0
    assert any(e["type"] == "log" and e["level"] == "warning" for e in job_ctx.events)
    assert db_session.query(Project).count() == 3


@pytest.mark.parametrize("payload", [{}, {"legacy_db_path": 5}, {"legacy_db_path": "C:/nope/projects.db"}])
def test_job_rejects_bad_payload(job_ctx, payload, tmp_path):
    job_ctx.payload = payload
    with pytest.raises(FatalJobError) as exc:
        asyncio.run(import_legacy_job(job_ctx))
    assert exc.value.code == "invalid_payload"


def test_job_rejects_non_db_file(job_ctx, tmp_path):
    txt = tmp_path / "projects.txt"
    txt.write_text("x", encoding="utf-8")
    job_ctx.payload = {"legacy_db_path": str(txt)}
    with pytest.raises(FatalJobError):
        asyncio.run(import_legacy_job(job_ctx))
    fake_db = tmp_path / "fake.db"
    fake_db.write_bytes(b"not sqlite at all" * 10)
    job_ctx.payload = {"legacy_db_path": str(fake_db)}
    with pytest.raises(FatalJobError, match="not a readable SQLite database"):
        asyncio.run(import_legacy_job(job_ctx))


def test_job_imports_json_asset(job_ctx, asset_store, make_user, showcase_json, db_session):
    user = make_user("alice")
    data = json.dumps(showcase_json).encode("utf-8")
    key = bytes_key("upload", data)
    asset_store.put(key, "upload", Produced(data=data, mime="application/json"))
    job_ctx.payload, job_ctx.user_id = {"json_asset_key": key}, user.id
    result = asyncio.run(import_legacy_job(job_ctx))
    (pid,) = result["project_ids"]
    project = db_session.get(Project, pid)
    assert project.owner_id == user.id and project.title.startswith("Ohm's Law")
    job_ctx.payload = {"json_asset_key": "upload-missing"}
    with pytest.raises(FatalJobError):
        asyncio.run(import_legacy_job(job_ctx))


def test_screenplay_from_json_accepts_v2_and_rejects_garbage(showcase_json):
    sp, warnings = screenplay_from_json(showcase_json)
    again, w2 = screenplay_from_json(sp.model_dump(mode="json"))
    assert w2 == [] and len(again.scenes) == len(sp.scenes)
    with pytest.raises(ValueError, match="not a v1 lecture"):
        screenplay_from_json({"scenes": "nope", "schema_version": 2})


def test_non_finite_json_is_rejected_per_project(app_env, db_session, v1_db):
    con = sqlite3.connect(v1_db)
    con.execute(
        "INSERT INTO projects (id, user_id, subject_name, json_data) VALUES (6, 2, 'NaN', ?)",
        ('{"scenes": [{"type": "content", "narration": "x", "html": "<p>x</p>"}], "score": NaN}',),
    )
    con.execute(
        "INSERT INTO projects (id, user_id, subject_name, json_data) VALUES (7, 2, 'Overflow', ?)",
        ('{"scenes": [{"type": "content", "narration": "x", "html": "<p>x</p>", "w": 1e999}]}',),
    )
    con.commit()
    con.close()
    result = import_legacy_db(db_session, v1_db, app_env)
    errors = {e.get("legacy_project_id"): e["error"] for e in result["errors"] if "legacy_project_id" in e}
    assert "NaN is not valid JSON" in errors[6] and "out of range" in errors[7]
    assert result["projects_imported"] == 3  # the other projects are unaffected


def test_job_parses_off_the_event_loop(job_ctx, asset_store, make_user, showcase_json, v1_db, monkeypatch):
    import threading

    import aadhi.legacy.jobs as legacy_jobs

    seen: dict[str, bool] = {}
    real_convert, real_db_path = legacy_jobs.screenplay_from_json, legacy_jobs._db_path

    def spy_convert(data):
        seen["convert_off_loop"] = threading.current_thread() is not threading.main_thread()
        return real_convert(data)

    def spy_db_path(payload):
        seen["db_path_off_loop"] = threading.current_thread() is not threading.main_thread()
        return real_db_path(payload)

    monkeypatch.setattr(legacy_jobs, "screenplay_from_json", spy_convert)
    monkeypatch.setattr(legacy_jobs, "_db_path", spy_db_path)
    user = make_user("bob")
    data = json.dumps(showcase_json).encode("utf-8")
    key = bytes_key("upload", data)
    asset_store.put(key, "upload", Produced(data=data, mime="application/json"))
    job_ctx.payload, job_ctx.user_id = {"json_asset_key": key}, user.id
    asyncio.run(import_legacy_job(job_ctx))
    job_ctx.payload = {"legacy_db_path": str(v1_db)}
    asyncio.run(import_legacy_job(job_ctx))
    assert seen == {"convert_off_loop": True, "db_path_off_loop": True}


@pytest.mark.parametrize("raw", [b'{"scenes": [], "x": Infinity}', b"[1e400]", b"\xff\xfe"])
def test_job_rejects_non_finite_or_undecodable_json(job_ctx, asset_store, make_user, raw):
    user = make_user("carol")
    key = bytes_key("upload", raw)
    asset_store.put(key, "upload", Produced(data=raw, mime="application/json"))
    job_ctx.payload, job_ctx.user_id = {"json_asset_key": key}, user.id
    with pytest.raises(FatalJobError) as exc:
        asyncio.run(import_legacy_job(job_ctx))
    assert exc.value.code == "invalid_payload"
