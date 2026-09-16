#!/usr/bin/env python3
"""
Tests for the shared filesystem walker used by read-only search tools.

File: utils_pkg/tests/test_walk.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import os

import pytest
from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.walk import SKIP_DIRECTORIES, iter_files

logger = logging.getLogger(__name__)


def _names(paths):
    return {p.name for p in paths}


def test_iter_files_yields_files_recursively(tmp_path):
    (tmp_path / "top.txt").write_text("top")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("nested")

    found = _names(list(iter_files(tmp_path, project_root=str(tmp_path))))

    logger.debug(f"{found = }")
    assert found == {"top.txt", "nested.txt"}


def test_iter_files_empty_dir_yields_nothing(tmp_path):
    assert list(iter_files(tmp_path, project_root=str(tmp_path))) == []


def test_iter_files_skips_skip_dirs(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("[core]")
    node = tmp_path / "node_modules"
    node.mkdir()
    (node / "pkg.json").write_text("{}")

    found = _names(list(iter_files(tmp_path, project_root=str(tmp_path))))

    logger.debug(f"{found = }")
    assert found == {"keep.txt"}


def test_iter_files_skip_dirs_argument_overrides_default(tmp_path):
    (tmp_path / "keep.txt").write_text("keep")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]")

    found = _names(
        list(iter_files(tmp_path, skip_dirs={"custom"}, project_root=str(tmp_path)))
    )

    logger.debug(f"{found = }")
    assert found == {"keep.txt", "config"}


def test_iter_files_root_named_like_skip_dir_is_traversed(tmp_path):
    """An explicitly requested root is walked even if its name is skipped."""
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("[core]")

    found = _names(list(iter_files(git, project_root=str(tmp_path))))

    logger.debug(f"{found = }")
    assert found == {"config"}


def test_iter_files_does_not_follow_dir_symlinks_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.txt").write_text("inside")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    (root / "link").symlink_to(outside, target_is_directory=True)

    found = _names(list(iter_files(root, project_root=str(root))))

    logger.debug(f"{found = }")
    assert found == {"inside.txt"}


def test_iter_files_skips_file_symlinks(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("real")
    (tmp_path / "link.txt").symlink_to(real)

    found = _names(list(iter_files(tmp_path, project_root=str(tmp_path))))

    logger.debug(f"{found = }")
    assert found == {"real.txt"}


def test_iter_files_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")

    with pytest.raises(PermissionDeniedError):
        list(iter_files(outside, project_root=str(root)))


def test_iter_files_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(iter_files(tmp_path / "nope", project_root=str(tmp_path)))


def test_iter_files_file_root_raises(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")

    with pytest.raises(NotADirectoryError):
        list(iter_files(a_file, project_root=str(tmp_path)))


@pytest.mark.skipif(os.geteuid() == 0, reason="permission bits ignored as root")
def test_iter_files_unreadable_subdir_is_skipped(tmp_path):
    (tmp_path / "top.txt").write_text("top")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "hidden.txt").write_text("hidden")
    locked.chmod(0o000)

    try:
        found = _names(list(iter_files(tmp_path, project_root=str(tmp_path))))
    finally:
        locked.chmod(0o700)

    logger.debug(f"{found = }")
    assert found == {"top.txt"}


def test_skip_directories_is_frozen():
    assert isinstance(SKIP_DIRECTORIES, frozenset)
    assert ".git" in SKIP_DIRECTORIES
    assert "__pycache__" in SKIP_DIRECTORIES
