#!/usr/bin/env python3
"""
Shared ``GET /commands`` router for the session-command framework (ADR-0047).

Serves the app's **server-side** command catalogue (only implemented
commands) so a frontend can build its ``/``-menu and validate input before
forwarding a server command as a query.  Client-side commands are owned by
each frontend and are not served here.

File: klea_utils/api/commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from fastapi import APIRouter, Request

from klea_utils.commands.common import CommandRegistry

logger = logging.getLogger(__name__)


def create_commands_router() -> APIRouter:
    """Return the ``GET /commands`` router.

    The endpoint reads the graph's ``command_registry`` (set by an app that
    uses the session-command framework); an app without one returns an empty
    catalogue.

    :returns: The router to mount on an app.
    """
    router = APIRouter(prefix="/commands", tags=["commands"])

    @router.get("")
    async def list_commands(request: Request) -> dict[str, Any]:
        """Return the app's available (implemented) server-side commands."""
        graph = getattr(request.app.state, "graph", None)
        registry: CommandRegistry | None = getattr(graph, "command_registry", None)
        commands = registry.available() if registry is not None else []
        logger.debug("GET /commands: %d command(s)", len(commands))
        return {"commands": [command.metadata() for command in commands]}

    return router
