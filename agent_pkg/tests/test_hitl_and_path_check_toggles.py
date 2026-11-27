#!/usr/bin/env python3
"""
Tests for the HITL and path-check toggles across the agent layer.

Covers:

(a) ``AwaitHuman(kind="review")._on_hitl_off`` auto-approves a pending plan
    review when ``hitl_enabled`` is false: the plan moves to ``in_progress``,
    the feedback fields stay empty and the transcript ends with the
    auto-approval message;
(b) ``AwaitHuman(kind="input")._on_hitl_off`` answers a blocked step's
    questions from the template and records that answer in the transcript;
(c) ``_on_hitl_off`` is a no-op (returns ``None``) while HITL is enabled;
(d) the handlers in ``klea_utils.commands.toggles`` write their state field
    for a valid value and return an error message for an invalid one;
(e) ``InitGraphState`` seeds the toggles from the environment, which wins
    over the checkpointed state values;
(f) ``planner.py`` carries the ``hitl_enabled`` guard that returns the
    unplannable outcome.

Notes on what is deliberately not asserted here, and why:

* (a) checks the frontier step's completion only when ``StepSchema``
  exposes a ``status`` field; that field name is not part of the material
  used to write this module, so the assertion is skipped rather than
  guessed when it is absent.
* (d) finds the handlers by name-free introspection of the module (public
  functions defined there) instead of hard-coding names, and accepts the
  first documented value a handler takes.  The "conflicting ceiling"
  interaction is not described by the material used here, so it is not
  asserted.
* (e) does not exercise the client-context tier: that needs a live
  LangGraph runtime.  The full three-tier precedence is covered by the
  parser tests in ``utils_pkg/tests``.

File: tests/test_hitl_and_path_check_toggles.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path

import pytest
from klea_agent.nodes.await_human import AwaitHuman
from klea_agent.nodes.init_graph import InitGraphState
from klea_agent.schemas import KleaAgentState, PlanSchema, StepSchema
from klea_utils.mcp.access import (
    HITL_ENV_VAR,
    PATH_CHECK_ENV_VAR,
    PATH_CHECK_STANCE_ENV_VAR,
    parse_hitl_enabled,
    parse_path_check_mode,
    parse_path_check_stance,
)
from langchain_core.messages import AIMessage, HumanMessage

#: The toggles module is new in this change set; importing it lazily keeps
#: the module collecting cleanly when it is absent.
toggles = pytest.importorskip("klea_utils.commands.toggles")

logger = logging.getLogger(__name__)

#: The transcript line written when HITL is disabled and a review is
#: auto-approved.
AUTO_APPROVED = "Auto-approved (no human available; HITL disabled)."

#: Values the toggle handlers may accept; the first one a handler takes is
#: used for the write test.
VALID_VALUES = (
    "on",
    "off",
    "true",
    "false",
    "1",
    "0",
    "strict",
    "declared",
    "allow",
    "deny",
)

INVALID_VALUE = "zzz-not-a-toggle-value"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_node(kind, monkeypatch):
    """Return an ``AwaitHuman`` node whose stream writes are stubbed out."""
    node = AwaitHuman(logging.getLogger("test"), "Awaiting", kind=kind)
    if hasattr(node, "write_custom_stream"):
        monkeypatch.setattr(node, "write_custom_stream", lambda event: None)
    if not hasattr(node, "_on_hitl_off"):
        pytest.skip("AwaitHuman._on_hitl_off is not available in this build")
    return node


def _field(state, update, name, default=None):
    """Read *name* from the returned update, else from the state.

    ``_on_hitl_off`` may mutate *state* in place or return a state update
    dict; reading the update first and the state second covers both.
    """
    if isinstance(update, dict) and name in update:
        return update[name]
    return getattr(state, name, default)


def _messages(state, update):
    """Return the transcript carried by the update or the state."""
    return list(_field(state, update, "messages") or [])


def _answer_text(human_input, step_number):
    """Return the answer recorded for *step_number* as one string."""
    answer = human_input[step_number]
    if isinstance(answer, (list, tuple)):
        return " ".join(str(part) for part in answer)
    return str(answer)


# ---------------------------------------------------------------------------
# (a) review auto-approval with HITL disabled
# ---------------------------------------------------------------------------


def test_review_auto_approves_when_hitl_disabled(monkeypatch):
    """A pending plan review is auto-approved and the plan starts."""
    node = _make_node("review", monkeypatch)
    plan = PlanSchema(
        status="not_started",
        step_list=[
            StepSchema(
                step_number=1,
                summary="review the plan",
                kind="review",
                success_criteria="plan approved",
            )
        ],
    )
    state = KleaAgentState(
        query="q",
        hitl_enabled=False,
        plan=plan,
        human_feedback="",
        review_progress="",
    )
    assert plan.review_frontier(), "the fixture must expose a review frontier"

    update = node._on_hitl_off(state, {"kind": "review"})

    effective_plan = _field(state, update, "plan")
    assert effective_plan.status == "in_progress"
    assert effective_plan.human_feedback_rounds == 1
    assert _field(state, update, "human_feedback") == ""
    assert _field(state, update, "review_progress") == ""

    messages = _messages(state, update)
    assert messages, "the auto-approval must be recorded in the transcript"
    last = messages[-1]
    assert isinstance(last, AIMessage)
    assert AUTO_APPROVED in str(last.content)

    frontier = effective_plan.step_list[-1]
    assert frontier.status == "done"


# ---------------------------------------------------------------------------
# (b) input auto-answer with HITL disabled
# ---------------------------------------------------------------------------


def test_input_answers_blocked_step_when_hitl_disabled(monkeypatch):
    """A blocked step is answered from the template and recorded."""
    node = _make_node("input", monkeypatch)
    plan = PlanSchema(
        status="needs_input",
        step_list=[
            StepSchema(
                step_number=2,
                summary="deploy",
                success_criteria="deployed",
                needs_input=["Which file?"],
            )
        ],
    )
    state = KleaAgentState(query="q", hitl_enabled=False, plan=plan)

    update = node._on_hitl_off(state, {"kind": "input"})

    human_input = _field(state, update, "human_input") or {}
    assert 2 in human_input, "the blocked step number must be answered"
    answer = _answer_text(human_input, 2)
    assert answer.strip(), "the template answer must not be empty"

    messages = _messages(state, update)
    assert any(
        isinstance(message, HumanMessage) and answer in str(message.content)
        for message in messages
    ), "the template answer must appear in the transcript"


# ---------------------------------------------------------------------------
# (c) no-op while HITL is enabled
# ---------------------------------------------------------------------------


def test_on_hitl_off_is_noop_while_hitl_enabled(monkeypatch):
    """With HITL enabled the auto-reply hook does nothing."""
    node = _make_node("review", monkeypatch)
    state = KleaAgentState(query="q", hitl_enabled=True)

    assert node._on_hitl_off(state, {"kind": "review"}) is None


# ---------------------------------------------------------------------------
# (d) the toggle command handlers
# ---------------------------------------------------------------------------


def _clear_toggle_env(monkeypatch):
    """Remove any ambient toggle configuration for the test."""
    for variable in (
        toggles.HITL_ENV,
        toggles.PATH_CHECK_MODE_ENV,
        toggles.PATH_CHECK_STANCE_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.mark.parametrize(
    ("handler_name", "value", "field", "expected"),
    [
        ("handle_hitl", "off", "hitl_enabled", False),
        ("handle_hitl", "on", "hitl_enabled", True),
        ("handle_pathcheck", "declared", "path_check_mode", "declared"),
        ("handle_pathcheck", "off", "path_check_mode", "off"),
        ("handle_pathcheck_stance", "allow", "path_check_stance", "allow"),
        ("handle_pathcheck_stance", "deny", "path_check_stance", "deny"),
    ],
)
def test_toggle_handler_writes_the_documented_field(
    monkeypatch, handler_name, value, field, expected
):
    """A documented value writes its state field and reports the change."""
    _clear_toggle_env(monkeypatch)
    handler = getattr(toggles, handler_name)

    result = handler(KleaAgentState(query="q"), value)

    assert result.updates == {field: expected}
    assert result.message.strip(), f"{handler_name} reported no message"


@pytest.mark.parametrize(
    ("handler_name", "value"),
    [
        ("handle_hitl", INVALID_VALUE),
        ("handle_pathcheck", INVALID_VALUE),
        ("handle_pathcheck_stance", INVALID_VALUE),
    ],
)
def test_toggle_handler_rejects_an_invalid_value(monkeypatch, handler_name, value):
    """An invalid value yields an error result that names the value."""
    _clear_toggle_env(monkeypatch)
    handler = getattr(toggles, handler_name)

    result = handler(KleaAgentState(query="q"), value)

    assert result.updates == {}
    assert value in result.message


@pytest.mark.parametrize(
    ("handler_name", "variable_name", "ceiling", "value"),
    [
        ("handle_hitl", "HITL_ENV", "on", "off"),
        ("handle_pathcheck", "PATH_CHECK_MODE_ENV", "strict", "off"),
        ("handle_pathcheck_stance", "PATH_CHECK_STANCE_ENV", "deny", "allow"),
    ],
)
def test_toggle_handler_refuses_to_exceed_the_process_ceiling(
    monkeypatch, handler_name, variable_name, ceiling, value
):
    """A process-level ceiling turns the conflicting value into a refusal."""
    _clear_toggle_env(monkeypatch)
    variable = getattr(toggles, variable_name)
    monkeypatch.setenv(variable, ceiling)
    handler = getattr(toggles, handler_name)

    result = handler(KleaAgentState(query="q"), value)

    assert result.updates == {}
    assert variable in result.message


# ---------------------------------------------------------------------------
# (e) InitGraphState seeding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_seeds_toggles_from_env_over_state(monkeypatch, tmp_path):
    """The environment wins over the checkpointed state values."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PATH_CHECK_ENV_VAR, "off")
    monkeypatch.setenv(PATH_CHECK_STANCE_ENV_VAR, "allow")
    monkeypatch.setenv(HITL_ENV_VAR, "0")

    node = InitGraphState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda event: None)

    state = KleaAgentState(
        query="q",
        path_check_mode="declared",
        path_check_stance="deny",
        hitl_enabled=True,
    )

    update = await node.execute(state)

    assert update["path_check_mode"] == "off"
    assert update["path_check_stance"] == "allow"
    assert update["hitl_enabled"] is False


