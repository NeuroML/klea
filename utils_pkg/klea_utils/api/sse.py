#!/usr/bin/env python3
"""
Shared SSE streaming client for Klea frontends.

Provides both an async generator (for NiceGUI and TUI) and a synchronous
generator (for Streamlit) that consume the ``/query/stream`` SSE endpoint
and yield parsed event dicts.

The ordered request/lifecycle view is ``devdocs/system/api-sse-sequence.md``;
the event catalogue is ``devdocs/system/streams.md``.

File: klea_utils/api/sse.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from collections.abc import AsyncGenerator, Generator
from typing import Any
from urllib.parse import quote

import httpx

from ..llm import parse_model_name

logger = logging.getLogger(__name__)

#: Idle read timeout for the streaming request, in seconds.  Must comfortably
#: exceed the server's SSE heartbeat interval
#: (``klea_utils.api.chat_core.HEARTBEAT_INTERVAL_SECONDS``, 15 s) so a
#: heartbeat arrives and resets the timer well before it fires while a
#: long-running node emits no events.
STREAM_READ_TIMEOUT_SECONDS = 300.0


def _stream_payload(
    query: str,
    chat_id: str,
    user_id: str,
    resume: bool,
    extra: dict | None,
    interrupt_response: dict | None = None,
    interrupt_id: str | None = None,
    interrupt_cancel: bool = False,
) -> dict:
    """Build the ``/query/stream`` POST body.

    Exactly one action is sent: an interrupt answer (``interrupt_response``,
    optionally with the ``interrupt_id`` it answers), an interrupt cancel
    (``interrupt_cancel``), a failure resume (``resume``), or the ``query``.
    """
    payload: dict = {"chat_id": chat_id, "user_id": user_id}
    if interrupt_cancel:
        payload["interrupt_cancel"] = True
    elif interrupt_response is not None:
        payload["interrupt_response"] = interrupt_response
        if interrupt_id is not None:
            payload["interrupt_id"] = interrupt_id
    elif resume:
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
    interrupt_response: dict | None = None,
    interrupt_id: str | None = None,
    interrupt_cancel: bool = False,
) -> AsyncGenerator[dict, None]:
    """POST to ``/query/stream`` and yield parsed SSE event dicts.

    Each yielded dict has at least a ``"type"`` key.  Known types::

        progress    {"type": "progress", "node": "<label>",
                     "data": {"heading": "<text>"}}  (heading is the line shown;
                     it changes for an LLM invoke retry)
        inspect     {"type": "inspect", "node": "<label>", "data": {...}}
        token       {"type": "token", "content": "<chunk>", "node": "<label>"}
                    (only for opted-in free-text nodes)
        usage       {"type": "usage", "node": "<label>", "data": {...}}
        context     {"type": "context", "data": {...}}  (graph-level session context)
        interrupt   {"type": "interrupt", "node": "<label>", "data": {...}}
                    (the run paused for human input; ``data`` carries ``kind``,
                    the ``question``/``questions``, the ``interrupt_id`` and a
                    ``hitl_response_schema`` when the node supplied one.  No
                    ``complete`` follows a pause.)
        complete    {"type": "complete", "message_for_user": "<text>"}
        error       {"type": "error", "message": "<text>", "error_type": "<class>",
                     "node": "<label>", "resumable": <bool>}
        ping        {"type": "ping"}  (periodic server heartbeat while a
                     long-running node emits no events; consumers may ignore
                     it -- its only purpose is to keep the SSE connection and
                     any intermediaries from timing out)

    This async generator is intended for NiceGUI and TUI frontends.

    :param query: User's query string.
    :param chat_id: Chat conversation identifier.
    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param extra: Optional extra request fields merged into the POST body
        (e.g. an app-specific ``mode`` request, ADR-0030).
    :param resume: Resume the chat's last failed run from its checkpoint
        instead of starting a new turn (the query is not sent).
    :param interrupt_response: Answer a pending HITL interrupt (ADR-0046); the
        resume mapping (e.g. ``{"answers": [...]}`` or
        ``{"decision": "approve", "feedback": "..."}``).  Pass an empty query.
    :param interrupt_id: Id of the interrupt being answered.
    :param interrupt_cancel: Cancel the pending interrupt instead of answering.
    """
    url = f"{server_url}/query/stream"
    payload = _stream_payload(
        query,
        chat_id,
        user_id,
        resume,
        extra,
        interrupt_response,
        interrupt_id,
        interrupt_cancel,
    )
    async with (
        httpx.AsyncClient(
            timeout=httpx.Timeout(STREAM_READ_TIMEOUT_SECONDS, connect=10.0)
        ) as client,
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


async def request_cancel(
    server_url: str,
    chat_id: str,
    user_id: str = "",
    timeout: float = 10.0,
) -> bool:
    """Ask the server to cancel the chat's active run (idempotent).

    Best-effort: a 204 (or any non-error response) means the request
    reached the server; network failures are logged and return ``False``
    so the frontend can still stop locally.

    :param server_url: Base URL of the backend API server.
    :param chat_id: Chat conversation identifier.
    :param user_id: Opaque persistent user identifier.
    :param timeout: Request timeout in seconds.
    :returns: True when the server accepted the cancel request.
    """
    url = f"{server_url}/query/cancel"
    payload = {"chat_id": chat_id, "user_id": user_id}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
        logger.info(
            "request_cancel(chat=%s, user=%s): HTTP %s",
            chat_id,
            user_id,
            resp.status_code,
        )
        return resp.status_code < 400
    except Exception as e:  # noqa: BLE001
        logger.warning("request_cancel failed for chat=%s: %s", chat_id, e)
        return False


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


#: Timeout for the model catalogue calls.  Longer than the default: the
#: first call may have to download the models.dev catalog on the server.
CATALOGUE_FETCH_TIMEOUT_SECONDS = 30


def _catalogue_url(server_url: str, user_id: str, provider: str | None) -> str:
    """Return the model catalogue URL, optionally for one provider."""
    url = f"{server_url}/chat/{user_id}/models/catalogue"
    if provider:
        url += f"?provider={quote(provider)}"
    return url


def _catalogue_list(data: Any, key: str) -> list[str]:
    """Pull the ``providers`` / ``models`` list out of a catalogue response."""
    if not isinstance(data, dict):
        return []
    items = data.get(key)
    return items if isinstance(items, list) else []


async def fetch_catalogue_providers(server_url: str, user_id: str) -> list[str]:
    """Fetch the providers offered for model selection.

    Calls ``GET /chat/{user_id}/models/catalogue``.  Returns an empty list
    on any error, so the model fields fall back to plain free text.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :returns: Sorted provider ids, e.g. ``["anthropic", "custom", ...]``.
    """
    url = _catalogue_url(server_url, user_id, None)
    data = await _fetch_json(url, timeout=CATALOGUE_FETCH_TIMEOUT_SECONDS)
    providers = _catalogue_list(data, "providers")
    logger.debug("Catalogue providers for %s: %d", user_id, len(providers))
    return providers


def fetch_catalogue_providers_sync(server_url: str, user_id: str) -> list[str]:
    """Synchronous counterpart of :func:`fetch_catalogue_providers`."""
    url = _catalogue_url(server_url, user_id, None)
    data = _fetch_json_sync(url, timeout=CATALOGUE_FETCH_TIMEOUT_SECONDS)
    providers = _catalogue_list(data, "providers")
    logger.debug("Catalogue providers for %s: %d", user_id, len(providers))
    return providers


async def fetch_catalogue_models(
    server_url: str, user_id: str, provider: str
) -> list[str]:
    """Fetch the model ids offered for one provider.

    Calls ``GET /chat/{user_id}/models/catalogue?provider=...``.  Returns an
    empty list on any error, and for providers whose models are free text
    (ollama, custom).

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param provider: Klea provider id, e.g. ``"openai"``.
    :returns: Sorted model ids, e.g. ``["gpt-4o", "gpt-4o-mini", ...]``.
    """
    url = _catalogue_url(server_url, user_id, provider)
    data = await _fetch_json(url, timeout=CATALOGUE_FETCH_TIMEOUT_SECONDS)
    models = _catalogue_list(data, "models")
    logger.debug("Catalogue models for %s (%s): %d", user_id, provider, len(models))
    return models


def fetch_catalogue_models_sync(
    server_url: str, user_id: str, provider: str
) -> list[str]:
    """Synchronous counterpart of :func:`fetch_catalogue_models`."""
    url = _catalogue_url(server_url, user_id, provider)
    data = _fetch_json_sync(url, timeout=CATALOGUE_FETCH_TIMEOUT_SECONDS)
    models = _catalogue_list(data, "models")
    logger.debug("Catalogue models for %s (%s): %d", user_id, provider, len(models))
    return models


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
    interrupt_response: dict | None = None,
    interrupt_id: str | None = None,
    interrupt_cancel: bool = False,
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
    :param interrupt_response: Answer a pending HITL interrupt (ADR-0046); the
        resume mapping (e.g. ``{"answers": [...]}``).  Pass an empty query.
    :param interrupt_id: Id of the interrupt being answered.
    :param interrupt_cancel: Cancel the pending interrupt instead of answering.
    """
    url = f"{server_url}/query/stream"
    payload = _stream_payload(
        query,
        chat_id,
        user_id,
        resume,
        extra,
        interrupt_response,
        interrupt_id,
        interrupt_cancel,
    )
    with (
        httpx.Client(
            timeout=httpx.Timeout(STREAM_READ_TIMEOUT_SECONDS, connect=10.0)
        ) as client,
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
