#!/usr/bin/env python3
"""
Serper.dev Google-SERP provider.

Serper proxies Google results and requires an API key (``SERPER_API_KEY``);
it is therefore only registered in the pool when the key is present.  Results
are raw Google SERP entries (title, link, snippet).

File: klea_utils/mcp/tool_impls/search/serper.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os

from klea_utils.mcp.tool_impls.search.base import SearchResult, SearchSession
from klea_utils.mcp.tool_impls.search.hosted import _snippet
from klea_utils.mcp.tool_impls.search.transport import DEFAULT_TIMEOUT, _rest_post_json

#: API key required to use the Serper.dev API.
ENV_SERPER_API_KEY = "SERPER_API_KEY"


class SerperProvider:
    """Serper.dev: raw Google SERP results; keyed REST (``SERPER_API_KEY``)."""

    name = "serper"
    url = "https://google.serper.dev/search"

    def is_available(self) -> bool:
        """Return whether ``SERPER_API_KEY`` is set."""
        return bool(os.environ.get(ENV_SERPER_API_KEY, ""))

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": os.environ.get(ENV_SERPER_API_KEY, "")}

    async def search(
        self,
        session: SearchSession | None,
        query: str,
        max_results: int,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[SearchResult]:
        """Run *query* via the Serper.dev Google SERP API."""
        data = await _rest_post_json(
            session,
            self.url,
            {"q": query, "num": max_results},
            headers=self._headers(),
            timeout=timeout,
        )
        items = data.get("organic") if isinstance(data, dict) else None
        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("link"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["link"]),
                    snippet=_snippet(str(item.get("snippet") or "")),
                    published=item.get("date"),
                )
            )
        return results
