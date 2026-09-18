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
from collections.abc import Container
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Per-chat data store.  External modules may read/write this dict
# directly for performance; the helper functions below cover the
# common create-or-get and sorted-lookup cases.
#
# Using plain dicts rather than Pydantic BaseModel because this is a
# simple in-memory frontend cache (not an API contract) and NiceGUI
# naturally works with dict access.  The saved schema is documented
# inline in ensure_chat() below.
chats: dict[str, dict] = {}


def ensure_chat(user_id: str, chat_id: str) -> dict:
    """Return the chat session dict for *user_id* / *chat_id*, creating it if missing.

    Each chat session dict has the following keys::

        name                Human-readable display name (auto-generated)
        created             ``datetime.timestamp()`` of creation (float).
        pinned              Whether the chat session is pinned to the top of the list.
        messages            List of message dicts ``{"text", "stamp",
                            "role", "header"}`` where *role* is ``"user"``,
                            ``"agent"`` or ``"tool"`` and *header* is an
                            optional small title (used by tool blocks).
        inspector_entries   List of dicts with info/debug events for the most
                            recent query in this chat session.
        inspector_expanded  Set of indices into *inspector_entries* that are
                            currently expanded in the UI.
        state_sections      Dict of ``{node_label: section_data}`` for the status
                            pane, ordered by first insertion (per node label).
        model_info          Dict of active model config per role
                            (from ``fetch_active_models``).
        token_usage         Numeric token totals accumulated for this in-memory
                            chat session.
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
            "state_sections": {},
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
    current_chat: dict | None,
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


def get_chats_sorted(user_id: str) -> list[tuple[str, dict]]:
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
