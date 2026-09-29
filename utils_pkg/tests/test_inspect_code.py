#!/usr/bin/env python3
"""
Tests for the inspector code highlighter.

File: tests/test_inspect_code.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import re
from html import unescape

import pytest

pytest.importorskip("nicegui")

from klea_utils.ui.inspect_format import format_details
from klea_utils.ui.web.nicegui.components.inspector_code import (
    CODE_HL_CLASS,
    highlight_details,
)


def _text() -> str:
    """A realistic details payload with a code fence and multi-line strings."""
    return format_details(
        {
            "input_prompt": [
                {"role": "ai", "content": "Here:\n\n```\nfoo\n```\n\ntail"},
                {"role": "human", "content": "list the files"},
            ],
            "unprocessed_output": '{"route": "task", "answer": ""}',
            "processed_output": "route='task' answer=''",
        }
    )


def _visible_text(html: str) -> str:
    """Return the text a browser would show for Pygments' HTML."""
    return unescape(re.sub(r"<[^>]+>", "", html))


def test_highlight_details_keeps_fences_and_newlines():
    """The HTML keeps code fences and newlines, and has no error spans."""
    html = highlight_details(_text())
    assert 'class="err"' not in html
    assert "```" in html
    assert "\n" in html
    assert CODE_HL_CLASS in html


def test_highlight_details_colors_keys_distinctly():
    """Object keys use ``Name.Tag`` (``nt``), values ``String.Double`` (``s2``)."""
    html = highlight_details(_text())
    assert '<span class="nt">"input_prompt"</span>' in html
    assert '<span class="s2">"ai"</span>' in html


def test_highlight_details_multiline_string_keeps_content():
    """A multi-line value is rendered whole, without error spans."""
    html = highlight_details(format_details({"content": "a\nb\nc"}))
    assert 'class="err"' not in html
    assert "a\nb\nc" in _visible_text(html)


def test_highlight_details_text_matches_input():
    """The visible text reproduces the original formatted text exactly."""
    text = _text()
    assert _visible_text(highlight_details(text)).rstrip("\n") == text
