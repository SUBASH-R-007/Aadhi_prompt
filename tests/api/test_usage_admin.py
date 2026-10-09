"""Usage reports and admin user management."""

from __future__ import annotations

from aadhi.models import Job, UsageEvent, User
from tests.api.factories import PASSWORD, add_project

STRONG = "Another-strong-passphrase-9"


def seed_usage(api, user, project=None, *, cost: float = 0.25, operation: str = "llm", job: Job | None = None):
    with api.db() as db:
        db.add(
            UsageEvent(
                user_id=user.id,
                project_id=project.id if project else None,
                job_id=job.id if job else None,
                provider="gemini",
                model="gemini-2.5-flash",
                operation=operation,
                cost_usd=cost,
            )
        )
        db.commit()


def test_usage_me(api):
    alice, c = api.editor("alice")
    seed_usage(api, alice, cost=0.5)
    seed_usage(api, alice, cost=0.25, operation="tts")
    body = c.get("/api/usage/me", params={"days": 7}).json()
    assert body["total_usd"] == 0.75 and body["today_usd"] == 0.75
    assert body["daily_budget_usd"] == api.settings.daily_budget_usd_per_user
    assert {o["operation"]: o["usd"] for o in body["by_operation"]} == {"llm": 0.5, "tts": 0.25}
    assert c.get("/api/usage/me", params={"days": 0}).status_code == 422
    assert c.get("/api/usage/me", params={"days": 1000}).status_code == 422


def test_usage_project_requires_access(api):
    alice, c = api.editor("alice")
    with api.db() as db:
        project, version = add_project(db, alice)
        job = Job(kind="generate_lecture", status="succeeded", user_id=alice.id, project_id=project.id, max_attempts=2)
        db.add(job)
        db.commit()
        db.refresh(job)
    seed_usage(api, alice, project, cost=1.5, job=job)
    body = c.get(f"/api/usage/projects/{project.id}").json()
    assert body["total_usd"] == 1.5
    assert body["by_job"][0]["job_id"] == job.id
    _, bob = api.editor("bob")
    assert bob.get(f"/api/usage/projects/{project.id}").status_code == 404


def test_admin_usage(api):
    alice, c = api.editor("alice")
    seed_usage(api, alice, cost=2.0)
    api.user("root", role="admin")
    admin = api.login("root")
    body = admin.get("/api/admin/usage").json()
    assert body["total_usd"] == 2.0
    assert {u["username"]: u["total_usd"] for u in body["users"]}["alice"] == 2.0
    assert c.get("/api/admin/usage").status_code == 403


def admin_client(api):
    root = api.user("root", role="admin")
    return root, api.login("root")


def test_admin_creates_users_who_must_change_password(api):
    _, admin = admin_client(api)
    r = admin.post("/api/admin/users", json={"username": "teacher1", "password": STRONG, "role": "editor"})
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    assert user["must_change_password"] is True and user["role"] == "editor" and user["is_active"] is True
    c = api.login("teacher1", STRONG)
    assert c.get("/api/projects").json()["code"] == "password_change_required"
    names = [u["username"] for u in admin.get("/api/admin/users").json()["items"]]
    assert "teacher1" in names and "root" in names


def test_admin_create_validation(api):
    _, admin = admin_client(api)
    assert admin.post("/api/admin/users", json={"username": "x", "password": STRONG}).status_code == 422
    assert admin.post("/api/admin/users", json={"username": "bad name", "password": STRONG}).status_code == 422
    assert (
        admin.post("/api/admin/users", json={"username": "viewer1", "password": STRONG, "role": "viewer"}).status_code
        == 422
    )
    weak = admin.post("/api/admin/users", json={"username": "teacher2", "password": "short"})
    assert weak.status_code == 422 and weak.json()["detail"][0]["loc"] == ["body", "password"]
    assert admin.post("/api/admin/users", json={"username": "teacher2", "password": STRONG}).status_code == 201
    dup = admin.post("/api/admin/users", json={"username": "Teacher2", "password": STRONG})
    assert dup.status_code == 409 and dup.json()["code"] == "username_taken"


def test_role_and_active_changes_revoke_sessions(api):
    _, admin = admin_client(api)
    alice, c = api.editor("alice")
    r = admin.patch(f"/api/admin/users/{alice.id}", json={"role": "admin"})
    assert r.status_code == 200 and r.json()["user"]["role"] == "admin"
    assert c.get("/api/auth/me").status_code == 401  # token_version bumped
    c2 = api.login("alice")
    assert c2.get("/api/admin/users").status_code == 200
    admin.patch(f"/api/admin/users/{alice.id}", json={"is_active": False})
    assert c2.get("/api/auth/me").status_code == 401
    assert api.client().post("/api/auth/login", json={"username": "alice", "password": PASSWORD}).status_code == 401


def test_budget_change_does_not_revoke_sessions(api):
    _, admin = admin_client(api)
    alice, c = api.editor("alice")
    r = admin.patch(f"/api/admin/users/{alice.id}", json={"daily_budget_usd": 3.5})
    assert r.json()["user"]["daily_budget_usd"] == 3.5
    assert c.get("/api/auth/me").status_code == 200
    assert c.get("/api/usage/me").json()["daily_budget_usd"] == 3.5
    cleared = admin.patch(f"/api/admin/users/{alice.id}", json={"daily_budget_usd": None})
    assert cleared.json()["user"]["daily_budget_usd"] is None
    assert admin.patch(f"/api/admin/users/{alice.id}", json={"daily_budget_usd": -1}).status_code == 422


def test_admin_password_reset_forces_change_and_revokes(api):
    _, admin = admin_client(api)
    alice, c = api.editor("alice")
    r = admin.patch(f"/api/admin/users/{alice.id}", json={"password": STRONG})
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True
    assert c.get("/api/auth/me").status_code == 401
    assert api.client().post("/api/auth/login", json={"username": "alice", "password": STRONG}).status_code == 200
    no_force = admin.patch(
        f"/api/admin/users/{alice.id}", json={"password": STRONG + "x", "must_change_password": False}
    )
    assert no_force.json()["user"]["must_change_password"] is False
    assert admin.patch(f"/api/admin/users/{alice.id}", json={"password": "short"}).status_code == 422


def test_admins_cannot_lock_themselves_out(api):
    root, admin = admin_client(api)
    me = admin.patch(f"/api/admin/users/{root.id}", json={"role": "editor"})
    assert me.status_code == 409 and me.json()["code"] == "self_lockout"
    assert admin.patch(f"/api/admin/users/{root.id}", json={"is_active": False}).status_code == 409
    assert admin.patch(f"/api/admin/users/{root.id}", json={"daily_budget_usd": 1}).status_code == 200
    second = api.user("second", role="admin")
    second_client = api.login("second")
    assert admin.patch(f"/api/admin/users/{second.id}", json={"role": "editor"}).status_code == 200
    assert second_client.get("/api/auth/me").status_code == 401  # demotion revoked the session
    assert admin.patch("/api/admin/users/999999", json={"role": "editor"}).status_code == 404


def test_last_active_admin_cannot_be_removed(api, monkeypatch):
    from aadhi.api.routers import admin as admin_router

    _, admin = admin_client(api)
    second = api.user("second", role="admin")
    monkeypatch.setattr(admin_router, "_other_active_admins", lambda db, user_id: 0)
    r = admin.patch(f"/api/admin/users/{second.id}", json={"is_active": False})
    assert r.status_code == 409 and r.json()["code"] == "last_admin"
    with api.db() as db:
        assert db.get(User, second.id).is_active is True
