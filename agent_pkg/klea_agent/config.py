#!/usr/bin/env python3
"""
Configurations for the API server

See ``devdocs/adr/0015-profile-env-config.md`` and
``devdocs/system/c4-component-agent.md``.

File: klea_agent/config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pathlib import Path
from typing import Any

from klea_utils.mcp.access import AccessLevel, ToolAccessOverride
from klea_utils.mcp.server.config import BundledToolsConfig
from pydantic import BaseModel, Field


class CommandsConfig(BaseModel):
    """Session-command settings (ADR-0047)."""

    #: Command names the operator disables.  A disabled command is not
    #: registered, so it is neither published by ``GET /commands`` nor runnable
    #: (a direct call is treated as unknown).  Names are canonical (lower-case,
    #: without the leading slash).
    disabled: list[str] = Field(
        default_factory=list,
        description="Session-command names to disable",
    )


class GeneralConfig(BaseModel):
    """General, domain-agnostic application settings (agent)."""

    #: The shared bundled tools server is on by default for the agent
    #: (batteries-included research agent); deployers can disable or filter it.
    bundled_tools: BundledToolsConfig = Field(default_factory=BundledToolsConfig)
    #: Session-command settings (ADR-0047).
    commands: CommandsConfig = Field(default_factory=CommandsConfig)
    #: Tool invocation access level (ADR-0037).  ``read_only`` hides and
    #: rejects mutating tools; per-request payloads may override it.
    access_level: AccessLevel = Field(
        default="full",
        description="Tool access level: 'read_only' or 'full'",
    )
    #: Per-tool capability overrides (ADR-0037), applied over MCP annotations
    #: for tools that do not annotate (or annotate inaccurately).
    tool_access: dict[str, ToolAccessOverride] = Field(
        default_factory=dict,
        description="Per-tool read_only/destructive overrides",
    )


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
