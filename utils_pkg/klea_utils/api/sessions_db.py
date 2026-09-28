#!/usr/bin/env python3
"""
Persistent SQLite-backed store for chat session data.

Manages three tables alongside the LangGraph checkpoint DB:
    - chat_sessions (chat metadata, listing, and per-chat model overrides)
    - session_overrides (per-user default model overrides)
    - messages (curated Q&A history for chat display)

There is no separate ``state`` table.  Graph state (plan, goal,
tool_status, ...) is read directly from the latest LangGraph checkpoint
via ``graph.aget_state(thread_id)`` -- the checkpoint DB is the
canonical source and already stores the full deserialised state with no
serialization round-trip.

File: klea_utils/api/sessions_db.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class SessionStore:
    """SQLite-backed persistent store for chat session data.

    All public methods are thread-safe.  The store auto-creates its schema
    on first connection.

    :param db_path: Filesystem path to the SQLite database file.
    """

    _SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        user_id     TEXT NOT NULL,
        chat_id     TEXT NOT NULL,
        title       TEXT NOT NULL DEFAULT '',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        overrides   TEXT NOT NULL DEFAULT '{}',  -- JSON blob: {"rag":{"model":...,},"guard":{...}}
        PRIMARY KEY (user_id, chat_id)
    );

    -- State is NOT stored here.  Read from LangGraph checkpoint
    -- via ``graph.aget_state(thread_id)`` instead.

    -- Per-user default model overrides, applied to every chat below any
    -- per-chat overrides.  Keyed by user alone (no chat).  JSON blob:
    -- {"rag":{"model":...,},"guard":{...}}
    CREATE TABLE IF NOT EXISTS session_overrides (
        user_id     TEXT PRIMARY KEY,
        overrides   TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id     TEXT NOT NULL,
        chat_id     TEXT NOT NULL,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        metadata    TEXT,
        created_at  REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_messages_chat
        ON messages(user_id, chat_id, created_at);

    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        # Improve concurrency and durability for threaded access
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.Error as exc:
            logger.warning(f"Failed to set PRAGMAs: {exc}")
        self._conn.executescript(self._SCHEMA_SQL)
        self._conn.commit()
        logger.debug("SessionStore opened at %s", self._path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def _json_dumps(self, obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False)

    def _json_loads(self, raw: str | None) -> Any:
        if raw is None:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning(
                f"Corrupt JSON in DB, returning empty dict: {exc!r} raw={raw!r}"
            )
            return {}

    # ------------------------------------------------------------------
    # Chat sessions
    # ------------------------------------------------------------------

    def list_chats(self, user_id: str) -> list[dict[str, Any]]:
        """Return all chats for *user_id*, newest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE user_id = ? ORDER BY updated_at DESC",
                (user_id,),
            ).fetchall()
        result = [dict(r) for r in rows]
        logger.debug("list_chats(%s): %d chat(s)", user_id, len(result))
        return result

    def get_chat(self, user_id: str, chat_id: str) -> dict[str, Any] | None:
        """Return a single chat or ``None``."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ).fetchone()
        result = dict(row) if row else None
        logger.debug(
            "get_chat(%s, %s): %s", user_id, chat_id, "found" if result else "not found"
        )
        return result

    def create_chat(self, user_id: str, chat_id: str, title: str = "") -> None:
        """Insert a chat row if it does not already exist."""
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO chat_sessions "
                "(user_id, chat_id, title, created_at, updated_at, overrides) "
                "VALUES (?, ?, ?, ?, ?, '{}')",
                (user_id, chat_id, title, now, now),
            )
            self._conn.commit()
        logger.debug("create_chat(%s, %s, title=%r)", user_id, chat_id, title)

    def delete_chat(self, user_id: str, chat_id: str) -> None:
        """Remove a chat and all its associated data."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM chat_sessions WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            self._conn.execute(
                "DELETE FROM messages WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            )
            self._conn.commit()
        logger.debug("delete_chat(%s, %s)", user_id, chat_id)

    def delete_user_chats(self, user_id: str) -> None:
        """Remove all chats, messages, and default overrides for a user."""
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            self._conn.execute(
                "DELETE FROM chat_sessions WHERE user_id = ?", (user_id,)
            )
            self._conn.execute(
                "DELETE FROM session_overrides WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
        logger.debug("delete_user_chats(%s)", user_id)

    def rename_chat(self, user_id: str, chat_id: str, title: str) -> None:
        """Update the display title of a chat."""
        with self._lock:
            self._conn.execute(
                "UPDATE chat_sessions SET title = ?, updated_at = ? "
                "WHERE user_id = ? AND chat_id = ?",
                (title, self._now(), user_id, chat_id),
            )
            self._conn.commit()
        logger.debug("rename_chat(%s, %s, title=%r)", user_id, chat_id, title)

    def touch_chat(self, user_id: str, chat_id: str) -> None:
        """Bump ``updated_at`` without changing any other field."""
        with self._lock:
            self._conn.execute(
                "UPDATE chat_sessions SET updated_at = ? "
                "WHERE user_id = ? AND chat_id = ?",
                (self._now(), user_id, chat_id),
            )
            self._conn.commit()
        logger.debug("touch_chat(%s, %s)", user_id, chat_id)

    # ------------------------------------------------------------------
    # Model overrides (stored in chat_sessions.overrides JSON blob)
    # ------------------------------------------------------------------

    def get_overrides(self, user_id: str, chat_id: str) -> dict[str, dict[str, Any]]:
        """Return per-role model overrides keyed by role.

        Returns ``{"rag": {"model": "...", "provider": "..."}, ...}``
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM chat_sessions WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ).fetchone()
        result = self._json_loads(row["overrides"] if row else None)
        logger.debug("get_overrides(%s, %s): %d role(s)", user_id, chat_id, len(result))
        return result

    def set_override(
        self,
        user_id: str,
        chat_id: str,
        role: str,
        config: dict[str, Any],
    ) -> None:
        """Set or replace model overrides for a given role."""
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM chat_sessions WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ).fetchone()
            current = self._json_loads(row["overrides"]) if row else {}
            current[role] = config
            self._conn.execute(
                "UPDATE chat_sessions SET overrides = ?, updated_at = ? "
                "WHERE user_id = ? AND chat_id = ?",
                (self._json_dumps(current), self._now(), user_id, chat_id),
            )
            self._conn.commit()
        logger.debug(
            "set_override(%s, %s, role=%s, model=%s)",
            user_id,
            chat_id,
            role,
            config.get("model", "?"),
        )

    def clear_overrides(self, user_id: str, chat_id: str) -> None:
        """Remove all model overrides for a chat."""
        with self._lock:
            self._conn.execute(
                "UPDATE chat_sessions SET overrides = '{}', updated_at = ? "
                "WHERE user_id = ? AND chat_id = ?",
                (self._now(), user_id, chat_id),
            )
            self._conn.commit()
        logger.debug("clear_overrides(%s, %s)", user_id, chat_id)

    def clear_override(self, user_id: str, chat_id: str, role: str) -> None:
        """Remove the model override for a single role in a chat."""
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM chat_sessions WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ).fetchone()
            current = self._json_loads(row["overrides"]) if row else {}
            current.pop(role, None)
            self._conn.execute(
                "UPDATE chat_sessions SET overrides = ?, updated_at = ? "
                "WHERE user_id = ? AND chat_id = ?",
                (self._json_dumps(current), self._now(), user_id, chat_id),
            )
            self._conn.commit()
        logger.debug("clear_override(%s, %s, role=%s)", user_id, chat_id, role)

    # ------------------------------------------------------------------
    # Per-user default model overrides (session_overrides.overrides)
    # ------------------------------------------------------------------

    def get_session_overrides(self, user_id: str) -> dict[str, dict[str, Any]]:
        """Return per-role default model overrides for *user_id*.

        These are the user's "last used" defaults, applied to every chat
        below any per-chat overrides.  Returns
        ``{"rag": {"model": "...", "provider": "..."}, ...}``.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM session_overrides WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        result = self._json_loads(row["overrides"] if row else None)
        logger.debug("get_session_overrides(%s): %d role(s)", user_id, len(result))
        return result

    def set_session_override(
        self,
        user_id: str,
        role: str,
        config: dict[str, Any],
    ) -> None:
        """Set or replace the per-user default override for a given role."""
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM session_overrides WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current = self._json_loads(row["overrides"]) if row else {}
            current[role] = config
            self._conn.execute(
                "INSERT INTO session_overrides (user_id, overrides) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET overrides = excluded.overrides",
                (user_id, self._json_dumps(current)),
            )
            self._conn.commit()
        logger.debug(
            "set_session_override(%s, role=%s, model=%s)",
            user_id,
            role,
            config.get("model", "?"),
        )

    def clear_session_override(self, user_id: str, role: str) -> None:
        """Remove the per-user default override for a single role."""
        with self._lock:
            row = self._conn.execute(
                "SELECT overrides FROM session_overrides WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            current = self._json_loads(row["overrides"]) if row else {}
            current.pop(role, None)
            self._conn.execute(
                "INSERT INTO session_overrides (user_id, overrides) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET overrides = excluded.overrides",
                (user_id, self._json_dumps(current)),
            )
            self._conn.commit()
        logger.debug("clear_session_override(%s, role=%s)", user_id, role)

    def clear_session_overrides(self, user_id: str) -> None:
        """Remove all per-user default overrides for *user_id*."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM session_overrides WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
        logger.debug("clear_session_overrides(%s)", user_id)

    # ------------------------------------------------------------------
    # Messages (curated Q&A for frontend display)
    # ------------------------------------------------------------------

    def get_messages(self, user_id: str, chat_id: str) -> list[dict[str, Any]]:
        """Return all messages for a chat, oldest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, role, content, metadata, created_at "
                "FROM messages "
                "WHERE user_id = ? AND chat_id = ? "
                "ORDER BY created_at ASC",
                (user_id, chat_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            m = dict(r)
            m["metadata"] = self._json_loads(m["metadata"])
            result.append(m)
        logger.debug(
            "get_messages(%s, %s): %d message(s)", user_id, chat_id, len(result)
        )
        return result

    def add_message(
        self,
        user_id: str,
        chat_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Append a single message to a chat's history."""
        meta_raw = self._json_dumps(metadata or {})
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages (user_id, chat_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, chat_id, role, content, meta_raw, now),
            )
            self._conn.commit()
        logger.debug(
            "add_message(%s, %s, role=%s, content_len=%d)",
            user_id,
            chat_id,
            role,
            len(content),
        )

    def add_messages(
        self, user_id: str, chat_id: str, messages: Sequence[dict[str, Any]]
    ) -> None:
        """Append multiple messages atomically.

        Each dict must have ``role`` and ``content`` keys, and may have
        an optional ``metadata`` key.
        """
        now = self._now()
        batch = [
            (
                user_id,
                chat_id,
                m["role"],
                m["content"],
                self._json_dumps(m.get("metadata", {})),
                now,
            )
            for m in messages
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO messages (user_id, chat_id, role, content, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                batch,
            )
            self._conn.commit()
        logger.debug(
            "add_messages(%s, %s): %d message(s)", user_id, chat_id, len(batch)
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
        logger.debug("SessionStore closed (%s)", self._path)
