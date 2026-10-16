#!/usr/bin/env python3
"""
Tests for logging setup (plogging).

File: tests/test_plogging.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import tempfile
import unittest
from logging.handlers import RotatingFileHandler

from klea_utils.plogging import (
    KLEA_LOG_LEVEL_ENV,
    KLEA_LOG_NAMESPACES,
    NOISY_LOGGERS,
    LoggerInfoFilter,
    LoggerNotInfoFilter,
    enable_debug_logging,
    resolve_log_level,
    setup_root_logger,
)


class TestSetupRootLogger(unittest.TestCase):
    """Tests for the process-wide root logger configuration."""

    def setUp(self):
        # The root logger is process-global and setup_root_logger is
        # idempotent, so save/restore root state around every test to avoid
        # leaking handlers (and namespace level overrides) into other tests.
        root = logging.getLogger()
        self._saved_handlers = list(root.handlers)
        self._saved_level = root.level
        self._saved_log_level_env = os.environ.get(KLEA_LOG_LEVEL_ENV)
        for handler in list(root.handlers):
            root.removeHandler(handler)
        root.setLevel(logging.WARNING)
        for name in (
            *KLEA_LOG_NAMESPACES,
            "testapp",
            "urllib3",
            "httpx",
            "httpcore",
            "mcp",
            "aiosqlite",
        ):
            logging.getLogger(name).setLevel(logging.NOTSET)

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in self._saved_handlers:
            root.addHandler(handler)
        root.setLevel(self._saved_level)
        if self._saved_log_level_env is None:
            os.environ.pop(KLEA_LOG_LEVEL_ENV, None)
        else:
            os.environ[KLEA_LOG_LEVEL_ENV] = self._saved_log_level_env

    def test_console_handlers_added(self):
        """INFO goes to stdout, other levels to stderr, root at INFO."""
        setup_root_logger("testapp")
        root = logging.getLogger()

        stream_handlers = [
            h for h in root.handlers if isinstance(h, logging.StreamHandler)
        ]
        self.assertEqual(len(stream_handlers), 2)

        info_handlers = [
            h
            for h in stream_handlers
            if any(isinstance(f, LoggerInfoFilter) for f in h.filters)
        ]
        other_handlers = [
            h
            for h in stream_handlers
            if any(isinstance(f, LoggerNotInfoFilter) for f in h.filters)
        ]
        self.assertEqual(len(info_handlers), 1)
        self.assertEqual(len(other_handlers), 1)
        self.assertEqual(root.getEffectiveLevel(), logging.INFO)

    def test_file_handler(self):
        """A rotating file handler is created and receives all levels."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_root_logger("testapp", log_dir=tmpdir)
            root = logging.getLogger()

            file_handlers = [
                h for h in root.handlers if isinstance(h, RotatingFileHandler)
            ]
            self.assertEqual(len(file_handlers), 1)
            self.assertTrue(file_handlers[0].baseFilename.endswith("testapp.log"))
            self.assertEqual(file_handlers[0].level, logging.DEBUG)

            logger = logging.getLogger("klea_utils.test")
            logger.debug("debug line")
            logger.info("info line")
            logger.warning("warning line")
            for handler in file_handlers:
                handler.flush()

            with open(file_handlers[0].baseFilename) as f:
                content = f.read()
            self.assertIn("debug line", content)
            self.assertIn("info line", content)
            self.assertIn("warning line", content)

    def test_no_file_handler_without_log_dir(self):
        """Without log_dir, only the two console handlers are added."""
        setup_root_logger("testapp")
        root = logging.getLogger()
        file_handlers = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        self.assertEqual(len(file_handlers), 0)

    def test_console_false_adds_only_the_file_handler(self):
        """console=False suppresses the stream handlers but keeps the file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            setup_root_logger("testapp", log_dir=tmpdir, console=False)
            root = logging.getLogger()
            stream_handlers = [
                h
                for h in root.handlers
                if isinstance(h, logging.StreamHandler)
                and not isinstance(h, logging.FileHandler)
            ]
            self.assertEqual(stream_handlers, [])
            self.assertEqual(
                len([h for h in root.handlers if isinstance(h, RotatingFileHandler)]),
                1,
            )

    def test_console_false_without_log_dir_discards_output(self):
        """console=False with no log_dir installs a NullHandler (no lastResort)."""
        setup_root_logger("testapp", console=False)
        root = logging.getLogger()
        self.assertEqual(len(root.handlers), 1)
        self.assertIsInstance(root.handlers[0], logging.NullHandler)

    def test_idempotent(self):
        """A second call returns the root logger without adding handlers."""
        root = logging.getLogger()
        setup_root_logger("app1")
        num_handlers = len(root.handlers)

        result = setup_root_logger("app2", log_dir="/tmp/should-not-exist")
        self.assertIs(result, root)
        self.assertEqual(len(root.handlers), num_handlers)

    def test_klea_namespaces_debug(self):
        """Our own logger namespaces are turned up to DEBUG."""
        setup_root_logger("testapp")
        for name in (*KLEA_LOG_NAMESPACES, "testapp"):
            self.assertEqual(logging.getLogger(name).getEffectiveLevel(), logging.DEBUG)

    def test_third_party_inherits_info(self):
        """Non-noisy third-party loggers inherit the root INFO level."""
        setup_root_logger("testapp")
        for name in ("urllib3", "mcp", "aiosqlite"):
            self.assertEqual(logging.getLogger(name).getEffectiveLevel(), logging.INFO)

    def test_noisy_loggers_quieted_to_warning(self):
        """Request-level library loggers default to WARNING."""
        setup_root_logger("testapp")
        for name in NOISY_LOGGERS:
            self.assertEqual(
                logging.getLogger(name).getEffectiveLevel(), logging.WARNING
            )

    def test_noisy_loggers_debug_when_debug_requested(self):
        """A debug run restores the noisy library loggers."""
        os.environ[KLEA_LOG_LEVEL_ENV] = "debug"
        setup_root_logger("testapp")
        for name in NOISY_LOGGERS:
            self.assertEqual(logging.getLogger(name).getEffectiveLevel(), logging.DEBUG)

    def test_third_party_explicit_level_not_overridden(self):
        """An explicitly configured third-party level is left alone."""
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        setup_root_logger("testapp")
        self.assertEqual(logging.getLogger("httpx").getEffectiveLevel(), logging.DEBUG)

    def test_stderr_handler_default_level_is_info(self):
        """The default console stderr level is INFO, not DEBUG."""
        setup_root_logger("testapp")
        root = logging.getLogger()
        other_handler = next(
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and any(isinstance(f, LoggerNotInfoFilter) for f in h.filters)
        )
        self.assertEqual(other_handler.level, logging.INFO)

    def test_stderr_handler_explicit_debug_level_honored(self):
        """stderr_level=DEBUG creates a stderr handler at DEBUG."""
        setup_root_logger("testapp", stderr_level=logging.DEBUG)
        root = logging.getLogger()
        other_handler = next(
            h
            for h in root.handlers
            if isinstance(h, logging.StreamHandler)
            and any(isinstance(f, LoggerNotInfoFilter) for f in h.filters)
        )
        self.assertEqual(other_handler.level, logging.DEBUG)

    def test_resolve_log_level_default_info(self):
        """With no env var and no flag, INFO is returned."""
        os.environ.pop(KLEA_LOG_LEVEL_ENV, None)
        self.assertEqual(resolve_log_level(), logging.INFO)

    def test_resolve_log_level_debug_flag_wins_over_env(self):
        """The --debug flag overrides a KLEA_LOG_LEVEL env value."""
        os.environ[KLEA_LOG_LEVEL_ENV] = "warning"
        self.assertEqual(resolve_log_level(debug=True), logging.DEBUG)

    def test_resolve_log_level_env_names(self):
        """Level names parse case-insensitively."""
        for name, expected in [
            ("debug", logging.DEBUG),
            ("INFO", logging.INFO),
            ("Warning", logging.WARNING),
            ("error", logging.ERROR),
            ("CRITICAL", logging.CRITICAL),
        ]:
            os.environ[KLEA_LOG_LEVEL_ENV] = name
            self.assertEqual(resolve_log_level(), expected)

    def test_resolve_log_level_env_numeric(self):
        """A numeric level string is accepted."""
        os.environ[KLEA_LOG_LEVEL_ENV] = "30"
        self.assertEqual(resolve_log_level(), logging.WARNING)

    def test_resolve_log_level_env_unknown_falls_back_to_info(self):
        """An unknown level value warns and returns INFO."""
        os.environ[KLEA_LOG_LEVEL_ENV] = "bogus"
        self.assertEqual(resolve_log_level(), logging.INFO)

    def test_resolve_log_level_env_out_of_range_numeric(self):
        """A numeric value outside the valid range falls back to INFO."""
        os.environ[KLEA_LOG_LEVEL_ENV] = "999"
        self.assertEqual(resolve_log_level(), logging.INFO)

    def test_enable_debug_logging_sets_env_var(self):
        """enable_debug_logging sets KLEA_LOG_LEVEL to 'debug'."""
        os.environ.pop(KLEA_LOG_LEVEL_ENV, None)
        enable_debug_logging()
        self.assertEqual(os.environ[KLEA_LOG_LEVEL_ENV], "debug")


if __name__ == "__main__":
    unittest.main()
