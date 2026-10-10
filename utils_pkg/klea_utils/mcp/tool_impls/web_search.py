#!/usr/bin/env python3
"""
Web search implementation for Klea MCP tools.

A thin, framework-agnostic wrapper over
:class:`~klea_utils.mcp.tool_impls.search.resolver.WebSearchResolver`.  The
MCP tool wrapper supplies ``session`` from the FastMCP lifespan context (see
``klea_utils.mcp.lifespan``).

File: klea_utils/mcp/tool_impls/web_search.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from klea_utils.mcp.tool_impls.search.base import SearchSession
from klea_utils.mcp.tool_impls.search.resolver import WebSearchResolver

logger = logging.getLogger(__name__)

#: Default number of results requested from the provider pool.
DEFAULT_MAX_RESULTS = 8
#: Maximum results a caller may request (matches the hosted providers' caps).
MAX_MAX_RESULTS = 20


async def web_search(
    session: SearchSession | None,
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    providers: list[str] | None = None,
) -> dict[str, Any]:
    """Search the web and return normalised results.

    Queries the keyless provider pool (Tavily, Exa, Parallel, Firecrawl) and,
    when their API keys are present, the keyed providers (Brave, Serper),
    falling back across providers so a rate limit or outage does not fail the
    search.  Never raises for expected failure; failures are reported via a
    non-empty ``error`` field.

    :param session: HTTP session from the app lifespan; ``None`` when no
        session is available.
    :param query: Free-text search query.
    :param max_results: Maximum number of results to request (1-20).
    :param providers: Optional allowlist of provider names to restrict the
        pool to for this call.
    :returns: Dict with ``query``, ``provider`` (the one that served, or
        ``""``), ``results`` (list of ``{title, url, snippet, published,
        score}`` dicts), and ``error``.
    """
    logger.debug(f"web_search\n{query = }\n{max_results = }\n{providers = }")
    resolver = WebSearchResolver()
    return await resolver.search(session, query, max_results, providers=providers)
