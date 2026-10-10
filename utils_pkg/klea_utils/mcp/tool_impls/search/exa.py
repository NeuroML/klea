#!/usr/bin/env python3
"""
Exa hosted web-search provider.

Exa works keyless (rate-limited), or authenticated with ``EXA_API_KEY`` via
the ``x-api-key`` header.  The hosted MCP server returns results as formatted
text blocks rather than JSON, so :func:`_parse_exa_text` parses them.

File: klea_utils/mcp/tool_impls/search/exa.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os
import re
from typing import Any

from klea_utils.mcp.tool_impls.search.base import SearchResult
from klea_utils.mcp.tool_impls.search.hosted import (
    _content_text,
    _HostedMCPProvider,
    _snippet,
)

#: Env var that upgrades Exa to its authenticated (higher-limit) tier.
ENV_EXA_API_KEY = "EXA_API_KEY"


def _exa_field(block: str, name: str) -> str:
    """Return the value of a ``Name: value`` line in an Exa text block."""
    match = re.search(rf"^{re.escape(name)}: (.*)$", block, flags=re.MULTILINE)
    return match.group(1).strip() if match else ""


def _parse_exa_text(text: str) -> list[SearchResult]:
    """Parse Exa's formatted text output into normalised results.

    Exa's hosted MCP server returns results as plain text blocks, one per
    result, separated by a newline before each ``Title:`` line::

        Title: ...
        URL: ...
        Published: ...
        Author: ...
        Highlights:
        <excerpt text>
    """
    results: list[SearchResult] = []
    for block in re.split(r"\n(?=Title: )", text):
        block = block.strip()
        if not block:
            continue
        url = _exa_field(block, "URL")
        if not url:
            continue
        published = _exa_field(block, "Published")
        marker = "Highlights:"
        idx = block.find(marker)
        snippet = block[idx + len(marker) :] if idx != -1 else ""
        results.append(
            SearchResult(
                title=_exa_field(block, "Title"),
                url=url,
                snippet=_snippet(snippet),
                published=published if published and published != "N/A" else None,
            )
        )
    return results


class ExaProvider(_HostedMCPProvider):
    """Exa: neural search; text-format results, keyless or ``EXA_API_KEY``."""

    name = "exa"
    url = "https://mcp.exa.ai/mcp"
    tool = "web_search_exa"

    def _headers(self) -> dict[str, str]:
        key = os.environ.get(ENV_EXA_API_KEY, "")
        return {"x-api-key": key} if key else {}

    def _arguments(self, query: str, max_results: int) -> dict[str, Any]:
        return {"query": query, "numResults": max_results, "type": "auto"}

    def _parse(self, result: dict[str, Any]) -> list[SearchResult]:
        return _parse_exa_text(_content_text(result))
