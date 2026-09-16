#!/usr/bin/env python3
"""
Tests for ripgrep backend resolution.

File: utils_pkg/tests/test_rg_backend.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import importlib.metadata
import json
import logging
from pathlib import Path

import pytest
from klea_utils.mcp.tool_impls import rg_backend

logger = logging.getLogger(__name__)


class _FakeDist:
    """Minimal stand-in for an importlib.metadata Distribution."""

    def __init__(self, files, base, version="15.2.0"):
        self.files = files
        self._base = base
        self.version = version

    def locate_file(self, record):
        return self._base / str(record)


@pytest.fixture(autouse=True)
def _clear_resolver_cache():
    rg_backend.resolve_rg.cache_clear()
    yield
    rg_backend.resolve_rg.cache_clear()


def test_resolve_rg_none_when_distribution_missing(monkeypatch):
    def _raise(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(rg_backend, "distribution", _raise)

    assert rg_backend.resolve_rg() is None


def test_resolve_rg_finds_executable(monkeypatch, tmp_path):
    rg = tmp_path / "rg"
    rg.write_text("#!/bin/sh\n")
    dist = _FakeDist(files=["rg"], base=tmp_path)
    monkeypatch.setattr(rg_backend, "distribution", lambda name: dist)

    resolved = rg_backend.resolve_rg()

    logger.debug(f"{resolved = }")
    assert resolved == str(rg.resolve())
    assert Path(resolved).is_file()


def test_resolve_rg_accepts_windows_name(monkeypatch, tmp_path):
    rg = tmp_path / "rg.exe"
    rg.write_bytes(b"MZ")
    dist = _FakeDist(files=["rg.exe"], base=tmp_path)
    monkeypatch.setattr(rg_backend, "distribution", lambda name: dist)

    assert rg_backend.resolve_rg() == str(rg.resolve())


def test_resolve_rg_resolves_relative_record(monkeypatch, tmp_path):
    """A record using ``..`` (maturin's ``../../../bin/rg``) resolves."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    rg = bin_dir / "rg"
    rg.write_text("#!/bin/sh\n")
    base = tmp_path / "lib" / "site-packages" / "dist.dist-info"
    base.mkdir(parents=True)
    dist = _FakeDist(files=["../../../bin/rg"], base=base)
    monkeypatch.setattr(rg_backend, "distribution", lambda name: dist)

    assert rg_backend.resolve_rg() == str(rg.resolve())


def test_resolve_rg_none_when_no_executable_record(monkeypatch, tmp_path):
    dist = _FakeDist(files=["ripgrep/__init__.py"], base=tmp_path)
    monkeypatch.setattr(rg_backend, "distribution", lambda name: dist)

    assert rg_backend.resolve_rg() is None


def test_resolve_rg_none_when_record_missing_on_disk(monkeypatch, tmp_path):
    dist = _FakeDist(files=["rg"], base=tmp_path)
    monkeypatch.setattr(rg_backend, "distribution", lambda name: dist)

    assert rg_backend.resolve_rg() is None


def test_resolve_rg_caches_result(monkeypatch, tmp_path):
    rg = tmp_path / "rg"
    rg.write_text("#!/bin/sh\n")
    calls = {"n": 0}

    def _distribution(name):
        calls["n"] += 1
        return _FakeDist(files=["rg"], base=tmp_path)

    monkeypatch.setattr(rg_backend, "distribution", _distribution)

    assert rg_backend.resolve_rg() is not None
    assert rg_backend.resolve_rg() is not None
    assert calls["n"] == 1


def test_resolve_rg_real_distribution_optional():
    """When the [search] extra is installed, the real binary is resolvable."""
    try:
        importlib.metadata.distribution(rg_backend.RG_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("search extra not installed")

    rg_backend.resolve_rg.cache_clear()
    resolved = rg_backend.resolve_rg()

    logger.debug(f"{resolved = }")
    assert resolved is not None
    assert Path(resolved).is_file()


# ---------------------------------------------------------------------------
# rg_grep / rg_files (driven by a fake rg script)
# ---------------------------------------------------------------------------

_FAKE_RG = """#!/usr/bin/env python3
import json
import os
import sys
import time

args = sys.argv[1:]
out = os.environ.get("FAKE_RG_ARGS_OUT")
if out:
    with open(out, "w") as fh:
        json.dump(args, fh)

fixture = os.environ.get("FAKE_RG_FIXTURE")
if not fixture:
    sys.exit(1)
with open(fixture) as fh:
    spec = json.load(fh)
if spec.get("sleep"):
    time.sleep(spec["sleep"])
sys.stderr.write(spec.get("stderr", ""))
for line in spec.get("stdout", []):
    sys.stdout.write(line + "\\n")
sys.exit(spec.get("exit", 0))
"""


def _write_fake_rg(tmp_path: Path) -> Path:
    script = tmp_path / "fake_rg"
    script.write_text(_FAKE_RG)
    script.chmod(0o755)
    return script


def _fixture(tmp_path: Path, **spec) -> Path:
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(spec))
    return path


def _match(path: str, line_number: int, text: str) -> str:
    return json.dumps(
        {
            "type": "match",
            "data": {
                "path": {"text": path},
                "lines": {"text": text + "\n"},
                "line_number": line_number,
            },
        }
    )


async def test_rg_grep_parses_matches(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(
        tmp_path,
        stdout=[_match("./sub/a.py", 3, "needle")],
        exit=0,
    )
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script), "needle", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["truncated"] is False
    assert result["files_scanned"] is None
    assert result["matches"] == [
        {"path": "sub/a.py", "line_number": 3, "line": "needle"}
    ]


async def test_rg_grep_ignores_non_match_records(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(
        tmp_path,
        stdout=[
            json.dumps({"type": "begin", "data": {"path": {"text": "a.py"}}}),
            _match("a.py", 1, "hit"),
            json.dumps({"type": "end", "data": {"path": {"text": "a.py"}}}),
            json.dumps({"type": "summary", "data": {}}),
        ],
        exit=0,
    )
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script), "hit", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["matches"] == [{"path": "a.py", "line_number": 1, "line": "hit"}]


async def test_rg_grep_skips_malformed_records(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, stdout=["not json", "{bad"], exit=0)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script), "hit", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["matches"] == []
    assert result["error"] == ""


