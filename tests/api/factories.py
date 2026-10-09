"""Seed data for the API tests (direct DB writes; no HTTP)."""

from __future__ import annotations

import struct
import zlib
from typing import Any

from sqlalchemy.orm import Session

from aadhi.models import Asset, AssetRef, Job, JobEvent, Project, ProjectVersion, User
from aadhi.schemas.screenplay import Screenplay
from aadhi.schemas.timeline import TimedBeat, TimedScene, Timeline

PASSWORD = "correct-horse-battery-1"


def tiny_png(width: int = 3, height: int = 2) -> bytes:
    """A valid PNG (RGB, no compression tricks)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def screenplay_dict(n_content: int = 2, *, quiz: bool = True, title: str = "Ohm's Law") -> dict[str, Any]:
    """A valid v2 screenplay: content scenes (+ one quiz scene)."""
    scenes: list[dict[str, Any]] = []
    for i in range(1, n_content + 1):
        sid = f"s{i}"
        scenes.append(
            {
                "id": sid,
                "type": "content",
                "title": f"Scene {i}",
                "board": [{"id": f"{sid}-i1", "kind": "bullet", "text": f"Point {i}"}],
                "beats": [
                    {
                        "id": f"{sid}-b1",
                        "narration": f"This is scene {i}, introducing the idea.",
                        "board_item_id": f"{sid}-i1",
                    },
                    {"id": f"{sid}-b2", "narration": "Let us think about it for a moment."},
                ],
            }
        )
    if quiz:
        scenes.append(
            {
                "id": "q1",
                "type": "quiz_checkpoint",
                "title": "Check yourself",
                "question": "What is V when I = 2 A and R = 3 ohm?",
                "options": ["6 V", "5 V", "1.5 V"],
                "correct_index": 0,
                "beats": [{"id": "q1-b1", "narration": "Here is a quick question."}],
                "reveal_beats": [{"id": "q1-r1", "narration": "Six volts, because V equals I times R."}],
            }
        )
    return {"session_title": title, "subject_name": "Physics", "scenes": scenes}


def make_screenplay(**kw: Any) -> Screenplay:
    return Screenplay.model_validate(screenplay_dict(**kw))


def make_timeline(
    sp: Screenplay, *, version_id: int | None = None, revision: int | None = None, audio_key: str | None = None
) -> Timeline:
    scenes = []
    t = 0.0
    for i, scene in enumerate(sp.scenes):
        beats = [
            TimedBeat(
                beat_id=b.id,
                index=j,
                start=0.5 + j * 2.0,
                speech_end=2.0 + j * 2.0,
                end=2.0 + j * 2.0,
                narration=b.narration,
            )
            for j, b in enumerate(scene.all_beats())
        ]
        duration = 0.5 + 2.0 * len(beats) + 1.0
        scenes.append(
            TimedScene(
                scene_id=scene.id,
                index=i,
                type=scene.type,
                title=scene.title,
                start=t,
                duration=duration,
                beats=beats,
                audio_asset_key=audio_key if i == 0 else None,
            )
        )
        t += duration
    return Timeline(version_id=version_id, screenplay_revision=revision, scenes=scenes, total_duration=t)


def add_user(
    db: Session, username: str, *, role: str = "editor", password: str = PASSWORD, must_change: bool = False
) -> User:
    from aadhi.auth.passwords import hash_password

    user = User(username=username, password_hash=hash_password(password), role=role, must_change_password=must_change)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def add_project(
    db: Session,
    owner: User,
    *,
    title: str = "Ohm's Law",
    screenplay: Screenplay | None = None,
    built: bool = True,
    status: str = "ready",
    language: str = "en-IN",
) -> tuple[Project, ProjectVersion]:
    """Project + version 1 (screenplay, and a timeline built from revision 1 when ``built``)."""
    project = Project(owner_id=owner.id, title=title, language=language, next_version_number=2)
    db.add(project)
    db.flush()
    sp = screenplay if screenplay is not None else make_screenplay()
    version = ProjectVersion(project_id=project.id, number=1, status=status, language=language, revision=1)
    version.set_screenplay(sp)
    version.issues = []
    version.generation_meta = {}
    db.add(version)
    db.flush()
    if built:
        version.set_timeline(make_timeline(sp, version_id=version.id, revision=1))
        version.built_revision = 1
    project.current_version_id = version.id
    db.commit()
    db.refresh(project)
    db.refresh(version)
    return project, version


def add_job(
    db: Session,
    *,
    kind: str = "build_assets",
    status: str = "running",
    user: User | None = None,
    project: Project | None = None,
    version: ProjectVersion | None = None,
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> Job:
    job = Job(
        kind=kind,
        status=status,
        user_id=user.id if user else None,
        project_id=project.id if project else None,
        version_id=version.id if version else None,
        payload=payload or {},
        result=result,
        max_attempts=2,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def add_event(db: Session, job: Job, message: str, *, progress: float | None = None) -> JobEvent:
    ev = JobEvent(job_id=job.id, message=message, stage="script", progress=progress, data={})
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def add_asset_ref(db: Session, project: Project, key: str, *, kind: str = "upload", mime: str = "image/png") -> Asset:
    """An Asset row (no blob) referenced by the project."""
    asset = Asset(key=key, kind=kind, storage_key=f"assets/{kind}/{key}/x.png", mime=mime, size_bytes=1)
    db.add(asset)
    db.add(AssetRef(project_id=project.id, asset_key=key))
    db.commit()
    return asset
