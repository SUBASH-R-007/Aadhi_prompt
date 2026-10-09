"""Operator CLI: ``python -m aadhi.cli <command> ...``.

Commands::

    migrate                     alembic upgrade head (stamps a matching pre-Alembic database)
    create-admin --username U   create an admin (password: --password-stdin or prompt)
    set-password USERNAME       set a password, revoke all sessions (--must-change to force a change)
    list-users                  list accounts
    import-legacy --db PATH     import a v1 projects.db (read-only; --latest-only: newest save of each lesson)
    import-json PATH --owner U  import one v1 lecture JSON or v2 screenplay JSON
    verify-assets               check that every asset row has its blob (--delete-missing)
    cleanup                     enqueue the retention "cleanup" job
    purge-stale-jobs            recover running jobs whose worker stopped heart-beating (aadhi.jobs reaper)
    manim-check                 probe the configured Manim sandbox (isolation, network, LaTeX; --json)

Passwords are never accepted as command-line arguments (they would end up in shell history).
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import importlib
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import and_, delete, func, inspect, select, update

from .config import ROOT_DIR, Settings, get_settings

__all__ = ["build_parser", "main", "run_migrations"]

log = logging.getLogger("aadhi.cli")


class CliError(Exception):
    """User-facing CLI failure (printed without a traceback, exit code 2)."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")


def _read_password(args: argparse.Namespace, *, confirm: bool = True) -> str:
    if getattr(args, "password_stdin", False):
        line = sys.stdin.readline()
        password = line.rstrip("\r\n")
        if not password:
            raise CliError("no password received on stdin")
        return password
    first = getpass.getpass("New password: ")
    if confirm and getpass.getpass("Repeat password: ") != first:
        raise CliError("passwords do not match")
    return first


def _check_strength(password: str, username: str, settings: Settings) -> None:
    from .auth.passwords import check_password_strength

    problems = check_password_strength(password, username, settings)
    if problems:
        raise CliError("password rejected: " + " ".join(problems))


def _alembic_config(settings: Settings) -> Any:
    from alembic.config import Config

    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", settings.resolved_database_url.replace("%", "%%"))
    cfg.attributes["configure_logger"] = False
    return cfg


def run_migrations(settings: Settings, revision: str = "head") -> str:
    """Upgrade the database to ``revision``. A database created without Alembic (``create_all``)
    whose schema matches the models is stamped instead; one that only lacks tables or columns that can
    be added in place (an older development database) is completed first (``aadhi.dev_schema``), then
    stamped; any other drift is refused."""
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from alembic import command

    from . import models  # noqa: F401  (register tables)
    from .db import Base, make_engine
    from .dev_schema import add_missing_columns, only_additive

    cfg = _alembic_config(settings)
    engine = make_engine(settings.resolved_database_url)
    try:
        with engine.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            if "users" in tables and "alembic_version" not in tables:
                diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
                if diffs and only_additive(diffs):
                    conn.close()
                    Base.metadata.create_all(engine)
                    add_missing_columns(engine)
                    diffs = []
                if diffs:
                    raise CliError(
                        f"database has tables but no Alembic version and differs from the models ({len(diffs)} "
                        "differences); migrate it manually"
                    )
                command.stamp(cfg, "head")
                return "stamped"
    finally:
        engine.dispose()
    command.upgrade(cfg, revision)
    return "upgraded"


def _session():
    from .db import session_scope

    return session_scope()


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


def cmd_migrate(args: argparse.Namespace, settings: Settings) -> int:
    result = run_migrations(settings, args.revision)
    _out(f"database {result} ({args.revision})")
    return 0


def cmd_create_admin(args: argparse.Namespace, settings: Settings) -> int:
    from .auth.passwords import hash_password
    from .models import User

    username = args.username.strip()
    if not username or len(username) > 64:
        raise CliError("username must be 1-64 characters")
    password = _read_password(args)
    _check_strength(password, username, settings)
    with _session() as db:
        if db.execute(select(User.id).where(User.username == username)).first() is not None:
            raise CliError(f"user {username!r} already exists (use set-password)")
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
                must_change_password=bool(args.must_change),
                token_version=0,
            )
        )
    _out(f"created admin {username!r}")
    return 0


