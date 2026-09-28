#!/usr/bin/env python3
"""
Tests for the RAG config module (profile templates).

File: tests/test_config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
from pathlib import Path

import pytest
from klea_rag.config import AppConfig, write_config_template
from klea_rag.ui.cli import rag_app
from klea_utils.stores.utils import DEFAULT_CROSS_ENCODER_MODEL
from typer.testing import CliRunner


class TestWriteConfigTemplate:
    """Unit tests for :func:`klea_rag.config.write_config_template`."""

    def test_roundtrips_through_app_config(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        config = AppConfig.model_validate(data)
        assert config.general.default_k == 5
        assert "ExampleDomain" in config.domains
        domain = config.domains["ExampleDomain"]
        assert domain.description
        assert domain.vector_stores[0].path.startswith("chroma:")
        assert domain.bm25_stores[0].name == "my-docs-bm25"

    def test_bundled_tools_disabled_by_default_for_rag(self):
        from klea_rag.config import GeneralConfig

        assert GeneralConfig().bundled_tools.enabled is False

    def test_template_includes_disabled_bundled_tools(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        assert data["general"]["bundled_tools"]["enabled"] is False

    def test_template_includes_cross_encoder_default(self, tmp_path):
        target = write_config_template(tmp_path)
        data = json.loads(target.read_text())
        assert data["general"]["cross_encoder_model"] == DEFAULT_CROSS_ENCODER_MODEL

    def test_refuses_overwrite(self, tmp_path):
        write_config_template(tmp_path)
        with pytest.raises(FileExistsError):
            write_config_template(tmp_path)

    def test_domain_filter_fields_parse_from_json(self):
        data = {
            "general": {"bundled_tools": {"enabled": False}},
            "domains": {
                "Open Source Brain": {
                    "description": "OSB repos",
                    "filter_fields": [
                        {
                            "name": "repository_type",
                            "description": "hosting type: github, dandi, biomodels, figshare",
                            "value_type": "string",
                        },
                        {
                            "name": "tags",
                            "description": "repository tags",
                            "value_type": "list",
                        },
                    ],
                }
            },
        }
        config = AppConfig.model_validate(data)
        fields = config.domains["Open Source Brain"].filter_fields
        assert [f.name for f in fields] == ["repository_type", "tags"]
        assert fields[1].value_type == "list"

    def test_filter_fields_survive_model_dump_include(self):
        """The rag ``_configure_resources`` include carries filter_fields."""
        data = {
            "general": {"bundled_tools": {"enabled": False}},
            "domains": {
                "Docs": {
                    "description": "papers",
                    "filter_fields": [
                        {
                            "name": "journal",
                            "description": "venue",
                            "value_type": "string",
                        }
                    ],
                }
            },
        }
        config = AppConfig.model_validate(data)
        dumped = config.domains["Docs"].model_dump(
            include={"vector_stores", "bm25_stores", "description", "filter_fields"}
        )
        assert dumped["filter_fields"] == [
            {"name": "journal", "description": "venue", "value_type": "string"}
        ]


class TestProfileTemplateCli:
    """``--profile template`` scaffolds a config through the CLI."""

    def test_cli_template_writes_config(self, tmp_path, monkeypatch):
        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(rag_app, ["cli", "--profile", "template"])
        assert result.exit_code == 0
        assert (Path.cwd() / "klea_rag.json").exists()
        assert "Template config written" in result.output


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
