#!/usr/bin/env python3
"""
Tests for the TUI system clipboard fallback.

File: tests/ui/tui/test_clipboard.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import subprocess

import pytest
from klea_utils.ui.tui import clipboard


@pytest.fixture(autouse=True)
def _reset_clipboard_cache(monkeypatch):
    """Do not let one test's successful backend leak into the next."""
    monkeypatch.setattr(clipboard, "_cached_backend", None)


def test_prefers_wl_copy_on_wayland(monkeypatch):
    """A Wayland session tries wl-copy first and passes UTF-8 text."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None,
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert clipboard.copy_to_system_clipboard("hello") is True
    assert calls[0][0] == ["wl-copy"]
    assert calls[0][1]["input"] == b"hello"


def test_falls_back_when_the_first_backend_fails(monkeypatch):
    """A failing backend does not stop the next candidate being tried."""
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if command[0] == "wl-copy" else 0)

    monkeypatch.setattr(clipboard.subprocess, "run", fake_run)

    assert clipboard.copy_to_system_clipboard("hello") is True
    assert [command[0] for command in calls] == ["wl-copy", "xclip"]


def test_no_backend_returns_false(monkeypatch):
    """No installed backend is a non-fatal failure."""
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    called = []
    monkeypatch.setattr(
        clipboard.subprocess, "run", lambda *args, **kwargs: called.append(True)
    )

    assert clipboard.copy_to_system_clipboard("hello") is False
    assert not called


def test_empty_text_is_not_copied(monkeypatch):
    """There is nothing to send to a clipboard backend for empty text."""
    called = []
    monkeypatch.setattr(
        clipboard.subprocess, "run", lambda *args, **kwargs: called.append(True)
    )

    assert clipboard.copy_to_system_clipboard("") is False
    assert not called
