"""Strict JSON decoding: standard JSON only, every number finite.

Python's ``json`` accepts the non-standard literals ``NaN``/``Infinity``/``-Infinity`` and turns
overflowing numbers such as ``1e999`` into ``inf``. Neither survives the rest of the system:
Starlette renders responses with ``allow_nan=False`` (HTTP 500) and PostgreSQL JSONB rejects them.
Untrusted JSON (uploads, imported lectures, v1 databases) is therefore decoded with ``loads_strict``.
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = ["finite_float", "loads_strict"]


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def finite_float(text: str) -> float:
    """``parse_float`` hook: the float value of ``text``, or ``ValueError`` when it is not finite."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"number out of range: {text[:40]}")
    return value


def loads_strict(text: str | bytes | bytearray) -> Any:
    """``json.loads`` that rejects ``NaN``/``Infinity`` literals and non-finite numbers (``ValueError``).

    Deeply nested documents raise ``RecursionError`` exactly like ``json.loads``.
    """
    return json.loads(text, parse_constant=_reject_constant, parse_float=finite_float)
