#!/usr/bin/env python3
"""
Tests for the file-name search implementation.

File: utils_pkg/tests/test_find_files.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from klea_utils.mcp.tool_impls import find_files as find_files_module
from klea_utils.mcp.tool_impls.find_files import find_files, find_files_inhouse

logger = logging.getLogger(__name__)


def test_find_files_basic(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.md").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("")

    result = find_files_inhouse(
        pattern="*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["truncated"] is False
    assert set(result["files"]) == {"a.py", "sub/c.py"}


def test_find_files_match_all(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("")

    result = find_files_inhouse(path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert set(result["files"]) == {"a.py", "sub/b.txt"}


def test_find_files_excludes_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.py").write_text("")

    result = find_files_inhouse(path=str(tmp_path), project_root=str(tmp_path))

    assert result["files"] == ["a.py"]


def test_find_files_skips_skip_dirs(tmp_path):
    (tmp_path / "keep.py").write_text("")
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("")

    result = find_files_inhouse(
        pattern="*", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["files"] == ["keep.py"]


def test_find_files_truncates(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text("")

    result = find_files_inhouse(
        pattern="*.py",
        path=str(tmp_path),
        max_results=2,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(result["files"]) == 2
    assert result["truncated"] is True


def test_find_files_exact_boundary_not_truncated(tmp_path):
    for i in range(3):
        (tmp_path / f"f{i}.py").write_text("")

    result = find_files_inhouse(
        pattern="*.py",
        path=str(tmp_path),
        max_results=3,
        project_root=str(tmp_path),
    )

    assert len(result["files"]) == 3
    assert result["truncated"] is False


def test_find_files_path_relative_pattern(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.py").write_text("")
    (tmp_path / "b.py").write_text("")

    result = find_files_inhouse(
        pattern="sub/*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["files"] == ["sub/a.py"]


def test_find_files_no_match(tmp_path):
    (tmp_path / "a.txt").write_text("")

    result = find_files_inhouse(
        pattern="*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["files"] == []
    assert result["error"] == ""


def test_find_files_not_a_directory(tmp_path):
    a_file = tmp_path / "a.txt"
    a_file.write_text("")

    result = find_files_inhouse(path=str(a_file), project_root=str(tmp_path))

    assert result["files"] == []
    assert "not a directory" in result["error"].lower()


def test_find_files_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = find_files_inhouse(path=str(outside), project_root=str(root))

    assert result["files"] == []
    assert "denied" in result["error"].lower()


# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------


async def test_find_files_dispatches_to_rg(monkeypatch, tmp_path):
    calls: dict = {}

    async def _fake_rg_files(rg, **kwargs):
        calls["rg"] = rg
        calls["kwargs"] = kwargs
        return {"files": ["x.py"], "truncated": False, "error": ""}

    monkeypatch.setattr(find_files_module, "resolve_rg", lambda: "/fake/rg")
    monkeypatch.setattr(find_files_module, "rg_files", _fake_rg_files)

    result = await find_files(
        pattern="*.py",
        path=str(tmp_path),
        include_ignored=True,
        max_results=5,
        project_root=str(tmp_path),
    )

    logger.debug(f"{calls = }")
    assert calls["rg"] == "/fake/rg"
    assert calls["kwargs"]["pattern"] == "*.py"
    assert calls["kwargs"]["include_ignored"] is True
    assert calls["kwargs"]["max_results"] == 5
    assert result["files"] == ["x.py"]


async def test_find_files_falls_back_to_inhouse(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("")
    monkeypatch.setattr(find_files_module, "resolve_rg", lambda: None)

    result = await find_files(
        pattern="*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["files"] == ["a.py"]


async def test_find_files_inhouse_does_not_honor_gitignore(monkeypatch, tmp_path):
    """Documents the fallback divergence: it lists gitignored files."""
    (tmp_path / ".gitignore").write_text("ignored.py\n")
    (tmp_path / "ignored.py").write_text("")
    monkeypatch.setattr(find_files_module, "resolve_rg", lambda: None)

    result = await find_files(path=str(tmp_path), project_root=str(tmp_path))

    logger.debug(f"{result = }")
    assert "ignored.py" in result["files"]


async def test_find_files_real_binary_optional(tmp_path):
    """End-to-end against the real pinned binary when [search] is installed."""
    from klea_utils.mcp.tool_impls.rg_backend import resolve_rg

    resolve_rg.cache_clear()
    if resolve_rg() is None:
        pytest.skip("search extra not installed")

    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.md").write_text("")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("")

    result = await find_files(
        pattern="*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["files"] == ["a.py"]
