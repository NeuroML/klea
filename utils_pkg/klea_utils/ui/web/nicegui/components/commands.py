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
from klea_utils.ui.export import (
    export_filename,
    export_timestamp,
    transcript_markdown,
)
from klea_utils.ui.state import ensure_chat
from klea_utils.ui.web.nicegui.components.context import PageContext

logger = logging.getLogger(__name__)

#: Capabilities the browser frontend has (ADR-0047): it cannot read the user's
#: filesystem or run a shell, but it can show a file picker / upload.
WEB_CAPABILITIES: frozenset[str] = frozenset({CAP_FILE_PICKER})

#: ``CommandContext.services`` key for the merged registry (used by ``/help``).
_REGISTRY_SERVICE = "registry"

#: ``CommandContext.services`` key for the "start a new chat" hook (``/new``).
_NEW_CHAT_SERVICE = "new_chat"

#: ``CommandContext.services`` key for the "rename a chat" hook (``/rename``).
_RENAME_CHAT_SERVICE = "rename_chat"

#: ``CommandContext.services`` key for the "open the model dialog" hook.
_MODEL_DIALOG_SERVICE = "show_model_dialog"

#: ``CommandContext.services`` key for the "save an export" hook (``/export``):
#: ``(filename, content) -> None``.
_SAVE_EXPORT_SERVICE = "save_export"


