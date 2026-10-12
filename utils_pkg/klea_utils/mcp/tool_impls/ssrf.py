#!/usr/bin/env python3
"""
SSRF (Server-Side Request Forgery) protection for outbound HTTP tools.

File: klea_utils/mcp/tool_impls/ssrf.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Maximum time to wait for DNS resolution when called from async context
_SSRF_DNS_TIMEOUT = 5.0

#: Maximum redirects followed by ``web_fetch``/``download_file`` (per-hop SSRF-checked)
_MAX_REDIRECTS = 5


def is_private_or_reserved(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Return True for addresses an SSRF guard should refuse to fetch.

    Blocks loopback, private (RFC1918/ULA), link-local (incl. the cloud
    metadata address 169.254.169.254), reserved, and multicast ranges.

    :param ip: Address to classify.
    """
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def check_ssrf(url: str) -> str | None:
    """Return an error message if *url* resolves to a private/internal host.

    Resolves the hostname and rejects the request when any resolved address
    is private, loopback, link-local, reserved, or multicast.  Returns
    ``None`` when the request is allowed.

    .. note::
        This is the synchronous, blocking variant (uses ``socket.getaddrinfo``
        directly). Call :func:`check_ssrf_async` from async code to avoid
        stalling the event loop.

    :param url: Absolute URL to check.
    :returns: An error message describing the denial, or ``None`` when the
        URL is allowed.
    """
    host = urlparse(url).hostname
    if not host:
        return "URL has no host."
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        logger.warning(f"Could not resolve host {host}: {exc}")
        return f"Could not resolve host {host}: {exc}"
    logger.debug(f"Resolved {host} -> {[info[4][0] for info in infos]}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if is_private_or_reserved(ip):
            logger.warning(f"SSRF guard: {host} resolves to {ip} (blocked)")
            return f"Blocked request to private/internal address: {ip}"
    return None


async def check_ssrf_async(url: str, timeout: float = _SSRF_DNS_TIMEOUT) -> str | None:
    """Async wrapper around :func:`check_ssrf` that offloads DNS to a thread.

    ``socket.getaddrinfo`` is blocking; running it in ``asyncio.to_thread``
    keeps the event loop responsive and adds a timeout.

    :param url: Absolute URL to check.
    :param timeout: Seconds to wait for DNS before returning a timeout error.
    :returns: Error message or ``None`` when allowed.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(check_ssrf, url),
            timeout=timeout,
        )
    except TimeoutError:
        host = urlparse(url).hostname or url
        logger.warning(f"SSRF DNS timeout for {host}")
        return f"DNS timeout for {host}"
