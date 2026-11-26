#!/usr/bin/env python3
"""
Path-check modes, dispatch skip, stance and parser tests.

Covers the path-check plumbing in ``klea_utils.mcp``:

(a) the ``parse_*`` helpers accept the documented values, default on
    ``None``/empty input and raise ``ValueError`` naming the environment
    variable on a bad value;
(b) ``strict`` mode detects the value heuristic (a path inside an
    arbitrary argument value);
(c) ``declared`` mode drops the value heuristic but keeps declared
    ``ToolInfo.checkpaths`` entries and ``picker_paths``;
(d) ``off`` mode detects nothing;
(e) ``dispatch_tool_calls`` with ``path_check_mode="off"`` still calls the
    tool and never invokes the permission resolver;
(f) the resolver built by ``ToolsCallerNode._make_permission_resolver``
    with ``hitl_enabled=False`` denies on stance ``deny`` and allows the
    current call on stance ``allow``, recording no session approvals.

The ``FakeMCPClient`` helper mirrors the one in
``utils_pkg/tests/test_mcp_dispatch.py``: it records ``(name, arguments)``
in ``self.calls`` and returns an empty ``CallToolResult``.

For (f) the resolver is obtained from the real factory method invoked on a
minimal stub node/context.  ``ToolsCallerNode`` itself needs a full
``ToolCallerContext`` and stream plumbing to construct standalone, whereas
the ``hitl_enabled=False`` branch only touches ``logger``,
``_emit_permission_inspect``, ``ctx.hitl_enabled`` and
``ctx.path_check_stance``.  No sensitive-path assertions are made here:
``is_sensitive`` could not be confirmed for a ``.env`` name, so only plain
non-sensitive paths outside the project root are used.

File: tests/test_path_check_modes.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from typing import cast

import pytest
from fastmcp.client.client import CallToolResult
from klea_utils.mcp.access import (
    DEFAULT_PATH_CHECK_MODE,
    DEFAULT_PATH_CHECK_STANCE,
    HITL_ENV_VAR,
    PATH_CHECK_ENV_VAR,
    PATH_CHECK_STANCE_ENV_VAR,
    parse_hitl_enabled,
    parse_path_check_mode,
    parse_path_check_stance,
)
from klea_utils.mcp.dispatch import dispatch_tool_calls
from klea_utils.mcp.path_detect import detect_path_requests
from klea_utils.mcp.permission_hitl import PermissionResolution
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.nodes.tools_caller import ToolCallerContext, ToolsCallerNode

logger = logging.getLogger(__name__)


class FakeMCPClient:
    """Record ``call_tool`` invocations and return an empty result."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def call_tool(self, name, arguments, raise_on_error=False, timeout=None):
        self.calls.append((name, arguments))
        return CallToolResult(content=[], structured_content=None, meta=None)


