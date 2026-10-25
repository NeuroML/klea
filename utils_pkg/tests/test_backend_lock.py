#!/usr/bin/env python3
"""
Tests for the backend lockfile helpers.

See ``devdocs/system/api-sse-sequence.md`` and ADR-0023 (one backend per
storage root).

File: utils_pkg/tests/test_backend_lock.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import os
import subprocess
import sys

import pytest
from klea_utils.backend_lock import (
    _acquire_backend_lock,
    _release_backend_lock,
)


def _dead_pid() -> int:
    """Return the PID of a process that has already exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _write_lockfile(lock_path, pid, starttime="0", **overrides):
    """Write a lockfile payload directly, bypassing the lock helpers."""
    payload = {
        "pid": pid,
        "starttime": starttime,
        "root": str(lock_path.parent),
        "version": "test",
        "host": "127.0.0.1",
        "port": 1,
        "timestamp": "1970-01-01T00:00:00+0000",
    }
    payload.update(overrides)
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_concurrent_start_second_exits(tmp_path):
    """A second backend on the same storage root must refuse to start."""
    lock_path = tmp_path / "backend.lock"
    fd = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8000)
    try:
        assert lock_path.exists()
        with pytest.raises(SystemExit) as excinfo:
            _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8001)
        message = str(excinfo.value)
        assert "Another Klea backend already owns this storage root" in message
        assert str(lock_path) in message
        assert "--data-dir / KLEA_AGENT_HOME" in message
    finally:
        _release_backend_lock(fd, lock_path)


def test_stale_pid_reclaim(tmp_path):
    """A lockfile left by a dead process is reclaimed."""
    lock_path = tmp_path / "backend.lock"
    _write_lockfile(lock_path, _dead_pid(), starttime="12345")
    fd = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8000)
    try:
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["port"] == 8000
    finally:
        _release_backend_lock(fd, lock_path)


def test_pid_reuse_via_start_time(tmp_path):
    """A live PID with a mismatched start time is treated as stale."""
    lock_path = tmp_path / "backend.lock"
    _write_lockfile(lock_path, os.getpid(), starttime="not-the-real-start-time")
    fd = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8000)
    try:
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        # The reclaimed record no longer carries the bogus start time, so the
        # mismatch (PID reuse) was correctly detected.
        assert owner["starttime"] != "not-the-real-start-time"
    finally:
        _release_backend_lock(fd, lock_path)


def test_clean_release(tmp_path):
    """The normal shutdown path removes the lockfile."""
    lock_path = tmp_path / "backend.lock"
    fd = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8000)
    assert lock_path.exists()
    _release_backend_lock(fd, lock_path)
    assert not lock_path.exists()


def test_crash_then_restart(tmp_path):
    """A lockfile left behind by a crashed backend is reclaimed on restart."""
    lock_path = tmp_path / "backend.lock"
    # First backend acquires the lock ...
    fd = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8000)
    # ... then "crashes": the flock dies with the process (fd closed) and the
    # file is left behind, recording a process that is now dead.
    os.close(fd)
    assert lock_path.exists()
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    payload["pid"] = _dead_pid()
    payload["starttime"] = "stale"
    lock_path.write_text(json.dumps(payload), encoding="utf-8")
    # Restart: acquiring again must succeed by reclaiming the stale lockfile.
    fd2 = _acquire_backend_lock(lock_path, tmp_path, "127.0.0.1", 8001)
    try:
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["port"] == 8001
    finally:
        _release_backend_lock(fd2, lock_path)
