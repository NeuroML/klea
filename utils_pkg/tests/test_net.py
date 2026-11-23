#!/usr/bin/env python3
"""
Tests for shared host helpers.

File: utils_pkg/tests/test_net.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import ipaddress

import pytest
from klea_utils.net import (
    clear_local_interface_address_cache,
    host_header_hostname,
    is_local_machine_host,
    is_loopback_host,
    is_same_machine_peer,
    parse_ip_host,
)


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "[::1]"],
)
def test_loopback_hosts_are_accepted(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host",
    ["192.168.1.10", "10.0.0.1", "example.com", "", None, "testclient"],
)
def test_non_loopback_hosts_are_rejected(host):
    assert not is_loopback_host(host)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", ipaddress.ip_address("127.0.0.1")),
        ("[::1]", ipaddress.ip_address("::1")),
        ("::ffff:127.0.0.1", ipaddress.ip_address("127.0.0.1")),
        ("localhost", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_ip_host(host, expected):
    assert parse_ip_host(host) == expected


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("192.168.0.5:7860", "192.168.0.5"),
        ("localhost:7860", "localhost"),
        ("[::1]:7860", "::1"),
        ("example.com", "example.com"),
        ("", ""),
        (None, ""),
    ],
)
def test_host_header_hostname(header, expected):
    assert host_header_hostname(header) == expected


def test_local_machine_host_includes_configured_lan_ip():
    lan = ipaddress.ip_address("192.168.0.5")
    assert is_local_machine_host("192.168.0.5", local_addresses={lan})
    assert not is_local_machine_host("192.168.0.10", local_addresses={lan})


def test_same_machine_peer_loopback():
    assert is_same_machine_peer("127.0.0.1", "localhost:7860", local_addresses=set())


def test_same_machine_peer_loopback_skips_interface_scan(monkeypatch):
    from klea_utils import net as net_mod

    calls: list[int] = []

    def _boom(**_kwargs):
        calls.append(1)
        return frozenset()

    monkeypatch.setattr(net_mod, "local_interface_addresses", _boom)
    assert is_same_machine_peer("127.0.0.1", "localhost:7860")
    assert is_same_machine_peer("::1", "[::1]:7860")
    assert calls == []


def test_same_machine_peer_hairpin_skips_interface_scan(monkeypatch):
    from klea_utils import net as net_mod

    calls: list[int] = []

    def _boom(**_kwargs):
        calls.append(1)
        return frozenset()

    monkeypatch.setattr(net_mod, "local_interface_addresses", _boom)
    assert is_same_machine_peer("192.168.0.5", "192.168.0.5:7860")
    assert calls == []


def test_same_machine_peer_hairpin_lan_url():
    # Same-machine browser hitting the LAN URL: client IP equals Host IP.
    assert is_same_machine_peer(
        "192.168.0.5",
        "192.168.0.5:7860",
        local_addresses=set(),
    )


def test_same_machine_peer_hairpin_docker_url():
    assert is_same_machine_peer(
        "172.17.0.1",
        "172.17.0.1:7860",
        local_addresses=set(),
    )


def test_remote_wifi_peer_is_not_same_machine():
    # Phone on the same Wi-Fi: different client IP from the server Host.
    lan = ipaddress.ip_address("192.168.0.5")
    assert not is_same_machine_peer(
        "192.168.0.10",
        "192.168.0.5:7860",
        local_addresses={lan},
    )


def test_multi_homed_self_access_uses_any_local_source():
    # URL is the LAN NIC; TCP source is another local NIC (VPN / docker).
    lan = ipaddress.ip_address("192.168.15.121")
    vpn = ipaddress.ip_address("10.0.0.2")
    assert is_same_machine_peer(
        "10.0.0.2",
        "192.168.15.121:7860",
        local_addresses={lan, vpn},
    )


def test_secondary_lan_nic_is_local_when_listed():
    # Secondary LAN IP may be missing from getaddrinfo / default-route scans.
    lan = ipaddress.ip_address("192.168.15.121")
    assert is_local_machine_host("192.168.15.121", local_addresses={lan})
    assert is_same_machine_peer(
        "192.168.15.121",
        "192.168.15.121:7860",
        local_addresses={lan},
    )


def test_adapter_listed_addresses_are_merged(monkeypatch):
    from klea_utils import net as net_mod

    clear_local_interface_address_cache()
    monkeypatch.setattr(
        net_mod,
        "_adapter_listed_addresses",
        lambda: {"192.168.15.121", "172.17.0.1"},
    )
    addresses = net_mod.local_interface_addresses()
    assert ipaddress.ip_address("192.168.15.121") in addresses
    assert ipaddress.ip_address("172.17.0.1") in addresses


def test_local_interface_addresses_are_ttl_cached(monkeypatch):
    from klea_utils import net as net_mod

    clear_local_interface_address_cache()
    calls: list[int] = []
    clock = {"now": 1000.0}

    def _listed() -> set[str]:
        calls.append(1)
        return {"192.168.15.121"}

    monkeypatch.setattr(net_mod, "_adapter_listed_addresses", _listed)
    monkeypatch.setattr(net_mod.time, "monotonic", lambda: clock["now"])

    first = net_mod.local_interface_addresses()
    second = net_mod.local_interface_addresses()
    assert first is second
    assert calls == [1]

    # Still inside the TTL window: no rescan.
    clock["now"] += net_mod._ADAPTER_LIST_TTL_S - 1
    assert net_mod.local_interface_addresses() is first
    assert calls == [1]

    # Past the TTL: DHCP / NIC changes are re-read.
    clock["now"] += 2
    third = net_mod.local_interface_addresses()
    assert third == first
    assert calls == [1, 1]

    clear_local_interface_address_cache()
    fourth = net_mod.local_interface_addresses()
    assert fourth == first
    assert calls == [1, 1, 1]


def test_adapter_listed_addresses_reads_ifaddr(monkeypatch):
    from klea_utils import net as net_mod

    class _IP:
        def __init__(self, ip):
            self.ip = ip

    class _Adapter:
        def __init__(self, ips):
            self.ips = ips

    fake_ifaddr = type(
        "ifaddr",
        (),
        {
            "get_adapters": staticmethod(
                lambda: [
                    _Adapter(
                        [
                            _IP("192.168.15.121"),
                            _IP(("fe80::1", 0, 0)),
                            _IP("172.17.0.1"),
                        ]
                    )
                ]
            )
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "ifaddr", fake_ifaddr)
    found = net_mod._adapter_listed_addresses()
    assert found == {"192.168.15.121", "fe80::1", "172.17.0.1"}
