#!/usr/bin/env python3
"""
Shared server launcher factory for Klea packages.

File: klea_utils/api/server.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from subprocess import Popen
from urllib.parse import urlsplit

import httpx
import typer

from klea_utils.paths import resolve_app_config_path

logger = logging.getLogger(__name__)


def configure_profile(
    profile: str | None,
    config_env_var: str | None,
    config_dir: str | Path | None,
    template_writer: Callable[[Path], Path] | None,
) -> None:
    """Apply a ``--profile`` value for the current process.

    The profile is carried into the app module through the environment:
    *config_env_var* is set to ``<name>.json`` and left in place (the env
    var takes precedence over the env file in pydantic-settings).  The
    profile name is validated against the working directory / *config_dir*
    first so a typo fails fast.

    ``template`` is special: it writes a scaffold config into the working
    directory and exits, never launching anything.

    :param profile: Raw ``--profile`` value (a trailing ``.json`` is
        stripped); ``None`` is a no-op
    :param config_env_var: Environment variable that names the config
        file, or ``None`` to skip setting one
    :param config_dir: Config directory used for validation; ``None``
        skips the fast-fail check
    :param template_writer: Callable that writes a config template into
        the working directory and returns its path; ``None`` disables
        ``--profile template``
    :raises typer.BadParameter: If the profile does not resolve to a file
    :raises typer.Exit: After writing a template
    """
    if profile is None:
        return

    logger.debug("configure_profile(profile=%s)", profile)

    if profile == "template":
        if template_writer is None:
            raise typer.BadParameter("--profile template is not supported here.")
        try:
            path = template_writer(Path.cwd())
        except FileExistsError as e:
            raise typer.BadParameter(str(e)) from e
        print(f"Template config written to {path}.\nFill it in, then re-run.")
        raise typer.Exit(0)

    stem = profile.removesuffix(".json")
    if config_dir is not None:
        try:
            resolved = resolve_app_config_path(f"{stem}.json", config_dir)
        except FileNotFoundError as e:
            raise typer.BadParameter(str(e)) from e
        print(f"Config profile: {stem} -> {resolved}")
    if config_env_var:
        os.environ[config_env_var] = f"{stem}.json"
    else:
        print(
            f"Warning: --profile {profile} has no effect here "
            "(no config environment variable is configured)."
        )


def make_serve_app(
    app_module: str,
    default_port: int = 8005,
    config_env_var: str | None = None,
    config_dir: str | Path | None = None,
    template_writer: Callable[[Path], Path] | None = None,
) -> typer.Typer:
    """Create a Typer app that runs uvicorn on the given *app_module*.

    The module string should be the importable path to a FastAPI ``app``
    instance, e.g. ``"klea_rag.api.main:app"``.

    The ``serve`` command accepts a ``--profile`` option that selects the
    JSON config file for the app.  The value is validated (see
    :func:`configure_profile`); when *config_env_var* is given it is
    forwarded to the app module through that environment variable, so a
    profile dropped in the working directory or the config directory is
    used without any other wiring.  The special profile ``template``
    scaffolds a new config and exits instead of launching.

    :param app_module: Uvicorn module string
    :param default_port: Default port number
    :param config_env_var: Environment variable that carries the config
        file name into the app process (e.g. ``"KLEA_RAG_APP_CONFIG_FILE"``)
    :param config_dir: Config directory searched after the working
        directory when validating ``--profile``
    :param template_writer: Callable that writes a config template into
        the working directory for ``--profile template``
    :returns: A :class:`typer.Typer` app for use as a CLI entry point
    """
    serve_app = typer.Typer()

    # With exactly one registered command typer collapses the app into that
    # command (see ``typer.main.get_command``): the ``serve`` name never
    # appears in the CLI, so the options below are the top-level options of
    # ``klea-serve`` / ``klea-rag-serve``.  The decorator still registers the
    # command -- adding a second one would turn the app into a command group.
    @serve_app.command()
    def serve(
        host: str = "127.0.0.1",
        port: int = default_port,
        reload: bool = typer.Option(
            False, "--reload", help="Enable auto-reload (like fastapi dev)"
        ),
        debug: bool = typer.Option(False, "--debug", help="Enable debug logging"),
        profile: str = typer.Option(
            None,
            "--profile",
            "-p",
            help="Config profile name: loads <name>.json from the current "
            "directory or the config dir. Use 'template' to scaffold a "
            "new config and exit.",
        ),
    ):
        """Run the API server."""
        configure_profile(profile, config_env_var, config_dir, template_writer)

        # --debug must be visible to the app process, whose logger level is
        # resolved from KLEA_LOG_LEVEL (set here) before setup() loads the
        # app env file.  Lazy: enable_debug_logging is stdlib-only, but
        # kept inside for consistency with the other lazy imports here.
        if debug:
            from klea_utils.plogging import enable_debug_logging

            enable_debug_logging()

        # Lazy: uvicorn pulls in starlette/httptools/websockets etc.
        import uvicorn

        uvicorn.run(
            app_module,
            host=host,
            port=port,
            reload=reload,
        )

    return serve_app


def split_server_url(url: str, default_port: int = 8005) -> tuple[str, int]:
    """Return ``(host, port)`` parsed from *url*.

    Falls back to ``127.0.0.1`` and *default_port* when the URL does not
    carry a hostname or port.

    :param url: Server URL (e.g. ``http://127.0.0.1:8005``)
    :param default_port: Port to use when the URL omits one
    :returns: ``(host, port)`` suitable for binding a local server
    """
    parts = urlsplit(url)
    host = parts.hostname or "127.0.0.1"
    try:
        port = parts.port or default_port
    except ValueError:
        port = default_port
    return host, port


def is_loopback_host(host: str) -> bool:
    """Return ``True`` when *host* refers to the local machine.

    :param host: Hostname from a server URL (e.g. ``"127.0.0.1"``)
    :returns: ``True`` for loopback addresses, ``False`` otherwise
    """
    return host.lower() in {"127.0.0.1", "localhost", "::1"}


@contextmanager
def spawn_server(
    app_module: str,
    host: str = "127.0.0.1",
    port: int = 8005,
    timeout: float = 180.0,
    profile: str | None = None,
) -> Iterator[Popen | None]:
    """Context manager that runs an API server in a subprocess.

    If a healthy server is already listening at ``host:port`` (probed via
    ``/health/ready``), nothing is spawned and ``None`` is yielded, so the
    caller does not own the server's lifecycle.  Otherwise a uvicorn
    subprocess is spawned (stdout and stderr inherited so startup errors and
    app output are visible; access logs are disabled to keep the shared
    terminal clean, with full logging preserved in the server's rotating log
    file), readiness is waited on, and the subprocess is terminated when the
    ``with`` block exits.

    When *profile* is given but a server is already running, the profile
    cannot take effect (the running server was started with its own
    config) and a warning is printed.

    :param app_module: Uvicorn module string (e.g. ``"klea_rag.api.main:app"``)
    :param host: Host to bind
    :param port: Port to bind
    :param timeout: Total seconds to wait for readiness after spawning
    :param profile: Config profile the caller requested (used only to warn
        when the requested profile cannot apply)
    :returns: The spawned :class:`subprocess.Popen` (or ``None`` if an
        existing server was reused)
    """
    # Lazy: asyncio/httpx and the api utils pull in heavy deps; keep --help fast.

    health_url = f"http://{host}:{port}/health/ready"
    logger.debug(
        "spawn_server(app_module=%s host=%s port=%s profile=%s)",
        app_module,
        host,
        port,
        profile,
    )

    def _probe_once() -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(health_url)
                resp.raise_for_status()
                data = resp.json()
                return data.get("status") == "ready"
        except Exception:  # noqa: BLE001
            return False

    if _probe_once():
        logger.debug("Reusing already-running server at %s", health_url)
        if profile:
            print(
                f"Warning: a server is already running at {health_url} -- "
                f"--profile {profile} is ignored (restart the server to "
                "change its config)."
            )
        yield None
        return

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            app_module,
            "--host",
            host,
            "--port",
            str(port),
            "--no-access-log",
        ],
        start_new_session=True,
    )
    logger.debug("Spawned server for %s (pid=%s)", app_module, proc.pid)

    try:
        # A short fast-fail window catches an instantly-crashed server
        # (bad module path, port already in use) so the error surfaces
        # immediately instead of after the full retry timeout.
        for _ in range(5):
            if proc.poll() is not None:
                raise RuntimeError(
                    f"Server process exited immediately (code {proc.returncode}). "
                    "Check the server's log file, or run the server directly "
                    "with 'klea-rag-serve' / 'klea-serve' "
                    "to see the error."
                )
            if _probe_once():
                break
            time.sleep(1.0)
        else:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"Server at {health_url} did not become ready within {timeout:g}s "
                        f"(process exited with code {proc.returncode}). "
                        "Check the server's log file, or run the server directly "
                        "with 'klea-rag-serve' / 'klea-serve' "
                        "to see the error."
                    )
                if _probe_once():
                    break
                time.sleep(1.0)
            else:
                raise RuntimeError(
                    f"Server at {health_url} did not become ready within {timeout:g}s. "
                    "Check the server's log file, or run the server directly "
                    "with 'klea-rag-serve' / 'klea-serve' "
                    "to see the error."
                )

        logger.info("Server ready at %s (pid=%s)", health_url, proc.pid)
        yield proc
    finally:
        if proc.poll() is None:
            try:
                if hasattr(os, "killpg"):
                    os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM to group
                else:
                    proc.terminate()
            except Exception:  # noqa: BLE001
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001, S110
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    if hasattr(os, "killpg"):
                        os.killpg(os.getpgid(proc.pid), 9)  # SIGKILL to group
                    else:
                        proc.kill()
                except Exception:  # noqa: BLE001
                    try:
                        proc.kill()
                    except Exception:  # noqa: BLE001, S110
                        pass
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001, S110
                    pass
