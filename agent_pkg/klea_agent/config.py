#!/usr/bin/env python3
"""
Configurations for the API server

File: klea_agent/config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pathlib import Path
from typing import Any

from klea_utils.mcp.server.config import BundledToolsConfig
from pydantic import BaseModel, Field


class GeneralConfig(BaseModel):
    """General, domain-agnostic application settings (agent)."""

    #: The shared bundled tools server is on by default for the agent
    #: (batteries-included research agent); deployers can disable or filter it.
    bundled_tools: BundledToolsConfig = Field(default_factory=BundledToolsConfig)


class AppConfig(BaseModel):
    """Application configuration loaded from the JSON config file."""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    mcp_servers: dict[str, Any] = Field(default_factory=dict)
    providers: dict[str, dict[str, dict[str, Any]]] = Field(default_factory=dict)


def write_config_template(output_dir: str | Path) -> Path:
    """Write a scaffold ``klea_agent.json`` into *output_dir*.

    The template is built from the ``AppConfig`` schema defaults so every
    field is present and ready to fill in.  Refuses to overwrite an
    existing file so a real config is never clobbered.

    :param output_dir: Directory to write the template into
    :returns: Path to the written template
    :raises FileExistsError: If the target file already exists
    """
    target = Path(output_dir) / "klea_agent.json"
    if target.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing config: {target}. "
            "Use --profile <name> with a different name instead."
        )
    target.write_text(AppConfig().model_dump_json(exclude_none=True, indent=2) + "\n")
    return target
