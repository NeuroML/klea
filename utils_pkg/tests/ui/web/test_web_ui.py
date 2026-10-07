#!/usr/bin/env python3
"""
Tests for the shared NiceGUI components.

The components are rendered on a minimal in-process page (see
``conftest.py``) and driven with NiceGUI's user simulation, so the
assertions cover what a user actually sees (header, footer, chat
empty-state, session list), not just the underlying state helpers.

File: tests/ui/web/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


async def test_page_renders_chrome(utils_user):
    """The header title, chat empty-state and center tabs render."""
    await utils_user.open("/")
    await utils_user.should_see("Klea Test")
    await utils_user.should_see("Start a conversation")
    await utils_user.should_see("inspect")


async def test_empty_state_prompts_when_a_required_model_is_missing(
    fake_backend, utils_user
):
    """A missing required model renders the first-run setup card."""
    fake_backend.session_models = {
        "chat": {"model": "", "required": True, "credential": {}}
    }
    await utils_user.open("/")
    await utils_user.should_see("Models are not configured")
    await utils_user.should_see("Choose models")


async def test_session_list_shows_hydrated_chats(fake_backend, utils_user):
    """A chat returned by the backend appears in the session list."""
    fake_backend.chats = [{"chat_id": "c1", "title": "First Chat", "created_at": 0}]
    await utils_user.open("/")
    await utils_user.should_see("First Chat", retries=50)
