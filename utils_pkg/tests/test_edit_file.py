#!/usr/bin/env python3
"""
Tests for the exact search/replace edit implementation.

File: utils_pkg/tests/test_edit_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import stat

from klea_utils.mcp.tool_impls import file_ops
from klea_utils.mcp.tool_impls.edit_file import edit_file

logger = logging.getLogger(__name__)


def test_edit_file_replaces_single_occurrence(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("alpha\nneedle\ngamma\n")

    result = edit_file(str(target), "needle", "replaced", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["replacements"] == 1
    assert result["matcher"] == "exact"
    assert target.read_text() == "alpha\nreplaced\ngamma\n"
    assert result["additions"] == 1
    assert result["deletions"] == 1


def test_edit_file_multiline_replacement(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("def main():\n    return 1\n")

    result = edit_file(
        str(target),
        "def main():\n    return 1\n",
        "def main():\n    return 2\n",
        project_root=str(tmp_path),
    )

    assert result["error"] == ""
    assert target.read_text() == "def main():\n    return 2\n"


def test_edit_file_not_found(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("alpha\n")

    result = edit_file(str(target), "absent", "x", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["replacements"] == 0
    assert "could not find" in result["error"].lower()
    assert target.read_text() == "alpha\n"


def test_edit_file_multiple_matches_refused(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hit\nhit\n")

    result = edit_file(str(target), "hit", "x", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert "multiple" in result["error"].lower() or "2 matches" in result["error"]
    assert target.read_text() == "hit\nhit\n"


def test_edit_file_replace_all(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hit\nhit\nhit\n")

    result = edit_file(
        str(target), "hit", "x", replace_all=True, project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["replacements"] == 3
    assert target.read_text() == "x\nx\nx\n"


def test_edit_file_identical_strings_refused(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("same\n")

    result = edit_file(str(target), "same", "same", project_root=str(tmp_path))

    assert "identical" in result["error"].lower()


def test_edit_file_empty_old_string_refused(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("same\n")

    result = edit_file(str(target), "", "x", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert "must not be empty" in result["error"].lower()
    assert "write_file" in result["error"]


def test_edit_file_missing_file(tmp_path):
    result = edit_file(str(tmp_path / "nope.txt"), "a", "b", project_root=str(tmp_path))

    assert "not found" in result["error"].lower()


def test_edit_file_rejects_directory(tmp_path):
    result = edit_file(str(tmp_path), "a", "b", project_root=str(tmp_path))

    assert "regular file" in result["error"].lower()


def test_edit_file_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("hello\n")

    result = edit_file(str(outside), "hello", "bye", project_root=str(root))

    assert "denied" in result["error"].lower()
    assert outside.read_text() == "hello\n"


def test_edit_file_preserves_mode(tmp_path):
    target = tmp_path / "a.sh"
    target.write_text("old\n")
    target.chmod(0o750)

    edit_file(str(target), "old", "new", project_root=str(tmp_path))

    assert stat.S_IMODE(target.stat().st_mode) == 0o750


def test_edit_file_preserves_crlf(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"alpha\r\nneedle\r\ngamma\r\n")

    result = edit_file(str(target), "needle", "replaced", project_root=str(tmp_path))

    assert result["error"] == ""
    assert target.read_bytes() == b"alpha\r\nreplaced\r\ngamma\r\n"


def test_edit_file_lf_old_string_matches_crlf_file(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"alpha\r\nneedle\r\n")

    result = edit_file(
        str(target), "alpha\nneedle", "one\ntwo", project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert target.read_bytes() == b"one\r\ntwo\r\n"


def test_edit_file_preserves_bom(tmp_path):
    target = tmp_path / "a.txt"
    target.write_bytes(file_ops.BOM.encode("utf-8") + b"old\n")

    result = edit_file(str(target), "old", "new", project_root=str(tmp_path))

    assert result["error"] == ""
    assert target.read_bytes() == file_ops.BOM.encode("utf-8") + b"new\n"


def test_edit_file_reports_diff(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("a\nb\nc\n")

    result = edit_file(str(target), "b", "B", project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert "-b" in result["diff"]
    assert "+B" in result["diff"]
    assert result["additions"] == 1
    assert result["deletions"] == 1
