"""Client IP resolution that only trusts ``X-Forwarded-For`` from configured proxies.

``TRUSTED_PROXIES`` holds IPs or CIDR networks (e.g. ``127.0.0.1,10.0.0.0/8``). When the direct
peer is trusted, ``X-Forwarded-For`` is walked right-to-left, skipping trusted hops; the first
untrusted address is the client. Spoofed left-most entries are therefore ignored.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

from starlette.requests import Request
from starlette.types import Scope

from ..config import Settings
from ._asgi import headers_all

__all__ = ["client_ip", "client_ip_from_scope", "is_trusted_proxy"]

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network
MAX_FORWARDED_HOPS = 20


@lru_cache(maxsize=32)
def _networks(entries: tuple[str, ...]) -> tuple[_Network, ...]:
    nets: list[_Network] = []
    for entry in entries:
        try:
            nets.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            continue  # ignore junk entries rather than trusting them
    return tuple(nets)


def _parse_ip(value: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if not value:
        return None
    v = value.strip().strip('"')
    if v.startswith("[") and "]" in v:  # [v6]:port
        v = v[1 : v.index("]")]
    elif v.count(":") == 1 and "." in v:  # v4:port
        v = v.split(":", 1)[0]
    try:
        ip = ipaddress.ip_address(v)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_trusted_proxy(ip: str | None, settings: Settings) -> bool:
    """True when ``ip`` is inside one of ``TRUSTED_PROXIES``."""
    addr = _parse_ip(ip)
    if addr is None:
        return False
    return any(addr in net for net in _networks(tuple(settings.trusted_proxies)) if addr.version == net.version)


def client_ip_from_scope(scope: Scope, settings: Settings) -> str:
    """Client IP for an ASGI scope (see module docstring). ``"unknown"`` if nothing usable."""
    client = scope.get("client")
    peer = str(client[0]) if client else ""
    if not settings.trusted_proxies or not is_trusted_proxy(peer, settings):
        addr = _parse_ip(peer)
        return str(addr) if addr is not None else (peer or "unknown")
    hops: list[str] = []
    for value in headers_all(scope, b"x-forwarded-for"):
        hops.extend(h.strip() for h in value.split(",") if h.strip())
    hops = hops[-MAX_FORWARDED_HOPS:]
    last_trusted = str(_parse_ip(peer))
    for hop in reversed(hops):
        addr = _parse_ip(hop)
        if addr is None:
            return last_trusted  # malformed hop: nothing left of it can be trusted
        if not is_trusted_proxy(str(addr), settings):
            return str(addr)
        last_trusted = str(addr)
    return last_trusted  # every hop is a trusted proxy: the request originated inside


def client_ip(request: Request, settings: Settings) -> str:
    """Client IP of ``request``, honouring ``X-Forwarded-For`` only from ``TRUSTED_PROXIES``."""
    return client_ip_from_scope(request.scope, settings)
