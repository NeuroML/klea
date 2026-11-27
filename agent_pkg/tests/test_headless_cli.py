#!/usr/bin/env python3
# Copyright (c) 2025 Klea. All rights reserved.
"""Tests for the ``klea headless`` subcommand."""

import os
import sys
import types

import pytest
from klea_agent.ui.cli import agent_app
from typer.testing import CliRunner

runner = CliRunner()

#: Environment variables the headless command exports for the graph.
TOGGLE_ENV_VARS = (
    "KLEA_AGENT_PATH_CHECK",
    "KLEA_AGENT_PATH_CHECK_STANCE",
    "KLEA_AGENT_HITL",
)

ANSWER = "the final answer"


class _FakeGraph:
    """Minimal graph stand-in that records the env at construction.

    The headless command builds the real graph lazily and iterates its event
    stream; substituting this class lets the test drive the command end to end
    without a model.  ``captured_env`` is the process environment as seen when
    the graph is instantiated, which is after the toggles are exported, so it
    also proves the export ordering.
    """

    captured_env: dict[str, str] | None = None
    answer: str = ANSWER

    def __init__(self, *args, **kwargs):
        type(self).captured_env = dict(os.environ)

    async def setup(self):
        """Pretend to initialise the graph."""
        return

    async def run_graph_astream_events(self, query, thread_id):
        """Yield a single ``complete`` event carrying the answer."""
        yield {"type": "complete", "message_for_user": type(self).answer}


@pytest.fixture(autouse=True)
def _isolated_toggle_env():
    """Remove the toggle vars before each test and restore them afterwards.

    ``monkeypatch`` cannot track the raw ``os.environ`` writes the command
    performs, so the snapshot/restore is done here to keep the process
    environment clean between tests.
    """
    saved = {name: os.environ.get(name) for name in TOGGLE_ENV_VARS}
    for name in TOGGLE_ENV_VARS:
        os.environ.pop(name, None)
    yield
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture
def fake_agent(monkeypatch):
    """Replace the lazily imported ``KleaAgent`` with :class:`_FakeGraph`."""
    module = types.ModuleType("klea_agent.klea_agent")
    module.KleaAgent = _FakeGraph
    monkeypatch.setitem(sys.modules, "klea_agent.klea_agent", module)
    _FakeGraph.captured_env = None
    _FakeGraph.answer = ANSWER
    return _FakeGraph


def _text(result):
    """Return a result's stdout and stderr as one string for assertions."""
    stderr = getattr(result, "stderr", "") or ""
    return (result.stdout or "") + stderr


def test_headless_help_lists_toggle_options():
    result = runner.invoke(agent_app, ["headless", "--help"])
    assert result.exit_code == 0
    for option in (
        "--thread-id",
        "--path-check",
        "--path-check-stance",
        "--hitl",
        "--no-hitl",
    ):
        assert option in result.stdout


def test_headless_rejects_invalid_path_check():
    result = runner.invoke(agent_app, ["headless", "hello", "--path-check", "bogus"])
    assert result.exit_code != 0
    assert "--path-check" in _text(result)


def test_headless_exports_toggles_to_environment(fake_agent):
    result = runner.invoke(
        agent_app,
        [
            "headless",
            "hello",
            "--path-check",
            "strict",
            "--path-check-stance",
            "deny",
            "--hitl",
        ],
    )
    assert result.exit_code == 0
    env = fake_agent.captured_env
    assert env is not None
    assert env["KLEA_AGENT_PATH_CHECK"] == "strict"
    assert env["KLEA_AGENT_PATH_CHECK_STANCE"] == "deny"
    assert env["KLEA_AGENT_HITL"] == "on"


def test_headless_no_hitl_exports_off(fake_agent):
    result = runner.invoke(agent_app, ["headless", "hello", "--no-hitl"])
    assert result.exit_code == 0
    env = fake_agent.captured_env
    assert env is not None
    assert env["KLEA_AGENT_HITL"] == "off"


def test_headless_omits_env_when_flags_absent(fake_agent):
    result = runner.invoke(agent_app, ["headless", "hello"])
    assert result.exit_code == 0
    env = fake_agent.captured_env
    assert env is not None
    for name in TOGGLE_ENV_VARS:
        assert name not in env


def test_headless_prints_the_answer(fake_agent):
    result = runner.invoke(agent_app, ["headless", "hello"])
    assert result.exit_code == 0
    assert ANSWER in result.stdout
