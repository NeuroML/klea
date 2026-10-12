#!/usr/bin/env python3
"""
Tests for the shared transcript-export helpers (ADR-0047).

File: tests/test_ui_export.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from datetime import datetime

from klea_utils.ui.export import (
    export_filename,
    export_timestamp,
    transcript_markdown,
)


def test_transcript_markdown_renders_roles_and_tools():
    """Roles map to labels; tool blocks use their header."""
    messages = [
        {"text": "hello", "stamp": "10:00:00", "role": "user", "header": ""},
        {"text": "hi there", "stamp": "10:00:01", "role": "agent", "header": ""},
        {"text": "out", "stamp": "10:00:02", "role": "tool", "header": "run_command"},
    ]
    md = transcript_markdown(messages, title="My Session", exported_at="2026-01-01")
    assert md.startswith("# My Session")
    assert "_Exported 2026-01-01_" in md
    assert "**User** (10:00:00)" in md
    assert "**Assistant** (10:00:01)" in md
    assert "**run_command** (10:00:02)" in md
    assert "hello" in md and "hi there" in md and "out" in md
    assert md.endswith("\n")


def test_transcript_markdown_without_timestamp():
    """No timestamp means no exported-at line."""
    md = transcript_markdown([{"text": "x", "stamp": "", "role": "user", "header": ""}])
    assert "_Exported" not in md
    assert "**User**" in md


def test_export_filename_sanitises():
    assert export_filename("My Session / 2026") == "My-Session-2026.md"
    assert export_filename("") == "klea-session.md"
    assert export_filename("...") == "klea-session.md"
    assert export_filename("name", suffix="json") == "name.json"


def test_export_timestamp_format():
    assert export_timestamp(datetime(2026, 1, 1, 12, 0, 0)) == "2026-01-01 12:00:00"
