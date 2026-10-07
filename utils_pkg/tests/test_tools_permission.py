#!/usr/bin/env python3
"""
Tests for the shared path permission layer.

File: utils_pkg/tests/test_tools_permission.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import (
    check_path_access,
    check_tool_arguments_permissions,
)

logger = logging.getLogger(__name__)


def _root_and_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    return root, outside


def test_allows_inside_project(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    (root / "sub").mkdir()
    check_path_access(root, root)
    check_path_access(root / "sub", root)
    check_path_access(root / "file.txt", root)
    check_path_access(root / "sub" / "deep.txt", root)


def test_allows_root_itself(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    check_path_access(root, root)


def test_denies_outside_project(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    with pytest.raises(PermissionDeniedError):
        check_path_access(outside, root)


def test_denies_sibling(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PermissionDeniedError):
        check_path_access(other, root)


def test_denies_dotdot_smuggling(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    with pytest.raises(PermissionDeniedError):
        check_path_access(root / ".." / "outside", root)


def test_denies_symlink_escape(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.write_text("secret")
    link = root / "link.txt"
    link.symlink_to(outside)
    with pytest.raises(PermissionDeniedError):
        check_path_access(link, root)


def test_allows_symlink_inside(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    (root / "real.txt").write_text("x")
    link = root / "link.txt"
    link.symlink_to(root / "real.txt")
    check_path_access(link, root)


def test_default_root_is_cwd(tmp_path, monkeypatch):
    root, _ = _root_and_outside(tmp_path)
    monkeypatch.chdir(root)
    check_path_access(root / "sub")
    with pytest.raises(PermissionDeniedError):
        check_path_access(tmp_path)


def test_check_tool_arguments_allows_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "file.txt").touch()
    meta = {"checkpaths": ["path"]}
    result = check_tool_arguments_permissions(
        meta, {"path": str(root / "file.txt")}, root
    )
    assert result == []


def test_check_tool_arguments_denies_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()
    meta = {"checkpaths": ["path"]}
    denials = check_tool_arguments_permissions(meta, {"path": str(outside)}, root)
    assert len(denials) == 1
    assert "denied" in denials[0]


def test_check_tool_arguments_no_meta(tmp_path):
    assert check_tool_arguments_permissions(None, {"path": "/etc"}, tmp_path) == []
    assert check_tool_arguments_permissions({}, {"path": "/etc"}, tmp_path) == []


def test_check_tool_arguments_no_checkpaths(tmp_path):
    assert (
        check_tool_arguments_permissions({"other": 1}, {"path": "/etc"}, tmp_path) == []
    )


def test_check_tool_arguments_missing_arg(tmp_path):
    meta = {"checkpaths": ["path"]}
    assert check_tool_arguments_permissions(meta, {}, tmp_path) == []


def test_check_tool_arguments_skips_non_string(tmp_path):
    meta = {"checkpaths": ["limit"]}
    assert check_tool_arguments_permissions(meta, {"limit": 3}, tmp_path) == []


def test_denies_symlink_loop(tmp_path):
    """A self-referential symlink cannot be resolved, so it is denied."""
    root, _ = _root_and_outside(tmp_path)
    loop = root / "loop"
    loop.symlink_to("loop")

    with pytest.raises(PermissionDeniedError):
        check_path_access(loop, root)


def test_symlink_loop_is_a_non_halting_denial(tmp_path):
    """The client-side gate reports a symlink loop as a denial, not a crash."""
    root, _ = _root_and_outside(tmp_path)
    loop = root / "loop"
    loop.symlink_to("loop")

    denials = check_tool_arguments_permissions(
        {"checkpaths": ["path"]}, {"path": str(loop)}, str(root)
    )

    assert denials
    assert "resolve" in denials[0].lower()
