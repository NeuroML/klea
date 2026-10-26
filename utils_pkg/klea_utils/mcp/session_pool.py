"""Per-session client pool for the bundled Klea MCP server.

Each frontend session (web page or TUI) talks to its own bundled MCP server
process.  This module keeps those clients in a bounded, least-recently-used
pool so a long-lived server does not accumulate child processes for sessions
the user has abandoned:

* at most ``max_sessions`` clients are kept (``KLEA_MCP_MAX_SESSIONS``,
  default :data:`DEFAULT_MAX_SESSIONS`); creating one more evicts and closes
  the least recently used client,
* :meth:`SessionMCPClients.sweep_idle` closes clients left unused for
  ``idle_ttl`` seconds (``KLEA_MCP_IDLE_TTL``, default
  :data:`DEFAULT_IDLE_TTL`; ``0`` disables the sweep),
* :meth:`SessionMCPClients.aclose` and :meth:`SessionMCPClients.aclose_all`
  close clients within ``close_timeout`` seconds (``KLEA_MCP_CLOSE_TIMEOUT``,
  default :data:`DEFAULT_CLOSE_TIMEOUT`), so teardown is deterministic even
  when a child ignores its shutdown handshake.

The pool does not own an event loop: every coroutine must run in the loop that
also hosts the clients it created.  Client creation is serialised, so two
concurrent :meth:`~SessionMCPClients.get` calls for the same session cannot
start two server processes.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from klea_utils.client_context import ClientContext, client_context_fingerprint

logger = logging.getLogger(__name__)

#: Default cap on the number of pooled clients.
DEFAULT_MAX_SESSIONS = 16

#: Default idle time (seconds) after which a pooled client is swept.
DEFAULT_IDLE_TTL = 300.0

#: Default timeout (seconds) for closing a single pooled client.
DEFAULT_CLOSE_TIMEOUT = 10.0

#: Environment variable bounding the pool size.
ENV_MAX_SESSIONS = "KLEA_MCP_MAX_SESSIONS"

#: Environment variable setting the idle TTL, in seconds.
ENV_IDLE_TTL = "KLEA_MCP_IDLE_TTL"

#: Environment variable setting the close timeout, in seconds.
ENV_CLOSE_TIMEOUT = "KLEA_MCP_CLOSE_TIMEOUT"

#: Close methods tried on a pooled client, in order.
CLOSE_METHODS: tuple[str, ...] = ("aclose", "close", "stop")

#: Coroutine function creating the client for a session id and context.
ClientFactory = Callable[[str, ClientContext | None], Awaitable[Any]]


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("mcp session pool: ignoring non-integer %s=%r", name, raw)
        return default


def _env_float(name: str, default: float) -> float:
    """Read a float from the environment, falling back to *default*."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("mcp session pool: ignoring non-numeric %s=%r", name, raw)
        return default


def _resolve_max_sessions(value: int | None) -> int:
    """Resolve the pool cap from *value* or the environment."""
    if value is None:
        limit = _env_int(ENV_MAX_SESSIONS, DEFAULT_MAX_SESSIONS)
    else:
        limit = int(value)
    if limit < 1:
        raise ValueError(f"max_sessions must be >= 1, got {limit}")
    return limit


def _resolve_idle_ttl(value: float | None) -> float:
    """Resolve the idle TTL (seconds) from *value* or the environment."""
    if value is None:
        ttl = _env_float(ENV_IDLE_TTL, DEFAULT_IDLE_TTL)
    else:
        ttl = float(value)
    if ttl < 0:
        raise ValueError(f"idle_ttl must be >= 0, got {ttl}")
    return ttl


def _resolve_close_timeout(value: float | None) -> float:
    """Resolve the close timeout (seconds) from *value* or the environment."""
    if value is None:
        timeout = _env_float(ENV_CLOSE_TIMEOUT, DEFAULT_CLOSE_TIMEOUT)
    else:
        timeout = float(value)
    if timeout <= 0:
        raise ValueError(f"close_timeout must be > 0, got {timeout}")
    return timeout


def _diagnostics(client: Any) -> dict[str, Any]:
    """Return the identifying details logged for a pooled client.

    The values are read from the client when it exposes them (falling back to
    its process object for the pid), so a client that does not track a
    lockfile or a start time still logs cleanly.
    """
    process = getattr(client, "process", None)
    return {
        "root": getattr(client, "root", None),
        "lockfile": getattr(client, "lockfile", None),
        "pid": getattr(client, "pid", None) or getattr(process, "pid", None),
        "starttime": getattr(client, "starttime", None),
    }