def _save_export(filename: str, content: str) -> None:
    """Deliver an export to the user as a browser download (default hook)."""
    logger.debug(f"command /export: downloading {filename = } {len(content) = }")
    ui.download.content(content, filename)


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
    registry.register(
        Command(
            name="new",
            summary="Start a new session",
            side="client",
            klass="ui",
            while_streaming="allow",
            handler=_handle_new,
        )
    )
    registry.register(
        Command(
            name="rename",
            summary="Rename the current session",
            side="client",
            klass="ui",
            arg_hint="<name>",
            while_streaming="allow",
            handler=_handle_rename,
        )
    )
    registry.register(
        Command(
            name="model",
            summary="Open the model and API-key configuration",
            side="client",
            klass="ui",
            while_streaming="allow",
            handler=_handle_model,
        )
    )
    registry.register(
        Command(
            name="export",
            summary="Export the session transcript as Markdown",
            side="client",
            klass="ui",
            while_streaming="allow",
            handler=_handle_export,
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
    logger.debug(f"command /help: {name = }")
    return render_help(registry, context.capabilities, name=name)


def _handle_new(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Start a new session via the frontend's ``new_chat`` hook.

    Silent on success: the effect is the view switching to a fresh chat, so no
    ``system`` bubble is rendered (the client-command contract allows an
    output-less result, ADR-0047).
    """
    new_chat = context.service(_NEW_CHAT_SERVICE)
    if new_chat is None:
        logger.warning("command /new: new_chat hook is not registered")
        return CommandResult.fail("Starting a new session is not supported here.")
    logger.debug("command /new: starting a new session")
    new_chat()
    return CommandResult()


def _handle_rename(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Rename the active session via the frontend's ``rename_chat`` hook.

    The name is the raw argument (spaces preserved); an empty name or no active
    session is a usage error.
    """
    name = parsed.raw_args.strip()
    if not name:
        logger.warning("command /rename: no name given")
        return CommandResult.fail("Usage: /rename <new name>")
    if not context.chat_id:
        logger.warning("command /rename: no active session to rename")
        return CommandResult.fail("No active session to rename.")
    rename = context.service(_RENAME_CHAT_SERVICE)
    if rename is None:
        logger.warning("command /rename: rename_chat hook is not registered")
        return CommandResult.fail("Renaming a session is not supported here.")
    logger.debug(f"command /rename: {context.chat_id = } {name = }")
    rename(context.chat_id, name)
    return CommandResult.ok(f"Renamed session to {name!r}")


def _handle_model(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Open the model/credential dialog for the active scope (ADR-0047).

    Silent on success: the dialog is the feedback, so no ``system`` bubble is
    rendered.  The active chat's scope is chosen by the dialog itself (session
    scope when no chat is active).
    """
    show = context.service(_MODEL_DIALOG_SERVICE)
    if show is None:
        logger.warning("command /model: show_model_dialog hook is not registered")
        return CommandResult.fail("Model configuration is not supported here.")
    logger.debug(f"command /model: opening the dialog {context.chat_id = }")
    show()
    return CommandResult()


def _handle_export(context: CommandContext, parsed: ParsedCommand) -> CommandResult:
    """Export the active session's transcript as a Markdown download.

    Reads the chat's rendered transcript from the shared state store and hands
    the Markdown to the frontend's ``save_export`` hook (a browser download for
    the web).  The durable server history is only the curated user/assistant
    rows; this exports the richer in-session transcript (including tool/system
    blocks) the frontend currently holds.
    """
    if not context.chat_id:
        logger.warning("command /export: no active session to export")
        return CommandResult.fail("No active session to export.")
    chat = ensure_chat(context.user_id, context.chat_id)
    messages = chat.get("messages") or []
    if not messages:
        logger.warning(f"command /export: nothing to export {context.chat_id = }")
        return CommandResult.fail("This session has nothing to export yet.")
    save = context.service(_SAVE_EXPORT_SERVICE)
    if save is None:
        logger.warning("command /export: save_export hook is not registered")
        return CommandResult.fail("Exporting a session is not supported here.")
    title = chat.get("name") or context.chat_id
    content = transcript_markdown(messages, title=title, exported_at=export_timestamp())
    filename = export_filename(title)
    logger.debug(
        f"command /export: {context.chat_id = } {len(messages) = } "
        f"{filename = } {len(content) = }"
    )
    save(filename, content)
    return CommandResult.ok(f"Exported the session transcript to {filename}.")


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
    logger.debug(f"handle_command: {parsed.name = }")
    registry: CommandRegistry | None = ctx.command_catalogue
    if registry is None:
        logger.warning("command catalogue not loaded; cannot handle command")
        _render(ctx, "The command catalogue is not loaded yet; try again.")
        return True
    if parsed.error:
        logger.warning(f"command parse error: {parsed.error}")
        _render(ctx, parsed.error)
        return True
    command = registry.get(parsed.name) if parsed.name else None
    if command is None:
        logger.warning(f"unknown command /{parsed.name}")
        _render(ctx, f"Unknown command: /{parsed.name}. Type /help for the list.")
        return True
    if command.side == "server":
        # Forward to the graph: the command node runs it (ADR-0047).
        logger.info(f"forwarding server command /{command.name} to the graph")
        return False
    logger.info(f"handling client command /{command.name}")
    context = CommandContext(
        user_id=ctx.user_id,
        chat_id=ctx.chat_id,
        capabilities=WEB_CAPABILITIES,
        services={
            _REGISTRY_SERVICE: registry,
            _NEW_CHAT_SERVICE: ctx.new_chat,
            _RENAME_CHAT_SERVICE: ctx.rename_chat,
            _MODEL_DIALOG_SERVICE: ctx.show_model_dialog,
            _SAVE_EXPORT_SERVICE: _save_export,
        },
    )
    result = registry.dispatch(parsed, context)
    _render_result(ctx, result)
    return True


def _render_result(ctx: PageContext, result: CommandResult) -> None:
    """Render a client command's :class:`CommandResult` into the chat."""
    logger.debug(
        f"command result: {bool(result.error) = } {len(result.output) = } "
        f"{bool(result.notice) = } {result.handled = }"
    )
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
    #
    # NOTE (future reference): keyboard navigation (Up/Down to highlight, Tab
    # to complete, Escape to close) is deliberately not implemented.  The
    # command is fully usable by typing it and pressing Enter, so this list is
    # an enhancement, not the only path.  A Vue key modifier such as
    # ``keydown.tab.exact.prevent`` is unconditional: it would block plain Tab
    # (breaking focus order out of the chat box) even while the list is
    # closed.  Doing it properly needs a client-side ``js_handler`` that
    # conditionally calls ``preventDefault()``, plus combobox ARIA wiring.
    # Revisit only if the catalogue grows enough that typing the full command
    # becomes a chore.

    #: A suggestion row: ``(insert_text, usage, summary)``.
    def _command_rows(prefix: str) -> list[tuple[str, str, str]]:
        registry: CommandRegistry | None = ctx.command_catalogue
        rows: list[tuple[str, str, str]] = []
        if registry is None:
            return rows
        for command in registry.available(WEB_CAPABILITIES):
            if not command.name.startswith(prefix):
                continue
            usage = f"/{command.name}"
            if command.arg_hint:
                usage += f" {command.arg_hint}"
            rows.append((f"/{command.name} ", usage, command.summary))
        return rows

    def _verb_rows(command: Command, prefix: str) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for sub in command.subcommands:
            if not sub.name.startswith(prefix):
                continue
            usage = f"/{command.name} {sub.name}"
            if sub.arg_hint:
                usage += f" {sub.arg_hint}"
            rows.append((f"/{command.name} {sub.name} ", usage, sub.summary))
        return rows

    def _matches(value: str) -> list[tuple[str, str, str]]:
        registry: CommandRegistry | None = ctx.command_catalogue
        # A command is the whole input: it must start with ``/`` as its literal
        # first character (no leading whitespace).
        if registry is None or not value.startswith("/"):
            return []
        body = value[1:]
        if " " not in body:
            return _command_rows(body.lower())
        # ``/command <verbprefix>``: suggest that command's verbs.
        command_name, _, verb_prefix = body.partition(" ")
        command = registry.get(command_name)
        if command is None or not command.subcommands:
            return []
        return _verb_rows(command, verb_prefix.lower())

    def _choose(insert: str) -> None:
        # Setting the value fires ``on_value_change`` -> ``_refresh``, which
        # hides the list (the trailing space ends the completion).
        logger.debug(f"autocomplete: chose {insert!r}")
        text.value = insert

    def _refresh(_event: Any = None) -> None:
        value = text.value or ""
        # Only ``/``-prefixed input is a command candidate; skip the match work
        # (and the log) for ordinary typing.
        if not value.startswith("/"):
            suggestions.clear()
            suggestions.set_visibility(False)
            return
        matches = _matches(value)
        logger.debug(
            f"autocomplete refresh {value = } "
            f"{[usage for _insert, usage, _summary in matches] = }"
        )
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
            for insert, usage, summary in matches:
                with (
                    ui.item(on_click=lambda s=insert: _choose(s))
                    .props("clickable dense")
                    .classes("w-full cursor-pointer"),
                    # A single section keeps the summary next to the command
                    # (two sections would split the row into equal halves);
                    # ``gap-3`` sets the spacing between the two labels.
                    ui.item_section().classes("w-full"),
                    ui.row().classes("w-full items-baseline gap-3"),
                ):
                    ui.label(usage).classes("font-mono text-sm")
                    ui.label(summary).classes("text-sm text-grey-6")
        suggestions.set_visibility(True)

    # ``on_value_change`` fires on the real client event and in the test
    # simulation (which sets the value directly), unlike the raw
    # ``update:model-value`` listener.  The list hides itself when the value is
    # no longer a command prefix, so no blur handler is needed.
    text.on_value_change(_refresh)
    logger.debug("autocomplete: on_value_change registered")


def _render(ctx: PageContext, text: str) -> None:
    """Append a ``system`` block to the active chat, creating one if needed."""
    logger.debug(f"rendering command output ({len(text)} chars)")
    chat_id = ctx.ensure_active_chat()
    stamp = datetime.now().astimezone().strftime("%X")
    ensure_chat(ctx.user_id, chat_id)["messages"].append(
        {"text": text, "stamp": stamp, "role": "system", "header": ""}
    )
    ctx.render_chat_area()
    ctx.scroll_chat_bottom()
