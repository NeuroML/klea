#!/usr/bin/env python3
"""
Read-only sqlite query implementation for Klea MCP tools.

File: klea_utils/mcp/tool_impls/sqlite_query.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access

logger = logging.getLogger(__name__)

#: File extensions accepted as sqlite databases.
SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})

#: Default maximum number of rows returned by :func:`sqlite_query`.
DEFAULT_LIMIT = 50

#: Hard cap on the number of rows returned, whatever the caller asks for.
MAX_LIMIT = 200

#: Default total sqlite VM-instruction budget for a single statement (the
#: ``max_ops`` argument).  Generous so normal queries are never affected;
#: it only bounds runaway or pathological (LLM-generated) SELECTs.
DEFAULT_MAX_OPS = 25_000_000

#: Granularity of the progress-handler ticks: the handler fires roughly
#: every ``PROGRESS_TICK`` virtual-machine instructions.
PROGRESS_TICK = 100_000

#: A query must start with SELECT (optionally after a leading WITH clause),
#: so no statement can be a write, DDL, PRAGMA, or ATTACH.  The read-only
#: connection is still the final gate.
_SELECT_START = re.compile(r"^\s*(?:with\b.*?\b)?select\b", re.IGNORECASE | re.DOTALL)


def sqlite_query(
    db_path: str,
    sql: str,
    limit: int = DEFAULT_LIMIT,
    project_root: str | None = None,
    max_ops: int | None = DEFAULT_MAX_OPS,
) -> dict[str, Any]:
    """Run a read-only SELECT query against a sqlite database.

    Framework-agnostic implementation shared across Klea MCP servers (and
    directly usable by agents).  The database is opened read-only
    (``mode=ro`` plus ``PRAGMA query_only``), so nothing can modify it even
    if validation is bypassed.  Only a single SELECT statement is allowed;
    other statements, statement separators, and unknown tables/columns
    return a clear error rather than raising.

    Use when:

    - Answering a question from structured, tabular data stored in a
      sqlite database (exact values, LIKE patterns, ranges, aggregations).
    - The data is not searchable as free text (that is what the vector/BM25
      stores are for).

    Do not use for:

    - Free-text / semantic search over document collections.
    - Anything that writes; this tool is read-only by construction.

    Example: sqlite_query(db_path="data.db", sql="SELECT name, year FROM
    papers WHERE journal = 'Nature' ORDER BY year DESC")

    Args:
        db_path: Path to the sqlite database file (.sqlite/.sqlite3/.db),
            relative to the project root.
        sql: A single SELECT statement (a trailing semicolon is allowed).
        limit: Maximum number of rows to return (clamped to MAX_LIMIT).
        project_root: Boundary directory the database path must resolve
            inside. Defaults to the current working directory.
        max_ops: Total sqlite virtual-machine instruction budget for the
            statement, bounding how much CPU a query may consume.  ``None``
            disables the budget.  Non-integer or non-positive values fall
            back to the default.

    Returns:
        Dictionary with db_path, columns, rows, row_count, truncated, error.
    """
    logger.debug(f"{db_path = }\n{sql = }\n{limit = }\n{max_ops = }\n{project_root = }")

    result: dict[str, Any] = {
        "db_path": db_path,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "truncated": False,
        "error": "",
    }

    issue = _path_issue(db_path, project_root)
    if issue is not None:
        return {**result, "error": issue}

    if not isinstance(limit, int):
        logger.warning(f"Non-integer limit {limit!r}; using default")
        limit = DEFAULT_LIMIT
    limit = max(1, min(limit, MAX_LIMIT))
    max_ops = _normalize_max_ops(max_ops)
    logger.debug(f"{limit = }\n{max_ops = }")

    try:
        cleaned = _validate_query(sql)
    except ValueError as exc:
        logger.warning(f"Query rejected: {exc}")
        return {**result, "error": str(exc)}
    logger.debug(f"Validated query to execute: {cleaned = }")

    conn = _connect_readonly(Path(db_path), max_ops)
    try:
        cursor = conn.execute(cleaned)
        columns = [desc[0] for desc in (cursor.description or [])]
        data = cursor.fetchmany(limit + 1)
        truncated = len(data) > limit
        rows = [[_cell(value) for value in row] for row in data[:limit]]
        logger.debug(f"{columns = }\n{len(rows) = }\n{truncated = }\n{rows[:5] = }")
        return {
            **result,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if max_ops is not None and any(
            token in msg.lower() for token in ("abort", "cancel", "interrupt")
        ):
            msg = (
                f"{msg} (statement exceeded the max_ops work budget; "
                f"try a more selective query)"
            )
        known = _known_tables(Path(db_path))
        if known and ("no such table" in msg or "no such column" in msg):
            msg = (
                f"{msg}. Known tables: {', '.join(known)}. "
                f"Use the sqlite_schema tool to list their columns."
            )
        logger.warning(f"Query failed: {msg}")
        return {**result, "error": msg}
    except sqlite3.DatabaseError as exc:
        logger.warning(f"Could not query database: {exc}")
        return {**result, "error": f"Could not query database: {exc}"}
    finally:
        conn.close()


def sqlite_schema(
    db_path: str,
    project_root: str | None = None,
    max_ops: int | None = DEFAULT_MAX_OPS,
) -> dict[str, Any]:
    """Inspect the tables and columns of a sqlite database.

    Framework-agnostic implementation shared across Klea MCP servers (and
    directly usable by agents).  Returns the list of tables/views and, for
    each, its columns with their declared types, so a caller can write a
    valid query.  Intended for agent-style callers that can chain an
    inspection step before querying; a RAG wrapper of :func:`sqlite_query`
    usually embeds the schema in the tool description instead, since the
    RAG runs tools in a single pass and cannot take a separate look-up step.

    Use when:

    - You need to know what tables and columns a database has before
      writing a query.

    Do not use for:

    - Querying data (use sqlite_query instead).

    Example: sqlite_schema(db_path="data.db")

    Args:
        db_path: Path to the sqlite database file (.sqlite/.sqlite3/.db),
            relative to the project root.
        project_root: Boundary directory the database path must resolve
            inside. Defaults to the current working directory.
        max_ops: Total sqlite virtual-machine instruction budget for the
            introspection statements.  ``None`` disables the budget.

    Returns:
        Dictionary with db_path, tables, error.  ``tables`` maps each
        table/view name to ``{"columns": [{"name": ..., "type": ...}, ...]}``.
    """
    logger.debug(f"{db_path = }\n{project_root = }\n{max_ops = }")

    result: dict[str, Any] = {"db_path": db_path, "tables": {}, "error": ""}

    issue = _path_issue(db_path, project_root)
    if issue is not None:
        return {**result, "error": issue}

    max_ops = _normalize_max_ops(max_ops)

    conn = _connect_readonly(Path(db_path), max_ops)
    try:
        names = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type IN ('table', 'view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        tables: dict[str, Any] = {}
        for (name,) in names:
            quoted = name.replace('"', '""')
            columns = conn.execute(f'PRAGMA table_info("{quoted}")').fetchall()
            tables[name] = {
                "columns": [
                    {"name": column[1], "type": column[2]} for column in columns
                ]
            }
        table_column_counts = {
            name: len(info["columns"]) for name, info in tables.items()
        }
        logger.debug(f"{len(tables) = }\n{table_column_counts = }")
        return {**result, "tables": tables}
    except sqlite3.DatabaseError as exc:
        logger.warning(f"Could not inspect database: {exc}")
        return {**result, "error": f"Could not inspect database: {exc}"}
    finally:
        conn.close()


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


def _path_issue(db_path: str, project_root: str | None) -> str | None:
    """Return an error message when *db_path* is unusable, else ``None``.

    Checks the permission boundary, that the file exists, and that its
    extension is a known sqlite suffix.

    :param db_path: Database path from the caller
    :param project_root: Permission boundary, or ``None`` for cwd
    :returns: Error message, or ``None`` when the path is usable
    """
    the_path = Path(db_path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for sqlite db: {db_path}")
        return str(exc)
    if not the_path.is_file():
        logger.warning(f"Not a sqlite database file: {db_path}")
        return f"Not a sqlite database file: {db_path}"
    suffix = the_path.suffix.lower()
    if suffix not in SQLITE_SUFFIXES:
        logger.warning(f"Unsupported sqlite extension {suffix!r} for {db_path}")
        return (
            f"Unsupported file extension '{the_path.suffix}'; expected one "
            f"of {', '.join(sorted(SQLITE_SUFFIXES))}"
        )
    logger.debug(f"Database path OK: {the_path = }\n{suffix = }")
    return None


def _find_code_semicolon(sql: str) -> bool:
    """Return True when *sql* has a semicolon outside strings and comments.

    The single-statement check must not trip on harmless semicolons inside
    quoted strings (``'a;b'``), quoted identifiers (``"a;b"``), or ``--`` /
    ``/* */`` comments, which are all perfectly valid inside a SELECT.  A
    small state machine tracks the current lexical context so only a true
    *statement* separator is detected.

    :param sql: Statement text (a trailing separator is already stripped)
    :returns: True when a code-level statement separator is present
    """
    i, n = 0, len(sql)
    mode = "code"  # code | single | double | line | block
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if mode == "single":
            if ch == "'":
                if nxt == "'":  # escaped quote ("''")
                    i += 2
                    continue
                mode = "code"
            i += 1
        elif mode == "double":
            if ch == '"':
                if nxt == '"':  # escaped quote ("""")
                    i += 2
                    continue
                mode = "code"
            i += 1
        elif mode == "line":
            if ch == "\n":
                mode = "code"
            i += 1
        elif mode == "block":
            if ch == "*" and nxt == "/":
                mode = "code"
                i += 2
                continue
            i += 1
        else:  # code
            if ch == "'":
                mode = "single"
            elif ch == '"':
                mode = "double"
            elif ch == "-" and nxt == "-":
                mode = "line"
                i += 1
            elif ch == "/" and nxt == "*":
                mode = "block"
                i += 1
            elif ch == ";":
                return True
            i += 1
    return False


def _validate_query(sql: str) -> str:
    """Validate and clean *sql*, returning the statement to execute.

    The statement must be a single SELECT (optionally with a leading WITH
    clause).  A single trailing semicolon is stripped; any *code-level*
    semicolon (one outside quoted strings or comments) means more than one
    statement and is rejected.  Anything that is not a SELECT
    (INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH/...) is rejected -- the
    read-only connection is the final safety net.

    :param sql: Raw query from the caller
    :returns: Cleaned single-statement SELECT text
    :raises ValueError: When the query is empty, not a single SELECT, or
        contains an embedded statement separator
    """
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("Empty query")
    cleaned = sql.strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].rstrip()
    if _find_code_semicolon(cleaned):
        raise ValueError("Only a single statement is allowed (no ';' inside the query)")
    if not _SELECT_START.match(cleaned):
        raise ValueError("Only SELECT queries are allowed (this is a read-only tool)")
    return cleaned


