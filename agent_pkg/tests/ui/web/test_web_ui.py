#!/usr/bin/env python3
"""
Tests for the agent NiceGUI page using NiceGUI's in-process user simulation.

The page is driven without a browser: ``nicegui.testing.user_simulation``
runs :func:`klea_agent.ui.web.page.setup_layout` in-process, while a patched
``httpx.AsyncClient`` routes the frontend's backend calls to a canned fake
backend, so no server or model is needed.  The fixtures live in
``tests/ui/web/conftest.py``.

File: tests/ui/web/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


async def test_agent_page_renders(agent_user):
    """The agent page renders, and the background load clears the banner."""
    await agent_user.open("/")
    await agent_user.should_see("Klea Test")
    # The health probe + hydrate run in a background task; the readiness
    # banner is cleared only after they complete, so this also proves the
    # patched transport served the bootstrap calls.
    await agent_user.should_not_see("Backend is starting")
