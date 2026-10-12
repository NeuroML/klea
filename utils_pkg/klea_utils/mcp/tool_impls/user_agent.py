#!/usr/bin/env python3
"""
Honest Klea User-Agent strings for the shared MCP tool implementations.

Each tool family identifies itself with a distinct prefix (``klea-web-fetch/``,
``klea-paper-search/``), so a server operator can tell which Klea subsystem is
calling and polite-pool/abuse handling stays attributed.  The Klea version is
read once from package metadata.

File: klea_utils/mcp/tool_impls/user_agent.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

logger = logging.getLogger(__name__)

#: Cached klea_utils version tag (``dev`` when metadata is unavailable).
_VERSION_CACHE: str | None = None
#: Cached User-Agent string per prefix.
_UA_CACHE: dict[str, str] = {}


def _klea_version() -> str:
    """Return the klea_utils version, or ``dev`` when metadata is unavailable.

    Reads the installed ``klea_utils`` version from package metadata; falls
    back to ``dev`` in an editable checkout without installed distribution
    metadata.
    """
    global _VERSION_CACHE
    if _VERSION_CACHE is None:
        try:
            from importlib.metadata import PackageNotFoundError, version

            version_str = version("klea_utils")
        except PackageNotFoundError:
            version_str = ""
        _VERSION_CACHE = version_str or "dev"
        logger.debug(f"Resolved klea_utils version tag {_VERSION_CACHE!r}")
    return _VERSION_CACHE


def klea_user_agent(prefix: str) -> str:
    """Return an honest ``<prefix><klea_utils version>`` User-Agent string.

    :param prefix: Tool-family prefix, e.g. ``"klea-web-fetch/"``.  Keep the
        trailing slash.
    :returns: The User-Agent string, cached per prefix.
    """
    ua = _UA_CACHE.get(prefix)
    if ua is None:
        ua = f"{prefix}{_klea_version()}"
        _UA_CACHE[prefix] = ua
        logger.debug(f"Using User-Agent {ua!r} for prefix {prefix!r}")
    return ua
