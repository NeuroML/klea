#!/usr/bin/env python3
"""
Shared Typer client CLI factory for Klea packages.

File: klea_utils/ui/cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import importlib.util
import os
import shlex
import subprocess
import sys
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


def _resolve_favicon(value: str | None) -> str | None:
    """Resolve a ``--favicon`` value to something NiceGUI can use.

    An empty value returns ``None`` so the caller falls back to the
    bundled Klea icon.  ``http(s)``/``data:`` URLs, raw SVG and a single
    character are passed through unchanged; anything else is treated as a
    local file path, expanded and required to exist, so a typo fails fast
    instead of silently falling back to the default icon.  Relative paths
    are resolved against the current directory (the web app runs from its
    own package directory, so a relative path would otherwise miss).

    :param value: Raw ``--favicon`` value, or ``None``.
    :returns: The favicon to hand to NiceGUI, or ``None``.
    :raises ValueError: When *value* looks like a path but does not exist.
    """
    value = (value or "").strip()
    if not value:
        return None
    if value.startswith(("http://", "https://", "data:", "<svg")):
        return value
    if len(value) == 1:
        return value
    path = Path(value).expanduser()
    if not path.is_file():
        raise ValueError(f"Favicon file not found: {value}")
    return str(path.resolve())


def _maybe_spawn_server(
    server_url: str,
    app_module: str,
    profile: str | None = None,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
    quiet: bool = False,
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
    :param quiet: When ``True``, discard the spawned server's stdout/stderr
        (used by the full-screen TUI so server logs do not corrupt it); see
        :func:`klea_utils.api.server.spawn_server`.
    """
    # Lazy: spawn_server pulls in the klea_utils.api machinery.
    from klea_utils.api.server import (
        configure_profile,
        spawn_server,
        split_server_url,
    )
    from klea_utils.net import is_loopback_host

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
        return spawn_server(
            app_module, host=host, port=port, profile=profile, quiet=quiet
        )
    return spawn_server(app_module, host=host, port=port, quiet=quiet)


def _parse_env_options(values: list[str]) -> dict[str, str]:
    """Parse repeatable ``--env KEY=VALUE`` options.

    :param values: Raw option values.
    :returns: The overrides, later values winning on duplicate keys.
    :raises typer.BadParameter: When a value is not ``KEY=VALUE``.
    """
    overrides: dict[str, str] = {}
    for raw in values:
        key, sep, value = raw.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"Invalid --env {raw!r}; expected KEY=VALUE")
        overrides[key] = value
    return overrides


def _build_client_context(
    server_url: str,
    *,
    cwd: str | None = None,
    env: list[str] | None = None,
    interpreter: str | None = None,
    disabled: bool = False,
):
    """Build the TUI's per-query client context, or ``None`` when disabled.

    The whole current environment is forwarded (with ``--env`` overrides on
    top), so custom project variables are preserved.  Context is only sent to
    a loopback server: a remote backend runs on another machine and cannot use
    the client's paths/interpreter.

    :param server_url: Base URL of the backend API server.
    :param cwd: Working directory override; defaults to the TUI's cwd.
    :param env: Repeatable ``KEY=VALUE`` overrides.
    :param interpreter: Python interpreter override; defaults to this process.
    :param disabled: Skip forwarding entirely.
    :returns: A :class:`~klea_utils.client_context.ClientContext`, or ``None``.
    :raises typer.BadParameter: When ``--cwd`` is not a directory or an
        ``--env`` value is malformed.
    """
    if disabled:
        return None
    # Lazy: keep the TUI/web --help path light.
    from klea_utils.api.server import split_server_url
    from klea_utils.client_context import ClientContext
    from klea_utils.net import is_loopback_host

    host, _ = split_server_url(server_url)
    if not is_loopback_host(host):
        typer.echo(
            "Note: client environment isolation is only available for a local "
            "server; not forwarding cwd/env/interpreter.",
            err=True,
        )
        return None

    cwd_path = Path(cwd).expanduser() if cwd else Path(os.getcwd())
    if not cwd_path.is_dir():
        raise typer.BadParameter(f"--cwd is not a directory: {cwd_path}")
    # Do not resolve symlinks: a virtualenv's ``bin/python`` is normally a
    # symlink to the base interpreter, and resolving it would leave the venv.
    interpreter_path = Path(
        os.path.abspath(
            Path(interpreter).expanduser() if interpreter else sys.executable
        )
    )
    env_values = dict(os.environ)
    env_values.update(_parse_env_options(env or []))
    return ClientContext(
        cwd=str(cwd_path.resolve()),
        env=env_values,
        interpreter=str(interpreter_path),
    )


