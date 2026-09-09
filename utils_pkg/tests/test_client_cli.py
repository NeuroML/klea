#!/usr/bin/env python3
"""
Tests for the shared client Typer CLI factory.

File: tests/test_client_cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import contextlib
import logging
import unittest
from unittest import mock

from klea_utils.ui.cli import _maybe_spawn_server, _run_web, make_client_app
from typer.testing import CliRunner

logger = logging.getLogger(__name__)


def _make_app(label="RAG", port=8005):
    """A factory instance mirroring the real rag/code entry points."""
    return make_client_app(
        label=label,
        server_url_default=f"http://127.0.0.1:{port}",
        app_module=f"klea_{label.lower()}.api.main:app",
        tui_app_name=f"klea-{label.lower()}-tui",
        web_app_name=f"klea-{label.lower()}-web",
        web_entry=f"klea_{label.lower()}.ui.web.app",
    )


class TestMakeClientApp(unittest.TestCase):
    """Routing of the Typer app: options, subcommand dispatch and help.

    The real client entry points (_run_cli / _run_web) are mocked so the
    tests only verify how the CLI parses and routes arguments.
    """

    def setUp(self):
        self.runner = CliRunner()
        self.app = _make_app()

    def test_bare_invocation_prints_usage(self):
        # No subcommand: a hint plus the group help, and a clean exit.
        result = self.runner.invoke(self.app, [])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("Please specify a subcommand (cli | web).", result.output)
        self.assertIn("Usage:", result.output)

    def test_cli_routing(self):
        with mock.patch("klea_utils.ui.cli._run_cli") as run_cli:
            result = self.runner.invoke(self.app, ["cli", "--single-query", "hello"])
            self.assertEqual(result.exit_code, 0)
            run_cli.assert_called_once_with(
                server_url="http://127.0.0.1:8005",
                title="KLEA RAG",
                single_query="hello",
                tui_app_name="klea-rag-tui",
                app_module="klea_rag.api.main:app",
                debug=False,
                profile=None,
                config_env_var=None,
                config_dir=None,
                template_writer=None,
            )

    def test_cli_custom_server_and_title(self):
        with mock.patch("klea_utils.ui.cli._run_cli") as run_cli:
            result = self.runner.invoke(
                self.app,
                ["cli", "--server", "http://10.0.0.1:9000", "--title", "T1", "-q", "q"],
            )
            self.assertEqual(result.exit_code, 0)
            run_cli.assert_called_once_with(
                server_url="http://10.0.0.1:9000",
                title="T1",
                single_query="q",
                tui_app_name="klea-rag-tui",
                app_module="klea_rag.api.main:app",
                debug=False,
                profile=None,
                config_env_var=None,
                config_dir=None,
                template_writer=None,
            )

    def test_cli_debug_routing(self):
        with mock.patch("klea_utils.ui.cli._run_cli") as run_cli:
            result = self.runner.invoke(self.app, ["cli", "--debug", "-q", "hi"])
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(run_cli.call_args.kwargs["debug"])

    def test_web_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(
                self.app, ["web", "--title", "Y", "--nicegui-url", "0.0.0.0:9999"]
            )
            self.assertEqual(result.exit_code, 0)
            kwargs = run_web.call_args.kwargs
            self.assertEqual(kwargs["server_url"], "http://127.0.0.1:8005")
            self.assertEqual(kwargs["title"], "Y")
            self.assertEqual(kwargs["nicegui_url"], "0.0.0.0:9999")
            self.assertEqual(kwargs["web_app_name"], "klea-rag-web")
            self.assertEqual(kwargs["reload"], False)
            self.assertEqual(kwargs["debug"], False)

    def test_web_reload_and_debug_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--reload", "--debug"])
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(run_web.call_args.kwargs["reload"])
            self.assertTrue(run_web.call_args.kwargs["debug"])

    def test_unknown_subcommand_fails(self):
        self.assertEqual(self.runner.invoke(self.app, ["bogus"]).exit_code, 2)

    def test_malformed_server_url_is_rejected(self):
        # A bad --server value fails URL validation before the client runs.
        with mock.patch("klea_utils.ui.cli._run_cli") as run_cli:
            result = self.runner.invoke(self.app, ["cli", "--server", "not a url"])
            self.assertEqual(result.exit_code, 2)
            run_cli.assert_not_called()

    def test_top_level_flags_do_not_exist(self):
        self.assertEqual(self.runner.invoke(self.app, ["--server", "x"]).exit_code, 2)


class TestMaybeSpawnServer(unittest.TestCase):
    """Auto-start decision: spawn for loopback hosts, no-op for remote."""

    def test_loopback_host_spawns(self):
        # _maybe_spawn_server lazily imports spawn_server from
        # klea_utils.api.server, so that module is the patch target.
        with mock.patch(
            "klea_utils.api.server.spawn_server", return_value="SPAWNED"
        ) as spawn:
            cm = _maybe_spawn_server("http://127.0.0.1:8005", "klea_rag.api.main:app")
            self.assertEqual(cm, "SPAWNED")
            spawn.assert_called_once_with(
                "klea_rag.api.main:app", host="127.0.0.1", port=8005
            )
            # A portless loopback URL must fall back to the default port.
            cm = _maybe_spawn_server("http://localhost", "klea_rag.api.main:app")
            self.assertEqual(cm, "SPAWNED")
            spawn.assert_called_with(
                "klea_rag.api.main:app", host="localhost", port=8005
            )
            logger.debug("loopback URLs routed to spawn_server (incl. default port)")

    def test_remote_host_is_noop(self):
        with mock.patch("klea_utils.api.server.spawn_server") as spawn:
            cm = _maybe_spawn_server(
                "http://192.168.1.10:8005", "klea_rag.api.main:app"
            )
            self.assertIsInstance(cm, contextlib.AbstractContextManager)
            spawn.assert_not_called()
            logger.debug("remote URL returned a no-op context manager")


class TestRunWeb(unittest.TestCase):
    def test_launches_nicegui_app(self):
        # Four things are faked: locating the nicegui app module, the chdir
        # into its directory, the subprocess that launches it, and the
        # server spawner (a nullcontext here, so nothing is spawned).
        spec = mock.Mock()
        spec.origin = "/opt/klea/nicegui/app.py"
        with (
            mock.patch("klea_utils.ui.cli.importlib.util.find_spec", return_value=spec),
            mock.patch(
                "klea_utils.ui.cli.chdir", return_value=contextlib.nullcontext()
            ),
            mock.patch("klea_utils.ui.cli.subprocess.run") as subprocess_run,
            mock.patch(
                "klea_utils.ui.cli._maybe_spawn_server",
                return_value=contextlib.nullcontext(),
            ),
        ):
            _run_web(
                server_url="http://127.0.0.1:8005",
                title="KLEA RAG",
                subtitle="S",
                disclaimer="D",
                footer_text="F",
                nicegui_url="0.0.0.0:7860",
                storage_secret="SECRET",
                reload=False,
                debug=False,
                web_app_name="klea-rag-web",
                app_module="klea_rag.api.main:app",
                web_entry="klea_rag.ui.web.app",
            )

        # The command is already shlex-split, so quotes are gone.
        subprocess_run.assert_called_once()
        command = " ".join(subprocess_run.call_args.args[0])
        self.assertIn("app.py", command)
        self.assertIn("--app-name klea-rag-web", command)
        self.assertIn("http://127.0.0.1:8005", command)
        self.assertNotIn("--reload", command)
        logger.debug("web client launched with command: %s", command)

    def test_launches_nicegui_app_with_reload(self):
        spec = mock.Mock()
        spec.origin = "/opt/klea/nicegui/app.py"
        with (
            mock.patch("klea_utils.ui.cli.importlib.util.find_spec", return_value=spec),
            mock.patch(
                "klea_utils.ui.cli.chdir", return_value=contextlib.nullcontext()
            ),
            mock.patch("klea_utils.ui.cli.subprocess.run") as subprocess_run,
            mock.patch(
                "klea_utils.ui.cli._maybe_spawn_server",
                return_value=contextlib.nullcontext(),
            ),
        ):
            _run_web(
                server_url="http://127.0.0.1:8005",
                title="KLEA RAG",
                subtitle="S",
                disclaimer="D",
                footer_text="F",
                nicegui_url="0.0.0.0:7860",
                storage_secret="SECRET",
                reload=True,
                debug=False,
                web_app_name="klea-rag-web",
                app_module="klea_rag.api.main:app",
                web_entry="klea_rag.ui.web.app",
            )

        subprocess_run.assert_called_once()
        command = " ".join(subprocess_run.call_args.args[0])
        self.assertIn("--reload", command)


if __name__ == "__main__":
    unittest.main()
