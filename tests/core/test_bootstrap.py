"""ensure_admin bootstrap."""

from __future__ import annotations

import logging
import os
import sys

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from aadhi.auth.bootstrap import INITIAL_PASSWORD_FILE, ensure_admin
from aadhi.auth.passwords import verify_password
from aadhi.models import User


def _admins(db):
    return db.execute(select(User).where(User.role == "admin")).scalars().all()


def test_env_password_strong(app_env, db_session):
    s = app_env.model_copy(update={"admin_password": SecretStr("Strong-Bootstrap-Phrase-91")})
    ensure_admin(db_session, s)
    (admin,) = _admins(db_session)
    assert admin.username == "admin" and admin.must_change_password is False
    assert verify_password("Strong-Bootstrap-Phrase-91", admin.password_hash)
    assert not (s.data_dir / INITIAL_PASSWORD_FILE).exists()


def test_env_password_weak_in_dev_forces_change(app_env, db_session, caplog):
    s = app_env.model_copy(update={"admin_password": SecretStr("admin12345")})
    with caplog.at_level(logging.WARNING, logger="aadhi.auth.bootstrap"):
        ensure_admin(db_session, s)
    (admin,) = _admins(db_session)
    assert admin.must_change_password is True
    assert "admin12345" not in caplog.text


def test_weak_env_password_refused_in_production(app_env, db_session):
    s = app_env.model_copy(update={"admin_password": SecretStr("admin12345"), "app_env": "production"})
    with pytest.raises(RuntimeError):
        ensure_admin(db_session, s)
    assert _admins(db_session) == []


def test_generated_one_time_password(app_env, db_session, caplog):
    s = app_env.model_copy(update={"admin_password": SecretStr(""), "admin_username": "principal"})
    with caplog.at_level(logging.WARNING, logger="aadhi.auth.bootstrap"):
        ensure_admin(db_session, s)
    (admin,) = _admins(db_session)
    assert admin.username == "principal" and admin.must_change_password is True
    path = s.data_dir / INITIAL_PASSWORD_FILE
    password = path.read_text(encoding="utf-8").strip()
    assert len(password) >= 20 and verify_password(password, admin.password_hash)
    assert str(path) in caplog.text and password not in caplog.text
    if sys.platform != "win32":
        assert (os.stat(path).st_mode & 0o777) == 0o600
    assert not list(s.data_dir.glob(".initial_admin_password*.tmp"))


def test_production_requires_env_password(app_env, db_session):
    s = app_env.model_copy(update={"admin_password": SecretStr(""), "app_env": "production"})
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD"):
        ensure_admin(db_session, s)


def test_noop_when_admin_exists(app_env, db_session, make_user):
    make_user("boss", role="admin")
    ensure_admin(db_session, app_env)
    assert [u.username for u in _admins(db_session)] == ["boss"]
    ensure_admin(db_session, app_env)  # idempotent
    assert len(_admins(db_session)) == 1


def test_username_taken_by_editor(app_env, db_session, make_user, caplog):
    make_user("admin", role="editor")
    with caplog.at_level(logging.WARNING, logger="aadhi.auth.bootstrap"):
        ensure_admin(db_session, app_env)
    assert _admins(db_session) == []
    assert "create-admin" in caplog.text


@pytest.mark.parametrize("error", ["race", "other"])
def test_commit_failure_leaves_no_password_file(app_env, db_session, monkeypatch, error):
    from sqlalchemy.exc import IntegrityError

    s = app_env.model_copy(update={"admin_password": SecretStr("")})

    def boom():
        if error == "race":
            raise IntegrityError("INSERT", {}, Exception("unique"))
        raise RuntimeError("db down")

    monkeypatch.setattr(db_session, "commit", boom)
    if error == "race":
        ensure_admin(db_session, s)
    else:
        with pytest.raises(RuntimeError):
            ensure_admin(db_session, s)
    assert not list(s.data_dir.glob("*initial_admin_password*"))
