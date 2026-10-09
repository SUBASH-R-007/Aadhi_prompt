"""FastAPI auth dependencies: cookie & bearer, inactive, token_version revocation, must_change_password."""

from __future__ import annotations

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from aadhi.auth.cookies import clear_session_cookie, set_session_cookie
from aadhi.auth.deps import (
    get_current_user,
    get_current_user_allow_pending,
    get_optional_user,
    require_roles,
)
from aadhi.auth.service import authenticate, bump_token_version, set_password
from aadhi.auth.tokens import create_scoped_token, create_session_token
from aadhi.models import User

from .conftest import add_error_handler


def _app() -> FastAPI:
    app = FastAPI()
    add_error_handler(app)

    @app.get("/me")
    def me(user: Annotated[User, Depends(get_current_user)]):
        return {"id": user.id, "role": user.role}

    @app.get("/pending")
    def pending(user: Annotated[User, Depends(get_current_user_allow_pending)]):
        return {"id": user.id}

    @app.get("/optional")
    def optional(user: Annotated[User | None, Depends(get_optional_user)]):
        return {"id": user.id if user else None}

    @app.get("/admin")
    def admin(user: Annotated[User, Depends(require_roles("admin"))]):
        return {"id": user.id}

    @app.get("/both")
    def both(
        a: Annotated[User, Depends(get_current_user)],
        b: Annotated[User | None, Depends(get_optional_user)],
    ):
        return {"same": a is b}

    return app


@pytest.fixture()
def client(app_env):
    with TestClient(_app()) as c:
        yield c


def test_unauthenticated(client):
    r = client.get("/me")
    assert r.status_code == 401
    assert r.json()["code"] == "unauthenticated"
    assert r.headers["www-authenticate"] == "Bearer"
    assert client.get("/optional").json() == {"id": None}


def test_cookie_auth(client, make_user, app_env):
    user = make_user("alice")
    client.cookies.set(app_env.cookie_name, create_session_token(user, app_env))
    assert client.get("/me").json() == {"id": user.id, "role": "editor"}
    assert client.get("/optional").json() == {"id": user.id}
    assert client.get("/both").json() == {"same": True}  # one cached auth resolution per request


