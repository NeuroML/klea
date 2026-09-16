#!/usr/bin/env python3
"""
Tests for the shared file-editing helpers.

File: utils_pkg/tests/test_file_ops.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import stat

import pytest
from klea_utils.mcp.errors import FileEditError
from klea_utils.mcp.tool_impls import file_ops

logger = logging.getLogger(__name__)


def test_detect_newline():
    assert file_ops.detect_newline("a\nb\n") == "\n"
    assert file_ops.detect_newline("a\r\nb\r\n") == "\r\n"
    assert file_ops.detect_newline("a\r\nb\n") == "\r\n"


def test_normalize_and_apply_newline():
    assert file_ops.normalize_newlines("a\r\nb\r\n") == "a\nb\n"
    assert file_ops.apply_newline("a\nb\n", "\n") == "a\nb\n"
    assert file_ops.apply_newline("a\nb\n", "\r\n") == "a\r\nb\r\n"


def test_bom_split_and_join():
    assert file_ops.split_bom(file_ops.BOM + "hi") == ("hi", True)
    assert file_ops.split_bom("hi") == ("hi", False)
    assert file_ops.join_bom("hi", True) == file_ops.BOM + "hi"
    assert file_ops.join_bom("hi", False) == "hi"


def test_is_binary():
    assert file_ops.is_binary(b"plain text") is False
    assert file_ops.is_binary(b"has\x00nul") is True


def test_read_whole_text_basic(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello\nworld\n")

    doc = file_ops.read_whole_text(f)

    logger.debug(f"{doc = }")
    assert doc.text == "hello\nworld\n"
    assert doc.bom is False
    assert doc.newline == "\n"
    assert stat.S_IMODE(doc.mode) == stat.S_IMODE(f.stat().st_mode)


def test_read_whole_text_detects_crlf(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"a\r\nb\r\n")

    assert file_ops.read_whole_text(f).newline == "\r\n"


def test_read_whole_text_strips_bom(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(file_ops.BOM.encode("utf-8") + b"hi")

    doc = file_ops.read_whole_text(f)

    assert doc.text == "hi"
    assert doc.bom is True


def test_read_whole_text_missing(tmp_path):
    with pytest.raises(FileEditError, match="not found"):
        file_ops.read_whole_text(tmp_path / "nope.txt")


def test_read_whole_text_directory(tmp_path):
    with pytest.raises(FileEditError, match="regular file"):
        file_ops.read_whole_text(tmp_path)


def test_read_whole_text_binary(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"text\x00binary")

    with pytest.raises(FileEditError, match="binary"):
        file_ops.read_whole_text(f)


def test_read_whole_text_invalid_utf8(tmp_path):
    f = tmp_path / "latin.txt"
    f.write_bytes(b"caf\xe9")

    with pytest.raises(FileEditError, match="UTF-8"):
        file_ops.read_whole_text(f)


def test_read_whole_text_too_large(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("a" * 100)

    with pytest.raises(FileEditError, match="too large"):
        file_ops.read_whole_text(f, max_bytes=10)


def test_write_whole_text_creates_file_and_parents(tmp_path):
    target = tmp_path / "sub" / "dir" / "a.txt"

    file_ops.write_whole_text(target, "hi\n")

    assert target.read_text() == "hi\n"


def test_write_whole_text_applies_newline_and_bom(tmp_path):
    target = tmp_path / "a.txt"

    file_ops.write_whole_text(target, "a\nb\n", newline="\r\n", bom=True)

    assert target.read_bytes() == file_ops.BOM.encode("utf-8") + b"a\r\nb\r\n"


def test_write_whole_text_preserves_existing_mode(tmp_path):
    target = tmp_path / "a.sh"
    target.write_text("old")
    target.chmod(0o750)

    file_ops.write_whole_text(target, "new\n")

    assert stat.S_IMODE(target.stat().st_mode) == 0o750
    assert target.read_text() == "new\n"


def test_write_whole_text_new_file_default_mode(tmp_path):
    target = tmp_path / "a.txt"

    file_ops.write_whole_text(target, "hi")

    assert stat.S_IMODE(target.stat().st_mode) == file_ops.DEFAULT_FILE_MODE


def test_write_whole_text_rejects_directory(tmp_path):
    with pytest.raises(FileEditError, match="directory"):
        file_ops.write_whole_text(tmp_path, "x")


def test_write_whole_text_leaves_no_temp_files(tmp_path):
    target = tmp_path / "a.txt"

    file_ops.write_whole_text(target, "hi\n")

    names = {p.name for p in tmp_path.iterdir()}
    logger.debug(f"{names = }")
    assert names == {"a.txt"}


def test_unified_diff_and_counts():
    old = "a\nb\nc\n"
    new = "a\nB\nc\nd\n"

    diff = file_ops.unified_diff(old, new, path="f.txt")
    additions, deletions = file_ops.diff_counts(old, new)

    logger.debug(f"{diff = }")
    assert "--- f.txt" in diff
    assert "+++ f.txt" in diff
    assert "-b" in diff
    assert "+B" in diff
    assert (additions, deletions) == (2, 1)


def test_unified_diff_identical_is_empty():
    assert file_ops.unified_diff("same\n", "same\n") == ""
    assert file_ops.diff_counts("same\n", "same\n") == (0, 0)
