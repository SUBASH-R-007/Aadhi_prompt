"""Fake aadhi.auth.bootstrap."""

from __future__ import annotations

from sqlalchemy import select

from aadhi.auth.passwords import hash_password
from aadhi.models import User


def ensure_admin(db, settings) -> None:
    if db.execute(select(User.id).where(User.role == "admin")).first() is not None:
        return
    password = settings.admin_password.get_secret_value() or "generated-admin-password-0"
    db.add(User(username=settings.admin_username, password_hash=hash_password(password), role="admin"))
    db.flush()
