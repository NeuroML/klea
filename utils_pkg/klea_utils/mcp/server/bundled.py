#!/usr/bin/env python3
"""
Bundled tools server for Klea.

Provides the common Klea tools (web fetch, file list/read, download) as an
MCP server.  Apps auto-launch this module over stdio (see
``BaseLangGraph._bundled_server_config``) so users get the common tools with
no extra setup; the same server can also be run standalone over HTTP via the
``klea-mcp`` CLI for remote deployments.

Tool implementations live in ``klea_utils.mcp.tool_impls`` and the FastMCP
wrappers in ``klea_utils.mcp.server.bundled_tools``; this module wires them
onto a FastMCP server.

File: klea_utils/mcp/server/bundled.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import typer
from fastmcp import FastMCP

from klea_utils.mcp.lifespan import make_http_session_lifespan
from klea_utils.mcp.registry import register_tools
from klea_utils.mcp.server import bundled_tools

logger = logging.getLogger(__name__)

#: The bundled FastMCP server instance.  Apps embed this module as a stdio
#: subprocess (``python -m klea_utils.mcp.server.bundled``); tests and the
#: ``klea-mcp`` CLI use it directly.
bundle_server = FastMCP(
    "KleaBundled",
    instructions=("Built-in tools for file operations, web fetching, and downloads."),
    lifespan=make_http_session_lifespan(),
)

register_tools(bundle_server, [bundled_tools])

app = typer.Typer()


@app.command()
def main(
    transport: str = typer.Option(
        "stdio", help="Transport to run on: 'stdio' (default) or 'http'"
    ),
    port: int = typer.Option(8000, help="Port to serve on when using 'http'"),
) -> None:
    """Run the bundled tools server.

    Accessed via the ``klea-mcp`` entry point.  ``--transport http`` serves
    the same pre-registered tools over HTTP so a remote client (e.g. a RAG
    deployment that runs Klea and the bundled server on different hosts) can
    point its ``mcp_servers`` config at this server's URL.
    """
    # Guard: give a clear install hint when the [mcp] extra is missing.
    # Lazy: require_extra uses only find_spec (stdlib) so ``klea-mcp --help``
    # stays fast. Keep this before any heavy work so ``klea-mcp`` (stdio/http)
    # without the extra fails fast with guidance.
    try:
        # Lazy: require_extra uses only find_spec (stdlib).
        from klea_utils.imports import require_extra

        require_extra(["bs4", "anydoc"], "mcp")
    except ImportError as exc:
        msg = str(exc)
        logger.error(msg)
        typer.echo(msg, err=True)
        raise typer.Exit(code=1) from None
    # Lazy: only the stdio/http run path needs the run machinery; keeping
    # this body thin means ``klea-mcp --help`` does not force the server to
    # start.
    if transport == "http":
        bundle_server.run(transport="http", port=port)
    else:
        bundle_server.run(transport="stdio")


if __name__ == "__main__":
    app()
