#!/usr/bin/env python3
"""
Tests for the whole-file write implementation.

File: utils_pkg/tests/test_write_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import stat

from klea_utils.mcp.tool_impls import file_ops
from klea_utils.mcp.tool_impls.write_file import write_file

logger = logging.getLogger(__name__)


def test_write_file_creates_new_file(tmp_path):
    target = tmp_path / "a.txt"

    result = write_file(str(target), "hello\n", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["created"] is True
    assert result["bytes_written"] == len(b"hello\n")
    assert target.read_text() == "hello\n"


def test_write_file_creates_parents(tmp_path):
    target = tmp_path / "sub" / "dir" / "a.txt"

    result = write_file(str(target), "hi", project_root=str(tmp_path))

    assert result["error"] == ""
    assert target.read_text() == "hi"


def test_write_file_overwrites_and_reports_diff(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old\nlines\n")

    result = write_file(str(target), "new\nlines\n", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["created"] is False
    assert "old" not in target.read_text()
    assert result["additions"] == 1
    assert result["deletions"] == 1
    assert "+new" in result["diff"]


def test_write_file_preserves_mode(tmp_path):
    target = tmp_path / "a.sh"
    target.write_text("old")
    target.chmod(0o750)

    write_file(str(target), "new\n", project_root=str(tmp_path))

    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_write_file_preserves_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"a\r\nb\r\n")

    write_file(str(target), "x\ny\n", project_root=str(tmp_path))

    assert target.read_bytes() == b"x\r\ny\r\n"


def test_write_file_preserves_bom(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(file_ops.BOM.encode("utf-8") + b"old")

    write_file(str(target), "new", project_root=str(tmp_path))

    assert target.read_bytes() == file_ops.BOM.encode("utf-8") + b"new"


def test_write_file_content_bom_on_new_file(tmp_path):
    target = tmp_path / "a.txt"

    write_file(str(target), file_ops.BOM + "hi", project_root=str(tmp_path))

    assert target.read_bytes() == file_ops.BOM.encode("utf-8") + b"hi"


def test_write_file_empty_content(tmp_path):
    target = tmp_path / "empty.txt"

    result = write_file(str(target), "", project_root=str(tmp_path))

    assert result["error"] == ""
    assert target.read_text() == ""


def test_write_file_rejects_directory(tmp_path):
    result = write_file(str(tmp_path), "x", project_root=str(tmp_path))

    assert result["error"] != ""
    assert "directory" in result["error"].lower()


def test_write_file_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"

    result = write_file(str(outside), "x", project_root=str(root))

    logger.debug(f"{result = }")
    assert result["error"] != ""
    assert "denied" in result["error"].lower()
    assert not outside.exists()


def test_write_file_refuses_binary_existing_file(tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"data\x00binary")

    result = write_file(str(target), "text", project_root=str(tmp_path))

    assert "binary" in result["error"].lower()