@pytest.mark.asyncio
async def test_init_falls_back_to_documented_defaults(monkeypatch, tmp_path):
    """With nothing configured the documented defaults are seeded."""
    monkeypatch.chdir(tmp_path)
    _clear_toggle_env(monkeypatch)

    node = InitGraphState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda event: None)

    update = await node.execute(KleaAgentState(query="q"))

    assert update["path_check_mode"] == parse_path_check_mode(None)
    assert update["path_check_stance"] == parse_path_check_stance(None)
    assert update["hitl_enabled"] == parse_hitl_enabled(None)


# ---------------------------------------------------------------------------
# (f) planner budget-exhaustion guard
# ---------------------------------------------------------------------------


def test_planner_guards_on_hitl_disabled():
    """``planner.py`` returns the unplannable outcome when HITL is off."""
    planner = (
        Path(__file__).resolve().parents[1] / "klea_agent" / "nodes" / "planner.py"
    )
    assert planner.is_file(), f"missing planner module: {planner}"

    lines = planner.read_text(encoding="utf-8").splitlines()
    hits = [index for index, line in enumerate(lines) if "hitl_enabled" in line]
    assert hits, "planner.py does not reference hitl_enabled"

    guarded = any(
        "unplannable" in "\n".join(lines[index : index + 20]) for index in hits
    )
    assert guarded, "no hitl_enabled guard returning an unplannable outcome"