def _context_diagnostics(context: ClientContext | None) -> dict[str, Any] | None:
    """Return non-secret client-context details for logging.

    Environment values are never included: only the number of variables and
    the context fingerprint, so logs cannot leak secrets.
    """
    if context is None:
        return None
    return {
        "cwd": context.cwd,
        "interpreter": context.interpreter,
        "env_keys": len(context.env),
        "fingerprint": client_context_fingerprint(context)[:12],
    }


async def _close_client(client: Any) -> None:
    """Close *client* with the first close method it exposes."""
    for name in CLOSE_METHODS:
        closer = getattr(client, name, None)
        if closer is None:
            continue
        result = closer()
        if inspect.isawaitable(result):
            await result
        return
    logger.debug("mcp session pool: client %r exposes no close method", client)


@dataclass
class _Entry:
    """A pooled client plus the bookkeeping the LRU and the TTL need."""

    session_id: str
    client: Any
    created_at: float
    last_used: float
    client_context: ClientContext | None = None
    context_fingerprint: str = ""


class SessionMCPClients:
    """A bounded LRU pool of per-session bundled MCP server clients.

    Clients are created on first use, reused afterwards, and closed when they
    are evicted, closed explicitly, swept for idleness, or when the pool is
    shut down.  A session's client is also recreated when the client-supplied
    context (cwd/env/interpreter) changes, so a tool server follows the
    frontend that is currently driving the chat.
    """

    def __init__(
        self,
        client_factory: ClientFactory,
        *,
        max_sessions: int | None = None,
        idle_ttl: float | None = None,
        close_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure a pool.

        Args:
            client_factory: Coroutine function creating the client for a
                session id and its optional :class:`ClientContext`.  It is
                called only when no pooled client exists or the context
                changed.
            max_sessions: Cap on pooled clients.  Defaults to
                ``KLEA_MCP_MAX_SESSIONS``, else :data:`DEFAULT_MAX_SESSIONS`.
            idle_ttl: Seconds a client may go unused before
                :meth:`sweep_idle` closes it.  Defaults to
                ``KLEA_MCP_IDLE_TTL``, else :data:`DEFAULT_IDLE_TTL`; ``0``
                disables sweeping.
            close_timeout: Seconds allowed for closing one client.  Defaults
                to ``KLEA_MCP_CLOSE_TIMEOUT``, else
                :data:`DEFAULT_CLOSE_TIMEOUT`.
            clock: Monotonic time source, injectable for tests.

        Raises:
            ValueError: If the resolved limits are out of range.
        """
        self._factory = client_factory
        self._max_sessions = _resolve_max_sessions(max_sessions)
        self._idle_ttl = _resolve_idle_ttl(idle_ttl)
        self._close_timeout = _resolve_close_timeout(close_timeout)
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    # -- introspection ----------------------------------------------------

    def __len__(self) -> int:
        """Return the number of pooled clients."""
        return len(self._entries)

    def __contains__(self, session_id: object) -> bool:
        """Return whether *session_id* has a pooled client."""
        return session_id in self._entries

    def session_ids(self) -> list[str]:
        """Return the pooled session ids, least recently used first."""
        return list(self._entries)

    @property
    def max_sessions(self) -> int:
        """The maximum number of clients kept in the pool."""
        return self._max_sessions

    @property
    def idle_ttl(self) -> float:
        """The idle time (seconds) after which a client is swept."""
        return self._idle_ttl

    @property
    def close_timeout(self) -> float:
        """The timeout (seconds) applied when closing a client."""
        return self._close_timeout

    # -- lookup -----------------------------------------------------------

    async def get(
        self, session_id: str, client_context: ClientContext | None = None
    ) -> Any:
        """Return the client for *session_id*, creating it on first use.

        Idle clients are swept before the lookup.  A hit is marked the most
        recently used one; a miss creates the client, which may evict (and
        close) the least recently used client before returning.  When the
        supplied *client_context* differs from the one a pooled client was
        created with, the old client is closed and a new one is created so a
        chat's tool server follows the current frontend.

        Args:
            session_id: The session whose client is wanted.
            client_context: Optional client-supplied environment (cwd,
                interpreter, env) for the bundled server.

        Returns:
            The client for *session_id*.

        Raises:
            Exception: Whatever *client_factory* raises; nothing is pooled
                when creation fails.
        """
        fingerprint = client_context_fingerprint(client_context)
        async with self._lock:
            await self._sweep_idle_locked()
            now = self._clock()
            entry = self._entries.get(session_id)
            if entry is not None:
                if entry.context_fingerprint == fingerprint:
                    entry.last_used = now
                    self._entries.move_to_end(session_id)
                    logger.debug(
                        "mcp session pool: reuse session_id=%s %s %s",
                        session_id,
                        _diagnostics(entry.client),
                        _context_diagnostics(entry.client_context),
                    )
                    return entry.client
                self._entries.pop(session_id, None)
                await self._close_entry(entry, reason="context change")

            client = await self._factory(session_id, client_context)
            entry = _Entry(
                session_id=session_id,
                client=client,
                created_at=now,
                last_used=now,
                client_context=client_context,
                context_fingerprint=fingerprint,
            )
            self._entries[session_id] = entry
            logger.info(
                "mcp session pool: create session_id=%s %s %s",
                session_id,
                _diagnostics(client),
                _context_diagnostics(client_context),
            )
            await self._evict_overflow_locked()
            return client

    # -- teardown ---------------------------------------------------------

    async def sweep_idle(self) -> list[str]:
        """Close the clients that have been idle for longer than the TTL.

        Returns:
            The ids of the closed sessions, least recently used first.
        """
        async with self._lock:
            return await self._sweep_idle_locked()

    async def aclose(self, session_id: str) -> bool:
        """Close and forget the client for *session_id*.

        Args:
            session_id: The session whose client should be closed.

        Returns:
            ``True`` when a pooled client was closed, ``False`` when the
            session was not pooled.
        """
        async with self._lock:
            entry = self._entries.pop(session_id, None)
        if entry is None:
            logger.debug(
                "mcp session pool: aclose: session_id=%s is not pooled",
                session_id,
            )
            return False
        await self._close_entry(entry, reason="aclose")
        return True

    async def aclose_all(self) -> int:
        """Close every pooled client, returning the number closed.

        Closing is best effort and bounded by ``close_timeout`` per client, so
        this is safe to call from a shutdown hook.
        """
        async with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        if not entries:
            return 0
        await asyncio.gather(
            *(self._close_entry(entry, reason="aclose_all") for entry in entries)
        )
        return len(entries)

    # -- internals --------------------------------------------------------

    async def _sweep_idle_locked(self) -> list[str]:
        """Sweep idle entries; the caller must hold :attr:`_lock`."""
        if self._idle_ttl <= 0:
            return []
        cutoff = self._clock() - self._idle_ttl
        expired = [
            entry for entry in self._entries.values() if entry.last_used <= cutoff
        ]
        for entry in expired:
            del self._entries[entry.session_id]
        for entry in expired:
            await self._close_entry(entry, reason="idle sweep")
        return [entry.session_id for entry in expired]

    async def _evict_overflow_locked(self) -> list[str]:
        """Evict the least recently used entries beyond the cap."""
        evicted: list[_Entry] = []
        while len(self._entries) > self._max_sessions:
            _, entry = self._entries.popitem(last=False)
            evicted.append(entry)
        for entry in evicted:
            await self._close_entry(entry, reason="lru eviction")
        return [entry.session_id for entry in evicted]

    async def _close_entry(self, entry: _Entry, *, reason: str) -> None:
        """Close *entry*'s client within ``close_timeout``, logging the result."""
        diagnostics = _diagnostics(entry.client)
        context = _context_diagnostics(entry.client_context)
        try:
            await asyncio.wait_for(
                _close_client(entry.client),
                timeout=self._close_timeout,
            )
        except TimeoutError:
            logger.warning(
                "mcp session pool: %s: close timed out after %ss session_id=%s %s %s",
                reason,
                self._close_timeout,
                entry.session_id,
                diagnostics,
                context,
            )
        except Exception:
            # Teardown is best effort: one broken client must not stop the rest.
            logger.exception(
                "mcp session pool: %s: close failed session_id=%s %s %s",
                reason,
                entry.session_id,
                diagnostics,
                context,
            )
        else:
            logger.info(
                "mcp session pool: %s: closed session_id=%s %s %s",
                reason,
                entry.session_id,
                diagnostics,
                context,
            )
