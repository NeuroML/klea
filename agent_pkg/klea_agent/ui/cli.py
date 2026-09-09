#!/usr/bin/env python3
"""
Cli for klea_agent.

File: klea_agent/ui/cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.paths import get_config_dir
from klea_utils.ui.cli import make_client_app
from platformdirs import PlatformDirs

from klea_agent.config import write_config_template

agent_app = make_client_app(
    label="Agent",
    server_url_default="http://127.0.0.1:8006",
    app_module="klea_agent.api.main:app",
    tui_app_name="klea-tui",
    web_app_name="klea-web",
    web_entry="klea_agent.ui.web.app",
    config_env_var="KLEA_AGENT_APP_CONFIG_FILE",
    config_dir=get_config_dir(PlatformDirs("klea")),
    template_writer=write_config_template,
)
