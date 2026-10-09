"""Fake aadhi.auth.deps: cookie or bearer session, DB-checked role/active/token_version."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, Request

from aadhi.auth.tokens import InvalidToken, decode_session_token
from aadhi.config import get_settings
from aadhi.db import DbSession
from aadhi.models import User

from .errors import AppHTTPException


def _token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return request.cookies.get(get_settings().cookie_name)


def _load(request: Request, db) -> User | None:
    token = _token(request)
    if not token:
        return None
    try:
        claims = decode_session_token(token, get_settings())
        user_id = int(claims["sub"])
    except (InvalidToken, KeyError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active or user.token_version != claims.get("tv"):
        return None
    return user


@dataclass(frozen=True)
class AuthState:
    user: User | None
    reason: str | None


def resolve_auth(request: Request, db: DbSession, settings: Any = None) -> AuthState:
    user = _load(request, db)
    return AuthState(user, None if user is not None else "missing")


def get_current_user_allow_pending(request: Request, db: DbSession) -> User:
    user = _load(request, db)
    if user is None:
        raise AppHTTPException(401, "unauthenticated", "Not signed in.")
    return user


def get_current_user(user: Annotated[User, Depends(get_current_user_allow_pending)]) -> User:
    if user.must_change_password:
        raise AppHTTPException(403, "password_change_required", "Change your password to continue.")
    return user


def get_optional_user(request: Request, db: DbSession) -> User | None:
    return _load(request, db)


def require_roles(*roles: str):
    def dependency(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in roles:
            raise AppHTTPException(403, "forbidden", "Not allowed.")
        return user

    return dependency
