#!/usr/bin/env python3
"""
Host and address helpers shared across Klea packages.

File: utils_pkg/klea_utils/net.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from collections.abc import Iterable

logger = logging.getLogger(__name__)

#: How long a cached interface inventory stays trusted (DHCP / NIC churn).
_ADAPTER_LIST_TTL_S = 60.0

#: Cached local interface inventory and the monotonic time it was taken.
_LOCAL_INTERFACE_CACHE: (
    frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] | None
) = None
_LOCAL_INTERFACE_CACHE_AT: float | None = None


def is_loopback_host(host: str | None) -> bool:
    """Return whether *host* is a loopback address or ``localhost``.

    Handles IPv4, IPv6, and IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``),
    which the OS may report for a loopback connection.

    :param host: Peer host name or address, or ``None``.
    :returns: ``True`` when the host refers to the local machine via loopback.
    """
    address = parse_ip_host(host)
    if address is not None:
        return address.is_loopback
    if not host:
        return False
    return host.strip().strip("[]").lower() == "localhost"


def parse_ip_host(
    host: str | None,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse *host* as an IP address, or return ``None``.

    Accepts bracketed IPv6 (``[::1]``) and IPv4-mapped IPv6 forms.  Hostnames
    such as ``localhost`` return ``None`` (use :func:`is_loopback_host` for
    those).  Zone indices (``fe80::1%lo0``) are stripped.

    :param host: Peer host name or address, or ``None``.
    :returns: The parsed address, or ``None`` when *host* is not an IP.
    """
    if not host:
        return None
    candidate = host.strip().strip("[]")
    if "%" in candidate:
        candidate = candidate.split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def host_header_hostname(host_header: str | None) -> str:
    """Return the hostname part of an HTTP ``Host`` header value.

    Strips a trailing port for ``ipv4:port`` and ``[ipv6]:port`` forms.

    :param host_header: Raw ``Host`` header, or ``None``.
    :returns: The hostname, or ``""`` when missing.
    """
    if not host_header:
        return ""
    value = host_header.strip()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[1:end]
        return value.strip("[]")
    if value.count(":") == 1:
        return value.rsplit(":", 1)[0]
    return value


def _adapter_listed_addresses() -> set[str]:
    """Return interface address strings via ``ifaddr`` (best effort).

    Same library NiceGUI uses in ``welcome._get_all_ips`` for access URLs.
    Soft-imports so callers without the web extra still work (they keep the
    ``getaddrinfo`` / UDP fallbacks only).
    """
    try:
        import ifaddr
    except ImportError:
        logger.debug("ifaddr not installed; skipping adapter scan")
        return set()
    found: set[str] = set()
    try:
        for adapter in ifaddr.get_adapters():
            for ip in adapter.ips:
                # IPv4 is a string; IPv6 is ``(addr, flowinfo, scope_id)``.
                raw = ip.ip[0] if isinstance(ip.ip, tuple) else ip.ip
                if raw:
                    found.add(str(raw))
    except OSError as exc:
        logger.debug("ifaddr adapter scan failed: %s", exc)
    return found


def clear_local_interface_address_cache() -> None:
    """Drop the cached local interface inventory (tests / rare refresh)."""
    global _LOCAL_INTERFACE_CACHE, _LOCAL_INTERFACE_CACHE_AT
    _LOCAL_INTERFACE_CACHE = None
    _LOCAL_INTERFACE_CACHE_AT = None


