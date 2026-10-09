"""Bring your own API key: ``/api/keys`` (personal), ``/api/admin/keys`` (server), and how the
requester's resolved keys drive ``/api/meta``, engine validation, the budget pre-check and usage.

``verify_key`` is always replaced (no network); keys never appear in any response or log.
"""

from __future__ import annotations

import json
import logging

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from aadhi import credentials as creds
from aadhi.models import ApiCredential, Job, Project, ProjectVersion, UsageEvent, User, utcnow
from tests.api.factories import add_job, add_project

PERSONAL = "sk-ant-api03-personal-key-AAAAAAAA-1111"
OTHER = "sk-ant-api03-other-user-key-BBBBBBB-2222"
SERVER = "sk-ant-api03-server-key-CCCCCCCCCCC-3333"
ENV = "sk-ant-api03-environment-key-DDDDDD-4444"
OPENAI_PERSONAL = "sk-proj-openai-personal-key-EEEEEE-5555"
GEMINI_PERSONAL = "AIzaSy-gemini-personal-key-FFFFFFFF-6666"
GEMINI_ENV = "AIzaSy-gemini-environment-key-GGGGG-7777"
ALL_SECRETS = (PERSONAL, OTHER, SERVER, ENV, OPENAI_PERSONAL, GEMINI_PERSONAL, GEMINI_ENV)
TXT = ("ohm.txt", b"Ohm's law: V = I R. Current is the flow of charge.", "text/plain")
ENTRY_KEYS = {"provider", "label", "personal", "server_available"}
VIEW_KEYS = {"hint", "updated_at", "last_verified_at", "last_error", "readable"}


@pytest.fixture()
def keys_api(api):
    api.settings.stored_api_keys_enabled = True
    api.settings.user_api_keys_enabled = True
    return api


@pytest.fixture()
def verify(monkeypatch):
    """Replace the provider call: ``verify.result`` is returned, every call is recorded."""

    class Recorder:
        result = (True, "Anthropic Claude accepted the key.")
        calls: list[tuple[str, str]] = []

    recorder = Recorder()
    recorder.calls = []

    async def fake_verify(provider, api_key, settings):
        recorder.calls.append((provider, api_key))
        return recorder.result

    monkeypatch.setattr(creds, "verify_key", fake_verify)
    return recorder


def entry(body: dict, provider: str = "anthropic") -> dict:
    return next(p for p in body["providers"] if p["provider"] == provider)


def admin_client(api, username: str = "root"):
    user = api.user(username, role="admin")
    return user, api.login(username)


def assert_no_secret(*texts: str) -> None:
    for text in texts:
        for secret in ALL_SECRETS:
            assert secret not in text


# --- personal keys -----------------------------------------------------------------------------------


def test_list_shape_and_disabled_flag(api):
    _, c = api.editor("alice")
    body = c.get("/api/keys").json()
    assert body["enabled"] is False  # tests run with STORED_API_KEYS_ENABLED=false
    assert [p["provider"] for p in body["providers"]] == ["gemini", "openai", "anthropic"]
    for p in body["providers"]:
        assert set(p) == ENTRY_KEYS and p["personal"] is None and p["server_available"] is False
    assert entry(body)["label"] == "Anthropic Claude"
    put = c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    assert put.status_code == 403 and put.json()["code"] == "api_keys_disabled"
    api.settings.stored_api_keys_enabled = True
    api.settings.user_api_keys_enabled = False  # server keys allowed, personal keys not
    assert c.get("/api/keys").json()["enabled"] is False
    assert c.put("/api/keys/anthropic", json={"api_key": PERSONAL}).json()["code"] == "api_keys_disabled"
    assert c.post("/api/keys/anthropic/test").json()["code"] == "api_keys_disabled"


