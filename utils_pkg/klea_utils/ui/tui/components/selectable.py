#!/usr/bin/env python3
"""
Selectable text widget for the Klea TUI.

File: klea_utils/ui/tui/components/selectable.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual.selection import Selection
from textual.strip import Strip
from textual.visual import RichVisual, Visual
from textual.widgets import Static

logger = logging.getLogger(__name__)


class SelectableStatic(Static):
    """A Static whose rendered text supports character-level mouse selection.

    Decision record: why this subclass instead of a native Textual widget
    ---------------------------------------------------------------------
    The transcript and the other text panes render MIME-aware content as Rich
    renderables (``rich.markdown.Markdown``, ``rich.syntax.Syntax``,
    ``rich.console.Group``, ``rich.text.Text``) hosted by a Textual ``Static``.
    That keeps one rendering path for prose, diffs, syntax-highlighted code,
    tool headers and the text fallback, mirrors the web chat bubble, and keeps
    ``render_message`` a pure function that is easy to test.

    Textual's native ``Markdown`` widget is not a drop-in replacement: it only
    covers Markdown, so diffs/code would have to be rewritten as fenced blocks
    and tool headers split into separate widgets; its centered headings also
    do not extract correctly.  ``Pretty`` and ``RichLog`` do not provide
    selection either, and ``Log`` is plain text only.  Rather than fork the
    rendering pipeline, this subclass supplies the two pieces the generic Rich
    path lacks:

    * ``render_line`` applies cell offsets, so Textual's compositor can map a
      mouse drag to a character range.
    * ``get_selection`` renders the same visual to strips and extracts the
      selected text, so Ctrl+C has something to copy.

    Revisit this if Textual adds selection support for ``RichVisual`` or if a
    native-Markdown migration becomes worth the rendering/test churn.
    """

    def render_line(self, y: int) -> Strip:
        """Render line *y*, apply selection styling, and attach its offset.

        ``RichVisual`` ignores Textual's selection options, so the selection
        background is applied here.  Plain ``Content`` visuals already get the
        selection style from Textual; re-apply the readable theme style so the
        transparent default foreground does not hide the text.

        Blank lines have no segments and therefore no cell offset; pad them to
        the widget width so a drag can start or end on an empty line.
        """
        strip = super().render_line(y)
        if not strip.text:
            strip = Strip.blank(self.size.width, self.visual_style.rich_style)

        selection = self.text_selection
        if selection is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = strip.cell_length
                if start < end:
                    style = self.screen.get_component_rich_style("screen--selection")
                    # Keep the Rich text/syntax colours; only tint the
                    # selected cells.  A transparent selection foreground
                    # would otherwise make text unreadable.
                    if (
                        isinstance(self.visual, RichVisual)
                        and style.bgcolor is not None
                    ):
                        style = RichStyle(bgcolor=style.bgcolor)
                    selected = strip.crop(start, end)
                    selected = Strip(
                        [
                            Segment(
                                segment.text,
                                (segment.style or RichStyle()) + style,
                            )
                            for segment in selected
                        ],
                        selected.cell_length,
                    )
                    strip = Strip.join(
                        [
                            strip.crop(0, start),
                            selected,
                            strip.crop(end, strip.cell_length),
                        ]
                    )
        return strip.apply_offsets(0, y)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return the text covered by *selection*.

        Textual's default implementation only extracts ``Text``/``Content``
        visuals.  Rich renderables are wrapped in a ``RichVisual``, so render
        the visual into the same strips used on screen and extract their text.

        :param selection: Screen-relative selection range for this widget.
        :returns: ``(text, "\\n")``, or ``None`` when the widget cannot be
            rendered.
        """
        if self.size.width <= 0:
            return None
        try:
            strips = Visual.to_strips(
                self,
                self.visual,
                self.size.width,
                None,
                self.visual_style,
                apply_selection=False,
            )
        except Exception:
            logger.exception("Could not extract selected text")
            return None
        text = "\n".join(strip.text for strip in strips)
        extracted = selection.extract(text)
        # Rich layout may pad lines to the widget width; do not copy that
        # layout whitespace (intentional trailing spaces in code are rare).
        extracted = "\n".join(line.rstrip() for line in extracted.splitlines())
        return extracted, "\n"
