#!/usr/bin/env python3
"""
Shared HTTP helpers for the paper search sources.

File: klea_utils/mcp/tool_impls/papers/http.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
import time
from typing import Any

import httpx

from klea_utils.mcp.tool_impls.session import SessionLike
from klea_utils.mcp.tool_impls.web_fetch import _honest_user_agent

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = httpx.Timeout(20.0)


class PaperSourceError(Exception):
    """Raised when a paper source cannot be queried (HTTP error, 429, bad body).

    The search falls back to the next source when this is raised.
    """


class Throttle:
    """Keep at least ``min_interval`` seconds between requests to one service.

    Used for services that ask for a gap between requests (arXiv, Semantic
    Scholar and PubMed); each module explains its own gap.
    """

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        wait = self._last + self.min_interval - time.monotonic()
        if wait > 0:
            logger.debug(f"Throttling for {wait:.2f}s")
            await asyncio.sleep(wait)

    async def __aexit__(self, *exc) -> None:
        self._last = time.monotonic()
        self._lock.release()


async def _get(
    session: SessionLike | None,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET *url* once and return the response.

    There is no retry: a rate-limited or failing source is skipped in favour
    of the next one, which is quicker than backing off on the same service.

    :raises PaperSourceError: on any failure, including HTTP 429.
    """
    if session is None:
        raise PaperSourceError("HTTP session not initialized")

    merged_headers = {"User-Agent": _honest_user_agent()}
    if headers:
        merged_headers.update(headers)

    try:
        response = await session.get(
            url,
            params=params,
            headers=merged_headers,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
    except (httpx.HTTPError, TimeoutError) as exc:
        raise PaperSourceError(f"Request to {url} failed: {exc}") from exc

    logger.debug(f"GET {url} -> {response.status_code}")
    if response.status_code == 429:
        raise PaperSourceError(f"Rate limited by {url} (HTTP 429)")
    if response.status_code != 200:
        raise PaperSourceError(f"HTTP {response.status_code} from {url}")
    return response


async def get_json(
    session: SessionLike | None,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET *url* and return the decoded JSON body.

    :raises PaperSourceError: on any failure or an unreadable body.
    """
    response = await _get(session, url, params=params, headers=headers)
    try:
        return response.json()
    except ValueError as exc:
        raise PaperSourceError(f"Invalid JSON from {url}: {exc}") from exc


async def get_text(
    session: SessionLike | None,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """GET *url* and return the body as text (for the XML APIs).

    :raises PaperSourceError: on any failure.
    """
    response = await _get(session, url, params=params, headers=headers)
    return response.text
