"""``/api/keys`` (personal API keys, every user) and ``/api/admin/keys`` (server keys, admins).

* Keys are write-only: responses carry a hint (vendor prefix + last 4 characters), timestamps, the
  last test result and whether the key can still be decrypted (``readable``) - never the key or its
  ciphertext (``aadhi.credentials.credential_view``).
* A personal key is used only for the jobs its owner starts; a server key saved here replaces the
  .env key for everyone (deleting it falls back to .env). Precedence: personal > server saved here >
  .env (``aadhi.credentials.resolve_keys``).
* Saving or testing is refused with 403 ``api_keys_disabled`` when keys saved in the Studio are
  switched off (``STORED_API_KEYS_ENABLED``; personal keys also ``USER_API_KEYS_ENABLED``). Deleting
  always works.
* A key test makes one cheap authenticated call to the provider (``aadhi.credentials.verify_key``),
  rate limited to ``TEST_RATE_LIMIT_PER_MINUTE`` per user, and records ``last_verified_at`` /
  ``last_error`` on the saved key.
* Audit: saves, deletions and tests are logged to ``aadhi.audit`` (user, scope, provider, outcome),
  never the key or its hint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from ... import credentials as creds
from ...config import Settings
from ...credentials import (
    KEY_PROVIDER_LABELS,
    KEY_PROVIDERS,
    CredentialError,
    Scope,
    credential_view,
    delete_credential,
    env_key_present,
    get_credential,
    personal_keys_enabled,
    read_key,
    record_verification,
    save_credential,
    server_key,
)
from ...models import User
from ...security.ratelimit import check_rate_limit
from ..deps import RATE_LIMITED, AdminUser, AppSettings, CurrentUser, DbSession
from ..errors import ApiException, not_found

router = APIRouter(tags=["keys"], dependencies=RATE_LIMITED)
audit = logging.getLogger("aadhi.audit")

TEST_RATE_LIMIT_PER_MINUTE = 10
TEST_BUCKET = "api_key_test"
UNREADABLE_MESSAGE = (
    "This saved key can no longer be decrypted (the server's encryption key changed); enter it again."
)


class ApiKeyBody(BaseModel):
    """A key to save (validated by ``aadhi.credentials.validate_api_key``; never echoed back)."""

    model_config = ConfigDict(extra="forbid")

    api_key: str


# --- helpers -----------------------------------------------------------------------------------------


def _provider(provider: str) -> str:
    if provider not in KEY_PROVIDERS:
        raise not_found("Provider")
    return provider


def _disabled() -> ApiException:
    return ApiException(403, "api_keys_disabled", "Saving API keys in the Studio is turned off on this server.")


def _save(db: Session, settings: Settings, user: User, provider: str, scope: Scope, api_key: str) -> None:
    try:
        save_credential(db, provider=provider, api_key=api_key, scope=scope, user=user, settings=settings)
    except CredentialError as exc:
        db.rollback()
        raise ApiException(
            422, "validation", [{"loc": ["body", "api_key"], "msg": str(exc), "type": "value_error"}]
        ) from None
    db.commit()
    audit.info("api key saved: scope=%s provider=%s user_id=%s", scope, provider, user.id)


def _delete(db: Session, user: User, provider: str, scope: Scope) -> Response:
    if delete_credential(db, provider=provider, scope=scope, user=user):
        db.commit()
        audit.info("api key deleted: scope=%s provider=%s user_id=%s", scope, provider, user.id)
    return Response(status_code=204)


def _personal_entry(db: Session, settings: Settings, user: User, provider: str) -> dict[str, Any]:
    row = get_credential(db, provider=provider, scope="user", user=user)
    return {
        "provider": provider,
        "label": KEY_PROVIDER_LABELS[provider],
        "personal": credential_view(row, settings) if row is not None else None,
        "server_available": server_key(db, settings, provider) is not None,
    }


def _server_entry(db: Session, settings: Settings, admin: User, provider: str) -> dict[str, Any]:
    row = get_credential(db, provider=provider, scope="server", user=admin)
    view = credential_view(row, settings) if row is not None else None
    env = env_key_present(settings, provider)
    if settings.stored_api_keys_enabled and view is not None and view["readable"]:
        active: str | None = "stored"
    else:
        active = "env" if env else None
    return {"provider": provider, "label": KEY_PROVIDER_LABELS[provider], "stored": view, "env": env,
            "active_source": active}


def _spend_test_token(user: User) -> None:
    check_rate_limit(TEST_BUCKET, f"user:{user.id}", per_minute=TEST_RATE_LIMIT_PER_MINUTE)


def _record(db: Session, row_id: int | None, ciphertext: str | None, ok: bool, message: str) -> None:
    if row_id is None:
        return
    record_verification(db, row_id, ok, message, ciphertext=ciphertext)
    db.commit()


async def _run_test(
    db: Session, settings: Settings, user: User, provider: str, scope: str, row_id: int | None, key: str | None,
    ciphertext: str | None = None,
) -> dict[str, Any]:
    """Verify ``key`` with the provider and record the outcome on ``row_id`` (when stored and still
    holding ``ciphertext``, the value ``key`` was read from)."""
    if key is None:  # saved but unreadable: nothing to send
        ok, message = False, UNREADABLE_MESSAGE
    else:
        _spend_test_token(user)
        ok, message = await creds.verify_key(provider, key, settings)
    await asyncio.to_thread(_record, db, row_id, ciphertext, ok, message)
    audit.info("api key tested: scope=%s provider=%s user_id=%s ok=%s", scope, provider, user.id, ok)
    return {"ok": ok, "message": message}


# --- personal keys -----------------------------------------------------------------------------------


@router.get("/api/keys")
def list_personal_keys(user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """The user's personal keys (hints only) and whether a server key exists for each provider."""
    return {
        "enabled": personal_keys_enabled(settings),
        "providers": [_personal_entry(db, settings, user, p) for p in KEY_PROVIDERS],
    }


