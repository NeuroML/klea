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

import httpx

from ..llm import parse_model_name

logger = logging.getLogger(__name__)


async def stream_events(
    query: str,
    chat_id: str,
    server_url: str,
    user_id: str = "",
    extra: dict | None = None,
) -> AsyncGenerator[dict, None]:
    """POST to ``/query/stream`` and yield parsed SSE event dicts.

    Each yielded dict has at least a ``"type"`` key.  Known types::

        progress    {"type": "progress", "node": "<label>"}
        info        {"type": "info", "node": "<label>", "data": {...}}
        debug       {"type": "debug", "node": "<label>", "data": {...}}
        token       {"type": "token", "content": "<chunk>", "node": "<label>"}
        usage       {"type": "usage", "node": "<label>", "data": {...}}
        context     {"type": "context", "data": {...}}  (graph-level session context)
        complete    {"type": "complete", "message_for_user": "<text>"}
        error       {"type": "error", "message": "<text>", "error_type": "<class>", "node": "<label>"}

    This async generator is intended for NiceGUI and TUI frontends.

    :param query: User's query string.
    :param chat_id: Chat conversation identifier.
    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param extra: Optional extra request fields merged into the POST body
        (e.g. an app-specific ``mode`` request, ADR-0030).
    """
    url = f"{server_url}/query/stream"
    payload: dict = {"query": query, "chat_id": chat_id, "user_id": user_id}
    if extra:
        payload.update(extra)
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


# TODO: if more fetch-json-from-endpoint functions are added, consider
# extracting the async/sync boilerplate into _fetch_json / _fetch_json_sync
# helpers in utils.py to avoid repetition.
async def fetch_active_models(
    server_url: str,
    user_id: str,
    chat_id: str,
) -> dict[str, dict[str, str]]:
    """Fetch the resolved model config per role for a chat.

    Calls ``GET /chat/{user_id}/{chat_id}/models/active`` and returns the
    merged default + override config dict.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: ``{"chat": {"model": "...", "provider": "..."}, "guard": ..., "embedding": ...}``
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/models/active"
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "Failed to fetch active models: HTTP %s from %s",
                    resp.status_code,
                    url,
                )
                return {}
            data: dict[str, dict[str, str]] = resp.json()
            logger.debug("Active models for %s:%s: %s", user_id, chat_id, data)
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to fetch active models from %s: %s",
                url,
                e,
            )
            return {}


def fetch_active_models_sync(
    server_url: str,
    user_id: str,
    chat_id: str,
) -> dict[str, dict[str, str]]:
    """Synchronous counterpart of :func:`fetch_active_models`.

    Intended for frontends that cannot use asyncio.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/models/active"
    with httpx.Client(timeout=5) as client:
        try:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(
                    "Failed to fetch active models: HTTP %s from %s",
                    resp.status_code,
                    url,
                )
                return {}
            data: dict[str, dict[str, str]] = resp.json()
            logger.debug("Active models for %s:%s: %s", user_id, chat_id, data)
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to fetch active models from %s: %s",
                url,
                e,
            )
            return {}


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
    """
    url = f"{server_url}/query/stream"
    payload: dict = {"query": query, "chat_id": chat_id, "user_id": user_id}
    if extra:
        payload.update(extra)
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
