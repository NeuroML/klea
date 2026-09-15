#!/usr/bin/env python3
"""
Chat list component: left drawer with sessions and session management.

Holds the session list, chat switching / renaming / pinning / deletion,
the delete-user-session flow, and the drawer rail toggle.  All identity
and active-chat reads go through the shared :class:`PageContext`, so a
user-identity reset (delete all data) takes effect immediately for
every handler without a closure rebind.

File: klea_utils/ui/web/nicegui/components/chat_list.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import uuid
from datetime import datetime

import coolname
from nicegui import background_tasks, ui

from klea_utils.ui.web.nicegui.client import (
    create_chat_on_server,
    delete_chat_on_server,
    rename_chat_on_server,
)
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import safe_set_user
from klea_utils.ui.web.nicegui.state import chats, ensure_chat, get_chats_sorted

logger = logging.getLogger(__name__)


def attach_chat_list(ctx: PageContext) -> None:
    """Build the left drawer (session rail) and register its handlers.

    Registers ``refresh_chat_list`` and ``switch_chat`` on the context.

    :param ctx: The shared page context; ``left_drawer`` and
        ``toggle_icon`` are filled in here.
    """

    def _switch_chat(chat_id: str) -> None:
        """Switch the active chat without a page reload."""
        safe_set_user("chat_id", chat_id)
        ctx.chat_id = chat_id
        logger.debug("chat_id=%s user_id=%s", chat_id, ctx.user_id)
        ctx.render_chat_area()
        ctx.refresh_chat_list()
        ctx.refresh_status_pane()
        ctx.reset_center_tab()
        ctx.refresh_inspector()
        if ctx.fetch_model_info is not None:
            background_tasks.create(ctx.fetch_model_info())

    def _delete_chat(chat_id: str) -> None:
        """Remove a chat session from the store and server.

        If the currently active chat session is deleted, the next
        available chat session becomes active (or a new one is created).
        """
        logger.debug(
            "deleting chat_id=%s user_id=%s (current=%s)",
            chat_id,
            ctx.user_id,
            ctx.chat_id,
        )
        background_tasks.create(
            delete_chat_on_server(ctx.server_url, ctx.user_id, chat_id)
        )
        chats.pop(f"{ctx.user_id}:{chat_id}", None)
        if ctx.chat_id == chat_id:
            remaining = get_chats_sorted(ctx.user_id)
            if remaining:
                _switch_chat(remaining[0][0])
            else:
                ctx.chat_id = ""
                safe_set_user("chat_id", "")
                ctx.render_chat_area()
                ctx.refresh_chat_list()
                ctx.refresh_status_pane()
                ctx.reset_center_tab()
                ctx.refresh_inspector()
        else:
            ctx.refresh_chat_list()

    def _toggle_pin(chat_id: str) -> None:
        """Flip the pinned flag for a chat and refresh the list."""
        current_chat = ensure_chat(ctx.user_id, chat_id)
        current_chat["pinned"] = not current_chat["pinned"]
        logger.debug(
            "toggled pin for chat=%s pinned=%s", chat_id, current_chat["pinned"]
        )
        ctx.refresh_chat_list()

    def _rename_chat(chat_id: str) -> None:
        """Open a dialog to rename a chat and persist on server."""
        chat = ensure_chat(ctx.user_id, chat_id)
        logger.debug("opening rename dialog for chat=%s", chat_id)
        dialog = ui.dialog()

        async def _save():
            chat["name"] = inp.value
            dialog.close()
            logger.debug("renaming chat=%s to %r", chat_id, inp.value)
            await rename_chat_on_server(ctx.server_url, ctx.user_id, chat_id, inp.value)
            ctx.refresh_chat_list()
            ctx.refresh_status_pane()

        with dialog, ui.card():
            ui.label("Rename chat").classes("text-lg font-bold")
            inp = ui.input(value=chat["name"]).on("keydown.enter", _save)
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Save", on_click=_save).props("unelevated color=primary")
        dialog.open()

    def _new_chat() -> None:
        """Create a new chat and switch to it."""
        chat_id = coolname.generate_slug(2)
        logger.debug("creating chat_id=%s", chat_id)
        ensure_chat(ctx.user_id, chat_id)
        _switch_chat(chat_id)
        background_tasks.create(
            create_chat_on_server(ctx.server_url, ctx.user_id, chat_id)
        )

    @ui.refreshable
    def _render_chat_list():
        """Render the sorted list of chat sessions in the left drawer.

        Each entry shows the chat name (bold for the active chat),
        a tooltip with the creation timestamp, and a three-dot context
        menu for rename / pin / delete.  Refresh this to pick up newly
        created chat sessions without a page reload.
        """
        # Underscore suffix avoids shadowing the enclosing chat_id params
        for chat_id_, sdata in get_chats_sorted(ctx.user_id):
            is_current = chat_id_ == ctx.chat_id
            with (
                ui.item(
                    on_click=lambda s=chat_id_: _switch_chat(s),
                )
                .props("dense")
                .classes("w-full")
                .on("dblclick", lambda s=chat_id_: _rename_chat(s))
            ):
                with ui.item_section().props("avatar"):
                    ui.icon("push_pin" if sdata["pinned"] else "history")
                with ui.item_section():
                    label_cls = "text-xs font-bold" if is_current else "text-xs"
                    ui.label(sdata["name"]).classes(label_cls)
                ui.tooltip(
                    "Created: "
                    + datetime.fromtimestamp(sdata["created"])
                    .astimezone()
                    .strftime("%a %d %b %Y at %X")
                )
                # Three-dot context menu (right-aligned).
                with (
                    ui.item_section().props("side"),
                    ui.button(icon="more_vert")
                    .props("flat dense round")
                    .classes("icon-btn")
                    .on("click.stop", lambda: None),
                    ui.menu(),
                ):
                    with ui.menu_item(on_click=lambda s=chat_id_: _rename_chat(s)):
                        with ui.item_section().props("avatar"):
                            ui.icon("edit")
                        with ui.item_section():
                            ui.label("Rename")
                    with ui.menu_item(on_click=lambda s=chat_id_: _toggle_pin(s)):
                        with ui.item_section().props("avatar"):
                            ui.icon("push_pin")
                        with ui.item_section():
                            ui.label("Unpin" if sdata["pinned"] else "Pin")
                    with ui.menu_item(on_click=lambda s=chat_id_: _delete_chat(s)):
                        with ui.item_section().props("avatar"):
                            ui.icon("delete")
                        with ui.item_section():
                            ui.label("Delete")

    def _delete_all_data():
        """Show a confirmation dialog before deleting the user session."""
        logger.debug("opening delete-user-session dialog for user_id=%s", ctx.user_id)
        dialog = ui.dialog()
        with dialog, ui.card():
            ui.label("Delete user session?").classes("text-lg font-bold")
            ui.label(
                "This will permanently delete all your chats, messages, and "
                "checkpoints from the server. This cannot be undone."
            ).classes("text-sm")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Delete", on_click=lambda: _confirm_delete_all(dialog)).props(
                    "unelevated color=negative"
                )
        dialog.open()

    async def _confirm_delete_all(dialog: ui.dialog):
        """DELETE all server data, reset in-memory state, and generate a new user ID."""
        import httpx

        old_id = ctx.user_id
        logger.debug("confirming delete of user session for user_id=%s", old_id)
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.delete(f"{ctx.server_url}/chat/{old_id}")
                if resp.status_code == 200:
                    logger.debug("delete_user_session succeeded for user_id=%s", old_id)
                else:
                    logger.warning(
                        "delete_user_session failed: HTTP %s for user_id=%s",
                        resp.status_code,
                        old_id,
                    )
                    ui.notification(
                        f"Failed to delete session (HTTP {resp.status_code}). "
                        "No data was cleared.",
                        type="negative",
                        timeout=10000,
                        close_button=True,
                    )
                    dialog.close()
                    return
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to delete user session: %s", e)
            ui.notification(
                f"Failed to delete session: {e}",
                type="negative",
                timeout=10000,
                close_button=True,
            )
            dialog.close()
            return

        logger.debug("clearing in-memory chats for user_id=%s", old_id)
        chat_key_prefix = f"{old_id}:"
        for key in list(chats.keys()):
            if key.startswith(chat_key_prefix):
                chats.pop(key, None)

        ctx.chat_id = ""
        safe_set_user("chat_id", "")
        new_id = str(uuid.uuid4())
        safe_set_user("user_id", new_id)
        # Rebind the shared context so every handler (new chat, send,
        # status pane, ...) uses the fresh identity for the rest of this
        # page session.
        ctx.user_id = new_id
        logger.debug(
            "reset local state: new user_id=%s (was %s)",
            ctx.user_id,
            old_id,
        )
        ctx.render_chat_area()
        ctx.refresh_chat_list()
        ctx.refresh_status_pane()
        dialog.close()

    def _toggle_left_drawer():
        """Switch the left drawer between mini (rail) and full width.

        When *mini_state* is ``True`` the drawer shows a 64 px narrow
        rail with only item icons; otherwise it expands to ``w-80``
        (320 px) showing icons and labels.  The toggle-button icon
        changes direction to hint at the available action.
        """
        ctx.mini_state = not ctx.mini_state
        logger.debug("toggling left drawer: mini_state=%s", ctx.mini_state)
        if ctx.mini_state:
            ctx.left_drawer.props("mini")
            ctx.toggle_icon.name = "keyboard_double_arrow_right"
        else:
            ctx.left_drawer.props(remove="mini")
            ctx.toggle_icon.name = "keyboard_double_arrow_left"

    ctx.switch_chat = _switch_chat
    ctx.refresh_chat_list = _render_chat_list.refresh

    # ---- Left drawer (rail mode by default) ----
    with (
        ui.left_drawer(value=True)
        .props("mini")
        .props("width=320")
        .classes("overflow-x-hidden p-2") as left_drawer
    ):
        ctx.left_drawer = left_drawer
        # Items use QItem + QItemSection(avatar) so that Quasar
        # automatically hides the label when the drawer is in mini mode.
        with ui.item(on_click=_new_chat).props("dense").classes("w-full"):
            with ui.item_section().props("avatar"):
                ui.icon("add")
                ui.tooltip("Start a new conversation")
            with ui.item_section():
                ui.label("New Chat")

        # Session list header (no icon  ---  plain text signals a section heading)
        with ui.item().props("dense").classes("w-full"), ui.item_section():
            ui.label("Chats").classes("text-sm font-bold")

        _render_chat_list()

        ui.space()

        with ui.item(on_click=_delete_all_data).props("dense").classes("w-full"):
            with ui.item_section().props("avatar"):
                ui.icon("delete")
                ui.tooltip("Delete all data for this user session")
            with ui.item_section():
                ui.label("Delete user session").classes("text-xs")

        # Toggle button at the bottom of the drawer.
        with ui.item(on_click=_toggle_left_drawer).props("dense").classes("w-full"):
            with ui.item_section().props("avatar"):
                ctx.toggle_icon = ui.icon("keyboard_double_arrow_right")
                ui.tooltip("Expand or collapse the sidebar")
            with ui.item_section():
                ui.label("").classes("text-xs")
