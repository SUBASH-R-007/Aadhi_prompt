"""Small, synchronous DB helpers used by pipeline stages (call them via ``asyncio.to_thread``).

Every state change is one atomic statement (``compare_and_set`` / ``UPDATE … WHERE``); JSON
documents are always written whole.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import compare_and_set
from ..models import AssetRef, Project, ProjectVersion, SourceDocument
from ..schemas.jsonsafe import validate_stored
from ..schemas.manifest import AssetManifest
from ..schemas.screenplay import Screenplay


@dataclass
class VersionSnapshot:
    id: int
    project_id: int
    status: str
    language: str
    revision: int
    built_revision: int | None
    source_version_id: int | None
    screenplay: dict[str, Any] | None
    manifest: dict[str, Any] | None
    generation_meta: dict[str, Any] = field(default_factory=dict)
    issues: list[dict[str, Any]] = field(default_factory=list)

    # Stored documents may predate the NaN/Infinity / lone-surrogate refusal: read them sanitised.
    def get_screenplay(self) -> Screenplay | None:
        return None if self.screenplay is None else validate_stored(Screenplay, self.screenplay)

    def get_manifest(self) -> AssetManifest | None:
        return None if self.manifest is None else validate_stored(AssetManifest, self.manifest)


@dataclass
class ProjectSnapshot:
    id: int
    owner_id: int
    title: str
    subject_name: str
    unit_name: str
    session_number: str
    session_title: str
    language: str
    settings: dict[str, Any]
    current_version_id: int | None


@dataclass
class SourceSnapshot:
    id: int
    project_id: int
    filename: str
    mime: str
    size_bytes: int
    sha256: str
    storage_key: str
    page_count: int | None
    extracted_key: str | None


def load_version(db: Session, version_id: int) -> VersionSnapshot | None:
    v = db.get(ProjectVersion, version_id)
    if v is None:
        return None
    return VersionSnapshot(
        id=v.id, project_id=v.project_id, status=v.status, language=v.language, revision=v.revision,
        built_revision=v.built_revision, source_version_id=v.source_version_id, screenplay=v.screenplay,
        manifest=v.asset_manifest, generation_meta=dict(v.generation_meta or {}), issues=list(v.issues or []),
    )


def load_project(db: Session, project_id: int) -> ProjectSnapshot | None:
    p = db.get(Project, project_id)
    if p is None:
        return None
    return ProjectSnapshot(
        id=p.id, owner_id=p.owner_id, title=p.title, subject_name=p.subject_name, unit_name=p.unit_name,
        session_number=p.session_number, session_title=p.session_title, language=p.language,
        settings=dict(p.settings or {}), current_version_id=p.current_version_id,
    )


def _source_snapshot(s: SourceDocument) -> SourceSnapshot:
    return SourceSnapshot(
        id=s.id, project_id=s.project_id, filename=s.filename, mime=s.mime, size_bytes=s.size_bytes,
        sha256=s.sha256, storage_key=s.storage_key, page_count=s.page_count, extracted_key=s.extracted_key,
    )


def load_source(db: Session, source_id: int | None, project_id: int) -> SourceSnapshot | None:
    """The given source document (must belong to the project) or the project's latest one."""
    if source_id is not None:
        s = db.get(SourceDocument, source_id)
        return _source_snapshot(s) if s is not None and s.project_id == project_id else None
    s = db.execute(
        select(SourceDocument).where(SourceDocument.project_id == project_id).order_by(SourceDocument.id.desc()).limit(1)
    ).scalar_one_or_none()
    return None if s is None else _source_snapshot(s)


def record_extract(db: Session, source_id: int, extracted_key: str, page_count: int | None) -> None:
    db.execute(
        update(SourceDocument)
        .where(SourceDocument.id == source_id)
        .values(extracted_key=extracted_key, page_count=page_count)
        .execution_options(synchronize_session=False)
    )


def ensure_asset_refs(db: Session, project_id: int, keys: Iterable[str]) -> int:
    """Insert missing ``AssetRef(project_id, key)`` rows (idempotent; safe under concurrency)."""
    wanted = sorted({k for k in keys if k})
    if not wanted:
        return 0
    dialect = db.get_bind().dialect.name
    rows = [{"project_id": project_id, "asset_key": k} for k in wanted]
    if dialect in ("sqlite", "postgresql"):
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        added = 0
        for i in range(0, len(rows), 500):
            stmt = insert(AssetRef).values(rows[i:i + 500]).on_conflict_do_nothing(index_elements=["project_id", "asset_key"])
            added += db.execute(stmt).rowcount or 0
        return added
    existing = set(db.execute(
        select(AssetRef.asset_key).where(AssetRef.project_id == project_id, AssetRef.asset_key.in_(wanted))
    ).scalars())
    added = 0
    for k in wanted:
        if k in existing:
            continue
        try:
            with db.begin_nested():
                db.add(AssetRef(project_id=project_id, asset_key=k))
            added += 1
        except IntegrityError:
            pass
    return added


def issue_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"error": 0, "warning": 0, "info": 0}
    for i in issues:
        sev = i.get("severity", "warning")
        counts[sev] = counts.get(sev, 0) + 1
    return counts


def cas_version(db: Session, version_id: int, expected: dict[str, Any], values: dict[str, Any]) -> bool:
    """``compare_and_set`` on ProjectVersion (caller's session commits)."""
    return compare_and_set(db, ProjectVersion, version_id, expected, values)


def set_status_if(db: Session, version_id: int, statuses: Iterable[str], values: dict[str, Any]) -> bool:
    """Atomically update the version when its status is one of ``statuses``."""
    res = db.execute(
        update(ProjectVersion)
        .where(ProjectVersion.id == version_id, ProjectVersion.status.in_(list(statuses)))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return (res.rowcount or 0) > 0


def set_current_version_if_unset(db: Session, project_id: int, version_id: int) -> bool:
    res = db.execute(
        update(Project)
        .where(Project.id == project_id, Project.current_version_id.is_(None))
        .values(current_version_id=version_id)
        .execution_options(synchronize_session=False)
    )
    return (res.rowcount or 0) > 0
