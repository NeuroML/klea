#!/usr/bin/env python3
"""
Tests for the shared client Typer CLI factory.

File: tests/test_client_cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import contextlib
import io
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import typer
from klea_utils.client_context import ClientContext
from klea_utils.ui.cli import (
    _build_client_context,
    _maybe_spawn_server,
    _parse_env_options,
    _resolve_favicon,
    _run_tui,
    _run_web,
    make_client_app,
)
from typer.testing import CliRunner

logger = logging.getLogger(__name__)


def _make_app(label="RAG", port=8005):
    """A factory instance mirroring the real rag/code entry points."""
    return make_client_app(
        label=label,
        server_url_default=f"http://127.0.0.1:{port}",
        app_module=f"klea_{label.lower()}.api.main:app",
        tui_app_name=f"klea-{label.lower()}-tui",
        tui_prefix=f"klea-{label.lower()}",
        tui_entry=f"klea_{label.lower()}.ui.tui.page:run_{label.lower()}_tui",
        web_app_name=f"klea-{label.lower()}-web",
        web_entry=f"klea_{label.lower()}.ui.web.app",
        identity_app_name=f"klea-{label.lower()}",
    )


class TestMakeClientApp(unittest.TestCase):
    """Routing of the Typer app: options, subcommand dispatch and help.

    The real client entry points (_run_tui / _run_web) are mocked so the
    tests only verify how the CLI parses and routes arguments.
    """

    def setUp(self):
        self.runner = CliRunner()
        self.app = _make_app()

    def test_bare_invocation_launches_tui(self):
        # No subcommand: launch the Textual TUI with the factory defaults.
        with mock.patch("klea_utils.ui.cli._run_tui") as run_tui:
            result = self.runner.invoke(self.app, [])
            self.assertEqual(result.exit_code, 0)
            run_tui.assert_called_once_with(
                server_url="http://127.0.0.1:8005",
                title="KLEA RAG",
                tui_app_name="klea-rag-tui",
                tui_prefix="klea-rag",
                app_module="klea_rag.api.main:app",
                tui_entry="klea_rag.ui.tui.page:run_rag_tui",
                identity_app_name="klea-rag",
                config_env_var=None,
                config_dir=None,
                template_writer=None,
            )

    def test_help_does_not_launch_tui(self):
        with mock.patch("klea_utils.ui.cli._run_tui") as run_tui:
            result = self.runner.invoke(self.app, ["--help"])
            self.assertEqual(result.exit_code, 0)
            run_tui.assert_not_called()

    def test_tui_routing(self):
        with mock.patch("klea_utils.ui.cli._run_tui") as run_tui:
            result = self.runner.invoke(self.app, ["tui"])
            self.assertEqual(result.exit_code, 0)
            run_tui.assert_called_once_with(
                server_url="http://127.0.0.1:8005",
                title="KLEA RAG",
                tui_app_name="klea-rag-tui",
                tui_prefix="klea-rag",
                app_module="klea_rag.api.main:app",
                tui_entry="klea_rag.ui.tui.page:run_rag_tui",
                user_id="",
                resume="",
                continue_session=False,
                identity_app_name="klea-rag",
                client_cwd=None,
                client_env=None,
                client_interpreter=None,
                no_client_context=False,
                debug=False,
                profile=None,
                config_env_var=None,
                config_dir=None,
                template_writer=None,
            )

    def test_tui_routes_client_context_options(self):
        with mock.patch("klea_utils.ui.cli._run_tui") as run_tui:
            result = self.runner.invoke(
                self.app,
                [
                    "tui",
                    "--cwd",
                    "/tmp",
                    "--env",
                    "A=1",
                    "--env",
                    "B=2",
                    "--interpreter",
                    "/usr/bin/python3",
                    "--no-client-context",
                ],
            )
            self.assertEqual(result.exit_code, 0)
            kwargs = run_tui.call_args.kwargs
            self.assertEqual(kwargs["client_cwd"], "/tmp")
            self.assertEqual(kwargs["client_env"], ["A=1", "B=2"])
            self.assertEqual(kwargs["client_interpreter"], "/usr/bin/python3")
            self.assertTrue(kwargs["no_client_context"])

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
            self.assertEqual(kwargs["favicon"], "")
            self.assertEqual(kwargs["user_id"], "")
            self.assertEqual(kwargs["identity_app_name"], "klea-rag")
            self.assertEqual(kwargs["resume"], "")
            self.assertFalse(kwargs["continue_session"])
            self.assertTrue(kwargs["open_browser"])

    def test_web_resume_and_continue_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--resume", "chat-1"])
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(run_web.call_args.kwargs["resume"], "chat-1")

        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--continue"])
            self.assertEqual(result.exit_code, 0)
            self.assertTrue(run_web.call_args.kwargs["continue_session"])

    def test_web_no_open_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--no-open"])
            self.assertEqual(result.exit_code, 0)
            self.assertFalse(run_web.call_args.kwargs["open_browser"])

    def test_web_user_id_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--user-id", "abc"])
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(run_web.call_args.kwargs["user_id"], "abc")
            self.assertEqual(run_web.call_args.kwargs["identity_app_name"], "klea-rag")

    def test_web_favicon_routing(self):
        with mock.patch("klea_utils.ui.cli._run_web") as run_web:
            result = self.runner.invoke(self.app, ["web", "--favicon", "K"])
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(run_web.call_args.kwargs["favicon"], "K")

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
        with mock.patch("klea_utils.ui.cli._run_tui") as run_tui:
            result = self.runner.invoke(self.app, ["tui", "--server", "not a url"])
            self.assertEqual(result.exit_code, 2)
            run_tui.assert_not_called()

    def test_top_level_flags_do_not_exist(self):
        self.assertEqual(self.runner.invoke(self.app, ["--server", "x"]).exit_code, 2)


class TestResolveFavicon(unittest.TestCase):
    """--favicon resolution: pass-through vs local path validation."""

    def test_empty_is_none(self):
        self.assertIsNone(_resolve_favicon(None))
        self.assertIsNone(_resolve_favicon(""))
        self.assertIsNone(_resolve_favicon("   "))

    def test_url_svg_and_char_pass_through(self):
        for value in (
            "https://example.com/icon.png",
            "http://example.com/icon.png",
            "data:image/svg+xml,<svg/>",
            "<svg></svg>",
            "K",
        ):
            self.assertEqual(_resolve_favicon(value), value)

    def test_existing_file_is_resolved_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "icon.png"
            path.write_bytes(b"\x89PNG")
            resolved = _resolve_favicon(str(path))
            assert resolved is not None
            self.assertTrue(Path(resolved).is_absolute())
            self.assertEqual(Path(resolved), path.resolve())

    def test_missing_file_raises(self):
        with self.assertRaises(ValueError):
            _resolve_favicon("/definitely/not/here/icon.png")


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
                "klea_rag.api.main:app", host="127.0.0.1", port=8005, quiet=False
            )
            # A portless loopback URL must fall back to the default port.
            cm = _maybe_spawn_server("http://localhost", "klea_rag.api.main:app")
            self.assertEqual(cm, "SPAWNED")
            spawn.assert_called_with(
                "klea_rag.api.main:app", host="localhost", port=8005, quiet=False
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
            mock.patch("klea_utils.proctitle.set_process_title") as set_title,
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
                user_id="u1",
                identity_app_name="klea-rag",
                resume="chat-1",
                continue_session=True,
                open_browser=False,
            )

        # The launcher process titles itself too (the child titles itself).
        set_title.assert_called_once_with("klea-rag-web")

        # The command is already shlex-split, so quotes are gone.
        subprocess_run.assert_called_once()
        command = " ".join(subprocess_run.call_args.args[0])
        self.assertIn("app.py", command)
        self.assertIn("--app-name klea-rag-web", command)
        self.assertIn("--user-id u1", command)
        self.assertIn("--identity-app-name klea-rag", command)
        self.assertIn("--resume chat-1", command)
        self.assertIn("--continue", command)
        self.assertIn("--no-open", command)
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

    def test_favicon_forwarded_to_app(self):
        spec = mock.Mock()
        spec.origin = "/opt/klea/nicegui/app.py"
        with tempfile.TemporaryDirectory() as tmp:
            icon = Path(tmp) / "icon.png"
            icon.write_bytes(b"\x89PNG")
            with (
                mock.patch(
                    "klea_utils.ui.cli.importlib.util.find_spec", return_value=spec
                ),
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
                    favicon=str(icon),
                )

        subprocess_run.assert_called_once()
        command = " ".join(subprocess_run.call_args.args[0])
        self.assertIn(f"--favicon {icon.resolve()}", command)

    def test_missing_favicon_errors_before_spawn(self):
        with (
            mock.patch(
                "klea_utils.ui.cli._maybe_spawn_server",
                return_value=contextlib.nullcontext(),
            ) as spawn,
            mock.patch("klea_utils.ui.cli.subprocess.run") as subprocess_run,
            self.assertRaises(typer.Exit),
        ):
            _run_web(
                server_url="http://127.0.0.1:8005",
                title="T",
                subtitle="",
                disclaimer="",
                footer_text="",
                nicegui_url="0.0.0.0:7860",
                storage_secret="SECRET",
                reload=False,
                debug=False,
                web_app_name="klea-rag-web",
                app_module="klea_rag.api.main:app",
                web_entry="klea_rag.ui.web.app",
                favicon="/no/such/favicon.png",
            )
        spawn.assert_not_called()
        subprocess_run.assert_not_called()


class TestRunTui(unittest.TestCase):
    """The tui entry point: [tui] extra guard and stub dispatch."""

    def test_missing_extra_errors_before_spawn(self):
        # textual absent: the guard fails fast, before any server spawn.
        with (
            mock.patch(
                "klea_utils.imports.require_extra",
                side_effect=ImportError("textual is required"),
            ),
            mock.patch("klea_utils.ui.cli._maybe_spawn_server") as spawn,
            self.assertRaises(typer.Exit),
        ):
            _run_tui(
                server_url="http://127.0.0.1:8005",
                title="T",
                tui_app_name="klea-rag-tui",
                tui_prefix="klea-rag",
                app_module="klea_rag.api.main:app",
                tui_entry="klea_rag.ui.tui.page:run_rag_tui",
            )
        spawn.assert_not_called()

    def test_launches_stub_when_extra_present(self):
        # textual present: the resolved entry is launched against a (no-op)
        # server.  Point tui_entry at the shared app so the test does not
        # import an app package.
        with (
            mock.patch("klea_utils.imports.require_extra"),
            mock.patch(
                "klea_utils.ui.cli._maybe_spawn_server",
                return_value=contextlib.nullcontext(),
            ) as spawn,
            mock.patch("klea_utils.ui.tui.app.run_tui") as run_tui,
        ):
            _run_tui(
                server_url="http://127.0.0.1:8005",
                title="T",
                tui_app_name="klea-rag-tui",
                tui_prefix="klea-rag",
                app_module="klea_rag.api.main:app",
                tui_entry="klea_utils.ui.tui.app:run_tui",
                identity_app_name="klea-rag",
                no_client_context=True,
            )
        # The TUI is full-screen: the spawned server output must be discarded.
        self.assertIs(spawn.call_args.kwargs["quiet"], True)
        run_tui.assert_called_once_with(
            url="http://127.0.0.1:8005",
            title="T",
            app_prefix="klea-rag",
            app_name="klea-rag-tui",
            user_id="",
            resume="",
            continue_session=False,
            identity_app_name="klea-rag",
            client_context=None,
        )


class TestClientContextOptions(unittest.TestCase):
    """The TUI client-context builder and --env parsing."""

    def test_build_context_defaults_to_process_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _build_client_context("http://127.0.0.1:8005", cwd=tmp)
            self.assertIsInstance(context, ClientContext)
            assert context is not None
            self.assertEqual(context.cwd, str(Path(tmp).resolve()))
            self.assertEqual(context.interpreter, str(Path(sys.executable).resolve()))
            self.assertIn("PATH", context.env)

    def test_env_overrides_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = _build_client_context(
                "http://127.0.0.1:8005",
                cwd=tmp,
                env=["KLEA_TEST=one", "PATH=/custom/bin"],
            )
            assert context is not None
            self.assertEqual(context.env["KLEA_TEST"], "one")
            self.assertEqual(context.env["PATH"], "/custom/bin")

    def test_no_client_context_returns_none(self):
        self.assertIsNone(_build_client_context("http://127.0.0.1:8005", disabled=True))

    def test_remote_server_returns_none(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertIsNone(_build_client_context("http://10.0.0.1:8005"))
        self.assertIn("only available for a local server", stderr.getvalue())

    def test_invalid_env_option_raises(self):
        with self.assertRaises(typer.BadParameter):
            _parse_env_options(["NOEQUALS"])

    def test_invalid_cwd_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(typer.BadParameter),
        ):
            _build_client_context(
                "http://127.0.0.1:8005", cwd=str(Path(tmp) / "missing")
            )


if __name__ == "__main__":
    unittest.main()
