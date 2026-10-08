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
from klea_utils.mcp.path_detect import detect_path_requests
from klea_utils.mcp.schemas import ToolInfo
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


def test_allowed_dirs_permits_outside(tmp_path):
    """A session-approved directory is allowed in addition to the root."""
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    with pytest.raises(PermissionDeniedError):
        check_path_access(outside / "f.txt", root)
    check_path_access(outside / "f.txt", root, allowed_dirs=[str(outside)])


def test_allowed_dirs_used_by_tool_arguments(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    meta = {"checkpaths": ["path"]}
    assert (
        check_tool_arguments_permissions(
            meta, {"path": str(outside)}, root, allowed_dirs=[str(outside)]
        )
        == []
    )


def _root_only(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def test_detect_ignores_inside_paths(tmp_path):
    root = _root_only(tmp_path)
    (root / "f.txt").touch()
    info = ToolInfo(checkpaths=["path"])
    assert (
        detect_path_requests(
            "read_file", {"path": str(root / "f.txt")}, info, project_root=root
        )
        == []
    )


def test_detect_declared_outside(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    requests = detect_path_requests(
        "read_file", {"path": str(outside / "x.txt")}, info, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "declared"
    assert requests[0].source == "checkpaths:path"
    assert requests[0].directory == str(outside)


def test_detect_picker_paths_expected(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "some_tool", {}, None, project_root=root, picker_paths=[str(outside / "f")]
    )
    assert [request.confidence for request in requests] == ["expected"]
    assert requests[0].source == "picker"


def test_detect_name_heuristic(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "t", {"file_path": str(outside / "f")}, None, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "guess"
    assert requests[0].source == "name:file_path"


def test_detect_shell_command_tokens(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    command = f"cat {outside}/a.txt && echo done"
    requests = detect_path_requests(
        "run_command", {"command": command}, None, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].directory == str(outside)
    assert requests[0].confidence == "guess"
    assert requests[0].source == "value:command"


def test_detect_shell_redirection_assignment_and_flags(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    command = f"tool -o --out={outside}/log FOO={outside}/env 2>{outside}/err"
    requests = detect_path_requests(
        "run_command", {"command": command}, None, project_root=root
    )
    assert {request.directory for request in requests} == {str(outside)}


def test_detect_skips_urls_and_globs(tmp_path):
    root = _root_only(tmp_path)
    command = "curl https://example.com/x && ls *.py && rg 'a/b'"
    assert (
        detect_path_requests(
            "run_command", {"command": command}, None, project_root=root
        )
        == []
    )


def test_detect_dedup_keeps_strongest(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    requests = detect_path_requests(
        "t", {"path": str(outside / "f")}, info, project_root=root
    )
    assert len(requests) == 1
    assert requests[0].confidence == "declared"


def test_detect_allowed_dirs_suppresses(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    info = ToolInfo(checkpaths=["path"])
    assert (
        detect_path_requests(
            "t",
            {"path": str(outside / "f")},
            info,
            project_root=root,
            allowed_dirs=[str(outside)],
        )
        == []
    )


def test_detect_file_uri_is_a_path(tmp_path):
    """A ``file:`` URI names a local path and must be checked, not skipped."""
    root, outside = _root_and_outside(tmp_path)
    outside.mkdir()
    requests = detect_path_requests(
        "run_command",
        {"command": f"cat file://{outside}/a.txt"},
        None,
        project_root=root,
    )
    assert len(requests) == 1
    assert requests[0].directory == str(outside)


def test_detect_file_uri_without_authority(tmp_path):
    """``file:/etc/passwd`` (no ``//``) is still unwrapped and checked."""
    root = _root_only(tmp_path)
    requests = detect_path_requests(
        "t",
        {"path": "file:/etc/passwd"},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
    )
    assert len(requests) == 1
    assert requests[0].directory == "/etc"


def test_detect_http_url_still_skipped(tmp_path):
    root = _root_only(tmp_path)
    assert (
        detect_path_requests(
            "run_command",
            {"command": "curl https://example.com/etc/passwd"},
            None,
            project_root=root,
        )
        == []
    )


def test_detect_dotdot_smuggling(tmp_path):
    root = _root_only(tmp_path)
    (tmp_path / "secret.txt").touch()
    requests = detect_path_requests(
        "t",
        {"path": str(root / ".." / "secret.txt")},
        ToolInfo(checkpaths=["path"]),
        project_root=root,
    )
    assert requests
    assert requests[0].directory == str(tmp_path)
