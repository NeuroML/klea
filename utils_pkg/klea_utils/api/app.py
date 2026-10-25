#!/usr/bin/env python3
"""
Shared FastAPI app factory for Klea packages.

See ``devdocs/system/api-sse-sequence.md`` and ``devdocs/system/c4-container.md``;
ADR-0031.

File: klea_utils/api/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)

from klea_utils.api.overrides import migrate_legacy_overrides
from klea_utils.api.runs import ActiveRunRegistry
from klea_utils.api.sessions_db import SessionStore
from klea_utils.backend_lock import (
    _acquire_backend_lock,
    _install_lock_signal_handlers,
    _release_backend_lock,
    _restore_lock_signal_handlers,
)
from klea_utils.graph.base import BaseLangGraph
from klea_utils.paths import init_dir


def make_app(
    graph_factory: Callable[[], BaseLangGraph],
    title: str = "Klea API",
    version: str = "0.1.0",
    routers: list[APIRouter] | None = None,
) -> FastAPI:
    """Create a FastAPI instance with a standard lifespan.

    The lifespan:

    1. Instantiates and sets up the graph via *graph_factory*
    2. Opens a persistent :class:`SessionStore` at
       ``{graph.paths.user_data_dir}/sessions.db`` alongside the
       graph's checkpoints.
    3. Stores the graph and session store on ``app.state``

    :param graph_factory: Callable that returns a configured
        :class:`~klea_utils.graph.base.BaseLangGraph` instance
    :param title: API title (appears in OpenAPI docs)
    :param version: API version (appears in OpenAPI docs)
    :param routers: List of APIRouters to include on the app
    :returns: Configured FastAPI app
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.is_ready = False
        logger.info("Klea API (%s) starting lifespan", title)

        # A spawned server passes its intended process title via the
        # environment (see spawn_server); apply it for ps/top.
        proc_title = os.environ.get("KLEA_PROC_TITLE")
        if proc_title:
            from klea_utils.proctitle import set_process_title

            set_process_title(proc_title)

        graph = graph_factory()
        await graph.setup()
        app.state.graph = graph
        logger.info("Graph setup complete (%s)", type(graph).__name__)

        # Take the per-root backend lock before any shared database is opened,
        # so a second backend fails fast instead of contending for the SQLite
        # files (ADR-0023: one backend per storage root).  The bind address is
        # recorded for owner diagnostics only.
        from klea_utils.paths import lockfile_path

        app.state.backend_lock_path = lockfile_path(graph.paths.user_data_dir)
        app.state.backend_lock = _acquire_backend_lock(
            app.state.backend_lock_path,
            graph.paths.user_data_dir,
            os.environ.get("KLEA_HOST", "127.0.0.1"),
            int(os.environ.get("KLEA_PORT", "0") or 0),
        )

        def _release_lock() -> None:
            """Release the backend lock once; safe to call repeatedly."""
            fd = getattr(app.state, "backend_lock", None)
            if fd is None and getattr(app.state, "backend_lock_released", False):
                return
            app.state.backend_lock = None
            app.state.backend_lock_released = True
            _release_backend_lock(fd, app.state.backend_lock_path)

        # SIGINT (Ctrl-C) and SIGTERM (systemd, docker stop) also release the
        # lock before the process goes away.  The handlers chain to whatever
        # uvicorn installed, so graceful shutdown still runs unchanged.
        app.state.backend_lock_signal_handlers = _install_lock_signal_handlers(
            _release_lock
        )

        # Per-process registry of in-flight runs (single-flight per thread,
        # cancellation).  In-process by design: the server is one uvicorn
        # worker and asyncio tasks cannot be cancelled across processes.
        app.state.active_runs = ActiveRunRegistry()

        db_path = init_dir(graph.paths.user_data_dir) / "sessions.db"
        app.state.chat_sessions = SessionStore(str(db_path))
        # Drop stored API credentials unused past their TTL (short-lived
        # processes still expire keys without a background task).
        app.state.chat_sessions.purge_expired_credentials()
        # Move any legacy inline per-chat api_key into provider credentials.
        migrate_legacy_overrides(app.state.chat_sessions)
        logger.debug("Session store ready at %s", db_path)

        app.state.is_ready = True
        logger.info("Klea API ready")

        yield

        app.state.is_ready = False
        logger.info("Klea API shutting down")

        # Release the per-root backend lock so the next backend can start
        # straight away, and put back the signal handlers we wrapped.  This is
        # the normal path only: a crash or SIGKILL skips it, and the leftover
        # lockfile is then reclaimed as *stale* by _acquire_backend_lock on the
        # next start (its flock is gone with the process, and its PID/start
        # time no longer match a live process).
        _release_lock()
        _restore_lock_signal_handlers(
            getattr(app.state, "backend_lock_signal_handlers", None)
        )

        # Clean up checkpointer and MCP client to avoid fd leaks / DB locks
        try:
            checkpointer = getattr(graph, "checkpointer", None)
            if checkpointer is not None:
                # AsyncSqliteSaver holds an aiosqlite Connection
                conn = getattr(checkpointer, "conn", None) or getattr(
                    graph, "_checkpointer_conn", None
                )
                if conn is not None:
                    try:
                        await conn.close()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Failed to close checkpointer conn: {exc}")
                # Some checkpointer implementations expose aclose
                aclose = getattr(checkpointer, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Failed to aclose checkpointer: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Error during checkpointer cleanup: {exc}")

        try:
            mcp_client = getattr(graph, "mcp_client", None)
            if mcp_client is not None:
                # FastMCP Client may have async close
                closer = getattr(mcp_client, "aclose", None) or getattr(
                    mcp_client, "close", None
                )
                if callable(closer):
                    try:
                        res = closer()
                        if hasattr(res, "__await__"):
                            await res
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"Failed to close MCP client: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Error during MCP client cleanup: {exc}")

        try:
            app.state.chat_sessions.close()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to close SessionStore: {exc}")

    app = FastAPI(lifespan=lifespan, title=title, version=version)

    for router in routers or []:
        app.include_router(router)

    return app
