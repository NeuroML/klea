#!/usr/bin/env python3
"""
Web fetch implementation for Klea MCP tools.

File: klea_utils/mcp/tool_impls/web_fetch.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from platformdirs import PlatformDirs

from klea_utils.api.utils import _make_retryer_httpx
from klea_utils.mcp.tool_impls.session import SessionLike
from klea_utils.mcp.tool_impls.ssrf import _MAX_REDIRECTS, check_ssrf_async
from klea_utils.mcp.tool_impls.user_agent import klea_user_agent
from klea_utils.paths import get_cache_dir

logger = logging.getLogger(__name__)

#: Source of current browser User-Agent strings (jnrbsn/user-agents, MIT).
#: Updated daily upstream; we cache a copy locally and refresh it at most
#: once per :data:`_UA_TTL_SECONDS`.
_UA_SOURCE_URL = "https://jnrbsn.github.io/user-agents/user-agents.json"
_UA_TTL_SECONDS = 24 * 60 * 60
_UA_REFRESH_TIMEOUT = 5.0
_UA_CACHE_PATH = get_cache_dir(PlatformDirs("klea")) / "user_agents.json"

#: Hardcoded fallback User-Agent, used when the remote list cannot be reached
#: (offline, site down) and no cached copy exists.  Keep reasonably current.
_FALLBACK_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

#: Honest client User-Agent, used as the single retry when a browser User-Agent
#: draws a Cloudflare challenge (a mismatched browser fingerprint can attract
#: more blocking than an honest client string, so we try one honest retry).
_HONEST_UA_PREFIX = "klea-web-fetch/"

#: In-process cache of the resolved User-Agent list and when it was resolved.
_UA_LIST: list[str] | None = None
_UA_RESOLVED_AT: float = 0.0
_UA_LOCK = asyncio.Lock()


def honest_user_agent() -> str:
    """Return the honest client User-Agent for web fetches.

    E.g. ``klea-web-fetch/<version>``; shared by the download and repository
    tools that reuse this identity (see
    :func:`klea_utils.mcp.tool_impls.user_agent.klea_user_agent`).
    """
    return klea_user_agent(_HONEST_UA_PREFIX)


async def _fetch_user_agents() -> list[str]:
    """Fetch the latest User-Agent list from the upstream source."""
    logger.debug(f"Fetching User-Agent list from {_UA_SOURCE_URL}")
    async with httpx.AsyncClient(
        timeout=_UA_REFRESH_TIMEOUT, follow_redirects=True
    ) as client:
        response = await client.get(_UA_SOURCE_URL)
        logger.debug(f"User-Agent list HTTP {response.status_code}")
        response.raise_for_status()
        data = response.json()
    if not isinstance(data, list) or not data:
        logger.warning(f"Unexpected User-Agent data from {_UA_SOURCE_URL}")
        return []
    agents = [u for u in data if isinstance(u, str) and u.strip()]
    logger.debug(f"Fetched {len(agents)} User-Agent strings")
    if not agents:
        logger.warning(f"No usable User-Agent strings from {_UA_SOURCE_URL}")
    return agents


def _read_cached_user_agents() -> list[str]:
    """Return the cached User-Agent list, or ``[]`` if none is present."""
    try:
        data = json.loads(_UA_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list) and data:
            return [u for u in data if isinstance(u, str) and u.strip()]
    except OSError as exc:
        logger.debug(f"No cached User-Agent list ({_UA_CACHE_PATH}): {exc}")
    except ValueError as exc:
        logger.warning(f"Corrupt cached User-Agent list ({_UA_CACHE_PATH}): {exc}")
    return []


def _cache_is_fresh() -> bool:
    """True when the cached User-Agent list is younger than the TTL."""
    try:
        age = time.time() - _UA_CACHE_PATH.stat().st_mtime
        logger.debug(
            f"Cached User-Agent list age = {age:.1f}s (TTL {_UA_TTL_SECONDS}s)"
        )
        return age < _UA_TTL_SECONDS
    except OSError:
        return False


def _write_cached_user_agents(agents: list[str]) -> None:
    """Atomically persist the User-Agent list to the cache directory."""
    try:
        _UA_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _UA_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(agents), encoding="utf-8")
        os.replace(tmp, _UA_CACHE_PATH)
        logger.debug(f"Cached {len(agents)} User-Agent strings to {_UA_CACHE_PATH}")
    except OSError as exc:
        logger.warning(f"Could not cache User-Agent list: {exc}")


async def _resolve_user_agents() -> list[str]:
    """Return a usable User-Agent list, refreshing at most once per day.

    Resolution order: fresh in-memory list, fresh local cache, remote fetch
    (with atomic cache write), hardcoded fallback.  Any failure in the fetch
    is logged and falls back, so the tool never blocks or crashes on it.
    """
    global _UA_LIST, _UA_RESOLVED_AT
    async with _UA_LOCK:
        if (
            _UA_LIST is not None
            and time.monotonic() - _UA_RESOLVED_AT < _UA_TTL_SECONDS
        ):
            logger.debug(f"Using in-memory User-Agent list ({len(_UA_LIST)} strings)")
            return _UA_LIST

        cached = _read_cached_user_agents()
        if cached and _cache_is_fresh():
            logger.debug(f"Using fresh cached User-Agent list ({len(cached)} strings)")
            _UA_LIST = cached
            _UA_RESOLVED_AT = time.monotonic()
            return _UA_LIST

        try:
            fetched = await _fetch_user_agents()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(f"Could not refresh User-Agent list: {exc}")
            fetched = []

        if fetched:
            _write_cached_user_agents(fetched)
            _UA_LIST = fetched
        elif cached:
            # Refresh failed but a (stale) cached copy beats a single
            # hardcoded string; serve it and fall back next time.
            logger.warning("Using stale cached User-Agent list")
            _UA_LIST = cached
        else:
            logger.warning(
                "No cached or fetched User-Agent list; using hardcoded fallback"
            )
            _UA_LIST = [_FALLBACK_USER_AGENT]
        _UA_RESOLVED_AT = time.monotonic()
        return _UA_LIST


def _html_to_text(html: str) -> str:
    """Strip HTML to plain text suitable for an LLM."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


