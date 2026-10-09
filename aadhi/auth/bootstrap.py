"""First-run admin bootstrap.

``ensure_admin`` creates the initial administrator when no admin account exists:

* ``ADMIN_PASSWORD`` set -> that password (it must pass the strength rules in production; in
  development a weak one is accepted but the account must change it at first login).
* otherwise (development/test only) -> a random one-time password written to
  ``DATA_DIR/initial_admin_password.txt`` with mode 0600. Only the *path* is logged, never the
  password. The account must change the password at first login.
* production without ``ADMIN_PASSWORD`` -> ``RuntimeError`` (refuse to start with an unknown admin).
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings
from ..models import User
from .passwords import check_password_strength, hash_password

__all__ = ["INITIAL_PASSWORD_FILE", "ensure_admin"]

log = logging.getLogger(__name__)

INITIAL_PASSWORD_FILE = "initial_admin_password.txt"


def _write_secret_file(path: Path, value: str) -> None:
    """Write ``value`` to ``path`` readable by the owner only (0600; best effort on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(value + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover - platform specific
        pass


def _discard(path: Path | None) -> None:
    if path is not None and path.exists():
        path.unlink()


def ensure_admin(db: Session, settings: Settings) -> None:
    """Create the bootstrap admin if no ``admin`` account exists (commits on creation)."""
    if db.execute(select(User.id).where(User.role == "admin").limit(1)).first() is not None:
        return
    username = (settings.admin_username or "admin").strip() or "admin"
    if db.execute(select(User.id).where(User.username == username)).first() is not None:
        log.warning(
            "No admin account exists and username %r is taken by a non-admin account; "
            "create one with `python -m aadhi.cli create-admin --username <name>`",
            username,
        )
        return

    env_password = settings.admin_password.get_secret_value()
    password_file: Path | None = None
    if env_password:
        problems = check_password_strength(env_password, username, settings)
        if problems and settings.is_production:
            raise RuntimeError("ADMIN_PASSWORD does not meet the password rules: " + " ".join(problems))
        password = env_password
        must_change = bool(problems)
        if problems:
            log.warning("ADMIN_PASSWORD is weak (%s); the admin must change it at first login", "; ".join(problems))
    else:
        if settings.is_production:
            raise RuntimeError("ADMIN_PASSWORD must be set to bootstrap the first admin when APP_ENV=production")
        password = secrets.token_urlsafe(18)
        must_change = True
        password_file = settings.data_dir / INITIAL_PASSWORD_FILE

    user = User(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        is_active=True,
        must_change_password=must_change,
        token_version=0,
    )
    db.add(user)
    # Write the one-time password to a unique temp file BEFORE committing (never create an admin
    # whose password nobody knows), then move it into place only if this process won the insert.
    pending: Path | None = None
    if password_file is not None:
        pending = password_file.with_name(f".{password_file.name}.{secrets.token_hex(6)}.tmp")
        _write_secret_file(pending, password)
    try:
        db.commit()
    except IntegrityError:  # another process bootstrapped concurrently
        db.rollback()
        _discard(pending)
        log.info("admin bootstrap raced with another process; nothing to do")
        return
    except BaseException:
        db.rollback()
        _discard(pending)
        raise
    if password_file is not None and pending is not None:
        try:
            os.replace(pending, password_file)
        except OSError:  # pragma: no cover - keep the temp file; it still holds the password
            password_file = pending
        log.warning(
            "Created admin account %r with a one-time password stored in %s (change it at first login, "
            "then delete the file)",
            username,
            password_file,
        )
    else:
        log.info("Created admin account %r from ADMIN_PASSWORD", username)
