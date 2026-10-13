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
from dataclasses import dataclass, field, replace
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
    :param verb: The resolved subcommand name (set by
        :func:`resolve_subcommand`); ``""`` for the bare form or a command
        without subcommands.
    """

    name: str
    args: list[str] = field(default_factory=list)
    raw_args: str = ""
    error: str = ""
    verb: str = ""


def parse_command(text: str) -> ParsedCommand | None:
    """Parse *text* as a ``/command``, or return ``None``.

    Only a leading ``/`` as the **literal first character** counts (no leading
    whitespace).  A leading ``//`` is an *escaped* literal slash and is not a
    command (use :func:`unescape_command` to recover the literal text).  A
    command with no name, or with malformed quoting, is returned with
    :attr:`ParsedCommand.error` set rather than raising.

    :param text: The user input.
    :returns: A :class:`ParsedCommand`, or ``None`` when *text* is not a command.
    """
    if not text.startswith("/") or text.startswith("//"):
        return None
    body = text[1:].strip()
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
    logger.debug(f"parsed command /{name} with {len(args)} arg(s)")
    return ParsedCommand(name=name, args=args, raw_args=raw_args)


def unescape_command(text: str) -> str | None:
    """Return the literal input for an escaped command, or ``None``.

    A user types ``//foo`` to send the literal text ``/foo`` (a message, not
    a command); this strips one slash.  The ``//`` must be the **literal first
    characters** (no leading whitespace).  Returns ``None`` when *text* is not
    an escaped command.

    :param text: The user input.
    :returns: The unescaped text (``/foo``), or ``None``.
    """
    if text.startswith("//"):
        return text[1:]
    return None


#: A client-side command handler: ``(context, parsed) -> CommandResult``.
CommandHandler = Callable[["CommandContext", ParsedCommand], CommandResult]


@dataclass(frozen=True)
class Subcommand:
    """A first-class verb of a command (ADR-0047).

    A command with ``subcommands`` is a small verb tree: the first argument
    selects the verb (see :func:`resolve_subcommand`), and the remaining
    arguments are the verb's own.  The command's single handler branches on
    ``ParsedCommand.verb``.

    :param name: Canonical verb name (lower-case, without the leading slash).
    :param summary: One-line description (shown by ``/help``).
    :param arg_hint: A short usage hint for the verb's arguments.
    """

    name: str
    summary: str
    arg_hint: str = ""


@dataclass(frozen=True)
class Command:
    """One entry in the command catalogue (ADR-0047).

    :param name: Canonical name, lower-case, without the leading slash.
    :param summary: One-line description (shown by ``/help`` and the menu).
    :param side: Execution host (``server`` | ``client``).
    :param klass: Handling class (``ui`` | ``session-state`` | ``workflow`` |
        ``prompt``).
    :param aliases: Alternative names.
    :param subcommands: First-class verbs (see :class:`Subcommand`); empty for
        a plain command.
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
    subcommands: tuple[Subcommand, ...] = ()
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
            "subcommands": [
                {
                    "name": sub.name,
                    "summary": sub.summary,
                    "arg_hint": sub.arg_hint,
                }
                for sub in self.subcommands
            ],
            "side": self.side,
            "klass": self.klass,
            "while_streaming": self.while_streaming,
            "persists": self.persists,
            "capabilities": sorted(self.capabilities),
            "implemented": self.implemented,
        }

    @classmethod
    def from_metadata(cls, data: Mapping[str, Any]) -> "Command":
        """Reconstruct a :class:`Command` from :meth:`metadata` output.

        Used by a frontend to add the server catalogue to its own client
        commands.  The handler is not carried over (``None``).

        :param data: A ``metadata()`` dict.
        :returns: The reconstructed command.
        """
        return cls(
            name=data["name"],
            summary=data.get("summary", ""),
            side=data.get("side", "client"),
            klass=data.get("klass", "ui"),
            aliases=tuple(data.get("aliases", ())),
            subcommands=tuple(
                Subcommand(
                    name=sub["name"],
                    summary=sub.get("summary", ""),
                    arg_hint=sub.get("arg_hint", ""),
                )
                for sub in data.get("subcommands", ())
            ),
            arg_hint=data.get("arg_hint", ""),
            while_streaming=data.get("while_streaming", "allow"),
            persists=data.get("persists", "none"),
            capabilities=frozenset(data.get("capabilities", ())),
            implemented=data.get("implemented", True),
        )


