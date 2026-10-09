"""Session cookie helpers.

The cookie is ``__Host-aadhi_session`` on https (``Secure``, ``Path=/``, no ``Domain`` -- required by
the ``__Host-`` prefix) and ``aadhi_session`` on plain-http development; always ``HttpOnly`` and
``SameSite=Lax``.
"""

from __future__ import annotations

from starlette.responses import Response

from ..config import Settings

__all__ = ["clear_session_cookie", "set_session_cookie"]


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    """Attach the session cookie (lifetime = ``JWT_TTL_HOURS``) to ``response``."""
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=int(settings.jwt_ttl_hours) * 3600,
        path="/",
        domain=None,
        secure=settings.resolved_cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Expire the session cookie on ``response``."""
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        domain=None,
        secure=settings.resolved_cookie_secure,
        httponly=True,
        samesite="lax",
    )