def _run_tui(
    server_url: str,
    title: str,
    tui_app_name: str,
    tui_prefix: str,
    app_module: str,
    tui_entry: str,
    user_id: str = "",
    resume: str = "",
    continue_session: bool = False,
    identity_app_name: str = "",
    client_cwd: str | None = None,
    client_env: list[str] | None = None,
    client_interpreter: str | None = None,
    no_client_context: bool = False,
    debug: bool = False,
    profile: str | None = None,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> None:
    """Run the in-process Textual terminal client.

    Unlike the NiceGUI web client (a subprocess), the Textual app is a
    library launched in this process (ADR-0050).  *tui_entry* is the app's
    entry as ``"module:function"`` (e.g.
    ``"klea_agent.ui.tui.page:run_agent_tui"``), resolved at launch so the
    CLI imports no app package and ``--help`` stays light.
    """
    # Guard: textual is an optional extra (utils_pkg/setup.cfg: [tui]).
    # Keep this at function entry so ``tui --help`` still works but ``tui``
    # without the extra fails fast with an actionable hint.
    try:
        # Lazy: require_extra uses only find_spec (stdlib).
        from klea_utils.imports import require_extra

        require_extra("textual", "tui")
    except ImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if debug:
        # Make debug visible to the spawned server subprocess.
        from klea_utils.plogging import enable_debug_logging

        enable_debug_logging()
    client_context = _build_client_context(
        server_url,
        cwd=client_cwd,
        env=client_env,
        interpreter=client_interpreter,
        disabled=no_client_context,
    )
    with _maybe_spawn_server(
        server_url,
        app_module,
        profile=profile,
        config_env_var=config_env_var,
        config_dir=config_dir,
        template_writer=template_writer,
        quiet=True,
    ):
        # Lazy: the app's Textual entry (and its heavy imports) is only needed
        # on launch; resolve it from ``tui_entry`` so this module imports no
        # app package.
        module_path, _, attr = tui_entry.partition(":")
        try:
            module = importlib.import_module(module_path)
            builder = getattr(module, attr or "run_tui")
        except (ImportError, AttributeError) as exc:
            typer.echo(f"Could not load TUI entry '{tui_entry}': {exc}", err=True)
            raise typer.Exit(code=1) from None
        builder(
            url=server_url,
            title=title,
            app_prefix=tui_prefix,
            app_name=tui_app_name,
            user_id=user_id,
            resume=resume,
            continue_session=continue_session,
            identity_app_name=identity_app_name,
            client_context=client_context,
        )


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
    favicon: str | None = None,
    user_id: str = "",
    identity_app_name: str = "",
    resume: str = "",
    continue_session: bool = False,
    open_browser: bool = True,
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
    try:
        favicon = _resolve_favicon(favicon)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None
    if debug:
        # Make debug visible to the spawned server and web app processes.
        from klea_utils.plogging import enable_debug_logging

        enable_debug_logging()
    # This launcher process blocks on the NiceGUI subprocess, so title it too
    # (the child titles itself in run_nicegui_server).
    from klea_utils.proctitle import set_process_title

    set_process_title(web_app_name)
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
        # klea-rag-web and klea-agent-web use separate user_data_dir/nicegui dirs.
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
                    + f" --user-id {shlex.quote(user_id)}"
                    + f" --identity-app-name {shlex.quote(identity_app_name)}"
                    + (f" --resume {shlex.quote(resume)}" if resume else "")
                    + (" --continue" if continue_session else "")
                    + ("" if open_browser else " --no-open")
                    + (f" --favicon '{favicon}'" if favicon else "")
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
    tui_prefix: str,
    tui_entry: str,
    web_app_name: str,
    web_entry: str,
    identity_app_name: str = "",
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> typer.Typer:
    """Create a Typer app for a Klea user client (tui + web).

    The app exposes ``tui`` / ``web`` subcommands for the Textual and NiceGUI
    clients respectively, each carrying its own options so ``--help`` on a
    subcommand lists everything it accepts.  Invoked with no subcommand the
    app launches the Textual TUI with defaults.  All package-specific values
    are passed as parameters, so the rag and code entry points stay thin
    wrappers.

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
    :param tui_prefix: Prompt label prefix for the terminal client (e.g.
        ``"klea-rag"``)
    :param tui_entry: Module:function of the app's TUI entry (the
        ``run_*_tui`` launched in-process by the ``tui`` subcommand, e.g.
        ``"klea_rag.ui.tui.page:run_rag_tui"``)
    :param web_app_name: Log identity for the web client (e.g.
        ``"klea-rag-web"``)
    :param web_entry: Module string of the app's NiceGUI entry point
        (the ``app.py`` launched by the ``web`` subcommand, e.g.
        ``"klea_rag.ui.web.app"``)
    :param identity_app_name: Canonical identity key for the persisted
        ``user_id`` file, shared by the TUI and web frontends of one app so a
        session can be opened in either (e.g. ``"klea-agent"``).  Falls back to
        the frontend's own ``app_name`` when empty.
    :param config_env_var: Environment variable that carries the config
        file name into the spawned server (e.g. ``"KLEA_RAG_APP_CONFIG_FILE"``)
    :param config_dir: Config directory searched after the working
        directory when validating ``--profile``
    :param template_writer: Callable that writes a config template into
        the working directory for ``--profile template``
    :returns: A :class:`typer.Typer` app for use as a CLI entry point
    """
    app = typer.Typer(help=f"Simple KLEA {label} user client")

    tui_help = f"Klea {label} tui client (Textual)"
    web_help = f"Klea {label} web client (NiceGUI)"

    # Shared options are defined once and attached to every subcommand so
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
    client_env_option = typer.Option(
        None,
        "--env",
        help="Environment override for the bundled tools, KEY=VALUE "
        "(repeatable). The whole current environment is forwarded by default.",
    )

    @app.callback(invoke_without_command=True)
    def main(ctx: typer.Context):
        """Launch the Textual TUI when no subcommand is given."""
        if ctx.invoked_subcommand is None:
            _run_tui(
                server_url=server_url_default,
                title=f"KLEA {label}",
                tui_app_name=tui_app_name,
                tui_prefix=tui_prefix,
                app_module=app_module,
                tui_entry=tui_entry,
                identity_app_name=identity_app_name,
                config_env_var=config_env_var,
                config_dir=config_dir,
                template_writer=template_writer,
            )

    @app.command()
    def tui(
        server_url: str = server_option,
        title: str = title_option,
        user_id: str = typer.Option(
            "",
            "--user-id",
            help="Opaque user id. Defaults to KLEA_USER_ID, else a persisted "
            "local UUID (sent only to a loopback server).",
        ),
        resume: str = typer.Option(
            "",
            "--resume",
            help="Resume a specific chat session by id.",
        ),
        continue_: bool = typer.Option(
            False,
            "--continue",
            help="Resume the most recently updated chat session.",
        ),
        client_cwd: str = typer.Option(
            "",
            "--cwd",
            help="Working directory for the session's bundled tools "
            "(default: the current directory).",
        ),
        client_env: list[str] | None = client_env_option,
        client_interpreter: str = typer.Option(
            "",
            "--interpreter",
            help="Python interpreter for the bundled tools "
            "(default: the interpreter running this client).",
        ),
        no_client_context: bool = typer.Option(
            False,
            "--no-client-context",
            help="Do not forward cwd/env/interpreter to the server.",
        ),
        debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
        profile: str = profile_option,
    ):
        _run_tui(
            server_url=server_url,
            title=title,
            tui_app_name=tui_app_name,
            tui_prefix=tui_prefix,
            app_module=app_module,
            tui_entry=tui_entry,
            user_id=user_id,
            resume=resume,
            continue_session=continue_,
            identity_app_name=identity_app_name,
            client_cwd=client_cwd or None,
            client_env=client_env,
            client_interpreter=client_interpreter or None,
            no_client_context=no_client_context,
            debug=debug,
            profile=profile,
            config_env_var=config_env_var,
            config_dir=config_dir,
            template_writer=template_writer,
        )

    tui.__doc__ = tui_help

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
            help="Disclaimer text shown in the footer",
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
        favicon: str = typer.Option(
            "",
            "--favicon",
            help=(
                "Browser-tab icon: a file path, http(s)/data URL, raw SVG or "
                "a single character. Defaults to the bundled Klea icon."
            ),
        ),
        reload: bool = typer.Option(
            False, "--reload", "-r", help="Enable auto-reload on file changes"
        ),
        user_id: str = typer.Option(
            "",
            "--user-id",
            help="Opaque user id. Defaults to KLEA_USER_ID, else a shared "
            "host-local identity file (loopback only).",
        ),
        resume: str = typer.Option(
            "",
            "--resume",
            help="Open a specific chat session by id on load.",
        ),
        continue_: bool = typer.Option(
            False,
            "--continue",
            help="Open the most recently updated chat session on load.",
        ),
        open_browser: bool = typer.Option(
            True,
            "--open/--no-open",
            help="Open the UI in the default browser once ready (default: yes).",
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
            favicon=favicon,
            user_id=user_id,
            identity_app_name=identity_app_name,
            resume=resume,
            continue_session=continue_,
            open_browser=open_browser,
            profile=profile,
            config_env_var=config_env_var,
            config_dir=config_dir,
            template_writer=template_writer,
        )

    web.__doc__ = web_help

    return app