async def test_rg_grep_no_matches(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script), "absent", path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["matches"] == []
    assert result["error"] == ""


async def test_rg_grep_invalid_pattern(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, stderr="regex parse error:\n[unclosed", exit=2)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script), "[unclosed", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "regex parse error" in result["error"]


async def test_rg_grep_empty_pattern(tmp_path):
    result = await rg_backend.rg_grep(
        "/nonexistent/rg", "", path=str(tmp_path), project_root=str(tmp_path)
    )
    assert "empty pattern" in result["error"].lower()


async def test_rg_grep_not_a_directory(tmp_path):
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")

    result = await rg_backend.rg_grep(
        "/nonexistent/rg", "x", path=str(a_file), project_root=str(tmp_path)
    )

    assert "not a directory" in result["error"].lower()


async def test_rg_grep_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = await rg_backend.rg_grep(
        "/nonexistent/rg", "x", path=str(outside), project_root=str(root)
    )

    assert "denied" in result["error"].lower()


async def test_rg_grep_truncates_at_max_results(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(
        tmp_path,
        stdout=[_match(f"f{i}.py", i, "hit") for i in range(10)],
        exit=0,
    )
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script),
        "hit",
        path=str(tmp_path),
        max_results=3,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert len(result["matches"]) == 3
    assert result["truncated"] is True


async def test_rg_grep_timeout(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, sleep=5, exit=0)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_grep(
        str(script),
        "hit",
        path=str(tmp_path),
        timeout_seconds=0.3,
        project_root=str(tmp_path),
    )

    logger.debug(f"{result = }")
    assert result["matches"] == []
    assert "timed out" in result["error"]


async def test_rg_grep_builds_expected_args(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    args_out = tmp_path / "args.json"
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_ARGS_OUT", str(args_out))
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    await rg_backend.rg_grep(
        str(script),
        "pat",
        path=str(tmp_path),
        include="*.py *.md",
        case_sensitive=False,
        include_ignored=True,
        project_root=str(tmp_path),
    )

    args = json.loads(args_out.read_text())
    logger.debug(f"{args = }")
    assert "--no-config" in args
    assert "--json" in args
    assert "--hidden" in args
    assert "--no-follow" in args
    assert "--no-messages" in args
    assert "--no-ignore" in args
    assert "-i" in args
    assert "!**/.git/**" in args
    assert "*.py" in args
    assert "*.md" in args
    assert args[-3:] == ["-e", "pat", "."]


async def test_rg_grep_case_sensitive_default(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    args_out = tmp_path / "args.json"
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_ARGS_OUT", str(args_out))
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    await rg_backend.rg_grep(
        str(script), "pat", path=str(tmp_path), project_root=str(tmp_path)
    )

    args = json.loads(args_out.read_text())
    assert "--case-sensitive" in args
    assert "-i" not in args
    assert "--no-ignore" not in args


async def test_rg_files_lists_and_normalizes(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, stdout=["./a.py", "sub/b.py"], exit=0)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_files(
        str(script), pattern="*.py", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert result["files"] == ["a.py", "sub/b.py"]
    assert result["truncated"] is False


async def test_rg_files_args(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    args_out = tmp_path / "args.json"
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_ARGS_OUT", str(args_out))
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    await rg_backend.rg_files(
        str(script),
        pattern="*.py",
        path=str(tmp_path),
        include_ignored=True,
        project_root=str(tmp_path),
    )

    args = json.loads(args_out.read_text())
    logger.debug(f"{args = }")
    assert "--files" in args
    assert "--no-ignore" in args
    assert "*.py" in args
    assert args[-1] == "."


async def test_rg_files_pattern_star_no_glob(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    args_out = tmp_path / "args.json"
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_ARGS_OUT", str(args_out))
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    await rg_backend.rg_files(
        str(script), pattern="*", path=str(tmp_path), project_root=str(tmp_path)
    )

    args = json.loads(args_out.read_text())
    assert "*" not in args
    assert args[-1] == "."


async def test_rg_files_no_matches(monkeypatch, tmp_path):
    script = _write_fake_rg(tmp_path)
    fixture = _fixture(tmp_path, stdout=[], exit=1)
    monkeypatch.setenv("FAKE_RG_FIXTURE", str(fixture))

    result = await rg_backend.rg_files(
        str(script), path=str(tmp_path), project_root=str(tmp_path)
    )

    assert result["files"] == []
    assert result["error"] == ""


async def test_rg_grep_real_binary_optional(tmp_path):
    """End-to-end against the real pinned binary when [search] is installed."""
    rg_backend.resolve_rg.cache_clear()
    rg = rg_backend.resolve_rg()
    if rg is None:
        pytest.skip("search extra not installed")

    (tmp_path / "a.py").write_text("def main():\n    return 1\n")
    (tmp_path / "b.md").write_text("no match here\n")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("def main\n")

    result = await rg_backend.rg_grep(
        rg, r"def\s+main", path=str(tmp_path), project_root=str(tmp_path)
    )

    logger.debug(f"{result = }")
    assert result["error"] == ""
    assert [m["path"] for m in result["matches"]] == ["a.py"]
