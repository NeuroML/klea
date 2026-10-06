#!/usr/bin/env python3
"""
In-memory chat state for the NiceGUI frontend.

Keyed by ``{user_id}:{chat_id}`` so that colliding chat_ids across
different users do not interfere.

File: klea_utils/ui/web/nicegui/state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Container, Mapping
from datetime import datetime
from typing import Any, NotRequired, TypedDict

from klea_utils.api.hitl import render_interrupt_response

logger = logging.getLogger(__name__)


class MessageData(TypedDict):
    """A single rendered chat message.

    ``role`` is ``"user"``, ``"agent"`` or ``"tool"``; ``header`` is an
    optional small title (used by tool blocks).  Tool messages add the
    mime/display/data/meta fields used by the chat-bubble renderer, plus
    ``is_error`` for an errored tool result (styled by the renderer).
    """

    text: str
    stamp: str
    role: str
    header: str
    mime: NotRequired[str]
    data: NotRequired[str]
    meta: NotRequired[dict[str, Any]]
    is_error: NotRequired[bool]


class StateSection(TypedDict):
    """A status-pane section streamed by a graph node."""

    heading: str
    display: str
    summary: str
    details: dict[str, Any]
    preformatted: bool


class InspectorEntry(TypedDict):
    """An inspector entry buffered for the most recent query in a chat."""

    type: str
    node: str
    heading: str
    summary: str
    details: dict[str, Any]
    timing_seconds: NotRequired[float | None]


class InspectorMarker(TypedDict):
    """A per-query heading in the inspector pane.

    Marks the start of a query's inspection section, so entries from
    different queries can be told apart when the pane keeps them all.
    """

    type: str
    text: str
    stamp: str


class TokenUsage(TypedDict):
    """Accumulated token totals for an in-memory chat session."""

    input_tokens: int
    output_tokens: int
    total_tokens: int


class TurnStatus(TypedDict):
    """Per-chat turn status content (the chat-pane region under the transcript).

    Rendered under the transcript for the chat and scoped to its current
    (or last) turn.  ``kind`` selects the rendering:

    ==================  ============================================
    kind                meaning
    ==================  ============================================
    ``idle``            nothing to show
    ``progress``        an in-flight run; ``heading`` is the live line
    ``error``           a failed run; ``message`` and ``resumable`` (Retry)
    ``stopped``         the run was cancelled by the user
    ``awaiting_input``  the run paused for HITL input; the ask is on
                        :attr:`ChatData.interrupt` (ADR-0046)
    ==================  ============================================

    Any new turn (send / retry) resets it, so a terminal status never
    survives past the next user action.
    """

    kind: str
    heading: NotRequired[str]
    message: NotRequired[str]
    resumable: NotRequired[bool]


def idle_turn_status() -> TurnStatus:
    """Return the empty per-chat status."""
    return {"kind": "idle"}


class ChatData(TypedDict):
    """In-memory state for one chat session (see :func:`ensure_chat`).

    Keys are created by :func:`ensure_chat`; the ``NotRequired`` ones are
    populated lazily by the stream/hydration/context-control paths.  The
    dict is a frontend cache only, not an API contract.
    """

    #: Human-readable display name (auto-generated, renameable).
    name: str
    #: ``datetime.timestamp()`` of creation.
    created: float
    #: Whether the chat session is pinned to the top of the list.
    pinned: bool
    #: Rendered transcript messages.
    messages: list[MessageData]
    #: Inspector items for this chat, in order: a per-query
    #: :class:`InspectorMarker` followed by that query's entries.  Kept
    #: for the browser session (not persisted).
    inspector_entries: list[InspectorEntry | InspectorMarker]
    #: Indices into ``inspector_entries`` currently expanded in the UI.
    inspector_expanded: set[int]
    #: Indices of inspector query sections currently collapsed.
    inspector_sections_collapsed: set[int]
    #: Status-pane sections, keyed by node label / section key.
    state_sections: dict[str, StateSection]
    #: Current/last turn's status (progress/error/stopped/awaiting_input).
    turn_status: TurnStatus
    #: Active model config per role (from ``fetch_active_models``).
    model_info: dict[str, dict[str, Any]]
    #: Accumulated token totals for this chat.
    token_usage: TokenUsage
    #: Hydrated graph session context (e.g. the agent operating mode).
    context: NotRequired[dict[str, Any]]
    #: Pending HITL ask (ADR-0046) while the run is paused: the interrupt
    #: ``data`` (``kind``, ``question``/``questions``, ``interrupt_id`` and a
    #: ``hitl_response_schema`` when the node supplied one).  Cleared when the
    #: next turn starts.
    interrupt: NotRequired[dict[str, Any]]
    #: App-defined context-control preferences (mode / access level).
    mode_pref: NotRequired[str]
    access_pref: NotRequired[str]


# Per-chat data store.  External modules may read/write this dict
# directly for performance; the helper functions below cover the
# common create-or-get and sorted-lookup cases.
chats: dict[str, ChatData] = {}


def ensure_chat(user_id: str, chat_id: str) -> ChatData:
    """Return the chat session dict for *user_id* / *chat_id*, creating it if missing.

    The dict schema is documented on :class:`ChatData`; this function
    creates the always-present keys and returns the (possibly new) entry.
    """
    key = f"{user_id}:{chat_id}"
    if key not in chats:
        now = datetime.now().astimezone()
        logger.debug("creating %s", key)
        chats[key] = {
            "name": chat_id.replace("-", " ").title(),
            "created": now.timestamp(),
            "pinned": False,
            "messages": [],
            "inspector_entries": [],
            "inspector_expanded": set(),
            "inspector_sections_collapsed": set(),
            "state_sections": {},
            "turn_status": idle_turn_status(),
            "model_info": {},
            "token_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        }
    else:
        logger.debug("found existing %s", key)
    return chats[key]


def interrupt_display(
    response: Mapping[str, Any] | None, *, cancel: bool = False
) -> str:
    """Render a HITL answer/cancel as the user turn shown in the transcript.

    Delegates to :func:`klea_utils.api.hitl.render_interrupt_response` (the
    same renderer the server uses for the persisted row) so the live and
    reloaded text cannot drift; a cancel is marked locally (the server writes
    no user row for a cancel).

    :param response: The resume mapping sent to the server, or ``None``.
    :param cancel: Whether the user cancelled the interrupt.
    :returns: The user-turn text.
    """
    if cancel or not response:
        return "(cancelled)"
    return render_interrupt_response(response)


def resolve_choice(
    pending: Any,
    pref: Any,
    context_value: Any,
    allowed: Container[str],
    default: str,
) -> str:
    """Return the effective UI choice from pending, pref, context, default.

    Used by the status-pane context controls (operating mode / tool access
    level) so their defaults render before a chat exists: the caller passes
    the pending request (``PageContext.query_extra``), the per-chat
    preference, and the hydrated context value; the first one that is a known
    option wins, otherwise *default*.

    :param pending: The pending request (next query's value), or ``None``.
    :param pref: The per-chat preference, or ``None``.
    :param context_value: The value from the hydrated ``context`` event.
    :param allowed: The set of valid option values.
    :param default: The fallback value.
    :returns: The first valid value among the candidates, else *default*.
    """
    for value in (pending, pref, context_value):
        if value in allowed:
            return value
    return default


def resolve_chat_choice(
    current_chat: Mapping[str, Any] | None,
    pending: Any,
    pref: Any,
    context_value: Any,
    allowed: Container[str],
    default: str,
) -> str:
    """Return the effective choice for *current_chat*.

    ``pending`` (``PageContext.query_extra``) is page-session scoped, not
    per-chat, so it is only consulted before a chat exists -- e.g. choosing an
    operating mode or access level for the first message.  Once a chat exists
    its per-chat preference and hydrated context win, so switching chats
    restores each chat's own value instead of the last selection made
    elsewhere.

    :param current_chat: The active chat session dict, or a falsy value.
    :param pending: The pending request, or ``None``.
    :param pref: The per-chat preference, or ``None``.
    :param context_value: The value from the hydrated ``context`` event.
    :param allowed: The set of valid option values.
    :param default: The fallback value.
    :returns: The resolved value, else *default*.
    """
    if current_chat:
        pending = None
    return resolve_choice(pending, pref, context_value, allowed, default)


def missing_credentials(model_info: dict[str, Any]) -> list[str]:
    """Return roles whose resolved model needs an API key that is not set.

    Used by the UI to flag roles that cannot run until the user stores a
    provider credential (or sets the provider's environment variable).
    Roles whose provider needs no key (e.g. local Ollama) are ignored, as
    are roles with no model resolved yet.

    :param model_info: The resolved per-role config from ``fetch_active_models``
        / ``fetch_session_models`` (each value carries a ``credential`` block).
    :returns: Role names whose credential source is ``none`` but required.
    """
    missing: list[str] = []
    for role, cfg in model_info.items():
        if not cfg.get("model"):
            continue
        credential = cfg.get("credential") or {}
        if credential.get("requires_key") and credential.get("source") == "none":
            missing.append(role)
    return missing


def get_chats_sorted(user_id: str) -> list[tuple[str, ChatData]]:
    """Return (chat_id, data) pairs for *user_id*, pinned first, then by creation desc.

    Filters by the user_id prefix so that in a multi-browser scenario
    (same NiceGUI process) each user only sees their own chats.
    """
    prefix = f"{user_id}:"
    all_keys = list(chats.keys())
    matched = [k for k in all_keys if k.startswith(prefix)]
    logger.debug(
        "user_id=%s prefix=%r chats_keys=%s matched=%s",
        user_id,
        prefix,
        all_keys,
        matched,
    )
    items = [(k.split(":", 1)[1], v) for k, v in chats.items() if k.startswith(prefix)]
    items.sort(key=lambda x: (not x[1]["pinned"], -x[1]["created"]))
    return items
