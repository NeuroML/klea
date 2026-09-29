#!/usr/bin/env python3
"""
Tests for config file resolution (profile lookup).

File: tests/test_config_resolution.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from types import SimpleNamespace

import pytest
from klea_utils.graph.base import BaseLangGraph
from klea_utils.paths import resolve_app_config_path
from pydantic import BaseModel


class TestResolveConfigPath:
    """Unit tests for :func:`klea_utils.paths.resolve_app_config_path`."""

    def test_absolute_path_is_used(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        target = tmp_path / "config.json"
        target.write_text("{}")

        result = resolve_app_config_path(str(target), conf_dir)

        assert result == target

    def test_absolute_missing_path_raises(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()
        target = tmp_path / "missing.json"

        with pytest.raises(FileNotFoundError):
            resolve_app_config_path(str(target), conf_dir)

    def test_cwd_precedence_over_conf_dir(self, tmp_path):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (cwd / "config.json").write_text("cwd")
        (conf_dir / "config.json").write_text("conf")

        result = resolve_app_config_path("config.json", conf_dir, cwd=cwd)

        assert result == cwd / "config.json"

    def test_conf_dir_fallback(self, tmp_path):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (conf_dir / "config.json").write_text("conf")

        result = resolve_app_config_path("config.json", conf_dir, cwd=cwd)

        assert result == conf_dir / "config.json"

    def test_missing_file_raises_helpful_error(self, tmp_path):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="--profile") as excinfo:
            resolve_app_config_path("config.json", conf_dir, cwd=cwd)

        message = str(excinfo.value)
        assert str(cwd) in message
        assert str(conf_dir) in message

    def test_empty_name_raises(self, tmp_path):
        conf_dir = tmp_path / "conf"
        conf_dir.mkdir()

        with pytest.raises(ValueError):
            resolve_app_config_path("", conf_dir)


class ToyConfig(BaseModel):
    """Minimal app config class for graph env loading tests."""

    foo: str = "bar"


class ToyGraph(BaseLangGraph):
    """Minimal graph subclass exposing only ``_load_env``."""

    config_class = ToyConfig
    env_prefix = "TOY_"
    env_var = "TOY_ENV_FILE"
    env_file_default = "toy.env"
    config_file_default = "toy.json"
    graph_name = "toy_graph"

    def _configure_resources(self) -> None:
        pass

    def _setup_models(self) -> None:
        from klea_utils.llm import LLMModel

        self.llm_models = {
            "chat": LLMModel(instance=None, required=True),
        }

    async def _create_graph(self) -> None:
        pass


class NoConfigToyGraph(ToyGraph):
    """Toy graph with no default config file (agent-like)."""

    config_file_default = ""


class TestLoadEnv:
    """Integration tests for ``BaseLangGraph._load_env``."""

    def _make_graph(
        self,
        tmp_path,
        monkeypatch,
        env_file_text,
        cwd,
        conf_dir,
        graph_cls=ToyGraph,
    ):
        env_file = tmp_path / "toy.env"
        env_file.write_text(env_file_text)
        # Ensure no leaked process env affects the test.
        monkeypatch.delenv("TOY_APP_CONFIG_FILE", raising=False)
        graph = graph_cls(logging_level=logging.INFO, checkpoint="none", log_file=False)
        graph.env_file = str(env_file)
        # ``_setup_models`` declares the model roles that ``_load_env`` uses
        # to generate the env schema (as ``setup()`` does in sequence).
        graph._setup_models()
        monkeypatch.setattr(
            graph, "paths", SimpleNamespace(user_config_dir=str(conf_dir))
        )
        monkeypatch.chdir(cwd)
        return graph

    def test_loads_from_conf_dir(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (conf_dir / "toy.json").write_text(json.dumps({"foo": "from-conf-dir"}))

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=toy.json\n",
            cwd,
            conf_dir,
        )
        graph._load_env()

        assert graph.app_config.foo == "from-conf-dir"

    def test_cwd_precedence_over_conf_dir(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (cwd / "toy.json").write_text(json.dumps({"foo": "from-cwd"}))
        (conf_dir / "toy.json").write_text(json.dumps({"foo": "from-conf-dir"}))

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=toy.json\n",
            cwd,
            conf_dir,
        )
        graph._load_env()

        assert graph.app_config.foo == "from-cwd"

    def test_process_env_overrides_env_file(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (conf_dir / "override.json").write_text(json.dumps({"foo": "from-process-env"}))

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=envfile.json\n",
            cwd,
            conf_dir,
        )
        # Set after graph construction: _make_graph clears TOY_APP_CONFIG_FILE
        # to guarantee a clean baseline.
        monkeypatch.setenv("TOY_APP_CONFIG_FILE", "override.json")
        graph._load_env()

        assert graph.app_config.foo == "from-process-env"

    def test_empty_env_file_value_uses_field_default(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (conf_dir / "toy.json").write_text(json.dumps({"foo": "from-default"}))

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=\n",
            cwd,
            conf_dir,
        )
        graph._load_env()

        assert graph.app_config.foo == "from-default"

    def test_missing_config_raises_helpful_error(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=missing.json\n",
            cwd,
            conf_dir,
        )
        with pytest.raises(FileNotFoundError, match="--profile"):
            graph._load_env()

    def test_no_default_config_uses_class_defaults(self, tmp_path, monkeypatch):
        """A graph with no default config starts from ``config_class`` defaults."""
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "",
            cwd,
            conf_dir,
            graph_cls=NoConfigToyGraph,
        )
        graph._load_env()

        assert getattr(graph.app_config, "foo") == "bar"  # noqa: B009

    def test_no_default_config_still_requires_explicit_file(
        self, tmp_path, monkeypatch
    ):
        """An explicitly named config still fails fast when it is missing."""
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()

        graph = self._make_graph(
            tmp_path,
            monkeypatch,
            "TOY_APP_CONFIG_FILE=missing.json\n",
            cwd,
            conf_dir,
            graph_cls=NoConfigToyGraph,
        )
        with pytest.raises(FileNotFoundError, match="--profile"):
            graph._load_env()

    def test_declared_default_config_is_required(self, tmp_path, monkeypatch):
        """A non-empty default config is still required (RAG-like)."""
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()

        graph = self._make_graph(tmp_path, monkeypatch, "", cwd, conf_dir)
        with pytest.raises(FileNotFoundError, match="--profile"):
            graph._load_env()

    def test_missing_env_file_is_optional(self, tmp_path, monkeypatch):
        cwd = tmp_path / "cwd"
        conf_dir = tmp_path / "conf"
        cwd.mkdir()
        conf_dir.mkdir()
        (conf_dir / "toy.json").write_text(json.dumps({"foo": "from-default"}))
        # Ensure no leaked process env affects the test, and prove model env
        # vars are read from the process env when no env file exists.
        monkeypatch.delenv("TOY_APP_CONFIG_FILE", raising=False)
        monkeypatch.setenv("TOY_CHAT_MODEL", "ollama:test")

        graph = ToyGraph(logging_level=logging.INFO, checkpoint="none", log_file=False)
        graph.env_file = str(tmp_path / "nope.env")
        graph._setup_models()
        monkeypatch.setattr(
            graph, "paths", SimpleNamespace(user_config_dir=str(conf_dir))
        )
        monkeypatch.chdir(cwd)

        graph._load_env()

        # ``app_env`` / ``app_config`` are typed ``BaseModel`` on the base
        # class, so read the concrete fields via getattr.
        assert getattr(graph.app_env, "chat_model") == "ollama:test"  # noqa: B009
        assert getattr(graph.app_config, "foo") == "from-default"  # noqa: B009


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
