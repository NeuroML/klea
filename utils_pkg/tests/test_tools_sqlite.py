#!/usr/bin/env python3
"""
Tests for the read-only sqlite query tool implementation.

File: utils_pkg/tests/test_tools_sqlite.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import sqlite3

from klea_utils.mcp.tool_impls.sqlite_query import (
    MAX_LIMIT,
    sqlite_query,
    sqlite_schema,
)


#: Tests use tmp_path databases, which live outside the repo cwd; pass the
#: database's own directory as the permission boundary.
def _query(db, sql, **kwargs):
    return sqlite_query(str(db), sql, project_root=str(db.parent), **kwargs)


def _schema(db, **kwargs):
    return sqlite_schema(str(db), project_root=str(db.parent), **kwargs)


def _make_db(tmp_path, name="data.sqlite3"):
    """Create a small read/write database with papers and tags tables."""
    path = tmp_path / name
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE papers ("
        "id INTEGER PRIMARY KEY, name TEXT, journal TEXT, year INTEGER)"
    )
    conn.execute("CREATE TABLE tags (paper_id INTEGER, tag TEXT)")
    rows = [
        ("Ankur Sinha", "Journal of Neuroscience", 2020),
        ("Padraig Gleeson", "eLife", 2021),
        ("X", "Nature", 2022),
    ]
    for name_author in rows:
        conn.execute(
            "INSERT INTO papers (name, journal, year) VALUES (?, ?, ?)", name_author
        )
    conn.executemany(
        "INSERT INTO tags (paper_id, tag) VALUES (?, ?)",
        [(1, "cortex"), (1, "spikes"), (2, "neuroML")],
    )
    conn.commit()
    conn.close()
    return tmp_path / "data.sqlite3", 3


def test_schema_lists_tables_and_columns(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _schema(db)
    assert out["error"] == ""
    assert set(out["tables"]) == {"papers", "tags"}
    papers_cols = {c["name"] for c in out["tables"]["papers"]["columns"]}
    assert papers_cols == {"id", "name", "journal", "year"}
    year_col = next(
        c for c in out["tables"]["papers"]["columns"] if c["name"] == "year"
    )
    assert year_col["type"].lower() == "integer"


def test_schema_empty_database(tmp_path):
    db = tmp_path / "empty.sqlite3"
    sqlite3.connect(db).close()
    out = _schema(db)
    assert out["error"] == ""
    assert out["tables"] == {}


def test_schema_missing_file_error(tmp_path):
    out = sqlite_schema(str(tmp_path / "nope.sqlite3"), project_root=str(tmp_path))
    assert "Not a sqlite database file" in out["error"]
    assert out["tables"] == {}


def test_query_basic_select_returns_columns_and_rows(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name, year FROM papers ORDER BY year")
    assert out["error"] == ""
    assert out["columns"] == ["name", "year"]
    assert len(out["rows"]) == 3
    assert out["row_count"] == 3
    assert out["rows"][0] == ["Ankur Sinha", 2020]
    assert out["truncated"] is False


def test_query_aggregation(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT journal, COUNT(*) AS n FROM papers GROUP BY journal")
    assert out["error"] == ""
    assert out["row_count"] == 3
    assert {row[1] for row in out["rows"]} == {1}


def test_query_limit_and_truncation(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name FROM papers ORDER BY year", limit=2)
    assert out["error"] == ""
    assert out["row_count"] == 2
    assert out["truncated"] is True


def test_query_limit_is_clamped(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name FROM papers ORDER BY year", limit=10**6)
    assert out["error"] == ""
    assert out["row_count"] == 3
    assert out["row_count"] <= MAX_LIMIT


def test_query_accepts_trailing_semicolon(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name FROM papers;")
    assert out["error"] == ""
    assert out["row_count"] == 3


def test_query_rejects_embedded_semicolon(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name FROM papers; SELECT journal FROM papers")
    assert "single statement" in out["error"]


def test_query_semicolon_in_strings_and_comments_allowed(tmp_path):
    """Semicolons inside strings, quoted identifiers, and comments are fine."""
    db, _ = _make_db(tmp_path)
    for statement in (
        "SELECT 'a;b' AS x",
        'SELECT "a;b" AS x',
        "SELECT 1 /* ; */",
        "SELECT 1 -- ;",
        "SELECT 'it''s; fine'",
    ):
        out = _query(db, statement)
        assert out["error"] == "", statement


def test_query_respects_max_ops_none_disables_budget(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT name FROM papers ORDER BY year", max_ops=None)
    assert out["error"] == ""
    assert out["row_count"] == 3


def test_query_invalid_max_ops_falls_back_to_default(tmp_path):
    db, _ = _make_db(tmp_path)
    for bad in (0, -5, "lots", True):
        out = _query(db, "SELECT name FROM papers ORDER BY year", max_ops=bad)
        assert out["error"] == "", bad


def test_query_pathological_select_aborts_on_small_budget(tmp_path):
    db = tmp_path / "big.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE nums (n INTEGER)")
    conn.executemany("INSERT INTO nums (n) VALUES (?)", [(i,) for i in range(500)])
    conn.commit()
    conn.close()
    # A 500 x 500 cross join far exceeds a tiny VM instruction budget.
    out = _query(db, "SELECT COUNT(*) FROM nums a, nums b", max_ops=1000)
    assert out["error"]
    assert "budget" in out["error"].lower() or "abort" in out["error"].lower()


def test_query_rejects_non_select_statements(tmp_path):
    db, _ = _make_db(tmp_path)
    for statement in (
        "INSERT INTO papers (name) VALUES ('x')",
        "UPDATE papers SET year = 0",
        "DELETE FROM papers",
        "DROP TABLE papers",
        "CREATE TABLE x (a)",
        "ALTER TABLE papers ADD COLUMN b",
        "PRAGMA table_info(papers)",
        "ATTACH DATABASE 'x.db' AS other",
        "VACUUM",
    ):
        out = _query(db, statement)
        assert "Only SELECT" in out["error"], statement


def test_query_rejects_empty(tmp_path):
    db, _ = _make_db(tmp_path)
    assert "Empty query" in _query(db, "").get("error", "")
    assert "Empty query" in _query(db, "   ").get("error", "")


def test_query_with_clause_allowed(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(
        db,
        "WITH recent AS (SELECT name FROM papers WHERE year >= 2021) "
        "SELECT * FROM recent",
    )
    assert out["error"] == ""
    assert out["row_count"] == 2


def test_query_unknown_table_is_helpful(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT * FROM missing_table")
    assert "no such table" in out["error"]
    assert "papers" in out["error"]


def test_query_unknown_column_is_helpful(tmp_path):
    db, _ = _make_db(tmp_path)
    out = _query(db, "SELECT id, nope FROM papers")
    assert "no such column" in out["error"]
    assert "papers" in out["error"]


def test_query_missing_file_error(tmp_path):
    out = sqlite_query(
        str(tmp_path / "nope.db"), "SELECT 1", project_root=str(tmp_path)
    )
    assert "Not a sqlite database file" in out["error"]


def test_query_unsupported_extension_rejected(tmp_path):
    bogus = tmp_path / "data.txt"
    bogus.write_text("not sqlite")
    out = sqlite_query(str(bogus), "SELECT 1", project_root=str(tmp_path))
    assert "Unsupported file extension" in out["error"]


def test_query_does_not_modify_database(tmp_path):
    db, _ = _make_db(tmp_path)
    before = db.read_bytes()
    _query(db, "UPDATE papers SET year = 0")
    _query(db, "INSERT INTO papers (name) VALUES ('x')")
    _query(db, "DELETE FROM papers")
    _query(db, "DROP TABLE papers")
    assert db.read_bytes() == before


def test_query_blob_decoded_to_text(tmp_path):
    db = tmp_path / "blob.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE blobs (id INTEGER PRIMARY KEY, payload BLOB)")
    conn.execute("INSERT INTO blobs (payload) VALUES (?)", (b"hello\xffworld",))
    conn.commit()
    conn.close()
    out = _query(db, "SELECT payload FROM blobs")
    assert out["error"] == ""
    # The invalid \xff byte is replaced, not left as raw bytes.
    assert isinstance(out["rows"][0][0], str)
    assert out["rows"][0][0].startswith("hello")


def test_schema_respects_max_ops_argument(tmp_path):
    db, _ = _make_db(tmp_path)
    assert _schema(db, max_ops=None)["error"] == ""
    assert _schema(db, max_ops=10**9)["error"] == ""
