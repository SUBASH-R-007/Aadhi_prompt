"""HTTPException with a machine ``code`` (mirrors core's AppHTTPException)."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class AppHTTPException(HTTPException):
    def __init__(self, status_code: int, code: str, detail: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
