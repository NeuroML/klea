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
