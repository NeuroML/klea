#!/usr/bin/env python3
"""
Tests for the shared TUI components' pure helpers.

These need no app or server: the MIME renderer, the inspector/status section
builders, and the session-command routing are all pure functions.

File: tests/ui/tui/test_components.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

from typing import cast

from klea_utils.ui.tui.components.commands import (
    CommandSuggestions,
    build_catalogue,
    handle_command,
)
from klea_utils.ui.tui.components.hitl import PermissionModal
from klea_utils.ui.tui.components.inspector import (
    _entry_widget,
    _section_title,
    group_sections,
)
from klea_utils.ui.tui.components.status_panel import (
    _section_widget,
    _usage_line,
)
from klea_utils.ui.tui.components.transcript import (
    MessageBlock,
    _render_body,
    render_message,
)
from rich.console import Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text


def _mode_metadata() -> dict:
    """A server-side ``/mode`` catalogue entry."""
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


class TestRenderMessage:
    """MIME-aware transcript rendering (mirrors the web chat_bubble rules)."""

    def test_user_is_a_bold_prompt(self):
        message = {"role": "user", "text": "hi", "stamp": "x", "header": ""}
        rendered = render_message(message)
        assert isinstance(rendered, Text)
        assert rendered.plain.startswith("> ")

    def test_agent_renders_markdown(self):
        message = {"role": "agent", "text": "# T\n\nbody", "stamp": "x", "header": ""}
        assert isinstance(render_message(message), Markdown)

    def test_diff_and_code_render_as_syntax(self):
        assert isinstance(_render_body("text/x-diff", "--- a\n+++ b", "d"), Syntax)
        assert isinstance(_render_body("text/x-patch", "p", "p"), Syntax)
        assert isinstance(_render_body("text/x-python", "print(1)", ""), Syntax)

    def test_media_falls_back_to_text(self):
        assert isinstance(_render_body("image/png", "AAAA", "fallback"), Markdown)
        assert isinstance(_render_body("audio/mpeg", "AAAA", "fallback"), Markdown)

    def test_plain_and_markdown_render_as_markdown(self):
        assert isinstance(_render_body("", "plain", "plain"), Markdown)
        assert isinstance(_render_body("text/markdown", "**x**", ""), Markdown)

    def test_tool_block_has_a_header(self):
        message = {
            "role": "tool",
            "text": "d",
            "stamp": "x",
            "header": "edit foo.py",
            "mime": "text/x-diff",
            "data": "--- a\n+++ b",
        }
        rendered = render_message(message)
        assert type(rendered).__name__ == "Group"
        group = cast(Group, rendered)
        assert isinstance(group.renderables[0], Text)
        assert isinstance(group.renderables[-1], Syntax)

    def test_error_header_uses_the_given_style(self):
        """An errored tool header uses the caller-supplied (theme) style."""
        message = {
            "role": "tool",
            "text": "d",
            "stamp": "x",
            "header": "edit foo.py",
            "is_error": True,
        }
        rendered = render_message(message, error_style="bold #ff0000")
        group = cast(Group, rendered)
        header = group.renderables[0]
        assert isinstance(header, Text)
        assert "#ff0000" in str(header.style)


class TestMessageBlock:
    """The transcript message wrapper's role styling."""

    def test_role_class_is_set(self):
        assert MessageBlock({"role": "user", "text": "hi"}).has_class("message-user")
        assert MessageBlock({"role": "agent", "text": "hi"}).has_class("message-agent")
        assert MessageBlock({"role": "tool", "text": "hi"}).has_class("message-tool")


