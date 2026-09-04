#!/usr/bin/env python3
"""
NiceGUI server bootstrap for Klea pages.

Shared, app-agnostic boilerplate: process logging, per-app NiceGUI
storage path, the ``/`` page handler (which resolves the per-browser
identity and delegates the actual composition to a *page builder*
supplied by the caller), and :func:`nicegui.ui.run`.

The page layout itself is *not* defined here -- the caller passes the
``page_builder`` callable so each app composes the page from
``klea_utils.ui.web.nicegui.components`` (ADR-0031).

File: klea_utils/ui/web/nicegui/components/bootstrap.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nicegui import app, ui

from klea_utils.ui.web.nicegui.components.storage import resolve_user_id

logger = logging.getLogger(__name__)

PageBuilder = Callable[..., Any]


def _configure_logging(app_name: str) -> None:
    """Configure process-wide logging for this client process."""
    import platformdirs

    from klea_utils.plogging import resolve_log_level, setup_root_logger

    setup_root_logger(
        app_name,
        stderr_level=resolve_log_level(),
        log_dir=platformdirs.PlatformDirs(app_name).user_data_dir,
    )


def _configure_storage(app_name: str) -> None:
    """Point NiceGUI's user storage at the per-app data directory.

    Defaults to ``PlatformDirs(app_name).user_data_dir/nicegui`` when the
    deployer has not set ``NICEGUI_STORAGE_PATH``, and rebuilds
    ``app.storage`` so ``FilePersistentDict`` picks up the new path.
    """
    if "NICEGUI_STORAGE_PATH" not in os.environ:
        import platformdirs

        default_storage_dir = (
            Path(platformdirs.PlatformDirs(app_name).user_data_dir) / "nicegui"
        )
        os.environ["NICEGUI_STORAGE_PATH"] = str(default_storage_dir.resolve())
        logger.debug(
            "set default NICEGUI_STORAGE_PATH=%s", os.environ["NICEGUI_STORAGE_PATH"]
        )
    storage_dir = Path(os.environ["NICEGUI_STORAGE_PATH"]).resolve()
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("failed to create NICEGUI_STORAGE_PATH %s: %s", storage_dir, e)
    try:
        from nicegui.storage import Storage

        Storage.path = storage_dir
        logger.debug(
            "nicegui Storage.path set to %s (app_name=%s)", Storage.path, app_name
        )
        # Rebuild storage so FilePersistentDict picks up the new path when
        # app.storage was already instantiated at import time.
        if not app.is_started:
            app.storage = Storage()
            logger.debug("rebuilt nicegui app.storage for path %s", Storage.path)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "failed to configure NiceGUI storage path %s: %s", storage_dir, e
        )


def run_nicegui_server(
    title: str,
    server_url: str,
    *,
    page_builder: PageBuilder,
    subtitle: str = "",
    disclaimer: str = "",
    footer_text: str = 'Powered by <a href="https://github.com/neuroml/klea">Klea</a>',
    reload: bool = False,
    nicegui_url: str = "0.0.0.0:7860",
    storage_secret: str = "klea-nicegui-secret-change-me",
    app_name: str = "klea-web",
) -> None:
    """Start the NiceGUI web server with a Klea page.

    This is the process-level entry point for a Klea frontend.  It
    registers a ``@ui.page("/")`` handler that resolves the per-browser
    ``user_id`` and delegates page composition to *page_builder*
    (signature ``(chat_id, user_id, server_url, title, subtitle,
    disclaimer, footer_text)``), then starts the NiceGUI server.

    Backend readiness is handled by the page itself: the layout is
    delivered immediately and the health probe + chat hydration run as a
    background task, so ``main_page`` returns within ``response_timeout``
    even on a cold start.

    :param title: Application title (displayed in the header and
        browser tab).
    :param server_url: Base URL of the backend API server
        (e.g. ``http://127.0.0.1:8005``).
    :param page_builder: Callable that composes the page layout.
    :param subtitle: Optional smaller text shown next to *title*.
    :param disclaimer: Optional text shown below the chat input.
    :param footer_text: HTML content for the footer bar.
    :param reload: When ``True``, enable NiceGUI's file-watch hot reload.
    :param nicegui_url: ``host:port`` to bind the NiceGUI web server to.
    :param storage_secret: Secret used by NiceGUI for browser session
        persistence.
    :param app_name: Log identity for this frontend process, used as the
        log file name so each app keeps its own logs.
    """
    _configure_logging(app_name)
    _configure_storage(app_name)

    host, port_str = nicegui_url.rsplit(":", 1)
    port = int(port_str)

    @ui.page("/", response_timeout=60)
    async def main_page():
        """Build the main page without blocking on backend readiness.

        User identity is resolved before any ``await`` so
        ``app.storage.user`` is still in the request context.
        """
        user_id = await resolve_user_id()
        await ui.context.client.connected()

        chat_id = ""

        logger.debug("user_id=%s chat_id=%s", user_id, chat_id)

        page_builder(
            chat_id=chat_id,
            user_id=user_id,
            server_url=server_url,
            title=title,
            subtitle=subtitle,
            disclaimer=disclaimer,
            footer_text=footer_text,
        )

    ui.run(
        port=port,
        host=host,
        title=title,
        show=False,
        reload=reload,
        storage_secret=storage_secret,
    )
