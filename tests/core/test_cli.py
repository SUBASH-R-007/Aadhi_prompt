"""CLI smoke tests (python -m aadhi.cli ...)."""

from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import inspect, select

from aadhi import cli
from aadhi.auth.passwords import verify_password
from aadhi.config import ROOT_DIR, get_settings
from aadhi.db import reset_engine_cache
from aadhi.models import Asset, Job, Project, User, utcnow

from .test_legacy_db import V1_SCHEMA


def _alembic_head() -> str:
    """The newest migration (later migrations may follow 0002)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def run(monkeypatch, capsys, *argv: str, stdin: str | None = None) -> tuple[int, str, str]:
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    code = cli.main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_create_admin_and_list_users(app_env, db_session, monkeypatch, capsys):
    code, out, _ = run(
        monkeypatch,
        capsys,
        "create-admin",
        "--username",
        "principal",
        "--password-stdin",
        stdin="Very-Strong-Admin-Pass-1\n",
    )
    assert code == 0 and "created admin" in out
    user = db_session.execute(select(User).where(User.username == "principal")).scalar_one()
    assert user.role == "admin" and verify_password("Very-Strong-Admin-Pass-1", user.password_hash)
    code, out, _ = run(monkeypatch, capsys, "list-users", "--json")
    rows = json.loads(out)
    assert code == 0 and rows[0]["username"] == "principal" and rows[0]["role"] == "admin"
    code, out, _ = run(monkeypatch, capsys, "list-users")
    assert "principal" in out and "USERNAME" in out
    code, _, err = run(
        monkeypatch,
        capsys,
        "create-admin",
        "--username",
        "principal",
        "--password-stdin",
        stdin="Very-Strong-Admin-Pass-1\n",
    )
    assert code == 2 and "already exists" in err


def test_create_admin_rejects_weak_password(app_env, monkeypatch, capsys):
    code, _, err = run(monkeypatch, capsys, "create-admin", "--username", "boss", "--password-stdin", stdin="boss1\n")
    assert code == 2 and "password rejected" in err
    code, _, err = run(monkeypatch, capsys, "create-admin", "--username", "boss", "--password-stdin", stdin="\n")
    assert code == 2 and "no password" in err


def test_password_prompt(app_env, monkeypatch, capsys):
    answers = iter(["Prompted-Password-42", "Different-Password-42"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(answers))
    code, _, err = run(monkeypatch, capsys, "create-admin", "--username", "boss")
    assert code == 2 and "do not match" in err
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": "Prompted-Password-42")
    assert run(monkeypatch, capsys, "create-admin", "--username", "boss")[0] == 0


def test_set_password_revokes_sessions(app_env, db_session, make_user, monkeypatch, capsys):
    user = make_user("admin", role="admin", must_change_password=True)
    code, out, _ = run(
        monkeypatch, capsys, "set-password", "admin", "--password-stdin", stdin="Rotated-Principal-Pass-2026\n"
    )
    assert code == 0 and "signed out" in out
    db_session.refresh(user)
    assert verify_password("Rotated-Principal-Pass-2026", user.password_hash)
    assert user.token_version == 1 and user.must_change_password is False
    code, _, err = run(
        monkeypatch, capsys, "set-password", "ghost", "--password-stdin", stdin="Rotated-Principal-Pass-2026\n"
    )
    assert code == 2 and "no such user" in err
    code, _, _ = run(
        monkeypatch,
        capsys,
        "set-password",
        "admin",
        "--must-change",
        "--password-stdin",
        stdin="Another-Principal-Pass-2026\n",
    )
    db_session.refresh(user)
    assert code == 0 and user.must_change_password is True


def test_import_json(app_env, db_session, make_user, monkeypatch, capsys, tmp_path, showcase_json):
    make_user("teacher")
    path = tmp_path / "lecture.json"
    path.write_text(json.dumps(showcase_json), encoding="utf-8")
    code, out, _ = run(monkeypatch, capsys, "import-json", str(path), "--owner", "teacher", "--title", "Ohm")
    data = json.loads(out)
    assert code == 0 and data["project_id"] and isinstance(data["warnings"], list)
    assert db_session.get(Project, data["project_id"]).title == "Ohm"
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": 1}', encoding="utf-8")
    code, _, err = run(monkeypatch, capsys, "import-json", str(bad), "--owner", "teacher")
    assert code == 2 and "cannot import" in err
    nan = tmp_path / "nan.json"
    nan.write_text('{"scenes": [{"type": "content", "narration": "x", "html": "<p>x</p>"}], "n": NaN}', "utf-8")
    code, _, err = run(monkeypatch, capsys, "import-json", str(nan), "--owner", "teacher")
    assert code == 2 and "NaN is not valid JSON" in err
    assert run(monkeypatch, capsys, "import-json", str(path), "--owner", "ghost")[0] == 2
    assert run(monkeypatch, capsys, "import-json", str(tmp_path / "x.json"), "--owner", "teacher")[0] == 2


def test_import_legacy(app_env, monkeypatch, capsys, tmp_path, showcase_json):
    db_path = tmp_path / "projects.db"
    con = sqlite3.connect(db_path)
    con.executescript(V1_SCHEMA)
    con.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'teacher', 'x')")
    con.execute(
        "INSERT INTO projects (id, user_id, subject_name, json_data) VALUES (1, 1, 'S', ?)",
        (json.dumps(showcase_json),),
    )
    con.commit()
    con.close()
    code, out, err = run(monkeypatch, capsys, "import-legacy", "--db", str(db_path))
    result = json.loads(out)
    assert code == 0 and result["projects_imported"] == 1 and result["users_created"] == ["teacher"]
    assert result["action_required"] == [] and "ACTION REQUIRED" not in err
    assert run(monkeypatch, capsys, "import-legacy", "--db", str(tmp_path / "missing.db"))[0] == 2
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"junk" * 100)
    code, _, err = run(monkeypatch, capsys, "import-legacy", "--db", str(junk))
    assert code == 2 and "SQLite" in err


def test_import_legacy_admin_needs_new_password(app_env, db_session, monkeypatch, capsys, tmp_path):
    import bcrypt

    db_path = tmp_path / "projects.db"
    con = sqlite3.connect(db_path)
    con.executescript(V1_SCHEMA)
    leaked = bcrypt.hashpw(b"publicly-known-pw", bcrypt.gensalt(rounds=4)).decode()
    con.execute("INSERT INTO users (id, username, password_hash) VALUES (1, 'admin', ?)", (leaked,))
    con.commit()
    con.close()
    code, out, err = run(monkeypatch, capsys, "import-legacy", "--db", str(db_path))
    assert code == 0 and json.loads(out)["users_created"] == ["admin"]
    assert "ACTION REQUIRED" in err and "set-password admin" in err
    admin = db_session.execute(select(User).where(User.username == "admin")).scalar_one()
    assert not verify_password("publicly-known-pw", admin.password_hash)
    # The documented recovery step makes the account usable.
    code, _, _ = run(
        monkeypatch, capsys, "set-password", "admin", "--password-stdin", stdin="Fresh-Principal-Pass-2026\n"
    )
    db_session.refresh(admin)
    assert code == 0 and verify_password("Fresh-Principal-Pass-2026", admin.password_hash)
    assert admin.role == "admin" and admin.must_change_password is False


def test_verify_assets(app_env, asset_store, db_session, monkeypatch, capsys):
    from aadhi.storage.assets import Produced

    good = asset_store.put("upload-good", "upload", Produced(data=b"x", mime="text/plain"))
    db_session.add(
        Asset(key="upload-gone", kind="upload", storage_key="assets/upload/upload-gone/a.txt", mime="text/plain")
    )
    db_session.commit()
    code, out, _ = run(monkeypatch, capsys, "verify-assets")
    assert code == 1 and "1 missing" in out and "upload-gone" in out
    code, out, _ = run(monkeypatch, capsys, "verify-assets", "--delete-missing")
    assert code == 0 and "deleted 1" in out
    keys = [a.key for a in db_session.execute(select(Asset)).scalars()]
    assert keys == [good.key]
    assert run(monkeypatch, capsys, "verify-assets")[0] == 0


def test_cleanup_enqueues(app_env, db_session, monkeypatch, capsys):
    code, out, _ = run(monkeypatch, capsys, "cleanup")
    assert code == 0 and "enqueued cleanup job" in out
    job = db_session.execute(select(Job).where(Job.kind == "cleanup")).scalar_one()
    assert job.status == "queued"


def _stale_jobs(db_session) -> dict[str, Job]:
    old = utcnow() - dt.timedelta(hours=2)
    jobs = {
        "fresh": Job(
            kind="build_assets", status="running", attempts=1, max_attempts=2, heartbeat_at=utcnow(), locked_by="w"
        ),
        "retry": Job(kind="cleanup", status="running", attempts=1, max_attempts=2, heartbeat_at=old, locked_by="w"),
        "dead": Job(kind="render_video", status="running", attempts=2, max_attempts=2, locked_at=old, locked_by="w"),
        "cancel": Job(
            kind="cleanup",
            status="running",
            attempts=1,
            max_attempts=3,
            heartbeat_at=old,
            locked_by="w",
            cancel_requested=True,
        ),
        "queued": Job(kind="cleanup", status="queued", attempts=0, max_attempts=2, created_at=old),
    }
    db_session.add_all(jobs.values())
    db_session.commit()
    return jobs


def test_purge_stale_jobs_uses_job_system_reaper(app_env, db_session, make_user, monkeypatch, capsys):
    from aadhi.jobs.queue import reap_stale
    from aadhi.models import JobEvent, ProjectVersion

    assert cli._job_reaper() is reap_stale
    owner = make_user("teacher")
    project = Project(owner_id=owner.id, title="Ohm")
    db_session.add(project)
    db_session.flush()
    version = ProjectVersion(project_id=project.id, number=1, status="building", revision=1, built_revision=None)
    db_session.add(version)
    db_session.flush()
    jobs = _stale_jobs(db_session)
    building = Job(
        kind="build_assets",
        status="running",
        attempts=2,
        max_attempts=2,
        locked_by="w",
        locked_at=utcnow() - dt.timedelta(hours=3),
        project_id=project.id,
        version_id=version.id,
    )
    db_session.add(building)
    db_session.commit()

    code, out, _ = run(monkeypatch, capsys, "purge-stale-jobs", "--dry-run")
    stale_ids = sorted([jobs["retry"].id, jobs["dead"].id, jobs["cancel"].id, building.id])
    assert code == 0 and json.loads(out)["stale"] == stale_ids
    assert db_session.get(Job, jobs["retry"].id).status == "running"  # dry run changes nothing

    calls: list[dict] = []

    def spy(db, **kwargs):
        calls.append(kwargs)
        return reap_stale(db, **kwargs)

    monkeypatch.setattr(cli, "_job_reaper", lambda: spy)
    monkeypatch.setattr(cli, "REAP_BATCH", 2)  # forces several batches
    code, out, _ = run(monkeypatch, capsys, "purge-stale-jobs", "--older-than", "600")
    result = json.loads(out)
    assert code == 0 and calls and all(c["stale_seconds"] == 600.0 and c["limit"] == 2 for c in calls)
    assert len(calls) >= 2
    assert result["stale"] == stale_ids
    assert result["requeued"] == [jobs["retry"].id]
    assert result["failed"] == sorted([jobs["dead"].id, building.id])
    assert result["cancelled"] == [jobs["cancel"].id]
    db_session.expire_all()
    status = {name: db_session.get(Job, j.id).status for name, j in jobs.items()}
    assert status == {
        "fresh": "running",
        "retry": "queued",
        "dead": "failed",
        "cancel": "cancelled",
        "queued": "queued",
    }
    # The job system settles the version and records events; a cancelled job is never requeued.
    assert db_session.get(ProjectVersion, version.id).status == "failed"
    events = db_session.execute(select(JobEvent.job_id)).scalars().all()
    assert {jobs["retry"].id, jobs["dead"].id, jobs["cancel"].id, building.id} <= set(events)


def test_purge_stale_jobs_fallback_without_job_system(app_env, db_session, monkeypatch, capsys):
    jobs = _stale_jobs(db_session)
    monkeypatch.setitem(sys.modules, "aadhi.jobs.queue", None)  # simulate a missing job system
    assert cli._job_reaper() is None
    code, out, _ = run(monkeypatch, capsys, "purge-stale-jobs", "--older-than", "600")
    result = json.loads(out)
    assert code == 0 and result["requeued"] == [jobs["retry"].id] and result["failed"] == [jobs["dead"].id]
    assert result["cancelled"] == [jobs["cancel"].id]
    db_session.expire_all()
    retry, dead, cancel = (db_session.get(Job, jobs[k].id) for k in ("retry", "dead", "cancel"))
    assert db_session.get(Job, jobs["fresh"].id).status == "running"
    assert db_session.get(Job, jobs["queued"].id).status == "queued"
    assert (retry.status, dead.status, cancel.status) == ("queued", "failed", "cancelled")
    assert retry.locked_by is None and dead.error_code == "stale" and dead.finished_at is not None
    assert cancel.error_code == "cancelled" and cancel.finished_at is not None


def test_migrate_stamps_matching_unversioned_db(app_env, monkeypatch, capsys):
    code, out, _ = run(monkeypatch, capsys, "migrate")  # app_env created tables with create_all
    assert code == 0 and "stamped" in out
    code, out, _ = run(monkeypatch, capsys, "migrate")
    assert code == 0 and "upgraded" in out


def test_migrate_fresh_database(app_env, monkeypatch, capsys, tmp_path):
    from aadhi.db import get_engine

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    reset_engine_cache()
    code, out, _ = run(monkeypatch, capsys, "migrate")
    assert code == 0 and "upgraded" in out
    tables = set(inspect(get_engine()).get_table_names())
    assert {"users", "projects", "jobs", "alembic_version"} <= tables
    get_engine().dispose()
    assert get_settings().resolved_database_url.endswith("fresh.db")


def test_migrate_refuses_drifted_unversioned_db(app_env, monkeypatch, capsys, tmp_path):
    path = tmp_path / "drift.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT)")
    con.commit()
    con.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    reset_engine_cache()
    code, _, err = run(monkeypatch, capsys, "migrate")
    assert code == 2 and "differs from the models" in err


def test_help_and_module_entrypoint():
    proc = subprocess.run(
        [sys.executable, "-m", "aadhi.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT_DIR,
        check=False,
    )
    assert proc.returncode == 0
    for command in (
        "migrate",
        "create-admin",
        "set-password",
        "list-users",
        "import-legacy",
        "import-json",
        "verify-assets",
        "cleanup",
        "purge-stale-jobs",
    ):
        assert command in proc.stdout
    with pytest.raises(SystemExit):
        cli.main(["no-such-command"])


def test_migrate_completes_an_older_unversioned_development_db(app_env, monkeypatch, capsys, tmp_path):
    """A database ``create_all`` made before usage_events.billed_to / api_credentials existed."""
    path = tmp_path / "older.db"
    from aadhi.db import Base, make_engine

    engine = make_engine(f"sqlite:///{path.as_posix()}")
    tables = [t for name, t in Base.metadata.tables.items() if name != "api_credentials"]
    Base.metadata.create_all(engine, tables=tables)
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE usage_events DROP COLUMN billed_to")
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path.as_posix()}")
    reset_engine_cache()
    code, out, err = run(monkeypatch, capsys, "migrate")
    assert code == 0 and "stamped" in out, err
    con = sqlite3.connect(path)
    try:
        assert "billed_to" in {row[1] for row in con.execute("PRAGMA table_info(usage_events)")}
        assert con.execute("SELECT name FROM sqlite_master WHERE name='api_credentials'").fetchone()
        assert con.execute("SELECT version_num FROM alembic_version").fetchall() == [(_alembic_head(),)]
    finally:
        con.close()


def test_manim_check_prints_the_sandbox_health(app_env, monkeypatch, capsys):
    from aadhi.manim import health

    def fake(settings):
        return health.SandboxHealth(available=True, sandbox="subprocess", isolated=False, isolation="audit hook only",
                                    manim_version="0.19.0", manim_version_ok=True, latex=False, network_denied=True,
                                    env_clean=True, probe_ran=True, notes=["use MANIM_SANDBOX=docker in production"])

    monkeypatch.setattr(health, "sandbox_health", fake)
    code, out, _ = run(monkeypatch, capsys, "manim-check")
    assert code == 0
    assert "available: True" in out and "network_denied: True" in out
    assert "note: use MANIM_SANDBOX=docker in production" in out
    code, out, _ = run(monkeypatch, capsys, "manim-check", "--json")
    assert code == 0 and json.loads(out)["sandbox"] == "subprocess"

    def leaking(settings):
        return fake(settings).__class__(**{**fake(settings).as_dict(), "network_denied": False})

    monkeypatch.setattr(health, "sandbox_health", leaking)
    assert run(monkeypatch, capsys, "manim-check")[0] == 1  # the probe reached the network: fail
    monkeypatch.setattr(health, "sandbox_health",
                        lambda s: fake(s).__class__(**{**fake(s).as_dict(), "files_denied": False}))
    assert run(monkeypatch, capsys, "manim-check")[0] == 1  # the probe read a host file: fail
    monkeypatch.setattr(health, "sandbox_health", lambda s: health.SandboxHealth(
        available=False, sandbox="disabled", isolated=False, isolation="Manim rendering is disabled",
        manim_version="", manim_version_ok=False, latex=False))
    assert run(monkeypatch, capsys, "manim-check")[0] == 1
