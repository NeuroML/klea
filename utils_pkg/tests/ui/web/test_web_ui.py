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

from klea_utils.ui.web.nicegui.components import header
from klea_utils.ui.web.nicegui.components.chat_list import _chat_preview
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction


def _buttons_with(user, predicate) -> list:
    return [button for button in user.find(ui.button).elements if predicate(button)]


def _power_button(user) -> list:
    return _buttons_with(
        user, lambda b: "power_settings_new" in str(getattr(b, "_props", ""))
    )


def _exit_button(user) -> list:
    return _buttons_with(user, lambda b: getattr(b, "text", "") == "Exit")


async def _wait_for(predicate, retries: int = 50, delay: float = 0.05) -> bool:
    """Poll *predicate* until it is true (or give up after *retries*)."""
    import asyncio

    for _ in range(retries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return False


def _stub_exit_hooks(monkeypatch) -> list[int]:
    """Replace the exit side effects (browser close + server shutdown)."""
    shutdown_calls: list[int] = []
    monkeypatch.setattr(header, "stop_all_streams", lambda ctx: None)
    monkeypatch.setattr(header.ui, "run_javascript", lambda *a, **k: None)
    monkeypatch.setattr(header.app, "shutdown", lambda: shutdown_calls.append(1))
    return shutdown_calls


async def test_page_renders_chrome(utils_user):
    """The header title, chat empty-state and center tabs render."""
    await utils_user.open("/")
    await utils_user.should_see("Klea Test")
    await utils_user.should_see("Start a conversation")
    await utils_user.should_see("inspect")


async def test_header_has_an_exit_button(utils_user):
    """The header offers an exit button (the ``/exit`` affordance)."""
    await utils_user.open("/")
    assert _power_button(utils_user)


async def test_exit_button_confirms_before_exiting(utils_user, monkeypatch):
    """The header exit button asks first; only Exit stops the server."""
    shutdown_calls = _stub_exit_hooks(monkeypatch)

    await utils_user.open("/")
    power = _power_button(utils_user)
    assert power
    UserInteraction(utils_user, {power[0]}, None).click()

    await utils_user.should_see("Exit Klea?", retries=50)
    assert shutdown_calls == []

    exit_buttons = _exit_button(utils_user)
    assert exit_buttons
    UserInteraction(utils_user, {exit_buttons[0]}, None).click()

    assert await _wait_for(lambda: shutdown_calls == [1])


async def test_exit_dialog_cancel_keeps_the_server_running(utils_user, monkeypatch):
    """Cancelling the exit dialog does not shut the server down."""
    shutdown_calls = _stub_exit_hooks(monkeypatch)

    await utils_user.open("/")
    power = _power_button(utils_user)
    assert power
    UserInteraction(utils_user, {power[0]}, None).click()
    await utils_user.should_see("Exit Klea?", retries=50)

    cancel_buttons = _buttons_with(
        utils_user, lambda b: getattr(b, "text", "") == "Cancel"
    )
    assert cancel_buttons
    UserInteraction(utils_user, {cancel_buttons[0]}, None).click()

    assert shutdown_calls == []


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


class TestChatPreview:
    """The chat-list tooltip preview prefers the server's first query."""

    def test_prefers_the_hydrated_server_preview(self):
        chat = {
            "preview": "server query",
            "messages": [{"role": "user", "text": "local query"}],
        }
        assert _chat_preview(chat) == "server query"

    def test_falls_back_to_the_first_local_user_message(self):
        chat = {
            "messages": [
                {"role": "assistant", "text": "welcome"},
                {"role": "user", "text": "first local query"},
                {"role": "user", "text": "second"},
            ]
        }
        assert _chat_preview(chat) == "first local query"

    def test_collapses_and_truncates(self):
        chat = {"preview": "line one\nline two " + "x" * 400}
        preview = _chat_preview(chat)
        assert preview.startswith("line one line two ")
        assert preview.endswith("...")
        assert len(preview) == 200
