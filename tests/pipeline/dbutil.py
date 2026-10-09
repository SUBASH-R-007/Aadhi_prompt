"""DB fixtures for pipeline tests: user, project, source document, version."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from aadhi.db import session_scope
from aadhi.models import Project, ProjectVersion, SourceDocument, User
from aadhi.storage.assets import EXT_BY_MIME, bytes_key, storage_key_for


@dataclass
class Seeded:
    user_id: int
    project_id: int
    source_id: int
    version_id: int


def seed_project(storage: Any, data: bytes, mime: str, filename: str, *, options: dict[str, Any] | None = None,
                 version_status: str = "generating", screenplay: dict[str, Any] | None = None) -> Seeded:
    """Create user + project + stored source + version 1; returns ids."""
    key = storage_key_for("source", bytes_key("source", data), mime) if mime in EXT_BY_MIME else None
    if key is None:
        raise ValueError(mime)
    storage.put_bytes(key, data, mime)
    with session_scope() as db:
        user = User(username=f"teacher{hashlib.sha256(data).hexdigest()[:6]}", password_hash="x", role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Ohm", settings=options or {}, next_version_number=2)
        db.add(project)
        db.flush()
        src = SourceDocument(project_id=project.id, filename=filename, mime=mime, size_bytes=len(data),
                             sha256=hashlib.sha256(data).hexdigest(), storage_key=key)
        db.add(src)
        version = ProjectVersion(project_id=project.id, number=1, status=version_status, revision=1,
                                 screenplay=screenplay, generation_meta={})
        db.add(version)
        db.flush()
        return Seeded(user.id, project.id, src.id, version.id)


def get_version(version_id: int) -> ProjectVersion:
    with session_scope() as db:
        v = db.get(ProjectVersion, version_id)
        assert v is not None
        # touch deferred columns while the session is open
        _ = (v.screenplay, v.asset_manifest, v.timeline, v.issues, v.generation_meta)
        db.expunge(v)
        return v


def get_source(source_id: int) -> SourceDocument:
    with session_scope() as db:
        s = db.get(SourceDocument, source_id)
        assert s is not None
        db.expunge(s)
        return s


def asset_refs(project_id: int) -> set[str]:
    from sqlalchemy import select

    from aadhi.models import AssetRef

    with session_scope() as db:
        return set(db.execute(select(AssetRef.asset_key).where(AssetRef.project_id == project_id)).scalars())
