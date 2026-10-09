"""``aadhi.credentials``: validation, hints, save/replace/delete, key resolution, billing, verification.

No network: provider SDK clients are replaced by fakes and ``verify_key`` failures are simulated.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import false, select, update

from aadhi import credentials as creds
from aadhi.credentials import (
    CredentialError,
    billed_to,
    credential_view,
    delete_credential,
    key_hint,
    personal_key_rejection,
    record_verification,
    resolve_keys,
    save_credential,
    validate_api_key,
)
from aadhi.jobs.base import FatalJobError
from aadhi.models import ApiCredential
from aadhi.providers.base import ProviderError
from aadhi.security.redaction import registered_secrets

PERSONAL = "sk-ant-api03-personal-AAAAAAAAAAAA-1111"
SERVER = "sk-ant-api03-server-BBBBBBBBBBBBBB-2222"
ENV = "sk-ant-api03-environment-CCCCCCCCC-3333"
GEMINI_PERSONAL = "AIzaSyPersonalGeminiKey-0123456789"


@pytest.fixture()
def keys_env(app_env):
    app_env.stored_api_keys_enabled = True
    app_env.user_api_keys_enabled = True
    return app_env


def save(db, user, api_key, *, provider="anthropic", scope="user", settings=None):
    row = save_credential(db, provider=provider, api_key=api_key, scope=scope, user=user, settings=settings)
    db.commit()
    return row


# --- validation & hints ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        ("sk-ant-api03-abcdefghijklmnopWXYZ", "sk-ant-…WXYZ"),
        ("sk-proj-abcdefghijklmnop1234", "sk-proj-…1234"),
        ("sk-abcdefghijklmnopqrstu9876", "sk-…9876"),
        ("AIzaSyABCDEFGHIJKLMNOPQRS5555", "AIza…5555"),
        ("plainvendorkey-0123456789zz", "…89zz"),
    ],
)
def test_hint_is_vendor_prefix_plus_last_four(key, hint):
    assert key_hint(key) == hint
    assert hint.split("…", 1)[1] == key[-4:]  # never more of the secret than its last four characters


def test_validation_strips_and_refuses_bad_input():
    assert validate_api_key("  sk-ant-api03-valid-key-123  \n") == "sk-ant-api03-valid-key-123"
    bad_keys = ("", "   ", "short-key", "x" * 513, "sk-ant has spaces inside", "sk-ant-\x00control-chars",
                "sk-ant-ünïcode-key-1234", None, 1234567890123456789,
                # not key-shaped: such strings must never reach the shared redaction list
                "/api/keys/openai", "provider=anthropic", "assets.tts_fallback", "teacher@example.com")
    for bad in bad_keys:
        with pytest.raises(CredentialError) as info:
            validate_api_key(bad)
        if isinstance(bad, str) and len(bad) > 3:
            assert bad.strip() not in str(info.value)
    assert validate_api_key("k" * 16) and validate_api_key("k" * 512)
    for real in (PERSONAL, GEMINI_PERSONAL, "sk-proj-AbC_dEf-0123456789", "sk-0123456789abcdefABCDEF"):
        assert validate_api_key(real) == real


# --- save / replace / delete -------------------------------------------------------------------------


def test_save_encrypts_and_replaces_in_place(keys_env, db_session, make_user):
    alice = make_user("alice")
    row = save(db_session, alice, PERSONAL)
    assert (row.owner_key, row.user_id, row.provider, row.created_by_id) == (f"user:{alice.id}", alice.id, "anthropic",
                                                                           alice.id)
    assert PERSONAL not in row.ciphertext and row.key_hint == "sk-ant-…1111"
    db_session.execute(update(ApiCredential).values(last_error="old failure"))
    db_session.commit()
    again = save(db_session, alice, PERSONAL.replace("1111", "9999"))
    assert again.id == row.id and again.key_hint == "sk-ant-…9999"
    assert again.last_error is None and again.last_verified_at is None  # a new key starts untested
    assert db_session.execute(select(ApiCredential)).scalars().all() == [again]


def test_a_test_result_never_lands_on_a_key_replaced_meanwhile(keys_env, db_session, make_user):
    alice = make_user("alice")
    row = save(db_session, alice, PERSONAL)
    tested_ciphertext = row.ciphertext
    replaced = save(db_session, alice, PERSONAL.replace("1111", "9999"))  # saved while the test ran
    assert replaced.id == row.id
    record_verification(db_session, row.id, False, "invalid x-api-key", ciphertext=tested_ciphertext)
    db_session.commit()
    db_session.refresh(replaced)
    assert replaced.last_error is None and replaced.last_verified_at is None  # the new key stays untested
    record_verification(db_session, row.id, True, "ok", ciphertext=replaced.ciphertext)
    db_session.commit()
    db_session.refresh(replaced)
    assert replaced.last_verified_at is not None


def test_server_and_personal_scopes_are_separate_rows(keys_env, db_session, make_user):
    admin = make_user("root", role="admin")
    save(db_session, admin, SERVER, scope="server")
    save(db_session, admin, PERSONAL)
    rows = {r.owner_key: r for r in db_session.execute(select(ApiCredential)).scalars()}
    assert set(rows) == {"server", f"user:{admin.id}"}
    assert rows["server"].user_id is None and rows["server"].created_by_id == admin.id
    assert delete_credential(db_session, provider="anthropic", scope="server", user=admin) is True
    assert delete_credential(db_session, provider="anthropic", scope="server", user=admin) is False
    db_session.commit()
    assert [r.owner_key for r in db_session.execute(select(ApiCredential)).scalars()] == [f"user:{admin.id}"]


def test_concurrent_insert_race_converges_on_one_row(keys_env, db_session, make_user, monkeypatch):
    from aadhi.db import get_sessionmaker

    alice = make_user("alice")
    real_execute = db_session.execute
    calls = {"n": 0}

    def racing_execute(stmt, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # our existence check runs just before another request commits its insert
            with get_sessionmaker()() as other:
                save(other, alice, SERVER)
            return real_execute(select(ApiCredential).where(false()))
        return real_execute(stmt, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", racing_execute)
    row = save_credential(db_session, provider="anthropic", api_key=PERSONAL, scope="user", user=alice)
    db_session.commit()
    monkeypatch.undo()
    rows = db_session.execute(select(ApiCredential)).scalars().all()
    assert len(rows) == 1 and rows[0].id == row.id and rows[0].key_hint == "sk-ant-…1111"  # the later save wins


def test_view_never_contains_the_key_or_ciphertext(keys_env, db_session, make_user):
    alice = make_user("alice")
    row = save(db_session, alice, PERSONAL)
    view = credential_view(row, keys_env)
    assert set(view) == {"hint", "updated_at", "last_verified_at", "last_error", "readable"}
    assert view["readable"] is True and view["hint"] == "sk-ant-…1111"
    assert PERSONAL not in str(view) and row.ciphertext not in str(view)


def test_saved_keys_are_registered_for_redaction(keys_env, db_session, make_user):
    from aadhi.config import get_settings

    save(db_session, make_user("alice"), PERSONAL)
    assert get_settings().redact(f"401 invalid x-api-key {PERSONAL}") == "401 invalid x-api-key [REDACTED]"


def test_redaction_slots_follow_saved_keys(keys_env, db_session, make_user):
    """A saved key is registered under its owner slot: replacing it swaps the entry and deleting it
    frees the slot, so one user's key churn never grows the shared list or evicts another's key."""
    alice, bob = make_user("alice"), make_user("bob")
    save(db_session, bob, SERVER)
    keys = [PERSONAL.replace("1111", f"{i:04d}") for i in range(10)]
    for key in keys:
        save(db_session, alice, key)
    registered = registered_secrets()
    assert keys[-1] in registered and not any(k in registered for k in keys[:-1])
    assert SERVER in registered and len(registered) == 2
    assert delete_credential(db_session, provider="anthropic", scope="user", user=alice) is True
    db_session.commit()
    assert registered_secrets() == (SERVER,)


# --- resolution --------------------------------------------------------------------------------------


def test_disabled_or_empty_returns_the_same_settings_object(app_env, db_session, make_user):
    alice = make_user("alice")
    app_env.anthropic_api_key = SecretStr(ENV)
    save(db_session, alice, PERSONAL, settings=app_env)  # saved while the feature is off (direct call)
    keys = resolve_keys(db_session, app_env, alice.id)
    assert keys.settings is app_env and keys.sources == {"gemini": None, "openai": None, "anthropic": "env"}
    app_env.stored_api_keys_enabled = True
    bob = make_user("bob")
    keys = resolve_keys(db_session, app_env, bob.id)  # no row applies to bob
    assert keys.settings is app_env and keys.sources["anthropic"] == "env"


def test_precedence_personal_then_server_then_env(keys_env, db_session, make_user):
    keys_env.anthropic_api_key = SecretStr(ENV)
    alice, bob, admin = make_user("alice"), make_user("bob"), make_user("root", role="admin")
    save(db_session, alice, PERSONAL)
    save(db_session, admin, SERVER, scope="server")

    a = resolve_keys(db_session, keys_env, alice.id)
    b = resolve_keys(db_session, keys_env, bob.id)
    none = resolve_keys(db_session, keys_env, None)
    assert a.settings.anthropic_api_key.get_secret_value() == PERSONAL and a.source("anthropic") == "personal"
    assert b.settings.anthropic_api_key.get_secret_value() == SERVER and b.source("anthropic") == "server"
    assert none.settings.anthropic_api_key.get_secret_value() == SERVER and none.source("anthropic") == "server"
    assert keys_env.anthropic_api_key.get_secret_value() == ENV  # the base settings are never changed

    delete_credential(db_session, provider="anthropic", scope="server", user=admin)
    db_session.commit()
    b = resolve_keys(db_session, keys_env, bob.id)
    assert b.settings is keys_env and b.source("anthropic") == "env"

    keys_env.user_api_keys_enabled = False  # personal keys switched off: alice is back on the server's key
    a = resolve_keys(db_session, keys_env, alice.id)
    assert a.settings.anthropic_api_key.get_secret_value() == ENV and a.source("anthropic") == "env"


def test_personal_gemini_key_replaces_the_whole_server_pool(keys_env, db_session, make_user):
    keys_env.gemini_api_key = SecretStr("AIzaServerPoolKeyOne-0000000000")
    keys_env.gemini_api_keys = [SecretStr("AIzaServerPoolKeyTwo-0000000000")]
    alice = make_user("alice")
    save(db_session, alice, GEMINI_PERSONAL, provider="gemini")
    keys = resolve_keys(db_session, keys_env, alice.id)
    assert keys.settings.all_gemini_keys == [GEMINI_PERSONAL]  # never mixed with the server's keys
    assert keys.sources == {"gemini": "personal", "openai": None, "anthropic": None}


def test_resolution_is_cached_by_identity_and_follows_changes(keys_env, db_session, make_user):
    from aadhi.providers.factory import get_llm

    alice = make_user("alice")
    save(db_session, alice, PERSONAL)
    first = resolve_keys(db_session, keys_env, alice.id)
    second = resolve_keys(db_session, keys_env, alice.id)
    assert first.settings is second.settings and first.settings is not keys_env
    assert get_llm(first.settings, "anthropic") is get_llm(second.settings, "anthropic")  # provider cache reused

    save(db_session, alice, PERSONAL.replace("1111", "7777"))
    third = resolve_keys(db_session, keys_env, alice.id)
    assert third.settings is not first.settings
    assert third.settings.anthropic_api_key.get_secret_value().endswith("7777")
    assert get_llm(third.settings, "anthropic") is not get_llm(first.settings, "anthropic")
    other = keys_env.model_copy()  # another settings object never shares a cached resolution
    assert resolve_keys(db_session, other, alice.id).settings is not third.settings

    keys_env.image_provider = "gemini"  # a changed base field is never served from a stale copy
    fourth = resolve_keys(db_session, keys_env, alice.id)
    assert fourth.settings is not third.settings and fourth.settings.image_provider == "gemini"
    assert resolve_keys(db_session, keys_env, alice.id).settings is fourth.settings


def test_unreadable_ciphertext_is_skipped_and_logged_once(keys_env, db_session, make_user, caplog):
    keys_env.anthropic_api_key = SecretStr(ENV)
    alice, admin = make_user("alice"), make_user("root", role="admin")
    personal = save(db_session, alice, PERSONAL)
    save(db_session, admin, SERVER, scope="server")
    db_session.execute(
        update(ApiCredential).where(ApiCredential.id == personal.id).values(ciphertext="gAAAAA-garbage-token")
    )
    db_session.commit()
    with caplog.at_level(logging.WARNING, logger="aadhi.credentials"):
        keys = resolve_keys(db_session, keys_env, alice.id)
        resolve_keys(db_session, keys_env, alice.id)
    assert keys.source("anthropic") == "server"  # skipped, never a crash
    warnings = [r for r in caplog.records if "cannot be decrypted" in r.getMessage()]
    assert len(warnings) == 1 and PERSONAL not in caplog.text
    db_session.refresh(personal)
    assert credential_view(personal, keys_env)["readable"] is False


def test_rotated_jwt_secret_marks_keys_unreadable(keys_env, db_session, make_user):
    alice = make_user("alice")
    row = save(db_session, alice, PERSONAL)
    rotated = keys_env.model_copy(update={"jwt_secret": SecretStr("rotated-jwt-secret-" + "z" * 40)})
    keys = resolve_keys(db_session, rotated, alice.id)
    assert keys.settings is rotated and keys.source("anthropic") is None
    assert credential_view(row, rotated)["readable"] is False


# --- billing & job errors ----------------------------------------------------------------------------


def test_billed_to_follows_the_paying_key():
    sources = {"gemini": "personal", "openai": "server", "anthropic": "env"}
    assert billed_to(sources, "gemini") == "user"
    assert billed_to(sources, "veo") == "user"  # Veo runs on the Gemini key
    assert billed_to(sources, "openai") == "server"
    assert billed_to(sources, "anthropic") == "server"
    for free in ("edge", "pollinations", "fake", "", "unknown"):
        assert billed_to(sources, free) == "server"


def test_personal_key_rejection_message():
    sources = {"anthropic": "personal", "openai": "server"}
    try:
        try:
            raise ProviderError("anthropic: failed (HTTP 401): invalid x-api-key", status=401, provider="anthropic")
        except ProviderError as inner:
            raise FatalJobError("AI provider error: ...", code="provider") from inner
    except FatalJobError as exc:
        message = personal_key_rejection(exc, sources)
    assert message and "personal Anthropic Claude API key was rejected (HTTP 401)" in message
    assert personal_key_rejection(ProviderError("x", status=401, provider="openai"), sources) is None  # server key
    assert personal_key_rejection(ProviderError("x", status=500, provider="anthropic"), sources) is None
    assert personal_key_rejection(RuntimeError("boom"), sources) is None


def test_gemini_invalid_key_400_is_a_rejection():
    """Gemini answers a wrong or expired key with HTTP 400 INVALID_ARGUMENT, not 401/403."""
    bad_key = ProviderError("gemini: plan failed (HTTP 400, generativelanguage.googleapis.com): INVALID_ARGUMENT "
                            "API key not valid. Please pass a valid API key.", status=400, provider="gemini")
    try:
        try:
            raise bad_key
        except ProviderError as inner:
            raise FatalJobError("AI provider error: ...", code="provider") from inner
    except FatalJobError as exc:
        message = personal_key_rejection(exc, {"gemini": "personal"})
        assert personal_key_rejection(exc, {"gemini": "server"}) is None
    assert message and "personal Google Gemini API key was rejected (HTTP 400)" in message
    expired = ProviderError("gemini: failed (HTTP 400): INVALID_ARGUMENT API key expired. Please renew the API key.",
                            status=400, provider="gemini")
    assert personal_key_rejection(expired, {"gemini": "personal"})
    other_400 = ProviderError("gemini: failed (HTTP 400): INVALID_ARGUMENT Request contains an invalid argument.",
                              status=400, provider="gemini")
    assert personal_key_rejection(other_400, {"gemini": "personal"}) is None
    openai_400 = ProviderError("openai: failed (HTTP 400): API key not valid", status=400, provider="openai")
    assert personal_key_rejection(openai_400, {"openai": "personal"}) is None  # only Gemini uses 400 for keys


# --- verification (no network) -----------------------------------------------------------------------


class _StatusError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (_StatusError(f"invalid x-api-key {PERSONAL}", 401), "rejected the key (HTTP 401)"),
        (_StatusError("forbidden", 403), "rejected the key (HTTP 403)"),
        (_StatusError("slow down", 429), "HTTP 429"),
        (_StatusError(f"bad request for {PERSONAL}", 400), "returned HTTP 400"),
        (TimeoutError(), "did not answer"),
        (ConnectionError(f"cannot connect with {PERSONAL}"), "ConnectionError"),
    ],
)
def test_verify_key_failures_are_redacted_messages(app_env, monkeypatch, exc, expected):
    async def failing(provider, api_key, timeout):
        raise exc

    monkeypatch.setattr(creds, "_list_models", failing)
    ok, message = asyncio.run(creds.verify_key("anthropic", PERSONAL, app_env))
    assert ok is False and expected in message
    assert PERSONAL not in message and len(message) <= creds.MAX_ERROR_CHARS
    assert PERSONAL not in registered_secrets()  # scrubbed locally, never added to the shared list


def test_verify_key_reports_geminis_invalid_key_400_as_a_rejection(app_env, monkeypatch):
    async def failing(provider, api_key, timeout):
        raise _StatusError("API key not valid. Please pass a valid API key.", 400)

    monkeypatch.setattr(creds, "_list_models", failing)
    ok, message = asyncio.run(creds.verify_key("gemini", GEMINI_PERSONAL, app_env))
    assert ok is False and "Google Gemini rejected the key (HTTP 400)" in message


def test_verify_key_success(app_env, monkeypatch):
    seen = []

    async def fine(provider, api_key, timeout):
        seen.append((provider, api_key, timeout))

    monkeypatch.setattr(creds, "_list_models", fine)
    assert asyncio.run(creds.verify_key("openai", "sk-proj-verify-0123456789", app_env)) == (
        True, "OpenAI accepted the key.")
    assert seen == [("openai", "sk-proj-verify-0123456789", creds.VERIFY_TIMEOUT_SECONDS)]


def test_list_models_uses_the_official_sdks_cheaply(monkeypatch):
    """The SDK calls a key test makes, with fake clients (nothing leaves the process)."""
    import anthropic
    import openai
    from google import genai

    calls: list[tuple] = []

    class _Models:
        def __init__(self, name):
            self.name = name

        async def list(self, **kwargs):
            calls.append((self.name, "list", kwargs))
            return []

    class FakeAnthropic:
        def __init__(self, *, api_key, timeout, max_retries):
            calls.append(("anthropic", "init", api_key, max_retries))
            self.models = _Models("anthropic")

        async def close(self):
            calls.append(("anthropic", "close"))

    class FakeOpenAI(FakeAnthropic):
        def __init__(self, *, api_key, timeout, max_retries):
            calls.append(("openai", "init", api_key, max_retries))
            self.models = _Models("openai")

        async def close(self):
            calls.append(("openai", "close"))

    class FakeGenai:
        def __init__(self, *, api_key, http_options):
            calls.append(("gemini", "init", api_key, http_options.timeout))

            async def aclose():
                calls.append(("gemini", "close"))

            self.aio = SimpleNamespace(models=_Models("gemini"), aclose=aclose)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", FakeAnthropic)
    monkeypatch.setattr(openai, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr(genai, "Client", FakeGenai)
    for provider in ("anthropic", "openai", "gemini"):
        asyncio.run(creds._list_models(provider, f"{provider}-key-0123456789", 15.0))
    assert calls == [
        ("anthropic", "init", "anthropic-key-0123456789", 0),
        ("anthropic", "list", {"limit": 1}),
        ("anthropic", "close"),
        ("openai", "init", "openai-key-0123456789", 0),
        ("openai", "list", {}),
        ("openai", "close"),
        ("gemini", "init", "gemini-key-0123456789", 15000),
        ("gemini", "list", {"config": {"page_size": 1}}),
        ("gemini", "close"),
    ]
