#!/usr/bin/env python3
"""
Tests for session-command handling in the web frontend (ADR-0047).

File: tests/ui/web/test_commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.commands.common import (
    CommandContext,
    ParsedCommand,
    parse_command,
)
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.web.nicegui.components.commands import client_commands
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction


def _suggestion_items(user, usage: str) -> list:
    """Autocomplete items whose label text contains *usage*."""
    return [
        item
        for item in user.find(ui.item).elements
        if any(
            usage in (getattr(descendant, "text", "") or "")
            for descendant in item.descendants()
        )
    ]


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


def _mode_metadata() -> dict:
    """Server-side ``/mode`` catalogue entry."""
    return {
        "name": "mode",
        "summary": "Show or set the operating mode",
        "arg_hint": "[general|scientific]",
        "side": "server",
        "klass": "session-state",
        "while_streaming": "block",
        "persists": "checkpoint",
        "capabilities": [],
        "implemented": True,
        "aliases": [],
    }


async def test_help_renders_in_the_chat(fake_backend, agent_user):
    """A client ``/help`` renders the merged catalogue as a system block."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "hi"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("hi", retries=50)

    _send(agent_user, "/help")
    await agent_user.should_see("Available commands:", retries=50)
    await agent_user.should_see("/help")


async def test_help_without_a_chat_creates_one(fake_backend, agent_user):
    """A client command before the first message still creates a chat."""
    before = set(chats)
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/help")
    await agent_user.should_see("Available commands:", retries=50)

    new_chats = [chats[key] for key in set(chats) - before]
    assert new_chats
    system_texts = [
        message["text"]
        for data in new_chats
        for message in data["messages"]
        if message["role"] == "system"
    ]
    assert any("Available commands:" in text for text in system_texts)


async def test_unknown_command_is_rejected_locally(fake_backend, agent_user):
    """An unknown command is rejected in the frontend and never sent."""
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/nope")
    await agent_user.should_see("Unknown command", retries=50)
    assert fake_backend.stream_bodies() == []


async def test_slash_lists_matching_commands(fake_backend, agent_user):
    """Typing ``/`` opens a menu of matching commands from the catalogue."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/")
    await agent_user.should_see("/help", retries=50)
    await agent_user.should_see("/mode", retries=50)


async def test_slash_filters_by_prefix(fake_backend, agent_user):
    """The menu lists only commands matching the typed prefix."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/mo")
    await agent_user.should_see("/mode", retries=50)
    await agent_user.should_not_see("/help")


async def test_autocomplete_ignores_plain_text(fake_backend, agent_user):
    """Plain text (no leading ``/``) does not open the menu (ADR-0047)."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("help")
    assert _suggestion_items(agent_user, "/help") == []


async def test_autocomplete_ignores_a_leading_space(fake_backend, agent_user):
    """A leading space is not a command: the menu stays closed."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type(" /help")
    assert _suggestion_items(agent_user, "/help") == []


