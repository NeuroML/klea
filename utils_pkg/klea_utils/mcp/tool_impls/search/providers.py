#!/usr/bin/env python3
"""
Hosted MCP transport and provider adapters for web search.

The JSON-RPC ``tools/call`` is sent with a thin POST helper
(:func:`_mcp_call`) rather than ``fastmcp.Client`` on purpose:

* The hosted keyless endpoints (Tavily/Exa/Parallel/Firecrawl) accept a
  direct ``tools/call`` without an ``initialize`` handshake, so a client
  would only add a round-trip per search.
* This module stays framework-agnostic and reusable, and reuses the app's
  shared lifespan ``httpx`` session (AGENTS HTTP conventions) instead of
  opening a second connection pool that ``fastmcp.Client`` would own.
* Retry/backoff and the honest User-Agent would still have to be supplied
  separately with a client, so the shared retryer (:func:`_make_retryer_httpx`)
  is used here regardless.

File: klea_utils/mcp/tool_impls/search/providers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from typing import Any

import httpx

from klea_utils.api.utils import _make_retryer_httpx
from klea_utils.mcp.tool_impls.search.base import (
    SearchProviderError,
    SearchSession,
)

logger = logging.getLogger(__name__)

#: Default per-request timeout for a provider call, in seconds.
DEFAULT_TIMEOUT = 30.0
#: Default attempts for transient failures (timeouts, 5xx, 429).
DEFAULT_RETRIES = 3

#: Honest client identity sent to search providers.  Providers do not
#: require a User-Agent (unlike page fetching), but a stable, descriptive
#: one aids provider-side abuse handling and avoids httpx's default
#: anonymous ``python-httpx/<version>`` string.
_UA_PREFIX = "klea-web-search/"
_user_agent_cache: str | None = None


def _user_agent() -> str:
    """Return the honest web-search User-Agent, versioned with klea_utils.

    Reads the installed ``klea_utils`` version from package metadata; falls
    back to ``dev`` when metadata is unavailable (e.g. an editable checkout
    without installed distribution metadata).
    """
    global _user_agent_cache
    if _user_agent_cache is None:
        try:
            from importlib.metadata import PackageNotFoundError, version

            version_str = version("klea_utils")
        except PackageNotFoundError:
            version_str = ""
        _user_agent_cache = f"{_UA_PREFIX}{version_str or 'dev'}"
    return _user_agent_cache


def _iter_sse_payloads(text: str) -> list[Any]:
    """Return the JSON objects carried by ``data:`` frames in *text*.

    Streamable-HTTP MCP servers may reply as Server-Sent Events; the
    JSON-RPC response is the payload of the ``data:`` line(s).  Anything
    that is not valid JSON (a keep-alive comment, a ``[DONE]`` marker) is
    skipped.
    """
    payloads: list[Any] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payloads.append(json.loads(data))
        except ValueError:
            logger.debug(f"Skipping non-JSON SSE data frame: {data[:80]!r}")
    return payloads


def _parse_mcp_response(text: str, content_type: str, url: str) -> dict[str, Any]:
    """Return the JSON-RPC ``result`` object from an MCP HTTP response.

    Handles both a plain JSON body and an SSE stream.  Raises
    :class:`~klea_utils.mcp.tool_impls.search.base.SearchProviderError` when the body is
    unreadable or carries a JSON-RPC ``error``.

    :param text: Raw response body.
    :param content_type: Response ``content-type`` header.
    :param url: Request URL (for error messages).
    :returns: The JSON-RPC ``result`` mapping.
    """
    if "text/event-stream" in content_type.lower():
        payloads = _iter_sse_payloads(text)
    else:
        try:
            parsed = json.loads(text)
        except ValueError as exc:
            raise SearchProviderError(f"Invalid JSON from {url}: {exc}") from exc
        payloads = parsed if isinstance(parsed, list) else [parsed]

    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        if payload.get("error"):
            raise SearchProviderError(f"MCP error from {url}: {payload['error']}")
        result = payload.get("result")
        if isinstance(result, dict):
            return result

    raise SearchProviderError(f"No MCP result in response from {url}")


async def _mcp_call(
    session: SearchSession | None,
    url: str,
    tool: str,
    arguments: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = DEFAULT_RETRIES,
) -> dict[str, Any]:
    """Call *tool* on a hosted MCP server and return its JSON-RPC result.

    Sends a ``tools/call`` request over Streamable HTTP (the transport the
    keyless Tavily/Exa/Parallel/Firecrawl endpoints expose).  Transient
    failures (timeouts, 5xx, 429) are retried with exponential backoff via
    the shared retryer; other 4xx errors are surfaced as
    :class:`~klea_utils.mcp.tool_impls.search.base.SearchProviderError`.  See the module
    docstring for why this is a raw POST rather than ``fastmcp.Client``.

    :param session: HTTP session from the app lifespan; ``None`` when no
        session is available.
    :param url: Hosted MCP endpoint URL.
    :param tool: MCP tool name to call.
    :param arguments: Tool arguments.
    :param headers: Additional request headers (e.g. provider auth) merged
        over the Klea defaults.
    :param timeout: Per-request timeout in seconds.
    :param retries: Number of attempts for transient failures.
    :returns: The JSON-RPC ``result`` mapping.
    :raises SearchProviderError: On any transport or protocol failure.
    """
    if session is None:
        raise SearchProviderError("HTTP session not initialized")

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments},
    }
    merged_headers = {
        "User-Agent": _user_agent(),
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if headers:
        merged_headers.update(headers)

    logger.debug(f"MCP call\n{url = }\n{tool = }\n{arguments = }")

    async def _do_post() -> httpx.Response:
        response = await session.post(
            url,
            json=body,
            headers=merged_headers,
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
        )
        response.raise_for_status()
        return response

    retryer = _make_retryer_httpx(attempts=retries)
    try:
        response = await retryer(_do_post)
    except httpx.HTTPStatusError as exc:
        raise SearchProviderError(
            f"HTTP {exc.response.status_code} from {url}"
        ) from exc
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SearchProviderError(f"Request to {url} failed: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    logger.debug(f"MCP response\n{url = }\n{content_type = }")
    return _parse_mcp_response(response.text, content_type, url)