def _normalize_max_ops(max_ops: Any) -> int | None:
    """Return a valid ``max_ops`` value, falling back to the default.

    ``None`` means unbounded (no work budget installed).  Non-integer or
    non-positive values (including ``bool``) fall back to
    :data:`DEFAULT_MAX_OPS` with a warning, mirroring how ``limit`` is
    treated.

    :param max_ops: Raw value from the caller
    :returns: The work budget to enforce, or ``None`` for unbounded
    """
    if max_ops is None:
        return None
    if not isinstance(max_ops, int) or isinstance(max_ops, bool) or max_ops < 1:
        logger.warning(f"Invalid max_ops {max_ops!r}; using default {DEFAULT_MAX_OPS}")
        return DEFAULT_MAX_OPS
    logger.debug(f"Using max_ops budget {max_ops = }")
    return max_ops


def _install_work_budget(conn: sqlite3.Connection, max_ops: int) -> None:
    """Abort statements that exceed a total VM-instruction budget.

    sqlite's progress handler fires roughly every :data:`PROGRESS_TICK`
    virtual-machine instructions; this accumulates the ticked amount and
    requests an abort once it exceeds *max_ops*, bounding how much CPU a
    single (possibly pathological, LLM-generated) SELECT may consume.  The
    abort surfaces to the caller as a sqlite error, which the public
    functions convert into a friendly message.

    :param conn: Open read-only connection
    :param max_ops: Total VM-instruction budget for a statement
    """
    spent = 0

    def _progress() -> int:
        nonlocal spent
        spent += PROGRESS_TICK
        return spent >= max_ops

    logger.debug(f"Installing work budget: {max_ops = }\n{PROGRESS_TICK = }\n{conn = }")
    conn.set_progress_handler(_progress, PROGRESS_TICK)


