#!/usr/bin/env python3
"""
Parallel hosted web-search provider.

Parallel works keyless (rate-limited), or authenticated with
``PARALLEL_API_KEY`` as a Bearer token.  Its endpoint has no result-count
argument; excerpts are server-capped.

File: klea_utils/mcp/tool_impls/search/parallel.py

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

#: Env var that upgrades Parallel to its authenticated (higher-limit) tier.
ENV_PARALLEL_API_KEY = "PARALLEL_API_KEY"


class ParallelProvider(_HostedMCPProvider):
    """Parallel: excerpt search, keyless or ``PARALLEL_API_KEY``."""

    name = "parallel"
    url = "https://search.parallel.ai/mcp"
    tool = "web_search"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(ENV_PARALLEL_API_KEY, "")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _arguments(self, query: str, max_results: int) -> dict[str, Any]:
        return {"objective": query, "search_queries": [query]}

    def _parse(self, result: dict[str, Any]) -> list[SearchResult]:
        data = _json_content(result)
        items = data.get("results") if isinstance(data, dict) else None
        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            excerpts = item.get("excerpts") or []
            snippet = "\n".join(str(e) for e in excerpts if isinstance(e, str))
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=_snippet(snippet),
                    published=item.get("publish_date"),
                )
            )
        return results
