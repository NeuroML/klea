#!/usr/bin/env python3
"""
Web-search resolver: a provider pool with priority-ordered fallback.

Mirrors the DOI resolver design (ADR-0027): query the keyless providers in
priority order, then any keyed provider whose API key is set, and fall back to
the next provider when one is rate-limited, errors, or returns nothing.

File: klea_utils/mcp/tool_impls/search/resolver.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Callable
from typing import Any

from klea_utils.mcp.tool_impls.search.base import (
    KEYED_SERVICE_ORDER,
    SERVICE_ORDER,
    SearchProvider,
    SearchSession,
)
from klea_utils.mcp.tool_impls.search.brave import BraveProvider
from klea_utils.mcp.tool_impls.search.exa import ExaProvider
from klea_utils.mcp.tool_impls.search.firecrawl import FirecrawlProvider
from klea_utils.mcp.tool_impls.search.parallel import ParallelProvider
from klea_utils.mcp.tool_impls.search.serper import SerperProvider
from klea_utils.mcp.tool_impls.search.tavily import TavilyProvider
from klea_utils.mcp.tool_impls.search.transport import DEFAULT_TIMEOUT

logger = logging.getLogger(__name__)

#: Full provider catalogue keyed by name (keyless and keyed).
PROVIDER_CLASSES: dict[str, Callable[[], SearchProvider]] = {
    "tavily": TavilyProvider,
    "exa": ExaProvider,
    "parallel": ParallelProvider,
    "firecrawl": FirecrawlProvider,
    "brave": BraveProvider,
    "serper": SerperProvider,
}


class WebSearchResolver:
    """Run a query against a pool of web-search providers with fallback.

    The pool defaults to the keyless providers in :data:`SERVICE_ORDER`, plus
    any keyed provider in :data:`KEYED_SERVICE_ORDER` whose API key is set.
    Providers are tried in order; the first one that returns a non-empty
    result set wins.  A provider that raises (or returns nothing) is skipped.
    """

    def __init__(
        self,
        providers: list[SearchProvider] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialise the resolver.

        :param providers: Explicit provider pool (used by tests); when
            ``None`` the default keyless-plus-available-keyed pool is built.
        :param timeout: Per-request timeout in seconds, passed to providers.
        """
        self.timeout = timeout
        self.providers = (
            providers if providers is not None else self._default_providers()
        )

    @staticmethod
    def _default_providers() -> list[SearchProvider]:
        """Build the default pool: keyless first, then available keyed."""
        providers: list[SearchProvider] = [
            PROVIDER_CLASSES[name]() for name in SERVICE_ORDER
        ]
        for name in KEYED_SERVICE_ORDER:
            provider = PROVIDER_CLASSES[name]()
            if provider.is_available():
                providers.append(provider)
        return providers

    def available_providers(self) -> list[SearchProvider]:
        """Return the providers that are usable right now, in priority order."""
        return [p for p in self.providers if p.is_available()]

    async def search(
        self,
        session: SearchSession | None,
        query: str,
        max_results: int,
        providers: list[str] | None = None,
    ) -> dict[str, Any]:
        """Search *query*, falling back across providers.

        :param session: HTTP session from the app lifespan.
        :param query: Free-text search query.
        :param max_results: Maximum number of results to request.
        :param providers: Optional allowlist of provider names to restrict
            the pool to for this call.
        :returns: Dict with ``query``, ``provider`` (the one that served, or
            ``""``), ``results`` (list of result dicts), and ``error``
            (non-empty only when no provider returned anything).
        """
        query = (query or "").strip()
        if not query:
            logger.warning("Empty web search query")
            return {
                "query": query,
                "provider": "",
                "results": [],
                "error": "Empty search query.",
            }

        allow = set(providers) if providers else None
        candidates = [
            p for p in self.available_providers() if allow is None or p.name in allow
        ]
        if not candidates:
            logger.warning("No web search provider available")
            return {
                "query": query,
                "provider": "",
                "results": [],
                "error": "No search provider is available.",
            }

        errors: list[str] = []
        for provider in candidates:
            logger.debug(f"Trying search provider {provider.name}")
            try:
                results = await provider.search(
                    session, query, max_results, self.timeout
                )
            # Any provider failure (transport, protocol, or result parsing)
            # must fall back to the next provider, so catch broadly.
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Search provider {provider.name} failed: {exc}")
                errors.append(f"{provider.name}: {exc}")
                continue
            if results:
                logger.info(
                    f"Served {query!r} via {provider.name} ({len(results)} result(s))"
                )
                return {
                    "query": query,
                    "provider": provider.name,
                    "results": [r.model_dump() for r in results],
                    "error": "",
                }
            logger.debug(f"Provider {provider.name} returned no results")

        detail = f" ({'; '.join(errors)})" if errors else ""
        logger.warning(f"No search results for {query!r}{detail}")
        return {
            "query": query,
            "provider": "",
            "results": [],
            "error": f"No results from any search provider.{detail}",
        }