@router.put("/api/keys/{provider}")
def save_personal_key(
    provider: str, body: ApiKeyBody, user: CurrentUser, db: DbSession, settings: AppSettings
) -> dict[str, Any]:
    """Save (or replace) the user's personal key for ``provider``."""
    provider = _provider(provider)
    if not personal_keys_enabled(settings):
        raise _disabled()
    _save(db, settings, user, provider, "user", body.api_key)
    return _personal_entry(db, settings, user, provider)


@router.delete("/api/keys/{provider}", status_code=204)
def delete_personal_key(provider: str, user: CurrentUser, db: DbSession) -> Response:
    """Delete the user's personal key (idempotent); their jobs use the server key again."""
    return _delete(db, user, _provider(provider), "user")


def _load_personal(db: Session, settings: Settings, user: User, provider: str) -> tuple[int, str, str | None]:
    row = get_credential(db, provider=provider, scope="user", user=user)
    if row is None:
        raise ApiException(404, "not_found", f"No personal {KEY_PROVIDER_LABELS[provider]} key is saved.")
    row_id, ciphertext, key = row.id, row.ciphertext, read_key(row, settings)
    db.rollback()  # end the read transaction before the network call
    return row_id, ciphertext, key


@router.post("/api/keys/{provider}/test")
async def test_personal_key(provider: str, user: CurrentUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Check the user's personal key with one authenticated call to the provider."""
    provider = _provider(provider)
    if not personal_keys_enabled(settings):
        raise _disabled()
    row_id, ciphertext, key = await asyncio.to_thread(_load_personal, db, settings, user, provider)
    return await _run_test(db, settings, user, provider, "user", row_id, key, ciphertext)


# --- server keys (admins) ----------------------------------------------------------------------------


@router.get("/api/admin/keys")
def list_server_keys(admin: AdminUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Server keys saved in the Studio (hints only), whether .env has a key and which one is active."""
    return {
        "enabled": bool(settings.stored_api_keys_enabled),
        "providers": [_server_entry(db, settings, admin, p) for p in KEY_PROVIDERS],
    }


@router.put("/api/admin/keys/{provider}")
def save_server_key(
    provider: str, body: ApiKeyBody, admin: AdminUser, db: DbSession, settings: AppSettings
) -> dict[str, Any]:
    """Save (or replace) the server key for ``provider``: used for everyone instead of the .env key."""
    provider = _provider(provider)
    if not settings.stored_api_keys_enabled:
        raise _disabled()
    _save(db, settings, admin, provider, "server", body.api_key)
    return _server_entry(db, settings, admin, provider)


@router.delete("/api/admin/keys/{provider}", status_code=204)
def delete_server_key(provider: str, admin: AdminUser, db: DbSession) -> Response:
    """Delete the server key saved in the Studio (idempotent); the .env key, if any, applies again."""
    return _delete(db, admin, _provider(provider), "server")


def _load_server(db: Session, settings: Settings, provider: str) -> tuple[str, int | None, str | None, str]:
    active = server_key(db, settings, provider)
    if active is None:
        raise ApiException(404, "not_found", f"No server {KEY_PROVIDER_LABELS[provider]} key is configured.")
    source, key, row = active
    row_id, ciphertext = (row.id, row.ciphertext) if row is not None else (None, None)
    db.rollback()  # end the read transaction before the network call
    return source, row_id, ciphertext, key


@router.post("/api/admin/keys/{provider}/test")
async def test_server_key(provider: str, admin: AdminUser, db: DbSession, settings: AppSettings) -> dict[str, Any]:
    """Check the active server key (saved in the Studio, else .env) with one authenticated call."""
    provider = _provider(provider)
    source, row_id, ciphertext, key = await asyncio.to_thread(_load_server, db, settings, provider)
    result = await _run_test(db, settings, admin, provider, "server", row_id, key, ciphertext)
    return {**result, "source": source}
