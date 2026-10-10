#!/usr/bin/env python3
"""
Tavily hosted web-search provider.

Tavily works keyless (rate-limited) via the ``X-Tavily-Access-Mode: keyless``
header, or authenticated with ``TAVILY_API_KEY`` for higher limits; a key
takes precedence over keyless mode.

File: klea_utils/mcp/tool_impls/search/tavily.py

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

#: Env var that upgrades Tavily to its authenticated (higher-limit) tier.
ENV_TAVILY_API_KEY = "TAVILY_API_KEY"


class TavilyProvider(_HostedMCPProvider):
    """Tavily: LLM-optimised ranked results, keyless or ``TAVILY_API_KEY``."""

    name = "tavily"
    url = "https://mcp.tavily.com/mcp/"
    tool = "tavily_search"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(ENV_TAVILY_API_KEY, "")
        if key:
            return {"Authorization": f"Bearer {key}"}
        return {"X-Tavily-Access-Mode": "keyless"}

    def _arguments(self, query: str, max_results: int) -> dict[str, Any]:
        return {"query": query, "max_results": max_results}

    def _parse(self, result: dict[str, Any]) -> list[SearchResult]:
        data = _json_content(result)
        items = data.get("results") if isinstance(data, dict) else None
        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            score = item.get("score")
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=_snippet(str(item.get("content") or "")),
                    score=score if isinstance(score, (int, float)) else None,
                )
            )
        return results
