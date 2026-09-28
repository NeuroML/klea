#!/usr/bin/env python3
"""
Model configuration component: per-session / per-chat model overrides and
the provider credentials editor.

Models are selected per role (chat/plan/guard/...).  The dialog edits the
per-session defaults when no chat is active and the current chat's
overrides otherwise; saving in a chat also promotes the changed role into
the session defaults ("last used").  API keys are not part of the model
override  ---  they are provider-scoped and edited in a separate, masked
(write-only) credentials dialog.

File: klea_utils/ui/web/nicegui/components/model_dialog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import ui

from klea_utils.api.sse import (
    fetch_active_models,
    fetch_credentials,
    fetch_session_models,
)
from klea_utils.ui.web.nicegui.client import (
    clear_credential,
    clear_model_override,
    clear_session_model_override,
    set_credential,
    set_model_override,
    set_session_model_override,
)
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import ensure_chat

logger = logging.getLogger(__name__)


def attach_model_info(ctx: PageContext) -> None:
    """Register the model-info fetches and the model-config dialog.

    :param ctx: The shared page context; ``fetch_model_info``,
        ``fetch_session_model_info`` and ``model_config_dialog`` are set
        here.
    """
    logger.debug(f"{ctx.user_id = }")
    # A valid slot for building dialogs when invoked from a context without
    # an ambient slot (the first-run prompt runs in a background task).
    ctx.dialog_container = ui.element("div").classes("hidden")

    async def _fetch_model_info() -> None:
        """Fetch active model config for the current chat (chat scope)."""
        chat_id = ctx.chat_id
        logger.debug(f"fetch chat model info: {chat_id = }")
        if not chat_id:
            logger.debug("fetch model info: no active chat; skipping")
            return
        active = await fetch_active_models(ctx.server_url, ctx.user_id, chat_id)
        if not active:
            logger.warning(
                "fetch model info: server returned no roles for chat=%s", chat_id
            )
            return
        ensure_chat(ctx.user_id, chat_id)["model_info"] = active
        logger.debug(
            f"fetch model info: cached {len(active)} role(s)\n{list(active) = }"
        )
        ctx.refresh_status_pane()
        ctx.refresh_send_state()

    async def _fetch_session_model_info() -> None:
        """Fetch the per-session default model config (no-chat scope)."""
        logger.debug("fetch session model info: requesting defaults")
        active = await fetch_session_models(ctx.server_url, ctx.user_id)
        if not active:
            logger.warning(
                "fetch session model info: server returned no roles (user=%s)",
                ctx.user_id,
            )
            return
        ctx.session_model_info = active
        logger.debug(
            f"fetch session model info: cached {len(active)} role(s)\n{list(active) = }"
        )
        ctx.refresh_status_pane()
        ctx.refresh_send_state()

    async def _refresh_scope(session_scope: bool) -> None:
        """Re-fetch the model info for the scope the dialog edited."""
        logger.debug(f"refresh scope: {session_scope = }\n{ctx.chat_id = }")
        if session_scope:
            await _fetch_session_model_info()
        elif ctx.chat_id:
            await _fetch_model_info()

    def _required_credential_scopes(info: dict) -> dict[tuple[str, str], dict]:
        """Distinct provider(+endpoint) scopes that the current roles need."""
        scopes: dict[tuple[str, str], dict] = {}
        for role_cfg in info.values():
            cred = role_cfg.get("credential") or {}
            if not cred.get("requires_key"):
                continue
            provider = cred.get("provider") or ""
            if not provider:
                continue
            scopes[(provider, cred.get("endpoint") or "")] = cred
        logger.debug(f"required credential scopes: {list(scopes) = }")
        return scopes

    async def _credentials_dialog(info: dict) -> None:
        """Open the masked, write-only provider credentials editor."""
        logger.debug("credentials dialog: opening")
        stored = await fetch_credentials(ctx.server_url, ctx.user_id)
        logger.debug(
            f"credentials dialog: {len(stored)} stored\n"
            f"{[(c.get('provider'), c.get('endpoint')) for c in stored] = }"
        )
        stored_by_scope = {
            (c.get("provider", ""), c.get("endpoint", "")): c for c in stored
        }
        scopes = _required_credential_scopes(info)
        # Also expose stored keys whose provider is not currently in use.
        for scope in stored_by_scope:
            scopes.setdefault(
                scope,
                {"provider": scope[0], "endpoint": scope[1], "requires_key": True},
            )
        if not scopes:
            logger.debug("credentials dialog: nothing to edit")
            ui.notification(
                "No API keys are required by the current models.", type="info"
            )
            return

        # Enter an explicit slot: this may run in a background task (the
        # first-run prompt), which has no ambient slot (see PageContext).
        with ctx.dialog_container:
            dialog = ui.dialog()

        async def _save_key(provider: str, endpoint: str, key_input, cred_dialog):
            secret = key_input.value.strip()
            if not secret:
                logger.debug(
                    f"credentials: empty key ignored for {provider = }\n{endpoint = }"
                )
                return
            logger.debug(f"credentials: saving {provider = }\n{endpoint = }")
            ok = await set_credential(
                ctx.server_url, ctx.user_id, provider, secret, endpoint
            )
            if not ok:
                logger.warning(
                    "credentials: failed to store key for provider=%s endpoint=%s",
                    provider,
                    endpoint,
                )
                ui.notification("Failed to store the API key.", type="negative")
                return
            logger.debug(f"credentials: stored {provider = }\n{endpoint = }")
            key_input.value = ""
            await _refresh_scope(not ctx.chat_id)
            cred_dialog.close()

        async def _clear_key(provider: str, endpoint: str, cred_dialog):
            logger.debug(f"credentials: clearing {provider = }\n{endpoint = }")
            ok = await clear_credential(ctx.server_url, ctx.user_id, provider, endpoint)
            if not ok:
                logger.warning(
                    "credentials: failed to clear key for provider=%s endpoint=%s",
                    provider,
                    endpoint,
                )
                ui.notification("Failed to clear the API key.", type="negative")
                return
            logger.debug(f"credentials: cleared {provider = }\n{endpoint = }")
            await _refresh_scope(not ctx.chat_id)
            cred_dialog.close()

        # Small factories so the buttons get no-arg coroutine handlers:
        # NiceGUI awaits async on_click handlers in the client context, so
        # the dialog can be built here (a background task has no slot).
        def _on_save_key(provider: str, endpoint: str, key_input):
            async def _handler() -> None:
                await _save_key(provider, endpoint, key_input, dialog)

            return _handler

        def _on_clear_key(provider: str, endpoint: str):
            async def _handler() -> None:
                await _clear_key(provider, endpoint, dialog)

            return _handler

        with dialog, ui.card().classes("w-full p-4"):
            ui.label("API keys").classes("text-lg font-bold")
            ui.label(
                "Stored per provider on the server, truncated in API "
                "responses, and removed after 7 days unused. Leave blank to "
                "use the environment."
            ).classes("text-xs text-grey-5")
            for provider, endpoint in scopes:
                label = provider + (f" ({endpoint})" if endpoint else "")
                with ui.column().classes("w-full gap-1 mt-2"):
                    ui.label(label).classes("text-sm font-bold")
                    stored_cred = stored_by_scope.get((provider, endpoint))
                    if stored_cred:
                        ui.label(f"Stored: {stored_cred.get('masked', '')}").classes(
                            "text-xs text-grey-5"
                        )
                    else:
                        ui.label("Not set").classes("text-xs text-grey-5")
                    key_input = ui.input(
                        "New API key", password=True, password_toggle_button=True
                    ).classes("w-full")
                    with ui.row().classes("w-full justify-end gap-2"):
                        if stored_cred:
                            ui.button(
                                "Clear",
                                on_click=_on_clear_key(provider, endpoint),
                            ).props("flat")
                        ui.button(
                            "Save",
                            on_click=_on_save_key(provider, endpoint, key_input),
                        ).props("unelevated color=primary")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    async def _model_config_dialog() -> None:
        """Open the model dialog for the active scope (session or chat)."""
        chat_id = ctx.chat_id
        session_scope = not bool(chat_id)
        logger.debug(f"model config dialog: {session_scope = }\n{chat_id = }")
        if session_scope:
            info = ctx.session_model_info
            if not info:
                await _fetch_session_model_info()
            info = ctx.session_model_info
        else:
            info = ensure_chat(ctx.user_id, chat_id).get("model_info", {})
            if not info:
                await _fetch_model_info()
                info = ensure_chat(ctx.user_id, chat_id).get("model_info", {})
        roles = list(info.keys())
        logger.debug(
            "model config dialog: session_scope=%s roles=%s", session_scope, roles
        )
        if not roles:
            logger.warning(
                "model config dialog: no roles to configure (session_scope=%s)",
                session_scope,
            )
            ui.notification("No model roles available to configure.", type="warning")
            return

        # Enter an explicit slot: this may run in a background task (the
        # first-run prompt), which has no ambient slot (see PageContext).
        with ctx.dialog_container:
            dialog = ui.dialog()

        async def _save_role(role: str, model_input, original_model: str):
            new_model = model_input.value.strip()
            if new_model == original_model:
                # Nothing changed: avoid pinning an inherited value.
                logger.debug(f"model config dialog: {role = } unchanged; not saving")
                dialog.close()
                return
            logger.debug(
                f"model config dialog: saving {role = }\n{original_model = }\n"
                f"{new_model = }\n{session_scope = }"
            )
            if session_scope:
                ok = await set_session_model_override(
                    ctx.server_url, ctx.user_id, role, {"model": new_model}
                )
            else:
                ok = await set_model_override(
                    ctx.server_url,
                    ctx.user_id,
                    chat_id,
                    role,
                    {"model": new_model, "promote_default": True},
                )
            if not ok:
                logger.warning(
                    "model config dialog: failed to save role=%s model=%s",
                    role,
                    new_model,
                )
                ui.notification("Failed to save the model.", type="negative")
                return
            logger.debug(f"model config dialog: saved {role = }")
            dialog.close()
            await _refresh_scope(session_scope)

        async def _clear_role(role: str):
            logger.debug(
                f"model config dialog: resetting {role = }\n{session_scope = }"
            )
            if session_scope:
                ok = await clear_session_model_override(
                    ctx.server_url, ctx.user_id, role
                )
            else:
                ok = await clear_model_override(
                    ctx.server_url, ctx.user_id, chat_id, role
                )
            if not ok:
                logger.warning("model config dialog: failed to reset role=%s", role)
                ui.notification("Failed to reset the model.", type="negative")
                return
            logger.debug(f"model config dialog: reset {role = }")
            dialog.close()
            await _refresh_scope(session_scope)

        def _on_save_role(role: str, model_input, original_model: str):
            async def _handler() -> None:
                await _save_role(role, model_input, original_model)

            return _handler

        def _on_clear_role(role: str):
            async def _handler() -> None:
                await _clear_role(role)

            return _handler

        async def _open_credentials() -> None:
            await _credentials_dialog(info)

        with dialog, ui.card().classes("w-full p-4"):
            ui.label(
                "Default models" if session_scope else "Models for this chat"
            ).classes("text-lg font-bold")
            ui.label(
                "These defaults apply to new chats until overridden in a chat."
                if session_scope
                else "Saving here also updates your default models, so new "
                "chats start from them."
            ).classes("text-xs text-grey-5")
            with ui.tabs().classes("w-full") as tabs:
                tab_map = {}
                for role in roles:
                    cfg = info.get(role, {})
                    modifiable = cfg.get("modifiable", True)
                    tab_map[role] = ui.tab(
                        name=role.capitalize(),
                        label=role.capitalize(),
                        icon="lock" if not modifiable else None,
                    )
            with ui.tab_panels(tabs, value=roles[0].capitalize()).classes("w-full"):
                for role in roles:
                    cfg = info.get(role, {})
                    modifiable = cfg.get("modifiable", True)
                    original_model = cfg.get("model", "")
                    with ui.tab_panel(tab_map[role]):
                        model_input = ui.input("Model", value=original_model).classes(
                            "w-full"
                        )
                        if not modifiable:
                            model_input.disable()
                        cred = cfg.get("credential") or {}
                        provider = cred.get("provider", "")
                        if not cred.get("requires_key", True):
                            ui.label("No API key required").classes(
                                "text-xs text-grey-6"
                            )
                        elif cred.get("source") == "env":
                            ui.label(
                                f"Using API key from the environment ({provider})"
                            ).classes("text-xs text-grey-6")
                        elif cred.get("source") == "user":
                            ui.label(
                                f"Stored API key {cred.get('masked', '')} ({provider})"
                            ).classes("text-xs text-grey-6")
                        else:
                            ui.label(f"No API key configured for {provider}").classes(
                                "text-xs text-negative"
                            )
                        if not modifiable:
                            ui.label("Locked by administrator").classes(
                                "text-xs text-grey-5 italic"
                            )
                        else:
                            with ui.row().classes("w-full justify-end gap-2"):
                                ui.button(
                                    "Reset",
                                    on_click=_on_clear_role(role),
                                ).props("flat")
                                ui.button(
                                    "Save",
                                    on_click=_on_save_role(
                                        role, model_input, original_model
                                    ),
                                ).props("unelevated color=primary")
            with ui.row().classes("w-full justify-between items-center"):
                ui.button(
                    "Manage API keys",
                    on_click=_open_credentials,
                ).props("flat")
                ui.button("Close", on_click=dialog.close).props("flat")

        dialog.open()

    ctx.fetch_model_info = _fetch_model_info
    ctx.fetch_session_model_info = _fetch_session_model_info
    ctx.model_config_dialog = _model_config_dialog