async def _read_capped(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Read the response body up to *max_bytes*, flagging truncation."""
    chunks: list[bytes] = []
    size = 0
    truncated = False
    async for chunk in response.aiter_bytes():
        room = max_bytes - size
        if room <= 0:
            truncated = True
            break
        chunks.append(chunk[:room])
        size += len(chunks[-1])
        if len(chunk) > room:
            truncated = True
    logger.debug(f"Read {size}/{max_bytes} bytes\n{truncated = }")
    return b"".join(chunks), truncated


async def web_fetch(
    session: SessionLike | None,
    url: str,
    timeout: float = 30.0,
    max_chars: int = 100_000,
    retries: int = 3,
    max_download_bytes: int = 5_000_000,
    allow_internal_hosts: bool = False,
) -> dict[str, Any]:
    """Fetch a URL and return its text content.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool that supplies ``session`` from their lifespan
    context (see klea_utils.mcp.lifespan).

    Transient failures (timeouts, connection errors, HTTP 5xx/429) are
    retried with exponential backoff.  HTTP 4xx errors are returned as error
    results and not retried.  The raw response body is capped at
    *max_download_bytes* and the returned text at *max_chars*; each cap is
    reported via its own flag.

    :param session: HTTP session to use for the request.  ``None`` when no
        session is available.
    :param url: HTTP or HTTPS URL to fetch.
    :param timeout: Request timeout in seconds.
    :param max_chars: Maximum number of characters of content to return.
    :param retries: Number of attempts for transient failures.
    :param max_download_bytes: Maximum number of raw response bytes to read.
    :param allow_internal_hosts: Skip the SSRF guard (requests to loopback,
        private, link-local, or reserved addresses).
    :returns: dict with url, status_code, content_type, content, truncated,
        download_truncated, error.
    """
    logger.debug(
        f"Fetching URL\n"
        f"{url = }\n"
        f"{timeout = }\n"
        f"{max_chars = }\n"
        f"{retries = }\n"
        f"{max_download_bytes = }\n"
        f"{allow_internal_hosts = }"
    )

    error = ""
    status_code: int | None = None
    content_type = ""
    content = ""
    truncated = False
    download_truncated = False

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        logger.warning(f"Rejecting invalid URL: {url}")
        return {
            "url": url,
            "status_code": None,
            "content_type": "",
            "content": "",
            "truncated": False,
            "download_truncated": False,
            "error": "URL must be an absolute http:// or https:// URL.",
        }

    if not allow_internal_hosts:
        ssrf_error = await check_ssrf_async(url)
        if ssrf_error is not None:
            logger.warning(f"SSRF guard blocked {url}: {ssrf_error}")
            return {
                "url": url,
                "status_code": None,
                "content_type": "",
                "content": "",
                "truncated": False,
                "download_truncated": False,
                "error": ssrf_error,
            }

    if session is None:
        logger.warning(f"No HTTP session available for: {url}")
        return {
            "url": url,
            "status_code": None,
            "content_type": "",
            "content": "",
            "truncated": False,
            "download_truncated": False,
            "error": "HTTP session not initialized",
        }

    async def _stream_once(user_agent: str) -> bool:
        """Stream the URL once with *user_agent*.

        Follows redirects manually (up to :data:`_MAX_REDIRECTS`) so each
        hop can be SSRF-checked. Returns ``False`` when a Cloudflare
        challenge should trigger an honest-UA retry, ``True`` otherwise
        (including HTTP errors).
        """
        nonlocal \
            status_code, \
            content_type, \
            content, \
            truncated, \
            download_truncated, \
            error
        headers = {
            "Accept": "text/markdown, text/html, text/plain;q=0.9, */*;q=0.1",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": user_agent,
        }
        current_url = url
        for _ in range(_MAX_REDIRECTS + 1):
            logger.debug(
                f"Streaming {current_url}\n{user_agent = }\n{allow_internal_hosts = }"
            )
            async with session.stream(
                "GET",
                current_url,
                headers=headers,
                timeout=httpx.Timeout(timeout),
                follow_redirects=False,
            ) as response:
                status_code = response.status_code
                content_type = response.headers.get("content-type", "")

                # Follow redirects manually with per-hop SSRF check
                if status_code in (301, 302, 303, 307, 308):
                    loc = response.headers.get("location")
                    if not loc:
                        logger.warning(
                            f"Redirect {status_code} with no Location for {current_url}"
                        )
                        error = f"Redirect {status_code} with no Location"
                        return True
                    next_url = urljoin(current_url, loc)
                    parsed_next = urlparse(next_url)
                    if (
                        parsed_next.scheme not in ("http", "https")
                        or not parsed_next.netloc
                    ):
                        logger.warning(f"Redirect to invalid URL: {next_url}")
                        error = f"Redirect to invalid URL: {next_url}"
                        return True
                    if not allow_internal_hosts:
                        ssrf_error = await check_ssrf_async(next_url)
                        if ssrf_error is not None:
                            logger.warning(
                                f"SSRF guard blocked redirect {current_url} -> {next_url}: {ssrf_error}"
                            )
                            error = ssrf_error
                            status_code = None
                            content_type = ""
                            return True
                    logger.debug(
                        f"Following redirect {status_code}: {current_url} -> {next_url}"
                    )
                    current_url = next_url
                    continue

                if (
                    status_code == 403
                    and response.headers.get("cf-mitigated") == "challenge"
                ):
                    logger.warning(
                        f"Cloudflare challenge for {current_url}; retrying honestly"
                    )
                    return False

                if status_code >= 400:
                    if status_code == 429 or status_code >= 500:
                        # Transient server-side error; raise so the retryer can
                        # retry (other 4xx are returned as-is below).
                        logger.warning(
                            f"Transient HTTP {status_code} from {current_url}; will retry"
                        )
                        response.raise_for_status()
                    logger.warning(f"HTTP error {status_code} for {current_url}")
                    error = f"HTTP {status_code}"
                    return True

                logger.debug(
                    f"Response received\n{current_url = }\n{status_code = }\n{content_type = }"
                )

                raw, download_truncated = await _read_capped(
                    response, max_download_bytes
                )
                text = raw.decode("utf-8", errors="replace")

                if "html" in content_type.lower():
                    content = _html_to_text(text)
                else:
                    content = text

                if len(content) > max_chars:
                    content = content[:max_chars]
                    truncated = True

                logger.debug(
                    f"Fetched URL\n"
                    f"{current_url = }\n"
                    f"{status_code = }\n"
                    f"{content_type = }\n"
                    f"{len(content) = }\n"
                    f"{truncated = }\n"
                    f"{download_truncated = }"
                )
                return True

        logger.warning(f"Too many redirects for {url}")
        error = "Too many redirects"
        return True

    async def _do_fetch() -> None:
        user_agents = await _resolve_user_agents()
        browser_ua = random.choice(user_agents)
        ok = await _stream_once(browser_ua)
        if not ok:
            # Cloudflare challenge: one retry with the honest client UA.
            await _stream_once(honest_user_agent())

    retryer = _make_retryer_httpx(attempts=retries)
    try:
        await retryer(_do_fetch)
    except TimeoutError:
        logger.warning(f"Request timed out after {timeout} seconds: {url}")
        error = f"Request timed out after {timeout} seconds."
    except httpx.HTTPError as exc:
        logger.warning(f"Request failed for {url}: {exc}")
        error = str(exc)

    logger.debug(
        f"web_fetch result\n"
        f"{url = }\n"
        f"{status_code = }\n"
        f"{content_type = }\n"
        f"{len(content) = }\n"
        f"{truncated = }\n"
        f"{download_truncated = }\n"
        f"{error = }"
    )

    return {
        "url": url,
        "status_code": status_code,
        "content_type": content_type,
        "content": content,
        "truncated": truncated,
        "download_truncated": download_truncated,
        "error": error,
    }
