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
        "--server",
        "-s",
        "--thread-id",
        "--user-id",
        "--profile",
        "--path-check",
        "--path-check-stance",
        "--hitl",
        "--no-hitl",
        "--unattended",
    ):
        assert option in result.stdout


def test_headless_unattended_exports_all_three_toggles(monkeypatch):
    """``--unattended`` exports the three safety values a batch run needs."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    captured = {}

    def fake_spawn(app_module, **kwargs):
        captured["env"] = dict(os.environ)
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(agent_app, ["headless", "hello", "--unattended"])

    assert result.exit_code == 0, _text(result)
    env = captured["env"]
    assert env["KLEA_AGENT_PATH_CHECK"] == "off"
    assert env["KLEA_AGENT_PATH_CHECK_STANCE"] == "allow"
    assert env["KLEA_AGENT_HITL"] == "off"


@pytest.mark.parametrize(
    ("extra_args", "flag"),
    [
        (["--hitl"], "--hitl"),
        (["--path-check", "strict"], "--path-check"),
        (["--path-check-stance", "deny"], "--path-check-stance"),
    ],
)
def test_headless_unattended_rejects_contradicting_flags(extra_args, flag):
    """A flag that contradicts ``--unattended`` is rejected as a usage error."""
    result = runner.invoke(
        agent_app, ["headless", "hello", "--unattended", *extra_args]
    )

    assert result.exit_code == 2
    text = _text(result)
    assert "--unattended" in text
    assert flag in text


def test_headless_rejects_invalid_path_check():
    result = runner.invoke(agent_app, ["headless", "hello", "--path-check", "bogus"])
    assert result.exit_code != 0
    assert "--path-check" in _text(result)


def test_headless_exports_toggles_to_environment(monkeypatch):
    """The toggles reach the environment a spawned server inherits.

    The command no longer builds the agent graph in-process, so the three
    safety values travel through the environment of the server process; the
    fake spawn is where they are observed.
    """
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    captured = {}

    def fake_spawn(app_module, **kwargs):
        captured["env"] = dict(os.environ)
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)
    originals = {name: os.environ.get(name) for name in TOGGLE_ENV_VARS}
    for name in TOGGLE_ENV_VARS:
        os.environ.pop(name, None)
    try:
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
        env = captured["env"]
        assert env["KLEA_AGENT_PATH_CHECK"] == "strict"
        assert env["KLEA_AGENT_PATH_CHECK_STANCE"] == "deny"
        assert env["KLEA_AGENT_HITL"] == "on"
    finally:
        for name, value in originals.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_headless_no_hitl_exports_off(monkeypatch):
    """``--no-hitl`` exports ``KLEA_AGENT_HITL=off`` for the spawned server."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    captured = {}

    def fake_spawn(app_module, **kwargs):
        captured["env"] = dict(os.environ)
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)
    original = os.environ.get("KLEA_AGENT_HITL")
    os.environ.pop("KLEA_AGENT_HITL", None)
    try:
        result = runner.invoke(agent_app, ["headless", "hello", "--no-hitl"])

        assert result.exit_code == 0
        env = captured["env"]
        assert env["KLEA_AGENT_HITL"] == "off"
    finally:
        if original is None:
            os.environ.pop("KLEA_AGENT_HITL", None)
        else:
            os.environ["KLEA_AGENT_HITL"] = original


def test_headless_omits_env_when_flags_absent(monkeypatch):
    """No toggle flag means none of the three variables is exported."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    captured = {}

    def fake_spawn(app_module, **kwargs):
        captured["env"] = dict(os.environ)
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)
    for name in TOGGLE_ENV_VARS:
        os.environ.pop(name, None)

    result = runner.invoke(agent_app, ["headless", "hello"])

    assert result.exit_code == 0
    env = captured["env"]
    for name in TOGGLE_ENV_VARS:
        assert name not in env


def test_headless_prints_the_answer(monkeypatch):
    """The answer is printed on stdout, so a pipe receives only the answer."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    def fake_spawn(app_module, **kwargs):
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(agent_app, ["headless", "hello"])

    assert result.exit_code == 0
    assert ANSWER in result.stdout


def test_headless_attaches_to_a_remote_server_without_spawning(monkeypatch):
    """A non-loopback ``--server`` is attached to: nothing is spawned.

    ``192.0.2.0/24`` is TEST-NET-1, so the URL can never be a server on this
    machine: the command must use it as-is and hand it, with the chat id and
    the user id, to the shared SSE client.
    """
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    remote_url = "http://192.0.2.10:8006"
    spawns = []
    streams = []

    def fake_spawn(app_module, **kwargs):
        spawns.append((app_module, kwargs))
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        streams.append(
            {
                "query": query,
                "chat_id": chat_id,
                "server_url": server_url,
                **kwargs,
            }
        )
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(
        agent_app,
        [
            "headless",
            "hello",
            "--server",
            remote_url,
            "--user-id",
            "tester",
            "--thread-id",
            "chat-1",
        ],
    )

    assert result.exit_code == 0, _text(result)
    assert spawns == [], "a remote server must not be spawned"
    assert len(streams) == 1
    call = streams[0]
    assert call["query"] == "hello"
    assert call["server_url"] == remote_url
    assert call["chat_id"] == "chat-1"
    assert call["user_id"] == "tester"
    assert ANSWER in result.stdout


def test_headless_spawns_a_local_server(monkeypatch):
    """The default loopback server is spawned through the shared helper."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    spawns = []

    def fake_spawn(app_module, **kwargs):
        spawns.append((app_module, kwargs))
        return nullcontext()

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {"type": "complete", "message_for_user": ANSWER}

    monkeypatch.setattr(server_module, "spawn_server", fake_spawn)
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(agent_app, ["headless", "hello"])

    assert result.exit_code == 0, _text(result)
    assert len(spawns) == 1
    app_module, kwargs = spawns[0]
    assert app_module == "klea_agent.api.main:app"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8006


def test_headless_reports_an_interrupt_and_exits_non_zero(monkeypatch):
    """A paused run exits 1 and reports the ask on stderr."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {
            "type": "interrupt",
            "node": "Plan review",
            "data": {"kind": "review", "interrupt_id": "abc123"},
        }

    monkeypatch.setattr(server_module, "spawn_server", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(agent_app, ["headless", "hello"])

    assert result.exit_code == 1
    text = _text(result)
    assert "abc123" in text
    assert "review" in text


def test_headless_reports_an_error_event_and_exits_non_zero(monkeypatch):
    """An ``error`` event is reported on stderr and exits 1."""
    from contextlib import nullcontext

    import klea_utils.api.server as server_module
    import klea_utils.api.sse as sse_module

    async def fake_stream(query, chat_id, server_url, **kwargs):
        yield {
            "type": "error",
            "message": "boom",
            "error_type": "ValueError",
            "resumable": True,
        }

    monkeypatch.setattr(server_module, "spawn_server", lambda *a, **kw: nullcontext())
    monkeypatch.setattr(sse_module, "stream_events", fake_stream)

    result = runner.invoke(agent_app, ["headless", "hello"])

    assert result.exit_code == 1
    assert "boom" in _text(result)
