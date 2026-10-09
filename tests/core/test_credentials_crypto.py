"""Encryption of saved API keys and the process-wide redaction registry."""

from __future__ import annotations

import io
import logging

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr, ValidationError

from aadhi.config import Settings, get_settings
from aadhi.logging_setup import RedactingFilter, SingleLineFormatter
from aadhi.security.credentials_crypto import CredentialUnreadable, decrypt_secret, encrypt_secret
from aadhi.security.redaction import (
    MAX_REGISTERED,
    clear_registered_secrets,
    forget_secret,
    register_secret,
    registered_secrets,
)

SECRET = "sk-ant-api03-crypto-test-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_registered_secrets()
    yield
    clear_registered_secrets()


def test_round_trip_with_the_jwt_derived_key(app_env):
    token = encrypt_secret(SECRET, app_env)
    assert SECRET not in token and token != encrypt_secret(SECRET, app_env)  # random IV
    assert decrypt_secret(token, app_env) == SECRET


def test_rotating_the_jwt_secret_makes_tokens_unreadable(app_env):
    token = encrypt_secret(SECRET, app_env)
    rotated = app_env.model_copy(update={"jwt_secret": SecretStr("another-jwt-secret-" + "y" * 40)})
    with pytest.raises(CredentialUnreadable):
        decrypt_secret(token, rotated)


def test_tampered_or_garbage_tokens_are_unreadable(app_env):
    token = encrypt_secret(SECRET, app_env)
    flipped = token[:-6] + ("A" if token[-6] != "A" else "B") + token[-5:]
    for bad in (flipped, "not-a-token", "", "ünïcode"):
        with pytest.raises(CredentialUnreadable):
            decrypt_secret(bad, app_env)


def test_explicit_key_is_used_and_still_reads_jwt_derived_tokens(app_env):
    legacy = encrypt_secret(SECRET, app_env)  # written before CREDENTIALS_ENCRYPTION_KEY was set
    explicit = app_env.model_copy(update={"credentials_encryption_key": SecretStr(Fernet.generate_key().decode())})
    fresh = encrypt_secret(SECRET, explicit)
    assert decrypt_secret(fresh, explicit) == SECRET
    assert decrypt_secret(legacy, explicit) == SECRET
    with pytest.raises(CredentialUnreadable):
        decrypt_secret(fresh, app_env)  # needs the explicit key


def test_invalid_explicit_key_is_refused_without_echoing_it(monkeypatch):
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", "definitely-not-a-fernet-key")
    with pytest.raises(ValidationError) as info:
        Settings()
    assert "Fernet key" in str(info.value)
    assert "definitely-not-a-fernet-key" not in str(info.value)  # Settings hides inputs in its errors
    monkeypatch.setenv("CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    assert Settings().credentials_encryption_key.get_secret_value()


# --- redaction registry ------------------------------------------------------------------------------


def test_registered_secrets_are_redacted_by_every_settings_instance(app_env):
    # Vendor-shaped keys (sk-..., AIza...) are masked even unregistered (Settings.redact patterns), so the
    # registry is shown with a value only the registry knows.
    plain = "crypto-test-0123456789abcdef"
    assert plain in app_env.redact(f"boom {plain}")
    register_secret(plain)
    for settings in (app_env, get_settings(), app_env.model_copy()):
        assert settings.redact(f"boom {plain} end") == "boom [REDACTED] end"
        assert plain in settings.secret_values()
    register_secret(SECRET)
    assert SECRET in app_env.secret_values() and app_env.redact(f"boom {SECRET} end") == "boom [REDACTED] end"


def test_job_util_redaction_uses_the_registry(app_env):
    from aadhi.jobs.util import redact_obj, redact_text, short_error

    register_secret(SECRET)
    assert SECRET not in redact_text(f"provider said {SECRET}")
    assert SECRET not in short_error(RuntimeError(f"401 for key {SECRET}"))
    assert SECRET not in str(redact_obj({"detail": [f"x{SECRET}y"]}))


def test_registry_ignores_short_values_and_is_bounded():
    register_secret("")
    register_secret(None)
    register_secret("abc")
    assert registered_secrets() == ()
    for i in range(MAX_REGISTERED + 5):
        register_secret(f"secret-value-{i:06d}")
    assert len(registered_secrets()) == MAX_REGISTERED
    assert "secret-value-000000" not in registered_secrets()  # oldest dropped first


def test_owned_values_replace_their_slot_and_are_never_evicted():
    """A slot ("user:3:openai") holds one value: re-registering swaps it, forgetting frees it, and no
    amount of other registrations can push it out of the list."""
    for i in range(10):
        register_secret(f"owned-secret-value-{i:02d}", owner="user:3:openai")
    assert [s for s in registered_secrets() if s.startswith("owned-")] == ["owned-secret-value-09"]
    register_secret("other-users-secret-0001", owner="user:4:openai")
    register_secret("other-users-secret-0001", owner="user:4:openai")  # unchanged: no-op
    for i in range(MAX_REGISTERED + 5):
        register_secret(f"junk-value-{i:06d}")
    registered = registered_secrets()
    assert "owned-secret-value-09" in registered and "other-users-secret-0001" in registered
    assert len(registered) == MAX_REGISTERED + 2
    forget_secret("user:3:openai")
    forget_secret("user:3:openai")  # an empty slot: no-op
    assert "owned-secret-value-09" not in registered_secrets()
    assert "other-users-secret-0001" in registered_secrets()


def test_longer_secret_wins_over_a_contained_shorter_one(app_env):
    register_secret("sk-ant-abcdef")
    register_secret("sk-ant-abcdef-longer-tail-123")
    assert app_env.redact("k=sk-ant-abcdef-longer-tail-123!") == "k=[REDACTED]!"


def test_logging_filter_redacts_secrets_registered_after_configuration():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(SingleLineFormatter(json_lines=False))
    handler.addFilter(RedactingFilter([]))  # configured before the key existed
    logger = logging.getLogger("aadhi.test.redaction")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        register_secret(SECRET)
        logger.warning("provider rejected %s", SECRET)
    finally:
        logger.removeHandler(handler)
    assert SECRET not in stream.getvalue() and "[REDACTED]" in stream.getvalue()
