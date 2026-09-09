#!/usr/bin/env python3
"""
Klea RAG NiceGUI web entry point.

This file is invoked directly (``python app.py <title> <subtitle>
<server_url> [--reload]``) by the ``web`` subcommand of the ``klea-rag``
CLI (see ``klea_utils.ui.cli``).

File: klea_rag/ui/web/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

# Default the NiceGUI storage path for this app *before* anything imports
# NiceGUI (nicegui/storage.py reads NICEGUI_STORAGE_PATH at import time).
from klea_utils.ui.web.nicegui.entry import default_storage_env

default_storage_env("klea-rag-web")

from klea_utils.ui.web.nicegui.parser import make_parser

from klea_rag.ui.web.page import run_rag_web

# Use the multiprocessing-safe guard so that NiceGUI's file-watch reload
# (which spawns a subprocess where ``__name__`` is ``"__mp_main__"``)
# does not raise a RuntimeError.
if __name__ in {"__main__", "__mp_main__"}:
    args = make_parser("Klea RAG NiceGUI web interface").parse_args()
    run_rag_web(
        title=args.title,
        server_url=args.url,
        subtitle=args.subtitle,
        disclaimer=args.disclaimer,
        footer_text=args.footer,
        reload=args.reload,
        nicegui_url=args.nicegui_url,
        storage_secret=args.storage_secret,
        app_name=args.app_name,
    )
