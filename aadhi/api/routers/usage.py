"""Usage & cost reports (``aadhi.usage.service``)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ...usage.service import usage_admin, usage_for_project, usage_for_user
from ..deps import RATE_LIMITED, AdminUser, AppSettings, CurrentUser, DbSession, load_project
from ..errors import ApiException

router = APIRouter(tags=["usage"], dependencies=RATE_LIMITED)

MAX_DAYS = 365


def _days(days: int) -> int:
    if days < 1 or days > MAX_DAYS:
        raise ApiException(
            422, "validation", [{"loc": ["query", "days"], "msg": f"days must be 1..{MAX_DAYS}", "type": "value_error"}]
        )
    return days


@router.get("/api/usage/me")
def usage_me(user: CurrentUser, db: DbSession, settings: AppSettings, days: int = 30) -> dict[str, Any]:
    """The signed-in user's spend (today, by day, by operation, by provider)."""
    return dict(usage_for_user(db, user, _days(days), settings))


@router.get("/api/usage/projects/{project_id}")
def usage_project(project_id: int, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    """Spend of one project (by job, by operation)."""
    project = load_project(db, user, project_id)
    return dict(usage_for_project(db, project.id))


@router.get("/api/admin/usage")
def usage_all(user: AdminUser, db: DbSession, days: int = 30) -> dict[str, Any]:
    """Spend of every user (admin)."""
    return dict(usage_admin(db, _days(days)))
