#!/usr/bin/env python3
"""
Session transcript export helpers.

Framework-agnostic rendering of a chat's rendered transcript (the
``klea_utils.ui.state.MessageData`` entries) to Markdown, shared by the
frontends' ``/export`` client command (ADR-0047).

File: klea_utils/ui/export.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Display labels for the non-tool transcript roles.
_ROLE_LABELS = {
    "user": "User",
    "agent": "Assistant",
    "system": "System",
}


def _message_label(message: Mapping[str, Any]) -> str:
    """Return the display label for a transcript message.

    Tool messages are prefixed with ``Tool:`` and their ``header`` (for
    example ``Tool: run_command``); the remaining roles map to a fixed label.
    """
    role = str(message.get("role") or "")
    if role == "tool":
        header = str(message.get("header") or "").strip()
        return f"Tool: {header}" if header else "Tool"
    return _ROLE_LABELS.get(role, role.capitalize() or "Message")


def _clean_title(title: str) -> str:
    """Collapse whitespace in *title* so it cannot break the ``#`` heading."""
    return " ".join((title or "").split()) or "Klea session"


def transcript_markdown(
    messages: Sequence[Mapping[str, Any]],
    *,
    title: str = "Klea session",
    exported_at: str = "",
) -> str:
    """Render *messages* as a Markdown transcript.

    Each turn is a ``##`` heading (``## User``, ``## Assistant``,
    ``## Tool: <name>``) rather than bold, so the role labels stay distinct
    from any ``**bold**`` in the message content.

    :param messages: The chat's rendered transcript entries (each with
        ``role``/``text``/``stamp``, and ``header`` for tool blocks).
    :param title: The document title (usually the session name); whitespace is
        collapsed and an empty title falls back to ``Klea session``.
    :param exported_at: An optional timestamp line (empty to omit).
    :returns: The Markdown document, ending in a newline.
    """
    lines = [f"# {_clean_title(title)}", ""]
    if exported_at:
        lines += [f"_Exported {exported_at}_", ""]
    for message in messages:
        stamp = str(message.get("stamp") or "")
        heading = f"## {_message_label(message)}"
        if stamp:
            heading += f" ({stamp})"
        lines += [heading, "", str(message.get("text") or "").rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def export_filename(name: str, *, suffix: str = "md") -> str:
    """Return a filesystem-safe export filename derived from *name*.

    Replaces any run of characters outside ``[A-Za-z0-9._-]`` with a single
    hyphen; falls back to ``klea-session`` when *name* has no usable
    characters.

    :param name: The session name (usually the chat title).
    :param suffix: The file extension, without the dot (default ``md``).
    :returns: A safe ``<slug>.<suffix>`` filename.
    """
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (name or "").strip()).strip("-.")
    return f"{slug or 'klea-session'}.{suffix}"


def export_timestamp(when: datetime | None = None) -> str:
    """Return a human-readable, ASCII timestamp for the export header."""
    moment = when or datetime.now().astimezone()
    return moment.strftime("%Y-%m-%d %H:%M:%S %Z").strip()
