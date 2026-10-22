#!/usr/bin/env python3
"""
Tests for the selectable text widget.

File: tests/ui/tui/test_selectable.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from klea_utils.ui.tui.components.selectable import SelectableStatic
from rich.console import Group, RenderableType
from rich.markdown import Markdown as RichMarkdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.selection import Selection


class _SelectableApp(App[None]):
    """A minimal app hosting one SelectableStatic."""

    def __init__(self, content: RenderableType) -> None:
        """Store the content to render.

        :param content: Rich renderable shown in the selectable widget.
        """
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        """Compose a scrolling container with the selectable widget."""
        with VerticalScroll():
            yield SelectableStatic(self._content, id="body")


async def test_drag_selects_plain_text_and_ctrl_c_copies():
    """A drag maps to a character range and Ctrl+C copies it."""
    app = _SelectableApp("Hello world")
    async with app.run_test(size=(60, 12)) as pilot:
        body = app.query_one("#body", SelectableStatic)
        await pilot.mouse_down(body, offset=(2, 0))
        await pilot.mouse_up(body, offset=(6, 0))
        await pilot.pause()

        assert app.screen.get_selected_text() == "llo w"
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert app.clipboard == "llo w"


async def test_select_all_extracts_rich_markdown():
    """A Rich Markdown body can be extracted and loses layout padding."""
    app = _SelectableApp(RichMarkdown("Hello world\n\nsecond line"))
    async with app.run_test(size=(60, 15)):
        body = app.query_one("#body", SelectableStatic)
        result = body.get_selection(Selection(None, None))
        assert result is not None
        text, ending = result

        assert "Hello world" in text
        assert "second line" in text
        assert ending == "\n"
        assert all(line == line.rstrip() for line in text.splitlines())


async def test_select_all_extracts_syntax_and_group():
    """A tool-style Group with a Syntax body is selectable too."""
    content = Group(
        Text("edit foo.py", style="bold"),
        Syntax("print('hi')", "python"),
    )
    app = _SelectableApp(content)
    async with app.run_test(size=(60, 15)):
        body = app.query_one("#body", SelectableStatic)
        result = body.get_selection(Selection(None, None))
        assert result is not None
        text, _ = result

        assert "edit foo.py" in text
        assert "print('hi')" in text


_MARKDOWN = (
    "# Heading\n\nParagraph one has selectable text.\n\nParagraph two has more text."
)


async def test_rich_markdown_selection_is_highlighted():
    """A Rich Markdown selection gets the theme selection background."""
    app = _SelectableApp(RichMarkdown(_MARKDOWN))
    async with app.run_test(size=(60, 15)) as pilot:
        body = app.query_one("#body", SelectableStatic)
        await pilot.mouse_down(body, offset=(0, 2))
        await pilot.mouse_up(body, offset=(11, 2))
        await pilot.pause()

        selected = app.screen.get_selected_text()
        assert selected is not None
        assert "Paragraph" in selected

        selection_style = app.screen.get_component_rich_style("screen--selection")
        strip = body.render_line(2)
        assert any(
            segment.style is not None
            and segment.style.bgcolor == selection_style.bgcolor
            for segment in strip
        )


async def test_drag_to_a_blank_line_has_a_real_end_offset():
    """Blank Rich lines carry offsets, so a drag ending there is not select-all."""
    app = _SelectableApp(RichMarkdown(_MARKDOWN))
    async with app.run_test(size=(60, 15)) as pilot:
        body = app.query_one("#body", SelectableStatic)
        await pilot.mouse_down(body, offset=(0, 2))
        await pilot.mouse_up(body, offset=(5, 3))  # line 3 is blank
        await pilot.pause()

        selection = app.screen.selections.get(body)
        assert selection is not None
        assert selection.end is not None

        selected = app.screen.get_selected_text()
        assert selected is not None
        assert selected.startswith("Paragraph")
        assert len(selected) < len(_MARKDOWN)
