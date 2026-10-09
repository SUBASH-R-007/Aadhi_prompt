"""Shared FastAPI dependencies and authorization loaders.

``load_project`` / ``load_version`` are the single authorization gate for project data: owner or
admin, project not soft-deleted. Anything else is a 404 (never a 403 that would reveal that the
object exists).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import get_current_user, get_current_user_allow_pending, get_optional_user, require_roles
from ..config import Settings, get_settings
from ..db import DbSession
from ..models import Project, ProjectVersion, User
from ..security.ratelimit import check_rate_limit
from ..storage.assets import AssetStore
from .errors import not_found

MAX_DB_ID = 2**63 - 1

__all__ = [
    "AdminUser",
    "AppSettings",
    "CurrentUser",
    "DbSession",
    "OptionalUser",
    "PendingUser",
    "RATE_LIMITED",
    "Store",
    "get_app_settings",
    "get_asset_store",
    "is_admin",
    "load_project",
    "load_version",
    "user_rate_limit",
    "valid_id",
]


def valid_id(value: int | None) -> bool:
    """True for ids that can exist in the database (avoids driver overflow errors)."""
    return value is not None and 0 < value <= MAX_DB_ID


def is_admin(user: User | None) -> bool:
    """Admins see and manage every project."""
    return user is not None and user.role == "admin"


def load_project(db: Session, user: User, project_id: int, *, for_update: bool = False) -> Project:
    """Project the user may access (owner or admin, not deleted) or 404."""
    if not valid_id(project_id):
        raise not_found("Project")
    stmt = select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    if not is_admin(user):
        stmt = stmt.where(Project.owner_id == user.id)
    if for_update:
        stmt = stmt.with_for_update()
    project = db.execute(stmt).scalar_one_or_none()
    if project is None:
        raise not_found("Project")
    return project


def load_version(db: Session, user: User, vid: int, *, for_update: bool = False) -> ProjectVersion:
    """Version whose project the user may access (owner or admin, not deleted) or 404."""
    if not valid_id(vid):
        raise not_found("Version")
    stmt = (
        select(ProjectVersion)
        .join(Project, Project.id == ProjectVersion.project_id)
        .where(ProjectVersion.id == vid, Project.deleted_at.is_(None))
    )
    if not is_admin(user):
        stmt = stmt.where(Project.owner_id == user.id)
    if for_update:
        stmt = stmt.with_for_update(of=ProjectVersion)
    version = db.execute(stmt).scalar_one_or_none()
    if version is None:
        raise not_found("Version")
    return version


def get_app_settings(request: Request) -> Settings:
    """Settings the app was created with (falls back to the global settings)."""
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


def get_asset_store(request: Request) -> AssetStore:
    """One content-addressed AssetStore per app (created lazily on first use)."""
    override = getattr(request.app.state, "asset_store", None)  # tests may inject a store
    if override is not None:
        return override
    from ..storage import get_asset_store as _shared_store

    return _shared_store()


def user_rate_limit(request: Request, user: Annotated[User, Depends(get_current_user_allow_pending)]) -> None:
    """General per-user API rate limit (``API_RATE_LIMIT_PER_MINUTE``) for authenticated routes."""
    settings = get_app_settings(request)
    if settings.api_rate_limit_per_minute > 0:
        check_rate_limit("api", f"user:{user.id}", per_minute=settings.api_rate_limit_per_minute)


RATE_LIMITED = [Depends(user_rate_limit)]

CurrentUser = Annotated[User, Depends(get_current_user)]
PendingUser = Annotated[User, Depends(get_current_user_allow_pending)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_roles("admin"))]
AppSettings = Annotated[Settings, Depends(get_app_settings)]
Store = Annotated[AssetStore, Depends(get_asset_store)]