def _connect_readonly(the_path: Path, max_ops: int | None = None) -> sqlite3.Connection:
    """Open *the_path* read-only, returning the connection.

    ``mode=ro`` in the URI makes the database read-only at the file level;
    ``PRAGMA query_only`` additionally refuses writes on this connection.
    The resolved absolute path is used so the URI is unambiguous.  When
    *max_ops* is set, a work budget is installed (see
    :func:`_install_work_budget`).

    :param the_path: Resolved database path
    :param max_ops: Optional VM-instruction work budget for statements
    :returns: Open read-only connection
    """
    # Quote the path so '?'/'#' in the filename don't break URI parsing
    quoted = urllib.parse.quote(str(the_path.resolve()), safe="/:")
    conn = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    logger.debug(f"Opened read-only connection: {the_path = }\n{max_ops = }")
    if max_ops is not None:
        _install_work_budget(conn, max_ops)
    return conn


def _known_tables(the_path: Path) -> list[str]:
    """Return the table/view names in *the_path*, or ``[]`` on failure.

    Used to build a helpful message when a query references an unknown
    table or column, so the caller can retry.

    :param the_path: Database path
    :returns: Sorted table/view names
    """
    try:
        conn = _connect_readonly(the_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type IN ('table', 'view') ORDER BY name"
            ).fetchall()
            names = [row[0] for row in rows]
            logger.debug(f"Known tables/views: {names = }")
            return names
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return []


def _cell(value: Any) -> Any:
    """Return a JSON-friendly form of a single sqlite cell.

    Blobs are decoded to text (with replacement characters) so they do not
    break JSON responses; all other sqlite types pass through unchanged.

    :param value: Raw cell value from sqlite
    :returns: Cell value safe for JSON serialization
    """
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
