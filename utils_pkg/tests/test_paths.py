#!/usr/bin/env python3
"""
Tests for the storage-root resolver and artifact-path helpers.

File: utils_pkg/tests/test_paths.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pathlib import Path

from klea_utils.paths import (
    checkpoint_db_path,
    endpoint_file_path,
    lockfile_path,
    log_dir_path,
    resolve_storage_root,
    session_db_path,
)


def test_resolve_storage_root_flag_precedence(monkeypatch, tmp_path):
    """An explicit flag wins over the environment, and is expanded."""
    monkeypatch.setenv("KLEA_AGENT_HOME", str(tmp_path / "envroot"))
    result = resolve_storage_root("~/flagroot")
    text = str(result)
    assert "flagroot" in text
    assert "envroot" not in text
    # ``~`` must have been expanded to the user's home directory.
    assert "~" not in text


def test_resolve_storage_root_env_precedence(monkeypatch, tmp_path):
    """The environment variable is used when no flag is given."""
    monkeypatch.setenv("KLEA_AGENT_HOME", str(tmp_path / "envroot"))
    result = resolve_storage_root()
    assert "envroot" in str(result)


def test_resolve_storage_root_default(monkeypatch):
    """Without a flag or the environment variable a default is returned."""
    monkeypatch.delenv("KLEA_AGENT_HOME", raising=False)
    result = resolve_storage_root()
    assert result is not None
    assert str(result)


def test_checkpoint_db_path(tmp_path):
    """The checkpoint DB lives under the storage root."""
    root = tmp_path / "store"
    root.mkdir()
    result = Path(checkpoint_db_path(root))
    assert root in result.parents


def test_session_db_path(tmp_path):
    """The session DB lives under the storage root."""
    root = tmp_path / "store"
    root.mkdir()
    result = Path(session_db_path(root))
    assert root in result.parents


def test_lockfile_path(tmp_path):
    """The backend lockfile lives under the storage root."""
    root = tmp_path / "store"
    root.mkdir()
    result = Path(lockfile_path(root))
    assert root in result.parents


def test_endpoint_file_path(tmp_path):
    """The endpoint file lives under the storage root."""
    root = tmp_path / "store"
    root.mkdir()
    result = Path(endpoint_file_path(root))
    assert root in result.parents


def test_log_dir_path(tmp_path):
    """The log directory lives under the storage root."""
    root = tmp_path / "store"
    root.mkdir()
    result = Path(log_dir_path(root))
    assert root in result.parents or result == root
