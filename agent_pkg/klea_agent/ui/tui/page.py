#!/usr/bin/env python3
"""
Agent Textual TUI composition.

Thin entry (mirroring ``run_agent_web``): builds the shared
:class:`~klea_utils.ui.tui.context.TuiContext` and launches the shared
Textual app.  Agent-specific status-panel controls (the operating-mode and
tool-access selectors, ADR-0030/0037) slot in here without touching
``klea_utils`` (ADR-0031/ADR-0050).

File: klea_agent/ui/tui/page.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

logger = logging.getLogger(__name__)


def run_agent_tui(
    *,
    url: str,
    title: str,
    app_prefix: str = "klea-agent",
    app_name: str = "klea-agent-tui",
    user_id: str = "",
    resume: str = "",
    continue_session: bool = False,
    identity_app_name: str = "",
) -> None:
    """Launch the agent Textual TUI.

    :param url: Base URL of the backend API server.
    :param title: Application title shown in the chat panel header.
    :param app_prefix: Prompt label prefix.
    :param app_name: Log identity for this frontend.
    :param user_id: The ``--user-id`` value (resolved by the shared entry).
    :param resume: Chat id to resume on launch (``--resume``).
    :param continue_session: Resume the most recently updated chat
        (``--continue``).
    :param identity_app_name: Canonical identity key shared with the web
        frontend (``"klea-agent"``).
    """
    # Lazy: Textual (the [tui] extra) and the shared TUI are only needed on
    # launch, so importing the CLI (e.g. for --help) stays light.
    from klea_utils.ui.tui.app import run_tui

    from klea_agent.ui.tui.project_root_ui import project_root_status_extra
    from klea_agent.ui.tui.status_ui import mode_access_status_extra

    run_tui(
        url=url,
        title=title,
        app_prefix=app_prefix,
        app_name=app_name,
        user_id=user_id,
        resume=resume,
        continue_session=continue_session,
        identity_app_name=identity_app_name,
        status_extras=[
            mode_access_status_extra,
            project_root_status_extra,
        ],
    )
