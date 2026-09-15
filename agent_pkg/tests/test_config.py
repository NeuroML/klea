#!/usr/bin/env python3
"""
Tests for the agent config module (profile templates).

File: tests/test_config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
from pathlib import Path

import pytest
from klea_agent.config import AppConfig, write_config_template
from klea_agent.ui.cli import agent_app
from typer.testing import CliRunner


class TestWriteConfigTemplate:
    """Unit tests for :func:`klea_agent.config.write_config_template`."""

    def test_roundtrips_through_app_config(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        config = AppConfig.model_validate(data)
        assert config.mcp_servers == {}
        assert config.providers == {}

    def test_bundled_tools_enabled_by_default_for_agent(self):
        config = AppConfig()
        assert config.general.bundled_tools.enabled is True

    def test_access_level_defaults(self):
        """Access level and overrides have safe defaults (ADR-0037)."""
        config = AppConfig()
        assert config.general.access_level == "full"
        assert config.general.tool_access == {}

    def test_template_includes_access_settings(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        assert data["general"]["access_level"] == "full"
        assert data["general"]["tool_access"] == {}

    def test_template_includes_bundled_tools(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        assert data["general"]["bundled_tools"]["enabled"] is True

    def test_refuses_overwrite(self, tmp_path):
        write_config_template(tmp_path)
        with pytest.raises(FileExistsError):
            write_config_template(tmp_path)


class TestProfileTemplateCli:
    """``--profile template`` scaffolds a config through the CLI."""

    def test_cli_template_writes_config(self, tmp_path, monkeypatch):
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(agent_app, ["cli", "--profile", "template"])
        assert result.exit_code == 0
        assert (Path.cwd() / "klea_agent.json").exists()
        assert "Template config written" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
