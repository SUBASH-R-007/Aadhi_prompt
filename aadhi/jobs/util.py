"""Small helpers shared by the job modules (time normalisation, strict JSON, redaction, DB errors)."""

from __future__ import annotations

import datetime as dt
import math
import traceback
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import exc as sa_exc

from ..config import get_settings

MAX_MESSAGE_CHARS = 500
MAX_EVENT_MESSAGE_CHARS = 4000
MAX_TRACEBACK_CHARS = 4000
_CYCLE = "<cycle>"


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """Return ``value`` as an aware UTC datetime (SQLite hands back naive UTC values)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    """ISO-8601 UTC string (``...+00:00``) or ``None``."""
    v = as_utc(value)
    return None if v is None else v.isoformat()


def truncate(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, marking the cut with an ellipsis."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def clean_text(text: str) -> str:
    """Strip NUL characters (Postgres rejects them in TEXT and JSONB values)."""
    return text.replace("\x00", "") if "\x00" in text else text


def finite_or_none(value: Any) -> float | None:
    """``float(value)`` when it is a finite number, else ``None``."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def jsonable(value: Any) -> Any:
    """Deep-copy ``value`` into strict JSON types that every database accepts.

    Dict keys become strings, tuples/sets become lists, non-finite floats (NaN, ±inf — which
    ``json.dumps`` would emit and Postgres JSONB rejects) become ``None``, NUL characters are
    stripped from strings, reference cycles become ``"<cycle>"`` and any other object its ``str()``.
    Never raises for ordinary data.
    """
    return _to_json(value, set())


def _json_key(key: Any) -> str:
    if key is None:
        return "null"
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, str):
        return clean_text(str.__str__(key))
    return clean_text(str(key))


def _to_json(value: Any, seen: set[int]) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return clean_text(str.__str__(value))
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        marker = id(value)
        if marker in seen:
            return _CYCLE
        seen.add(marker)
        try:
            if isinstance(value, Mapping):
                return {_json_key(k): _to_json(v, seen) for k, v in value.items()}
            items = sorted(value, key=repr) if isinstance(value, set | frozenset) else value
            return [_to_json(v, seen) for v in items]
        finally:
            seen.discard(marker)
    return clean_text(str(value))


def redact_obj(value: Any, redact: Callable[[str], str] | None = None) -> Any:
    """Recursively redact every string inside a JSON-like structure."""
    fn = redact or get_settings().redact
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {str(k): redact_obj(v, fn) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_obj(v, fn) for v in value]
    return value


def redact_text(
    text: str, limit: int = MAX_EVENT_MESSAGE_CHARS, redact: Callable[[str], str] | None = None
) -> str:
    """Redact secrets (``redact``, default ``get_settings().redact``), strip NULs and truncate."""
    fn = redact or get_settings().redact
    return truncate(clean_text(fn(str(text or ""))), limit)


def short_error(
    exc: BaseException, limit: int = MAX_MESSAGE_CHARS, redact: Callable[[str], str] | None = None
) -> str:
    """User-facing one-line description of an exception (redacted, truncated)."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    name = type(exc).__name__
    msg = f"{name}: {text}" if text else name
    return redact_text(msg, limit, redact)


def traceback_tail(
    exc: BaseException, limit: int = MAX_TRACEBACK_CHARS, redact: Callable[[str], str] | None = None
) -> str:
    """The last ``limit`` characters of the formatted traceback.

    Redacted (``redact``, default ``get_settings().redact``) *before* it is cut, so a secret
    straddling the cut can never leave a fragment behind.
    """
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    text = clean_text((redact or get_settings().redact)(text))
    if len(text) > limit:
        text = "…" + text[-(limit - 1) :]
    return text


# --- database error classification -------------------------------------------------------------

_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    sa_exc.OperationalError,  # connection dropped, server restarting, "database is locked", deadlock
    sa_exc.InterfaceError,
    sa_exc.DisconnectionError,
    sa_exc.TimeoutError,  # connection pool exhausted
    ConnectionError,
    TimeoutError,
)
_DATA_ERRORS: tuple[type[BaseException], ...] = (
    sa_exc.StatementError,  # DataError / IntegrityError / ProgrammingError / bind-parameter errors
    sa_exc.ArgumentError,
    sa_exc.CompileError,
    ValueError,  # e.g. a driver refusing NUL characters (UnicodeError included)
    TypeError,
)


def is_connection_error(exc: BaseException) -> bool:
    """True for errors that say nothing about the data written (connectivity, locks, pool)."""
    if isinstance(exc, _CONNECTION_ERRORS):
        return True
    return isinstance(exc, sa_exc.DBAPIError) and bool(exc.connection_invalidated)


def is_transient_db_error(exc: BaseException) -> bool:
    """Worth retrying? Connection-level errors and unknown errors are; data errors are not."""
    if is_connection_error(exc):
        return True
    return not isinstance(exc, _DATA_ERRORS)
