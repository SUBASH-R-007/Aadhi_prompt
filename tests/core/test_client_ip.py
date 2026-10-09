"""Client IP resolution with trusted proxies."""

from __future__ import annotations

import pytest

from aadhi.config import Settings
from aadhi.security.client_ip import client_ip_from_scope, is_trusted_proxy


def _scope(peer: str | None, *xff: str) -> dict:
    return {
        "type": "http",
        "client": (peer, 1234) if peer is not None else None,
        "headers": [(b"x-forwarded-for", v.encode()) for v in xff],
    }


def test_no_trusted_proxies_ignores_header():
    s = Settings(trusted_proxies="")
    assert client_ip_from_scope(_scope("203.0.113.9", "1.1.1.1"), s) == "203.0.113.9"


def test_untrusted_peer_ignores_header():
    s = Settings(trusted_proxies="10.0.0.1")
    assert client_ip_from_scope(_scope("203.0.113.9", "1.1.1.1"), s) == "203.0.113.9"


def test_trusted_proxy_chain_right_to_left():
    s = Settings(trusted_proxies="10.0.0.0/8,127.0.0.1")
    # "6.6.6.6" was supplied by the client (spoofed); the proxies appended the real client.
    scope = _scope("10.0.0.2", "6.6.6.6, 198.51.100.7, 10.0.0.5")
    assert client_ip_from_scope(scope, s) == "198.51.100.7"
    assert client_ip_from_scope(_scope("127.0.0.1", "198.51.100.8"), s) == "198.51.100.8"


def test_multiple_header_lines_and_ports():
    s = Settings(trusted_proxies="10.0.0.1")
    assert client_ip_from_scope(_scope("10.0.0.1", "6.6.6.6", "198.51.100.7:5555"), s) == "198.51.100.7"
    assert client_ip_from_scope(_scope("10.0.0.1", "[2001:db8::1]:443"), s) == "2001:db8::1"


def test_malformed_hop_stops_trust():
    s = Settings(trusted_proxies="10.0.0.0/8")
    assert client_ip_from_scope(_scope("10.0.0.2", "6.6.6.6, garbage, 10.0.0.5"), s) == "10.0.0.5"


def test_all_hops_trusted():
    s = Settings(trusted_proxies="10.0.0.0/8")
    assert client_ip_from_scope(_scope("10.0.0.2", "10.0.0.9, 10.0.0.5"), s) == "10.0.0.9"
    assert client_ip_from_scope(_scope("10.0.0.2"), s) == "10.0.0.2"


def test_ipv4_mapped_and_odd_peers():
    s = Settings(trusted_proxies="127.0.0.1")
    assert is_trusted_proxy("::ffff:127.0.0.1", s)
    assert client_ip_from_scope(_scope("testclient"), s) == "testclient"
    assert client_ip_from_scope(_scope(None), s) == "unknown"


@pytest.mark.parametrize("entry", ["not-an-ip", "300.1.1.1/8"])
def test_junk_trusted_entries_are_ignored(entry):
    s = Settings(trusted_proxies=entry)
    assert client_ip_from_scope(_scope("10.0.0.2", "1.1.1.1"), s) == "10.0.0.2"