async def test_clicking_a_suggestion_completes_and_closes(fake_backend, agent_user):
    """Clicking a suggestion fills the input and hides the list (ADR-0047)."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/hel")
    await agent_user.should_see("/help", retries=50)

    items = _suggestion_items(agent_user, "/help")
    assert len(items) == 1
    UserInteraction(agent_user, {items[0]}, None).click()

    assert agent_user.find(ui.textarea).elements.pop().value == "/help "
    assert _suggestion_items(agent_user, "/help") == []


async def test_server_command_is_forwarded(fake_backend, agent_user):
    """A server command is forwarded to the graph as the query."""
    fake_backend.commands = [_mode_metadata()]
    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/mode general")
    await agent_user.should_see("Run complete!", retries=50)
    assert fake_backend.stream_bodies()[-1]["query"] == "/mode general"


def test_new_handler_calls_the_new_chat_hook_silently():
    """``/new`` invokes the hook and produces no output (ADR-0047)."""
    calls: list[str] = []
    context = CommandContext(services={"new_chat": lambda: calls.append("new")})
    result = client_commands().dispatch(ParsedCommand(name="new"), context)
    assert calls == ["new"]
    assert result.output == []
    assert result.error == ""
    assert result.notice == ""


async def _wait_for(predicate, retries: int = 50, delay: float = 0.05) -> bool:
    """Poll *predicate* until it is true (or give up after *retries*)."""
    import asyncio

    for _ in range(retries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return False


async def test_new_starts_a_fresh_session(fake_backend, agent_user):
    """``/new`` creates and switches to an empty session, without a run."""
    before = set(chats)
    marker = "zzz-answer-marker-zzz"
    fake_backend.stream_events = [{"type": "complete", "message_for_user": marker}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see(marker, retries=50)

    _send(agent_user, "/new")
    assert await _wait_for(lambda: len(set(chats) - before) == 2)

    new_chats = [chats[key] for key in set(chats) - before]
    # ``/new`` opened an empty session and sent nothing to the graph.
    assert sum(not data["messages"] for data in new_chats) == 1
    assert len(fake_backend.stream_bodies()) == 1


def test_rename_handler_requires_a_name():
    """``/rename`` with no name is a usage error (ADR-0047)."""
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="   "), CommandContext(chat_id="c1")
    )
    assert result.error
    assert "Usage: /rename" in result.error


def test_rename_handler_requires_an_active_chat():
    """``/rename`` with no active session is an error."""
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="New Name"), CommandContext(chat_id="")
    )
    assert "No active session" in result.error


def test_rename_handler_calls_the_hook_with_the_raw_name():
    """The full argument (spaces preserved) reaches the rename hook."""
    calls: list[tuple[str, str]] = []
    context = CommandContext(
        chat_id="c1",
        services={"rename_chat": lambda chat_id, name: calls.append((chat_id, name))},
    )
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="My New Name"), context
    )
    assert calls == [("c1", "My New Name")]
    assert any("My New Name" in line for line in result.output)


async def test_rename_command_renames_the_session(fake_backend, agent_user):
    """``/rename`` renames the active session (ADR-0047)."""
    before = set(chats)
    marker = "zzz-rename-marker-zzz"
    fake_backend.stream_events = [{"type": "complete", "message_for_user": marker}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see(marker, retries=50)

    _send(agent_user, "/rename My Renamed Session")
    await agent_user.should_see("Renamed session", retries=50)

    new_chats = [chats[key] for key in set(chats) - before]
    assert any(data["name"] == "My Renamed Session" for data in new_chats)


def test_model_handler_calls_the_dialog_hook_silently():
    """``/model`` opens the model dialog and produces no chat output."""
    calls: list[str] = []
    context = CommandContext(services={"show_model_dialog": lambda: calls.append("m")})
    result = client_commands().dispatch(ParsedCommand(name="model"), context)
    assert calls == ["m"]
    assert result.output == []
    assert result.error == ""


async def test_model_command_opens_the_model_dialog(fake_backend, agent_user):
    """``/model`` opens the model/credential dialog (ADR-0047)."""
    fake_backend.session_models = {
        "chat": {"model": "m1", "required": True, "credential": {}}
    }
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    # The dialog is created only when opened.
    await agent_user.should_not_see("Manage API keys")

    _send(agent_user, "/model")
    await agent_user.should_see("Manage API keys", retries=50)


def test_exit_handler_calls_the_hook_silently():
    """``/exit`` invokes the hook and produces no output (ADR-0047)."""
    calls: list[str] = []
    context = CommandContext(services={"exit_app": lambda: calls.append("exit")})
    result = client_commands().dispatch(ParsedCommand(name="exit"), context)
    assert calls == ["exit"]
    assert result.output == []
    assert result.error == ""
    assert result.notice == ""


def test_exit_without_the_hook_fails_cleanly():
    """``/exit`` with no hook is reported, not raised."""
    result = client_commands().dispatch(ParsedCommand(name="exit"), CommandContext())
    assert "not supported" in result.error


def test_export_handler_requires_an_active_chat():
    """``/export`` with no active session is an error."""
    result = client_commands().dispatch(
        ParsedCommand(name="export"), CommandContext(user_id="u", chat_id="")
    )
    assert "No active session" in result.error


def test_export_handler_rejects_an_empty_session():
    """``/export`` on a session with no messages is an error."""
    result = client_commands().dispatch(
        ParsedCommand(name="export"), CommandContext(user_id="u", chat_id="c-empty")
    )
    assert "nothing to export" in result.error


def test_export_handler_saves_the_transcript():
    """``/export`` hands a Markdown transcript to the save hook."""
    ensure_chat("u", "c1")["messages"] = [
        {"text": "hello", "stamp": "10:00:00", "role": "user", "header": ""},
        {"text": "hi", "stamp": "10:00:01", "role": "agent", "header": ""},
    ]
    calls: list[tuple[str, str]] = []
    context = CommandContext(
        user_id="u",
        chat_id="c1",
        services={
            "save_export": lambda filename, content: calls.append((filename, content))
        },
    )
    result = client_commands().dispatch(ParsedCommand(name="export"), context)
    assert len(calls) == 1
    filename, content = calls[0]
    assert filename.endswith(".md")
    assert "hello" in content and "hi" in content
    assert any("Exported the session transcript" in line for line in result.output)


async def test_export_command_downloads_the_transcript(fake_backend, agent_user):
    """``/export`` runs end to end and reports the export (ADR-0047)."""
    marker = "zzz-export-marker-zzz"
    fake_backend.stream_events = [{"type": "complete", "message_for_user": marker}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see(marker, retries=50)

    _send(agent_user, "/export")
    await agent_user.should_see("Exported the session transcript", retries=50)


def _parse(text: str) -> ParsedCommand:
    """Parse *text*, asserting it is a command (for the type checker)."""
    parsed = parse_command(text)
    assert parsed is not None
    return parsed


def _seed_sessions(user_id: str, *specs: tuple[str, str, bool]) -> None:
    """Create chats for *user_id* as ``(chat_id, name, pinned)``.

    Creation timestamps are set in list order so the sorted session list is
    deterministic (later entries are newer).
    """
    for offset, (chat_id, name, pinned) in enumerate(specs):
        chat = ensure_chat(user_id, chat_id)
        chat["name"] = name
        chat["pinned"] = pinned
        chat["created"] = 1000.0 + offset


class TestSessionsList:
    def test_orders_pinned_first_with_index_and_active_marker(self):
        _seed_sessions("su-list", ("a", "Alpha", False), ("b", "Beta", True))
        result = client_commands().dispatch(
            _parse("/sessions"), CommandContext(user_id="su-list", chat_id="a")
        )
        joined = "\n".join(result.output)
        assert "Sessions:" in joined
        assert "Beta" in joined and "Alpha" in joined
        # Pinned ``Beta`` sorts first even though ``Alpha`` is the active one.
        assert joined.index("Beta") < joined.index("Alpha")
        assert "- `1` - Beta (pinned) - `b`" in joined
        assert "- `2` - Alpha (active) - `a`" in joined

    def test_list_verb_matches_the_bare_form(self):
        _seed_sessions("su-listverb", ("a", "Alpha", False))
        result = client_commands().dispatch(
            _parse("/sessions list"), CommandContext(user_id="su-listverb", chat_id="a")
        )
        assert "Alpha" in "\n".join(result.output)

    def test_no_sessions_reports_empty(self):
        result = client_commands().dispatch(
            _parse("/sessions"), CommandContext(user_id="su-none")
        )
        assert result.output == ["No sessions yet."]


class TestSessionsSwitch:
    def test_switch_by_index_calls_the_hook(self):
        _seed_sessions("ss-index", ("a", "Alpha", False), ("b", "Beta", True))
        calls: list[str] = []
        context = CommandContext(
            user_id="ss-index",
            chat_id="a",
            services={"switch_chat": lambda chat_id: calls.append(chat_id)},
        )
        result = client_commands().dispatch(_parse("/sessions switch 1"), context)
        # Pinned ``Beta`` is index 1.
        assert calls == ["b"]
        assert "Switched to session" in "\n".join(result.output)

    def test_switch_by_exact_id_calls_the_hook(self):
        _seed_sessions("ss-id", ("alpha", "Alpha", False))
        calls: list[str] = []
        context = CommandContext(
            user_id="ss-id",
            services={"switch_chat": lambda chat_id: calls.append(chat_id)},
        )
        client_commands().dispatch(_parse("/sessions switch alpha"), context)
        assert calls == ["alpha"]

    def test_switch_by_unique_prefix_calls_the_hook(self):
        _seed_sessions(
            "ss-prefix", ("witty-eagle", "WE", False), ("calm-otter", "CO", False)
        )
        calls: list[str] = []
        context = CommandContext(
            user_id="ss-prefix",
            services={"switch_chat": lambda chat_id: calls.append(chat_id)},
        )
        client_commands().dispatch(_parse("/sessions switch witty"), context)
        assert calls == ["witty-eagle"]

    def test_switch_rejects_an_out_of_range_index(self):
        _seed_sessions("ss-range", ("a", "Alpha", False))
        result = client_commands().dispatch(
            _parse("/sessions switch 9"), CommandContext(user_id="ss-range")
        )
        assert "No session number 9" in result.error

    def test_switch_rejects_an_unknown_id(self):
        _seed_sessions("ss-unknown", ("a", "Alpha", False))
        result = client_commands().dispatch(
            _parse("/sessions switch nope"), CommandContext(user_id="ss-unknown")
        )
        assert "No session with id" in result.error

    def test_switch_rejects_an_ambiguous_prefix(self):
        _seed_sessions(
            "ss-amb", ("witty-eagle", "WE", False), ("witty-otter", "WO", False)
        )
        result = client_commands().dispatch(
            _parse("/sessions switch witty"), CommandContext(user_id="ss-amb")
        )
        assert "Ambiguous" in result.error

    def test_switch_without_a_target_is_a_usage_error(self):
        result = client_commands().dispatch(
            _parse("/sessions switch"), CommandContext(user_id="ss-usage")
        )
        assert "Usage: /sessions switch" in result.error

    def test_switch_without_the_hook_fails_cleanly(self):
        _seed_sessions("ss-nohook", ("a", "Alpha", False))
        result = client_commands().dispatch(
            _parse("/sessions switch a"), CommandContext(user_id="ss-nohook")
        )
        assert "not supported" in result.error


class TestSessionsDelete:
    def test_delete_without_a_target_is_a_usage_error(self):
        result = client_commands().dispatch(
            _parse("/sessions delete"), CommandContext(user_id="sd-usage")
        )
        assert "Usage: /sessions delete" in result.error

    def test_delete_calls_the_hook_with_the_resolved_id(self):
        _seed_sessions("sd-index", ("a", "Alpha", False), ("b", "Beta", True))
        calls: list[str] = []
        context = CommandContext(
            user_id="sd-index",
            services={"delete_chat": lambda chat_id: calls.append(chat_id)},
        )
        result = client_commands().dispatch(_parse("/sessions delete 2"), context)
        # Pinned ``Beta`` is index 1, so index 2 is ``Alpha``.
        assert calls == ["a"]
        # The confirmation dialog is the feedback; the command is silent.
        assert result.output == []
        assert result.error == ""

    def test_delete_by_exact_id_calls_the_hook(self):
        _seed_sessions("sd-id", ("alpha", "Alpha", False))
        calls: list[str] = []
        context = CommandContext(
            user_id="sd-id",
            services={"delete_chat": lambda chat_id: calls.append(chat_id)},
        )
        client_commands().dispatch(_parse("/sessions delete alpha"), context)
        assert calls == ["alpha"]

    def test_delete_rejects_an_unknown_id(self):
        _seed_sessions("sd-unknown", ("a", "Alpha", False))
        calls: list[str] = []
        context = CommandContext(
            user_id="sd-unknown",
            services={"delete_chat": lambda chat_id: calls.append(chat_id)},
        )
        result = client_commands().dispatch(_parse("/sessions delete nope"), context)
        assert "No session with id" in result.error
        assert calls == []

    def test_delete_without_the_hook_fails_cleanly(self):
        _seed_sessions("sd-nohook", ("a", "Alpha", False))
        result = client_commands().dispatch(
            _parse("/sessions delete a"), CommandContext(user_id="sd-nohook")
        )
        assert "not supported" in result.error


async def test_sessions_command_lists_and_switches(fake_backend, agent_user):
    """``/sessions`` lists sessions; ``switch`` changes the active one."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "hi"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("hi", retries=50)

    before = set(chats)
    _send(agent_user, "/new")
    assert await _wait_for(lambda: len(set(chats) - before) == 1)

    _send(agent_user, "/sessions")
    await agent_user.should_see("Sessions:", retries=50)

    # The new session is the most recent, so the first chat is index 2.
    _send(agent_user, "/sessions switch 2")
    await agent_user.should_see("Switched to session", retries=50)


def _delete_requests(backend) -> list:
    """Recorded ``DELETE /chat/...`` requests."""
    return [
        request
        for request in backend.requests
        if request.method == "DELETE" and "/chat/" in request.url.path
    ]


async def test_sessions_delete_confirms_then_deletes(fake_backend, agent_user):
    """``/sessions delete`` confirms before deleting the session."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "hi"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("hi", retries=50)

    _send(agent_user, "/sessions delete 1")
    await agent_user.should_see("Delete session?", retries=50)
    # Nothing is deleted until the confirmation is accepted.
    assert _delete_requests(fake_backend) == []

    delete_buttons = [
        button
        for button in agent_user.find(ui.button).elements
        if getattr(button, "text", "") == "Delete"
    ]
    assert delete_buttons
    UserInteraction(agent_user, {delete_buttons[0]}, None).click()

    assert await _wait_for(lambda: bool(_delete_requests(fake_backend)))
