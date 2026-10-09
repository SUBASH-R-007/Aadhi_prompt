"""Import a v1 ``projects.db`` (SQLite, opened READ-ONLY) into the v2 database.

v1 schema: ``users(id, username, password_hash)`` (passlib bcrypt ``$2b$`` hashes) and
``projects(id, user_id, subject_name, unit_name, session_number, session_title, json_data, created_at,
...)``.

* Users: teacher hashes are kept (v1 passwords keep working) and teachers become editors. The v1
  ``admin`` credential is NEVER carried over: its password is public in the v1 source, so the
  account is created as ``admin`` with an unusable hash (nobody can sign in to it) and
  ``result["action_required"]`` tells the operator to run ``python -m aadhi.cli set-password admin``.
  Because an admin then exists, the ``ADMIN_PASSWORD`` bootstrap (``ensure_admin``) is skipped.
  Existing v2 usernames are skipped and their v1 projects are attributed to the existing account.
* Projects: one ``Project`` + ``ProjectVersion`` (number allocated through
  ``Project.next_version_number``, status ``draft``, converted screenplay, ``built_revision`` None)
  per v1 project. Idempotent: ``Project.settings["legacy_project_id"]`` marks imported projects.
  Each project is committed separately so a failure never loses earlier work.
* ``latest_only`` (CLI ``import-legacy --latest-only``): the friend's fork of v1 saves every save as a new row (a
  history entry), so only the newest row (highest id) of each lesson is imported: the same owner, subject, unit,
  session number and session title (compared without case or outer spaces). A row without any of those four is
  its own lesson. The older rows' ids are listed in ``result["projects_superseded"]``.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import Settings
from ..db import atomic_add
from ..models import Project, ProjectVersion, User
from ..schemas.screenplay import Screenplay
from ..security.strict_json import loads_strict
from .convert import convert_legacy

__all__ = ["UNUSABLE_HASH", "create_project_from_screenplay", "import_legacy_db", "warnings_to_issues"]

log = logging.getLogger(__name__)

_BCRYPT_RE = re.compile(r"^\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}$")
UNUSABLE_HASH = "!legacy-unusable"  # never verifies; an admin must set a password
LEGACY_ADMIN_USERNAME = "admin"  # seeded by v1 with a hard-coded (public) password
_SCENE_IN_WARNING = re.compile(r"^scene (s\d+(?:-\d+)?)\b")


def warnings_to_issues(warnings: Iterable[str]) -> list[dict[str, Any]]:
    """Conversion warnings as ``Issue`` dicts (code ``legacy.import_warning``, info, not fixable)."""
    from ..pipeline.base import Issue

    issues = []
    for w in warnings:
        m = _SCENE_IN_WARNING.match(w)
        issues.append(
            Issue(
                code="legacy.import_warning",
                severity="info",
                message=w[:1000],
                scene_id=m.group(1) if m else None,
                source="system",
                fixable=False,
            ).model_dump(mode="json")
        )
    return issues


def _aware(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = dt.datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _clip(value: Any, limit: int) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text[:limit]


def create_project_from_screenplay(
    db: Session,
    screenplay: Screenplay,
    *,
    owner_id: int,
    title: str | None = None,
    warnings: Iterable[str] = (),
    project_settings: dict[str, Any] | None = None,
    created_at: dt.datetime | None = None,
    meta_overrides: dict[str, str] | None = None,
    label: str = "Imported",
) -> tuple[Project, ProjectVersion]:
    """Create a project with version 1 (status ``draft``) holding ``screenplay``. Flushes; caller commits."""
    meta = {
        "subject_name": screenplay.subject_name,
        "unit_name": screenplay.unit_name,
        "session_number": screenplay.session_number,
        "session_title": screenplay.session_title,
    }
    for k, v in (meta_overrides or {}).items():
        if v:
            meta[k] = v
    warnings = list(warnings)
    project = Project(
        owner_id=owner_id,
        title=_clip(title or meta["session_title"] or meta["subject_name"] or "Imported lecture", 255),
        subject_name=_clip(meta["subject_name"], 255),
        unit_name=_clip(meta["unit_name"], 255),
        session_number=_clip(meta["session_number"], 64),
        session_title=_clip(meta["session_title"], 255),
        language=screenplay.language,
        settings=dict(project_settings or {}),
    )
    if created_at is not None:
        project.created_at = created_at
        project.updated_at = created_at
    db.add(project)
    db.flush()
    next_number = atomic_add(db, Project, project.id, "next_version_number", 1)
    number = int(next_number or 2) - 1
    version = ProjectVersion(
        project_id=project.id,
        number=number,
        label=label[:255],
        status="draft",
        language=screenplay.language,
        revision=1,
        built_revision=None,
        created_by=owner_id,
        generation_meta={"import": {"warnings": warnings[:500], "warning_count": len(warnings)}},
    )
    version.set_screenplay(screenplay)
    version.set_issues(warnings_to_issues(warnings[:500]))
    db.add(version)
    db.flush()
    db.execute(
        update(Project)
        .where(Project.id == project.id)
        .values(current_version_id=version.id)
        .execution_options(synchronize_session=False)
    )
    return project, version


def _read_v1(path: Path) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """Read the v1 users and projects through a READ-ONLY connection (``mode=ro``)."""
    try:
        return _read_v1_rows(path)
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"not a readable SQLite database: {exc}") from exc


def _read_v1_rows(path: Path) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"users", "projects"} <= tables:
            raise ValueError("not a v1 projects.db (tables 'users' and 'projects' are required)")
        pcols = {r[1] for r in con.execute("PRAGMA table_info(projects)")}
        if not {"id", "user_id", "json_data"} <= pcols:
            raise ValueError("v1 projects table is missing required columns")
        wanted = [
            "id",
            "user_id",
            "subject_name",
            "unit_name",
            "session_number",
            "session_title",
            "json_data",
            "created_at",
        ]
        select_cols = ", ".join(c if c in pcols else f"NULL AS {c}" for c in wanted)
        users = con.execute("SELECT id, username, password_hash FROM users ORDER BY id").fetchall()
        # Column names come from the fixed ``wanted`` allow-list (checked against PRAGMA table_info).
        projects = con.execute(f"SELECT {select_cols} FROM projects ORDER BY id").fetchall()  # noqa: S608
    finally:
        con.close()
    return users, projects


LESSON_KEYS = ("subject_name", "unit_name", "session_number", "session_title")


def latest_rows(projects: list[sqlite3.Row]) -> tuple[list[sqlite3.Row], list[int]]:
    """``(rows to import, superseded ids)``: the newest row (highest id) of each lesson (owner + ``LESSON_KEYS``,
    without case or outer spaces), in the original order; a row without any lesson key is always kept."""
    newest: dict[tuple[Any, ...], int] = {}
    for row in projects:
        names = tuple(str(row[k] or "").strip().casefold() for k in LESSON_KEYS)
        if not any(names):
            continue
        key = (row["user_id"], *names)
        newest[key] = max(newest.get(key, int(row["id"])), int(row["id"]))
    superseded: list[int] = []
    keep: list[sqlite3.Row] = []
    for row in projects:
        names = tuple(str(row[k] or "").strip().casefold() for k in LESSON_KEYS)
        if any(names) and newest[(row["user_id"], *names)] != int(row["id"]):
            superseded.append(int(row["id"]))
        else:
            keep.append(row)
    return keep, superseded


def _import_users(db: Session, rows: list[sqlite3.Row], result: dict[str, Any]) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in rows:
        username = (row["username"] or "").strip()
        if not username or len(username) > 64:
            result["errors"].append({"legacy_user_id": row["id"], "error": "invalid username; skipped"})
            continue
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is not None:
            mapping[int(row["id"])] = existing.id
            result["users_existing"].append(username)
            continue
        role = "admin" if username == LEGACY_ADMIN_USERNAME else "editor"
        password_hash = (row["password_hash"] or "").strip()
        if role == "admin":
            # The v1 admin password is hard-coded in the public v1 source: never import that credential.
            password_hash = UNUSABLE_HASH
            result["action_required"].append(
                f"The v1 {username!r} password was not imported because it is publicly known. Nobody can sign "
                f"in as {username!r} until an operator runs: python -m aadhi.cli set-password {username}"
            )
        elif not _BCRYPT_RE.match(password_hash):
            result["errors"].append(
                {"legacy_user_id": row["id"], "error": "unsupported password hash; set a new password"}
            )
            password_hash = UNUSABLE_HASH
        user = User(
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=True,
            must_change_password=role == "admin" or password_hash == UNUSABLE_HASH,
            token_version=0,
        )
        db.add(user)
        db.flush()
        mapping[int(row["id"])] = user.id
        result["users_created"].append(username)
    db.commit()
    return mapping


def _already_imported(db: Session) -> set[int]:
    expr = Project.settings["legacy_project_id"].as_integer()
    return {int(v) for (v,) in db.execute(select(expr).where(expr.is_not(None))) if v is not None}


def import_legacy_db(
    db: Session,
    path: Path,
    settings: Settings,
    *,
    owner_fallback: str = "admin",
    on_progress: Callable[[int, int], None] | None = None,
    latest_only: bool = False,
) -> dict[str, Any]:
    """Import users and projects from a v1 ``projects.db`` (see module docstring).

    Returns ``{"project_ids", "projects_imported", "projects_skipped", "users_created",
    "users_existing", "errors", "action_required", "warning_count"}`` (+ ``"projects_superseded"`` with
    ``latest_only``). ``action_required`` lists operator steps (e.g. setting the admin password). Raises
    ``FileNotFoundError`` / ``ValueError`` for a missing or non-v1 database.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"legacy database not found: {path.name}")
    users, projects = _read_v1(path)
    result: dict[str, Any] = {
        "project_ids": [],
        "projects_imported": 0,
        "projects_skipped": [],
        "users_created": [],
        "users_existing": [],
        "errors": [],
        "action_required": [],
        "warning_count": 0,
    }
    if latest_only:
        projects, result["projects_superseded"] = latest_rows(projects)
    user_map = _import_users(db, users, result)
    fallback = db.execute(select(User.id).where(User.username == owner_fallback)).scalar_one_or_none()
    done = _already_imported(db)
    total = len(projects)
    for i, row in enumerate(projects, start=1):
        legacy_id = int(row["id"])
        try:
            if legacy_id in done:
                result["projects_skipped"].append(legacy_id)
                continue
            owner_id = user_map.get(int(row["user_id"])) if row["user_id"] is not None else None
            owner_id = owner_id or fallback
            if owner_id is None:
                result["errors"].append({"legacy_project_id": legacy_id, "error": "no owner (user missing)"})
                continue
            try:
                data = loads_strict(row["json_data"] or "null")
                screenplay, warnings = convert_legacy(data)
            except (ValueError, TypeError, RecursionError) as exc:
                result["errors"].append(
                    {"legacy_project_id": legacy_id, "error": "conversion failed: " + settings.redact(str(exc)[:300])}
                )
                continue
            created = _aware(row["created_at"])
            project, _version = create_project_from_screenplay(
                db,
                screenplay,
                owner_id=owner_id,
                warnings=warnings,
                project_settings={"legacy_project_id": legacy_id, "legacy_source": "v1_projects_db"},
                created_at=created,
                meta_overrides={
                    k: _clip(row[k], 255) for k in ("subject_name", "unit_name", "session_number", "session_title")
                },
                label="Imported from v1",
            )
            db.commit()
            done.add(legacy_id)
            result["project_ids"].append(project.id)
            result["projects_imported"] += 1
            result["warning_count"] += len(warnings)
        except Exception as exc:
            db.rollback()
            log.exception("legacy import of project %s failed", legacy_id)
            result["errors"].append(
                {"legacy_project_id": legacy_id, "error": f"{type(exc).__name__}: " + settings.redact(str(exc)[:300])}
            )
        finally:
            if on_progress is not None:
                on_progress(i, total)
    return result
