#!/usr/bin/env python3
"""
Agent session-command catalogue and graph handlers (ADR-0047).

Builds the agent's **server-side** command catalogue (served to frontends via
``GET /commands``) and the graph handlers the command node dispatches to.
Client-side commands (``/model``, ``/help``, ...) belong to the frontends, not
here: the backend cannot know them without a circular dependency, so a
client-side command reaching the graph is simply unknown.  ``/mode`` and
``/access`` are implemented graph commands; ``/compact`` (memory-gated) hands
off to the compaction node, and ``/init`` hands off back into the normal path
with a synthesised query.  The rest are documented stubs that reply "not
implemented yet" until each lands.  See
``devdocs/system/session-commands.md``.

File: klea_agent/commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Iterable
from pathlib import Path

from klea_utils.commands.common import Command, CommandRegistry, ParsedCommand
from klea_utils.commands.graph import GraphCommandHandler, GraphCommandResult
from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from klea_agent.discovery import refresh_project_files
from klea_agent.nodes.mode_router import decide_mode
from klea_agent.schemas import Discovery, Mode

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

#: ``/cwd``: show or set the session project root (the file tools' boundary
#: and default directory).  A session-state command like ``/mode``/``/access``.
_CWD_COMMAND = Command(
    name="cwd",
    summary="Show or set the session project root",
    side="server",
    klass="session-state",
    persists="checkpoint",
    while_streaming="block",
    arg_hint="[path]",
)

#: ``/compact``: summarise the old conversation now.  Registered only when
#: conversation memory is enabled (it hands off to the compaction node); the
#: handoff keeps the command out of the LLM's message history (checkpoint).
_COMPACT_COMMAND = Command(
    name="compact",
    summary="Summarise and shorten the conversation memory",
    side="server",
    klass="workflow",
    persists="checkpoint",
    while_streaming="block",
)

#: ``/init``: generate project guidance (``AGENTS.md``).  A workflow command
#: that hands off *back into the normal path* with a synthesised query, so the
#: ordinary agent loop explores the project and writes the file.  ``persists``
#: is ``message`` so the (replaced) init turn stays in the model's history.
_INIT_COMMAND = Command(
    name="init",
    summary="Generate project guidance (AGENTS.md)",
    side="server",
    klass="workflow",
    persists="message",
    while_streaming="block",
)

#: The synthesised query ``/init`` runs as a normal task.  Deliberately
#: bounded and conservative so the agent does not invent commands.
_INIT_PROMPT = (
    "Generate project guidance for future coding agents: create or update an "
    "AGENTS.md file at the project root. First explore the repository (the "
    "README, the build/package configuration, the top-level source layout, "
    "and any existing AGENTS.md or CLAUDE.md), then write a concise AGENTS.md "
    "covering the project layout, the build/test/lint commands, the code "
    "conventions, and important gotchas. Keep it short and accurate; do not "
    "invent commands or conventions you have not verified."
)

#: Server-side commands that are documented but not implemented yet.
#: Registered so the command node can reply "not implemented yet" to a direct
#: client; the public catalogue filters them out (``CommandRegistry.available``).
_STUB_COMMANDS: tuple[Command, ...] = (
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
        logger.debug(f"command /mode: {requested = }")
        current = getattr(state, "mode", None)
        if not requested:
            resolved = getattr(current, "resolved", "general")
            requested_now = getattr(current, "requested", "general")
            logger.debug(f"command /mode (report): {resolved = } {requested_now = }")
            message = f"Operating mode: {resolved} (requested: {requested_now})."
            return GraphCommandResult(message=message)
        if requested not in ("general", "scientific"):
            logger.warning(f"command /mode: unknown mode {requested!r}")
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
    logger.debug(f"command /access: {requested = }")
    if not requested:
        current = getattr(state, "access_level", DEFAULT_ACCESS_LEVEL)
        logger.debug(f"command /access (report): {current = }")
        return GraphCommandResult(message=f"Tool access level: {current}.")
    if requested not in ("read_only", "full"):
        logger.warning(f"command /access: unknown access level {requested!r}")
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


def _handle_cwd(state: BaseModel, parsed: ParsedCommand) -> GraphCommandResult:
    """Handle ``/cwd``: show or set the session project root.

    With no argument, reports the effective root (the checkpointed
    ``project_root``, or the process working directory when unset).  With a
    path, resolves it (relative to the current root), requires an existing
    directory, and writes it to ``state.project_root``.  The root is the file
    tools' boundary and default directory.

    Setting a root also reconciles discovery against it immediately, so the
    previous root's ``AGENTS.md``/``CLAUDE.md`` does not persist in the session
    (otherwise it would only be refreshed on the next turn's
    ``InitGraphState``).

    :param state: The graph state.
    :param parsed: The parsed command.
    :returns: The command result.
    """
    arg = " ".join(parsed.args).strip()
    current = getattr(state, "project_root", "") or str(Path.cwd())
    logger.debug(f"command /cwd: {arg = } {current = }")
    if not arg:
        return GraphCommandResult(message=f"Project root: {current}.")
    base = Path(getattr(state, "project_root", "") or Path.cwd())
    candidate = Path(arg).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        logger.warning(f"command /cwd: cannot resolve {arg!r}: {exc}")
        return GraphCommandResult(message=f"Cannot resolve {arg!r}: {exc}")
    if not resolved.is_dir():
        logger.warning(f"command /cwd: not a directory {resolved}")
        return GraphCommandResult(message=f"Not a directory: {resolved}.")
    # Reconcile discovery with the new root now, so a stale instruction file
    # from the previous root is dropped in the same turn.
    current_discovery = getattr(state, "discovery_persistent", None) or Discovery()
    discovery = refresh_project_files(current_discovery, resolved)
    message = f"Project root set to {resolved}."
    logger.debug(f"command /cwd: set project_root={resolved}")
    return GraphCommandResult(
        updates={"project_root": str(resolved), "discovery_persistent": discovery},
        message=message,
        summary=message,
        details={"project_root": str(resolved)},
    )


def _handle_compact(state: BaseModel, parsed: ParsedCommand) -> GraphCommandResult:
    """Handle ``/compact``: summarise the old conversation now (ADR-0047).

    Hands off to the app's compaction node via ``route="compact"``.  The
    message is a neutral fallback: the compaction node overwrites it with a
    confirmation only when it actually produces a summary, so a session with
    nothing old to fold keeps the fallback.  The outcome is always reported in
    the inspection pane by the node.

    :param state: The graph state.
    :param parsed: The parsed command.
    :returns: The command result carrying the handoff route.
    """
    logger.debug("command /compact: handing off to the compaction node")
    return GraphCommandResult(
        message="Nothing to compact yet.",
        summary="Compacting the conversation memory",
        route="compact",
    )


def _handle_init(state: BaseModel, parsed: ParsedCommand) -> GraphCommandResult:
    """Handle ``/init``: generate project guidance via the normal agent loop.

    Rather than a dedicated node, this hands off *back into the normal path*
    (``route="init"``) with the query replaced by :data:`_INIT_PROMPT`, so the
    ordinary planner/tool loop explores the project and writes ``AGENTS.md``.
    The trailing ``/init`` turn (appended by ``InitGraphState``) is replaced by
    the prompt so the model's memory reads coherently; the normal path sets the
    user-facing message.

    :param state: The graph state.
    :param parsed: The parsed command.
    :returns: The command result carrying the handoff route and prompt query.
    """
    logger.debug("command /init: handing off to the normal path with the prompt")
    messages = list(getattr(state, "messages", []) or [])
    query = getattr(state, "query", "")
    if messages and getattr(messages[-1], "content", None) == query:
        messages = [*messages[:-1], HumanMessage(content=_INIT_PROMPT)]
    return GraphCommandResult(
        summary="Generating project guidance (AGENTS.md)",
        updates={"query": _INIT_PROMPT, "messages": messages},
        route="init",
    )


def build_agent_commands(
    *, source_available: bool, disabled: Iterable[str] = (), memory: bool = True
) -> tuple[CommandRegistry, dict[str, GraphCommandHandler]]:
    """Build the agent's server-side command registry and graph handlers.

    :param source_available: Whether a curated knowledge source is configured
        (Scientific mode precondition, ADR-0030).
    :param disabled: Command names the operator disabled (ADR-0047): a disabled
        command is not registered, so it is neither published nor runnable.
    :param memory: Whether conversation memory is enabled.  ``/compact`` is
        registered only when it is (its handoff target, the compaction node,
        exists only then).
    :returns: ``(registry, handlers)`` -- the catalogue and the graph handler
        map the command node dispatches to.
    """
    disabled_names = {name.strip().lower() for name in disabled}
    registry = CommandRegistry()
    commands = [
        _MODE_COMMAND,
        _ACCESS_COMMAND,
        _CWD_COMMAND,
        _INIT_COMMAND,
        *_STUB_COMMANDS,
    ]
    if memory:
        commands.append(_COMPACT_COMMAND)
    for command in commands:
        if command.name in disabled_names:
            logger.info(f"session command /{command.name} disabled by config")
            continue
        registry.register(command)
    handlers: dict[str, GraphCommandHandler] = {}
    if registry.get("mode") is not None:
        handlers["mode"] = _handle_mode(source_available)
    if registry.get("access") is not None:
        handlers["access"] = _handle_access
    if registry.get("cwd") is not None:
        handlers["cwd"] = _handle_cwd
    if registry.get("init") is not None:
        handlers["init"] = _handle_init
    if registry.get("compact") is not None:
        handlers["compact"] = _handle_compact
    logger.debug(f"agent command catalogue: {len(registry.all())} command(s)")
    return registry, handlers
