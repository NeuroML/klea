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

agent_app = make_client_app(
    label="Agent",
    server_url_default="http://127.0.0.1:8006",
    app_module="klea_agent.api.main:app",
    tui_app_name="klea-agent-tui",
    tui_prefix="klea-agent",
    tui_entry="klea_agent.ui.tui.page:run_agent_tui",
    web_app_name="klea-agent-web",
    web_entry="klea_agent.ui.web.app",
    identity_app_name="klea-agent",
    config_env_var="KLEA_AGENT_APP_CONFIG_FILE",
    config_dir=get_config_dir(PlatformDirs("klea-agent")),
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


@agent_app.command()
def headless(
    query: str = typer.Argument(..., help="Query to run through the agent."),
    thread_id: str = typer.Option(
        "default_thread",
        "--thread-id",
        help="Thread id the run is checkpointed under.",
    ),
    path_check: str = typer.Option(
        "",
        "--path-check",
        help="Path-check mode: strict, declared or off (unset keeps the default).",
    ),
    path_check_stance: str = typer.Option(
        "",
        "--path-check-stance",
        help="Path-check stance: deny or allow (unset keeps the default).",
    ),
    hitl: bool | None = typer.Option(
        None,
        "--hitl/--no-hitl",
        help="Enable or disable human-in-the-loop (unset keeps the default).",
    ),
    debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
):
    """Run one query non-interactively and print the final answer.

    The three safety toggles are validated here and exported through the
    environment (``KLEA_AGENT_PATH_CHECK``, ``KLEA_AGENT_PATH_CHECK_STANCE``,
    ``KLEA_AGENT_HITL``) before the orchestrator is constructed, so the graph
    reads them at startup just as it does for the interactive clients.
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

    # Lazy: the agent graph and the async runner are heavy and only needed
    # once the command actually runs, not for ``headless --help``.
    import asyncio

    from klea_agent.klea_agent import KleaAgent

    async def _run() -> int:
        graph = KleaAgent(checkpoint="sqlite")
        await graph.setup()
        answer = ""
        interrupted = None
        async for event in graph.run_graph_astream_events(query, thread_id):
            event_type = event.get("type")
            if event_type == "complete":
                answer = event.get("message_for_user", "")
            elif event_type == "interrupt":
                interrupted = event.get("data", event)
        if interrupted is not None:
            typer.echo(json.dumps(interrupted, indent=2, default=str), err=True)
            return 1
        typer.echo(answer)
        return 0

    raise typer.Exit(code=asyncio.run(_run()))
