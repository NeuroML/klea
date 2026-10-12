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
from collections.abc import Iterable

from klea_utils.commands.common import Command, CommandRegistry, ParsedCommand
from klea_utils.commands.graph import GraphCommandHandler, GraphCommandResult
from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL
from pydantic import BaseModel

from klea_agent.nodes.mode_router import decide_mode
from klea_agent.schemas import Mode

logger = logging.getLogger(__name__)

#: The implemented server-side commands.  Further commands land here as they
#: are built; client-side commands belong to the frontends, not this module.
_MODE_COMMAND = Command(
    name="mode",
    summary="Show or set the operating mode",
    side="server",
    klass="session-state",
    persists="checkpoint",
    while_streaming="block",
    arg_hint="[general|scientific]",
)

_ACCESS_COMMAND = Command(
    name="access",
    summary="Show or set the tool access level",
    side="server",
    klass="session-state",
    persists="checkpoint",
    while_streaming="block",
    arg_hint="[read_only|full]",
)

#: Server-side commands that are documented but not implemented yet.
#: Registered so the command node can reply "not implemented yet" to a direct
#: client; the public catalogue filters them out (``CommandRegistry.available``).
_STUB_COMMANDS: tuple[Command, ...] = (
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


def _handle_access(state: BaseModel, parsed: ParsedCommand) -> GraphCommandResult:
    """Handle ``/access`` (ADR-0037).

    With no argument, reports the current tool access level; with
    ``read_only`` or ``full``, writes it.  The level gates which tools the
    planner may use (``read_only`` is fail-closed: an unannotated tool is not
    permitted).

    :param state: The graph state.
    :param parsed: The parsed command.
    :returns: The command result.
    """
    requested = (parsed.args[0] if parsed.args else "").strip().lower()
    if not requested:
        current = getattr(state, "access_level", DEFAULT_ACCESS_LEVEL)
        return GraphCommandResult(message=f"Tool access level: {current}.")
    if requested not in ("read_only", "full"):
        return GraphCommandResult(
            message=f"Unknown access level: {requested}. Use read_only or full."
        )
    message = f"Tool access level set to {requested}."
    logger.debug(f"command /access: set access_level={requested}")
    return GraphCommandResult(
        updates={"access_level": requested},
        message=message,
        summary=message,
        details={"access_level": requested},
    )


def build_agent_commands(
    *, source_available: bool, disabled: Iterable[str] = ()
) -> tuple[CommandRegistry, dict[str, GraphCommandHandler]]:
    """Build the agent's server-side command registry and graph handlers.

    :param source_available: Whether a curated knowledge source is configured
        (Scientific mode precondition, ADR-0030).
    :param disabled: Command names the operator disabled (ADR-0047): a disabled
        command is not registered, so it is neither published nor runnable.
    :returns: ``(registry, handlers)`` -- the catalogue and the graph handler
        map the command node dispatches to.
    """
    disabled_names = {name.strip().lower() for name in disabled}
    registry = CommandRegistry()
    for command in (_MODE_COMMAND, _ACCESS_COMMAND, *_STUB_COMMANDS):
        if command.name in disabled_names:
            logger.info(f"session command /{command.name} disabled by config")
            continue
        registry.register(command)
    handlers: dict[str, GraphCommandHandler] = {}
    if registry.get("mode") is not None:
        handlers["mode"] = _handle_mode(source_available)
    if registry.get("access") is not None:
        handlers["access"] = _handle_access
    logger.debug(f"agent command catalogue: {len(registry.all())} command(s)")
    return registry, handlers