@pytest.fixture()
def project_tree(tmp_path):
    """Return a project root plus a real file outside it."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the project root\n", encoding="utf-8")
    return root, outside


def _detected_requests(root, outside):
    """Return the strict-mode requests for a path inside a command value."""
    requests = detect_path_requests(
        "run_command",
        {"command": f"cat {outside}"},
        project_root=root,
        path_check_mode="strict",
    )
    assert requests, "strict mode should detect the path in the command value"
    return requests


class _StubToolsCallerNode:
    """Minimal stand-in carrying what the resolver factory touches."""

    def __init__(self) -> None:
        self.logger = logger
        self.inspections = []

    def _emit_permission_inspect(self, requests, resolution):
        self.inspections.append((list(requests), resolution))


class _StubToolCallerContext:
    """Minimal stand-in for ``ToolCallerContext``."""

    def __init__(self, hitl_enabled: bool, path_check_stance: str) -> None:
        self.hitl_enabled = hitl_enabled
        self.path_check_stance = path_check_stance
        self.allowed_session_dirs: list[str] = []
        self.allowed_session_files: list[str] = []


def _make_resolver(node, ctx):
    """Build the real resolver factory against the minimal stand-ins."""
    return ToolsCallerNode._make_permission_resolver(
        cast(ToolsCallerNode, node), cast(ToolCallerContext, ctx)
    )


# ---------------------------------------------------------------------------
# (a) parsers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["strict", "declared", "off"])
def test_parse_path_check_mode_accepts_documented_values(value):
    assert parse_path_check_mode(value) == value


@pytest.mark.parametrize("value", [None, ""])
def test_parse_path_check_mode_defaults(value):
    assert parse_path_check_mode(value) == DEFAULT_PATH_CHECK_MODE


@pytest.mark.parametrize("value", ["bogus", "on"])
def test_parse_path_check_mode_rejects_bad_value(value):
    with pytest.raises(ValueError, match=PATH_CHECK_ENV_VAR):
        parse_path_check_mode(value)


@pytest.mark.parametrize("value", ["deny", "allow"])
def test_parse_path_check_stance_accepts_documented_values(value):
    assert parse_path_check_stance(value) == value


@pytest.mark.parametrize("value", [None, ""])
def test_parse_path_check_stance_defaults(value):
    assert parse_path_check_stance(value) == DEFAULT_PATH_CHECK_STANCE


@pytest.mark.parametrize("value", ["bogus", "permit"])
def test_parse_path_check_stance_rejects_bad_value(value):
    with pytest.raises(ValueError, match=PATH_CHECK_STANCE_ENV_VAR):
        parse_path_check_stance(value)


@pytest.mark.parametrize("value", ["1", "true"])
def test_parse_hitl_enabled_accepts_true(value):
    assert parse_hitl_enabled(value) is True


@pytest.mark.parametrize("value", ["0", "false"])
def test_parse_hitl_enabled_accepts_false(value):
    assert parse_hitl_enabled(value) is False


@pytest.mark.parametrize("value", [None, ""])
def test_parse_hitl_enabled_defaults(value):
    parsed = parse_hitl_enabled(value)
    assert isinstance(parsed, bool)
    assert parsed == parse_hitl_enabled(None)


def test_parse_hitl_enabled_rejects_bad_value():
    with pytest.raises(ValueError, match=HITL_ENV_VAR):
        parse_hitl_enabled("maybe")


# ---------------------------------------------------------------------------
# (b) strict mode: value heuristic
# ---------------------------------------------------------------------------


def test_strict_mode_detects_value_heuristic(project_tree):
    root, outside = project_tree
    requests = _detected_requests(root, outside)
    assert any(request.source.startswith("value:") for request in requests)


# ---------------------------------------------------------------------------
# (c) declared mode: checkpaths and picker paths, no heuristics
# ---------------------------------------------------------------------------


def test_declared_mode_keeps_checkpaths_and_drops_heuristics(project_tree):
    root, outside = project_tree
    requests = detect_path_requests(
        "run_command",
        {"command": f"cat {outside}", "path": str(outside)},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
        path_check_mode="declared",
    )
    sources = [request.source for request in requests]
    assert any(source.startswith("checkpaths:path") for source in sources)
    assert not any(source.startswith("value:") for source in sources)


def test_declared_mode_keeps_picker_paths(project_tree):
    root, outside = project_tree
    requests = detect_path_requests(
        "run_command",
        {"command": "ls"},
        None,
        project_root=root,
        picker_paths=[str(outside)],
        path_check_mode="declared",
    )
    assert any(request.source.startswith("picker") for request in requests)


# ---------------------------------------------------------------------------
# (d) off mode: nothing detected
# ---------------------------------------------------------------------------


def test_off_mode_detects_nothing(project_tree):
    root, outside = project_tree
    requests = detect_path_requests(
        "run_command",
        {"command": f"cat {outside}", "path": str(outside)},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
        picker_paths=[str(outside)],
        path_check_mode="off",
    )
    assert requests == []


# ---------------------------------------------------------------------------
# (e) dispatch with path_check_mode="off" still calls the tool
# ---------------------------------------------------------------------------


def test_dispatch_off_mode_calls_tool_without_permission(project_tree):
    root, outside = project_tree
    client = FakeMCPClient()

    def _resolver(requests):
        raise AssertionError(
            "permission resolver must not run when path_check_mode='off'"
        )

    results = asyncio.run(
        dispatch_tool_calls(
            client,
            [("run_command", {"command": f"cat {outside}"})],
            tool_infos={"run_command": ToolInfo()},
            project_root=str(root),
            access_level="full",
            permission_resolver=_resolver,
            path_check_mode="off",
        )
    )

    assert client.calls == [("run_command", {"command": f"cat {outside}"})]
    assert len(results) == 1


# ---------------------------------------------------------------------------
# (f) resolver with HITL disabled: stance deny / allow
# ---------------------------------------------------------------------------


def test_resolver_denies_when_hitl_disabled_and_stance_deny(project_tree):
    root, outside = project_tree
    requests = _detected_requests(root, outside)
    node = _StubToolsCallerNode()
    ctx = _StubToolCallerContext(hitl_enabled=False, path_check_stance="deny")

    resolver = _make_resolver(node, ctx)
    resolution = resolver(requests)

    assert isinstance(resolution, PermissionResolution)
    assert resolution.denied == [request.approval_key for request in requests]
    assert resolution.allowed_now == []
    assert resolution.allowed_session == []
    assert ctx.allowed_session_dirs == []
    assert ctx.allowed_session_files == []
    assert len(node.inspections) == 1


def test_resolver_allows_now_when_hitl_disabled_and_stance_allow(project_tree):
    root, outside = project_tree
    requests = _detected_requests(root, outside)
    node = _StubToolsCallerNode()
    ctx = _StubToolCallerContext(hitl_enabled=False, path_check_stance="allow")

    resolver = _make_resolver(node, ctx)
    resolution = resolver(requests)

    assert isinstance(resolution, PermissionResolution)
    assert resolution.allowed_now == [request.approval_key for request in requests]
    assert resolution.denied == []
    assert resolution.allowed_session == []
    assert ctx.allowed_session_dirs == []
    assert ctx.allowed_session_files == []
    assert len(node.inspections) == 1
