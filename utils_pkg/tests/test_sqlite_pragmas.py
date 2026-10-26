#!/usr/bin/env python3
"""
Tests for the shared SQLite PRAGMA policy (WAL, synchronous, busy timeout).

See ADR-0023 (sqlite checkpointer) and ``klea_utils.graph.base``'s
``SQLITE_PRAGMAS``, which the checkpoint store (``aiosqlite``) and the session
store (``sqlite3``) share.

File: utils_pkg/tests/test_sqlite_pragmas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import sqlite3
import subprocess
import sys

from klea_utils.graph.base import (
    _apply_sqlite_pragmas,
    _apply_sqlite_pragmas_async,
)

# A writer process that applies the same connection policy as the backend
# (WAL + busy timeout + NORMAL synchronous) and commits row by row, so two of
# them running at once genuinely contend for the write lock instead of failing
# with "database is locked".
_WRITER_SCRIPT = """
import sqlite3
import sys

db, tag, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
conn = sqlite3.connect(db, timeout=5.0)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=5000")
conn.execute("CREATE TABLE IF NOT EXISTS writes (tag TEXT, n INTEGER)")
for i in range(count):
    conn.execute("INSERT INTO writes VALUES (?, ?)", (tag, i))
    conn.commit()
conn.close()
"""

# A writer that commits a first batch and then exits abruptly, so the process
# never closes the connection and the write-ahead log is left behind on disk --
# exactly what a crash (SIGKILL, segfault, power loss) does.  ``os._exit`` is
# used instead of SIGKILL so the simulation also works on Windows.
_CRASH_SCRIPT = """
import os
import sqlite3
import sys

db = sys.argv[1]
conn = sqlite3.connect(db, timeout=5.0)
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=5000")
conn.execute("CREATE TABLE IF NOT EXISTS crash (n INTEGER)")
for i in range(1000):
    conn.execute("INSERT INTO crash VALUES (?)", (i,))
    conn.commit()
    if i == 50:
        os._exit(1)
"""


def _wal_path(db_path):
    """Return the ``-wal`` sidecar path for *db_path*."""
    return db_path.with_name(db_path.name + "-wal")


def test_concurrent_writes_succeed(tmp_path):
    """Two processes writing to the same database both succeed.

    With WAL journalling and a busy timeout a concurrent writer waits for the
    lock instead of failing, so neither process loses its rows.
    """
    db_path = tmp_path / "checkpoints.db"

    # Seed the database (and its WAL mode) through the shared pragma helper.
    conn = sqlite3.connect(str(db_path), timeout=5.0)
    _apply_sqlite_pragmas(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS writes (tag TEXT, n INTEGER)")
    conn.commit()
    conn.close()

    count = 200
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WRITER_SCRIPT, str(db_path), tag, str(count)]
        )
        for tag in ("a", "b")
    ]
    results = [
        (proc.wait(timeout=120), proc.stderr.read() if proc.stderr else b"")
        for proc in procs
    ]

    assert [code for code, _ in results] == [0, 0], results
    for _code, stderr in results:
        assert b"database is locked" not in stderr

    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0]
        tags = {row[0] for row in conn.execute("SELECT DISTINCT tag FROM writes")}
    finally:
        conn.close()
    assert total == 2 * count
    assert tags == {"a", "b"}


def test_journal_mode_persists(tmp_path):
    """WAL is a property of the database file, so it survives a reopen."""
    db_path = tmp_path / "checkpoints.db"

    conn = sqlite3.connect(str(db_path))
    _apply_sqlite_pragmas(conn)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.commit()
    conn.close()

    # A fresh connection (no pragma applied yet) already sees WAL.
    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
    finally:
        conn.close()
    assert mode == "wal"

    # The ``aiosqlite`` path used by the checkpoint store reports WAL too.
    async def _probe_mode() -> str:
        import aiosqlite

        aconn = await aiosqlite.connect(str(db_path))
        try:
            await _apply_sqlite_pragmas_async(aconn)
            cursor = await aconn.execute("PRAGMA journal_mode")
            row = await cursor.fetchone()
            return str(row[0]).lower() if row else ""
        finally:
            await aconn.close()

    assert asyncio.run(_probe_mode()) == "wal"


def test_sidecar_cleanup(tmp_path):
    """A truncating WAL checkpoint empties the ``-wal`` sidecar on shutdown."""
    db_path = tmp_path / "checkpoints.db"
    wal_path = _wal_path(db_path)

    conn = sqlite3.connect(str(db_path))
    _apply_sqlite_pragmas(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS t (x INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(100)])
    conn.commit()

    # While connected and holding committed pages, the write-ahead log exists
    # and carries them.
    assert wal_path.exists()
    assert wal_path.stat().st_size > 0

    # This is what the lifespan shutdown runs before closing the connection.
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.commit()
    assert not wal_path.exists() or wal_path.stat().st_size == 0

    conn.close()
    assert not wal_path.exists() or wal_path.stat().st_size == 0

    # The data itself is folded back into the main database file.
    conn = sqlite3.connect(str(db_path))
    try:
        total = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    finally:
        conn.close()
    assert total == 100


def test_crash_recovery(tmp_path):
    """A backend killed mid-write leaves a recoverable database behind."""
    db_path = tmp_path / "checkpoints.db"
    wal_path = _wal_path(db_path)

    proc = subprocess.run(
        [sys.executable, "-c", _CRASH_SCRIPT, str(db_path)],
        check=False,
        capture_output=True,
        timeout=120,
    )
    # The child exited abruptly, so it never closed its connection.
    assert proc.returncode != 0
    assert db_path.exists()
    # The write-ahead log is left behind, which is what makes recovery needed.
    assert wal_path.exists()

    conn = sqlite3.connect(str(db_path))
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0].lower()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM crash").fetchone()[0]
    finally:
        conn.close()

    # WAL replay on the next open recovers the committed batch and leaves a
    # consistent, still-WAL database that the next backend can use.
    assert mode == "wal"
    assert integrity == "ok"
    assert rows >= 51
