#!/usr/bin/env python3
"""
Common components of the session-command framework.

Commands are the user-driven analogue of tool calls (ADR-0047): the user
chooses the command instead of the model choosing a tool.  This module holds
the shared mechanics -- the ``Command`` metadata, a ``CommandRegistry``, the
``/command`` parser (with a ``//`` escape), the ``CommandContext`` handed to
handlers, the ``CommandResult`` they return, and ``/help`` rendering -- with
no frontend or app imports.  Each frontend routes an input that starts with
``/`` by the command's ``klass``; app-specific handlers are registered with
the app.  See ``devdocs/system/session-commands.md``.

File: klea_utils/commands/common.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import shlex
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)

#: Standard capability vocabulary: a command's ``capabilities`` must be a
#: subset of the frontend's for the frontend to expose/run it.  Defined
#: centrally so frontends filter consistently instead of inventing ad-hoc
#: predicates.
CAP_LOCAL_FS = "local_fs"
CAP_LOCAL_SHELL = "local_shell"
CAP_FILE_PICKER = "file_picker"
STANDARD_CAPABILITIES: frozenset[str] = frozenset(
    {CAP_LOCAL_FS, CAP_LOCAL_SHELL, CAP_FILE_PICKER}
)

#: Execution host of a command: ``server`` (graph/tools backend) or
#: ``client`` (wherever the frontend process runs).
Side = Literal["server", "client"]
#: Handling class: ``ui`` / client ``session-state`` run in the frontend;
#: ``workflow`` runs in the graph; ``prompt`` is a frontend-expanded template.
Klass = Literal["ui", "session-state", "workflow", "prompt"]
#: Whether the command may run while a chat has an active run.
WhileStreaming = Literal["block", "allow"]
#: What a command records: ``none`` (nothing), ``checkpoint`` (graph state,
#: no chat row), ``message`` (a real turn in the LLM's action history).
Persists = Literal["none", "checkpoint", "message"]


@dataclass(frozen=True)
class CommandResult:
    """The common outcome shape of a client-side command.

    :param output: Lines to display (help text, status, confirmations).
    :param error: A failure or usage message; frontends style it as an error.
    :param notice: An optional transient message (e.g. a toast).
    :param handled: Whether the input was consumed as a command (the frontend
        must not forward it as a query).
    """

    output: list[str] = field(default_factory=list)
    error: str = ""
    notice: str = ""
    handled: bool = True

    @classmethod
    def ok(cls, *lines: str) -> "CommandResult":
        """Return a success result carrying *lines*."""
        return cls(output=[line for line in lines if line])

    @classmethod
    def fail(cls, message: str) -> "CommandResult":
        """Return an error result carrying *message*."""
        return cls(error=message)

    @classmethod
    def info(cls, message: str) -> "CommandResult":
        """Return a success result with *message* as a transient notice."""
        return cls(notice=message)


@dataclass(frozen=True)
class ParsedCommand:
    """A parsed ``/command`` input.

    :param name: The command name (lower-cased, without the leading slash);
        empty when the input had no name (see :attr:`error`).
    :param args: Positional arguments (``shlex``-split, so quotes group).
    :param raw_args: The argument string exactly as typed.
    :param error: A parse error (missing name / malformed quoting), else ``""``.
    """

    name: str
    args: list[str] = field(default_factory=list)
    raw_args: str = ""
    error: str = ""


def parse_command(text: str) -> ParsedCommand | None:
    """Parse *text* as a ``/command``, or return ``None``.

    Only a leading ``/`` (after stripping surrounding whitespace) counts.  A
    leading ``//`` is an *escaped* literal slash and is not a command (use
    :func:`unescape_command` to recover the literal text).  A command with no
    name, or with malformed quoting, is returned with :attr:`ParsedCommand.error`
    set rather than raising.

    :param text: The user input.
    :returns: A :class:`ParsedCommand`, or ``None`` when *text* is not a command.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return None
    body = stripped[1:].strip()
    if not body:
        return ParsedCommand(name="", error="No command name given.")
    tokens = body.split(maxsplit=1)
    name = tokens[0].lower()
    raw_args = tokens[1] if len(tokens) > 1 else ""
    try:
        args = shlex.split(raw_args)
    except ValueError:
        logger.debug(f"Malformed command arguments: {raw_args = }")
        return ParsedCommand(
            name=name,
            raw_args=raw_args,
            error="Malformed arguments (check quoting).",
        )
    return ParsedCommand(name=name, args=args, raw_args=raw_args)


