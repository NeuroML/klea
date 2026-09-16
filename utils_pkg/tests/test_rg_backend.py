#!/usr/bin/env python3
"""
Tests for ripgrep backend resolution.

File: utils_pkg/tests/test_rg_backend.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import importlib.metadata
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
