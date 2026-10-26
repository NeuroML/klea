#!/usr/bin/env python3
"""
Host and address helpers shared across Klea packages.

File: utils_pkg/klea_utils/net.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import ipaddress


def is_loopback_host(host: str | None) -> bool:
    """Return whether *host* is a loopback address or ``localhost``.

    Handles IPv4, IPv6, and IPv4-mapped IPv6 addresses (``::ffff:127.0.0.1``),
    which the OS may report for a loopback connection.

    :param host: Peer host name or address, or ``None``.
    :returns: ``True`` when the host refers to the local machine.
    """
    if not host:
        return False
    candidate = host.strip().strip("[]")
    if candidate.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback
