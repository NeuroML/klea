#!/usr/bin/env python3
"""
Session-command handling for the TUI frontend (ADR-0047).

The TUI owns its client-side commands and merges them with the **server
catalogue** fetched from ``GET /commands``.  An input beginning with ``/`` is
validated against the merge: a client command runs locally and its output is
rendered as a ``system`` transcript message; a server command is forwarded as
the query (the graph's command node runs it); an unknown command is rejected
locally and never sent.  A leading ``//`` escapes a literal slash.

Capabilities start empty (``devdocs/system/tui.md``): the TUI has no browser
file picker, and local-filesystem/shell capabilities are deferred with the
session-workspace work.

File: klea_utils/ui/tui/components/commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from klea_utils.commands.common import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    ParsedCommand,
    parse_command,
    render_help,
)

logger = logging.getLogger(__name__)

#: Capabilities the TUI has (ADR-0047).  Empty for now: no file picker, and
#: local-fs/shell capabilities are deferred with the session-workspace work.
TUI_CAPABILITIES: frozenset[str] = frozenset()

#: ``CommandContext.services`` key for the merged registry (used by ``/help``).
_REGISTRY_SERVICE = "registry"


def client_commands() -> CommandRegistry:
    """Return the TUI's own client-side commands (ADR-0047).

    :returns: A registry holding the commands the TUI handles itself.
    """
    registry = CommandRegistry()
    registry.register(
        Command(
            name="help",
            summary="List commands, or show one command's usage",
            side="client",
            arg_hint="[command]",
            handler=_handle_help,
        )
    )
    return registry


def build_catalogue(server_commands: list[dict[str, Any]]) -> CommandRegistry:
    """Merge the client commands with the server catalogue (ADR-0047).

    :param server_commands: Command metadata dicts from ``GET /commands``.
    :returns: The merged registry (client + server).
    """
    registry = client_commands()
    for data in server_commands:
        try:
            registry.register(Command.from_metadata(data))
        except (KeyError, ValueError) as e:
            logger.warning(f"skipping invalid command metadata: {e}")
    return registry


def _handle_help(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Render ``/help`` from the merged registry."""
    registry = context.service(_REGISTRY_SERVICE)
    name = parsed.args[0] if parsed.args else ""
    return render_help(registry, context.capabilities, name=name)


def handle_command(
    text: str,
    catalogue: CommandRegistry | None,
    capabilities: frozenset[str] = TUI_CAPABILITIES,
) -> CommandResult | None:
    """Handle *text* as a command if it is one.

    Returns a :class:`CommandResult` for a client command (or an unknown /
    parse-error command) to render locally, or ``None`` when the caller should
    forward the input as a query (a server command, or a non-command).

    :param text: The raw input.
    :param catalogue: The merged command catalogue, or ``None`` if not loaded.
    :param capabilities: The TUI's capabilities.
    :returns: A local result, or ``None`` to forward the input as a query.
    """
    parsed = parse_command(text)
    if parsed is None:
        return None
    if catalogue is None:
        return CommandResult.fail("The command catalogue is not loaded yet; try again.")
    if parsed.error:
        return CommandResult.fail(parsed.error)
    command = catalogue.get(parsed.name) if parsed.name else None
    if command is None:
        return CommandResult.fail(
            f"Unknown command: /{parsed.name}. Type /help for the list."
        )
    if command.side == "server":
        # Forward to the graph: the command node runs it (ADR-0047).
        return None
    context = CommandContext(
        capabilities=capabilities,
        services={_REGISTRY_SERVICE: catalogue},
    )
    return catalogue.dispatch(parsed, context)


class CommandSuggestions(OptionList):
    """A completion list of matching commands for a ``/prefix`` input."""

    DEFAULT_CSS = """
    CommandSuggestions {
        display: none;
        max-height: 6;
        border: solid $panel;
    }
    """

    @staticmethod
    def matches(
        value: str,
        catalogue: CommandRegistry | None,
        capabilities: frozenset[str] = TUI_CAPABILITIES,
    ) -> list[Command]:
        """Return the available commands matching a ``/prefix`` value.

        Only a leading ``/`` with no space matches (the space ends the
        completion, mirroring the web).

        :param value: The current input value.
        :param catalogue: The merged catalogue, or ``None`` if not loaded.
        :param capabilities: The TUI's capabilities.
        :returns: The matching commands, or ``[]``.
        """
        if catalogue is None:
            return []
        value = value.lstrip()
        if not value.startswith("/") or " " in value:
            return []
        prefix = value[1:].lower()
        return [
            command
            for command in catalogue.available(capabilities)
            if command.name.startswith(prefix)
        ]

    @staticmethod
    def _label(command: Command) -> Text:
        """Build the option label (usage + summary)."""
        usage = f"/{command.name}"
        if command.arg_hint:
            usage += f" {command.arg_hint}"
        text = Text(usage, style="bold")
        text.append(f"  {command.summary}", style="dim")
        return text

    def update_for(
        self,
        value: str,
        catalogue: CommandRegistry | None,
        capabilities: frozenset[str] = TUI_CAPABILITIES,
    ) -> None:
        """Refresh the list for *value*; hide it when there is no match.

        :param value: The current input value.
        :param catalogue: The merged catalogue, or ``None`` if not loaded.
        :param capabilities: The TUI's capabilities.
        """
        self.clear_options()
        matches = self.matches(value, catalogue, capabilities)
        if not matches:
            self.display = False
            return
        self.add_options([Option(self._label(c), id=c.name) for c in matches])
        self.display = True
