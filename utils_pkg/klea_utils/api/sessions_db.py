#!/usr/bin/env python3
"""
Persistent SQLite-backed store for chat session data.

Manages four tables alongside the LangGraph checkpoint DB:
    - chat_sessions (chat metadata, listing, and per-chat model overrides)
    - session_overrides (per-user default model overrides)
    - user_credentials (per-user provider API credentials)
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
import os
import sqlite3
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Default lifetime for an unused per-user API credential.  Credentials
#: untouched for longer are purged so plaintext keys are not retained
#: indefinitely.
DEFAULT_CREDENTIAL_TTL_SECONDS = 7 * 24 * 60 * 60

#: Env var overriding the credential TTL, in days (``0`` disables expiry).
CREDENTIAL_TTL_ENV_VAR = "KLEA_CREDENTIAL_TTL_DAYS"

#: Minimum gap between opportunistic credential purges.
CREDENTIAL_PURGE_INTERVAL_SECONDS = 60 * 60

#: Minimum gap between ``last_used_at`` bumps for a credential.
CREDENTIAL_TOUCH_INTERVAL_SECONDS = 60 * 60


def resolve_credential_ttl_seconds() -> float:
    """Resolve the credential TTL (seconds) from the environment.

    Reads :data:`CREDENTIAL_TTL_ENV_VAR` (days).  A missing/invalid value
    falls back to :data:`DEFAULT_CREDENTIAL_TTL_SECONDS`; ``0`` or a
    negative value disables expiry.
    """
    raw = os.environ.get(CREDENTIAL_TTL_ENV_VAR, "").strip()
    if not raw:
        return float(DEFAULT_CREDENTIAL_TTL_SECONDS)
    try:
        days = float(raw)
    except ValueError:
        logger.warning(
            "Invalid %s=%r; using default %d days",
            CREDENTIAL_TTL_ENV_VAR,
            raw,
            DEFAULT_CREDENTIAL_TTL_SECONDS // (24 * 60 * 60),
        )
        return float(DEFAULT_CREDENTIAL_TTL_SECONDS)
    if days <= 0:
        return 0.0
    return days * 24 * 60 * 60


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

    -- Per-user API credentials, keyed by provider (plus the endpoint for
    -- custom / explicit-URL models).  Secrets are stored in plaintext,
    -- mirroring the opencode config behaviour; the API never returns them
    -- raw (only a masked suffix).  ``last_used_at`` drives the unused-key
    -- TTL purge (see ``purge_expired_credentials``).
    CREATE TABLE IF NOT EXISTS user_credentials (
        user_id      TEXT NOT NULL,
        provider     TEXT NOT NULL,
        endpoint     TEXT NOT NULL DEFAULT '',
        secret       TEXT NOT NULL,
        created_at   REAL NOT NULL DEFAULT 0,
        last_used_at REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, provider, endpoint)
    );

    CREATE INDEX IF NOT EXISTS idx_user_credentials_last_used
        ON user_credentials(last_used_at);

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

    def __init__(
        self,
        db_path: str | Path,
        credential_ttl_seconds: float | None = None,
    ) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, timeout=5.0
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        # Unused-credential TTL.  ``None`` resolves from the environment
        # (KLEA_CREDENTIAL_TTL_DAYS); <=0 disables expiry.
        self._credential_ttl_seconds = (
            resolve_credential_ttl_seconds()
            if credential_ttl_seconds is None
            else float(credential_ttl_seconds)
        )
        # Timestamp of the last opportunistic purge (0 forces one on first use).
        self._last_credential_purge = 0.0
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
        """Remove all chats, messages, overrides, and credentials for a user."""
        with self._lock:
            self._conn.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
            self._conn.execute(
                "DELETE FROM chat_sessions WHERE user_id = ?", (user_id,)
            )
            self._conn.execute(
                "DELETE FROM session_overrides WHERE user_id = ?", (user_id,)
            )
            self._conn.execute(
                "DELETE FROM user_credentials WHERE user_id = ?", (user_id,)
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

    def all_chat_overrides(self) -> list[dict[str, Any]]:
        """Return every chat that has (parsed) overrides.

        Each entry is ``{user_id, chat_id, overrides}``.  Used by the
        startup migration that moves legacy per-chat ``api_key`` values
        into the provider credential store.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT user_id, chat_id, overrides FROM chat_sessions"
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            overrides = self._json_loads(r["overrides"])
            if overrides:
                result.append(
                    {
                        "user_id": r["user_id"],
                        "chat_id": r["chat_id"],
                        "overrides": overrides,
                    }
                )
        logger.debug("all_chat_overrides(): %d chat(s) with overrides", len(result))
        return result

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
    # API credentials (user_credentials; provider + optional endpoint)
    # ------------------------------------------------------------------

    def _purge_expired_credentials_locked(self) -> int:
        """Delete credentials unused past the TTL (caller holds the lock)."""
        if self._credential_ttl_seconds <= 0:
            return 0
        cutoff = self._now() - self._credential_ttl_seconds
        cur = self._conn.execute(
            "DELETE FROM user_credentials WHERE last_used_at < ?", (cutoff,)
        )
        removed = int(cur.rowcount or 0)
        if removed:
            self._conn.commit()
        return removed

    def _maybe_purge_expired_credentials_locked(self) -> None:
        """Opportunistic TTL purge, at most once per interval."""
        now = self._now()
        if now - self._last_credential_purge < CREDENTIAL_PURGE_INTERVAL_SECONDS:
            return
        self._last_credential_purge = now
        removed = self._purge_expired_credentials_locked()
        if removed:
            logger.info("Purged %d unused credential(s) past TTL", removed)

    def purge_expired_credentials(self) -> int:
        """Delete credentials unused for longer than the configured TTL.

        Called at startup and (opportunistically) on credential access, so
        short-lived processes still clean up without a background task.  A
        non-positive TTL disables expiry.  Returns the number of rows
        removed.
        """
        with self._lock:
            self._last_credential_purge = self._now()
            removed = self._purge_expired_credentials_locked()
        if removed:
            logger.info("Purged %d unused credential(s) past TTL", removed)
        return removed

    def get_credential(
        self, user_id: str, provider: str, endpoint: str = ""
    ) -> str | None:
        """Return the stored secret for a provider(+endpoint), or ``None``."""
        with self._lock:
            self._maybe_purge_expired_credentials_locked()
            row = self._conn.execute(
                "SELECT secret FROM user_credentials "
                "WHERE user_id = ? AND provider = ? AND endpoint = ?",
                (user_id, provider, endpoint or ""),
            ).fetchone()
        secret = row["secret"] if row else None
        logger.debug(
            "get_credential(%s, %s, %s): %s",
            user_id,
            provider,
            endpoint or "",
            "found" if secret else "none",
        )
        return secret

    def list_credentials(self, user_id: str) -> list[dict[str, Any]]:
        """Return credentials for *user_id* with timestamps.

        Each entry is ``{provider, endpoint, secret, created_at,
        last_used_at}``.  Callers must mask ``secret`` before returning it
        to a client.
        """
        with self._lock:
            self._maybe_purge_expired_credentials_locked()
            rows = self._conn.execute(
                "SELECT provider, endpoint, secret, created_at, last_used_at "
                "FROM user_credentials "
                "WHERE user_id = ? ORDER BY provider, endpoint",
                (user_id,),
            ).fetchall()
        result = [dict(r) for r in rows]
        logger.debug("list_credentials(%s): %d", user_id, len(result))
        return result

    def set_credential(
        self, user_id: str, provider: str, endpoint: str, secret: str
    ) -> None:
        """Store (or replace) the secret for a provider(+endpoint).

        Records ``created_at``/``last_used_at`` so the unused-key TTL can
        apply.  The secret is never logged.
        """
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_credentials "
                "(user_id, provider, endpoint, secret, created_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, provider, endpoint) "
                "DO UPDATE SET secret = excluded.secret, "
                "last_used_at = excluded.last_used_at",
                (user_id, provider, endpoint or "", secret, now, now),
            )
            self._conn.commit()
            self._maybe_purge_expired_credentials_locked()
        logger.debug("set_credential(%s, %s, %s)", user_id, provider, endpoint or "")

    def touch_credential(
        self,
        user_id: str,
        provider: str,
        endpoint: str = "",
        min_interval_seconds: float = CREDENTIAL_TOUCH_INTERVAL_SECONDS,
    ) -> None:
        """Mark a stored credential as used now (throttled).

        Bumps ``last_used_at`` so the TTL measures *unused* time.  Writes
        are skipped while the previous bump is younger than
        *min_interval_seconds* to avoid write amplification on hot paths.
        """
        now = self._now()
        with self._lock:
            row = self._conn.execute(
                "SELECT last_used_at FROM user_credentials "
                "WHERE user_id = ? AND provider = ? AND endpoint = ?",
                (user_id, provider, endpoint or ""),
            ).fetchone()
            if row is None:
                return
            if now - float(row["last_used_at"] or 0) < min_interval_seconds:
                return
            self._conn.execute(
                "UPDATE user_credentials SET last_used_at = ? "
                "WHERE user_id = ? AND provider = ? AND endpoint = ?",
                (now, user_id, provider, endpoint or ""),
            )
            self._conn.commit()
            self._maybe_purge_expired_credentials_locked()
        logger.debug("touch_credential(%s, %s, %s)", user_id, provider, endpoint or "")

    def clear_credential(self, user_id: str, provider: str, endpoint: str = "") -> None:
        """Remove the stored secret for a provider(+endpoint)."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM user_credentials "
                "WHERE user_id = ? AND provider = ? AND endpoint = ?",
                (user_id, provider, endpoint or ""),
            )
            self._conn.commit()
        logger.debug("clear_credential(%s, %s, %s)", user_id, provider, endpoint or "")

    def clear_credentials(self, user_id: str) -> None:
        """Remove all stored credentials for *user_id*."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM user_credentials WHERE user_id = ?", (user_id,)
            )
            self._conn.commit()
        logger.debug("clear_credentials(%s)", user_id)

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
