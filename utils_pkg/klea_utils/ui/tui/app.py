#!/usr/bin/env python3
"""
Shared Textual terminal client entry (placeholder).

The real Textual ``App``/``Screen`` (two-panel layout, streaming, HITL)
lands in the next implementation step; this stub only wires the ``tui``
subcommand and the ``[tui]`` extra guard so the CLI entry point can be
verified (ADR-0050).

File: klea_utils/ui/tui/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

logger = logging.getLogger(__name__)


def run_tui(
    url: str,
    title: str,
    app_prefix: str = "klea",
    app_name: str = "klea-agent-tui",
) -> None:
    """Launch the shared Klea Textual TUI (stub).

    :param url: Base URL of the API server (e.g. ``http://127.0.0.1:8005``)
    :param title: Application title displayed on start
    :param app_prefix: Prefix for prompt labels (e.g. ``"klea-rag"``)
    :param app_name: Log identity for the terminal client
        (e.g. ``"klea-rag-tui"``)
    """
    logger.info("TUI stub invoked (app=%s, url=%s, title=%s)", app_name, url, title)
    print(
        f"Klea TUI ({app_name}) is not implemented yet; "
        f"the Textual skeleton for '{app_prefix}' lands in the next step."
    )