def local_interface_addresses(
    *,
    extra_hosts: Iterable[str] | None = None,
) -> frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Return IP addresses currently assigned to this machine (best effort).

    Combines an ``ifaddr`` adapter listing (same approach as NiceGUI's welcome
    URLs), ``getaddrinfo(hostname)``, the UDP "outbound" address trick, and any
    *extra_hosts* (useful in tests).  Loopback addresses are always included.

    Results without *extra_hosts* are cached for :data:`_ADAPTER_LIST_TTL_S` so
    page loads do not re-scan every request, while DHCP / NIC changes are not
    trusted forever.  Call :func:`clear_local_interface_address_cache` to
    refresh immediately.

    :param extra_hosts: Optional host strings to include (tests / callers).
    :returns: The set of local interface addresses.
    """
    global _LOCAL_INTERFACE_CACHE, _LOCAL_INTERFACE_CACHE_AT
    extras = tuple(extra_hosts or ())
    now = time.monotonic()
    if (
        not extras
        and _LOCAL_INTERFACE_CACHE is not None
        and _LOCAL_INTERFACE_CACHE_AT is not None
        and (now - _LOCAL_INTERFACE_CACHE_AT) < _ADAPTER_LIST_TTL_S
    ):
        return _LOCAL_INTERFACE_CACHE

    found: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()

    def _add(raw: str | None) -> None:
        address = parse_ip_host(raw)
        if address is not None:
            found.add(address)

    _add("127.0.0.1")
    _add("::1")
    for raw in extras:
        _add(raw)
    for raw in _adapter_listed_addresses():
        _add(raw)

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            _add(info[4][0])
    except OSError:
        pass

    for family, probe in (
        (socket.AF_INET, "1.1.1.1"),
        (socket.AF_INET6, "2606:4700:4700::1111"),
    ):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as sock:
                sock.connect((probe, 80))
                _add(sock.getsockname()[0])
        except OSError:
            continue

    result = frozenset(found)
    if not extras:
        _LOCAL_INTERFACE_CACHE = result
        _LOCAL_INTERFACE_CACHE_AT = now
    return result


def is_local_machine_host(
    host: str | None,
    *,
    local_addresses: (
        Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address] | None
    ) = None,
) -> bool:
    """Return whether *host* is an address of this machine.

    True for loopback / ``localhost``, and for any IP currently assigned to a
    local interface (LAN, docker bridge, VPN, …).

    :param host: Peer host name or address, or ``None``.
    :param local_addresses: Optional precomputed local address set (tests).
    :returns: ``True`` when *host* refers to this machine.
    """
    if is_loopback_host(host):
        return True
    address = parse_ip_host(host)
    if address is None:
        return False
    addresses = (
        frozenset(local_addresses)
        if local_addresses is not None
        else local_interface_addresses()
    )
    return address in addresses


def is_same_machine_peer(
    client_host: str | None,
    request_host_header: str | None,
    *,
    local_addresses: (
        Iterable[ipaddress.IPv4Address | ipaddress.IPv6Address] | None
    ) = None,
) -> bool:
    """Return whether a request peer is this machine.

    True when:

    * the client host is loopback / a local interface address, or
    * the client IP equals the ``Host`` header IP (same-machine hairpin to a
      LAN or docker URL), or
    * the ``Host`` header IP is itself a local interface address and the
      client is loopback or any other local interface (source NIC may differ
      from the URL NIC on multi-homed hosts).

    A phone on the same Wi-Fi keeps a different client IP that is not on this
    machine, so it stays remote.

    :param client_host: ``request.client.host``.
    :param request_host_header: Raw ``Host`` header value.
    :param local_addresses: Optional precomputed local address set (tests).
    :returns: ``True`` when the peer is this machine.
    """
    # Loopback clients never need a NIC scan (covers plain localhost).
    if is_loopback_host(client_host):
        return True
    client = parse_ip_host(client_host)
    advertised = parse_ip_host(host_header_hostname(request_host_header))
    # Hairpin: browser and Host share the same LAN/docker IP.
    if client is not None and advertised is not None and client == advertised:
        return True
    addresses = (
        frozenset(local_addresses)
        if local_addresses is not None
        else local_interface_addresses()
    )
    # Client is on a local NIC, or multi-homed self-access (source NIC differs
    # from the URL NIC): both reduce to "client IP is local".
    return client is not None and client in addresses
