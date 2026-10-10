#!/usr/bin/env python3
"""
Brave Search REST provider.

Brave runs its own independent index and requires an API key
(``BRAVE_API_KEY``); it is therefore only registered in the pool when the key
is present.  Uses the plain Web Search endpoint (links + snippets).

File: klea_utils/mcp/tool_impls/search/brave.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os

from klea_utils.mcp.tool_impls.search.base import SearchResult, SearchSession
from klea_utils.mcp.tool_impls.search.hosted import _snippet
from klea_utils.mcp.tool_impls.search.transport import DEFAULT_TIMEOUT, _rest_get_json

#: API key required to use the Brave Search API.
ENV_BRAVE_API_KEY = "BRAVE_API_KEY"


class BraveProvider:
    """Brave Search: independent index; keyed REST (``BRAVE_API_KEY``)."""

    name = "brave"
    url = "https://api.search.brave.com/res/v1/web/search"

    def is_available(self) -> bool:
        """Return whether ``BRAVE_API_KEY`` is set."""
        return bool(os.environ.get(ENV_BRAVE_API_KEY, ""))

    def _headers(self) -> dict[str, str]:
        return {"X-Subscription-Token": os.environ.get(ENV_BRAVE_API_KEY, "")}

    async def search(
        self,
        session: SearchSession | None,
        query: str,
        max_results: int,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[SearchResult]:
        """Run *query* via the Brave Web Search API."""
        data = await _rest_get_json(
            session,
            self.url,
            params={"q": query, "count": max_results},
            headers=self._headers(),
            timeout=timeout,
        )
        web = data.get("web") if isinstance(data, dict) else None
        items = web.get("results") if isinstance(web, dict) else None
        results: list[SearchResult] = []
        for item in items or []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item["url"]),
                    snippet=_snippet(str(item.get("description") or "")),
                    published=item.get("page_age") or item.get("age"),
                )
            )
        return results
