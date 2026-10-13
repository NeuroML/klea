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
    :param route: A logical route to hand off to after the command's state
        updates (written to the transient ``command_route``); ``""`` (default)
        ends the run.  The app maps the route name to a graph node via the
        command node's conditional edge (see ``devdocs/system/graph-routing.md``).
    """

    updates: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    route: str = ""


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


def command_route_router(state: BaseModel) -> str:
    """Return a command's handoff route, or ``""`` for a terminal command.

    The command node writes the handler's ``GraphCommandResult.route`` to the
    transient ``command_route`` state field; the app's conditional edge maps
    this label to a graph node (``""`` -> ``END``).  Used as the path function
    of ``add_conditional_edges`` immediately after the command node, so a
    workflow command can hand off to another node (e.g. ``/compact`` -> the
    summarise node) before the run ends.  See
    ``devdocs/system/graph-routing.md``.

    :param state: The graph state (its ``command_route`` is read).
    :returns: The route label (``""`` when the command is terminal).
    """
    route = getattr(state, "command_route", "") or ""
    logger.debug(f"command route: {route!r}")
    return route
