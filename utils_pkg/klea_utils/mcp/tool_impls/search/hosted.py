#!/usr/bin/env python3
"""
Shared scaffolding for the hosted web-search providers.

Holds the :class:`_HostedMCPProvider` base class and the result-normalisation
helpers common to the JSON- and text-format providers.

File: klea_utils/mcp/tool_impls/search/hosted.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from typing import Any

from klea_utils.mcp.tool_impls.search.base import SearchResult, SearchSession
from klea_utils.mcp.tool_impls.search.errors import SearchProviderError
from klea_utils.mcp.tool_impls.search.transport import DEFAULT_TIMEOUT, _mcp_call

logger = logging.getLogger(__name__)

#: Snippets are capped so a verbose provider cannot flood the model context.
MAX_SNIPPET_CHARS = 2000


def _snippet(text: str) -> str:
    """Return *text* stripped and capped to :data:`MAX_SNIPPET_CHARS`."""
    text = (text or "").strip()
    if len(text) > MAX_SNIPPET_CHARS:
        return text[:MAX_SNIPPET_CHARS]
    return text


def _content_text(result: dict[str, Any]) -> str:
    """Join the ``text`` blocks of an MCP ``CallToolResult``."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def _json_content(result: dict[str, Any]) -> Any:
    """Parse the (JSON string) text content of an MCP result.

    :raises SearchProviderError: When the content is not valid JSON.
    """
    text = _content_text(result)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SearchProviderError(f"Provider returned non-JSON content: {exc}") from exc


class _HostedMCPProvider:
    """Base class for a keyless-or-keyed hosted MCP search provider.

    Subclasses declare ``name``/``url``/``tool`` and override
    :meth:`_headers`, :meth:`_arguments`, and :meth:`_parse`.  The default
    :meth:`is_available` is always true: these providers work without an API
    key, and a key (when present) only raises limits.
    """

    name: str = ""
    url: str = ""
    tool: str = ""

    def is_available(self) -> bool:
        """Return whether this provider can be used right now."""
        return True

    def _headers(self) -> dict[str, str]:
        """Return provider auth headers (empty for the keyless tier)."""
        return {}

    def _arguments(self, query: str, max_results: int) -> dict[str, Any]:
        """Return the tool-call arguments for *query*."""
        raise NotImplementedError

    def _parse(self, result: dict[str, Any]) -> list[SearchResult]:
        """Normalise an MCP result into :class:`SearchResult` records."""
        raise NotImplementedError

    async def search(
        self,
        session: SearchSession | None,
        query: str,
        max_results: int,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> list[SearchResult]:
        """Run *query* via the hosted MCP endpoint and normalise results."""
        logger.debug(f"Searching via {self.name}\n{query = }\n{max_results = }")
        result = await _mcp_call(
            session,
            self.url,
            self.tool,
            self._arguments(query, max_results),
            headers=self._headers(),
            timeout=timeout,
        )
        results = self._parse(result)
        logger.debug(f"{self.name} returned {len(results)} results")
        return results
