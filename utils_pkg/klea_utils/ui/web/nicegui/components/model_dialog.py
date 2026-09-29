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
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from klea_utils.api.sse import (
    fetch_active_models,
    fetch_credentials,
    fetch_session_models,
)
from klea_utils.llm import credential_scope, provider_requires_api_key
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
        # The welcome/empty-state card in the chat area reports missing
        # models and API keys, so it must be rebuilt once the scope
        # changes; the status pane and send button are refreshed by the
        # fetch helpers above.
        ctx.render_chat_area()

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

    async def _credentials_dialog(
        info: dict, on_credentials_changed: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        """Open the masked, write-only provider credentials editor.

        :param info: The current model config, used to find the provider
            scopes that need a key.
        :param on_credentials_changed: Optional async callback invoked after
            a key is saved or cleared, so the caller can refresh its view
            (the model dialog rebuilds its credential labels from here).
        """
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
            if on_credentials_changed is not None:
                await on_credentials_changed()
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
            if on_credentials_changed is not None:
                await on_credentials_changed()
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

        stored = await fetch_credentials(ctx.server_url, ctx.user_id)
        stored_by_scope: dict[tuple[str, str], dict] = {
            (c.get("provider", ""), c.get("endpoint", "")): c for c in stored
        }
        logger.debug(
            f"model config dialog: {len(stored_by_scope)} stored credential scope(s)"
        )

        def _current_info() -> dict:
            """Return the latest model config for the dialog's scope.

            Read fresh on every body refresh so credentials saved from the
            nested credentials dialog are reflected instead of the snapshot
            taken when the dialog opened.
            """
            if session_scope:
                return ctx.session_model_info
            return ensure_chat(ctx.user_id, chat_id).get("model_info", {})

        def _live_credential(model_name: str, saved: dict) -> dict:
            """Credential block for a (possibly unsaved) model string.

            Provider and key requirement are derived client-side so typed
            but unsaved models are recognised; the source/masked come from
            the stored credentials or, when the scope matches, the saved
            block.
            """
            scope = credential_scope(model_name)
            provider = scope.provider or ""
            endpoint = scope.endpoint or ""
            block: dict[str, Any] = {
                "provider": provider,
                "endpoint": endpoint,
                "requires_key": provider_requires_api_key(provider),
            }
            saved = saved or {}
            same_scope = (
                saved.get("provider", "") == provider
                and (saved.get("endpoint") or "") == endpoint
            )
            stored_cred = stored_by_scope.get((provider, endpoint))
            if stored_cred:
                block["source"] = "user"
                block["masked"] = stored_cred.get("masked", "")
            elif same_scope and saved.get("source") in ("env", "user"):
                block["source"] = saved.get("source")
                block["masked"] = saved.get("masked", "")
            else:
                block["source"] = "none"
                block["masked"] = ""
            return block

        def _live_info() -> dict:
            """Model config built from the dialog's current input values."""
            live: dict[str, Any] = {}
            for role, cfg in _current_info().items():
                cfg = dict(cfg)
                model = role_values.get(role, cfg.get("model", ""))
                cfg["model"] = model
                cfg["credential"] = _live_credential(model, cfg.get("credential") or {})
                live[role] = cfg
            return live

        def _credential_display(model_name: str, saved: dict) -> tuple[str, str]:
            """Return ``(label, colour class)`` for a model's credential status."""
            cred = _live_credential(model_name, saved)
            provider = cred["provider"]
            if not model_name:
                return "No model selected", "text-grey-6"
            if not provider:
                return "No API key configured", "text-negative"
            if not cred["requires_key"]:
                return "No API key required", "text-grey-6"
            if cred["source"] == "env":
                return (
                    f"Using API key from the environment ({provider})",
                    "text-grey-6",
                )
            if cred["source"] == "user":
                return (
                    f"Stored API key {cred.get('masked', '')} ({provider})",
                    "text-grey-6",
                )
            return f"No API key configured for {provider}", "text-negative"

        def _on_model_change(role: str, value: str) -> None:
            """Track a typed model and refresh just its credential label."""
            role_values[role] = (value or "").strip()
            saved = (_current_info().get(role, {}) or {}).get("credential") or {}
            text, colour = _credential_display(role_values[role], saved)
            label = role_labels.get(role)
            if label is not None:
                label.text = text
                label.classes(remove="text-grey-6 text-negative")
                label.classes(add=colour)
            logger.debug(f"model dialog: live label {role = }\n{text = }")

        # Keep the selected tab across body refreshes: a credential change
        # rebuilds the tabs, and without this the user is bounced back to
        # the first role.
        selected_tab = {"value": roles[0].capitalize()}
        # Current input values and per-role credential labels, so unsaved
        # model edits survive a body refresh and their labels update live.
        role_values: dict[str, str] = {
            role: info.get(role, {}).get("model", "") for role in roles
        }
        role_labels: dict[str, Any] = {}
        role_inputs: dict[str, Any] = {}

        # Enter an explicit slot: this may run in a background task (the
        # first-run prompt), which has no ambient slot (see PageContext).
        with ctx.dialog_container:
            dialog = ui.dialog()

        async def _save_all() -> None:
            """Persist every role changed in the dialog, not just the tab shown.

            Each tab edits one role's model; Save must apply them all in one
            go (otherwise the dialog has to be reopened per role).
            """
            info = _current_info()
            saved: list[tuple[str, str]] = []
            for role, model_input in role_inputs.items():
                cfg = info.get(role, {})
                if not cfg.get("modifiable", True):
                    continue
                new_model = model_input.value.strip()
                original_model = cfg.get("model", "")
                if new_model == original_model:
                    # Nothing changed: avoid pinning an inherited value.
                    continue
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
                    ui.notification(
                        f"Failed to save the model for {role}.", type="negative"
                    )
                    return
                saved.append((role, new_model))
            logger.debug(f"model config dialog: saved roles: {saved}")
            dialog.close()
            if saved:
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
            # Keep the dialog open so several roles can be reset, then
            # saved together; the input reverts to the inherited model.
            await _refresh_scope(session_scope)
            role_values.pop(role, None)
            _render_body.refresh()

        def _on_clear_role(role: str):
            async def _handler() -> None:
                await _clear_role(role)

            return _handler

        async def _refresh_after_credentials() -> None:
            """Re-read stored credentials and rebuild the model dialog."""
            stored = await fetch_credentials(ctx.server_url, ctx.user_id)
            stored_by_scope.clear()
            stored_by_scope.update(
                {(c.get("provider", ""), c.get("endpoint", "")): c for c in stored}
            )
            logger.debug(
                f"model dialog: refreshed {len(stored_by_scope)} stored scope(s)"
            )
            _render_body.refresh()

        async def _open_credentials() -> None:
            # Pass the live (typed, possibly unsaved) config so a provider
            # that only appears in an unsaved model still gets a key scope.
            await _credentials_dialog(_live_info(), _refresh_after_credentials)

        @ui.refreshable
        def _render_body() -> None:
            """Render the tabs for the current model config.

            Refreshable so a credential change (saved in the nested
            credentials dialog) updates the per-role key status without
            closing and reopening this dialog.
            """
            info = _current_info()
            roles = list(info.keys())
            if not roles:
                logger.warning("model config dialog: refresh found no roles")
                return
            role_tabs = [role.capitalize() for role in roles]
            value = (
                selected_tab["value"]
                if selected_tab["value"] in role_tabs
                else role_tabs[0]
            )
            with ui.card().classes("w-full p-4"):
                ui.label(
                    "Default models" if session_scope else "Models for this chat"
                ).classes("text-lg font-bold")
                ui.label(
                    "These defaults apply to new chats until overridden in a chat."
                    if session_scope
                    else "Saving here also updates your default models, so new "
                    "chats start from them."
                ).classes("text-xs text-grey-5")
                tabs = ui.tabs().classes("w-full")
                tabs.on_value_change(lambda e: selected_tab.update(value=e.value))
                with tabs:
                    tab_map = {}
                    for role in roles:
                        cfg = info.get(role, {})
                        modifiable = cfg.get("modifiable", True)
                        tab_map[role] = ui.tab(
                            name=role.capitalize(),
                            label=role.capitalize(),
                            icon="lock" if not modifiable else None,
                        )
                tabs.value = value
                with ui.tab_panels(tabs, value=value).classes("w-full"):
                    for role in roles:
                        cfg = info.get(role, {})
                        modifiable = cfg.get("modifiable", True)
                        original_model = cfg.get("model", "")
                        with ui.tab_panel(tab_map[role]):
                            model_value = role_values.get(role, original_model)
                            model_input = ui.input("Model", value=model_value).classes(
                                "w-full"
                            )
                            role_inputs[role] = model_input
                            if not modifiable:
                                model_input.disable()
                            else:
                                model_input.on_value_change(
                                    lambda e, r=role: _on_model_change(r, e.value)
                                )
                            cred_text, cred_colour = _credential_display(
                                model_value, cfg.get("credential") or {}
                            )
                            role_labels[role] = ui.label(cred_text).classes(
                                f"text-xs {cred_colour}"
                            )
                            if not modifiable:
                                ui.label("Locked by administrator").classes(
                                    "text-xs text-grey-5 italic"
                                )
                            else:
                                with ui.row().classes("w-full justify-end"):
                                    ui.button(
                                        "Reset",
                                        on_click=_on_clear_role(role),
                                    ).props("flat")
                with ui.row().classes("w-full justify-between items-center"):
                    ui.button(
                        "Manage API keys",
                        on_click=_open_credentials,
                    ).props("flat")
                    with ui.row().classes("gap-2"):
                        ui.button("Close", on_click=dialog.close).props("flat")
                        ui.button("Save", on_click=_save_all).props(
                            "unelevated color=primary"
                        )

        with dialog:
            _render_body()

        dialog.open()

    ctx.fetch_model_info = _fetch_model_info
    ctx.fetch_session_model_info = _fetch_session_model_info
    ctx.model_config_dialog = _model_config_dialog
