"""``/api/admin/users``: user management (admins only).

Role, active-state and password changes bump ``token_version`` (ends every session of that
user). New users and admin-set passwords require a password change at next login. The last
active admin can never be demoted or deactivated, and admins cannot lock themselves out.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from ...auth.passwords import check_password_strength, hash_password
from ...models import User
from ..deps import RATE_LIMITED, AdminUser, AppSettings, DbSession, valid_id
from ..errors import ApiException, not_found
from ..serializers import user_dict

router = APIRouter(prefix="/api/admin/users", tags=["admin"], dependencies=RATE_LIMITED)

USERNAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.\-]{2,63}$"
Role = Literal["admin", "editor"]


class UserCreate(BaseModel):
    """New account (it must change its password at first login)."""

    model_config = ConfigDict(extra="forbid")

    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=1, max_length=1024)
    role: Role = "editor"
    daily_budget_usd: float | None = Field(default=None, ge=0, le=100_000, allow_inf_nan=False)


class UserPatch(BaseModel):
    """Partial user update; role/active/password changes end the user's sessions."""

    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None
    daily_budget_usd: float | None = Field(default=None, ge=0, le=100_000, allow_inf_nan=False)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    must_change_password: bool | None = None


def _weak(password: str, username: str, settings: AppSettings) -> None:
    problems = check_password_strength(password, username, settings)
    if problems:
        raise ApiException(
            422, "validation", [{"loc": ["body", "password"], "msg": p, "type": "password.weak"} for p in problems]
        )


def _other_active_admins(db: Session, user_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(User)
            .where(User.role == "admin", User.is_active.is_(True), User.id != user_id)
        ).scalar_one()
    )


@router.get("")
def list_users(admin: AdminUser, db: DbSession) -> dict[str, Any]:
    """Every user account."""
    return {"items": [user_dict(u) for u in db.execute(select(User).order_by(User.id)).scalars()]}


@router.post("", status_code=201)
def create_user(body: UserCreate, admin: AdminUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Create an account that must change its password at first login."""
    taken = db.execute(select(User.id).where(func.lower(User.username) == body.username.lower())).scalar_one_or_none()
    if taken is not None:
        raise ApiException(409, "username_taken", "That username is already in use.")
    _weak(body.password, body.username, settings)
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        is_active=True,
        must_change_password=True,
        daily_budget_usd=body.daily_budget_usd,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiException(409, "username_taken", "That username is already in use.") from None
    db.refresh(user)
    return {"user": user_dict(user)}


@router.patch("/{user_id}")
def patch_user(user_id: int, body: UserPatch, admin: AdminUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Change role / active state / budget / password; session-relevant changes revoke sessions."""
    target = db.get(User, user_id) if valid_id(user_id) else None
    if target is None:
        raise not_found("User")
    fields = body.model_fields_set
    values: dict[str, Any] = {}
    revoke = False
    if "role" in fields and body.role is not None and body.role != target.role:
        values["role"] = body.role
        revoke = True
    if "is_active" in fields and body.is_active is not None and body.is_active != target.is_active:
        values["is_active"] = body.is_active
        revoke = True
    if "daily_budget_usd" in fields:
        values["daily_budget_usd"] = body.daily_budget_usd
    if "password" in fields and body.password is not None:
        _weak(body.password, target.username, settings)
        values["password_hash"] = hash_password(body.password)
        values["must_change_password"] = True
        revoke = True
    if "must_change_password" in fields and body.must_change_password is not None:
        values["must_change_password"] = body.must_change_password
    loses_admin = (
        target.role == "admin"
        and target.is_active
        and (values.get("role", "admin") != "admin" or values.get("is_active", True) is False)
    )
    if loses_admin:
        if target.id == admin.id:
            raise ApiException(409, "self_lockout", "You cannot remove your own administrator access.")
        if _other_active_admins(db, target.id) == 0:
            raise ApiException(409, "last_admin", "At least one active administrator is required.")
    if revoke:
        values["token_version"] = User.token_version + 1
    if values:
        stmt = update(User).where(User.id == target.id)
        if loses_admin:  # atomic guard: two admins demoting each other concurrently
            other = aliased(User)
            stmt = stmt.where(exists().where(other.role == "admin", other.is_active.is_(True), other.id != target.id))
        result = db.execute(stmt.values(**values).execution_options(synchronize_session=False))
        if (result.rowcount or 0) == 0:
            db.rollback()
            raise ApiException(409, "last_admin", "At least one active administrator is required.")
        db.commit()
        db.refresh(target)
    return {"user": user_dict(target)}