def test_save_replace_and_delete_personal_key(keys_api):
    _, c = keys_api.editor("alice")
    r = c.put("/api/keys/anthropic", json={"api_key": f"  {PERSONAL}  "})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == ENTRY_KEYS and body["provider"] == "anthropic" and body["label"] == "Anthropic Claude"
    assert set(body["personal"]) == VIEW_KEYS
    assert body["personal"]["hint"] == "sk-ant-…1111" and body["personal"]["readable"] is True
    assert body["personal"]["last_verified_at"] is None and body["server_available"] is False
    replaced = c.put("/api/keys/anthropic", json={"api_key": PERSONAL.replace("1111", "9999")}).json()
    assert replaced["personal"]["hint"] == "sk-ant-…9999"
    with keys_api.db() as db:
        rows = db.execute(select(ApiCredential)).scalars().all()
        assert len(rows) == 1 and PERSONAL not in rows[0].ciphertext
    assert entry(c.get("/api/keys").json())["personal"]["hint"] == "sk-ant-…9999"
    assert c.delete("/api/keys/anthropic").status_code == 204
    assert entry(c.get("/api/keys").json())["personal"] is None
    assert c.delete("/api/keys/anthropic").status_code == 204  # idempotent


def test_validation_and_unknown_provider(keys_api):
    _, c = keys_api.editor("alice")
    for body in ({"api_key": "short"}, {"api_key": "sk-ant has spaces in it 123"}, {"api_key": "x" * 513},
                 {"api_key": ""}):
        r = c.put("/api/keys/anthropic", json=body)
        assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["body", "api_key"], r.text
        assert body["api_key"].strip() not in r.text or not body["api_key"].strip()
    for body in ({}, {"api_key": 123456789012345678}, {"api_key": PERSONAL, "extra": PERSONAL}):
        r = c.put("/api/keys/anthropic", json=body)
        assert r.status_code == 422 and PERSONAL not in r.text
    for path in ("/api/keys/klingon", "/api/keys/fake", "/api/admin/keys/klingon"):
        assert c.put(path, json={"api_key": PERSONAL}).status_code in (403, 404)
    assert c.put("/api/keys/klingon", json={"api_key": PERSONAL}).json()["code"] == "not_found"
    assert c.delete("/api/keys/klingon").status_code == 404
    assert c.post("/api/keys/klingon/test").status_code == 404
    with keys_api.db() as db:
        assert db.execute(select(ApiCredential)).first() is None


def test_users_never_see_or_touch_each_others_keys(keys_api, verify):
    _, alice = keys_api.editor("alice")
    _, bob = keys_api.editor("bob")
    assert alice.put("/api/keys/anthropic", json={"api_key": PERSONAL}).status_code == 200
    assert entry(bob.get("/api/keys").json())["personal"] is None
    assert bob.post("/api/keys/anthropic/test").status_code == 404  # bob has no key; alice's is not reachable
    assert bob.delete("/api/keys/anthropic").status_code == 204
    assert entry(alice.get("/api/keys").json())["personal"]["hint"] == "sk-ant-…1111"  # untouched
    assert bob.put("/api/keys/anthropic", json={"api_key": OTHER}).status_code == 200
    assert alice.post("/api/keys/anthropic/test").json()["ok"] is True
    assert verify.calls == [("anthropic", PERSONAL)]  # alice's test used alice's key
    assert entry(bob.get("/api/keys").json())["personal"]["last_verified_at"] is None


def test_editors_cannot_use_admin_endpoints(keys_api):
    _, c = keys_api.editor("alice")
    for method, path, body in [
        ("GET", "/api/admin/keys", None),
        ("PUT", "/api/admin/keys/anthropic", {"api_key": SERVER}),
        ("DELETE", "/api/admin/keys/anthropic", None),
        ("POST", "/api/admin/keys/anthropic/test", None),
    ]:
        r = c.request(method, path, json=body)
        assert r.status_code == 403 and r.json()["code"] == "forbidden", (method, path, r.text)
    with keys_api.db() as db:
        assert db.execute(select(ApiCredential)).first() is None


