#!/usr/bin/env python3
"""
Utility functions for the Klea API layer.

File: klea_utils/api/utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging

from pydantic import HttpUrl
from pydantic import ValidationError as PydanticValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_random_exponential,
)

logger = logging.getLogger(__name__)


def validate_url(value: str) -> str:
    """Return *value* if it is a valid HTTP(S) URL, else raise ``ValueError``."""
    try:
        HttpUrl(value)
    except PydanticValidationError:
        raise ValueError(f"'{value}' is not a valid HTTP(S) URL")
    return value


def _make_retryer_httpx(
    attempts: int | None = None, timeout: float = 180.0
) -> AsyncRetrying:
    """Create an ``AsyncRetrying`` that retries transient httpx errors.

    Retries transient httpx failures: connection errors, read errors,
    timeouts, and HTTP 5xx/429 responses.  Other 4xx client errors are not
    retried.

    :param attempts: If set, bound the number of probe attempts.  Takes
        precedence over *timeout* (useful for fast single-shot probes).
    :param timeout: Total wall-clock seconds to keep probing when
        *attempts* is unset.  Generous by default so a server that is slow
        to initialize (MCP servers, embedding model downloads) is not given
        up on prematurely.
    :returns: A configured :class:`tenacity.AsyncRetrying` instance
    """
    # Import lazily so importing this module never requires httpx.
    import httpx

    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, (asyncio.TimeoutError, httpx.TransportError)):
            # asyncio.TimeoutError for client-side total timeouts;
            # httpx.TransportError covers ConnectError/ReadError/ReadTimeout.
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            # 429 (rate limit) and 5xx (server side) are transient; other
            # 4xx are client mistakes and should not be retried.
            return exc.response.status_code == 429 or exc.response.status_code >= 500
        return False

    if attempts is not None:
        stop = stop_after_attempt(attempts)
    else:
        stop = stop_after_delay(timeout)
    return AsyncRetrying(
        wait=wait_random_exponential(multiplier=1, max=10),
        stop=stop,
        retry=retry_if_exception(_is_retryable),
        reraise=True,
    )


async def _get_ready(url: str) -> dict:
    """GET the health endpoint and return its JSON, raising on non-2xx."""
    # Deferred so importing this module never requires httpx.
    import httpx

    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def check_api_is_ready(
    url: str, attempts: int | None = None, timeout: float = 180.0
):
    """Exponentially back off checking that the API is ready.

    :param url: Health check endpoint URL
    :param attempts: If set, maximum number of probe attempts (overrides
        *timeout*)
    :param timeout: Total wall-clock seconds to keep probing when
        *attempts* is unset
    """
    retryer = _make_retryer_httpx(attempts, timeout)
    logger.debug("Probing API readiness at %s (attempts=%s)", url, attempts)
    try:
        result = await retryer(_get_ready, url)
    except Exception as e:
        logger.warning("API not ready at %s after probes: %s", url, e)
        raise
    logger.debug("API ready at %s", url)
    return result
