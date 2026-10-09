"""Password hashing and strength rules."""

from __future__ import annotations

import bcrypt
import pytest

from aadhi.auth import passwords
from aadhi.auth.passwords import check_password_strength, hash_password, needs_rehash, verify_password

# A $2b$ hash in the exact format v1 (passlib bcrypt, 12 rounds) stored, generated once with bcrypt.
V1_PASSWORD = "Legacy-Teacher-Pass-42"
V1_HASH = bcrypt.hashpw(V1_PASSWORD.encode(), bcrypt.gensalt(rounds=12, prefix=b"2b")).decode()


def test_hash_and_verify_roundtrip(app_env):
    h = hash_password("Correct-Horse-Battery-9")
    assert h.startswith("$2b$04$")  # test rounds
    assert verify_password("Correct-Horse-Battery-9", h)
    assert not verify_password("correct-horse-battery-9", h)
    assert hash_password("Correct-Horse-Battery-9") != h  # salted


def test_v1_passlib_hash_verifies(app_env):
    assert V1_HASH.startswith("$2b$12$")
    assert verify_password(V1_PASSWORD, V1_HASH)
    assert not verify_password(V1_PASSWORD + "x", V1_HASH)
    assert needs_rehash(V1_HASH) is False or passwords._rounds() > 12


def test_2a_and_2y_prefixes_verify(app_env):
    for prefix in (b"2a", b"2b"):
        h = bcrypt.hashpw(b"Some-Pass-123", bcrypt.gensalt(rounds=4, prefix=prefix)).decode()
        assert verify_password("Some-Pass-123", h)
    h2y = "$2y$" + bcrypt.hashpw(b"Some-Pass-123", bcrypt.gensalt(rounds=4)).decode()[4:]
    assert verify_password("Some-Pass-123", h2y)


def test_long_passwords_rejected_not_truncated(app_env):
    base = "a1B2c3D4e5" * 7 + "xy"  # 72 bytes
    assert len(base.encode()) == 72
    h = hash_password(base)
    assert verify_password(base, h)
    with pytest.raises(ValueError):
        hash_password(base + "z")
    # bcrypt would ignore byte 73+; we refuse instead of silently truncating.
    assert verify_password(base + "z", h) is False
    assert any("72 bytes" in p for p in check_password_strength(base + "z", "bob", app_env))
    multibyte = "ப" * 25  # 75 bytes in UTF-8
    assert any("72 bytes" in p for p in check_password_strength(multibyte, "bob", app_env))


def test_empty_and_malformed_hashes_are_false_but_still_hash(app_env, monkeypatch):
    calls = []
    real = bcrypt.checkpw

    def spy(pw, hashed):
        calls.append(hashed)
        return real(pw, hashed)

    monkeypatch.setattr(passwords.bcrypt, "checkpw", spy)
    for bad in (None, "", "plaintext", "$2b$04$short", "!legacy-unusable"):
        assert verify_password("whatever-password", bad) is False
    assert verify_password("", hash_password("Real-Password-1")) is False
    # Every call did one bcrypt comparison (dummy hash) -> unknown users cost the same as known ones.
    assert len(calls) == 6
    assert all(c == passwords._dummy_hash(passwords._rounds()) for c in calls)


def test_hash_password_rejects_empty(app_env):
    with pytest.raises(ValueError):
        hash_password("")


def test_needs_rehash(app_env, monkeypatch):
    weak = bcrypt.hashpw(b"x-password-1", bcrypt.gensalt(rounds=4)).decode()
    monkeypatch.setattr(passwords, "_rounds", lambda: 12)
    assert needs_rehash(weak)
    assert needs_rehash("garbage")
    assert not needs_rehash(V1_HASH)


@pytest.mark.parametrize(
    ("password", "fragment"),
    [
        ("short1!", "at least"),
        ("aaaaaaaaaaaaaaaa", "5 different"),
        ("alice-is-great-2024", "username"),
        ("Password@2025", "too common"),
        ("1234567890", "too common"),
        ("Rajalakshmi#123", "too common"),
        ("tab\there-and-more", "control"),
    ],
)
def test_strength_problems(app_env, password, fragment):
    problems = check_password_strength(password, "alice", app_env)
    assert any(fragment in p for p in problems), problems


@pytest.mark.parametrize("password", ["correct horse battery staple", "Tr0ub4dor&3-xyz", "மழை-நீர்-சேமிப்பு-2"])
def test_strength_ok(app_env, password):
    assert check_password_strength(password, "alice", app_env) == []


def test_min_length_setting_respected(app_env):
    s = app_env.model_copy(update={"password_min_length": 16})
    assert any("16" in p for p in check_password_strength("Gr8-pass-phrase", "bob", s))
    assert check_password_strength("Gr8-pass-phrase-long", "bob", s) == []
