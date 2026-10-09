"""Fake aadhi.auth.tokens (PyJWT HS256; scoped tokens use a derived key)."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import jwt

from aadhi.config import get_settings


class InvalidToken(Exception):
    pass


def create_session_token(user, settings) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user.id),
        "tv": int(user.token_version),
        "typ": "session",
        "iat": now,
        "exp": now + settings.jwt_ttl_hours * 3600,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.resolved_jwt_secret(), algorithm="HS256")


def decode_session_token(token: str, settings) -> dict:
    try:
        claims = jwt.decode(token, settings.resolved_jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from None
    if claims.get("typ") != "session":
        raise InvalidToken("wrong token type")
    return claims


def _scoped_key(scope: str, settings) -> bytes:
    return hmac.new(settings.resolved_jwt_secret().encode(), f"aadhi-scoped:{scope}".encode(), hashlib.sha256).digest()


def create_scoped_token(scope: str, claims: dict, ttl_seconds: int, settings=None) -> str:
    settings = settings or get_settings()
    payload = {**claims, "typ": "scoped", "scope": scope, "exp": int(time.time()) + ttl_seconds}
    return jwt.encode(payload, _scoped_key(scope, settings), algorithm="HS256")


def decode_scoped_token(token: str, scope: str, settings=None) -> dict:
    settings = settings or get_settings()
    try:
        claims = jwt.decode(token, _scoped_key(scope, settings), algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from None
    if claims.get("typ") != "scoped" or claims.get("scope") != scope:
        raise InvalidToken("wrong scope")
    return claims
