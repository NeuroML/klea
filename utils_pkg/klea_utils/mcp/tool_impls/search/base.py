#!/usr/bin/env python3
"""
Shared types and the provider protocol for web search.

File: klea_utils/mcp/tool_impls/search/base.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, Protocol

from pydantic import BaseModel

logger = logging.getLogger(__name__)

#: Default provider priority: the keyless hosted services, best-quality
#: first.  Queries are tried in this order with fallback to the next on a
#: rate limit or error (see :class:`~klea_utils.mcp.tool_impls.search.resolver`).
SERVICE_ORDER = ("tavily", "exa", "parallel", "firecrawl")

#: Providers that require an API key.  They are appended after the keyless
#: pool only when their key is present in the environment.
KEYED_SERVICE_ORDER = ("brave", "serper")


class SearchSession(Protocol):
    """Minimal HTTP interface the search providers need.

    Kept structural and distinct from the broader MCP ``SessionLike`` so
    provider adapters and their tests only need what they use: ``post`` for
    the hosted MCP JSON-RPC endpoints and the Serper REST API, and ``get``
    for the Brave REST API.  :class:`httpx.AsyncClient` satisfies both.
    """

    async def get(
        self,
        url: str,
        *,
        params: Any | None = None,
        headers: Any | None = None,
        timeout: Any = None,
        follow_redirects: bool = False,
    ) -> Any: ...

    async def post(
        self,
        url: str,
        *,
        json: Any | None = None,
        headers: Any | None = None,
        timeout: Any = None,
        follow_redirects: bool = False,
    ) -> Any: ...


class SearchResult(BaseModel):
    """A single normalised web search result.

    Providers return different shapes (Tavily/Parallel/Firecrawl JSON, Exa
    formatted text); each provider adapter normalises to this record so the
    model always sees one consistent schema.
    """

    title: str = ""
    url: str = ""
    snippet: str = ""
    published: str | None = None
    score: float | None = None


class SearchProvider(Protocol):
    """Protocol for a web search backend.

    The per-provider modules under :mod:`klea_utils.mcp.tool_impls.search`
    implement this; the resolver uses only ``name``, :meth:`is_available`,
    and :meth:`search`.
    """

    #: Stable provider identifier, e.g. ``"tavily"``.
    name: str

    def is_available(self) -> bool:
        """Return whether this provider can be used right now."""
        ...

    async def search(
        self,
        session: SearchSession | None,
        query: str,
        max_results: int,
        timeout: float,
    ) -> list["SearchResult"]:
        """Run *query* and return normalised results.

        Implementations may raise on any transport or provider error; the
        resolver catches the exception and falls back to the next provider.

        :param session: HTTP session from the app lifespan (may be ``None``).
        :param query: Free-text search query.
        :param max_results: Maximum number of results to return.
        :param timeout: Per-request timeout in seconds.
        :returns: Normalised results, possibly empty.
        """
        ...