def resolve_subcommand(command: Command, parsed: ParsedCommand) -> ParsedCommand | str:
    """Resolve a command's verb from *parsed*, or return an error message.

    Commands without ``subcommands`` are returned unchanged.  For a command
    with subcommands the first argument selects the verb:

    * an empty argument list resolves to the bare form (``verb == ""``, the
      command's default behaviour, e.g. a listing);
    * a known verb name returns a copy with ``verb`` set and ``args``/``raw_args``
      reduced to the verb's own arguments;
    * an unknown verb yields an error string naming the valid verbs.

    :param command: The resolved command.
    :param parsed: The parsed input.
    :returns: The updated :class:`ParsedCommand`, or an error string.
    """
    if not command.subcommands:
        return parsed
    if not parsed.args:
        return replace(parsed, verb="", args=[], raw_args="")
    verbs = {sub.name.lower(): sub.name for sub in command.subcommands}
    verb = verbs.get(parsed.args[0].lower())
    if verb is None:
        names = ", ".join(sub.name for sub in command.subcommands)
        return (
            f"Unknown subcommand for /{command.name}: {parsed.args[0]!r}. "
            f"Use one of: {names}."
        )
    # Split the raw argument string the same way ``parse_command`` separates
    # the name from its arguments, so ``raw_args`` drops the verb token.
    parts = parsed.raw_args.split(maxsplit=1)
    remainder_raw = parts[1] if len(parts) > 1 else ""
    return replace(parsed, verb=verb, args=parsed.args[1:], raw_args=remainder_raw)


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
        seen_verbs: set[str] = set()
        for sub in command.subcommands:
            key = sub.name.strip().lower()
            if not key or "/" in key or any(ch.isspace() for ch in key):
                raise ValueError(f"Invalid subcommand name: {sub.name!r}")
            if key in seen_verbs:
                raise ValueError(f"Duplicate subcommand: /{name} {key}")
            seen_verbs.add(key)
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
        resolved = resolve_subcommand(command, parsed)
        if isinstance(resolved, str):
            logger.debug(f"command /{command.name}: {resolved}")
            return CommandResult.fail(resolved)
        logger.debug(
            f"dispatching command /{command.name} {resolved.verb = } {resolved.args = }"
        )
        result = command.handler(ctx, resolved)
        logger.debug(
            f"command /{command.name} result: {result.handled = } "
            f"{bool(result.error) = } {len(result.output) = }"
        )
        return result


def render_help(
    registry: CommandRegistry,
    capabilities: Iterable[str],
    name: str = "",
) -> CommandResult:
    """Render ``/help`` output for a frontend with *capabilities*.

    With *name*, show that command's detail; otherwise list the available
    commands (filtered by capability).  The output is Markdown: the command
    usages in inline code and, for a command detail, bold ``Aliases`` /
    ``Subcommands`` labels.  A chat frontend then renders one command per line
    with a monospaced usage (plain newlines would collapse into a single
    paragraph).

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
        usage = f"/{command.name}"
        if command.arg_hint:
            usage += f" {command.arg_hint}"
        lines = [f"`{usage}`", "", command.summary]
        if command.aliases:
            aliases = ", ".join(f"`/{alias}`" for alias in command.aliases)
            lines += ["", f"**Aliases:** {aliases}"]
        if command.subcommands:
            lines += ["", "**Subcommands:**", ""]
            for sub in command.subcommands:
                sub_usage = f"/{command.name} {sub.name}"
                if sub.arg_hint:
                    sub_usage += f" {sub.arg_hint}"
                lines.append(f"- `{sub_usage}` - {sub.summary}")
        return CommandResult(output=lines)

    lines = ["**Available commands:**", ""]
    for command in registry.available(capabilities):
        usage = f"/{command.name}"
        if command.arg_hint:
            usage += f" {command.arg_hint}"
        lines.append(f"- `{usage}` - {command.summary}")
    return CommandResult(output=lines)
