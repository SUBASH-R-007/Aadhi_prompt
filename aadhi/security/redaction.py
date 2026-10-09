"""Process-wide registry of secret values that are not ``Settings`` fields.

API keys saved in the Studio (``aadhi.credentials``) are decrypted at run time. Every decrypted (or
newly entered) key is registered here, and ``Settings.secret_values()`` / ``Settings.redact()`` of
EVERY Settings instance (``get_settings()`` included, which ``aadhi.jobs.util`` uses) plus the logging
``RedactingFilter`` consult it, so such a key is scrubbed from job events, errors and logs exactly
like a key from ``.env``.

Values live in memory only (never persisted or logged). A saved key is registered under its
**owner slot** (``"server:anthropic"``, ``"user:3:openai"``): a slot holds one value, so replacing a
key swaps that slot's entry, deleting it frees the slot (``forget_secret``), and owned values are
never evicted (they are bounded by the number of saved keys). This keeps one user's key churn from
growing the shared list or pushing another user's key out of it. Values registered without an
owner form a bounded LRU: past ``MAX_REGISTERED`` the least recently registered one is dropped.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

__all__ = [
    "MIN_SECRET_CHARS",
    "clear_registered_secrets",
    "forget_secret",
    "register_secret",
    "registered_secrets",
]

MIN_SECRET_CHARS = 6  # same floor as Settings.secret_values(): shorter strings would redact ordinary text
MAX_REGISTERED = 4096  # values registered without an owner

_lock = threading.Lock()
_values: OrderedDict[str, None] = OrderedDict()  # unowned values, least recently registered first
_owned: dict[str, str] = {}  # owner slot -> its current value
_snapshot: tuple[str, ...] = ()  # longest first; replaced (never mutated) so readers need no lock


def _rebuild() -> None:
    """Recompute the snapshot (caller holds ``_lock``)."""
    global _snapshot
    _snapshot = tuple(sorted(set(_values) | set(_owned.values()), key=len, reverse=True))


def register_secret(value: str | None, owner: str | None = None) -> None:
    """Redact ``value`` everywhere from now on (no-op for empty or very short values).

    With ``owner`` (a slot such as ``"user:3:openai"``) the value replaces that slot's previous one
    and is never evicted; without it the value joins the bounded LRU.
    """
    if not value or len(value) < MIN_SECRET_CHARS:
        return
    with _lock:
        if owner is not None:
            if _owned.get(owner) == value:
                return
            _owned[owner] = value
            _rebuild()
            return
        if value in _values:
            _values.move_to_end(value)
            return
        _values[value] = None
        while len(_values) > MAX_REGISTERED:
            _values.popitem(last=False)
        _rebuild()


def forget_secret(owner: str) -> None:
    """Stop redacting the value held by ``owner`` (its key was deleted); no-op for an empty slot."""
    with _lock:
        if _owned.pop(owner, None) is not None:
            _rebuild()


def registered_secrets() -> tuple[str, ...]:
    """Every registered value, longest first."""
    return _snapshot


def clear_registered_secrets() -> None:
    """Forget every registered value (tests)."""
    global _snapshot
    with _lock:
        _values.clear()
        _owned.clear()
        _snapshot = ()
