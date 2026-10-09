"""API keys saved in the Studio ("bring your own key").

* Admins save **server keys** (``owner_key`` ``"server"``): used for everyone, instead of the
  ``*_API_KEY`` environment values. Every user may save **personal keys** (``"user:<id>"``): used only
  for the jobs that user starts. Providers: ``gemini``, ``openai``, ``anthropic`` (a saved OpenAI or
  Gemini key also powers that provider's voices, and Gemini images / Veo videos, because those
  adapters read the same Settings fields).
* Precedence for user U and provider P (``resolve_keys``): U's personal key (when personal keys are
  enabled) > the server key saved in the Studio > the environment key. A personal key the provider
  rejects fails the job (``personal_key_rejection``); it never silently falls back to a server key.
* Keys are stored encrypted (``aadhi.security.credentials_crypto``) and only ever leave this module as
  a ``key_hint`` (vendor prefix + last 4 characters). Every decrypted or newly saved key is
  registered for redaction (``aadhi.security.redaction``) under its owner slot
  (``"<owner_key>:<provider>"``: replacing a key swaps the slot's value, deleting it frees the slot),
  so it is scrubbed from logs, job events and error messages like a key from ``.env`` without any
  user being able to grow the shared list. A ciphertext that can no longer be decrypted (e.g. after
  rotating JWT_SECRET) is skipped and reported as ``readable: false``, never a crash.
* ``STORED_API_KEYS_ENABLED=false`` ignores every saved key (the offline demo sets it);
  ``USER_API_KEYS_ENABLED=false`` ignores personal keys only.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .jobs.util import iso
from .models import CREDENTIAL_PROVIDERS, SERVER_CREDENTIAL_OWNER, ApiCredential, User, utcnow
from .providers.base import ProviderError
from .providers.factory import LLM_ENGINE_LABELS
from .security.credentials_crypto import CredentialUnreadable, decrypt_secret, encrypt_secret
from .security.redaction import forget_secret, register_secret

log = logging.getLogger(__name__)

__all__ = [
    "KEY_PROVIDERS",
    "KEY_PROVIDER_LABELS",
    "CredentialError",
    "ResolvedKeys",
    "billed_to",
    "credential_view",
    "delete_credential",
    "env_key_present",
    "get_credential",
    "key_hint",
    "personal_key_rejection",
    "personal_keys_enabled",
    "read_key",
    "record_verification",
    "reset_key_cache",
    "resolve_keys",
    "save_credential",
    "server_key",
    "validate_api_key",
    "verify_key",
]

KEY_PROVIDERS = CREDENTIAL_PROVIDERS  # ("gemini", "openai", "anthropic")
KEY_PROVIDER_LABELS = {p: LLM_ENGINE_LABELS[p] for p in KEY_PROVIDERS}  # "Anthropic Claude" ...
# Usage.provider -> the provider whose key pays for it (Veo videos run on the Gemini key).
USAGE_KEY_PROVIDER = {"gemini": "gemini", "veo": "gemini", "openai": "openai", "anthropic": "anthropic"}
Scope = Literal["user", "server"]

MIN_KEY_CHARS = 16
MAX_KEY_CHARS = 512
# The characters the three vendors' keys use (base64url-like). Anything else is not a key, and it must
# not reach the redaction registry, where an arbitrary string would be censored in everyone's logs.
_KEY_CHARS = re.compile(r"[A-Za-z0-9_-]+")
VENDOR_PREFIXES = ("sk-ant-", "sk-proj-", "sk-", "AIza")  # longest first: the hint shows the most specific
HINT_TAIL = 4
VERIFY_TIMEOUT_SECONDS = 15.0
MAX_ERROR_CHARS = 300  # ApiCredential.last_error
_RESOLVE_CACHE_MAX = 256
_UNREADABLE_SEEN_MAX = 1024


class CredentialError(ValueError):
    """Invalid API key input (422). The message never contains the key."""


# --- small helpers ---------------------------------------------------------------------------------


def personal_keys_enabled(settings: Settings) -> bool:
    """Whether personal keys are used (and may be saved): both switches on."""
    return bool(settings.stored_api_keys_enabled and settings.user_api_keys_enabled)


def env_key_present(settings: Settings, provider: str) -> bool:
    """Whether ``settings`` (environment / .env, or already resolved) holds a key for ``provider``."""
    if provider == "gemini":
        return bool(settings.all_gemini_keys)
    return bool(getattr(settings, f"{provider}_api_key").get_secret_value())


def _env_key(settings: Settings, provider: str) -> str:
    if provider == "gemini":
        keys = settings.all_gemini_keys
        return keys[0] if keys else ""
    return getattr(settings, f"{provider}_api_key").get_secret_value()


def _owner(scope: str, user: User) -> str:
    if scope == "server":
        return SERVER_CREDENTIAL_OWNER
    if scope == "user":
        return f"user:{user.id}"
    raise ValueError(f"unknown credential scope {scope!r}")


def _check_provider(provider: str) -> None:
    if provider not in KEY_PROVIDERS:
        raise ValueError(f"unknown API key provider {provider!r}")


def _slot(owner_key: str, provider: str) -> str:
    """Redaction-registry slot of a saved key (one value per owner and provider)."""
    return f"{owner_key}:{provider}"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def key_hint(api_key: str) -> str:
    """``"<vendor prefix>…<last 4>"``: never more of the secret than its last four characters."""
    prefix = next((p for p in VENDOR_PREFIXES if api_key.startswith(p)), "")
    return f"{prefix}…{api_key[-HINT_TAIL:]}"


def validate_api_key(raw: Any) -> str:
    """The stripped key, or ``CredentialError`` (16-512 letters, digits, ``-`` or ``_``)."""
    if not isinstance(raw, str):
        raise CredentialError("The API key must be a string.")
    key = raw.strip()
    if not key:
        raise CredentialError("Enter an API key.")
    if len(key) < MIN_KEY_CHARS or len(key) > MAX_KEY_CHARS:
        raise CredentialError(f"API keys are {MIN_KEY_CHARS} to {MAX_KEY_CHARS} characters long.")
    if not _KEY_CHARS.fullmatch(key):  # also rejects whitespace, control and non-ASCII characters
        raise CredentialError("An API key contains only letters, digits, '-' and '_' (no spaces).")
    return key


# --- rows ------------------------------------------------------------------------------------------


def get_credential(db: Session, *, provider: str, scope: Scope, user: User) -> ApiCredential | None:
    """The saved key row of ``scope`` (``user``: ``user``'s personal key; ``server``: the admin key)."""
    _check_provider(provider)
    return db.execute(
        select(ApiCredential).where(ApiCredential.owner_key == _owner(scope, user), ApiCredential.provider == provider)
    ).scalar_one_or_none()


def _server_row(db: Session, provider: str) -> ApiCredential | None:
    return db.execute(
        select(ApiCredential).where(
            ApiCredential.owner_key == SERVER_CREDENTIAL_OWNER, ApiCredential.provider == provider
        )
    ).scalar_one_or_none()


_unreadable_seen: OrderedDict[tuple[int, str], None] = OrderedDict()
_unreadable_lock = threading.Lock()


def _decrypt(row_id: int, owner_key: str, provider: str, ciphertext: str, settings: Settings) -> str | None:
    try:
        key = decrypt_secret(ciphertext, settings)
    except CredentialUnreadable:
        marker = (row_id, _digest(ciphertext))
        with _unreadable_lock:
            first = marker not in _unreadable_seen
            _unreadable_seen[marker] = None
            while len(_unreadable_seen) > _UNREADABLE_SEEN_MAX:
                _unreadable_seen.popitem(last=False)
        if first:
            kind = "server" if owner_key == SERVER_CREDENTIAL_OWNER else "personal"
            log.warning(
                "saved %s %s API key (credential %s) cannot be decrypted (encryption key changed?); it is "
                "ignored until it is entered again", kind, provider, row_id,
            )
        return None
    register_secret(key, owner=_slot(owner_key, provider))
    return key


def read_key(row: ApiCredential, settings: Settings) -> str | None:
    """Decrypted key of ``row`` (registered for redaction), None when it cannot be decrypted."""
    return _decrypt(row.id, row.owner_key, row.provider, row.ciphertext, settings)


def credential_view(row: ApiCredential, settings: Settings | None = None) -> dict[str, Any]:
    """``{"hint", "updated_at", "last_verified_at", "last_error", "readable"}`` (never the key or ciphertext)."""
    settings = settings or get_settings()
    return {
        "hint": row.key_hint,
        "updated_at": iso(row.updated_at),
        "last_verified_at": iso(row.last_verified_at),
        "last_error": row.last_error,
        "readable": read_key(row, settings) is not None,
    }


def save_credential(
    db: Session, *, provider: str, api_key: str, scope: Scope, user: User, settings: Settings | None = None
) -> ApiCredential:
    """Encrypt and store (insert or replace) a key; flushed, the caller commits.

    Raises ``CredentialError`` for invalid input. Concurrent saves of the same (owner, provider)
    converge on one row: the loser of the insert race replaces the winner's key (upsert semantics).
    Replacing a key clears its verification state.
    """
    settings = settings or get_settings()
    _check_provider(provider)
    owner = _owner(scope, user)
    key = validate_api_key(api_key)
    register_secret(key, owner=_slot(owner, provider))  # replaces this slot's previous key
    values: dict[str, Any] = {
        "ciphertext": encrypt_secret(key, settings),
        "key_hint": key_hint(key),
        "created_by_id": user.id,
        "last_verified_at": None,
        "last_error": None,
        "updated_at": utcnow(),
    }
    stmt = select(ApiCredential).where(ApiCredential.owner_key == owner, ApiCredential.provider == provider)
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        fresh = ApiCredential(
            owner_key=owner, user_id=None if scope == "server" else user.id, provider=provider, **values
        )
        try:
            with db.begin_nested():
                db.add(fresh)
            return fresh
        except IntegrityError:  # a concurrent save inserted it first: replace that key instead
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                raise
    db.execute(
        update(ApiCredential).where(ApiCredential.id == row.id).values(**values).execution_options(
            synchronize_session=False
        )
    )
    db.refresh(row)
    return row


def delete_credential(db: Session, *, provider: str, scope: Scope, user: User) -> bool:
    """Delete the saved key of ``scope``; True when one existed. The caller commits."""
    _check_provider(provider)
    owner = _owner(scope, user)
    result = db.execute(
        delete(ApiCredential)
        .where(ApiCredential.owner_key == owner, ApiCredential.provider == provider)
        .execution_options(synchronize_session=False)
    )
    if (result.rowcount or 0) > 0:
        forget_secret(_slot(owner, provider))
        return True
    return False


def record_verification(
    db: Session, row_id: int, ok: bool, message: str, *, ciphertext: str | None = None
) -> None:
    """Store the outcome of a key test (``last_verified_at`` on success, else ``last_error``). The caller commits.

    ``ciphertext`` is the value the tested key was read from: a key replaced while the test ran
    (same row, updated in place) keeps its own status instead of inheriting the old key's result.
    """
    values: dict[str, Any] = {"last_error": None if ok else message[:MAX_ERROR_CHARS]}
    if ok:
        values["last_verified_at"] = utcnow()
    stmt = update(ApiCredential).where(ApiCredential.id == row_id)
    if ciphertext is not None:
        stmt = stmt.where(ApiCredential.ciphertext == ciphertext)
    db.execute(stmt.values(**values).execution_options(synchronize_session=False))


def server_key(db: Session, settings: Settings, provider: str) -> tuple[str, str, ApiCredential | None] | None:
    """The active server key of ``provider``: ``("stored", key, row)`` (saved in the Studio and
    readable), else ``("env", key, None)``, else None."""
    _check_provider(provider)
    if settings.stored_api_keys_enabled:
        row = _server_row(db, provider)
        key = read_key(row, settings) if row is not None else None
        if key:
            return "stored", key, row
    env = _env_key(settings, provider)
    return ("env", env, None) if env else None


# --- resolution ------------------------------------------------------------------------------------


@dataclass
class ResolvedKeys:
    """The settings a user's work runs with and where each provider's key comes from.

    ``sources``: provider -> ``"personal"`` | ``"server"`` (saved in the Studio) | ``"env"`` | None.
    """

    settings: Settings
    sources: dict[str, str | None]

    def source(self, provider: str | None) -> str | None:
        return self.sources.get(provider or "")


# key -> (base settings, snapshot of its field values, resolution)
_cache: OrderedDict[tuple[Any, ...], tuple[Settings, dict[str, Any], ResolvedKeys]] = OrderedDict()
_cache_lock = threading.Lock()


def reset_key_cache() -> None:
    """Drop every cached resolution (tests)."""
    with _cache_lock:
        _cache.clear()
    with _unreadable_lock:
        _unreadable_seen.clear()


def _env_sources(settings: Settings) -> dict[str, str | None]:
    return {p: ("env" if env_key_present(settings, p) else None) for p in KEY_PROVIDERS}


def resolve_keys(db: Session, settings: Settings, user_id: int | None) -> ResolvedKeys:
    """Effective keys for ``user_id`` (None = server keys only; see the module docstring for precedence).

    Returns ``settings`` itself when no saved key applies (provider instances are cached per settings
    object, so identity matters); otherwise a cached ``settings.model_copy`` with ``gemini_api_key``
    (and an emptied ``gemini_api_keys`` pool, so a personal key is never mixed with the server's),
    ``openai_api_key`` and ``anthropic_api_key`` replaced. The cache is keyed by the settings object,
    the user and a fingerprint of the relevant rows, so a saved, replaced or deleted key takes effect
    on the next call; a hit is reused only while the base settings' field values are unchanged (the
    copy carries all of them).
    """
    if not settings.stored_api_keys_enabled:
        return ResolvedKeys(settings, _env_sources(settings))
    personal_owner = f"user:{user_id}" if user_id is not None and settings.user_api_keys_enabled else None
    owners = [SERVER_CREDENTIAL_OWNER] + ([personal_owner] if personal_owner else [])
    rows = db.execute(
        select(ApiCredential.id, ApiCredential.owner_key, ApiCredential.provider, ApiCredential.ciphertext).where(
            ApiCredential.owner_key.in_(owners)
        )
    ).all()
    if not rows:
        return ResolvedKeys(settings, _env_sources(settings))
    fingerprint = tuple(sorted((r.owner_key, r.provider, r.id, _digest(r.ciphertext)) for r in rows))
    cache_key = (id(settings), user_id, fingerprint)
    snapshot = dict(settings.__dict__)
    with _cache_lock:
        hit = _cache.get(cache_key)
        if hit is not None and hit[0] is settings and hit[1] == snapshot:
            _cache.move_to_end(cache_key)
            return hit[2]

    by_owner = {(r.owner_key, r.provider): r for r in rows}
    chosen: dict[str, tuple[str, str]] = {}
    for provider in KEY_PROVIDERS:
        for owner, source in ((personal_owner, "personal"), (SERVER_CREDENTIAL_OWNER, "server")):
            row = by_owner.get((owner, provider)) if owner else None
            if row is None:
                continue
            key = _decrypt(row.id, row.owner_key, row.provider, row.ciphertext, settings)
            if key:
                chosen[provider] = (source, key)
                break
    sources = _env_sources(settings)
    if chosen:
        changes: dict[str, Any] = {}
        for provider, (source, key) in chosen.items():
            sources[provider] = source
            changes[f"{provider}_api_key"] = SecretStr(key)
            if provider == "gemini":
                changes["gemini_api_keys"] = []
        resolved = ResolvedKeys(settings.model_copy(update=changes), sources)
    else:  # every relevant saved key is unreadable
        resolved = ResolvedKeys(settings, sources)
    with _cache_lock:
        _cache[cache_key] = (settings, snapshot, resolved)
        while len(_cache) > _RESOLVE_CACHE_MAX:
            _cache.popitem(last=False)
    return resolved


def billed_to(sources: Mapping[str, str | None], usage_provider: str) -> str:
    """``"user"`` when a usage of ``usage_provider`` was paid with the user's personal key, else ``"server"``."""
    provider = USAGE_KEY_PROVIDER.get(str(usage_provider or "").lower())
    return "user" if provider is not None and sources.get(provider) == "personal" else "server"


# Gemini answers a wrong or expired API key with HTTP 400 INVALID_ARGUMENT, not 401/403.
_GEMINI_BAD_KEY = re.compile(r"(?i)API[ _]key (?:not valid|expired)|API_KEY_(?:INVALID|EXPIRED)")


def _key_refused(provider: str | None, status: int | None, text: str) -> bool:
    """Whether a provider answer means "this API key is not accepted" (401/403, Gemini's invalid-key 400)."""
    if status in (401, 403):
        return True
    return provider == "gemini" and status == 400 and bool(_GEMINI_BAD_KEY.search(text))


def personal_key_rejection(exc: BaseException, sources: Mapping[str, str | None]) -> str | None:
    """User-facing job error when ``exc`` (or an exception it wraps) is a provider refusing a personal key
    (HTTP 401/403, or Gemini's HTTP 400 ``API_KEY_INVALID`` / ``API key expired``)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProviderError):
            provider = USAGE_KEY_PROVIDER.get(str(current.provider or "").lower())
            refused = _key_refused(provider, current.status, str(current))
            if refused and provider is not None and sources.get(provider) == "personal":
                return (
                    f"Your personal {KEY_PROVIDER_LABELS[provider]} API key was rejected (HTTP {current.status}). "
                    "Update or remove it under API keys, then retry; jobs never fall back to the server key."
                )
        current = current.__cause__ or current.__context__
    return None


# --- verification ----------------------------------------------------------------------------------


def _client_factory(provider: str, api_key: str, timeout: float) -> Any:
    """Build the official SDK client (imports + TLS setup: run in a thread)."""
    if provider == "anthropic":
        import anthropic

        return anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=0)
    if provider == "openai":
        import openai

        return openai.AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    from google import genai
    from google.genai import types

    return genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=int(timeout * 1000)))


async def _list_models(provider: str, api_key: str, timeout: float) -> None:
    """One cheap authenticated call: list (at most a page of) models."""
    client = await asyncio.to_thread(_client_factory, provider, api_key, timeout)
    try:
        if provider == "anthropic":
            await client.models.list(limit=1)
        elif provider == "openai":
            await client.models.list()
        else:
            await client.aio.models.list(config={"page_size": 1})
    finally:
        target = client.aio if provider == "gemini" else client
        close = getattr(target, "aclose", None) or getattr(target, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:  # noqa: BLE001 - closing a throwaway client is best effort
                pass


def _status(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


def _failure_message(provider: str, exc: BaseException) -> str:
    """Unredacted user-facing text for a failed key test (the caller redacts and truncates)."""
    label = KEY_PROVIDER_LABELS[provider]
    status = _status(exc)
    if isinstance(exc, ImportError):
        return f"The {label} SDK is not installed on this server."
    if isinstance(exc, TimeoutError | asyncio.TimeoutError) or "timeout" in type(exc).__name__.lower():
        return f"{label} did not answer within {VERIFY_TIMEOUT_SECONDS:.0f} s; try again later."
    detail = " ".join(str(getattr(exc, "message", None) or exc).split())
    if _key_refused(provider, status, detail):
        return f"{label} rejected the key (HTTP {status}): check that it is correct, active and allowed to list models."
    if status == 429:
        return f"{label} is rate limiting this key or its quota is used up (HTTP 429); try again later."
    if status is not None:
        text = f"{label} returned HTTP {status}" + (f": {detail}" if detail else "")
    elif "connect" in type(exc).__name__.lower():
        text = f"Could not reach {label} ({type(exc).__name__})."
    else:
        text = f"Testing the key failed: {type(exc).__name__}" + (f": {detail}" if detail else "")
    return text


async def verify_key(provider: str, api_key: str, settings: Settings) -> tuple[bool, str]:
    """``(ok, message)`` from one authenticated list-models call with the official SDK (short
    timeout, no retries). The message is redacted and at most ``MAX_ERROR_CHARS`` long.

    ``api_key`` is scrubbed from the message here rather than registered process-wide: callers pass a
    saved key (``read_key`` already registered it under its slot) or a ``.env`` key (a Settings secret).
    """
    _check_provider(provider)
    try:
        await asyncio.wait_for(_list_models(provider, api_key, VERIFY_TIMEOUT_SECONDS), VERIFY_TIMEOUT_SECONDS + 5)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure becomes a redacted message
        message = settings.redact(_failure_message(provider, exc))
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        return False, message[:MAX_ERROR_CHARS]
    return True, f"{KEY_PROVIDER_LABELS[provider]} accepted the key."
