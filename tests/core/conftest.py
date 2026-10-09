"""Fixtures for the core (auth / security / usage / legacy / storage / CLI) tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    from aadhi.security.ratelimit import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture()
def make_user(db_session) -> Callable[..., Any]:
    """Create and commit a user: ``make_user("alice", password="...", role="editor", **fields)``."""
    from aadhi.auth.passwords import hash_password
    from aadhi.models import User

    def _make(username: str = "alice", password: str = "Correct-Horse-Battery-9", role: str = "editor", **fields: Any):
        user = User(username=username, password_hash=hash_password(password), role=role, **fields)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make


@pytest.fixture()
def showcase_json() -> dict[str, Any]:
    return json.loads((FIXTURES / "showcase_ohms_law.json").read_text(encoding="utf-8"))


@pytest.fixture()
def demo_slides_json() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "v1_demo_slides.json").read_text(encoding="utf-8"))


def add_error_handler(app: Any) -> None:
    """Render ``AppHTTPException`` with the standard envelope (what aadhi.api.errors does)."""
    from fastapi.responses import JSONResponse

    from aadhi.auth.errors import AppHTTPException

    async def handler(_request, exc: AppHTTPException):  # noqa: ANN001
        return JSONResponse(exc.body(), status_code=exc.status_code, headers=exc.headers)

    app.add_exception_handler(AppHTTPException, handler)