def test_anonymous_requests_are_refused(keys_api):
    c = keys_api.client()
    for method, path in [("GET", "/api/keys"), ("PUT", "/api/keys/anthropic"), ("DELETE", "/api/keys/anthropic"),
                         ("POST", "/api/keys/anthropic/test"), ("GET", "/api/admin/keys")]:
        r = c.request(method, path, json={"api_key": PERSONAL} if method == "PUT" else None)
        assert r.status_code == 401, (method, path, r.text)


def test_key_test_records_success_and_failure(keys_api, verify):
    _, c = keys_api.editor("alice")
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    r = c.post("/api/keys/anthropic/test")
    assert r.status_code == 200 and r.json() == {"ok": True, "message": "Anthropic Claude accepted the key."}
    view = entry(c.get("/api/keys").json())["personal"]
    assert view["last_verified_at"] is not None and view["last_error"] is None
    verify.result = (False, "Anthropic Claude rejected the key (HTTP 401): check that it is correct.")
    r = c.post("/api/keys/anthropic/test")
    assert r.json()["ok"] is False
    view = entry(c.get("/api/keys").json())["personal"]
    assert view["last_error"].startswith("Anthropic Claude rejected the key") and view["last_verified_at"] is not None
    assert verify.calls == [("anthropic", PERSONAL)] * 2


