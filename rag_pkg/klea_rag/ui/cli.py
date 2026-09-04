#!/usr/bin/env python3
"""
Cli for klea_rag.

File: klea_rag/ui/cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.paths import get_config_dir
from klea_utils.ui.cli import make_client_app
from platformdirs import PlatformDirs

from klea_rag.config import write_config_template

rag_app = make_client_app(
    label="RAG",
    server_url_default="http://127.0.0.1:8005",
    app_module="klea_rag.api.main:app",
    tui_app_name="klea-rag-tui",
    web_app_name="klea-rag-web",
    web_entry="klea_rag.ui.web.app",
    config_env_var="KLEA_RAG_APP_CONFIG_FILE",
    config_dir=get_config_dir(PlatformDirs("klea-rag")),
    template_writer=write_config_template,
)
