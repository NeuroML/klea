#!/usr/bin/env python3
"""
Tests for best-effort process-title setting.

File: tests/test_proctitle.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import sys
import types

from klea_utils.proctitle import set_process_title


def test_empty_name_is_a_noop():
    """An empty title is ignored (no error)."""
    set_process_title("")


def test_set_process_title_never_raises():
    """The Linux prctl fallback (no setproctitle) does not raise."""
    sys.modules.pop("setproctitle", None)
    set_process_title("klea-agent-tui")


def test_uses_setproctitle_when_available(monkeypatch):
    """A present ``setproctitle`` package is preferred."""
    calls: list[str] = []

    class _FakeSetProcTitle(types.ModuleType):
        def setproctitle(self, name: str) -> None:
            calls.append(name)

    monkeypatch.setitem(sys.modules, "setproctitle", _FakeSetProcTitle("setproctitle"))

    set_process_title("klea-agent-tui")

    assert calls == ["klea-agent-tui"]


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