def test_unreadable_key_is_reported_without_calling_the_provider(keys_api, verify):
    _, c = keys_api.editor("alice")
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    with keys_api.db() as db:
        db.execute(update(ApiCredential).values(ciphertext="gAAAAA-not-decryptable"))
        db.commit()
    assert entry(c.get("/api/keys").json())["personal"]["readable"] is False
    r = c.post("/api/keys/anthropic/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "enter it again" in r.json()["message"]
    assert verify.calls == []
    assert entry(c.get("/api/keys").json())["personal"]["last_error"].startswith("This saved key")


def test_key_tests_are_rate_limited(keys_api, verify):
    _, c = keys_api.editor("alice")
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    for _ in range(10):
        assert c.post("/api/keys/anthropic/test").status_code == 200
    limited = c.post("/api/keys/anthropic/test")
    assert limited.status_code == 429 and limited.json()["code"] == "rate_limited"
    assert len(verify.calls) == 10
    _, bob = keys_api.editor("bob")  # per user
    bob.put("/api/keys/anthropic", json={"api_key": OTHER})
    assert bob.post("/api/keys/anthropic/test").status_code == 200


# --- server keys -------------------------------------------------------------------------------------


def test_admin_server_keys_replace_and_fall_back_to_env(keys_api, verify):
    keys_api.settings.anthropic_api_key = SecretStr(ENV)
    _, admin = admin_client(keys_api)
    body = admin.get("/api/admin/keys").json()
    assert body["enabled"] is True and [p["provider"] for p in body["providers"]] == ["gemini", "openai", "anthropic"]
    assert entry(body) == {"provider": "anthropic", "label": "Anthropic Claude", "stored": None, "env": True,
                           "active_source": "env"}
    assert entry(body, "gemini")["active_source"] is None

    r = admin.put("/api/admin/keys/anthropic", json={"api_key": SERVER})
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["active_source"] == "stored" and row["env"] is True and row["stored"]["hint"] == "sk-ant-…3333"
    tested = admin.post("/api/admin/keys/anthropic/test").json()
    assert tested == {"ok": True, "message": "Anthropic Claude accepted the key.", "source": "stored"}
    assert entry(admin.get("/api/admin/keys").json())["stored"]["last_verified_at"] is not None

    assert admin.delete("/api/admin/keys/anthropic").status_code == 204
    assert entry(admin.get("/api/admin/keys").json())["active_source"] == "env"
    assert admin.post("/api/admin/keys/anthropic/test").json()["source"] == "env"
    assert verify.calls == [("anthropic", SERVER), ("anthropic", ENV)]
    assert admin.post("/api/admin/keys/gemini/test").status_code == 404  # nothing configured

    keys_api.settings.stored_api_keys_enabled = False
    disabled = admin.put("/api/admin/keys/anthropic", json={"api_key": SERVER})
    assert disabled.status_code == 403 and disabled.json()["code"] == "api_keys_disabled"
    assert admin.get("/api/admin/keys").json()["enabled"] is False


def test_server_key_is_visible_to_users_as_available(keys_api):
    _, admin = admin_client(keys_api)
    _, alice = keys_api.editor("alice")
    assert entry(alice.get("/api/keys").json())["server_available"] is False
    admin.put("/api/admin/keys/anthropic", json={"api_key": SERVER})
    body = alice.get("/api/keys").json()
    assert entry(body)["server_available"] is True and entry(body)["personal"] is None
    assert entry(body, "openai")["server_available"] is False


def test_secrets_never_leave_the_server(keys_api, verify, caplog, capfd):
    keys_api.settings.anthropic_api_key = SecretStr(ENV)
    caplog.set_level(logging.DEBUG)
    _, admin = admin_client(keys_api)
    _, alice = keys_api.editor("alice")
    texts = [
        admin.put("/api/admin/keys/anthropic", json={"api_key": SERVER}).text,
        alice.put("/api/keys/anthropic", json={"api_key": PERSONAL}).text,
        alice.put("/api/keys/openai", json={"api_key": OPENAI_PERSONAL}).text,
        alice.put("/api/keys/anthropic", json={"api_key": PERSONAL + " tail"}).text,  # 422
        alice.get("/api/keys").text,
        alice.post("/api/keys/anthropic/test").text,
        admin.get("/api/admin/keys").text,
        admin.post("/api/admin/keys/anthropic/test").text,
        alice.get("/api/meta").text,
        admin.get("/api/meta").text,
        alice.get("/api/usage/me").text,
    ]
    assert_no_secret(*texts, caplog.text, capfd.readouterr().err)
    audit = [r.getMessage() for r in caplog.records if r.name == "aadhi.audit"]
    assert any("api key saved: scope=server provider=anthropic" in m for m in audit)
    assert any("api key tested: scope=user provider=anthropic" in m for m in audit)


# --- /api/meta and generation with the requester's keys ------------------------------------------------


def test_meta_reflects_each_users_keys(keys_api):
    _, alice = keys_api.editor("alice")
    _, bob = keys_api.editor("bob")
    meta = alice.get("/api/meta").json()
    assert meta["api_keys"] == {"enabled": True, "personal_enabled": True}
    alice.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    alice.put("/api/keys/openai", json={"api_key": OPENAI_PERSONAL})

    a = {e["id"]: e for e in alice.get("/api/meta").json()["llm"]["engines"]}
    b = {e["id"]: e for e in bob.get("/api/meta").json()["llm"]["engines"]}
    assert (a["anthropic"]["configured"], a["anthropic"]["key_source"]) == (True, "personal")
    assert (a["openai"]["configured"], a["openai"]["key_source"]) == (True, "personal")
    assert (b["anthropic"]["configured"], b["anthropic"]["key_source"]) == (False, None)
    assert a["fake"]["key_source"] is None and a["gemini"]["key_source"] is None
    tts_a = {p["id"]: p["configured"] for p in alice.get("/api/meta").json()["tts"]["providers"]}
    tts_b = {p["id"]: p["configured"] for p in bob.get("/api/meta").json()["tts"]["providers"]}
    assert tts_a["openai"] is True and tts_b["openai"] is False  # the personal OpenAI key powers its voices too

    _, admin = admin_client(keys_api)
    admin.put("/api/admin/keys/anthropic", json={"api_key": SERVER})
    b = {e["id"]: e for e in bob.get("/api/meta").json()["llm"]["engines"]}
    assert (b["anthropic"]["configured"], b["anthropic"]["key_source"]) == (True, "server")
    keys_api.settings.user_api_keys_enabled = False
    assert alice.get("/api/meta").json()["api_keys"] == {"enabled": True, "personal_enabled": False}
    a = {e["id"]: e for e in alice.get("/api/meta").json()["llm"]["engines"]}
    assert a["anthropic"]["key_source"] == "server" and a["openai"]["configured"] is False


def create(c, options: dict):
    return c.post("/api/projects", files={"file": TXT}, data={"options": json.dumps(options)})


def test_engine_validation_uses_the_requesters_keys(keys_api):
    _, alice = keys_api.editor("alice")
    _, bob = keys_api.editor("bob")
    alice.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    assert create(alice, {"llm_provider": "anthropic"}).status_code == 201
    refused = create(bob, {"llm_provider": "anthropic"})
    assert refused.status_code == 422
    detail = refused.json()["detail"][0]
    assert detail["type"] == "engine_not_configured" and "add your own API key" in detail["msg"]


def test_version_engine_checks_use_the_requesters_keys(keys_api):
    bob, c = keys_api.editor("bob")
    with keys_api.db() as db:
        _, version = add_project(db, db.get(User, bob.id))
        db.get(ProjectVersion, version.id).generation_meta = {"options": {"llm_provider": "anthropic"}}
        db.commit()
    url = f"/api/versions/{version.id}/scenes/s1/regenerate"
    refused = c.post(url, json={"instructions": "simpler"})
    assert refused.status_code == 409 and refused.json()["code"] == "engine_not_configured"
    c.put("/api/keys/anthropic", json={"api_key": OTHER})
    assert c.post(url, json={"instructions": "simpler"}).status_code == 202


def test_budget_precheck_is_skipped_for_personal_key_engines(keys_api):
    alice, c = keys_api.editor("alice")
    with keys_api.db() as db:
        db.get(User, alice.id).daily_budget_usd = 1.0
        db.add(UsageEvent(user_id=alice.id, provider="gemini", model="m", operation="llm", cost_usd=2.0,
                          created_at=utcnow()))
        db.commit()
    blocked = create(c, {"llm_provider": "fake"})
    assert blocked.status_code == 429 and blocked.json()["code"] == "budget"
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    assert create(c, {"llm_provider": "anthropic"}).status_code == 201  # billed to alice's own key
    assert create(c, {"llm_provider": "fake"}).status_code == 429


def over_budget(api, user) -> None:
    """``user`` has a 1.0 daily budget and already spent 2.0 on the server today."""
    with api.db() as db:
        db.get(User, user.id).daily_budget_usd = 1.0
        db.add(UsageEvent(user_id=user.id, provider="gemini", model="m", operation="llm", cost_usd=2.0,
                          created_at=utcnow()))
        db.commit()


def claude_version(api, user) -> ProjectVersion:
    with api.db() as db:
        _, version = add_project(db, db.get(User, user.id))
        db.get(ProjectVersion, version.id).generation_meta = {"options": {"llm_provider": "anthropic"}}
        db.commit()
    return version


def test_budget_precheck_counts_voices_and_images_the_server_pays_for(keys_api):
    """A personal Claude key alone does not skip the pre-check while the job's voices would still be
    paid with a server key: the user would pay for the script, then fail at the first voice."""
    keys_api.settings.tts_provider = "gemini"
    keys_api.settings.gemini_api_key = SecretStr(GEMINI_ENV)
    alice, c = keys_api.editor("alice")
    over_budget(keys_api, alice)
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    blocked = create(c, {"llm_provider": "anthropic"})
    assert blocked.status_code == 429 and blocked.json()["code"] == "budget"  # Gemini voices on the .env key
    version = claude_version(keys_api, alice)
    build = c.post(f"/api/versions/{version.id}/build", json={"scene_ids": None})
    assert build.status_code == 429 and build.json()["code"] == "budget"
    assert create(c, {"llm_provider": "anthropic", "tts_provider": "fake"}).status_code == 201  # free voices
    keys_api.settings.image_provider = "gemini"  # ... but Gemini images are paid again
    assert create(c, {"llm_provider": "anthropic", "tts_provider": "fake"}).status_code == 429
    assert create(c, {"llm_provider": "anthropic", "tts_provider": "fake",
                      "allow_generated_images": False}).status_code == 201
    c.put("/api/keys/gemini", json={"api_key": GEMINI_PERSONAL})  # now her own Gemini key pays for both
    assert create(c, {"llm_provider": "anthropic"}).status_code == 201
    assert c.post(f"/api/versions/{version.id}/build", json={"scene_ids": None}).status_code == 202


def test_retry_checks_the_engine_with_the_requesters_keys(keys_api):
    """A retry runs under the requester's keys: refused up front (409) when they cannot run the job's
    engine, before the old job is claimed, instead of failing in the worker."""
    alice, c = keys_api.editor("alice")
    with keys_api.db() as db:
        owner = db.get(User, alice.id)
        project, version = add_project(db, owner)
        failed = add_job(db, kind="generate_lecture", status="failed", user=owner, project=project, version=version,
                         payload={"options": {"llm_provider": "anthropic"}, "base_revision": 1})
    refused = c.post(f"/api/jobs/{failed.id}/retry")
    assert refused.status_code == 409 and refused.json()["code"] == "engine_not_configured", refused.text
    assert "add your own API key" in refused.json()["detail"]
    with keys_api.db() as db:
        assert db.get(Job, failed.id).error_code != "retried"  # the job keeps its single retry
        assert db.execute(select(func.count()).select_from(Job)).scalar_one() == 1
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    assert c.post(f"/api/jobs/{failed.id}/retry").status_code == 202


def test_translate_retry_checks_the_source_versions_engine(keys_api):
    alice, c = keys_api.editor("alice")
    source = claude_version(keys_api, alice)
    with keys_api.db() as db:
        owner = db.get(User, alice.id)
        target = ProjectVersion(project_id=source.project_id, number=2, status="failed", language="ta-IN", revision=1,
                                source_version_id=source.id)
        db.add(target)
        db.commit()
        db.refresh(target)
        failed = add_job(db, kind="translate", status="failed", user=owner, project=db.get(Project, source.project_id),
                         version=target, payload={"source_version_id": source.id, "target_language": "ta-IN"})
    refused = c.post(f"/api/jobs/{failed.id}/retry")
    assert refused.status_code == 409 and refused.json()["code"] == "engine_not_configured", refused.text


def test_retry_budget_precheck_follows_who_pays(keys_api):
    alice, c = keys_api.editor("alice")
    over_budget(keys_api, alice)
    version = claude_version(keys_api, alice)
    with keys_api.db() as db:
        owner = db.get(User, alice.id)
        failed = add_job(db, kind="build_assets", status="failed", user=owner,
                         project=db.get(Project, version.project_id), version=version,
                         payload={"scene_ids": None, "base_revision": 1})
    assert c.post(f"/api/jobs/{failed.id}/retry").json()["code"] == "budget"
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})  # Claude on her key, voices and images free
    assert c.post(f"/api/jobs/{failed.id}/retry").status_code == 202


