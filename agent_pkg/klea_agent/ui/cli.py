#!/usr/bin/env python3
"""
Cli for klea_agent.

File: klea_agent/ui/cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import os

import typer
from klea_utils.paths import get_config_dir
from klea_utils.ui.cli import make_client_app
from platformdirs import PlatformDirs

from klea_agent.config import write_config_template

logger = logging.getLogger(__name__)

#: Uvicorn module string for the backend server this client talks to.
SERVER_APP_MODULE = "klea_agent.api.main:app"

#: Default base URL of the local backend server.
DEFAULT_SERVER_URL = "http://127.0.0.1:8006"

#: Environment variable carrying the config file into a spawned server.
CONFIG_ENV_VAR = "KLEA_AGENT_APP_CONFIG_FILE"

#: Config directory searched when validating ``--profile``.
CONFIG_DIR = get_config_dir(PlatformDirs("klea-agent"))

#: Canonical identity key shared by the TUI and web frontends.
IDENTITY_APP_NAME = "klea-agent"

agent_app = make_client_app(
    label="Agent",
    server_url_default=DEFAULT_SERVER_URL,
    app_module=SERVER_APP_MODULE,
    tui_app_name="klea-agent-tui",
    tui_prefix="klea-agent",
    tui_entry="klea_agent.ui.tui.page:run_agent_tui",
    web_app_name="klea-agent-web",
    web_entry="klea_agent.ui.web.app",
    identity_app_name=IDENTITY_APP_NAME,
    config_env_var=CONFIG_ENV_VAR,
    config_dir=CONFIG_DIR,
    template_writer=write_config_template,
)


def _parse_toggle(parser, value: str, flag: str):
    """Validate one toggle value, mapping ``ValueError`` to a CLI error.

    :param parser: One of the ``parse_*`` validators from
        :mod:`klea_utils.mcp.access`.
    :param value: The raw option value.
    :param flag: The option name, used in the error message.
    :returns: The validated value.
    :raises typer.BadParameter: When *value* is not accepted.
    """
    try:
        return parser(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint=flag) from exc


def _validate_server_url(value: str) -> str:
    """Validate a ``--server`` value, mapping ``ValueError`` to a CLI error.

    :param value: The raw option value.
    :returns: The validated server URL.
    :raises typer.BadParameter: When *value* is not an accepted URL.
    """
    # Lazy: the URL validator lives in the shared API helpers.
    from klea_utils.api.utils import validate_url

    try:
        return validate_url(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--server") from exc


@agent_app.command()
def headless(
    query: str = typer.Argument(..., help="Query to run through the agent."),
    server_url: str = typer.Option(
        DEFAULT_SERVER_URL,
        "--server",
        "-s",
        help="KLEA Agent server (URL:port)",
        callback=_validate_server_url,
    ),
    thread_id: str = typer.Option(
        "default_thread",
        "--thread-id",
        help="Chat id the run is checkpointed under.",
    ),
    user_id: str = typer.Option(
        "",
        "--user-id",
        help="Opaque user id. Defaults to KLEA_USER_ID, else a persisted "
        "local UUID (sent only to a loopback server).",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Config profile for a server this command spawns: loads "
        "<name>.json from the current directory or the config dir. Use "
        "'template' to scaffold a new config and exit.",
    ),
    path_check: str = typer.Option(
        "",
        "--path-check",
        help="Path-check mode for a server this command spawns: strict, "
        "declared or off (unset keeps the server's default).",
    ),
    path_check_stance: str = typer.Option(
        "",
        "--path-check-stance",
        help="Stance for detected paths on a spawned server: deny or allow. "
        "Only read when HITL is off; an already-running server keeps its own.",
    ),
    hitl: bool | None = typer.Option(
        None,
        "--hitl/--no-hitl",
        help="Human-in-the-loop for a spawned server. With HITL on the run "
        "stops at the first ask and exits 1; use --no-hitl for unattended "
        "runs (unset keeps the server's default).",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """Run one query non-interactively and print the final answer.

    The query goes to a server through the shared ``/query/stream`` client: a
    local server is spawned when none is running (and stopped again on exit),
    otherwise the given one is used as-is.  The three safety toggles are
    validated here and exported through the environment
    (``KLEA_AGENT_PATH_CHECK``, ``KLEA_AGENT_PATH_CHECK_STANCE``,
    ``KLEA_AGENT_HITL``) before the spawn attempt, so a server this command
    starts inherits them; an already-running or remote server keeps its own
    configuration.  The answer is printed on stdout; diagnostics, an error or
    an interrupt payload go to stderr.
    """
    # Lazy: the validators live in the MCP access layer; importing them here
    # keeps ``headless --help`` light (AGENTS.md CLI conventions).
    from klea_utils.mcp.access import (
        parse_hitl_enabled,
        parse_path_check_mode,
        parse_path_check_stance,
    )
    from klea_utils.plogging import resolve_log_level, setup_root_logger

    if path_check:
        _parse_toggle(parse_path_check_mode, path_check, "--path-check")
        os.environ["KLEA_AGENT_PATH_CHECK"] = path_check
    if path_check_stance:
        _parse_toggle(parse_path_check_stance, path_check_stance, "--path-check-stance")
        os.environ["KLEA_AGENT_PATH_CHECK_STANCE"] = path_check_stance
    if hitl is not None:
        hitl_value = "on" if hitl else "off"
        _parse_toggle(parse_hitl_enabled, hitl_value, "--hitl")
        os.environ["KLEA_AGENT_HITL"] = hitl_value

    setup_root_logger("klea-agent", stderr_level=resolve_log_level(debug))

    # Lazy: the SSE client, the identity seam, the shared spawn helper and the
    # async runner are heavy and only needed once the command actually runs,
    # not for ``headless --help``.
    import asyncio

    import httpx
    from klea_utils.api.sse import stream_events
    from klea_utils.ui.cli import _maybe_spawn_server
    from klea_utils.ui.identity import resolve_user_id

    if debug:
        # Make debug visible to the spawned server subprocess.
        from klea_utils.plogging import enable_debug_logging

        enable_debug_logging()

    resolved_user_id = resolve_user_id(
        explicit=user_id,
        server_url=server_url,
        app_name=IDENTITY_APP_NAME,
    )

    async def _run() -> int:
        answer = ""
        interrupted = None
        failed = None
        async for event in stream_events(
            query,
            thread_id,
            server_url,
            user_id=resolved_user_id,
        ):
            event_type = event.get("type")
            if event_type == "ping":
                # Server heartbeat: keeps the stream warm, nothing to show.
                continue
            if event_type == "complete":
                answer = event.get("message_for_user", "")
            elif event_type == "interrupt":
                # The run paused for human input (ADR-0046).  The checkpoint
                # stays resumable from another client, so report the ask and
                # exit non-zero instead of waiting.
                interrupted = event.get("data", event)
            elif event_type == "error":
                failed = event
            elif debug:
                # Every other event type is diagnostic only, so it is echoed
                # to stderr rather than mixed into the answer on stdout.
                typer.echo(json.dumps(event, default=str), err=True)
        if interrupted is not None:
            typer.echo(json.dumps(interrupted, indent=2, default=str), err=True)
            return 1
        if failed is not None:
            typer.echo(f"Error: {failed.get('message', '')}", err=True)
            if failed.get("resumable"):
                typer.echo("The run is resumable: retry it from a client.", err=True)
            return 1
        typer.echo(answer)
        return 0

    try:
        with _maybe_spawn_server(
            server_url,
            SERVER_APP_MODULE,
            profile=profile,
            config_env_var=CONFIG_ENV_VAR,
            config_dir=CONFIG_DIR,
            template_writer=write_config_template,
        ):
            raise typer.Exit(code=asyncio.run(_run()))
    except httpx.HTTPError as exc:
        # A dropped/timed-out stream or a rejected request (for example HTTP
        # 409 when the chat is awaiting an answer).
        typer.echo(f"Request failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
