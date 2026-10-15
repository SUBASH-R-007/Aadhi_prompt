"""Switches an administrator sets in the Studio, overriding the environment for every process.

The environment (``aadhi.config.Settings``) is read once per process and cannot be changed without a restart, so a
switch a teacher is meant to flip lives in the ``app_settings`` table instead (``models.AppSetting``). This module is
the only reader: it keeps a short per-process cache, so a flag may be consulted on any request or job without a query
each time, and every process notices a change within :data:`TTL_SECONDS`.

**Fail open to the environment.** A missing row, a database that is not configured yet (imports, a classic build, a
unit test with no database), or any database error means "no override": the caller gets the environment's value, which
is exactly the behaviour before this table existed. A switch must never be the reason something fails to run.

Today there is one flag, ``dynamic_enabled`` (the Dynamic video mode, design §6.7, §10.3 B2): with it on, a newly
generated lecture is written with a ``Screenplay.presentation`` (``compose.dynamic.authoring``) and built, served and
rendered as a Dynamic video; with it off everything is classic. ``compose.presentation.dynamic_enabled`` is the single
place that merges this override with the environment, so every caller — the API, the job workers and the compose
layer — sees the same answer.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

#: The flags an administrator may set, with the ``Settings`` field each one overrides (same name) and its type.
FLAGS: dict[str, type] = {"dynamic_enabled": bool}

#: How long a process may serve a cached value. Short enough that a teacher's switch takes effect while they watch,
#: long enough that a busy render worker does not query per frame.
TTL_SECONDS = 5.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "flags": {}}


def _read() -> dict[str, Any]:
    """Every stored flag, or ``{}`` when there is no database to read (see the module docstring)."""
    try:
        from sqlalchemy import select

        from .db import session_scope
        from .models import AppSetting

        with session_scope() as db:
            rows = db.execute(select(AppSetting.key, AppSetting.value)).all()
    except Exception as exc:  # no database, no table yet, or it is unreachable: the environment rules
        log.debug("runtime flags unavailable, using the environment: %s", exc)
        return {}
    return {key: value for key, value in rows if key in FLAGS}


def flags(*, refresh: bool = False) -> dict[str, Any]:
    """Every stored flag, from the per-process cache (refreshed at most every :data:`TTL_SECONDS`)."""
    now = time.monotonic()
    with _lock:
        fresh = not refresh and _cache["flags"] is not None and (now - float(_cache["at"])) < TTL_SECONDS
        if fresh and _cache["at"]:
            return dict(_cache["flags"])
    loaded = _read()
    with _lock:
        _cache["at"], _cache["flags"] = time.monotonic(), loaded
    return dict(loaded)


def get(name: str) -> Any | None:
    """The stored value of one flag, or ``None`` when an administrator has not set it."""
    if name not in FLAGS:
        raise KeyError(name)
    value = flags().get(name)
    return None if value is None else FLAGS[name](value)


def set_flag(db: Any, name: str, value: Any, *, user_id: int | None = None) -> Any:
    """Store one flag and drop every process's cache sooner by invalidating this one.

    The caller commits. Other processes pick the new value up within :data:`TTL_SECONDS`; this process sees it at
    once, so the administrator's own next request already reflects what they switched.
    """
    if name not in FLAGS:
        raise KeyError(name)
    typed = FLAGS[name](value)

    from .models import AppSetting

    row = db.get(AppSetting, name)
    if row is None:
        db.add(AppSetting(key=name, value=typed, updated_by=user_id))
    else:
        row.value, row.updated_by = typed, user_id
    invalidate()
    return typed


def invalidate() -> None:
    """Forget the cache, so the next read goes to the database (after a write, and in tests)."""
    with _lock:
        _cache["at"], _cache["flags"] = 0.0, {}
