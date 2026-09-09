#!/usr/bin/env python3
"""
Shared FastAPI app factory for Klea packages.

File: klea_utils/api/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

logger = logging.getLogger(__name__)

from klea_utils.api.sessions_db import SessionStore
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

        graph = graph_factory()
        await graph.setup()
        app.state.graph = graph
        logger.info("Graph setup complete (%s)", type(graph).__name__)

        db_path = init_dir(graph.paths.user_data_dir) / "sessions.db"
        app.state.chat_sessions = SessionStore(str(db_path))
        logger.debug("Session store ready at %s", db_path)

        app.state.is_ready = True
        logger.info("Klea API ready")

        yield

        app.state.is_ready = False
        logger.info("Klea API shutting down")
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