def test_bearer_auth_and_precedence(client, make_user, app_env):
    alice, bob = make_user("alice"), make_user("bob")
    client.cookies.set(app_env.cookie_name, create_session_token(alice, app_env))
    r = client.get("/me", headers={"Authorization": f"Bearer {create_session_token(bob, app_env)}"})
    assert r.json()["id"] == bob.id
    # An explicit but broken Bearer header never falls back to the cookie.
    assert client.get("/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "Bearer"}).status_code == 401
    assert client.get("/me", headers={"Authorization": "bearer   "}).status_code == 401


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Digest username=x", "Negotiate abc", "Basic"])
def test_non_bearer_authorization_falls_back_to_cookie(client, make_user, app_env, header):
    """A reverse proxy using HTTP Basic auth must not break cookie sessions."""
    alice = make_user("alice")
    client.cookies.set(app_env.cookie_name, create_session_token(alice, app_env))
    assert client.get("/me", headers={"Authorization": header}).json()["id"] == alice.id
    client.cookies.clear()
    assert client.get("/me", headers={"Authorization": header}).status_code == 401


def test_settings_come_from_the_app(app_env, make_user):
    """create_app(settings) with settings that differ from the global ones (other JWT secret and an
    https cookie name): tokens issued with the app's settings must authenticate."""
    from aadhi.config import Settings

    app_settings = Settings(
        app_env="test",
        base_url="https://lectures.rec.edu",
        jwt_secret="x" * 48,
        data_dir=app_env.data_dir,
    )
    assert app_settings.cookie_name != app_env.cookie_name
    alice = make_user("alice")
    app = _app()
    app.state.settings = app_settings
    with TestClient(app, base_url="https://lectures.rec.edu") as c:
        c.cookies.set(app_settings.cookie_name, create_session_token(alice, app_settings))
        assert c.get("/me").json()["id"] == alice.id
        c.cookies.clear()
        global_token = create_session_token(alice, app_env)
        assert c.get("/me", headers={"Authorization": f"Bearer {global_token}"}).status_code == 401
        ok = c.get("/me", headers={"Authorization": f"Bearer {create_session_token(alice, app_settings)}"})
        assert ok.status_code == 200


def test_invalid_and_scoped_tokens_rejected(client, make_user, app_env):
    make_user("alice")
    for token in ("garbage", create_scoped_token("render", {"vid": 1}, 60, app_env)):
        r = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401 and r.json()["code"] == "unauthenticated"


def test_inactive_user(client, make_user, app_env):
    user = make_user("alice", is_active=False)
    r = client.get("/me", headers={"Authorization": f"Bearer {create_session_token(user, app_env)}"})
    assert r.status_code == 401
    assert "disabled" in r.json()["detail"]


def test_unknown_user(client, app_env):
    from types import SimpleNamespace

    token = create_session_token(SimpleNamespace(id=999, token_version=0), app_env)
    assert client.get("/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_token_version_revocation(client, make_user, app_env, db_session):
    user = make_user("alice")
    token = create_session_token(user, app_env)
    hdr = {"Authorization": f"Bearer {token}"}
    assert client.get("/me", headers=hdr).status_code == 200
    assert bump_token_version(db_session, user.id) == 1
    db_session.commit()
    r = client.get("/me", headers=hdr)
    assert r.status_code == 401 and "ended" in r.json()["detail"]
    db_session.refresh(user)
    assert (
        client.get("/me", headers={"Authorization": f"Bearer {create_session_token(user, app_env)}"}).status_code == 200
    )


def test_role_changes_apply_immediately(client, make_user, app_env, db_session):
    user = make_user("alice", role="admin")
    hdr = {"Authorization": f"Bearer {create_session_token(user, app_env)}"}
    assert client.get("/admin", headers=hdr).status_code == 200
    db_session.execute(update(User).where(User.id == user.id).values(role="editor"))
    db_session.commit()
    r = client.get("/admin", headers=hdr)
    assert r.status_code == 403 and r.json()["code"] == "forbidden"


def test_must_change_password(client, make_user, app_env):
    user = make_user("alice", must_change_password=True)
    hdr = {"Authorization": f"Bearer {create_session_token(user, app_env)}"}
    r = client.get("/me", headers=hdr)
    assert r.status_code == 403 and r.json()["code"] == "password_change_required"
    assert client.get("/pending", headers=hdr).status_code == 200
    assert client.get("/optional", headers=hdr).json() == {"id": None}
    assert client.get("/admin", headers=hdr).status_code == 403


def test_require_roles_validation():
    with pytest.raises(ValueError):
        require_roles()
    with pytest.raises(ValueError):
        require_roles("student")


def test_set_password_revokes_and_clears_flag(app_env, make_user, db_session):
    user = make_user("alice", must_change_password=True)
    tv = set_password(db_session, user.id, "Brand-New-Pass-77")
    db_session.commit()
    db_session.refresh(user)
    assert tv == 1 and user.token_version == 1 and user.must_change_password is False
    assert authenticate(db_session, "alice", "Brand-New-Pass-77") is not None
    assert set_password(db_session, 4242, "Brand-New-Pass-77") is None


def test_authenticate(app_env, make_user, db_session):
    make_user("alice", password="Right-Password-1")
    make_user("carol", password="Right-Password-1", is_active=False)
    user = authenticate(db_session, "alice", "Right-Password-1")
    assert user is not None and user.last_login_at is not None
    assert authenticate(db_session, "alice", "wrong") is None
    assert authenticate(db_session, "nobody", "Right-Password-1") is None
    assert authenticate(db_session, "carol", "Right-Password-1") is None
    assert authenticate(db_session, "x" * 100, "Right-Password-1") is None


def test_authenticate_upgrades_weak_hash(app_env, make_user, db_session, monkeypatch):
    from aadhi.auth import passwords

    make_user("alice", password="Right-Password-1")  # 4 rounds in tests
    monkeypatch.setattr(passwords, "_rounds", lambda: 5)
    user = authenticate(db_session, "alice", "Right-Password-1")
    db_session.commit()
    assert user.password_hash.startswith("$2b$05$")


def test_cookie_helpers(app_env):
    from starlette.responses import Response

    resp = Response()
    set_session_cookie(resp, "tok", app_env)
    header = resp.headers["set-cookie"]
    assert header.startswith("aadhi_session=tok") and "HttpOnly" in header and "SameSite=lax" in header
    assert "Path=/" in header and "Secure" not in header and "Domain" not in header
    secure = app_env.model_copy(update={"base_url": "https://aadhi.example.edu"})
    resp2 = Response()
    set_session_cookie(resp2, "tok", secure)
    h2 = resp2.headers["set-cookie"]
    assert h2.startswith("__Host-aadhi_session=tok") and "Secure" in h2
    resp3 = Response()
    clear_session_cookie(resp3, secure)
    assert "__Host-aadhi_session=" in resp3.headers["set-cookie"] and "Max-Age=0" in resp3.headers["set-cookie"]
