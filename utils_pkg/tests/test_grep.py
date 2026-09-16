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


def test_grep_finds_matching_lines(tmp_path):
    (tmp_path / "a.txt").write_text("alpha\nneedle here\ngamma")

    result = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert len(result["matches"]) == 1
    assert result["matches"][0]["line_number"] == 2
    assert result["matches"][0]["line"] == "needle here"
    assert result["matches"][0]["path"] == "a.txt"
    assert result["files_scanned"] == 1


def test_grep_multiple_files_and_lines(tmp_path):
    (tmp_path / "a.txt").write_text("hit\nmiss\nhit")
    (tmp_path / "b.txt").write_text("hit")

    result = grep_inhouse(pattern="hit", path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 3
    assert result["truncated"] is False


def test_grep_regex_pattern(tmp_path):
    (tmp_path / "a.py").write_text("def main():\n    return 1\n")

    result = grep_inhouse(
        pattern=r"def\s+\w+", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 1
    assert "def main" in result["matches"][0]["line"]


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
    assert len(result["matches"]) == 1
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
    assert len(sensitive["matches"]) == 1
    assert len(insensitive["matches"]) == 2


def test_grep_truncates_at_max_results(tmp_path):
    (tmp_path / "a.txt").write_text("\n".join("hit" for _ in range(10)))

    result = grep_inhouse(
        pattern="hit",
        path=str(tmp_path),
        max_results=3,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 3
    assert result["truncated"] is True


def test_grep_skips_binary_files(tmp_path):
    (tmp_path / "text.txt").write_text("needle")
    (tmp_path / "blob.bin").write_bytes(b"needle\x00binary")

    result = grep_inhouse(
        pattern="needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 1
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
    assert len(result["matches"]) == 1
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
    assert len(result["matches"]) == 1
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
    assert len(result["matches"]) == 1
    assert result["truncated"] is True


def test_grep_not_a_directory(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")

    result = grep_inhouse(pattern="x", path=str(a_file), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "not a directory" in result["error"].lower()


def test_grep_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.txt").write_text("needle")

    result = grep_inhouse(pattern="needle", path=str(outside), project_root=str(root))

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "denied" in result["error"].lower()


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
