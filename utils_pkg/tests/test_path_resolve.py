#!/usr/bin/env python3
"""
Tests for resolving tool path arguments to absolute (per-session project root).

File: tests/test_path_resolve.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

from pathlib import Path

from klea_utils.mcp.path_detect import resolve_path_arguments
from klea_utils.mcp.schemas import ToolInfo


def test_declared_checkpath_relative_is_absolute(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = ToolInfo(checkpaths=["path"])
    out = resolve_path_arguments({"path": "a.txt"}, info, project_root=str(root))
    assert out == {"path": str(root / "a.txt")}


def test_declared_checkpath_absolute_is_untouched(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    absolute = tmp_path / "elsewhere.txt"
    info = ToolInfo(checkpaths=["path"])
    out = resolve_path_arguments({"path": str(absolute)}, info, project_root=str(root))
    assert out == {"path": str(absolute)}


def test_declared_checkpath_list_is_absolute(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = ToolInfo(checkpaths=["paths"])
    out = resolve_path_arguments(
        {"paths": ["a.txt", str(tmp_path / "b.txt")]},
        info,
        project_root=str(root),
    )
    assert out == {"paths": [str(root / "a.txt"), str(tmp_path / "b.txt")]}


def test_heuristic_pathy_name_resolved_without_tool_info(tmp_path):
    """A path-ish argument name resolves even for an unannotated (third-party) tool."""
    root = tmp_path / "root"
    root.mkdir()
    out = resolve_path_arguments({"path": "notes.txt"}, None, project_root=str(root))
    assert out == {"path": str(root / "notes.txt")}


def test_non_pathy_name_left_alone(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    out = resolve_path_arguments({"query": "hello"}, None, project_root=str(root))
    assert out == {"query": "hello"}


def test_command_string_is_never_rewritten(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    # ``command`` is not path-ish and the value is multi-token: untouched.
    out = resolve_path_arguments(
        {"command": "cat notes.txt"}, None, project_root=str(root)
    )
    assert out == {"command": "cat notes.txt"}


def test_pathy_name_but_multi_token_is_not_rewritten(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    out = resolve_path_arguments(
        {"path": "sed -n 1p a.txt"}, None, project_root=str(root)
    )
    assert out == {"path": "sed -n 1p a.txt"}


def test_url_and_flag_and_glob_untouched(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    out = resolve_path_arguments(
        {"path": "https://example.com/x", "file": "-x", "dir": "*.py"},
        None,
        project_root=str(root),
    )
    assert out == {"path": "https://example.com/x", "file": "-x", "dir": "*.py"}


def test_working_directory_defaults_to_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = ToolInfo(checkpaths=["working_directory"])
    out = resolve_path_arguments({"command": "pwd"}, info, project_root=str(root))
    assert out == {"command": "pwd", "working_directory": str(root)}


def test_working_directory_relative_resolves(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    info = ToolInfo(checkpaths=["working_directory"])
    out = resolve_path_arguments(
        {"command": "ls", "working_directory": "sub"}, info, project_root=str(root)
    )
    assert out == {"command": "ls", "working_directory": str(root / "sub")}


def test_empty_arguments_returned_as_is():
    assert resolve_path_arguments({}, None) == {}


def test_file_uri_untouched(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    out = resolve_path_arguments(
        {"path": "file:///etc/hosts"}, None, project_root=str(root)
    )
    assert out == {"path": "file:///etc/hosts"}


def test_default_root_is_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = resolve_path_arguments({"path": "a.txt"}, None)
    assert out == {"path": str(Path(tmp_path).resolve() / "a.txt")}
