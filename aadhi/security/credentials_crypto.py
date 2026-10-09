"""Encryption of the API keys saved in the Studio (``api_credentials.ciphertext``).

* Fernet tokens (AES-128-CBC + HMAC-SHA256, random IV, versioned): a ciphertext never reveals the key
  and any tampering is detected.
* The key is ``CREDENTIALS_ENCRYPTION_KEY`` (a Fernet key: urlsafe base64 of 32 bytes). When it is
  empty the key is derived from the resolved JWT secret with HKDF-SHA256 (info
  ``aadhi-api-credentials-v1``), so a default installation needs no extra secret. Rotating JWT_SECRET
  then makes the saved keys unreadable: ``decrypt_secret`` raises ``CredentialUnreadable`` and callers
  show the key as needing re-entry (never a crash).
* With an explicit key, tokens written with the JWT-derived key are still readable (moving from the
  derived key to an explicit one keeps the saved keys); new tokens always use the explicit key.
"""

from __future__ import annotations

import base64
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import Settings

__all__ = ["HKDF_INFO", "CredentialUnreadable", "decrypt_secret", "encrypt_secret"]

HKDF_INFO = b"aadhi-api-credentials-v1"


class CredentialUnreadable(Exception):
    """A saved key cannot be decrypted (encryption key changed, or the ciphertext was altered)."""


@lru_cache(maxsize=8)
def _derived_key(jwt_secret: str) -> bytes:
    raw = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=HKDF_INFO).derive(jwt_secret.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def _fernets(settings: Settings) -> list[Fernet]:
    """Fernets to try, the one used for new tokens first."""
    out: list[Fernet] = []
    explicit = settings.credentials_encryption_key.get_secret_value().strip()
    if explicit:
        out.append(Fernet(explicit.encode("ascii")))  # format checked by the Settings validator
    try:
        out.append(Fernet(_derived_key(settings.resolved_jwt_secret())))
    except RuntimeError:  # production without JWT_SECRET: refused at startup anyway
        if not out:
            raise
    return out


def encrypt_secret(plaintext: str, settings: Settings) -> str:
    """Fernet token (ASCII) for ``plaintext``."""
    return _fernets(settings)[0].encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, settings: Settings) -> str:
    """Plaintext of ``token``; raises ``CredentialUnreadable`` when no configured key can read it."""
    try:
        data = MultiFernet(_fernets(settings)).decrypt(token.encode("ascii"))
        return data.decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError, TypeError) as exc:
        raise CredentialUnreadable("the saved API key cannot be decrypted with the current encryption key") from exc
