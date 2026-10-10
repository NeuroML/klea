#!/usr/bin/env python3
"""
Firecrawl hosted web-search provider.

Firecrawl's search works keyless (rate-limited, search/scrape/parse only), or
authenticated with ``FIRECRAWL_API_KEY`` as a Bearer token (full toolset and
higher limits).

File: klea_utils/mcp/tool_impls/search/firecrawl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os
from typing import Any

from klea_utils.mcp.tool_impls.search.base import SearchResult
from klea_utils.mcp.tool_impls.search.hosted import (
    _HostedMCPProvider,
    _json_content,
    _snippet,
)

#: Env var that upgrades Firecrawl to its authenticated (higher-limit) tier.
ENV_FIRECRAWL_API_KEY = "FIRECRAWL_API_KEY"


class FirecrawlProvider(_HostedMCPProvider):
    """Firecrawl: web search, keyless or ``FIRECRAWL_API_KEY``."""

    name = "firecrawl"
    url = "https://mcp.firecrawl.dev/v2/mcp"
    tool = "firecrawl_search"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(ENV_FIRECRAWL_API_KEY, "")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _arguments(self, query: str, max_results: int) -> dict[str, Any]:
        return {"query": query, "limit": max_results}

    def _parse(self, result: dict[str, Any]) -> list[SearchResult]:
        data = _json_content(result)
        payload = data.get("data") if isinstance(data, dict) else None
        items = payload.get("web") if isinstance(payload, dict) else None
        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=_snippet(str(item.get("description") or "")),
                )
            )
        return results
