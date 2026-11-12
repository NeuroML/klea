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


async def test_status_pane_renders_content_blocks_with_fallback(
    fake_backend, utils_user
):
    """The status pane renders items and degrades unknown kinds/roles."""
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Renderer",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "ok item", "role": "ok"},
                    {"text": "error item", "role": "error"},
                    {"text": "current item", "role": "current"},
                    {"text": "pending item", "role": "pending"},
                    {"text": "unknown role item", "role": "mystery"},
                ],
            },
            {"kind": "mystery", "text": "unknown kind item"},
            {"kind": "text", "text": "plain item"},
        ],
    }
    chat["state_sections"]["render"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("ok item")
    await utils_user.should_see("error item")
    await utils_user.should_see("current item")
    await utils_user.should_see("pending item")
    await utils_user.should_see("unknown role item")
    await utils_user.should_see("unknown kind item")
    await utils_user.should_see("plain item")


async def test_status_pane_styles_the_current_role_and_degrades_unknown_roles(
    fake_backend, utils_user
):
    """'current' items are bold; an unknown role still renders unstyled."""
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Roles",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "bold current item", "role": "current"},
                    {"text": "mystery role item", "role": "mystery"},
                ],
            }
        ],
    }
    chat["state_sections"]["roles"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("bold current item")
    await utils_user.should_see("mystery role item")

    from klea_utils.ui.glyphs import UNICODE_GLYPHS

    # The pane prefixes every item with its role marker, so the rendered label
    # text carries the marker ahead of the item text.
    current_text = f"{UNICODE_GLYPHS.marker_current} bold current item"
    unknown_text = f"{UNICODE_GLYPHS.marker_plain} mystery role item"

    labels = {
        getattr(label, "text", ""): label
        for label in utils_user.find(ui.label).elements
    }

    def _css_classes(label) -> str:
        """Return a rendered label's CSS classes (version tolerant)."""
        classes = getattr(label, "_classes", None)
        if classes is None:
            attr = getattr(label, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    assert current_text in labels
    assert unknown_text in labels
    current_classes = _css_classes(labels[current_text])
    unknown_classes = _css_classes(labels[unknown_text])
    assert "font-bold" in current_classes
    assert "font-bold" not in unknown_classes


async def test_status_pane_renders_the_role_markers(fake_backend, utils_user):
    """Each role renders its Unicode marker; unknown roles use the plain one."""
    from typing import cast

    from klea_utils.ui.glyphs import UNICODE_GLYPHS
    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Markers",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "ok item", "role": "ok"},
                    {"text": "error item", "role": "error"},
                    {"text": "current item", "role": "current"},
                    {"text": "pending item", "role": "pending"},
                    {"text": "unknown role item", "role": "mystery"},
                ],
            }
        ],
    }
    chat["state_sections"]["markers"] = cast(StateSection, section)
    # A two-item section pins the inter-item spacing: every item but the last
    # carries the spacing margin, and the last item carries none.
    chat["state_sections"]["spacing"] = cast(
        StateSection,
        {
            "heading": "Spacing",
            "display": "",
            "summary": "",
            "details": {},
            "content": [
                {
                    "type": "items",
                    "items": [
                        {"text": "first spaced item", "role": "ok"},
                        {"text": "second spaced item", "role": "ok"},
                    ],
                }
            ],
        },
    )

    await utils_user.open("/")
    await utils_user.should_see("ok item", retries=50)

    rendered = {
        getattr(label, "text", "") for label in utils_user.find(ui.label).elements
    }
    expected = {
        f"{UNICODE_GLYPHS.marker_ok} ok item",
        f"{UNICODE_GLYPHS.marker_error} error item",
        f"{UNICODE_GLYPHS.marker_current} current item",
        f"{UNICODE_GLYPHS.marker_pending} pending item",
        f"{UNICODE_GLYPHS.marker_plain} unknown role item",
    }
    assert expected <= rendered, f"missing markers: {sorted(expected - rendered)}"

    def _label_classes(text: str) -> str:
        """Return the CSS classes of the rendered label whose text is *text*."""
        matches = [
            element
            for element in utils_user.find(ui.label).elements
            if getattr(element, "text", "") == text
        ]
        assert matches, f"missing label: {text}"
        classes = getattr(matches[0], "_classes", None)
        if classes is None:
            attr = getattr(matches[0], "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    # Only items followed by another item carry the spacing margin.
    assert "mb-2" in _label_classes(f"{UNICODE_GLYPHS.marker_ok} first spaced item")
    assert "mb-2" not in _label_classes(
        f"{UNICODE_GLYPHS.marker_ok} second spaced item"
    )
