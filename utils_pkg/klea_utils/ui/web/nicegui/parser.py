#!/usr/bin/env python3
"""
Argparse parser for the Klea NiceGUI frontend entry point.

Provides a standard :mod:`argparse` parser for the arguments passed by
the Typer ``web`` command when it launches ``app.py``.  The entry point
simply calls :func:`make_parser().parse_args()` and picks only the
arguments it needs.

Usage::

    from klea_utils.ui.web.nicegui.parser import make_parser

    args = make_parser("My frontend").parse_args()
    run_app(
        args.title, args.url,
        subtitle=args.subtitle,
        disclaimer=args.disclaimer,
        reload=args.reload,
    )

File: klea_utils/ui/web/nicegui/parser.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import argparse


def make_parser(
    description: str = "Klea web interface",
) -> argparse.ArgumentParser:
    """Return a preconfigured :class:`argparse.ArgumentParser`.

    :param description: Description shown in ``--help``.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("title", help="Application title (shown in header)")
    parser.add_argument(
        "subtitle", nargs="?", default="", help="Text shown next to title in header"
    )
    parser.add_argument("url", help="Backend server URL")
    parser.add_argument(
        "--disclaimer",
        default="",
        help="Disclaimer text shown below the chat area",
    )
    parser.add_argument(
        "--footer",
        default='Powered by <a href="https://github.com/neuroml/klea">Klea</a>',
        help="Footer HTML content",
    )
    parser.add_argument(
        "--nicegui-url",
        default="0.0.0.0:7860",
        help="Host:port to bind the NiceGUI web server to (default: 0.0.0.0:7860)",
    )
    parser.add_argument(
        "--storage-secret",
        default="klea-nicegui-secret-change-me",
        help="NiceGUI storage secret for session persistence",
    )
    parser.add_argument(
        "--favicon",
        default=None,
        help=(
            "Browser-tab icon: a file path, http(s)/data URL, raw SVG or a "
            "single character (default: the bundled Klea icon)"
        ),
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on file changes (supported by nicegui only)",
    )
    parser.add_argument(
        "--app-name",
        default="klea-web",
        help="Log identity for this frontend process (used for the log file name)",
    )
    return parser
