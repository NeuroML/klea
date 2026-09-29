#!/usr/bin/env python3
"""
Shared SSE streaming client for Klea frontends.

Provides both an async generator (for NiceGUI and TUI) and a synchronous
generator (for Streamlit) that consume the ``/query/stream`` SSE endpoint
and yield parsed event dicts.

File: klea_utils/api/sse.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Any

import httpx

from ..llm import parse_model_name

logger = logging.getLogger(__name__)


def _stream_payload(
    query: str,
    chat_id: str,
    user_id: str,
    resume: bool,
    extra: dict | None,
) -> dict:
    """Build the ``/query/stream`` POST body.

    On *resume* the ``query`` is omitted (the server resumes the thread's
    last failed run from its checkpoint); otherwise it is required.
    """
    payload: dict = {"chat_id": chat_id, "user_id": user_id}
    if resume:
        payload["resume"] = True
    else:
        payload["query"] = query
    if extra:
        payload.update(extra)
    return payload


async def stream_events(
    query: str,
    chat_id: str,
    server_url: str,
    user_id: str = "",
    extra: dict | None = None,
    resume: bool = False,
) -> AsyncGenerator[dict, None]:
    """POST to ``/query/stream`` and yield parsed SSE event dicts.

    Each yielded dict has at least a ``"type"`` key.  Known types::

        progress    {"type": "progress", "node": "<label>"}
        inspect     {"type": "inspect", "node": "<label>", "data": {...}}
        token       {"type": "token", "content": "<chunk>", "node": "<label>"}
                    (only for opted-in free-text nodes)
        usage       {"type": "usage", "node": "<label>", "data": {...}}
        context     {"type": "context", "data": {...}}  (graph-level session context)
        complete    {"type": "complete", "message_for_user": "<text>"}
        error       {"type": "error", "message": "<text>", "error_type": "<class>",
                     "node": "<label>", "resumable": <bool>}

    This async generator is intended for NiceGUI and TUI frontends.

    :param query: User's query string.
    :param chat_id: Chat conversation identifier.
    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param extra: Optional extra request fields merged into the POST body
        (e.g. an app-specific ``mode`` request, ADR-0030).
    :param resume: Resume the chat's last failed run from its checkpoint
        instead of starting a new turn (the query is not sent).
    """
    url = f"{server_url}/query/stream"
    payload = _stream_payload(query, chat_id, user_id, resume, extra)
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client,
        client.stream(
            "POST",
            url,
            json=payload,
        ) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                if line.strip():
                    logger.warning("Skipping non-data line: %s", line[:80])
                continue
            # Strip the SSE "data: " prefix (6 chars) to get raw JSON.
            try:
                yield json.loads(line[6:])
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(f"Skipping malformed SSE line: {line[:80]!r} ({exc})")
                continue


async def _fetch_json(url: str, timeout: float = 5) -> Any:
    """GET *url* and return the parsed JSON body, or ``None`` on failure."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch %s: %s", url, e)
            return None
    if resp.status_code != 200:
        logger.warning("HTTP %s from %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except ValueError as e:
        logger.warning("Invalid JSON from %s: %s", url, e)
        return None


def _fetch_json_sync(url: str, timeout: float = 5) -> Any:
    """Synchronous counterpart of :func:`_fetch_json`."""
    with httpx.Client(timeout=timeout) as client:
        try:
            resp = client.get(url)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to fetch %s: %s", url, e)
            return None
    if resp.status_code != 200:
        logger.warning("HTTP %s from %s", resp.status_code, url)
        return None
    try:
        return resp.json()
    except ValueError as e:
        logger.warning("Invalid JSON from %s: %s", url, e)
        return None


async def fetch_active_models(
    server_url: str,
    user_id: str,
    chat_id: str,
) -> dict[str, dict[str, str]]:
    """Fetch the resolved model config per role for a chat.

    Calls ``GET /chat/{user_id}/{chat_id}/models/active`` and returns the
    merged default + per-session + per-chat config dict.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: ``{"chat": {"model": "...", "credential": {...}}, ...}``
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/models/active"
    data = await _fetch_json(url)
    logger.debug("Active models for %s:%s: %s", user_id, chat_id, data)
    return data or {}


def fetch_active_models_sync(
    server_url: str,
    user_id: str,
    chat_id: str,
) -> dict[str, dict[str, str]]:
    """Synchronous counterpart of :func:`fetch_active_models`.

    Intended for frontends that cannot use asyncio.
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/models/active"
    data = _fetch_json_sync(url)
    logger.debug("Active models for %s:%s: %s", user_id, chat_id, data)
    return data or {}


async def fetch_session_models(
    server_url: str,
    user_id: str,
) -> dict[str, dict[str, str]]:
    """Fetch defaults + per-session overrides for a user (no chat).

    Calls ``GET /chat/{user_id}/models/active``.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :returns: ``{"chat": {"model": "...", "credential": {...}}, ...}``
    """
    url = f"{server_url}/chat/{user_id}/models/active"
    data = await _fetch_json(url)
    logger.debug("Session models for %s: %s", user_id, data)
    return data or {}


def fetch_session_models_sync(
    server_url: str,
    user_id: str,
) -> dict[str, dict[str, str]]:
    """Synchronous counterpart of :func:`fetch_session_models`."""
    url = f"{server_url}/chat/{user_id}/models/active"
    data = _fetch_json_sync(url)
    logger.debug("Session models for %s: %s", user_id, data)
    return data or {}


async def fetch_credentials(server_url: str, user_id: str) -> list[dict[str, Any]]:
    """Fetch the user's stored provider credentials (masked).

    Calls ``GET /credentials/{user_id}``.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :returns: List of ``{provider, endpoint, source, masked, ...}``.
    """
    url = f"{server_url}/credentials/{user_id}"
    data = await _fetch_json(url)
    logger.debug("Credentials for %s: %d", user_id, len(data or []))
    return data or []


def fetch_credentials_sync(server_url: str, user_id: str) -> list[dict[str, Any]]:
    """Synchronous counterpart of :func:`fetch_credentials`."""
    url = f"{server_url}/credentials/{user_id}"
    data = _fetch_json_sync(url)
    logger.debug("Credentials for %s: %d", user_id, len(data or []))
    return data or []


def format_model_info(info: dict[str, dict[str, str]]) -> str:
    """Build a compact one-line model summary from active models config.

    Strips provider prefixes and joins roles, e.g.::

        Chat:deepseek-v4-flash | Guard:llama-guard3 | Embedding:bge-m3

    :param info: The dict returned by ``fetch_active_models`` /
        ``fetch_active_models_sync``.
    :returns: Empty string if no models are configured.
    """
    parts: list[str] = []
    for role, cfg in info.items():
        raw = cfg.get("model", "")
        if raw:
            parsed = parse_model_name(raw)
            name_short = parsed.model_name if parsed.model_name else raw
        else:
            name_short = "?"
        parts.append(f"{role.capitalize()}: {name_short}")
    result = " | ".join(parts)
    logger.debug("Formatted model info: %s", result)
    return result


def stream_events_sync(
    query: str,
    chat_id: str,
    server_url: str,
    user_id: str = "",
    extra: dict | None = None,
    resume: bool = False,
) -> Generator[dict, None, None]:
    """Synchronous counterpart of :func:`stream_events`.

    Intended for frontends that cannot use asyncio.  Async frontends
    (NiceGUI, TUI) should use :func:`stream_events` instead.

    :param query: User's query string.
    :param chat_id: Chat conversation identifier.
    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param extra: Optional extra request fields merged into the POST body
        (e.g. an app-specific ``mode`` request, ADR-0030).
    :param resume: Resume the chat's last failed run from its checkpoint
        instead of starting a new turn (the query is not sent).
    """
    url = f"{server_url}/query/stream"
    payload = _stream_payload(query, chat_id, user_id, resume, extra)
    with (
        httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0)) as client,
        client.stream(
            "POST",
            url,
            json=payload,
        ) as response,
    ):
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                if line.strip():
                    logger.warning("Skipping non-data line: %s", line[:80])
                continue
            # Strip the SSE "data: " prefix (6 chars) to get raw JSON.
            try:
                yield json.loads(line[6:])
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning(f"Skipping malformed SSE line: {line[:80]!r} ({exc})")
                continue
