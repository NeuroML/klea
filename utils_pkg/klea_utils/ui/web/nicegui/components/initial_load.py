#!/usr/bin/env python3
"""
Initial-load component: backend readiness probe and chat hydration.

Runs in a background task after the layout is delivered so the page
frame renders immediately even when the backend needs 30-60s to
become ready (e.g. a cold HuggingFace container).

File: klea_utils/ui/web/nicegui/components/initial_load.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import background_tasks, ui

from klea_utils.api.utils import check_api_is_ready
from klea_utils.llm import missing_required_roles
from klea_utils.ui.web.nicegui.client import hydrate_chats
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import user_storage_or_none
from klea_utils.ui.web.nicegui.state import chats, missing_credentials

logger = logging.getLogger(__name__)


def attach_initial_load(ctx: PageContext) -> None:
    """Schedule the backend probe + hydration in a background task.

    Requires ``ctx.loading_row`` (the backend banner) and ``ctx.text``
    (the chat input) to already be built.

    :param ctx: The shared page context.
    """

    async def _maybe_prompt_models() -> None:
        """Open the model dialog once per browser if setup is incomplete.

        Models may come from the environment, so the prompt only appears
        when required models (or their API keys) are genuinely missing.
        """
        store = user_storage_or_none()
        if store is not None and store.get("models_prompt_shown"):
            logger.debug("model prompt already shown for this session")
            return
        missing_models = missing_required_roles(ctx.session_model_info)
        missing_keys = missing_credentials(ctx.session_model_info)
        if not (missing_models or missing_keys):
            logger.debug("models configured; no first-run prompt needed")
            return
        logger.info(
            "first run: missing models=%s missing API keys=%s; prompting",
            missing_models,
            missing_keys,
        )
        if store is not None:
            store["models_prompt_shown"] = True
        if ctx.model_config_dialog is not None:
            await ctx.model_config_dialog()

    async def _initial_load() -> None:
        """Wait for backend, hydrate chats, then refresh UI."""
        try:
            logger.debug("initial load: probing %s/health/ready", ctx.server_url)
            await check_api_is_ready(f"{ctx.server_url}/health/ready")
        except Exception as e:  # noqa: BLE001
            logger.warning("backend unavailable after probe: %s", e)
            ctx.loading_row.clear()
            with ctx.loading_row.classes("justify-center"):
                ui.icon("cloud_off").classes("text-grey-5")
                ui.label("Backend unavailable").classes("text-sm text-grey-7")
                ui.label("Please check that the server is running.").classes(
                    "text-xs text-grey-5"
                )
                ui.button(
                    "Retry",
                    on_click=lambda: background_tasks.create(_initial_load()),
                ).props("flat dense color=primary")
            ui.notification(
                "Backend unavailable - click Retry when ready",
                type="negative",
                timeout=0,
                close_button=True,
            )
            return

        # Hydrate (swallows its own exceptions but log here as well).
        try:
            logger.debug("backend ready, hydrating chats for user_id=%s", ctx.user_id)
            await hydrate_chats(ctx.server_url, ctx.user_id)
            logger.debug("hydrate done, chats keys=%s", list(chats.keys()))
        except Exception as e:  # noqa: BLE001
            logger.warning("hydrate failed: %s", e)

        # Per-session default models: needed for the first-run CTA and the
        # status pane.  Fetch before rendering so the CTA shows immediately.
        if ctx.fetch_session_model_info is not None:
            try:
                await ctx.fetch_session_model_info()
            except Exception as e:  # noqa: BLE001
                logger.warning("session model fetch failed: %s", e)

        await _maybe_prompt_models()

        # Clear banner and enable input.
        ctx.loading_row.clear()
        ctx.loading_row.classes("hidden")
        try:
            ctx.text.enable()
        except Exception as e:  # noqa: BLE001
            logger.debug("enable input failed (already enabled?): %s", e)
        ctx.refresh_chat_list()
        ctx.render_chat_area()
        ctx.refresh_status_pane()
        ctx.refresh_inspector()
        logger.debug("initial load complete")

    background_tasks.create(_initial_load())
