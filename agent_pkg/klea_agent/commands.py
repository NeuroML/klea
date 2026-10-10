#!/usr/bin/env python3
"""
Agent session-command catalogue and graph handlers (ADR-0047).

Builds the agent's **server-side** command catalogue (served to frontends via
``GET /commands``) and the graph handlers the command node dispatches to.
Client-side commands (``/model``, ``/help``, ...) belong to the frontends, not
here: the backend cannot know them without a circular dependency, so a
client-side command reaching the graph is simply unknown.  ``/mode`` is the
first implemented graph command; the rest are documented stubs that reply
"not implemented yet" until each lands.  See
``devdocs/system/session-commands.md``.

File: klea_agent/commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.commands.common import Command, CommandRegistry, ParsedCommand
from klea_utils.commands.graph import GraphCommandHandler, GraphCommandResult
from pydantic import BaseModel

from klea_agent.nodes.mode_router import decide_mode
from klea_agent.schemas import Mode

logger = logging.getLogger(__name__)

#: Server-side commands that are documented but not implemented yet.
#: Registered so the command node can reply "not implemented yet" to a direct
#: client; the public catalogue filters them out (``CommandRegistry.available``).
_STUB_COMMANDS: tuple[Command, ...] = (
    Command(
        name="access",
        summary="Show or set the tool access level",
        side="server",
        klass="session-state",
        persists="checkpoint",
        while_streaming="block",
        arg_hint="[read_only|full]",
        implemented=False,
    ),
    Command(
        name="cwd",
        summary="Show or set the session project root",
        side="server",
        klass="session-state",
        persists="checkpoint",
        while_streaming="block",
        arg_hint="[path]",
        implemented=False,
    ),
    Command(
        name="file",
        summary="Add a backend file to the session context",
        side="server",
        klass="workflow",
        persists="checkpoint",
        while_streaming="block",
        arg_hint="<path>",
        implemented=False,
    ),
    Command(
        name="webfetch",
        summary="Fetch a URL into the session context",
        side="server",
        klass="workflow",
        persists="checkpoint",
        while_streaming="block",
        arg_hint="<url>",
        implemented=False,
    ),
    Command(
        name="run",
        summary="Run a shell command via the run_command tool",
        side="server",
        klass="workflow",
        persists="message",
        while_streaming="block",
        arg_hint="<command>",
        implemented=False,
    ),
    Command(
        name="compact",
        summary="Summarise and shorten the conversation memory",
        side="server",
        klass="workflow",
        persists="checkpoint",
        while_streaming="block",
        implemented=False,
    ),
    Command(
        name="plan",
        summary="Enter plan review",
        side="server",
        klass="workflow",
        persists="checkpoint",
        while_streaming="block",
        implemented=False,
    ),
    Command(
        name="init",
        summary="Write project guidance to AGENTS.md",
        side="server",
        klass="workflow",
        persists="message",
        while_streaming="block",
        implemented=False,
    ),
)


def _handle_mode(source_available: bool) -> GraphCommandHandler:
    """Return the ``/mode`` graph handler (ADR-0030).

    With no argument, reports the current mode; with ``general`` or
    ``scientific``, resolves and writes it.  Resolution never silently
    downgrades: a scientific request without a curated source keeps the
    explanatory ``note`` (the checkpointed state records it).

    :param source_available: Whether a curated knowledge source is configured.
    :returns: The handler.
    """

    def handler(state: BaseModel, parsed: ParsedCommand) -> GraphCommandResult:
        requested = (parsed.args[0] if parsed.args else "").strip().lower()
        current = getattr(state, "mode", None)
        if not requested:
            resolved = getattr(current, "resolved", "general")
            requested_now = getattr(current, "requested", "general")
            message = f"Operating mode: {resolved} (requested: {requested_now})."
            return GraphCommandResult(message=message)
        if requested not in ("general", "scientific"):
            return GraphCommandResult(
                message=f"Unknown mode: {requested}. Use general or scientific."
            )
        resolved, note = decide_mode(requested, source_available=source_available)
        mode = Mode(
            requested=requested,
            resolved=resolved,
            assurance="unverified",
            note=note,
        )
        message = note or f"Operating mode set to {resolved}."
        logger.debug(f"command /mode: requested={requested} -> {resolved}")
        return GraphCommandResult(
            updates={"mode": mode},
            message=message,
            summary=message,
            details={"requested": requested, "resolved": resolved},
        )

    return handler


def build_agent_commands(
    *, source_available: bool
) -> tuple[CommandRegistry, dict[str, GraphCommandHandler]]:
    """Build the agent's command registry and graph handlers.

    :param source_available: Whether a curated knowledge source is configured
        (Scientific mode precondition, ADR-0030).
    :returns: ``(registry, handlers)`` -- the catalogue and the graph handler
        map the command node dispatches to.
    """
    registry = CommandRegistry()
    registry.register(
        Command(
            name="mode",
            summary="Show or set the operating mode",
            side="server",
            klass="session-state",
            persists="checkpoint",
            while_streaming="block",
            arg_hint="[general|scientific]",
        )
    )
    handlers: dict[str, GraphCommandHandler] = {"mode": _handle_mode(source_available)}
    for stub in _STUB_COMMANDS:
        registry.register(stub)
    logger.debug(f"agent command catalogue: {len(registry.all())} command(s)")
    return registry, handlers
