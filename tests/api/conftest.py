"""API test harness.

* Registers fallback fakes for other areas' modules that cannot be imported yet
  (``tests/api/fakes``); real modules always win.
* ``api`` fixture: a fresh app (``create_app``) on the per-test database with its lifespan
  running, plus helpers to create users and signed-in "browser" clients (cookie + CSRF header +
  same Origin).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from tests.api import fakes

FAKES_IN_USE = fakes.install()

BASE = "http://testserver"
BROWSER_HEADERS = {"Origin": BASE, "X-Aadhi-CSRF": "1"}


def pytest_report_header(config: pytest.Config) -> list[str]:
    if not FAKES_IN_USE:
        return ["tests/api: all integration modules are real"]
    return ["tests/api: fallback fakes in use for: " + ", ".join(sorted(FAKES_IN_USE))]


@dataclass
class Api:
    """A running app plus helpers."""

    app: Any
    settings: Any
    clients: list[Any] = field(default_factory=list)

    def client(self, *, browser: bool = True, **kw: Any):
        from fastapi.testclient import TestClient

        c = TestClient(self.app, base_url=BASE, headers=dict(BROWSER_HEADERS) if browser else {}, **kw)
        self.clients.append(c)
        return c

    def db(self):
        from aadhi.db import get_sessionmaker

        return get_sessionmaker()()

    def user(self, username: str, *, role: str = "editor", must_change: bool = False, password: str | None = None):
        from tests.api.factories import PASSWORD, add_user

        with self.db() as db:
            return add_user(db, username, role=role, must_change=must_change, password=password or PASSWORD)

    def login(self, username: str, password: str | None = None, *, browser: bool = True, **kw: Any):
        from tests.api.factories import PASSWORD

        c = self.client(browser=browser, **kw)
        r = c.post("/api/auth/login", json={"username": username, "password": password or PASSWORD})
        assert r.status_code == 200, r.text
        return c

    def editor(self, username: str = "alice", **kw: Any):
        """(user, signed-in browser client)."""
        user = self.user(username, **kw)
        return user, self.login(username)


@pytest.fixture()
def api(app_env) -> Iterator[Api]:
    from aadhi.main import create_app
    from aadhi.security.ratelimit import reset_rate_limits

    reset_rate_limits()
    app = create_app(app_env)
    harness = Api(app=app, settings=app_env)
    lifespan_client = harness.client(browser=False)
    with lifespan_client:
        yield harness
        for c in harness.clients:
            if c is not lifespan_client:
                c.close()
    reset_rate_limits()
