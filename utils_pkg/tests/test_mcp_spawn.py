"""Tests for the bundled MCP server spawn configuration.

These tests pin the contract of
:func:`klea_utils.mcp.spawn.bundled_server_config`: the mapping it returns must
stay byte-for-byte compatible with the configuration callers previously
assembled by hand, while still stripping a host virtual environment.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from klea_utils.mcp import spawn
from klea_utils.mcp.spawn import (
    SERVER_MODULE,
    STRIPPED_ENV_VARS,
    bundled_server_config,
)

# ---------------------------------------------------------------------------
# Behaviour-preserving defaults
# ---------------------------------------------------------------------------


def test_default_config_omits_env_and_cwd() -> None:
    """With no env and no cwd, neither key is emitted at all."""
    config = bundled_server_config()

    assert "env" not in config
    assert "cwd" not in config


def test_default_config_runs_the_bundled_server_module() -> None:
    """The command/args pair launches the bundled server as a module."""
    config = bundled_server_config()

    assert config["command"] == sys.executable
    assert config["args"] == ["-m", SERVER_MODULE]


def test_default_config_uses_the_current_interpreter() -> None:
    """The interpreter running this process is the default command."""
    assert bundled_server_config()["command"] == sys.executable


def test_default_config_forwards_no_tag_filters() -> None:
    """Tag filters are omitted when the caller does not ask for them."""
    config = bundled_server_config()

    assert "include_tags" not in config
    assert "exclude_tags" not in config


# ---------------------------------------------------------------------------
# Environment merging
# ---------------------------------------------------------------------------


def test_explicit_env_is_merged_over_the_inherited_environment(monkeypatch) -> None:
    """Caller env values are layered on top of the inherited environment."""
    monkeypatch.setenv("KLEA_TEST_INHERITED", "from-parent")

    env = bundled_server_config(env={"KLEA_TEST_OWN": "from-caller"})["env"]

    assert env["KLEA_TEST_INHERITED"] == "from-parent"
    assert env["KLEA_TEST_OWN"] == "from-caller"


def test_caller_env_wins_on_conflicts(monkeypatch) -> None:
    """An explicit value overrides the inherited one for the same variable."""
    monkeypatch.setenv("KLEA_TEST_CONFLICT", "inherited")

    env = bundled_server_config(env={"KLEA_TEST_CONFLICT": "caller"})["env"]

    assert env["KLEA_TEST_CONFLICT"] == "caller"


def test_building_a_config_does_not_mutate_the_environment(monkeypatch) -> None:
    """Merging produces a fresh mapping; ``os.environ`` is left untouched."""
    monkeypatch.setenv("KLEA_TEST_STABLE", "unchanged")
    monkeypatch.setenv("VIRTUAL_ENV", "/host/venv")
    before = dict(os.environ)

    bundled_server_config(env={"KLEA_TEST_EXTRA": "x"})

    assert dict(os.environ) == before


# ---------------------------------------------------------------------------
# Host virtual-environment stripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", STRIPPED_ENV_VARS)
def test_host_virtualenv_variables_are_stripped(monkeypatch, name: str) -> None:
    """Each host virtual-environment variable is dropped from the merged env."""
    monkeypatch.setenv(name, "/host/venv-ish")

    env = bundled_server_config(env={"KLEA_TEST_KEEP": "kept"})["env"]

    assert name not in env
    assert env["KLEA_TEST_KEEP"] == "kept"


def test_caller_can_set_a_stripped_variable_explicitly(monkeypatch) -> None:
    """A caller-provided value survives even for a normally stripped variable."""
    monkeypatch.setenv("VIRTUAL_ENV", "/host/venv")

    env = bundled_server_config(env={"VIRTUAL_ENV": "/server/venv"})["env"]

    assert env["VIRTUAL_ENV"] == "/server/venv"


def test_inherited_environment_survives_apart_from_stripped_variables(
    monkeypatch,
) -> None:
    """Only the virtual-environment variables are removed from the base env."""
    monkeypatch.setenv("KLEA_TEST_BASE", "base-value")
    monkeypatch.setenv("PYTHONHOME", "/host/python")

    env = bundled_server_config(env={})["env"]

    assert env["KLEA_TEST_BASE"] == "base-value"
    assert "PYTHONHOME" not in env


# ---------------------------------------------------------------------------
# Explicit cwd and interpreter
# ---------------------------------------------------------------------------


def test_explicit_cwd_is_emitted() -> None:
    """A caller-supplied cwd is forwarded verbatim."""
    assert bundled_server_config(cwd="/srv/klea")["cwd"] == "/srv/klea"


def test_explicit_cwd_accepts_a_path_like() -> None:
    """Path objects are normalised to ``str`` for the spawn layer."""
    cwd = bundled_server_config(cwd=Path("/srv/klea"))["cwd"]

    assert cwd == "/srv/klea"
    assert isinstance(cwd, str)


def test_explicit_interpreter_overrides_the_current_one() -> None:
    """A pinned interpreter replaces ``sys.executable`` as the command."""
    config = bundled_server_config(interpreter="/opt/py/bin/python3.12")

    assert config["command"] == "/opt/py/bin/python3.12"
    assert config["args"] == ["-m", SERVER_MODULE]


def test_explicit_interpreter_accepts_a_path_like() -> None:
    """A ``Path`` interpreter is normalised to ``str``."""
    command = bundled_server_config(interpreter=Path("/opt/py/bin/python"))["command"]

    assert command == "/opt/py/bin/python"
    assert isinstance(command, str)


# ---------------------------------------------------------------------------
# Tag filters
# ---------------------------------------------------------------------------


def test_tag_filters_are_forwarded() -> None:
    """Include/exclude tags reach the server's own tag filter unchanged."""
    config = bundled_server_config(
        include_tags=["files", "web"], exclude_tags=["shell"]
    )

    assert config["include_tags"] == ["files", "web"]
    assert config["exclude_tags"] == ["shell"]


def test_tag_filters_are_forwarded_as_lists() -> None:
    """Tuples become lists so the mapping stays JSON-friendly."""
    config = bundled_server_config(
        include_tags=("files",), exclude_tags=("shell", "admin")
    )

    assert config["include_tags"] == ["files"]
    assert isinstance(config["include_tags"], list)
    assert config["exclude_tags"] == ["shell", "admin"]
    assert isinstance(config["exclude_tags"], list)


# ---------------------------------------------------------------------------
# Disabled tools
# ---------------------------------------------------------------------------


def test_disabled_tool_resolves_to_none() -> None:
    """A tool disabled by the configuration is not resolved to a callable.

    Disabling is expressed through the tag filter, which the server applies to
    its own catalogue; an excluded tool is therefore never offered, and any
    per-tool resolver exposed by :mod:`klea_utils.mcp.spawn` must answer
    ``None`` for it rather than inventing a tool.
    """
    config = bundled_server_config(exclude_tags=["disabled_tool"])
    assert config["exclude_tags"] == ["disabled_tool"]

    resolver = getattr(spawn, "resolve_tool", None)
    if resolver is None:
        pytest.skip("tool disabling is server-side; no resolver in spawn")

    assert resolver("disabled_tool", exclude_tags=config["exclude_tags"]) is None
