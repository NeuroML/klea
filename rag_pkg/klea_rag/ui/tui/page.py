#!/usr/bin/env python3
"""
RAG Textual TUI composition.

Thin entry (mirroring ``run_rag_web``): builds the shared
:class:`~klea_utils.ui.tui.context.TuiContext` and launches the shared
Textual app.  RAG registers no app-specific status slots.

File: klea_rag/ui/tui/page.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

logger = logging.getLogger(__name__)


def run_rag_tui(
    *,
    url: str,
    title: str,
    app_prefix: str = "klea-rag",
    app_name: str = "klea-rag-tui",
    user_id: str = "",
) -> None:
    """Launch the RAG Textual TUI.

    :param url: Base URL of the backend API server.
    :param title: Application title shown in the chat panel header.
    :param app_prefix: Prompt label prefix.
    :param app_name: Log identity for this frontend.
    :param user_id: The ``--user-id`` value (resolved by the shared entry).
    """
    # Lazy: Textual (the [tui] extra) and the shared TUI are only needed on
    # launch, so importing the CLI (e.g. for --help) stays light.
    from klea_utils.ui.tui.app import run_tui

    run_tui(
        url=url,
        title=title,
        app_prefix=app_prefix,
        app_name=app_name,
        user_id=user_id,
    )
