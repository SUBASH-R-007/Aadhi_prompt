"""Password hashing (bcrypt 5, used directly) and strength rules.

* Hashes are standard ``$2b$`` bcrypt strings, so v1 hashes created by passlib's bcrypt scheme
  verify unchanged.
* bcrypt only looks at the first 72 bytes and bcrypt 5 *raises* for longer input. Instead of
  silently truncating, the strength check rejects such passwords and ``verify_password`` returns
  False for them.
* ``verify_password`` with an empty/unknown hash (unknown user) still runs a full bcrypt
  comparison against a dummy hash of the same cost, so response timing does not reveal whether a
  username exists.
"""

from __future__ import annotations

import re
import threading
import unicodedata

import bcrypt

from ..config import Settings, get_settings

__all__ = [
    "BCRYPT_MAX_BYTES",
    "check_password_strength",
    "hash_password",
    "needs_rehash",
    "verify_password",
]

BCRYPT_MAX_BYTES = 72
PRODUCTION_ROUNDS = 12  # passlib's default too (v1 hashes)
TEST_ROUNDS = 4  # APP_ENV=test only: keeps the test-suite fast
_HASH_RE = re.compile(r"^\$2[aby]\$(\d{2})\$[./A-Za-z0-9]{53}$")

_dummy_lock = threading.Lock()
_dummy_hashes: dict[int, bytes] = {}

# Base words that make a password guessable no matter which digits/symbols decorate them.
_BASE_WORDS = frozenset(
    {
        "password",
        "passw",
        "admin",
        "administrator",
        "aadhi",
        "aadhiedu",
        "eduengine",
        "rajalakshmi",
        "rec",
        "qwerty",
        "qwertyuiop",
        "asdfgh",
        "welcome",
        "letmein",
        "changeme",
        "iloveyou",
        "blackbuck",
        "teacher",
        "student",
        "secret",
        "default",
        "login",
        "abc",
        "abcdef",
        "abcdefgh",
        "test",
        "guest",
    }
)
_COMMON = frozenset(
    {
        "1234567890",
        "0123456789",
        "12345678910",
        "123456789012",
        "1111111111",
        "0000000000",
        "9876543210",
        "1q2w3e4r5t",
        "qazwsxedcr",
        "zaq12wsxcde",
        "1qaz2wsx3edc",
    }
)


def _rounds() -> int:
    try:
        return TEST_ROUNDS if get_settings().app_env == "test" else PRODUCTION_ROUNDS
    except (ValueError, OSError, RuntimeError):  # pragma: no cover - broken env: use the safe default
        return PRODUCTION_ROUNDS


def _dummy_hash(rounds: int) -> bytes:
    with _dummy_lock:
        h = _dummy_hashes.get(rounds)
        if h is None:
            h = bcrypt.hashpw(b"aadhi-dummy-password-for-timing", bcrypt.gensalt(rounds=rounds))
            _dummy_hashes[rounds] = h
        return h


def _parse_hash(password_hash: str | None) -> tuple[bytes, int] | None:
    """``(hash bytes, cost)`` for a well-formed bcrypt hash, else None."""
    if not password_hash or not isinstance(password_hash, str):
        return None
    m = _HASH_RE.match(password_hash)
    if not m:
        return None
    return password_hash.encode("ascii"), int(m.group(1))


def hash_password(password: str) -> str:
    """Return a bcrypt hash (``$2b$``) of ``password``.

    Raises ``ValueError`` for empty passwords and passwords longer than 72 UTF-8 bytes (callers
    run ``check_password_strength`` first, which reports this as a user-facing problem).
    """
    if not password:
        raise ValueError("password must not be empty")
    raw = password.encode("utf-8")
    if len(raw) > BCRYPT_MAX_BYTES:
        raise ValueError(f"password must be at most {BCRYPT_MAX_BYTES} bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=_rounds())).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-effort password check.

    Accepts ``$2a$``/``$2b$``/``$2y$`` bcrypt hashes (incl. v1 passlib hashes). An empty, malformed
    or missing hash (unknown user) and over-long passwords still cost one bcrypt comparison against a
    dummy hash and return False.
    """
    raw = (password or "").encode("utf-8")
    parsed = _parse_hash(password_hash)
    if parsed is None or not raw or len(raw) > BCRYPT_MAX_BYTES:
        rounds = parsed[1] if parsed is not None else _rounds()
        try:
            bcrypt.checkpw((raw or b"x")[:BCRYPT_MAX_BYTES], _dummy_hash(min(max(rounds, 4), 16)))
        except ValueError:  # pragma: no cover - defensive
            pass
        return False
    try:
        return bcrypt.checkpw(raw, parsed[0])
    except ValueError:
        return False


def needs_rehash(password_hash: str | None) -> bool:
    """True when the stored hash uses fewer rounds than the current policy (rehash on next login)."""
    m = _HASH_RE.match(password_hash or "")
    return m is None or int(m.group(1)) < _rounds()


def check_password_strength(password: str, username: str, settings: Settings) -> list[str]:
    """Return user-facing problems with ``password`` (empty list = acceptable).

    Rules (NIST 800-63B style: length + deny-list, no composition rules): minimum length
    ``PASSWORD_MIN_LENGTH``, at most 72 UTF-8 bytes (bcrypt limit), no control characters, at least
    5 distinct characters, must not contain the username, must not be a common password or a common
    word decorated with digits/symbols.
    """
    problems: list[str] = []
    password = password or ""
    normalized = unicodedata.normalize("NFKC", password)
    min_len = max(int(settings.password_min_length), 8)
    if len(normalized) < min_len:
        problems.append(f"Password must be at least {min_len} characters long.")
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        problems.append(f"Password must be at most {BCRYPT_MAX_BYTES} bytes (shorter, or fewer non-ASCII characters).")
    if any(unicodedata.category(ch) == "Cc" for ch in password):
        problems.append("Password must not contain control characters.")
    if len(set(normalized)) < 5:
        problems.append("Password must contain at least 5 different characters.")
    lowered = normalized.casefold()
    uname = (username or "").strip().casefold()
    if uname and len(uname) >= 3 and uname in lowered:
        problems.append("Password must not contain the username.")
    letters = re.sub(r"[^a-z]", "", lowered)
    if lowered in _COMMON or (len(letters) >= 3 and letters in _BASE_WORDS) or lowered in _BASE_WORDS:
        problems.append("Password is too common; choose a less guessable one (a passphrase works well).")
    return problems