class TestGroupSections:
    """Inspector per-query grouping."""

    def test_groups_by_marker(self):
        entries = [
            {"type": "query", "text": "q1", "stamp": "10:00"},
            {"type": "inspect", "heading": "a", "details": {}},
            {"type": "inspect", "heading": "b", "details": {}},
            {"type": "query", "text": "q2", "stamp": "10:05"},
            {"type": "inspect", "heading": "c", "details": {}},
        ]
        sections = group_sections(entries)
        assert len(sections) == 2
        assert sections[0][0]["text"] == "q1" and len(sections[0][1]) == 2
        assert sections[1][0]["text"] == "q2" and len(sections[1][1]) == 1

    def test_markerless_entries_get_a_synthetic_section(self):
        sections = group_sections([{"type": "inspect", "heading": "x"}])
        assert len(sections) == 1
        assert sections[0][1][0]["heading"] == "x"

    def test_section_title_has_stamp_and_query(self):
        title = _section_title({"text": "hello", "stamp": "10:00"})
        assert "10:00" in title and "hello" in title

    def test_entry_widget_title_has_heading_and_timing(self):
        widget = _entry_widget(
            {"heading": "Planner", "timing_seconds": 1.5, "details": {"x": 1}}
        )
        assert "Planner" in str(widget.title) and "1.5" in str(widget.title)


class TestStatusPanelHelpers:
    """Status-panel section builders."""

    def test_usage_line(self):
        assert _usage_line({"input_tokens": 10, "output_tokens": 5}) == (
            "tokens: 10 in / 5 out"
        )

    def test_section_widget_uses_heading(self):
        widget = _section_widget("Planner", {"heading": "Plan", "display": "a\nb"})
        assert widget.title == "Plan"


class TestHandleCommand:
    """Session-command routing (ADR-0047)."""

    def test_non_command_and_escape_forward(self):
        catalogue = build_catalogue([_mode_metadata()])
        assert handle_command("hello", catalogue) is None
        assert handle_command("//foo", catalogue) is None

    def test_server_command_forwards(self):
        catalogue = build_catalogue([_mode_metadata()])
        assert handle_command("/mode general", catalogue) is None

    def test_client_help_renders_locally(self):
        catalogue = build_catalogue([_mode_metadata()])
        result = handle_command("/help", catalogue)
        assert result is not None
        assert any("Available commands" in line for line in result.output)

    def test_unknown_command_is_an_error(self):
        catalogue = build_catalogue([_mode_metadata()])
        result = handle_command("/bogus", catalogue)
        assert result is not None and "Unknown command" in result.error

    def test_catalogue_not_loaded(self):
        result = handle_command("/help", None)
        assert result is not None and "not loaded" in result.error

    def test_exit_calls_the_service_hook(self):
        calls: list[str] = []
        result = handle_command(
            "/exit",
            build_catalogue([]),
            services={"exit_app": lambda: calls.append("exit")},
        )
        assert calls == ["exit"]
        assert result is not None and result.error == ""

    def test_exit_without_the_hook_fails(self):
        result = handle_command("/exit", build_catalogue([]))
        assert result is not None and "not supported" in result.error


class TestCommandSuggestions:
    """The ``/``-autocomplete matcher."""

    def test_prefix_matches_available_commands(self):
        catalogue = build_catalogue([_mode_metadata()])
        assert [c.name for c in CommandSuggestions.matches("/m", catalogue)] == [
            "mode",
            "model",
        ]

    def test_space_ends_completion(self):
        catalogue = build_catalogue([_mode_metadata()])
        assert CommandSuggestions.matches("/mode ", catalogue) == []

    def test_non_command_value_has_no_matches(self):
        catalogue = build_catalogue([_mode_metadata()])
        assert CommandSuggestions.matches("hi", catalogue) == []

    def test_no_catalogue_has_no_matches(self):
        assert CommandSuggestions.matches("/", None) == []


class TestPermissionModalHelpers:
    """The permission modal's per-request options and context hint."""

    def test_guess_options_include_not_a_path(self):
        assert PermissionModal._options({"confidence": "guess"}) == (
            "once",
            "session",
            "not_a_path",
            "deny",
        )

    def test_non_guess_options_exclude_not_a_path(self):
        assert PermissionModal._options({"confidence": "declared"}) == (
            "once",
            "session",
            "deny",
        )

    def test_hint_shows_tool_argument_and_value(self):
        hint = PermissionModal._hint(
            {
                "kind": "outside",
                "tool": "read_file",
                "argument": "path",
                "raw": "/etc/hosts",
                "confidence": "guess",
            }
        )
        assert "read_file" in hint
        assert "path" in hint
        assert "/etc/hosts" in hint
