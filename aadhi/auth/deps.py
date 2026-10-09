"""FastAPI authentication dependencies.

Token sources: ``Authorization: Bearer <session token>`` (API clients that asked for a token) or the
session cookie ``settings.cookie_name``. When a Bearer header is present it is the only credential
considered (a broken Bearer header never falls back to the cookie). Other ``Authorization``
schemes -- e.g. ``Basic`` added by a reverse proxy protecting a staging box -- are not ours and are
ignored, so the session cookie still applies. The user row is loaded on every request, so role /
``is_active`` / ``token_version`` changes take effect immediately (a ``tv`` mismatch means the
session was revoked).

Settings come from the application the request is served by (``request.app.state.settings``, set by
``create_app(settings)``), falling back to ``get_settings()``, so cookies and tokens are always
decoded with the same JWT secret / cookie name the API issued them with.

All dependencies share one cached per-request ``resolve_auth`` result (FastAPI caches a dependency
per request), so combining ``get_current_user`` with e.g. ``rate_limit(...)`` costs one user lookup.
They are plain ``def`` functions: FastAPI runs them in its threadpool (blocking DB I/O is fine).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, Any

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import DbSession
from ..models import ROLES, User
from .errors import forbidden, password_change_required, unauthenticated
from .tokens import InvalidToken, decode_session_token

__all__ = [
    "AuthState",
    "SettingsDep",
    "app_settings",
    "get_current_user",
    "get_current_user_allow_pending",
    "get_optional_user",
    "require_roles",
    "resolve_auth",
    "token_from_request",
    "user_from_token",
]


def app_settings(request: Request) -> Settings:
    """Settings of the app serving ``request`` (``app.state.settings``), else the global settings."""
    app = request.scope.get("app")
    settings = getattr(getattr(app, "state", None), "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


SettingsDep = Annotated[Settings, Depends(app_settings)]


@dataclass(frozen=True)
class AuthState:
    """Outcome of authenticating one request.

    ``reason`` is None when ``user`` is set, otherwise one of ``missing`` (no credential),
    ``invalid`` (bad/expired token), ``unknown_user``, ``inactive``, ``revoked`` (token_version).
    """

    user: User | None
    reason: str | None
    claims: dict[str, Any] = field(default_factory=dict)


def token_from_request(request: Request, settings: Settings) -> str | None:
    """The Bearer token when the ``Authorization`` scheme is Bearer (None if it is empty: no cookie
    fallback), otherwise the session cookie (non-Bearer schemes such as proxy Basic auth are ignored)."""
    auth = request.headers.get("authorization")
    if auth is not None:
        scheme, _, value = auth.strip().partition(" ")
        if scheme.lower() == "bearer":
            return value.strip() or None
    token = request.cookies.get(settings.cookie_name)
    return token or None


def user_from_token(db: Session, token: str | None, settings: Settings) -> AuthState:
    """Validate a session token and load its user (usable outside FastAPI, e.g. SSE polling)."""
    if not token:
        return AuthState(None, "missing")
    try:
        claims = decode_session_token(token, settings)
    except InvalidToken:
        return AuthState(None, "invalid")
    user = db.get(User, int(claims["sub"]))
    if user is None:
        return AuthState(None, "unknown_user", claims)
    if not user.is_active:
        return AuthState(None, "inactive", claims)
    if int(user.token_version or 0) != int(claims["tv"]):
        return AuthState(None, "revoked", claims)
    return AuthState(user, None, claims)


def resolve_auth(request: Request, db: DbSession, settings: SettingsDep) -> AuthState:
    """Per-request authentication result (cached by FastAPI; also stored on ``request.state``)."""
    state = user_from_token(db, token_from_request(request, settings), settings)
    request.state.auth = state
    return state


AuthStateDep = Annotated[AuthState, Depends(resolve_auth)]

_MESSAGES = {
    "missing": "Not authenticated",
    "invalid": "Your session is invalid or has expired; please sign in again",
    "unknown_user": "Your session is no longer valid; please sign in again",
    "inactive": "This account is disabled",
    "revoked": "Your session has ended; please sign in again",
}


def get_current_user_allow_pending(state: AuthStateDep) -> User:
    """Authenticated user, even while ``must_change_password`` is set (me, change-password, logout)."""
    if state.user is None:
        raise unauthenticated(_MESSAGES.get(state.reason or "missing", "Not authenticated"))
    return state.user


def get_current_user(user: Annotated[User, Depends(get_current_user_allow_pending)]) -> User:
    """Authenticated user. 401 ``unauthenticated``; 403 ``password_change_required`` when flagged."""
    if user.must_change_password:
        raise password_change_required()
    return user


def get_optional_user(state: AuthStateDep) -> User | None:
    """The user if fully authenticated, else None.

    Users that still have to change their password are treated as anonymous here, so optional-auth
    endpoints can never be used to act on behalf of a pending account.
    """
    user = state.user
    if user is None or user.must_change_password:
        return None
    return user


def require_roles(*roles: str) -> Callable[..., User]:
    """Dependency factory: the current user if their role is one of ``roles`` (else 403 ``forbidden``)."""
    unknown = [r for r in roles if r not in ROLES]
    if not roles or unknown:
        raise ValueError(f"require_roles needs roles from {ROLES}, got {roles!r}")
    allowed = frozenset(roles)

    def _require(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role not in allowed:
            raise forbidden()
        return user

    _require.__name__ = f"require_roles_{'_'.join(sorted(allowed))}"
    return _require
