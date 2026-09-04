#!/usr/bin/env python3
"""
Model configuration component: per-chat model overrides dialog.

File: klea_utils/ui/web/nicegui/components/model_dialog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import background_tasks, ui

from klea_utils.api.sse import fetch_active_models
from klea_utils.ui.web.nicegui.client import (
    clear_model_override,
    set_model_override,
)
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import ensure_chat

logger = logging.getLogger(__name__)


def attach_model_info(ctx: PageContext) -> None:
    """Register the model-info fetch and the model-config dialog.

    :param ctx: The shared page context; ``fetch_model_info`` and
        ``model_config_dialog`` are set here.
    """

    async def _fetch_model_info() -> None:
        """Fetch active model config for the current chat and update the status pane."""
        chat_id = ctx.chat_id
        if not chat_id:
            logger.debug("fetch model info: no active chat (user=%s)", ctx.user_id)
            return
        logger.debug("fetch model info: fetching for chat=%s", chat_id)
        active = await fetch_active_models(ctx.server_url, ctx.user_id, chat_id)
        if not active:
            logger.debug(
                "fetch model info: server returned no roles for chat=%s", chat_id
            )
            return
        current = ensure_chat(ctx.user_id, chat_id)
        current["model_info"] = active
        logger.debug(
            "fetch model info: cached %d role(s) for chat=%s",
            len(active),
            chat_id,
        )
        ctx.refresh_status_pane()

    async def _model_config_dialog() -> None:
        """Open a dialog to view and change per-role model overrides."""
        chat_id = ctx.chat_id
        if not chat_id:
            logger.debug("model config dialog: no active chat (user=%s)", ctx.user_id)
            return
        current_chat = ensure_chat(ctx.user_id, chat_id)
        current_info = current_chat.get("model_info", {})
        roles = list(current_info.keys())
        if not roles:
            # No model info yet (e.g. a chat created by typing the first
            # message). Fetch it on demand so the dialog has roles to render.
            logger.debug(
                "model config dialog: no roles cached, fetching model info for chat=%s",
                chat_id,
            )
            await _fetch_model_info()
            current_chat = ensure_chat(ctx.user_id, chat_id)
            current_info = current_chat.get("model_info", {})
            roles = list(current_info.keys())
            if not roles:
                logger.debug(
                    "model config dialog: still no roles after fetch for chat=%s",
                    chat_id,
                )
                return
        logger.debug("model config dialog: chat=%s roles=%s", chat_id, roles)

        dialog = ui.dialog()

        async def _save_role(role: str, model_inp, api_key_inp):
            payload = {"model": model_inp.value}
            if api_key_inp.value.strip():
                payload["api_key"] = api_key_inp.value.strip()
            logger.debug(
                "model config dialog: saving role=%s model=%s chat=%s",
                role,
                payload["model"],
                chat_id,
            )
            ok = await set_model_override(
                ctx.server_url, ctx.user_id, chat_id, role, payload
            )
            logger.debug(
                "model config dialog: save role=%s ok=%s chat=%s",
                role,
                ok,
                chat_id,
            )
            if ok:
                dialog.close()
                await _fetch_model_info()

        async def _clear_role(role: str):
            logger.debug("model config dialog: clearing role=%s chat=%s", role, chat_id)
            ok = await clear_model_override(ctx.server_url, ctx.user_id, chat_id, role)
            logger.debug(
                "model config dialog: clear role=%s ok=%s chat=%s",
                role,
                ok,
                chat_id,
            )
            if ok:
                dialog.close()
                await _fetch_model_info()

        with dialog, ui.card().classes("w-full p-4"):
            with ui.tabs().classes("w-full") as tabs:
                tab_map = {}
                for role in roles:
                    cfg = current_info.get(role, {})
                    modifiable = cfg.get("modifiable", True)
                    tab_icon = None
                    if cfg.get("overridden"):
                        tab_icon = "person"
                    elif not modifiable:
                        tab_icon = "lock"
                    tab_map[role] = ui.tab(
                        name=role.capitalize(),
                        label=role.capitalize(),
                        icon=tab_icon,
                    )
            with ui.tab_panels(tabs, value=roles[0].capitalize()).classes("w-full"):
                for role in roles:
                    cfg = current_info.get(role, {})
                    modifiable = cfg.get("modifiable", True)
                    with ui.tab_panel(tab_map[role]):
                        model_inp = ui.input(
                            "Model", value=cfg.get("model", "")
                        ).classes("w-full")
                        if not modifiable:
                            model_inp.disable()
                        with model_inp.add_slot("append"):
                            ui.icon("info").classes(
                                "text-sm cursor-pointer text-grey-5"
                            ).on(
                                "click",
                                lambda: ui.run_javascript(
                                    "window.open('https://neuroklea.org/install.html#choosing-models', '_blank')"
                                ),
                            )
                            ui.tooltip("See the docs for model selection options")
                        api_key_inp = ui.input(
                            "API key", password=True, password_toggle_button=True
                        ).classes("w-full")
                        if not modifiable:
                            api_key_inp.disable()
                        with api_key_inp:
                            ui.tooltip(
                                "Stored per-chat on the server.\n"
                                "Truncated in API responses.\n"
                                "Reset the override or delete the chat to remove."
                            ).classes("model-tooltip")
                        if not modifiable:
                            ui.label("Locked by administrator").classes(
                                "text-xs text-grey-5 italic"
                            )
                        else:
                            with ui.row().classes("w-full justify-end gap-2"):
                                ui.button(
                                    "Reset",
                                    on_click=lambda r=role: background_tasks.create(
                                        _clear_role(r)
                                    ),
                                ).props("flat")
                                ui.button(
                                    "Save",
                                    on_click=lambda r=role, m=model_inp, a=api_key_inp: (
                                        background_tasks.create(_save_role(r, m, a))
                                    ),
                                ).props("unelevated color=primary")

        dialog.open()

    ctx.fetch_model_info = _fetch_model_info
    ctx.model_config_dialog = _model_config_dialog
