"""Share links and the public watch endpoint.

Share tokens are unguessable capability strings. The token alone determines project/version;
revoked or expired links and deleted projects are 404.
"""

from __future__ import annotations

import datetime as dt
import re
import secrets
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import atomic_add, compare_and_set
from ...models import Project, ProjectVersion, ShareLink
from ...security.client_ip import client_ip
from ...security.ratelimit import check_rate_limit
from ..deps import RATE_LIMITED, AppSettings, CurrentUser, DbSession, Store, load_project
from ..errors import ApiException, not_found
from ..serializers import share_dict, share_url
from ..timelines import resolve_timeline
from ..util import as_utc, iso, utcnow

router = APIRouter(tags=["shares"])

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_SHARE_DAYS = 365


class ShareCreate(BaseModel):
    """Share link options (pinned version, expiry)."""

    model_config = ConfigDict(extra="forbid")

    version_id: int | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=MAX_SHARE_DAYS)


@dataclass
class ResolvedShare:
    """A live share link with its project and the version it shows (None: nothing built yet)."""

    share: ShareLink
    project: Project
    version_id: int | None


def resolve_share(db: Session, token: str) -> ResolvedShare:
    """Live share link or 404 (unknown, revoked, expired, project deleted)."""
    if not TOKEN_RE.match(token or ""):
        raise not_found("Share link")
    share = db.execute(select(ShareLink).where(ShareLink.token == token)).scalar_one_or_none()
    if share is None or share.revoked_at is not None:
        raise not_found("Share link")
    expires = as_utc(share.expires_at)
    if expires is not None and expires <= utcnow():
        raise not_found("Share link")
    project = db.get(Project, share.project_id)
    if project is None or project.deleted_at is not None:
        raise not_found("Share link")
    return ResolvedShare(share=share, project=project, version_id=share.version_id or project.current_version_id)


@router.post("/api/projects/{project_id}/shares", status_code=201, dependencies=RATE_LIMITED)
def create_share(
    project_id: int, body: ShareCreate, user: CurrentUser, db: DbSession, settings: AppSettings
) -> dict[str, Any]:
    """Create a share link (pinned to a version, or following the project's current version)."""
    project = load_project(db, user, project_id)
    if body.version_id is not None:
        owner = db.execute(
            select(ProjectVersion.project_id).where(ProjectVersion.id == body.version_id)
        ).scalar_one_or_none()
        if owner != project.id:
            raise ApiException(
                422,
                "validation",
                [
                    {
                        "loc": ["body", "version_id"],
                        "msg": "Version does not belong to this project.",
                        "type": "value_error",
                    }
                ],
            )
    expires_at = utcnow() + dt.timedelta(days=body.expires_in_days) if body.expires_in_days else None
    share = ShareLink(
        token=secrets.token_urlsafe(24),
        project_id=project.id,
        version_id=body.version_id,
        created_by=user.id,
        expires_at=expires_at,
    )
    db.add(share)
    db.commit()
    return {
        "token": share.token,
        "url": share_url(settings, share.token),
        "version_id": share.version_id,
        "expires_at": iso(share.expires_at),
    }


@router.get("/api/projects/{project_id}/shares", dependencies=RATE_LIMITED)
def list_shares(project_id: int, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Every share link of the project (including revoked/expired ones), newest first."""
    project = load_project(db, user, project_id)
    rows = db.execute(
        select(ShareLink).where(ShareLink.project_id == project.id).order_by(ShareLink.id.desc())
    ).scalars()
    return {"items": [share_dict(s, settings) for s in rows]}


@router.delete("/api/shares/{token}", status_code=204, dependencies=RATE_LIMITED)
def revoke_share(token: str, user: CurrentUser, db: DbSession) -> Response:
    """Revoke a share link of a project the user may access."""
    if not TOKEN_RE.match(token):
        raise not_found("Share link")
    share = db.execute(select(ShareLink).where(ShareLink.token == token)).scalar_one_or_none()
    if share is None:
        raise not_found("Share link")
    load_project(db, user, share.project_id)
    compare_and_set(db, ShareLink, share.id, {"revoked_at": None}, {"revoked_at": utcnow()})
    db.commit()
    return Response(status_code=204)


@router.get("/api/public/watch/{token}")
def public_watch(token: str, request: Request, db: DbSession, settings: AppSettings, store: Store) -> dict[str, Any]:
    """Public player payload for a share link (no auth; counts the view atomically)."""
    check_rate_limit("watch", f"ip:{client_ip(request, settings)}", per_minute=settings.api_rate_limit_per_minute)
    resolved = resolve_share(db, token)
    version = db.get(ProjectVersion, resolved.version_id) if resolved.version_id else None
    if version is None or version.project_id != resolved.project.id or version.timeline is None:
        raise not_found("Lecture")
    timeline = resolve_timeline(version.timeline, store, settings)
    atomic_add(db, ShareLink, resolved.share.id, "view_count", 1)
    db.commit()
    project = resolved.project
    return {
        "timeline": timeline,
        "project": {
            "title": project.title,
            "subject_name": project.subject_name,
            "session_title": project.session_title,
        },
        "share_token": resolved.share.token,
    }