def unescape_command(text: str) -> str | None:
    """Return the literal input for an escaped command, or ``None``.

    A user types ``//foo`` to send the literal text ``/foo`` (a message, not
    a command); this strips one slash.  Returns ``None`` when *text* is not an
    escaped command.

    :param text: The user input.
    :returns: The unescaped text (``/foo``), or ``None``.
    """
    stripped = text.strip()
    if stripped.startswith("//"):
        return stripped[1:]
    return None


#: A client-side command handler: ``(context, parsed) -> CommandResult``.
CommandHandler = Callable[["CommandContext", ParsedCommand], CommandResult]


@dataclass(frozen=True)
class Command:
    """One entry in the command catalogue (ADR-0047).

    :param name: Canonical name, lower-case, without the leading slash.
    :param summary: One-line description (shown by ``/help`` and the menu).
    :param side: Execution host (``server`` | ``client``).
    :param klass: Handling class (``ui`` | ``session-state`` | ``workflow`` |
        ``prompt``).
    :param aliases: Alternative names.
    :param arg_hint: A short usage hint for the arguments (e.g. ``<path>``).
    :param while_streaming: ``block`` | ``allow`` while a run is active.
    :param persists: ``none`` | ``checkpoint`` | ``message``.
    :param capabilities: Frontend capabilities the command needs (subset of
        :data:`STANDARD_CAPABILITIES`).
    :param implemented: ``False`` marks a documented stub.
    :param handler: The client-side handler (``None`` for server commands and
        stubs).
    """

    name: str
    summary: str
    side: Side = "client"
    klass: Klass = "ui"
    aliases: tuple[str, ...] = ()
    arg_hint: str = ""
    while_streaming: WhileStreaming = "allow"
    persists: Persists = "none"
    capabilities: frozenset[str] = frozenset()
    implemented: bool = True
    handler: CommandHandler | None = None

    def metadata(self) -> dict[str, Any]:
        """Return the catalogue metadata (no handler) for ``GET /commands``."""
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "arg_hint": self.arg_hint,
            "side": self.side,
            "klass": self.klass,
            "while_streaming": self.while_streaming,
            "persists": self.persists,
            "capabilities": sorted(self.capabilities),
            "implemented": self.implemented,
        }


@dataclass
class CommandContext:
    """The capability surface a client-side handler is given.

    Carries the session identity, the frontend's capabilities, and a generic
    ``services`` mapping for frontend/app-provided hooks (e.g. a callable to
    open the model dialog or switch chat).  Keeping it generic means the shared
    core stays free of frontend and app imports.

    :param user_id: The session's user identifier.
    :param chat_id: The active chat identifier (empty before a chat exists).
    :param capabilities: The frontend's capabilities.
    :param services: Frontend/app hooks, keyed by convention.
    """

    user_id: str = ""
    chat_id: str = ""
    capabilities: frozenset[str] = frozenset()
    services: Mapping[str, Any] = field(default_factory=dict)

    def service(self, key: str, default: Any = None) -> Any:
        """Return the frontend/app hook registered under *key*, or *default*."""
        return self.services.get(key, default)


