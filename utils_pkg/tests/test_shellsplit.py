#!/usr/bin/env python3
"""
Tests for the platform-aware shell-style argument splitter.

Both the tool-argument path discovery (``klea_utils.mcp.path_detect``) and the
``/command`` parser (``klea_utils.commands.common.parse_command``) split shell
strings with this helper, so a Windows path such as ``C:\\Users\\me\\a.txt``
must stay one token with its backslashes intact.

File: tests/test_shellsplit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_utils.mcp.path_detect import split_arguments


class TestPosixSplitting:
    def test_quotes_group(self):
        assert split_arguments('echo "a b" c', windows=False) == ["echo", "a b", "c"]

    def test_backslash_escapes_are_honoured(self):
        assert split_arguments(r"cat a\ b.txt", windows=False) == ["cat", "a b.txt"]

    def test_single_quotes_group(self):
        assert split_arguments("echo 'a b'", windows=False) == ["echo", "a b"]


class TestWindowsSplitting:
    def test_backslash_path_is_one_token(self):
        assert split_arguments(r"cat C:\Users\me\data\file.txt", windows=True) == [
            "cat",
            r"C:\Users\me\data\file.txt",
        ]

    def test_quoted_path_with_spaces_keeps_backslashes(self):
        assert split_arguments(r'cat "C:\Program Files\a b.txt"', windows=True) == [
            "cat",
            r"C:\Program Files\a b.txt",
        ]

    def test_single_quoted_path_is_unquoted(self):
        assert split_arguments(r"cat 'C:\data\a b.txt'", windows=True) == [
            "cat",
            r"C:\data\a b.txt",
        ]

    def test_redirection_target_keeps_backslashes(self):
        assert split_arguments(r"tool 2>C:\logs\err.txt", windows=True) == [
            "tool",
            r"2>C:\logs\err.txt",
        ]


class TestPlatformDefault:
    def test_posix_rules_by_default(self, monkeypatch):
        monkeypatch.setattr(
            "klea_utils.mcp.path_detect.platform_is_windows", lambda: False
        )
        assert split_arguments('echo "a b"') == ["echo", "a b"]

    def test_windows_rules_when_flagged(self, monkeypatch):
        monkeypatch.setattr(
            "klea_utils.mcp.path_detect.platform_is_windows", lambda: True
        )
        assert split_arguments(r"cat C:\data\a.txt") == ["cat", r"C:\data\a.txt"]


@pytest.mark.parametrize("windows", [False, True])
def test_unbalanced_quote_still_raises(windows):
    """Malformed quoting is still a ``ValueError`` on every platform."""
    with pytest.raises(ValueError):
        split_arguments('cat "unclosed', windows=windows)
