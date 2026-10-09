"""HTTP errors raised by auth/security dependencies.

``AppHTTPException`` is a plain FastAPI ``HTTPException`` that also carries a machine-readable
``code`` (and optional ``extra`` fields). The API layer's exception handler (``aadhi.api.errors``)
renders it as the standard envelope ``{"detail": <message>, "code": <code>, **extra}``; without that
handler FastAPI's default handler still produces a sensible ``{"detail": ...}`` response.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

__all__ = [
    "AppHTTPException",
    "error_body",
    "forbidden",
    "password_change_required",
    "unauthenticated",
]


class AppHTTPException(HTTPException):
    """``HTTPException`` with a machine ``code`` (e.g. ``unauthenticated``, ``rate_limited``)."""

    def __init__(
        self,
        status_code: int,
        detail: Any = None,
        *,
        code: str,
        headers: Mapping[str, str] | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=dict(headers) if headers else None)
        self.code = code
        self.extra: dict[str, Any] = dict(extra)

    def body(self) -> dict[str, Any]:
        """The JSON error envelope for this exception."""
        return error_body(self.detail, self.code, **self.extra)


def error_body(detail: Any, code: str, **extra: Any) -> dict[str, Any]:
    """Standard API error envelope ``{"detail", "code", **extra}`` (docs/API.md)."""
    body: dict[str, Any] = {"detail": detail, "code": code}
    body.update(extra)
    return body


def unauthenticated(message: str = "Not authenticated") -> AppHTTPException:
    """401 ``unauthenticated``."""
    return AppHTTPException(401, message, code="unauthenticated", headers={"WWW-Authenticate": "Bearer"})


def forbidden(message: str = "You do not have permission to do this") -> AppHTTPException:
    """403 ``forbidden``."""
    return AppHTTPException(403, message, code="forbidden")


def password_change_required() -> AppHTTPException:
    """403 ``password_change_required`` (user must change the password before anything else)."""
    return AppHTTPException(403, "You must change your password before continuing", code="password_change_required")
