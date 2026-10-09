"""Auth flows: cookie session, bearer tokens, logout revocation, pending password change, rate limit."""

from __future__ import annotations

from sqlalchemy import select

from aadhi.models import User
from tests.api.factories import PASSWORD

NEW_PASSWORD = "a-much-better-passphrase-42"


def test_login_sets_httponly_cookie_and_returns_user(api):
    api.user("alice")
    c = api.client()
    r = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["username"] == "alice"
    assert body["user"]["role"] == "editor"
    assert "access_token" not in body
    assert "password_hash" not in body["user"]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(api.settings.cookie_name + "=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    assert c.get("/api/auth/me").json()["user"]["username"] == "alice"
    with api.db() as db:
        assert db.execute(select(User.last_login_at).where(User.username == "alice")).scalar_one() is not None


def test_login_failures_share_one_generic_message(api):
    api.user("alice")
    c = api.client()
    wrong = c.post("/api/auth/login", json={"username": "alice", "password": "nope-nope-nope"})
    unknown = c.post("/api/auth/login", json={"username": "nobody", "password": "nope-nope-nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "unauthenticated"
    assert "set-cookie" not in wrong.headers


def test_inactive_user_cannot_log_in(api):
    user = api.user("alice")
    with api.db() as db:
        db.get(User, user.id).is_active = False
        db.commit()
    r = api.client().post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert r.status_code == 401


def test_issue_token_returns_bearer_token_usable_without_cookie(api):
    api.user("alice")
    r = api.client().post("/api/auth/login", json={"username": "alice", "password": PASSWORD, "issue_token": True})
    token = r.json()["access_token"]
    bare = api.client(browser=False)
    assert bare.get("/api/auth/me").status_code == 401
    me = bare.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    # Bearer clients need no CSRF header (no cookie).
    created = bare.post("/api/projects/import", headers={"Authorization": f"Bearer {token}"})
    assert created.status_code == 422  # reached the endpoint (missing file), not 403 csrf


def test_logout_revokes_every_session(api):
    api.user("alice")
    c = api.client()
    token = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD, "issue_token": True}).json()[
        "access_token"
    ]
    other_device = api.login("alice")
    r = c.post("/api/auth/logout")
    assert r.status_code == 204
    assert api.settings.cookie_name in r.headers.get("set-cookie", "")
    assert c.get("/api/auth/me").status_code == 401
    assert other_device.get("/api/auth/me").status_code == 401
    assert (
        api.client(browser=False).get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def _token_version(api, username: str) -> int:
    with api.db() as db:
        return int(db.execute(select(User.token_version).where(User.username == username)).scalar_one())


def test_logout_with_a_stale_cookie_still_clears_it(api):
    """An expired/revoked cookie must not make logout fail: 204 + Set-Cookie that expires it."""
    from sqlalchemy import update

    api.user("alice")
    c = api.login("alice")
    with api.db() as db:  # e.g. the password was changed on another device
        db.execute(update(User).where(User.username == "alice").values(token_version=User.token_version + 1))
        db.commit()
    before = _token_version(api, "alice")
    r = c.post("/api/auth/logout")
    assert r.status_code == 204
    set_cookie = r.headers.get("set-cookie", "")
    assert api.settings.cookie_name in set_cookie and ("Max-Age=0" in set_cookie or "expires=" in set_cookie.lower())
    assert api.settings.cookie_name not in c.cookies  # the browser dropped it
    assert _token_version(api, "alice") == before  # nothing to revoke for an invalid session


def test_logout_without_any_session_is_a_harmless_204(api):
    c = api.client()
    r = c.post("/api/auth/logout")
    assert r.status_code == 204
    assert api.settings.cookie_name in r.headers.get("set-cookie", "")
    garbage = api.client()
    garbage.cookies.set(api.settings.cookie_name, "not-a-jwt")
    assert garbage.post("/api/auth/logout").status_code == 204


def test_logout_still_requires_the_csrf_header(api):
    api.user("alice")
    c = api.login("alice")
    r = c.post("/api/auth/logout", headers={"X-Aadhi-CSRF": ""})
    assert r.status_code == 403
    assert c.get("/api/auth/me").status_code == 200  # still signed in


def test_pending_user_logout_revokes_sessions(api):
    api.user("bob", must_change=True)
    c = api.login("bob")
    before = _token_version(api, "bob")
    assert c.post("/api/auth/logout").status_code == 204
    assert _token_version(api, "bob") == before + 1


def test_must_change_password_blocks_everything_but_allowed_routes(api):
    api.user("bob", must_change=True)
    c = api.login("bob")
    blocked = c.get("/api/projects")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "password_change_required"
    assert c.get("/api/meta").status_code == 403
    assert c.get("/api/auth/me").status_code == 200


def test_change_password_flow(api):
    api.user("bob", must_change=True)
    c = api.login("bob")
    other = api.login("bob")
    wrong = c.post(
        "/api/auth/change-password", json={"current_password": "wrong-password-1", "new_password": NEW_PASSWORD}
    )
    assert wrong.status_code == 422 and wrong.json()["code"] == "validation"
    weak = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code == 422
    assert weak.json()["detail"][0]["loc"] == ["body", "new_password"]
    same = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": PASSWORD})
    assert same.status_code == 422
    ok = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert ok.status_code == 204, ok.text
    assert api.settings.cookie_name in ok.headers["set-cookie"]
    # This client got a fresh cookie; other sessions are revoked; the flag is cleared.
    me = c.get("/api/auth/me").json()["user"]
    assert me["must_change_password"] is False
    assert c.get("/api/projects").status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    assert api.client().post("/api/auth/login", json={"username": "bob", "password": NEW_PASSWORD}).status_code == 200


def test_login_rate_limit_per_username_and_ip(api):
    api.settings.login_rate_limit_per_minute = 3
    api.user("alice")
    c = api.client()
    codes = [
        c.post("/api/auth/login", json={"username": "alice", "password": "bad-password-x"}).status_code
        for _ in range(3)
    ]
    assert codes == [401, 401, 401]
    limited = c.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) >= 1
    # Another username from the same IP is a different bucket.
    api.user("carol")
    assert c.post("/api/auth/login", json={"username": "carol", "password": PASSWORD}).status_code == 200


def test_login_validation_errors_use_envelope(api):
    r = api.client().post("/api/auth/login", json={"username": ""})
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation"
    assert isinstance(body["detail"], list) and body["detail"][0]["loc"][0] == "body"
    assert all("input" not in err for err in body["detail"])


def test_general_api_rate_limit_per_user(api):
    api.settings.api_rate_limit_per_minute = 3
    _, alice = api.editor("alice")
    _, bob = api.editor("bob")
    assert [alice.get("/api/projects").status_code for _ in range(3)] == [200, 200, 200]
    limited = alice.get("/api/jobs")
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert bob.get("/api/projects").status_code == 200  # per user, not global
    assert alice.get("/api/auth/me").status_code == 200  # auth routes are not part of the bucket
