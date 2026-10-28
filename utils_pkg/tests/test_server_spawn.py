#!/usr/bin/env python3
"""
Tests for the shared server launcher helpers.

File: tests/test_server_spawn.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import unittest
from pathlib import Path
from unittest import mock

import httpx
import pytest
import typer
from klea_utils.api.server import configure_profile, is_loopback_host, spawn_server

logger = logging.getLogger(__name__)


class TestConfigureProfile:
    """Tests for the --profile handler used by the serve and client CLIs."""

    def test_none_is_noop(self, monkeypatch):
        monkeypatch.delenv("TEST_APP_CONFIG_FILE", raising=False)
        configure_profile(None, "TEST_APP_CONFIG_FILE", None, None)
        assert "TEST_APP_CONFIG_FILE" not in os.environ

    def test_sets_env_var(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_APP_CONFIG_FILE", raising=False)
        (tmp_path / "config.json").write_text("{}")
        configure_profile("config", "TEST_APP_CONFIG_FILE", tmp_path, None)
        assert os.environ["TEST_APP_CONFIG_FILE"] == "config.json"

    def test_strips_trailing_json(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_APP_CONFIG_FILE", raising=False)
        (tmp_path / "config.json").write_text("{}")
        configure_profile("config.json", "TEST_APP_CONFIG_FILE", tmp_path, None)
        assert os.environ["TEST_APP_CONFIG_FILE"] == "config.json"

    def test_missing_profile_raises_bad_parameter(self, tmp_path):
        with pytest.raises(typer.BadParameter):
            configure_profile("nope", "TEST_APP_CONFIG_FILE", tmp_path, None)

    def test_no_config_dir_skips_validation(self, monkeypatch):
        monkeypatch.delenv("TEST_APP_CONFIG_FILE", raising=False)
        configure_profile("whatever", "TEST_APP_CONFIG_FILE", None, None)
        assert os.environ["TEST_APP_CONFIG_FILE"] == "whatever.json"

    def test_no_env_var_warns(self, tmp_path, capsys):
        (tmp_path / "config.json").write_text("{}")
        configure_profile("config", None, tmp_path, None)
        assert "has no effect" in capsys.readouterr().out

    def test_template_writes_and_exits(self, tmp_path, monkeypatch, capsys):
        monkeypatch.delenv("TEST_APP_CONFIG_FILE", raising=False)
        monkeypatch.chdir(tmp_path)

        def writer(output_dir):
            target = Path(output_dir) / "config.json"
            target.write_text("{}")
            return target

        with pytest.raises(typer.Exit):
            configure_profile("template", "TEST_APP_CONFIG_FILE", tmp_path, writer)
        assert (tmp_path / "config.json").exists()
        assert "TEST_APP_CONFIG_FILE" not in os.environ
        assert "Template config written" in capsys.readouterr().out

    def test_template_without_writer_raises(self, tmp_path):
        with pytest.raises(typer.BadParameter):
            configure_profile("template", "TEST_APP_CONFIG_FILE", tmp_path, None)

    def test_template_refusing_overwrite_raises_bad_parameter(self, tmp_path):
        def writer(output_dir):
            raise FileExistsError("Refusing to overwrite existing config")

        with pytest.raises(typer.BadParameter, match="Refusing to overwrite"):
            configure_profile("template", "TEST_APP_CONFIG_FILE", tmp_path, writer)


class TestIsLoopbackHost(unittest.TestCase):
    def test_loopback_hosts(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(is_loopback_host(host))

    def test_case_insensitive(self):
        self.assertTrue(is_loopback_host("LOCALHOST"))

    def test_non_loopback(self):
        for host in ("192.168.1.10", "0.0.0.0", "example.com"):
            self.assertFalse(is_loopback_host(host))


class TestSpawnServer(unittest.TestCase):
    """Exercise spawn_server with a mocked readiness check and subprocess.

    Three things are mocked so the tests run with no real server, no bound
    ports, and no waiting:

    - ``check_api_is_ready`` (AsyncMock): drives every readiness probe.
    - ``subprocess.Popen``: stands in for the spawned uvicorn process.
    - ``time.sleep``: no-ops the fast-fail window so the tests run instantly.

    The probe sequence is the main thing to get right: spawn_server first
    probes once (reuse check), then polls the process every second in a
    short fast-fail window, and only falls back to the long retrying wait
    if the window expires.  The per-test ``side_effect`` lists below encode
    which probe returns what.
    """

    def setUp(self):
        self.mock_popen = mock.patch(
            "klea_utils.api.server.subprocess.Popen", autospec=True
        ).start()
        self.addCleanup(mock.patch.stopall)
        # Avoid actually sleeping through the fast-fail window.
        self.mock_sleep = mock.patch("klea_utils.api.server.time.sleep").start()
        self.mock_client = mock.patch("klea_utils.api.server.httpx.Client").start()
        self.mock_monotonic = mock.patch("klea_utils.api.server.time.monotonic").start()
        # Default monotonic progression for deadline loop
        self.mock_monotonic.side_effect = lambda: 0  # overridden per-test as needed
        # killpg/getpgid are POSIX-only; create them so Windows can patch too.
        self.mock_killpg = mock.patch(
            "klea_utils.api.server.os.killpg", create=True
        ).start()
        self.mock_getpgid = mock.patch(
            "klea_utils.api.server.os.getpgid", return_value=12345, create=True
        ).start()

    def _proc(self, poll_result=None, returncode=None):
        """A fake Popen: ``poll`` returns *poll_result* (None == alive)."""
        proc = mock.Mock()
        proc.poll.return_value = poll_result
        proc.returncode = returncode
        proc.pid = 12345
        self.mock_popen.return_value = proc
        return proc

    def _client_resp(self, status="ready", raise_error=None):
        """Helper to make httpx.Client.get return value."""
        if raise_error:
            raise raise_error
        mock_resp = mock.Mock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"status": status}
        return mock_resp

    def test_reuses_existing_server(self):
        # The very first probe already reports ready, so spawn_server must
        # yield None (reuse, no lifecycle ownership) and never spawn.
        mock_instance = self.mock_client.return_value.__enter__.return_value
        mock_instance.get.return_value = self._client_resp(status="ready")
        self.mock_monotonic.side_effect = None
        self.mock_monotonic.return_value = 0

        with spawn_server("klea_rag.api.main:app", port=8005) as proc:
            self.assertIsNone(proc)
        self.mock_popen.assert_not_called()

    def test_spawns_and_terminates_on_exit(self):
        self._proc(poll_result=None)
        # Probe 1 (reuse check): down -> spawn.
        # Probe 2 (window iteration 1): still down.
        # Probe 3 (window iteration 2): up -> break out of the window.
        mock_instance = self.mock_client.return_value.__enter__.return_value
        mock_instance.get.side_effect = [
            httpx.ConnectError("down"),
            httpx.ConnectError("down"),
            self._client_resp(status="ready"),
        ]
        self.mock_monotonic.side_effect = None
        self.mock_monotonic.return_value = 0

        with spawn_server("klea_rag.api.main:app", port=8005) as proc:
            self.assertIsNotNone(proc)
            logger.debug("server considered ready inside the with block")

        # Leaving the with block must terminate the spawned process (via killpg).
        self.mock_popen.assert_called_once()
        self.mock_killpg.assert_called()
        logger.debug("spawned process was terminated and reaped on exit")

    def test_fast_fail_on_instant_crash(self):
        # poll() returning non-None simulates the spawned server crashing
        # immediately (bad module path, port already in use).  The fast-fail
        # window must raise right away instead of waiting out the retry
        # budget, and the (already dead) process is not terminated again.
        self._proc(poll_result=3, returncode=3)
        mock_instance = self.mock_client.return_value.__enter__.return_value
        mock_instance.get.side_effect = httpx.ConnectError("down")
        self.mock_monotonic.return_value = 0

        with (
            self.assertRaisesRegex(RuntimeError, "exited immediately"),
            spawn_server("klea_rag.api.main:app", port=8005),
        ):
            pass

        # Already dead, so no terminate (or killpg) should be called
        self.mock_killpg.assert_not_called()
        self.mock_popen.return_value.terminate.assert_not_called()
        logger.debug("instant crash surfaced without terminating the dead process")

    def test_timeout_when_never_ready(self):
        # The server stays alive (poll() is None) but never becomes ready, so
        # every probe fails.  The fast-fail window is exhausted, the long
        # retrying wait gives up after ``timeout``, and spawn_server raises
        # while still cleaning up the spawned process.
        self._proc(poll_result=None)
        mock_instance = self.mock_client.return_value.__enter__.return_value
        mock_instance.get.side_effect = httpx.ConnectError("down")
        # Simulate time progression: start 0, then deadline 1, need monotonic to exceed
        self.mock_monotonic.side_effect = [0, 0, 0, 0, 0, 0, 2]

        with (
            self.assertRaisesRegex(RuntimeError, "did not become ready"),
            spawn_server("klea_rag.api.main:app", port=8005, timeout=1),
        ):
            pass

        # Went through the fast-fail window before giving up, then cleaned up.
        self.mock_sleep.assert_called()
        self.mock_killpg.assert_called()
        logger.debug("timeout surfaced after the fast-fail window; process terminated")


if __name__ == "__main__":
    unittest.main()
