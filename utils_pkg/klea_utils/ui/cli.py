#!/usr/bin/env python3
"""
Shared Typer client CLI factory for Klea packages.

File: klea_utils/ui/cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import importlib.util
import shlex
import subprocess
from collections.abc import Callable
from contextlib import chdir, nullcontext
from pathlib import Path

import typer

from klea_utils.api.utils import validate_url


def _validate_url(value: str) -> str:
    try:
        return validate_url(value)
    except ValueError as e:
        raise typer.BadParameter(str(e))


def _maybe_spawn_server(
    server_url: str,
    app_module: str,
    profile: str | None = None,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
):
    """Context manager spawning a local server when none is running.

    Shared by the cli and web clients.  Auto-starting only makes sense for
    a server on the local machine, so this is a no-op when *server_url*
    points at a remote host.  When the local server is already running it
    is reused and left running (no-op), otherwise one is spawned and
    stopped when the ``with`` block exits.

    A ``--profile`` value is applied before any spawn decision: the
    special value ``template`` scaffolds a config and exits, any other
    name is validated and forwarded to the spawned server through
    *config_env_var* (see
    :func:`klea_utils.api.server.configure_profile`).  A profile cannot
    affect a server that already exists or one on a remote host, so a
    warning is printed in those cases.

    :param server_url: Base URL of the API server (e.g. ``http://127.0.0.1:8005``)
    :param app_module: Uvicorn module string for the server (e.g.
        ``"klea_rag.api.main:app"``)
    :param profile: Config profile name, or ``None``
    :param config_env_var: Env var carrying the config file into the server
    :param config_dir: Config directory used for profile validation
    :param template_writer: Callable that writes a config template for
        ``--profile template``
    """
    # Lazy: spawn_server pulls in the klea_utils.api machinery.
    from klea_utils.api.server import (
        configure_profile,
        is_loopback_host,
        spawn_server,
        split_server_url,
    )

    configure_profile(profile, config_env_var, config_dir, template_writer)

    host, port = split_server_url(server_url)
    if not is_loopback_host(host):
        if profile:
            print(
                f"Warning: --profile {profile} is ignored when connecting to a "
                f"remote server ({server_url})."
            )
        return nullcontext()
    if profile:
        return spawn_server(app_module, host=host, port=port, profile=profile)
    return spawn_server(app_module, host=host, port=port)


def _run_cli(
    server_url: str,
    title: str,
    single_query: str,
    tui_app_name: str,
    app_module: str,
    debug: bool = False,
    profile: str | None = None,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> None:
    """Run the interactive terminal (cli) client."""
    if debug:
        # Make debug visible to the spawned server subprocess.
        from klea_utils.plogging import enable_debug_logging

        enable_debug_logging()
    with _maybe_spawn_server(
        server_url,
        app_module,
        profile=profile,
        config_env_var=config_env_var,
        config_dir=config_dir,
        template_writer=template_writer,
    ):
        from klea_utils.ui.tui.repl import run_repl

        try:
            asyncio.run(
                run_repl(
                    url=server_url,
                    title=title,
                    single_query=single_query,
                    app_prefix="klea",
                    app_name=tui_app_name,
                )
            )
        except KeyboardInterrupt:
            print("\nInterrupted. Exiting.")


def _run_web(
    server_url: str,
    title: str,
    subtitle: str,
    disclaimer: str,
    footer_text: str,
    nicegui_url: str,
    storage_secret: str,
    reload: bool,
    debug: bool,
    web_app_name: str,
    app_module: str,
    web_entry: str,
    profile: str | None = None,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> None:
    """Run the NiceGUI web client.

    Launches the app's own NiceGUI entry module (``web_entry``, e.g.
    ``"klea_rag.ui.web.app"``) as a subprocess from its directory, so
    each app's page composition lives in the app (ADR-0031).
    """
    # Guard: nicegui is an optional extra (utils_pkg/setup.cfg: [nicegui]).
    # Keep this at function entry so ``web --help`` still works but
    # ``web`` without the extra fails fast with an actionable hint.
    try:
        # Lazy: require_extra uses only find_spec (stdlib).
        from klea_utils.imports import require_extra

        require_extra("nicegui", "nicegui")
    except ImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if debug:
        # Make debug visible to the spawned server and web app processes.
        from klea_utils.plogging import enable_debug_logging

        enable_debug_logging()
    with _maybe_spawn_server(
        server_url,
        app_module,
        profile=profile,
        config_env_var=config_env_var,
        config_dir=config_dir,
        template_writer=template_writer,
    ):
        spec = importlib.util.find_spec(web_entry)
        assert spec and spec.origin, f"Could not locate NiceGUI web entry {web_entry}"
        cwd = Path(spec.origin).parent
        # Forward NICEGUI_STORAGE_PATH for reload subprocess and per-app
        # default.  If not set, derive from PlatformDirs(web_app_name) so
        # klea-rag-web and klea-web use separate user_data_dir/nicegui dirs.
        import os

        from platformdirs import PlatformDirs

        env = dict(os.environ)
        if "NICEGUI_STORAGE_PATH" not in env:
            env["NICEGUI_STORAGE_PATH"] = str(
                (Path(PlatformDirs(web_app_name).user_data_dir) / "nicegui").resolve()
            )
        with chdir(cwd):
            subprocess.run(
                shlex.split(
                    f"python app.py '{title}' '{subtitle}' '{server_url}'"
                    + f" --disclaimer '{disclaimer}'"
                    + f" --footer '{footer_text}'"
                    + f" --nicegui-url '{nicegui_url}'"
                    + f" --storage-secret '{storage_secret}'"
                    + f" --app-name '{web_app_name}'"
                    + (" --reload" if reload else "")
                ),
                check=False,
                env=env,
            )


def make_client_app(
    *,
    label: str,
    server_url_default: str,
    app_module: str,
    tui_app_name: str,
    web_app_name: str,
    web_entry: str,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> typer.Typer:
    """Create a Typer app for a Klea user client (cli + web).

    The app exposes ``cli`` / ``web`` subcommands for the terminal and
    NiceGUI clients respectively, each carrying its own options so
    ``--help`` on a subcommand lists everything it accepts.  Invoked with
    no subcommand the app prints its usage help.  All package-specific
    values are passed as parameters, so the rag and code entry points
    stay thin wrappers.

    Both subcommands accept ``--profile``: the value is validated and
    forwarded to a spawned local server through *config_env_var* (see
    :func:`klea_utils.api.server.configure_profile`), so the config file
    is chosen per invocation.  A profile only applies to a server the
    client spawns itself; reusing an already-running server or pointing at
    a remote host ignores it with a warning.  The special profile
    ``template`` scaffolds a new config and exits.

    :param label: Short package name used in help text (e.g. ``"RAG"``)
    :param server_url_default: Default server URL (e.g.
        ``"http://127.0.0.1:8005"``)
    :param app_module: Uvicorn module string for the server this client
        talks to (e.g. ``"klea_rag.api.main:app"``)
    :param tui_app_name: Log identity for the terminal client (e.g.
        ``"klea-rag-tui"``)
    :param web_app_name: Log identity for the web client (e.g.
        ``"klea-rag-web"``)
    :param web_entry: Module string of the app's NiceGUI entry point
        (the ``app.py`` launched by the ``web`` subcommand, e.g.
        ``"klea_rag.ui.web.app"``)
    :param config_env_var: Environment variable that carries the config
        file name into the spawned server (e.g. ``"KLEA_RAG_APP_CONFIG_FILE"``)
    :param config_dir: Config directory searched after the working
        directory when validating ``--profile``
    :param template_writer: Callable that writes a config template into
        the working directory for ``--profile template``
    :returns: A :class:`typer.Typer` app for use as a CLI entry point
    """
    app = typer.Typer(help=f"Simple KLEA {label} user client")

    cli_help = f"Klea {label} cli client"
    web_help = f"Klea {label} web client (NiceGUI)"

    # Shared options are defined once and attached to both subcommands so
    # each command's --help lists everything it accepts.
    server_option = typer.Option(
        server_url_default,
        "--server",
        "-s",
        help=f"KLEA {label} server (URL:port)",
        callback=_validate_url,
    )
    title_option = typer.Option(
        f"KLEA {label}", "--title", "-t", help="Title for application"
    )
    profile_option = typer.Option(
        None,
        "--profile",
        "-p",
        help="Config profile name: loads <name>.json from the current "
        "directory or the config dir. Use 'template' to scaffold a "
        "new config and exit.",
    )

    @app.callback(invoke_without_command=True)
    def main(ctx: typer.Context):
        """Print usage help when no subcommand is given."""
        if ctx.invoked_subcommand is None:
            print("Please specify a subcommand (cli | web).")
            print(ctx.get_help())
            ctx.exit()

    @app.command()
    def cli(
        server_url: str = server_option,
        title: str = title_option,
        single_query: str = typer.Option(
            None,
            "--single-query",
            "-q",
            help="Single query mode: answer a query and exit",
        ),
        debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
        profile: str = profile_option,
    ):
        _run_cli(
            server_url=server_url,
            title=title,
            single_query=single_query or "",
            tui_app_name=tui_app_name,
            app_module=app_module,
            debug=debug,
            profile=profile,
            config_env_var=config_env_var,
            config_dir=config_dir,
            template_writer=template_writer,
        )

    cli.__doc__ = cli_help

    @app.command()
    def web(
        server_url: str = server_option,
        title: str = title_option,
        subtitle: str = typer.Option(
            "",
            "--subtitle",
            "-b",
            help="Subtitle shown next to title in header",
        ),
        disclaimer: str = typer.Option(
            "Answers use LLM technology and may be incorrect. Please re-confirm.",
            "--disclaimer",
            "-c",
            help="Disclaimer text shown below the chat area",
        ),
        footer_text: str = typer.Option(
            'Powered by <a href="https://github.com/neuroml/klea">Klea</a>',
            "--footer",
            help="Footer HTML content",
        ),
        nicegui_url: str = typer.Option(
            "0.0.0.0:7860",
            "--nicegui-url",
            help="Host:port to bind the NiceGUI web server to",
        ),
        storage_secret: str = typer.Option(
            "klea-nicegui-secret-change-me",
            "--storage-secret",
            help="NiceGUI storage secret for session persistence",
        ),
        reload: bool = typer.Option(
            False, "--reload", "-r", help="Enable auto-reload on file changes"
        ),
        debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
        profile: str = profile_option,
    ):
        _run_web(
            server_url=server_url,
            title=title,
            subtitle=subtitle,
            disclaimer=disclaimer,
            footer_text=footer_text,
            nicegui_url=nicegui_url,
            storage_secret=storage_secret,
            reload=reload,
            debug=debug,
            web_app_name=web_app_name,
            app_module=app_module,
            web_entry=web_entry,
            profile=profile,
            config_env_var=config_env_var,
            config_dir=config_dir,
            template_writer=template_writer,
        )

    web.__doc__ = web_help

    return app
