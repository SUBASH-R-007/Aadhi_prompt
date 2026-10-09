"""Session and scoped JWTs (ARCHITECTURE §11).

* Session tokens: ``{sub: str(user.id), tv: token_version, typ: "session", iat, exp, jti}``, HS256
  signed with ``JWT_SECRET``. Only HS256 is accepted (no ``alg`` negotiation), 30 s leeway.
* Scoped tokens (e.g. the render worker's ``scope="render"`` token): ``{typ: "scoped", scope, ...,
  iat, exp, jti}`` signed with ``HMAC-SHA256(JWT_SECRET, b"aadhi-scoped:" + scope)``. A scoped token
  therefore never verifies as a session token (different key *and* ``typ``), and a token for one
  scope never verifies for another scope.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

import jwt

from ..config import Settings, get_settings

if TYPE_CHECKING:  # pragma: no cover
    from ..models import User

__all__ = [
    "ALGORITHM",
    "LEEWAY_SECONDS",
    "InvalidToken",
    "create_scoped_token",
    "create_session_token",
    "decode_scoped_token",
    "decode_session_token",
]

ALGORITHM = "HS256"
LEEWAY_SECONDS = 30
MAX_TOKEN_LENGTH = 4096
MAX_SCOPED_TTL_SECONDS = 7 * 24 * 3600
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_RESERVED_CLAIMS = frozenset({"typ", "scope", "iat", "exp", "nbf", "jti"})
_SESSION_REQUIRED = ["sub", "tv", "typ", "iat", "exp", "jti"]
_SCOPED_REQUIRED = ["typ", "scope", "iat", "exp", "jti"]


class InvalidToken(Exception):
    """The token is malformed, expired, has the wrong type/scope or a bad signature."""


def _session_key(settings: Settings) -> bytes:
    return settings.resolved_jwt_secret().encode("utf-8")


def _scoped_key(scope: str, settings: Settings) -> bytes:
    """Purpose-bound key: HMAC(JWT_SECRET, b"aadhi-scoped:" + scope)."""
    return hmac.new(_session_key(settings), b"aadhi-scoped:" + scope.encode("ascii"), hashlib.sha256).digest()


def _decode(token: str, key: bytes, required: list[str]) -> dict[str, Any]:
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise InvalidToken("malformed token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise InvalidToken("malformed token") from exc
    if header.get("alg") != ALGORITHM:
        raise InvalidToken("unexpected token algorithm")
    try:
        return jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            leeway=LEEWAY_SECONDS,
            options={"require": required, "verify_signature": True, "verify_exp": True, "verify_iat": True},
        )
    except jwt.ExpiredSignatureError as exc:
        raise InvalidToken("token expired") from exc
    except jwt.PyJWTError as exc:
        raise InvalidToken("invalid token") from exc


def create_session_token(user: User, settings: Settings) -> str:
    """Issue a session JWT for ``user`` valid for ``JWT_TTL_HOURS``."""
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "tv": int(user.token_version or 0),
        "typ": "session",
        "iat": now,
        "exp": now + int(settings.jwt_ttl_hours) * 3600,
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, _session_key(settings), algorithm=ALGORITHM)


def decode_session_token(token: str, settings: Settings) -> dict[str, Any]:
    """Verify a session JWT and return its claims. Raises ``InvalidToken`` (incl. scoped tokens)."""
    claims = _decode(token, _session_key(settings), _SESSION_REQUIRED)
    if claims.get("typ") != "session":
        raise InvalidToken("not a session token")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub.isdigit() or len(sub) > 18:
        raise InvalidToken("invalid subject")
    tv = claims.get("tv")
    if not isinstance(tv, int) or isinstance(tv, bool):
        raise InvalidToken("invalid token version")
    return claims


def create_scoped_token(scope: str, claims: dict[str, Any], ttl_seconds: int, settings: Settings | None = None) -> str:
    """Issue a short-lived capability token for ``scope`` carrying ``claims`` (e.g. ``{"vid": 7}``).

    Raises ``ValueError`` for an invalid scope, reserved claim names or a ttl outside 1 s .. 7 days.
    """
    settings = settings or get_settings()
    if not _SCOPE_RE.match(scope or ""):
        raise ValueError(f"invalid token scope {scope!r}")
    bad = _RESERVED_CLAIMS.intersection(claims)
    if bad:
        raise ValueError(f"reserved claim names in scoped token: {sorted(bad)}")
    ttl = int(ttl_seconds)
    if ttl < 1 or ttl > MAX_SCOPED_TTL_SECONDS:
        raise ValueError("scoped token ttl must be between 1 second and 7 days")
    now = int(time.time())
    payload = {
        **claims,
        "typ": "scoped",
        "scope": scope,
        "iat": now,
        "exp": now + ttl,
        "jti": secrets.token_urlsafe(12),
    }
    return jwt.encode(payload, _scoped_key(scope, settings), algorithm=ALGORITHM)


def decode_scoped_token(token: str, scope: str, settings: Settings | None = None) -> dict[str, Any]:
    """Verify a scoped token for exactly ``scope`` and return its claims (raises ``InvalidToken``)."""
    settings = settings or get_settings()
    if not _SCOPE_RE.match(scope or ""):
        raise InvalidToken("invalid scope")
    claims = _decode(token, _scoped_key(scope, settings), _SCOPED_REQUIRED)
    if claims.get("typ") != "scoped" or claims.get("scope") != scope:
        raise InvalidToken("token scope mismatch")
    return claims
