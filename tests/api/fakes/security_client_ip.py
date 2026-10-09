"""Fake aadhi.security.client_ip."""

from __future__ import annotations


def client_ip(request, settings) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in settings.trusted_proxies:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return peer
