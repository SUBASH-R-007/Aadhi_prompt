"""Tiny helpers shared by the pure-ASGI security middlewares."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from starlette.types import Scope, Send

__all__ = ["header", "headers_all", "normalize_origin", "send_json_error"]


def header(scope: Scope, name: bytes) -> str | None:
    """First value of request header ``name`` (lower-case bytes) decoded as latin-1, or None."""
    for key, value in scope.get("headers") or ():
        if key == name:
            return value.decode("latin-1")
    return None


def headers_all(scope: Scope, name: bytes) -> list[str]:
    """All values of request header ``name``."""
    return [v.decode("latin-1") for k, v in scope.get("headers") or () if k == name]


def normalize_origin(value: str | None) -> str | None:
    """``scheme://host[:port]`` lower-cased with default ports removed; None if not an http(s) origin."""
    if not value or value == "null":
        return None
    try:
        parts = urlsplit(value.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    if scheme not in ("http", "https") or not host:
        return None
    if port is None or (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


async def send_json_error(
    send: Send, status: int, detail: str, code: str, *, extra_headers: Iterable[tuple[str, str]] = (), **extra: Any
) -> None:
    """Send a complete ``{"detail", "code"}`` JSON error response."""
    body = json.dumps({"detail": detail, "code": code, **extra}).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"cache-control", b"no-store"),
    ]
    headers.extend((k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})
