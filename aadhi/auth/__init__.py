"""Authentication: password hashing, session/scoped tokens, cookies, FastAPI deps, admin bootstrap."""

from .errors import AppHTTPException
from .passwords import check_password_strength, hash_password, verify_password
from .tokens import (
    InvalidToken,
    create_scoped_token,
    create_session_token,
    decode_scoped_token,
    decode_session_token,
)

__all__ = [
    "AppHTTPException",
    "InvalidToken",
    "check_password_strength",
    "create_scoped_token",
    "create_session_token",
    "decode_scoped_token",
    "decode_session_token",
    "hash_password",
    "verify_password",
]
