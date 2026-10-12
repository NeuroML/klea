#!/usr/bin/env python3
"""
Shared helpers for the repository source implementations.

File: klea_utils/mcp/tool_impls/repositories/sources.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

import httpx

from klea_utils.api.utils import _make_retryer_httpx
from klea_utils.mcp.tool_impls.session import SessionLike
from klea_utils.mcp.tool_impls.ssrf import check_ssrf
from klea_utils.mcp.tool_impls.web_fetch import honest_user_agent

from .errors import RepositorySourceError

logger = logging.getLogger(__name__)

#: Request timeout for the JSON API calls, in seconds.
REQUEST_TIMEOUT = httpx.Timeout(30.0)


async def _get_json(
    session: SessionLike | None,
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    retries: int = 3,
    allow_internal_hosts: bool = False,
) -> Any:
    """GET *url* and return the decoded JSON body.

    Raises :class:`RepositorySourceError` on any failure (no session, SSRF
    denial, HTTP error, or unreadable JSON).  Transient failures (timeouts,
    connection errors, HTTP 5xx/429) are retried with exponential backoff
    via the shared retryer; other 4xx errors are reported directly.

    :param session: HTTP session to use for the request.  ``None`` when no
        session is available.
    :param url: HTTP or HTTPS URL to fetch.
    :param params: Optional query parameters for the request.
    :param headers: Additional request headers (merged over the honest
        User-Agent).
    :param retries: Number of attempts for transient failures.
    :param allow_internal_hosts: Skip the SSRF guard (requests to loopback,
        private, link-local, or reserved addresses).
    """
    if session is None:
        raise RepositorySourceError("HTTP session not initialized")

    if not allow_internal_hosts:
        ssrf_error = check_ssrf(url)
        if ssrf_error is not None:
            logger.warning(f"SSRF guard blocked {url}: {ssrf_error}")
            raise RepositorySourceError(ssrf_error)

    merged_headers = {"User-Agent": honest_user_agent()}
    if headers:
        merged_headers.update(headers)

    async def _do_get() -> Any:
        response = await session.get(
            url,
            params=params,
            headers=merged_headers,
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.json()

    retryer = _make_retryer_httpx(attempts=retries)
    try:
        return await retryer(_do_get)
    except httpx.HTTPStatusError as exc:
        raise RepositorySourceError(
            f"HTTP {exc.response.status_code} from {url}"
        ) from exc
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise RepositorySourceError(f"Request to {url} failed: {exc}") from exc
