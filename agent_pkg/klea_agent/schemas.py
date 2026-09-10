#!/usr/bin/env python3
"""
Schemas used by the agent

File: klea_agent/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Annotated, Literal

from fastmcp.client.client import CallToolResult
from klea_utils.graph.reducers import add_token_usage
from klea_utils.graph.schemas import TokenUsage
from klea_utils.mcp.schemas import ToolCallSchema
from langchain_core.messages import AnyMessage
from pydantic import BaseModel, Field
from typing_extensions import Any


class CodeSchema(BaseModel):
    code: str = ""
    version: int = 0
    patches: list[str] = []


class StepSchema(BaseModel):
    step_number: int = 1
    description: str = ""
    success_criteria: str = ""
    suggested_tools: list[str] = Field(default_factory=list)
    depends_on: list[int] = []
    status: Literal["pending", "done", "failed"] = Field(
        default="pending", validate_default=True
    )

    def status_label(self, *, current: bool = False) -> str:
        """Return the step status as a bracketed text marker.

        Plain-word markers (rather than symbols) are used so every model can
        read the per-step status unambiguously in prompts.

        :param current: Whether this is the plan's current step.
        :returns: ``[DONE]``, ``[FAILED]``, ``[CURRENT]`` or ``[PENDING]``.
        """
        if self.status == "done":
            return "[DONE]"
        if self.status == "failed":
            return "[FAILED]"
        return "[CURRENT]" if current else "[PENDING]"


class PlanSchema(BaseModel):
    step_list: list[StepSchema] = Field(default_factory=list)
    status: Literal["not_started", "in_progress", "completed", "failed", "aborted"] = (
        Field(default="not_started", validate_default=True)
    )
    current_step_index: int = 0

    def render(self, *, markdown: bool = False) -> str:
        """Render the plan as text (or a markdown list) with status markers.

        Each step renders as ``[STATUS] N. description (success criteria: ...)``
        where ``STATUS`` is :meth:`StepSchema.status_label`; the current step is
        marked ``[CURRENT]``.  Used in node prompts and in the status pane, so
        the same rendering is shown to the model and to the user.

        :param markdown: Prefix each line with ``- `` for a markdown list.
        :returns: The rendered plan, or ``"(no plan)"`` when there are no steps.
        """
        if not self.step_list:
            return "(no plan)"
        lines: list[str] = []
        for index, step in enumerate(self.step_list):
            current = index == self.current_step_index and step.status == "pending"
            criteria = step.success_criteria or "(none)"
            line = (
                f"{step.status_label(current=current)} {step.step_number}. "
                f"{step.description} (success criteria: {criteria})"
            )
            lines.append(f"- {line}" if markdown else line)
        return "\n".join(lines)


class GoalSchema(BaseModel):
    goal: str = ""
    success_criteria: str = ""


class ArtefactSchema(BaseModel):
    id_: str = ""
    type_: str = ""
    content: Any
    # mtime!
    metadata: dict[str, Any] = {}


class Discovery(BaseModel):
    # when it was created
    timestamp: int = 0
    # TODO
    # general: files, scripts
    # NeuroML specific: files, semantic info (ions/parameters)
    pass


class Mode(BaseModel):
    """Operating mode of the agent (ADR-0030).

    :attr:`requested` is the caller's ask (injected at invoke via
    ``extra_state``, e.g. ``{"mode": {"requested": "scientific"}}``); the
    :attr:`resolved` mode is decided by :class:`ModeDecision` at task
    entry and is the checkpointed session-mode.  :attr:`note` carries a
    human-readable explanation when a request cannot be honoured as asked
    (e.g. Scientific mode without a curated knowledge source); a
    non-empty ``note`` routes the run to the informing node instead of
    silently downgrading (ADR-0030 no-silent-downgrade).

    Verification/assurance tracking is intentionally deferred to the
    ADR-0029 verification phase; this model only records the mode.
    """

    requested: Literal["general", "scientific"] = Field(
        default="general",
        description="Explicit operating-mode request from the caller",
    )
    resolved: Literal["general", "scientific"] = Field(
        default="general",
        description="Resolved operating mode at task entry",
    )
    note: str = Field(
        default="",
        description="Explanation when a requested mode cannot run",
    )


class RouteSchema(BaseModel):
    """Upfront routing decision made by ``RouteDecision`` (ADR-0035).

    ``answer`` is populated only for the ``answer`` route (the node answers
    inline); ``rationale`` is a short justification kept for inspection.
    """

    route: Literal["answer", "act", "plan"] = Field(
        default="answer",
        description="Answer directly, handle with a single act, or plan",
    )
    answer: str = Field(default="", description="Inline answer when route is 'answer'")
    rationale: str = Field(default="", description="Short justification for the route")


class EvaluationSchema(BaseModel):
    """Operational verdict produced by the general Evaluator (ADR-0035).

    ``next_step`` is the explicit routing outcome; ``reason`` is a short
    justification for inspection.  ``answer`` carries the user-facing answer
    when the Evaluator doubles as answer synthesis (general mode).
    """

    next_step: Literal[
        "step_incomplete",
        "step_done",
        "plan_done",
        "need_replan",
    ] = Field(
        default="plan_done",
        description="Operational routing outcome for the current step/task",
    )
    reason: str = Field(default="", description="Short justification for the verdict")
    answer: str = Field(default="", description="User-facing answer when done")


class KleaAgentState(BaseModel):
    """The state of the graph"""

    query: str = ""
    messages: list[AnyMessage] = Field(default_factory=list)
    guard_decision: str = "safe"
    usage_metrics: Annotated[TokenUsage, add_token_usage] = Field(
        default_factory=TokenUsage
    )
    mode: Mode = Mode()
    route: RouteSchema = RouteSchema()
    evaluation: EvaluationSchema = EvaluationSchema()

    # code string if any
    code: CodeSchema = CodeSchema()

    # planning related
    goal: GoalSchema = GoalSchema()
    plan: PlanSchema = PlanSchema()
    step_outputs: dict[int, list[CallToolResult]] = Field(default_factory=dict)
    # per-step re-pick counter for the ADaPT escalation policy
    step_retry_counts: dict[int, int] = Field(default_factory=dict)
    # global project discovery information
    # only to be updated if files change
    discovery_persistent: Discovery = Discovery()
    # per step cache
    discovery_per_step: Discovery = Discovery()

    # { id -> artefact }
    artefacts: dict[str, ArtefactSchema] = Field(default_factory=dict)

    # summarised version of context so far
    context_summary: str = ""

    # index till which summarised
    summarised_till: int = 0
    message_for_user: str = ""

    # selected tool calls and their results (one call per plan step, kept as
    # a list to share the tool caller/picker nodes with RAG)
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)
