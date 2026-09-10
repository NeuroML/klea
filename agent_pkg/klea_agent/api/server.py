#!/usr/bin/env python3
"""
Server entry point for the Klea Agent API.

File: klea_agent/api/server.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.api.server import make_serve_app
from klea_utils.paths import get_config_dir
from platformdirs import PlatformDirs

from klea_agent.config import write_config_template

serve_app = make_serve_app(
    "klea_agent.api.main:app",
    default_port=8006,
    config_env_var="KLEA_AGENT_APP_CONFIG_FILE",
    config_dir=get_config_dir(PlatformDirs("klea-agent")),
    template_writer=write_config_template,
)
