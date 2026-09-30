#!/usr/bin/env python3
"""
Tests for the NiceGUI favicon wiring.

Covers the ``--favicon`` parser default, the bundled Klea icon, and the
bootstrap forwarding the resolved favicon to ``ui.run``.

File: tests/test_web_favicon.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import unittest
from unittest import mock

from klea_utils.ui.web.nicegui.components import bootstrap
from klea_utils.ui.web.nicegui.parser import make_parser


class TestFaviconParser(unittest.TestCase):
    """The web entry parser accepts ``--favicon`` and defaults to None."""

    def test_default_is_none(self):
        args = make_parser().parse_args(["T", "S", "http://127.0.0.1:8005"])
        self.assertIsNone(args.favicon)

    def test_favicon_parsed(self):
        args = make_parser().parse_args(
            ["T", "S", "http://127.0.0.1:8005", "--favicon", "K"]
        )
        self.assertEqual(args.favicon, "K")


class TestBundledFavicon(unittest.TestCase):
    """The package ships a default favicon."""

    def test_default_favicon_exists(self):
        path = bootstrap.default_favicon_path()
        self.assertIsNotNone(path)
        assert path is not None
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "klea-favicon.png")


class TestBootstrapForwardsFavicon(unittest.TestCase):
    """run_nicegui_server passes the favicon to ui.run."""

    def _run(self, **kwargs):
        with (
            mock.patch.object(bootstrap, "ui") as ui,
            mock.patch.object(bootstrap, "_configure_logging"),
            mock.patch.object(bootstrap, "_configure_storage"),
        ):
            bootstrap.run_nicegui_server(
                "T",
                "http://127.0.0.1:8005",
                page_builder=lambda **_: None,
                **kwargs,
            )
        return ui

    def test_explicit_favicon(self):
        ui = self._run(favicon="/x/y.png")
        self.assertEqual(ui.run.call_args.kwargs["favicon"], "/x/y.png")

    def test_default_favicon_used(self):
        ui = self._run()
        self.assertEqual(
            ui.run.call_args.kwargs["favicon"], bootstrap.default_favicon_path()
        )

    def test_empty_favicon_falls_back_to_default(self):
        ui = self._run(favicon="")
        self.assertEqual(
            ui.run.call_args.kwargs["favicon"], bootstrap.default_favicon_path()
        )


if __name__ == "__main__":
    unittest.main()
