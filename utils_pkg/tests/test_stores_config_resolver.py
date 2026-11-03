#!/usr/bin/env python3
"""
Tests for the stores-configuration resolver and cached loader.

File: utils_pkg/tests/test_stores_config_resolver.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from klea_utils.stores.utils import (
    _CONFIG_CACHE,
    load_retriever_config,
    resolve_stores_config_path,
)


@pytest.fixture(autouse=True)
def _isolate_state():
    """Give each test an empty loader cache."""
    _CONFIG_CACHE.clear()
    yield
    _CONFIG_CACHE.clear()


def _write_config(path: Path, domain: str = "ExampleDomain") -> Path:
    """Write a minimal, schema-valid stores configuration to *path*."""
    path.write_text(
        f'{{"domains": {{"{domain}": {{"description": "docs for {domain}"}}}}}}'
    )
    return path


class TestResolveStoresConfigPath:
    """The explicit path (as ``str`` or ``Path``) is resolved; nothing
    configured fails closed."""

    def test_explicit_path_is_resolved(self, tmp_path):
        configured = _write_config(tmp_path / "config.json")

        assert resolve_stores_config_path(configured) == configured
        assert resolve_stores_config_path(str(configured)) == configured

    def test_nothing_configured_fails_closed(self, caplog):
        with caplog.at_level(logging.WARNING):
            resolved = resolve_stores_config_path()

        assert resolved is None
        assert any(
            "No stores configuration file" in record.getMessage()
            for record in caplog.records
        )

    def test_blank_configured_path_fails_closed(self):
        with pytest.raises(ValueError, match="klea/stores-config-path/v1"):
            resolve_stores_config_path("   ")


class TestLoadRetrieverConfig:
    """Loading, caching and cache invalidation of the stores file."""

    def test_loads_domains(self, tmp_path):
        config = load_retriever_config(_write_config(tmp_path / "stores.json", "Docs"))

        assert set(config.domains) == {"Docs"}
        assert config.domains["Docs"].description == "docs for Docs"

    def test_unchanged_file_is_served_from_cache(self, tmp_path):
        path = _write_config(tmp_path / "stores.json")

        first = load_retriever_config(path)
        second = load_retriever_config(path)

        assert second is first

    def test_changed_file_invalidates_cache(self, tmp_path):
        path = _write_config(tmp_path / "stores.json", "Before")
        first = load_retriever_config(path)
        assert set(first.domains) == {"Before"}

        # Rewrite with different content and bump the mtime, so both cache
        # keys (size and modification time) change.
        _write_config(path, "AfterWithLongerName")
        stat = path.stat()
        os.utime(path, (stat.st_atime, stat.st_mtime + 1))

        second = load_retriever_config(path)

        assert set(second.domains) == {"AfterWithLongerName"}
        assert second is not first

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_retriever_config(tmp_path / "does-not-exist.json")
