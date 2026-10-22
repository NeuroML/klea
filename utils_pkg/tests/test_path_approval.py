#!/usr/bin/env python3
"""
Tests for the session path-approval helpers.

File: tests/test_path_approval.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from pathlib import Path

from klea_utils.mcp.path_approval import (
    entry_matches_path,
    find_covering_dir,
    find_matching_entry,
    format_allowed_paths,
    is_relative_to,
    normalise_allowed_dirs,
    normalise_allowed_files,
    normalise_directory,
    resolve_project_root,
    resolve_user_path,
)


def _root_and_outside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    return root, outside


def test_resolve_project_root_defaults_to_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert resolve_project_root(None) == tmp_path


def test_resolve_user_path_relative_and_absolute(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    assert resolve_user_path("sub", base) == base / "sub"
    assert resolve_user_path(str(tmp_path / "other"), base) == tmp_path / "other"


def test_is_relative_to():
    assert is_relative_to(Path("/a/b/c"), Path("/a/b"))
    assert not is_relative_to(Path("/a/b"), Path("/a/b/c"))


def test_normalise_directory_accepts_outside(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    resolved, error = normalise_directory(root, str(outside))
    assert error == ""
    assert resolved == str(outside)


def test_normalise_directory_rejects_missing(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    resolved, error = normalise_directory(root, str(tmp_path / "missing"))
    assert resolved is None
    assert "Cannot resolve" in error


def test_normalise_directory_rejects_file(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("x")
    resolved, error = normalise_directory(root, str(file))
    assert resolved is None
    assert "Not a directory" in error


def test_normalise_directory_rejects_inside_root(tmp_path):
    root, _ = _root_and_outside(tmp_path)
    inside = root / "inside"
    inside.mkdir()
    resolved, error = normalise_directory(root, str(inside))
    assert resolved is None
    assert "inside the project root" in error


def test_normalise_allowed_dirs_collects_errors_and_dedupes(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    accepted, ignored = normalise_allowed_dirs(
        root, [str(outside), str(outside), str(tmp_path / "missing")]
    )
    assert accepted == [str(outside)]
    assert len(ignored) == 1


def test_normalise_allowed_files_keeps_only_selected(tmp_path):
    first = str(tmp_path / "a.env")
    second = str(tmp_path / "b.env")
    assert normalise_allowed_files([first, second], [first]) == [first]
    assert normalise_allowed_files([first, second], [first, "new"]) == [first]


def test_format_allowed_paths_empty_and_populated(tmp_path):
    root, outside = _root_and_outside(tmp_path)
    empty = format_allowed_paths(root, [], [])
    assert f"Project root: {root}" in empty
    assert empty.count("(none)") == 2

    populated = format_allowed_paths(root, [str(outside)], ["/tmp/.env"])
    assert str(outside) in populated
    assert "- /tmp/.env" in populated


def test_entry_matching_and_covering(tmp_path):
    _root, outside = _root_and_outside(tmp_path)
    child = outside / "child"
    child.mkdir()
    resolved = child.resolve()

    assert entry_matches_path(str(outside), outside.resolve())
    assert find_matching_entry([str(outside)], outside.resolve()) == str(outside)
    assert find_matching_entry([str(outside)], resolved) is None
    assert find_covering_dir([str(outside)], resolved) == str(outside)
    assert find_covering_dir([], resolved) is None
