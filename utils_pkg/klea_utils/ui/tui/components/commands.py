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
import os
from collections.abc import Mapping
from pathlib import Path
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

#: ``CommandContext.services`` key for the app hook that opens the session
#: picker (used by ``/sessions``).  The app registers a no-argument callable.
OPEN_SESSIONS_SERVICE = "open_sessions"

#: ``CommandContext.services`` key for the app hook that starts a new chat
#: (used by ``/new``).  The app registers a no-argument callable.
NEW_CHAT_SERVICE = "new_chat"

#: ``CommandContext.services`` key for the app hook that opens the model
#: dialog (used by ``/model``).  The app registers a no-argument callable.
OPEN_MODELS_SERVICE = "open_models"

#: ``CommandContext.services`` key for the app hook that quits the application
#: (used by ``/exit``).  The app registers a no-argument callable that stops
#: any active run, then exits.
EXIT_APP_SERVICE = "exit_app"

#: ``CommandContext.services`` key reserved for an optional app notification
#: when ``/stores`` records this chat's configuration file.  The handler writes
#: the per-chat state directly; a registered one-argument callable receives the
#: absolute path, or ``None`` for ``/stores clear``.
STORES_CONFIG_SERVICE = "stores_config"


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
    registry.register(
        Command(
            name="sessions",
            summary="List and switch chat sessions",
            side="client",
            while_streaming="block",
            handler=_handle_sessions,
        )
    )
    registry.register(
        Command(
            name="new",
            summary="Start a new chat",
            side="client",
            klass="ui",
            while_streaming="block",
            handler=_handle_new,
        )
    )
    registry.register(
        Command(
            name="model",
            summary="Show or set the models for this chat",
            side="client",
            while_streaming="block",
            handler=_handle_models,
        )
    )
    registry.register(
        Command(
            name="stores",
            summary="Set, or clear, this chat's stores configuration file",
            side="client",
            arg_hint="<path>|clear",
            while_streaming="block",
            handler=_handle_stores,
        )
    )
    registry.register(
        Command(
            name="exit",
            summary="Quit the application",
            side="client",
            while_streaming="allow",
            handler=_handle_exit,
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


def _handle_sessions(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Open the session picker via the app-provided hook.

    The handler itself renders nothing: opening the modal is the effect.  The
    hook is a no-argument callable the app registers under
    :data:`OPEN_SESSIONS_SERVICE` (it schedules the async action).
    """
    open_sessions = context.service(OPEN_SESSIONS_SERVICE)
    if open_sessions is None:
        return CommandResult.fail("The session picker is not available.")
    open_sessions()
    return CommandResult.ok()


def _handle_new(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Start a new chat via the app-provided hook.

    Like ``/sessions``, the handler renders nothing: switching to a fresh chat
    is the effect.  The hook is registered under :data:`NEW_CHAT_SERVICE`.
    """
    new_chat = context.service(NEW_CHAT_SERVICE)
    if new_chat is None:
        return CommandResult.fail("Starting a new chat is not supported here.")
    logger.debug("command /new: starting a new chat")
    new_chat()
    return CommandResult()


def _handle_models(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Open the model dialog via the app-provided hook.

    Like ``/sessions``, the handler renders nothing: opening the modal is the
    effect.  The hook is registered under :data:`OPEN_MODELS_SERVICE`.
    """
    open_models = context.service(OPEN_MODELS_SERVICE)
    if open_models is None:
        return CommandResult.fail("The model dialog is not available.")
    open_models()
    return CommandResult.ok()


def _handle_stores(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Set, or clear, this chat's stores configuration file.

    The handler validates the path locally, then records the resolved absolute
    path on this chat's ``stores_config`` slot (:func:`ensure_chat`), which the
    app turns into the outgoing tool-call ``_meta``.  ``clear`` sets the slot
    back to ``None`` so the deployment default applies.  Every check runs
    before the write, so a failure leaves the chat's state unchanged.
    """
    from klea_utils.ui.state import ensure_chat

    if len(parsed.args) != 1 or not parsed.args[0].strip():
        return CommandResult.fail("Usage: /stores <path>|clear")
    if not context.user_id or not context.chat_id:
        return CommandResult.fail("No active chat: /stores needs a chat session.")
    value = parsed.args[0].strip()
    if value.lower() == "clear":
        ensure_chat(context.user_id, context.chat_id)["stores_config"] = None
        logger.debug("command /stores: cleared the configuration")
        return CommandResult.ok(
            "Stores configuration cleared; using the deployment default."
        )
    path = Path(value).expanduser()
    if not path.is_absolute():
        return CommandResult.fail(
            f"Stores configuration path must be absolute: {value}"
        )
    try:
        absolute = Path(os.path.abspath(path))
        absolute.stat()
    except OSError:
        return CommandResult.fail(f"Stores configuration file does not exist: {path}")
    if not absolute.is_file():
        return CommandResult.fail(
            f"Stores configuration path is not a file: {absolute}"
        )
    ensure_chat(context.user_id, context.chat_id)["stores_config"] = str(absolute)
    logger.debug(f"command /stores: {absolute = }")
    return CommandResult.ok(f"Stores configuration set to {absolute}.")


def _handle_exit(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Quit the application via the app-provided hook.

    Like ``/sessions``, the handler renders nothing: quitting is the effect.
    The hook is registered under :data:`EXIT_APP_SERVICE` (it stops any active
    run, then exits).
    """
    exit_app = context.service(EXIT_APP_SERVICE)
    if exit_app is None:
        return CommandResult.fail("Quitting is not supported here.")
    exit_app()
    return CommandResult.ok()


def handle_command(
    text: str,
    catalogue: CommandRegistry | None,
    capabilities: frozenset[str] = TUI_CAPABILITIES,
    services: Mapping[str, Any] | None = None,
    user_id: str | None = None,
    chat_id: str | None = None,
) -> CommandResult | None:
    """Handle *text* as a command if it is one.

    Returns a :class:`CommandResult` for a client command (or an unknown /
    parse-error command) to render locally, or ``None`` when the caller should
    forward the input as a query (a server command, or a non-command).

    :param text: The raw input.
    :param catalogue: The merged command catalogue, or ``None`` if not loaded.
    :param capabilities: The TUI's capabilities.
    :param services: Extra frontend/app hooks for the handler context.
    :param user_id: The active user id, if a chat is open (handlers may need it).
    :param chat_id: The active chat id, if a chat is open.
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
    merged = {_REGISTRY_SERVICE: catalogue}
    if services:
        merged.update(services)
    context = CommandContext(
        capabilities=capabilities,
        services=merged,
        user_id=user_id or "",
        chat_id=chat_id or "",
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
