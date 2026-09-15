#!/usr/bin/env python3
"""
Tests for the root privilege guard (ADR-0038).

File: utils_pkg/tests/test_mcp_privilege.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.mcp import privilege
from klea_utils.mcp.privilege import (
    ALLOW_ROOT_ENV_VAR,
    allow_root_tools,
    root_denial_message,
    running_as_root,
    warn_if_root,
)


def test_running_as_root_true_and_false(monkeypatch):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 0, raising=False)
    assert running_as_root() is True
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000, raising=False)
    assert running_as_root() is False


def test_running_as_root_without_geteuid(monkeypatch):
    """Non-POSIX (no geteuid) is treated as non-root."""
    monkeypatch.delattr(privilege.os, "geteuid", raising=False)
    assert running_as_root() is False


def test_allow_root_tools_env(monkeypatch):
    monkeypatch.delenv(ALLOW_ROOT_ENV_VAR, raising=False)
    assert allow_root_tools() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv(ALLOW_ROOT_ENV_VAR, value)
        assert allow_root_tools() is True
    monkeypatch.setenv(ALLOW_ROOT_ENV_VAR, "no")
    assert allow_root_tools() is False


def test_root_denial_message_names_env_var():
    assert ALLOW_ROOT_ENV_VAR in root_denial_message()


def test_warn_if_root_refusal(monkeypatch, caplog):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.delenv(ALLOW_ROOT_ENV_VAR, raising=False)
    with caplog.at_level(logging.WARNING):
        warn_if_root(logging.getLogger("test"))
    assert "root" in caplog.text.lower()
    assert ALLOW_ROOT_ENV_VAR in caplog.text


def test_warn_if_root_override(monkeypatch, caplog):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setenv(ALLOW_ROOT_ENV_VAR, "1")
    with caplog.at_level(logging.WARNING):
        warn_if_root(logging.getLogger("test"))
    assert "root privileges" in caplog.text.lower()


def test_warn_if_root_noop_when_not_root(monkeypatch, caplog):
    monkeypatch.setattr(privilege.os, "geteuid", lambda: 1000, raising=False)
    with caplog.at_level(logging.WARNING):
        warn_if_root(logging.getLogger("test"))
    assert caplog.text == ""
