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
    unattended: bool = typer.Option(
        False,
        "--unattended",
        help="Unattended mode for a spawned server: implies --path-check off, "
        "--path-check-stance allow and --no-hitl. A contradicting flag is "
        "rejected.",
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
    configuration.  ``--unattended`` is shorthand for the three safety values
    an unattended run needs (``--path-check off``, ``--path-check-stance
    allow`` and ``--no-hitl``); a contradicting flag is rejected.  The answer
    is printed on stdout; diagnostics, an error or an interrupt payload go to
    stderr.
    """
    # Lazy: the validators live in the MCP access layer; importing them here
    # keeps ``headless --help`` light (AGENTS.md CLI conventions).
    from klea_utils.mcp.access import (
        parse_hitl_enabled,
        parse_path_check_mode,
        parse_path_check_stance,
    )
    from klea_utils.plogging import resolve_log_level, setup_root_logger

    if unattended:
        if hitl:
            raise typer.BadParameter(
                "--unattended implies --no-hitl; drop --hitl or --unattended",
                param_hint="--hitl",
            )
        if path_check and path_check != "off":
            raise typer.BadParameter(
                "--unattended implies --path-check off; drop --path-check "
                f"{path_check} or --unattended",
                param_hint="--path-check",
            )
        if path_check_stance and path_check_stance != "allow":
            raise typer.BadParameter(
                "--unattended implies --path-check-stance allow; drop "
                f"--path-check-stance {path_check_stance} or --unattended",
                param_hint="--path-check-stance",
            )
        _parse_toggle(parse_path_check_mode, "off", "--unattended")
        _parse_toggle(parse_path_check_stance, "allow", "--unattended")
        _parse_toggle(parse_hitl_enabled, "off", "--unattended")
        os.environ["KLEA_AGENT_PATH_CHECK"] = "off"
        os.environ["KLEA_AGENT_PATH_CHECK_STANCE"] = "allow"
        os.environ["KLEA_AGENT_HITL"] = "off"
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


#: Default number of sessions ``list-sessions`` prints (``0`` means all).
LIST_SESSIONS_DEFAULT_LIMIT = 20


def _format_epoch(value: float | str | None) -> str:
    """Render a server ``updated_at`` epoch as a local date-time label.

    :param value: The epoch seconds a chat record carries, or ``None``.
    :returns: A ``YYYY-MM-DD HH:MM`` label, or ``-`` when it cannot be read.
    """
    if value is None:
        return "-"
    # Lazy: the datetime module is only needed once a listing is printed.
    from datetime import datetime

    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError, OverflowError):
        return "-"


@agent_app.command("list-sessions")
def list_sessions(
    server_url: str = typer.Option(
        DEFAULT_SERVER_URL,
        "--server",
        "-s",
        help="KLEA Agent server (URL:port)",
        callback=_validate_server_url,
    ),
    user_id: str = typer.Option(
        "",
        "--user-id",
        help="Opaque user id. Defaults to KLEA_USER_ID, else a persisted "
        "local UUID (sent only to a loopback server).",
    ),
    limit: int = typer.Option(
        LIST_SESSIONS_DEFAULT_LIMIT,
        "--limit",
        "-n",
        help="Maximum number of sessions to print, newest first (0 = all).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Print one JSON array instead of the human-readable listing.",
    ),
    no_preview: bool = typer.Option(
        False,
        "--no-preview",
        help="Omit each session's preview of its first query.",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        "-p",
        help="Config profile for a server this command spawns: loads "
        "<name>.json from the current directory or the config dir. Use "
        "'template' to scaffold a new config and exit.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """List the user's chat sessions, newest first.

    The listing comes from the shared ``GET /chat/{user_id}`` client call
    (:func:`klea_utils.ui.client.fetch_chats`), so it shows exactly the
    sessions the ``tui``, ``web`` and ``headless`` clients share.  It is a
    client in the same way: a local server is spawned when none is running
    (and stopped again on exit), otherwise the given one is used as-is.

    The human-readable form prints one block per session, newest first, capped
    at ``--limit``: the chat id, the server's ``updated_at`` rendered as a
    local date-time, and the title on the first line, then the preview of the
    chat's first query indented beneath it.  The preview is the one the server
    already truncates (``CHAT_PREVIEW_MAX_CHARS``), so this command never
    re-truncates it and fetches nothing else; a chat with no user message yet
    has no preview line.  ``--no-preview`` drops the preview, and ``--json``
    prints one array of ``chat_id`` / ``title`` / ``updated_at`` / ``preview``
    objects (``preview`` omitted with ``--no-preview``) for scripting.  Output
    is plain ASCII.

    With no sessions (or when the server cannot be reached) the human-readable
    form says so and exits 0; ``--json`` prints ``[]`` and exits 0.

    :raises typer.BadParameter: When ``--limit`` is negative.
    """
    # Lazy: the logging helpers live in klea_utils.plogging; importing them
    # here keeps ``list-sessions --help`` light (AGENTS.md CLI conventions).
    from klea_utils.plogging import resolve_log_level, setup_root_logger

    if limit < 0:
        raise typer.BadParameter(
            "--limit must be 0 or greater (0 prints every session)",
            param_hint="--limit",
        )

    setup_root_logger("klea-agent", stderr_level=resolve_log_level(debug))

    # Lazy: stdlib logging only backs this command's debug trace, so it is
    # imported here rather than at module import time.
    import logging

    logger = logging.getLogger(__name__)

    # Lazy: the SSE client, the identity seam, the shared spawn helper and the
    # async runner are heavy and only needed once the command actually runs,
    # not for ``list-sessions --help``.
    import asyncio

    import httpx
    from klea_utils.ui.cli import _maybe_spawn_server
    from klea_utils.ui.client import fetch_chats
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
        sessions = await fetch_chats(server_url, resolved_user_id)
        logger.debug(
            "list-sessions(%s): %d session(s), limit=%d, json=%s, preview=%s",
            resolved_user_id,
            len(sessions),
            limit,
            as_json,
            not no_preview,
        )
        if limit:
            sessions = sessions[:limit]
        if as_json:
            payload = []
            for session in sessions:
                item = {
                    "chat_id": session.get("chat_id", ""),
                    "title": session.get("title", ""),
                    "updated_at": session.get("updated_at"),
                }
                if not no_preview:
                    item["preview"] = session.get("preview")
                payload.append(item)
            typer.echo(json.dumps(payload, indent=2, default=str))
            return 0
        if not sessions:
            typer.echo(f"No chat sessions found for user {resolved_user_id}.")
            return 0
        for session in sessions:
            chat_id = session.get("chat_id", "")
            title = session.get("title", "")
            timestamp = _format_epoch(session.get("updated_at"))
            typer.echo(f"{chat_id}  {timestamp}  {title}".rstrip())
            preview = session.get("preview")
            if preview and not no_preview:
                typer.echo(f"    {preview}")
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
        # A rejected request (for example HTTP 422 on a malformed user id).
        typer.echo(f"Request failed: {exc}", err=True)
        raise typer.Exit(code=1) from None