def test_usage_me_reports_own_key_spend(keys_api):
    alice, c = keys_api.editor("alice")
    with keys_api.db() as db:
        db.get(User, alice.id).daily_budget_usd = 1.0
        for cost, billed in ((0.25, "server"), (3.0, "user"), (0.5, "user")):
            db.add(UsageEvent(user_id=alice.id, provider="anthropic", model="m", operation="llm", cost_usd=cost,
                              billed_to=billed, created_at=utcnow()))
        db.commit()
    body = c.get("/api/usage/me").json()
    assert body["total_usd"] == pytest.approx(3.75)
    assert body["own_key_usd"] == pytest.approx(3.5)
    assert body["today_usd"] == pytest.approx(0.25)  # the daily budget only counts server-billed spend
    assert create(c, {"llm_provider": "fake"}).status_code == 201  # 0.25 of 1.0 used: not blocked


def test_deleted_user_takes_their_keys_along(keys_api):
    alice, c = keys_api.editor("alice")
    c.put("/api/keys/anthropic", json={"api_key": PERSONAL})
    with keys_api.db() as db:
        db.execute(update(Project).values(owner_id=alice.id))  # no projects: nothing restricts the delete
        db.delete(db.get(User, alice.id))
        db.commit()
        assert db.execute(select(ApiCredential)).first() is None  # ondelete=CASCADE
