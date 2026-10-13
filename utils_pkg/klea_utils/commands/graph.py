#!/usr/bin/env python3
"""
Graph-side support for the session-command framework (ADR-0047).

Holds the graph-command handler contract and its result type, plus the entry
router that decides whether a query is a server-side command.  The
``CommandNode`` itself lives in :mod:`klea_utils.nodes.command`.

See ``devdocs/system/session-commands.md``; ADR-0047.

File: klea_utils/commands/graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from klea_utils.commands.common import ParsedCommand, parse_command

logger = logging.getLogger(__name__)


@dataclass
class GraphCommandResult:
    """The outcome of a server-side command handler.

    :param updates: State updates to merge (e.g. a new ``mode``).
    :param message: The user-facing reply (written to ``message_for_user``).
    :param summary: Inspect-event summary (defaults to :attr:`message`).
    :param details: Inspect-event details.
    """

    updates: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)


#: A graph command handler: ``(state, parsed) -> GraphCommandResult``, or an
#: awaitable of one.  A handler may be ``async`` when it awaits an LLM or tool
#: call (e.g. ``/compact``); the command node awaits the result when needed.
GraphCommandHandler = Callable[
    [BaseModel, ParsedCommand],
    GraphCommandResult | Awaitable[GraphCommandResult],
]


def command_query_router(state: BaseModel) -> str:
    """Return ``"command"`` for a command query, else ``"continue"``.

    The router makes a single decision: is the query syntactically a command
    (a leading ``/``, with ``//`` escaping a literal slash) or a general
    query?  The command node does the rest -- validating the name, dispatching
    an executable command, or replying with a direct message for an unknown,
    client-side, or not-yet-implemented command.

    :param state: The graph state (its ``query`` is inspected).
    :returns: ``"command"`` or ``"continue"``.
    """
    parsed = parse_command(getattr(state, "query", ""))
    if parsed is not None:
        logger.debug(f"command query routed to the command node: /{parsed.name}")
        return "command"
    return "continue"
