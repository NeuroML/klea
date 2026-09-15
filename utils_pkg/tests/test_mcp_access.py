#!/usr/bin/env python3
"""
Tests for the shared tool access level helpers (ADR-0037).

File: tests/test_mcp_access.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.mcp.access import (
    DEFAULT_ACCESS_LEVEL,
    ToolAccessOverride,
    check_tool_access,
    filter_tools_info,
    resolve_access_level,
    resolve_capability,
    tool_permits,
)
from klea_utils.mcp.schemas import ToolInfo


def _info(read_only: bool | None = None, destructive: bool | None = None) -> ToolInfo:
    return ToolInfo(read_only=read_only, destructive=destructive)


class TestToolPermits:
    """The permit rule: full allows all; read_only is fail-closed."""

    def test_full_permits_everything(self):
        assert tool_permits(None, None, "full") is True
        assert tool_permits(True, None, "full") is True
        assert tool_permits(None, True, "full") is True
        assert tool_permits(False, True, "full") is True

    def test_read_only_permits_explicit_read_only(self):
        assert tool_permits(True, None, "read_only") is True
        assert tool_permits(True, False, "read_only") is True

    def test_read_only_fails_closed_on_unannotated(self):
        assert tool_permits(None, None, "read_only") is False
        assert tool_permits(False, None, "read_only") is False

    def test_read_only_rejects_destructive(self):
        assert tool_permits(True, True, "read_only") is False
        assert tool_permits(None, True, "read_only") is False


class TestFilterToolsInfo:
    """Disclosure filtering drops disallowed tools without mutating input."""

    @staticmethod
    def _tools_info():
        return {
            "a": {
                "search": _info(read_only=True),
                "delete": _info(destructive=True),
                "plain": _info(),
            },
            "b": {"fetch": _info(read_only=True)},
        }

    def test_full_returns_input_unchanged(self):
        info = self._tools_info()
        assert filter_tools_info(info, "full") is info

    def test_read_only_drops_disallowed(self):
        filtered = filter_tools_info(self._tools_info(), "read_only")
        assert set(filtered["a"]) == {"search"}
        assert set(filtered["b"]) == {"fetch"}

    def test_read_only_keeps_empty_domain_keys(self):
        info = {"a": {"delete": _info(destructive=True)}}
        filtered = filter_tools_info(info, "read_only")
        assert filtered == {"a": {}}
        # the input mapping is not mutated
        assert "delete" in info["a"]


class TestCheckToolAccess:
    """check_tool_access returns None when allowed, a message otherwise."""

    def test_allowed_returns_none(self):
        assert check_tool_access("search", True, None, "read_only") is None
        assert check_tool_access("delete", None, True, "full") is None

    def test_denied_returns_message(self):
        msg = check_tool_access("delete", None, True, "read_only")
        assert msg is not None and "delete" in msg
        msg2 = check_tool_access("plain", None, None, "read_only")
        assert msg2 is not None and "plain" in msg2


class TestResolveCapability:
    """Config override precedence: override > annotation > None."""

    def test_no_override_unchanged(self):
        assert resolve_capability(True, None, None) == (True, None)

    def test_override_relaxes_unannotated(self):
        assert resolve_capability(None, None, ToolAccessOverride(read_only=True)) == (
            True,
            None,
        )

    def test_override_restricts_optimistic_tool(self):
        assert resolve_capability(True, None, ToolAccessOverride(destructive=True)) == (
            True,
            True,
        )

    def test_partial_override_leaves_other_field(self):
        assert resolve_capability(True, True, ToolAccessOverride(read_only=False)) == (
            False,
            True,
        )


class TestResolveAccessLevel:
    """Untrusted inputs coerce to a level, defaulting to full."""

    def test_valid_values(self):
        assert resolve_access_level("read_only") == "read_only"
        assert resolve_access_level("full") == "full"
        assert resolve_access_level(" READ_ONLY ") == "read_only"

    def test_invalid_defaults_to_full(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert resolve_access_level("wat") == DEFAULT_ACCESS_LEVEL
        assert "Unknown access level" in caplog.text

    def test_none_defaults_silently(self):
        assert resolve_access_level(None) == DEFAULT_ACCESS_LEVEL
