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

from collections.abc import Mapping
from typing import Any, cast

from klea_utils.ui.status_layout import (
    MAX_LINES,
    MIN_LINES,
    estimate_section_lines,
    section_budget_lines,
)
from klea_utils.ui.tui.components.commands import (
    CommandSuggestions,
    build_catalogue,
    handle_command,
)
from klea_utils.ui.tui.components.hitl import PermissionPanel
from klea_utils.ui.tui.components.inspector import (
    _entry_widget,
    _section_title,
    group_sections,
)
from klea_utils.ui.tui.components.scroll import KleaScroll
from klea_utils.ui.tui.components.sessions import _session_label
from klea_utils.ui.tui.components.status_panel import (
    _section_scroll,
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
        assert rendered.plain == "hi"

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

    def test_small_plan_body_takes_the_floor_budget(self):
        """A one-item plan body is capped at the floor, not left unbounded."""
        section = {
            "display": "Goal: short plan",
            "content": [{"type": "items", "items": [{"text": "Step 1/1: read"}]}],
        }
        assert estimate_section_lines(section) == 1
        assert section_budget_lines(section) == MIN_LINES
        scroll = _section_scroll(section, collapsed=False)
        assert isinstance(scroll, KleaScroll)
        assert scroll.styles.max_height is not None

    def test_long_body_is_capped_at_the_maximum_budget(self):
        """A long fan-out body never exceeds the ceiling budget."""
        section = {
            "content": [
                {
                    "type": "items",
                    "items": [{"text": f"Step {index}/60: run"} for index in range(60)],
                }
            ]
        }
        assert estimate_section_lines(section) > MAX_LINES
        assert section_budget_lines(section) == MAX_LINES
        scroll = _section_scroll(section, collapsed=False)
        assert scroll.styles.max_height is not None

    def test_section_body_is_wrapped_in_a_klea_scroll(self):
        """A section body is wrapped in a budgeted KleaScroll."""
        section = {"display": "Goal: wrapped"}
        scroll = _section_scroll(section, collapsed=False)
        assert isinstance(scroll, KleaScroll)
        assert "status-section-body" in scroll.classes
        assert scroll.styles.max_height is not None

    async def test_mounted_section_body_children_live_in_its_klea_scroll(self):
        """Once mounted, the body widgets sit inside the KleaScroll."""
        from textual.app import App

        section = {"display": "Goal: wrapped"}

        class _Harness(App):
            def compose(self):
                yield _section_widget("plan", section)

        app = _Harness()
        async with app.run_test() as pilot:
            await pilot.pause()
            scroll = app.query_one(KleaScroll)
            assert list(scroll.children)

    def test_collapsed_section_body_cannot_take_focus(self):
        """A collapsed body never takes focus away from the pane's scroll."""
        section = {
            "content": [
                {
                    "type": "items",
                    "items": [{"text": f"Step {index}/20: run"} for index in range(20)],
                }
            ]
        }
        assert _section_scroll(section, collapsed=True).can_focus is False
        assert _section_scroll(section, collapsed=False).can_focus is True

    def test_usage_line(self):
        assert _usage_line({"input_tokens": 10, "output_tokens": 5}) == ("10 in/5 out")
        # Large totals are abbreviated in the compact heading.
        assert _usage_line({"input_tokens": 123456, "output_tokens": 7890}) == (
            "123.5k in/7.9k out"
        )
        # Cached reads are a subset of input and shown only when present.
        assert (
            _usage_line({"input_tokens": 10, "output_tokens": 5, "cached_tokens": 4})
            == "10 in/5 out/4 cached"
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

    def test_new_calls_the_service_hook(self):
        calls: list[str] = []
        result = handle_command(
            "/new",
            build_catalogue([]),
            services={"new_chat": lambda: calls.append("new")},
        )
        assert calls == ["new"]
        assert result is not None and result.error == ""

    def test_new_without_the_hook_fails(self):
        result = handle_command("/new", build_catalogue([]))
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


class TestPermissionPanelHelpers:
    """The permission panel's per-request options and context hint."""

    def test_step_lines_render_bounded_lines(self):
        """Plan-step context renders one bounded ``Step N:`` line per step."""
        lines = PermissionPanel._step_lines(
            {
                "steps": [
                    {"step_number": 1, "summary": "  Read   the config "},
                    {"step_number": 2, "summary": "x" * 400},
                ]
            }
        )

        assert lines[0] == "Step 1: Read the config"
        assert lines[1].startswith("Step 2: ")
        assert lines[1].endswith("...")
        assert len(lines[1]) == len("Step 2: ") + 160 + len("...")

    def test_step_lines_absent_is_empty(self):
        """An ask without step context renders no step lines."""
        assert PermissionPanel._step_lines({}) == []
        assert PermissionPanel._step_lines({"steps": []}) == []

    def test_step_static_is_a_bold_heading_set_apart(self):
        """The step heading is bold and carries a blank line above it."""
        heading = PermissionPanel._step_static("Step 1: Read the config")
        renderable = heading.content
        assert isinstance(renderable, Text)

        # The blank line rides on the heading's own renderable, so it cannot
        # collapse into, or double up with, the panel's separators.
        assert renderable.plain == "\nStep 1: Read the config"

        # The heading text itself is bold; the span starts after that newline.
        bold = [
            span
            for span in renderable.spans
            if isinstance(span.style, str) and span.style == "bold"
        ]
        assert len(bold) == 1
        assert bold[0].start == 1
        assert bold[0].end == len("\nStep 1: Read the config")

        # The optional coverage line rides below the heading on the same
        # renderable, dim so it reads as a note, and is never bold.
        covered = PermissionPanel._step_static(
            "Step 1: Read the config", "2 paths: 2 outside the project"
        )
        covered_text = covered.content
        assert isinstance(covered_text, Text)
        assert covered_text.plain == (
            "\nStep 1: Read the config\n2 paths: 2 outside the project"
        )
        dim = [
            span
            for span in covered_text.spans
            if isinstance(span.style, str) and span.style == "dim"
        ]
        assert len(dim) == 1
        assert (
            covered_text.plain[dim[0].start : dim[0].end]
            == "2 paths: 2 outside the project"
        )

    def test_step_static_caps_a_long_summary_with_an_ellipsis(self):
        """An over-long step summary wraps to two lines and ends in ``...``."""
        line = "Step 1: " + " ".join(["word"] * 60)
        heading = PermissionPanel._step_static(line)
        renderable = heading.content
        assert isinstance(renderable, Text)

        body = renderable.plain.split("\n", 1)[1]
        lines = body.splitlines()
        assert lines[0].startswith("Step 1: word")
        assert len(lines) == PermissionPanel.STEP_HEADING_MAX_LINES
        assert lines[-1].endswith("...")
        for rendered in lines:
            assert len(rendered) <= PermissionPanel.STEP_HEADING_WRAP_CHARS

    def test_group_coverage_counts_the_requests_by_kind(self):
        """The coverage line counts each request tier under the heading."""
        group = cast(
            list[tuple[int, Mapping[str, Any], str]],
            [
                (1, {"kind": "outside", "path": "/a"}, ""),
                (2, {"kind": "outside", "path": "/b"}, ""),
                (3, {"kind": "sensitive", "path": "/c"}, ""),
            ],
        )
        assert PermissionPanel._group_coverage(group) == (
            "3 paths: 2 outside the project, 1 sensitive"
        )
        assert (
            PermissionPanel._group_coverage(
                [(1, {"kind": "sensitive", "path": "/c"}, "")]
            )
            == "1 path: 1 sensitive"
        )
        assert (
            PermissionPanel._group_coverage([(1, {"path": "/a"}, "")])
            == "1 path: 1 unknown"
        )
        assert PermissionPanel._group_coverage([]) == ""

    def test_group_also_unions_the_further_steps_once(self):
        """The multi-step hint is unioned once per group, never per row."""
        import inspect

        group = cast(
            list[tuple[int, Mapping[str, Any], str]],
            [
                (
                    1,
                    {"path": "/a", "steps": [{"step_number": 1, "summary": "A"}]},
                    "",
                ),
                (
                    2,
                    {
                        "path": "/b",
                        "steps": [
                            {"step_number": 1},
                            {"step_number": 3, "summary": "C"},
                        ],
                    },
                    "",
                ),
            ],
        )
        assert PermissionPanel._group_also(group) == "Also serves step 3"
        assert PermissionPanel._group_also([(1, {"path": "/a"}, "")]) == ""

        # The hint is emitted only beneath the heading: ``compose`` no longer
        # carries the per-row hint, so a path row can never repeat it.
        source = inspect.getsource(PermissionPanel.compose)
        assert "if also:" not in source

    def test_groups_place_requests_under_their_step(self):
        """Requests group under their step heading, in ascending step order."""
        groups = PermissionPanel._groups(
            [
                {"path": "/b", "steps": [{"step_number": 2, "summary": "Second"}]},
                {"path": "/a", "steps": [{"step_number": 1, "summary": "First"}]},
                {"path": "/c"},
            ]
        )

        assert [heading for heading, _ in groups] == [
            "Step 1: First",
            "Step 2: Second",
            PermissionPanel.OTHER_HEADING,
        ]
        assert [item[1]["path"] for item in groups[0][1]] == ["/a"]
        assert [item[1]["path"] for item in groups[1][1]] == ["/b"]
        # The step-less request lands in the single trailing bucket.
        assert [item[1]["path"] for item in groups[2][1]] == ["/c"]
        # The global 1-based display number is preserved, not renumbered.
        assert [item[0] for item in groups[0][1]] == [2]
        assert [item[0] for item in groups[2][1]] == [3]

    def test_groups_put_multi_step_request_under_lowest_step(self):
        """A request serving several steps sits under its lowest step number."""
        groups = PermissionPanel._groups(
            [
                {
                    "path": "/a",
                    "steps": [
                        {"step_number": 2, "summary": "Second"},
                        {"step_number": 1, "summary": "First"},
                    ],
                }
            ]
        )

        assert len(groups) == 1
        heading, items = groups[0]
        assert heading == "Step 1: First"
        assert items[0][0] == 1
        assert items[0][1]["path"] == "/a"
        assert items[0][2] == "Also serves step 2"

    def test_groups_flat_without_step_context(self):
        """With no per-request step context the grouping is flat."""
        requests = [{"path": "/a"}, {"path": "/b"}]
        groups = PermissionPanel._groups(requests)

        assert groups == [("", [(1, requests[0], ""), (2, requests[1], "")])]

    def test_guess_options_include_not_a_path(self):
        assert PermissionPanel._options({"confidence": "guess"}) == (
            "once",
            "session",
            "not_a_path",
            "deny",
        )

    def test_non_guess_options_exclude_not_a_path(self):
        assert PermissionPanel._options({"confidence": "declared"}) == (
            "once",
            "session",
            "deny",
        )

    def test_hint_shows_tool_argument_and_value(self):
        hint = PermissionPanel._hint(
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

    def test_hint_shows_detected_token_for_command_guesses(self):
        hint = PermissionPanel._hint(
            {
                "kind": "outside",
                "tool": "run_command",
                "argument": "command",
                "raw": "/dev/null",
                "argument_preview": "tee x > /dev/null",
                "confidence": "guess",
                "source": "value:command",
            }
        )
        assert "Detected token: /dev/null" in hint

    def test_coverage_lists_multiple_paths(self):
        coverage = PermissionPanel._coverage(
            {"covered_paths_display": ["/tmp/a", "/tmp/b"]}
        )
        assert "Covers:" in coverage
        assert "/tmp/a" in coverage
        assert "/tmp/b" in coverage

    def test_coverage_is_empty_for_one_path(self):
        assert PermissionPanel._coverage({"covered_paths_display": ["/tmp/a"]}) == ""

    def test_hint_renderable_highlights_the_detected_token(self):
        """The styled hint reverse-styles exactly the detected token span."""
        request = {
            "kind": "outside",
            "tool": "run_command",
            "argument": "command",
            "raw": "/dev/null",
            "argument_preview": "tee x > /dev/null",
            "preview_token_start": 8,
            "preview_token_end": 17,
            "preview_omitted_before": 0,
            "preview_omitted_after": 0,
            "argument_length": 18,
            "token_line": 1,
            "confidence": "guess",
            "source": "value:command",
        }
        rendered = PermissionPanel._hint_renderable(request)

        assert isinstance(rendered, Text)
        assert "Detected path" in rendered.plain
        # The "Detected path: " heading itself is bold-styled.
        heading_start = rendered.plain.index("Detected path: ")
        heading_end = heading_start + len("Detected path: ")
        bold_spans = [span for span in rendered.spans if str(span.style) == "bold"]
        assert any(
            span.start <= heading_start and span.end >= heading_end
            for span in bold_spans
        )
        reverse_spans = [
            span for span in rendered.spans if str(span.style) == "reverse"
        ]
        assert len(reverse_spans) == 1
        span = reverse_spans[0]
        assert span.end - span.start == 17 - 8
        assert rendered.plain[span.start : span.end] == "/dev/null"

    def test_hint_renderable_falls_back_without_offsets(self):
        """A request without token offsets degrades to the plain hint string."""
        request = {
            "kind": "outside",
            "tool": "read_file",
            "argument": "path",
            "raw": "/etc/hosts",
            "confidence": "guess",
            "source": "value:path",
            "preview_token_start": -1,
            "preview_token_end": -1,
        }
        rendered = PermissionPanel._hint_renderable(request)

        assert isinstance(rendered, str)
        assert rendered == PermissionPanel._hint(request)


class TestSessionLabel:
    """Session-picker rows show only the title and chat id."""

    def test_label_has_title_and_chat_id(self):
        label = _session_label({"chat_id": "c1", "title": "Chat"}, active=False)
        assert label.plain.splitlines()[0].strip() == "Chat  c1"

    def test_label_does_not_include_the_preview(self):
        label = _session_label(
            {"chat_id": "c1", "title": "Chat", "preview": "what is X?"},
            active=False,
        )
        assert "\n" not in label.plain
        assert "what is X?" not in label.plain


def test_inspector_entry_drops_blacklisted_details(monkeypatch) -> None:
    """The TUI inspector entry body drops not-displayed details keys.

    ``NodeStreamData.details`` rides inside the entry's ``details`` mapping as
    an empty mapping; the renderer must filter it out so no stray ``{}`` prints.
    """
    from klea_utils.ui.tui.components import inspector as inspector_mod

    captured: dict[str, object] = {}

    def fake_format(details):
        captured["details"] = details
        return "formatted"

    monkeypatch.setattr(inspector_mod, "format_details", fake_format)
    inspector_mod._entry_widget(
        {
            "type": "inspect",
            "heading": "a",
            "details": {"details": {}, "route": "task"},
        }
    )
    assert captured["details"] == {"route": "task"}
