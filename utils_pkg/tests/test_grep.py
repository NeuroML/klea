#!/usr/bin/env python3
"""
Tests for the regular-expression content search implementation.

File: utils_pkg/tests/test_grep.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_utils.mcp.tool_impls import grep as grep_module
from klea_utils.mcp.tool_impls.grep import grep, grep_inhouse

logger = logging.getLogger(__name__)


def _matched_lines(result: dict) -> list[dict]:
    """Return the matching lines across all hunks."""
    return [
        line for hunk in result["matches"] for line in hunk["lines"] if line["match"]
    ]


def test_grep_finds_matching_lines(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nneedle here\ngamma")

    result = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert len(result["matches"]) == 1
    hunk = result["matches"][0]
    assert hunk["path"] == "a.txt"
    assert hunk["start_line"] == 1
    assert hunk["end_line"] == 3
    assert [line["line"] for line in hunk["lines"]] == [
        "alpha",
        "needle here",
        "gamma",
    ]
    assert [line["match"] for line in hunk["lines"]] == [False, True, False]
    assert _matched_lines(result)[0]["line_number"] == 2
    assert result["files_scanned"] == 1


def test_grep_multiple_files_and_lines(tmp_path):
    (tmp_path / "a.txt").write_text("hit\nmiss\nhit")
    (tmp_path / "b.txt").write_text("hit")

    result = grep_inhouse(pattern="hit", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert len(_matched_lines(result)) == 3
    assert result["truncated"] is False


def test_grep_regex_pattern(tmp_path):
    (tmp_path / "a.py").write_text("def main():\n    return 1\n")

    result = grep_inhouse(
        pattern=r"def\s+\w+", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    matched = _matched_lines(result)
    assert len(matched) == 1
    assert "def main" in matched[0]["line"]


def test_grep_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("nothing here")

    result = grep_inhouse(
        pattern="absent", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert result["error"] == ""


def test_grep_invalid_regex(tmp_path):
    result = grep_inhouse(
        pattern="[unclosed", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "invalid regular expression" in result["error"].lower()


def test_grep_empty_pattern(tmp_path):
    result = grep_inhouse(pattern="", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "empty pattern" in result["error"].lower()


def test_grep_include_filter(tmp_path):
    (tmp_path / "a.py").write_text("needle")
    (tmp_path / "b.md").write_text("needle")

    result = grep_inhouse(
        pattern="needle",
        path=str(tmp_path),
        include="*.py",
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    matched = _matched_lines(result)
    assert len(matched) == 1
    assert result["matches"][0]["path"] == "a.py"


def test_grep_case_sensitive_default(tmp_path):
    (tmp_path / "a.txt").write_text("Needle\nneedle")

    sensitive = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )
    insensitive = grep_inhouse(
        pattern="needle",
        path=str(tmp_path),
        case_sensitive=False,
        project_root=str(tmp_path),
    )

    logger.debug(f"{sensitive['matches'] = }")
    logger.debug(f"{insensitive['matches'] = }")
    assert len(_matched_lines(sensitive)) == 1
    assert len(_matched_lines(insensitive)) == 2


def test_grep_truncates_at_max_results(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join("hit" for _ in range(10)))

    result = grep_inhouse(
        pattern="hit",
        path=str(tmp_path),
        max_results=3,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(_matched_lines(result)) == 3
    assert result["truncated"] is True
    hunk = result["matches"][0]
    assert hunk["end_line"] == 3
    assert all(line["match"] for line in hunk["lines"])


def test_grep_skips_binary_files(tmp_path):
    (tmp_path / "text.txt").write_text("needle")
    (tmp_path / "blob.bin").write_bytes(b"needle\x00binary")

    result = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    matched = _matched_lines(result)
    assert len(matched) == 1
    assert result["matches"][0]["path"] == "text.txt"


def test_grep_skips_skip_dirs(tmp_path):
    (tmp_path / "keep.txt").write_text("needle")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("needle")

    result = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    matched = _matched_lines(result)
    assert len(matched) == 1
    assert result["matches"][0]["path"] == "keep.txt"


def test_grep_skips_large_files(tmp_path):
    (tmp_path / "small.txt").write_text("needle")
    (tmp_path / "big.txt").write_text("needle" + "x" * 100)

    result = grep_inhouse(
        pattern="needle",
        path=str(tmp_path),
        max_file_bytes=50,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    matched = _matched_lines(result)
    assert len(matched) == 1
    assert result["matches"][0]["path"] == "small.txt"


def test_grep_max_chars_truncates_early(tmp_path):
    (tmp_path / "a.txt").write_text("needle\nneedle\nneedle")

    result = grep_inhouse(
        pattern="needle",
        path=str(tmp_path),
        max_chars=6,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(_matched_lines(result)) == 1
    assert result["truncated"] is True


def test_grep_not_a_directory(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")

    result = grep_inhouse(pattern="x", path=str(a_file), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "not a directory" in result["error"].lower()


# ---------------------------------------------------------------------------
# Context hunks
# ---------------------------------------------------------------------------


def test_grep_default_context_lines(tmp_path):
    (tmp_path / "a.py").write_text("l1\nl2\nl3\nMATCH\nl5\nl6\nl7\n")

    result = grep_inhouse(
        pattern="MATCH", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 1
    hunk = result["matches"][0]
    assert hunk["start_line"] == 2
    assert hunk["end_line"] == 6
    assert [line["line_number"] for line in hunk["lines"]] == [2, 3, 4, 5, 6]
    assert [line["line"] for line in hunk["lines"] if line["match"]] == ["MATCH"]


def test_grep_context_clamped_at_file_boundaries(tmp_path):
    (tmp_path / "start.txt").write_text("MATCH\na\nb\n")
    (tmp_path / "end.txt").write_text("a\nb\nc\nMATCH\n")

    result = grep_inhouse(
        pattern="MATCH", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    by_path = {hunk["path"]: hunk for hunk in result["matches"]}
    assert by_path["start.txt"]["start_line"] == 1
    assert by_path["start.txt"]["end_line"] == 3
    assert by_path["end.txt"]["start_line"] == 2
    assert by_path["end.txt"]["end_line"] == 4


def test_grep_context_lines_zero(tmp_path):
    (tmp_path / "a.txt").write_text("x\nhit\nhit\ny")

    result = grep_inhouse(
        pattern="hit",
        path=str(tmp_path),
        context_lines=0,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 1
    hunk = result["matches"][0]
    assert hunk["start_line"] == 2
    assert hunk["end_line"] == 3
    assert [line["line_number"] for line in hunk["lines"]] == [2, 3]
    assert all(line["match"] for line in hunk["lines"])


def test_grep_overlapping_context_merges(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nhit\nl3\nhit\nl5")

    result = grep_inhouse(pattern="hit", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 1
    hunk = result["matches"][0]
    assert hunk["start_line"] == 1
    assert hunk["end_line"] == 5
    assert [line["line_number"] for line in hunk["lines"] if line["match"]] == [2, 4]


def test_grep_context_lines_clamped_to_max(tmp_path):
    lines = ["x"] * 20
    lines[9] = "MATCH"
    (tmp_path / "a.txt").write_text("\n".join(lines))

    result = grep_inhouse(
        pattern="MATCH",
        path=str(tmp_path),
        context_lines=99,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    hunk = result["matches"][0]
    assert hunk["start_line"] == 3
    assert hunk["end_line"] == 17
    assert len(hunk["lines"]) == 15


def test_grep_context_counts_toward_char_budget(tmp_path):
    (tmp_path / "a.txt").write_text("aa\nbb\nhit\ncc\ndd")

    result = grep_inhouse(
        pattern="hit",
        path=str(tmp_path),
        context_lines=1,
        max_chars=5,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    hunk = result["matches"][0]
    assert hunk["end_line"] == 3
    assert [line["line"] for line in hunk["lines"]] == ["bb", "hit"]
    assert result["truncated"] is True


# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------


async def test_grep_dispatches_to_rg(monkeypatch, tmp_path):
    calls: dict = {}

    async def _fake_rg_grep(rg, pattern, **kwargs):
        calls["rg"] = rg
        calls["pattern"] = pattern
        calls["kwargs"] = kwargs
        return {
            "pattern": pattern,
            "path": kwargs["path"],
            "matches": [{"path": "x.py", "line_number": 1, "line": "hit"}],
            "truncated": False,
            "files_scanned": None,
            "error": "",
        }

    monkeypatch.setattr(grep_module, "resolve_rg", lambda: "/fake/rg")
    monkeypatch.setattr(grep_module, "rg_grep", _fake_rg_grep)

    result = await grep(
        "needle",
        path=str(tmp_path),
        include="*.py",
        include_ignored=True,
        max_results=5,
        project_root=str(tmp_path),
    )

    logger.debug(f"{calls = }")
    assert calls["rg"] == "/fake/rg"
    assert calls["pattern"] == "needle"
    assert calls["kwargs"]["include"] == "*.py"
    assert calls["kwargs"]["include_ignored"] is True
    assert calls["kwargs"]["max_results"] == 5
    assert result["matches"] == [{"path": "x.py", "line_number": 1, "line": "hit"}]


async def test_grep_falls_back_to_inhouse(monkeypatch, tmp_path):
    (tmp_path / "a.txt").write_text("needle")
    monkeypatch.setattr(grep_module, "resolve_rg", lambda: None)

    result = await grep("needle", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["matches"][0]["path"] == "a.txt"
    assert result["files_scanned"] == 1


async def test_grep_inhouse_does_not_honor_gitignore(monkeypatch, tmp_path):
    """Documents the fallback divergence: it searches gitignored files."""
    (tmp_path / ".gitignore").write_text("ignored.txt\n")
    (tmp_path / "ignored.txt").write_text("needle")
    monkeypatch.setattr(grep_module, "resolve_rg", lambda: None)

    result = await grep("needle", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert any(match["path"] == "ignored.txt" for match in result["matches"])
