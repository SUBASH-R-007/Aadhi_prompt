"""Small pure helpers shared by the routers (time, hashing, file names, pagination)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from typing import Any

from .errors import ApiException

MAX_PAGE = 200


def utcnow() -> dt.datetime:
    """Timezone-aware now (UTC)."""
    return dt.datetime.now(dt.timezone.utc)


def as_utc(value: dt.datetime | None) -> dt.datetime | None:
    """SQLite returns naive datetimes for ``DateTime(timezone=True)``: treat them as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    """ISO-8601 UTC string (``...Z``) or None."""
    v = as_utc(value)
    if v is None:
        return None
    return v.isoformat().replace("+00:00", "Z")


def start_of_utc_day(now: dt.datetime | None = None) -> dt.datetime:
    """Midnight (UTC) of ``now``'s day."""
    now = as_utc(now) or utcnow()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def canonical_json(data: Any) -> str:
    """Stable JSON used for hashing (sorted keys, compact)."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: Any) -> str:
    """sha256 of the canonical JSON of ``data`` (or of raw bytes/str)."""
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonical_json(data).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_UNSAFE_FILENAME = re.compile(r"[^\w\s.,()\-+&']+", re.UNICODE)


def download_name(title: str, ext: str, *, fallback: str = "lecture", suffix: str = "") -> str:
    """Safe download file name derived from a project title (no quotes, separators or controls)."""
    base = unicodedata.normalize("NFC", title or "")
    base = "".join(" " if unicodedata.category(ch)[0] == "C" else ch for ch in base)
    base = _UNSAFE_FILENAME.sub(" ", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    if not base:
        base = fallback
    base = base[:120].rstrip(" .")
    return f"{base}{suffix}.{ext.lstrip('.')}"


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """RFC 6266 header with an ASCII fallback and a UTF-8 ``filename*``."""
    from urllib.parse import quote

    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r'["\\]', "", ascii_name).strip() or "download"
    if ascii_name == filename:
        return f'{disposition}; filename="{ascii_name}"'
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


def page_params(limit: int, offset: int) -> tuple[int, int]:
    """Validate list pagination (``limit`` 1..200, ``offset`` >= 0)."""
    if limit < 1 or limit > MAX_PAGE:
        raise ApiException(
            422,
            "validation",
            [{"loc": ["query", "limit"], "msg": f"limit must be 1..{MAX_PAGE}", "type": "value_error"}],
        )
    if offset < 0:
        raise ApiException(
            422, "validation", [{"loc": ["query", "offset"], "msg": "offset must be >= 0", "type": "value_error"}]
        )
    return limit, offset