class CommandRegistry:
    """A per-app registry of commands, keyed by canonical name and aliases."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}

    def register(self, command: Command) -> None:
        """Add *command*, raising on an invalid or duplicate name/alias.

        :param command: The command to register.
        :raises ValueError: On an invalid name or a duplicate name/alias.
        """
        name = command.name.strip().lower()
        if not name or "/" in name or any(ch.isspace() for ch in name):
            raise ValueError(f"Invalid command name: {command.name!r}")
        if name in self._commands or name in self._aliases:
            raise ValueError(f"Duplicate command name: {name}")
        for alias in command.aliases:
            key = alias.strip().lower()
            if not key or key in self._aliases or key in self._commands:
                raise ValueError(f"Duplicate command alias: {alias}")
        self._commands[name] = command
        for alias in command.aliases:
            self._aliases[alias.strip().lower()] = name
        logger.debug(f"registered command /{name}")

    def get(self, name: str) -> Command | None:
        """Return the command for *name* or an alias, or ``None``."""
        key = name.strip().lower()
        if key in self._commands:
            return self._commands[key]
        canonical = self._aliases.get(key)
        return self._commands.get(canonical) if canonical else None

    def all(self) -> list[Command]:
        """Return every command, sorted by name."""
        return [self._commands[name] for name in sorted(self._commands)]

    def available(self, capabilities: Iterable[str] | None = None) -> list[Command]:
        """Return the publicly available commands, sorted by name.

        Only ``implemented`` commands are returned: a command still marked
        ``implemented=False`` is not published (so ``GET /commands``, ``/help``
        and the frontend menu never show a not-yet-real command).  When
        *capabilities* is given, commands requiring a capability the frontend
        lacks are excluded too.

        :param capabilities: The frontend's capabilities, or ``None`` for all.
        :returns: The available commands, sorted by name.
        """
        commands = [c for c in self.all() if c.implemented]
        if capabilities is not None:
            have = frozenset(capabilities)
            commands = [c for c in commands if c.capabilities <= have]
        return commands

    def dispatch(self, parsed: ParsedCommand, ctx: CommandContext) -> CommandResult:
        """Run *parsed* against the registry and return its result.

        Unknown names, stubs, and parse errors produce an error result rather
        than raising, so a frontend can render them directly.

        :param parsed: The parsed command.
        :param ctx: The handler context.
        :returns: The command's :class:`CommandResult`.
        """
        if parsed.error:
            return CommandResult.fail(parsed.error)
        command = self.get(parsed.name)
        if command is None:
            logger.debug(f"unknown command: {parsed.name}")
            return CommandResult.fail(
                f"Unknown command: /{parsed.name}. Type /help for the list."
            )
        if not command.implemented or command.handler is None:
            logger.debug(f"unimplemented command: {command.name}")
            return CommandResult.fail(
                f"Command /{command.name} is not implemented yet."
            )
        logger.debug(f"dispatching command /{command.name}")
        return command.handler(ctx, parsed)


def render_help(
    registry: CommandRegistry,
    capabilities: Iterable[str],
    name: str = "",
) -> CommandResult:
    """Render ``/help`` output for a frontend with *capabilities*.

    With *name*, show that command's detail; otherwise list the available
    commands (filtered by capability), one per line.

    :param registry: The command registry.
    :param capabilities: The frontend's capabilities.
    :param name: An optional command name to describe.
    :returns: A :class:`CommandResult` with the rendered lines.
    """
    if name:
        command = registry.get(name)
        if command is None or not command.implemented:
            return CommandResult.fail(
                f"Unknown command: /{name}. Type /help for the list."
            )
        header = f"/{command.name}"
        if command.arg_hint:
            header += f" {command.arg_hint}"
        lines = [header, f"  {command.summary}"]
        if command.aliases:
            lines.append(f"  aliases: {', '.join('/' + a for a in command.aliases)}")
        return CommandResult(output=lines)

    lines = ["Available commands:"]
    for command in registry.available(capabilities):
        usage = f"/{command.name}"
        if command.arg_hint:
            usage += f" {command.arg_hint}"
        lines.append(f"  {usage:<24} {command.summary}")
    return CommandResult(output=lines)
