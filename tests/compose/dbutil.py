"""DB rows for render_video tests."""

from __future__ import annotations

from dataclasses import dataclass

from aadhi.compose.timeline import build_timeline
from aadhi.config import Settings
from aadhi.db import session_scope
from aadhi.models import Project, ProjectVersion, Render, User
from aadhi.schemas.manifest import AssetManifest
from aadhi.schemas.screenplay import Screenplay


@dataclass
class Rows:
    user_id: int
    project_id: int
    version_id: int
    render_id: int


def create_rows(settings: Settings, sp: Screenplay, manifest: AssetManifest | None, *, revision: int = 3,
                built_revision: int | None = 3, with_timeline: bool = True, render_status: str = "queued") -> Rows:
    with session_scope() as db:
        user = User(username=f"teacher{id(sp)}", password_hash="x", role="editor")
        db.add(user)
        db.flush()
        project = Project(owner_id=user.id, title="Ohm's Law Lecture", subject_name=sp.subject_name)
        db.add(project)
        db.flush()
        version = ProjectVersion(project_id=project.id, number=1, status="ready", revision=revision,
                                 built_revision=built_revision)
        version.set_screenplay(sp)
        version.set_manifest(manifest)
        if with_timeline:
            version.set_timeline(build_timeline(sp, manifest, settings=settings, version_id=None,
                                                revision=built_revision))
        db.add(version)
        db.flush()
        render = Render(version_id=version.id, status=render_status, options={"burn_captions": False})
        db.add(render)
        db.flush()
        return Rows(user.id, project.id, version.id, render.id)


def get_render(render_id: int) -> Render:
    with session_scope() as db:
        r = db.get(Render, render_id)
        db.expunge(r)
        return r
