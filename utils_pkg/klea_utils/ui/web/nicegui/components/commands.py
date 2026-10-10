#!/usr/bin/env python3
"""
Session-command handling for the web frontend (ADR-0047).

The web frontend owns its client-side commands and merges them with the
**server catalogue** fetched from ``GET /commands``.  An input beginning with
``/`` is validated against the merge: a client command runs locally and its
output is rendered as a ``system`` chat bubble; a server command is forwarded
as the query (the graph's command node runs it); an unknown command is
rejected locally and never sent.

File: klea_utils/ui/web/nicegui/components/commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from datetime import datetime
from typing import Any

from nicegui import ui

from klea_utils.commands.common import (
    CAP_FILE_PICKER,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    ParsedCommand,
    parse_command,
    render_help,
)
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import ensure_chat

logger = logging.getLogger(__name__)

#: Capabilities the browser frontend has (ADR-0047): it cannot read the user's
#: filesystem or run a shell, but it can show a file picker / upload.
WEB_CAPABILITIES: frozenset[str] = frozenset({CAP_FILE_PICKER})

#: ``CommandContext.services`` key for the merged registry (used by ``/help``).
_REGISTRY_SERVICE = "registry"


def client_commands() -> CommandRegistry:
    """Return the web frontend's own client-side commands (ADR-0047).

    :returns: A registry holding the commands the browser handles itself.
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


def handle_command(ctx: PageContext, text: str) -> bool:
    """Handle *text* as a command if it is one; return ``True`` when consumed.

    A client command (or an unknown / parse-error command) is handled locally
    and rendered as a ``system`` bubble.  A **server** command returns ``False``
    so the caller forwards it as the query; a non-command also returns
    ``False``.

    :param ctx: The page context.
    :param text: The raw input.
    :returns: ``True`` when the input was consumed as a client command.
    """
    parsed = parse_command(text)
    if parsed is None:
        return False
    registry: CommandRegistry | None = ctx.command_catalogue
    if registry is None:
        _render(ctx, "The command catalogue is not loaded yet; try again.")
        return True
    if parsed.error:
        _render(ctx, parsed.error)
        return True
    command = registry.get(parsed.name) if parsed.name else None
    if command is None:
        _render(ctx, f"Unknown command: /{parsed.name}. Type /help for the list.")
        return True
    if command.side == "server":
        # Forward to the graph: the command node runs it (ADR-0047).
        return False
    context = CommandContext(
        user_id=ctx.user_id,
        chat_id=ctx.chat_id,
        capabilities=WEB_CAPABILITIES,
        services={_REGISTRY_SERVICE: registry},
    )
    result = registry.dispatch(parsed, context)
    _render_result(ctx, result)
    return True


def _render_result(ctx: PageContext, result: CommandResult) -> None:
    """Render a client command's :class:`CommandResult` into the chat."""
    lines = [*result.output]
    if result.error:
        lines.append(result.error)
    if lines:
        _render(ctx, "\n".join(lines))
    if result.notice:
        ui.notification(result.notice, close_button=True)


def attach_autocomplete(ctx: PageContext, text: Any, suggestions: Any) -> None:
    """Attach a ``/``-command autocomplete list to the chat input (ADR-0047).

    As the user types ``/prefix``, *suggestions* (a container above the input)
    is populated with the matching commands from the merged catalogue;
    clicking one inserts ``/name `` into the input.

    :param ctx: The page context (its ``command_catalogue`` backs the list).
    :param text: The chat ``ui.textarea`` element.
    :param suggestions: The container element the suggestions render into.
    """
    # NOTE (future reference): this is a plain list, not ``ui.menu``.  A
    # Quasar ``QMenu`` (``ui.menu``) is click-anchored: it must be nested
    # inside the element that opens it (a ``ui.button``) and shown by that
    # click.  Opening one programmatically from a textarea / row - even with
    # an explicit ``target`` selector - does not render it, so a command
    # palette opened while typing uses this list instead.  ``ui.menu`` remains
    # the right choice for click-triggered context menus (e.g. the chat-list
    # row menu).

    def _matches(value: str) -> list[Command]:
        registry: CommandRegistry | None = ctx.command_catalogue
        # ``lstrip`` (not ``strip``): the trailing space inserted after a
        # chosen command is what ends the completion, so it must survive for
        # the ``" " in value`` check below (``strip`` would remove it and the
        # chosen command would keep matching).
        value = value.lstrip()
        if registry is None or not value.startswith("/") or " " in value:
            return []
        prefix = value[1:].lower()
        return [
            command
            for command in registry.available(WEB_CAPABILITIES)
            if command.name.startswith(prefix)
        ]

    def _choose(command: Command) -> None:
        # Setting the value fires ``on_value_change`` -> ``_refresh``, which
        # hides the list (the trailing space makes it a non-command).
        logger.debug(f"autocomplete: chose /{command.name}")
        text.value = f"/{command.name} "

    def _refresh(_event: Any = None) -> None:
        value = text.value or ""
        matches = _matches(value)
        logger.debug(f"autocomplete refresh {value = } {[c.name for c in matches] = }")
        suggestions.clear()
        if not matches:
            suggestions.set_visibility(False)
            return
        with (
            suggestions,
            ui.card()
            .tight()
            .classes("w-full")
            .style("max-height: 40vh; overflow-y: auto;"),
        ):
            for command in matches:
                usage = f"/{command.name}"
                if command.arg_hint:
                    usage += f" {command.arg_hint}"
                with (
                    ui.item(on_click=lambda c=command: _choose(c))
                    .props("clickable dense")
                    .classes("w-full cursor-pointer"),
                    # A single section keeps the summary next to the command
                    # (two sections would split the row into equal halves);
                    # ``gap-3`` sets the spacing between the two labels.
                    ui.item_section().classes("w-full"),
                    ui.row().classes("w-full items-baseline gap-3"),
                ):
                    ui.label(usage).classes("font-mono text-sm")
                    ui.label(command.summary).classes("text-sm text-grey-6")
        suggestions.set_visibility(True)

    # ``on_value_change`` fires on the real client event and in the test
    # simulation (which sets the value directly), unlike the raw
    # ``update:model-value`` listener.  The list hides itself when the value is
    # no longer a command prefix, so no blur handler is needed.
    text.on_value_change(_refresh)
    logger.debug("autocomplete: on_value_change registered")


def _render(ctx: PageContext, text: str) -> None:
    """Append a ``system`` block to the active chat, or notify when none."""
    if not ctx.chat_id:
        ui.notification(text, close_button=True)
        return
    stamp = datetime.now().astimezone().strftime("%X")
    ensure_chat(ctx.user_id, ctx.chat_id)["messages"].append(
        {"text": text, "stamp": stamp, "role": "system", "header": ""}
    )
    ctx.render_chat_area()
    ctx.scroll_chat_bottom()
