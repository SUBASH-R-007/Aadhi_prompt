"""Storable JSON: every number finite, every string encodable as UTF-8.

Python's ``json`` accepts the literals ``NaN``/``Infinity`` and turns overflowing numbers such as
``1e999`` into ``inf``; a JSON escape can carry a lone UTF-16 surrogate (``"\\ud800"``). None of these
survive the rest of the system: Starlette renders responses with ``allow_nan=False`` and encodes
them as UTF-8 (HTTP 500 on every later read), and PostgreSQL JSONB refuses both.

Policy: refuse on write, sanitise on read.

* ``unstorable`` / ``check_storable`` find the first offending value (with its path) so request
  bodies and documents are refused with a 422 *before* anything is written;
  ``StrictModel`` (``allow_inf_nan=False``) already refuses non-finite numbers in typed fields.
* ``json_safe`` serves documents stored before writes were checked (non-finite numbers become
  ``null``, lone surrogates U+FFFD), and ``validate_stored`` loads them into a schema (non-finite
  numbers the schema refuses are read as 0), so such a document never turns into a 500.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

__all__ = [
    "NON_FINITE_MESSAGE",
    "SURROGATE_MESSAGE",
    "check_storable",
    "dotted",
    "json_safe",
    "unstorable",
    "validate_stored",
]

log = logging.getLogger(__name__)

NON_FINITE_MESSAGE = (
    "Numbers must be finite: NaN, Infinity and numbers too large to store (such as 1e999) are not allowed."
)
SURROGATE_MESSAGE = "The text contains an invalid character (an unpaired UTF-16 surrogate) that cannot be stored."

_SURROGATE = re.compile("[\ud800-\udfff]")
M = TypeVar("M", bound=BaseModel)


def dotted(path: tuple[Any, ...] | list[Any]) -> str:
    """``("scenes", 2, "title")`` -> ``"scenes.2.title"`` (``"(root)"`` for the empty path)."""
    return ".".join(str(p) for p in path) or "(root)"


def unstorable(value: Any) -> tuple[tuple[Any, ...], str] | None:
    """``(path, message)`` of the first value JSON cannot store (document order), else None.

    Checks floats (finite), strings and dict keys (no lone surrogates) inside dicts, lists and tuples;
    iterative, so deeply nested input cannot exhaust the stack.
    """
    stack: list[tuple[tuple[Any, ...], Any]] = [((), value)]
    while stack:
        path, v = stack.pop()
        if isinstance(v, str):
            if _SURROGATE.search(v):
                return path, SURROGATE_MESSAGE
        elif isinstance(v, float):
            if not math.isfinite(v):
                return path, NON_FINITE_MESSAGE
        elif isinstance(v, dict):
            items = []
            for k, item in v.items():
                if isinstance(k, str) and _SURROGATE.search(k):
                    return (*path, k), SURROGATE_MESSAGE
                if isinstance(k, float) and not math.isfinite(k):
                    return (*path, k), NON_FINITE_MESSAGE
                items.append(((*path, k), item))
            stack.extend(reversed(items))
        elif isinstance(v, (list, tuple)):
            stack.extend(((*path, i), item) for i, item in reversed(list(enumerate(v))))
    return None


def check_storable(value: Any) -> None:
    """``ValueError`` naming the path of the first value JSON cannot store."""
    found = unstorable(value)
    if found is not None:
        path, message = found
        raise ValueError(f"{message} (at {dotted(path)})")


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return _SURROGATE.sub("�", value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {_clean(k) if isinstance(k, str) else k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def json_safe(value: Any) -> Any:
    """``value`` when it is storable, else a sanitised copy (non-finite numbers -> None, lone surrogates ->
    U+FFFD). For serving documents stored before writes were checked; never used to accept input.

    The clean case is checked by encoding exactly as Starlette renders responses (C speed); only
    non-JSON input falls back to the structural walk."""
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        return value
    except ValueError:  # NaN / Infinity, or UnicodeEncodeError for a lone surrogate
        return _clean(value)
    except TypeError:  # not plain JSON data (e.g. a datetime): check it structurally
        return value if unstorable(value) is None else _clean(value)


def _set_at(data: Any, loc: tuple[Any, ...], value: Any) -> bool:
    """Replace the value at a pydantic error ``loc`` (discriminated-union tags in it are skipped)."""
    node = data
    parts = list(loc)
    while parts:
        part = parts.pop(0)
        if isinstance(node, dict):
            if part in node:
                if not parts:
                    node[part] = value
                    return True
                node = node[part]
            elif isinstance(part, str) and node.get("type") == part:
                continue  # the union member's tag ("content", "quiz_checkpoint" ...)
            else:
                return False
        elif isinstance(node, list) and isinstance(part, int) and 0 <= part < len(node):
            if not parts:
                node[part] = value
                return True
            node = node[part]
        else:
            return False
    return False


_REPAIRABLE = frozenset({"finite_number", "string_unicode"})


def validate_stored(model: type[M], data: Any) -> M:
    """``model.model_validate(data)`` for a document read from storage.

    A document written before unstorable values were refused still loads: when every validation error
    is a non-finite number or a lone surrogate, the numbers are read as 0 and the surrogates as U+FFFD
    (logged). Any other error raises as usual.
    """
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        if not errors or any(e.get("type") not in _REPAIRABLE for e in errors):
            raise
        fixed = copy.deepcopy(data)
        for e in errors:
            if e.get("type") == "finite_number" and not _set_at(fixed, tuple(e.get("loc", ())), 0.0):
                raise
        if any(e.get("type") == "string_unicode" for e in errors):
            fixed = _clean(fixed)  # lone surrogates -> U+FFFD (the non-finite numbers are already 0)
        log.warning("stored %s had %d unstorable value(s); read sanitised", model.__name__, len(errors))
        return model.model_validate(fixed)
