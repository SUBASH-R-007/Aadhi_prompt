"""Fake aadhi.auth.cookies."""

from __future__ import annotations


def set_session_cookie(response, token: str, settings) -> None:
    response.set_cookie(
        settings.cookie_name,
        token,
        max_age=settings.jwt_ttl_hours * 3600,
        httponly=True,
        secure=settings.resolved_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response, settings) -> None:
    response.delete_cookie(
        settings.cookie_name, path="/", secure=settings.resolved_cookie_secure, httponly=True, samesite="lax"
    )
