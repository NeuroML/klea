#!/usr/bin/env python3
"""
Shared FastMCP lifespan helpers.

See ``devdocs/adr/0005-httpx-single-stack.md``.

File: klea_utils/mcp/lifespan.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import httpx
from fastmcp.server.lifespan import lifespan

logger = logging.getLogger(__name__)

#: Connection-pool tuning for the shared HTTP session.  Generous so a busy
#: multi-user MCP server keeps many warm connections instead of paying
#: TCP/TLS handshakes under bursty load.
_SESSION_LIMITS = httpx.Limits(
    max_connections=100, max_keepalive_connections=100, keepalive_expiry=30.0
)


def make_http_session_lifespan(session_key: str = "http_session"):
    """Create a FastMCP lifespan that provides a shared httpx session.

    Tools that need an HTTP session (e.g. klea_utils.mcp.tool_impls.web_fetch)
    read it from ``ctx.lifespan_context[<session_key>]`` in their MCP wrapper.
    Lifespans are composable with the ``|`` operator.

    :param session_key: Lifespan context key under which the session is stored.
    :returns: A FastMCP ``@lifespan``-decorated function.
    """

    @lifespan
    async def _http_session_lifespan(server):
        logger.debug("Creating shared httpx session")
        http_session = httpx.AsyncClient(
            limits=_SESSION_LIMITS,
            timeout=httpx.Timeout(30.0),
            http2=True,
        )
        try:
            yield {session_key: http_session}
        finally:
            logger.debug("Closing shared httpx session")
            await http_session.aclose()

    return _http_session_lifespan
