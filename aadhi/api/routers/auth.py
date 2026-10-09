"""``/api/auth``: login, logout, me, change-password."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update

from ...auth.cookies import clear_session_cookie, set_session_cookie
from ...auth.deps import resolve_auth
from ...auth.passwords import check_password_strength, hash_password, verify_password
from ...auth.tokens import create_session_token
from ...models import User
from ...security.client_ip import client_ip
from ...security.ratelimit import check_rate_limit
from ..deps import AppSettings, DbSession, PendingUser
from ..errors import ApiException
from ..serializers import user_dict
from ..util import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

LOGIN_FAILED = "Invalid username or password."


class LoginBody(BaseModel):
    """Username/password login; ``issue_token`` also returns a bearer token for API clients."""

    model_config = ConfigDict(extra="ignore")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    issue_token: bool = False


class ChangePasswordBody(BaseModel):
    """Own password change (current password required)."""

    model_config = ConfigDict(extra="ignore")

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=1, max_length=1024)


def _safe_verify(password: str, password_hash: str) -> bool:
    try:
        return bool(verify_password(password, password_hash))
    except ValueError:
        return False


@router.post("/login")
def login(
    body: LoginBody, request: Request, response: Response, db: DbSession, settings: AppSettings
) -> dict[str, Any]:
    """Password login: sets the session cookie; returns a bearer token only when asked."""
    username = body.username.strip()
    ip = client_ip(request, settings)
    check_rate_limit("login", f"{username.lower()}|{ip}", per_minute=settings.login_rate_limit_per_minute)
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    # Unknown users: verify_password compares against a dummy hash (same cost, no user enumeration).
    ok = _safe_verify(body.password, user.password_hash if user is not None else "")
    if user is None or not ok or not user.is_active:
        raise ApiException(401, "unauthenticated", LOGIN_FAILED)
    db.execute(update(User).where(User.id == user.id).values(last_login_at=utcnow()))
    db.commit()
    db.refresh(user)
    token = create_session_token(user, settings)
    set_session_cookie(response, token, settings)
    out: dict[str, Any] = {"user": user_dict(user)}
    if body.issue_token:
        out["access_token"] = token
        out["token_type"] = "bearer"
    return out


def logout_user(request: Request, db: DbSession, settings: AppSettings) -> User | None:
    """The user a logout applies to: anyone with a valid session (pending password change included).

    Unlike ``PendingUser`` this never raises, so a browser holding an expired or revoked cookie can
    still log out and have that stale cookie cleared.
    """
    return resolve_auth(request, db, settings).user


LogoutUser = Annotated[User | None, Depends(logout_user)]


@router.post("/logout", status_code=204)
def logout(user: LogoutUser, db: DbSession, settings: AppSettings) -> Response:
    """Clear the session cookie; with a valid session also end every session (token_version bump)."""
    if user is not None:
        db.execute(update(User).where(User.id == user.id).values(token_version=User.token_version + 1))
        db.commit()
    response = Response(status_code=204)
    clear_session_cookie(response, settings)
    return response


@router.get("/me")
def me(user: PendingUser) -> dict[str, Any]:
    """The signed-in user (also while a password change is pending)."""
    return {"user": user_dict(user)}


@router.post("/change-password", status_code=204)
def change_password(body: ChangePasswordBody, user: PendingUser, db: DbSession, settings: AppSettings) -> Response:
    """Change the own password: revokes other sessions and re-issues this one."""
    check_rate_limit("change_password", f"user:{user.id}", per_minute=settings.login_rate_limit_per_minute)
    if not _safe_verify(body.current_password, user.password_hash):
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["body", "current_password"],
                    "msg": "Current password is incorrect.",
                    "type": "password.incorrect",
                }
            ],
        )
    if body.new_password == body.current_password:
        raise ApiException(
            422,
            "validation",
            [
                {
                    "loc": ["body", "new_password"],
                    "msg": "The new password must differ from the current one.",
                    "type": "password.reused",
                }
            ],
        )
    problems = check_password_strength(body.new_password, user.username, settings)
    if problems:
        raise ApiException(
            422, "validation", [{"loc": ["body", "new_password"], "msg": p, "type": "password.weak"} for p in problems]
        )
    db.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            password_hash=hash_password(body.new_password),
            must_change_password=False,
            token_version=User.token_version + 1,
        )
    )
    db.commit()
    db.refresh(user)
    response = Response(status_code=204)
    set_session_cookie(response, create_session_token(user, settings), settings)
    return response