def cmd_set_password(args: argparse.Namespace, settings: Settings) -> int:
    from .auth.service import find_user, set_password

    password = _read_password(args)
    _check_strength(password, args.username, settings)
    with _session() as db:
        user = find_user(db, args.username)
        if user is None:
            raise CliError(f"no such user {args.username!r}")
        set_password(db, user.id, password, must_change=bool(args.must_change))
    _out(f"password updated for {args.username!r}; all of its sessions were signed out")
    return 0


def cmd_list_users(args: argparse.Namespace, settings: Settings) -> int:
    from .models import User

    with _session() as db:
        users = db.execute(select(User).order_by(User.id)).scalars().all()
        rows = [
            {
                "id": u.id,
                "username": u.username,
                "role": u.role,
                "is_active": u.is_active,
                "must_change_password": u.must_change_password,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ]
    if args.json:
        _out(json.dumps(rows, indent=2))
        return 0
    _out(f"{'ID':>4}  {'USERNAME':<24} {'ROLE':<7} {'ACTIVE':<6} {'MUST_CHANGE':<11} LAST_LOGIN")
    for r in rows:
        _out(
            f"{r['id']:>4}  {r['username']:<24} {r['role']:<7} {str(r['is_active']).lower():<6} "
            f"{str(r['must_change_password']).lower():<11} {r['last_login_at'] or '-'}"
        )
    return 0


def cmd_import_legacy(args: argparse.Namespace, settings: Settings) -> int:
    from .legacy.legacy_db import import_legacy_db

    path = Path(args.db)
    if not path.is_file():
        raise CliError(f"no such file: {path}")
    with _session() as db:
        try:
            result = import_legacy_db(db, path, settings, owner_fallback=args.owner_fallback,
                                      latest_only=bool(getattr(args, "latest_only", False)))
        except ValueError as exc:
            raise CliError(str(exc)) from exc
    _out(json.dumps(result, indent=2, default=str))
    for step in result.get("action_required") or []:
        _err(f"ACTION REQUIRED: {step}")
    return 1 if result["errors"] and not result["project_ids"] else 0


def cmd_import_json(args: argparse.Namespace, settings: Settings) -> int:
    from .auth.service import find_user
    from .legacy.jobs import parse_lecture_bytes
    from .legacy.legacy_db import create_project_from_screenplay

    path = Path(args.path)
    if not path.is_file():
        raise CliError(f"no such file: {path}")
    try:
        screenplay, warnings = parse_lecture_bytes(path.read_bytes())
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise CliError(f"cannot import {path.name}: {exc}") from exc
    with _session() as db:
        owner = find_user(db, args.owner)
        if owner is None:
            raise CliError(f"no such user {args.owner!r}")
        project, version = create_project_from_screenplay(
            db, screenplay, owner_id=owner.id, title=args.title, warnings=warnings
        )
        db.flush()
        pid, vid = project.id, version.id
    _out(json.dumps({"project_id": pid, "version_id": vid, "warnings": warnings}, indent=2))
    return 0


def cmd_verify_assets(args: argparse.Namespace, settings: Settings) -> int:
    from .models import Asset
    from .storage import build_storage

    storage = build_storage(settings)
    missing: list[tuple[int, str]] = []
    checked = 0
    last_id = 0
    with _session() as db:
        while True:
            rows = db.execute(
                select(Asset.id, Asset.key, Asset.storage_key).where(Asset.id > last_id).order_by(Asset.id).limit(500)
            ).all()
            if not rows:
                break
            for asset_id, key, storage_key in rows:
                checked += 1
                if not storage.exists(storage_key):
                    missing.append((asset_id, key))
            last_id = rows[-1][0]
        if missing and args.delete_missing:
            ids = [i for i, _ in missing]
            for i in range(0, len(ids), 500):
                db.execute(delete(Asset).where(Asset.id.in_(ids[i : i + 500])))
    _out(f"checked {checked} asset(s); {len(missing)} missing blob(s)")
    for _, key in missing[:50]:
        _out(f"  missing: {key}")
    if missing and args.delete_missing:
        _out(f"deleted {len(missing)} asset row(s); they will be regenerated on the next build")
        return 0
    return 1 if missing else 0


def cmd_cleanup(args: argparse.Namespace, settings: Settings) -> int:
    from .models import Job

    with _session() as db:
        job_id: int
        try:
            from .jobs.queue import enqueue  # type: ignore[attr-defined]
        except ImportError:
            enqueue = None
        if enqueue is not None:
            job = enqueue(db, "cleanup", payload={}, priority=500)
            db.flush()
            job_id = job.id
        else:
            job = Job(kind="cleanup", status="queued", payload={}, priority=500, max_attempts=settings.job_max_attempts)
            db.add(job)
            db.flush()
            job_id = job.id
    _out(f"enqueued cleanup job {job_id}")
    return 0


REAP_BATCH = 100
MAX_REAP_BATCHES = 1000


def _stale_condition(cutoff: dt.datetime) -> Any:
    """``running`` jobs last seen before ``cutoff`` (same staleness rule as ``aadhi.jobs.queue.reap_stale``)."""
    from .models import Job

    last_seen = func.coalesce(Job.heartbeat_at, Job.locked_at, Job.created_at)
    return and_(Job.status == "running", last_seen < cutoff)


def list_stale_jobs(db: Any, settings: Settings, *, older_than: int | None = None) -> list[int]:
    """Ids of running jobs without a heartbeat for ``older_than`` seconds (default ``JOB_STALE_SECONDS``)."""
    from .models import Job, utcnow

    cutoff = utcnow() - dt.timedelta(seconds=int(older_than or settings.job_stale_seconds))
    return [int(i) for (i,) in db.execute(select(Job.id).where(_stale_condition(cutoff)).order_by(Job.id))]


def reap_with_job_system(db: Any, reaper: Callable[..., dict[str, list[int]]], stale_seconds: int) -> dict:
    """Run the job system's reaper (``aadhi.jobs.queue.reap_stale``) in batches until nothing is left.

    The reaper cancels jobs with a pending cancellation, fails jobs out of attempts, requeues the rest,
    writes job events and settles the versions; each batch is committed on its own.
    """
    out: dict[str, list[int]] = {"requeued": [], "failed": [], "cancelled": []}
    for _ in range(MAX_REAP_BATCHES):
        batch = reaper(db, stale_seconds=float(stale_seconds), limit=REAP_BATCH)
        db.commit()
        handled = 0
        for outcome, ids in (batch or {}).items():
            out.setdefault(outcome, []).extend(int(i) for i in ids)
            handled += len(ids)
        if handled == 0:  # nothing (recoverable) is stale any more; recovered rows never match again
            break
    result = {k: sorted(v) for k, v in out.items()}
    return {"stale": sorted(i for ids in result.values() for i in ids), **result}


def purge_stale_jobs(db: Any, settings: Settings, *, older_than: int | None = None, dry_run: bool = False) -> dict:
    """Built-in fallback used only when ``aadhi.jobs.queue`` cannot be imported.

    Stale running jobs with a pending cancellation are cancelled, jobs out of attempts are failed and
    the rest are requeued. Each transition is ONE atomic UPDATE guarded by the stale condition, so a
    worker that resumes heart-beating concurrently keeps its job. Unlike the job system's reaper it
    cannot settle versions or write job events.
    """
    from .models import Job, utcnow

    now = utcnow()
    cutoff = now - dt.timedelta(seconds=int(older_than or settings.job_stale_seconds))
    stale = _stale_condition(cutoff)
    if dry_run:
        return {
            "stale": list_stale_jobs(db, settings, older_than=older_than),
            "requeued": [],
            "failed": [],
            "cancelled": [],
        }

    def transition(where: Any, values: dict[str, Any]) -> list[int]:
        stmt = update(Job).where(stale, where).values(**values).returning(Job.id)
        return sorted(int(i) for i in db.execute(stmt.execution_options(synchronize_session=False)).scalars())

    cancelled = transition(
        Job.cancel_requested.is_(True),
        {"status": "cancelled", "locked_by": None, "finished_at": now, "error_code": "cancelled"},
    )
    failed = transition(
        Job.attempts >= Job.max_attempts,
        {
            "status": "failed",
            "locked_by": None,
            "finished_at": now,
            "error_code": "stale",
            "error": "The worker stopped responding and no attempts are left",
        },
    )
    requeued = transition(
        Job.attempts < Job.max_attempts,
        {
            "status": "queued",
            "locked_by": None,
            "locked_at": None,
            "heartbeat_at": None,
            "run_after": now,
            "message": "Requeued: the worker stopped responding",
        },
    )
    return {
        "stale": sorted([*cancelled, *failed, *requeued]),
        "requeued": requeued,
        "failed": failed,
        "cancelled": cancelled,
    }


def _job_reaper() -> Callable[..., dict[str, list[int]]] | None:
    """``aadhi.jobs.queue.reap_stale`` (None only when the job system cannot be imported)."""
    try:
        jobs_queue = importlib.import_module("aadhi.jobs.queue")
    except ImportError:
        return None
    reaper = getattr(jobs_queue, "reap_stale", None)
    return reaper if callable(reaper) else None


def cmd_purge_stale_jobs(args: argparse.Namespace, settings: Settings) -> int:
    reaper = _job_reaper()
    older_than = int(args.older_than or settings.job_stale_seconds)
    with _session() as db:
        if args.dry_run:
            result: dict[str, Any] = {"stale": list_stale_jobs(db, settings, older_than=older_than)}
        elif reaper is not None:
            result = reap_with_job_system(db, reaper, older_than)
        else:
            log.warning("aadhi.jobs.queue is unavailable; using the built-in stale-job recovery")
            result = purge_stale_jobs(db, settings, older_than=older_than)
    _out(json.dumps(result, indent=2, default=str))
    return 0


def cmd_manim_check(args: argparse.Namespace, settings: Settings) -> int:
    """Run the sandbox self-check (``aadhi.manim.health``): booleans and short strings only, no host
    paths or secrets. Exit 1 when the sandbox is unavailable or the probe found it leaking."""
    from .manim.health import sandbox_health

    health = sandbox_health(settings)
    data = health.as_dict()
    if args.json:
        _out(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            if key != "notes":
                _out(f"{key}: {value}")
        for note in health.notes:
            _out(f"note: {note}")
    leaking = health.network_denied is False or health.env_clean is False or health.files_denied is False
    return 1 if (not health.available or leaking) else 0


# ---------------------------------------------------------------------------
# parser / entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """The argparse parser for all subcommands."""
    parser = argparse.ArgumentParser(prog="python -m aadhi.cli", description="Aadhi EduEngine operator commands")
    parser.add_argument("-v", "--verbose", action="store_true", help="log at INFO level")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("migrate", help="upgrade the database schema (alembic upgrade head)")
    p.add_argument("--revision", default="head")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("create-admin", help="create an admin account")
    p.add_argument("--username", required=True)
    p.add_argument("--password-stdin", action="store_true", help="read the password from the first line of stdin")
    p.add_argument("--must-change", action="store_true", help="require a password change at first login")
    p.set_defaults(func=cmd_create_admin)

    p = sub.add_parser("set-password", help="set a user's password and sign out all of its sessions")
    p.add_argument("username")
    p.add_argument("--password-stdin", action="store_true")
    p.add_argument("--must-change", action="store_true")
    p.set_defaults(func=cmd_set_password)

    p = sub.add_parser("list-users", help="list accounts")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list_users)

    p = sub.add_parser("import-legacy", help="import users and projects from a v1 projects.db")
    p.add_argument("--db", required=True, help="path to the v1 SQLite database (opened read-only)")
    p.add_argument("--owner-fallback", default="admin", help="owner for projects whose v1 user is missing")
    p.add_argument("--latest-only", action="store_true",
                   help="import only the newest saved row of each lesson (databases that keep every save as a row)")
    p.set_defaults(func=cmd_import_legacy)

    p = sub.add_parser("import-json", help="import a v1 lecture JSON or a v2 screenplay JSON")
    p.add_argument("path")
    p.add_argument("--owner", required=True, help="username that will own the project")
    p.add_argument("--title", default=None)
    p.set_defaults(func=cmd_import_json)

    p = sub.add_parser("verify-assets", help="check that every asset blob exists in storage")
    p.add_argument("--delete-missing", action="store_true", help="delete rows whose blob is missing")
    p.set_defaults(func=cmd_verify_assets)

    p = sub.add_parser("cleanup", help="enqueue the retention cleanup job")
    p.set_defaults(func=cmd_cleanup)

    p = sub.add_parser("purge-stale-jobs", help="requeue/fail running jobs whose worker stopped responding")
    p.add_argument("--older-than", type=int, default=None, help="seconds without heartbeat (default JOB_STALE_SECONDS)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_purge_stale_jobs)

    p = sub.add_parser("manim-check", help="probe the configured Manim sandbox (isolation, network, LaTeX)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_manim_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    from .logging_setup import configure_logging

    configure_logging(settings)
    logging.getLogger().setLevel(logging.INFO if args.verbose else logging.WARNING)
    try:
        return int(args.func(args, settings) or 0)
    except CliError as exc:
        _err(f"error: {settings.redact(str(exc))}")
        return 2
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _err("interrupted")
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
