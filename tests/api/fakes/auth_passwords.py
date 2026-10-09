"""Fake aadhi.auth.passwords (bcrypt, low cost for test speed)."""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=4)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], (password_hash or "").encode("ascii"))
    except ValueError:
        return False


def check_password_strength(password: str, username: str, settings) -> list[str]:
    problems = []
    if len(password) < settings.password_min_length:
        problems.append(f"Use at least {settings.password_min_length} characters.")
    if username and username.lower() in password.lower():
        problems.append("The password must not contain the username.")
    if password.lower() in {"password123", "aaaaaaaaaa"}:
        problems.append("This password is too common.")
    return problems
