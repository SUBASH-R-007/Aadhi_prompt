"""Account operations shared by the API and the CLI (atomic, no read-modify-write races)."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import User, utcnow
from .passwords import hash_password, needs_rehash, verify_password

__all__ = ["authenticate", "bump_token_version", "find_user", "set_password"]


def find_user(db: Session, username: str) -> User | None:
    """Exact username lookup (usernames are stored as entered; matching is exact)."""
    if not username or len(username) > 64:
        return None
    return db.execute(select(User).where(User.username == username)).scalar_one_or_none()


def authenticate(db: Session, username: str, password: str) -> User | None:
    """Return the active user for valid credentials, else None.

    Always performs exactly one bcrypt comparison (against a dummy hash for unknown users), so the
    response time does not reveal whether the username exists. Updates ``last_login_at`` and
    transparently upgrades weak hashes (caller commits).
    """
    user = find_user(db, username)
    ok = verify_password(password, user.password_hash if user is not None else None)
    if user is None or not ok or not user.is_active:
        return None
    values: dict[str, object] = {"last_login_at": utcnow()}
    if needs_rehash(user.password_hash):
        values["password_hash"] = hash_password(password)
    db.execute(update(User).where(User.id == user.id).values(**values).execution_options(synchronize_session=False))
    db.refresh(user)
    return user


def bump_token_version(db: Session, user_id: int) -> int | None:
    """Atomically increment ``token_version`` (revokes every session of the user). Caller commits."""
    row = db.execute(
        update(User)
        .where(User.id == user_id)
        .values(token_version=func.coalesce(User.token_version, 0) + 1, updated_at=utcnow())
        .returning(User.token_version)
    ).first()
    return None if row is None else int(row[0])


def set_password(db: Session, user_id: int, new_password: str, *, must_change: bool = False) -> int | None:
    """Store a new hash, set ``must_change_password`` and revoke all sessions in ONE statement.

    Returns the new ``token_version`` (None if the user does not exist). Caller commits and should
    re-issue the session cookie for the current user with the new token version.
    """
    row = db.execute(
        update(User)
        .where(User.id == user_id)
        .values(
            password_hash=hash_password(new_password),
            must_change_password=must_change,
            token_version=func.coalesce(User.token_version, 0) + 1,
            updated_at=utcnow(),
        )
        .returning(User.token_version)
    ).first()
    return None if row is None else int(row[0])
