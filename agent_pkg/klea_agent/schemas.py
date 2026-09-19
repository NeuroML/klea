#!/usr/bin/env python3
"""
Schemas used by the agent

File: klea_agent/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Literal

from fastmcp.client.client import CallToolResult
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.tools import textualize_tool_results
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
    #: Whether this step executes tools or reasons over the evidence
    #: (ADR-0035 update 2026-09-19).  A ``tool`` step names at least one tool
    #: in :attr:`suggested_tools` and runs through the picker/caller; a
    #: ``reasoning`` step names none and is handled by the ``ReasoningNode``,
    #: its conclusion recorded as a :class:`StepOutput`.
    kind: Literal["tool", "reasoning"] = Field(default="tool", validate_default=True)
    suggested_tools: list[str] = Field(default_factory=list)
    depends_on: list[int] = []
    status: Literal["pending", "done", "failed"] = Field(
        default="pending", validate_default=True
    )

    def status_label(self, *, current: bool = False, markdown: bool = False) -> str:
        """Return the step status as a bracketed marker.

        Two styles by audience.  Prompts (``markdown=False``) use plain-word
        markers, which every model reads unambiguously.  The status pane
        (``markdown=True``) uses compact symbols so the markers align and do
        not dominate the line.

        :param current: Whether this is the plan's current step.
        :param markdown: ``True`` for symbol markers (user-facing render),
            ``False`` for word markers (prompt render).
        :returns: Word form ``[DONE]``/``[FAILED]``/``[CURRENT]``/``[PENDING]``
            or symbol form ``[x]``/``[!]``/``[*]``/``[ ]``.
        """
        if markdown:
            if self.status == "done":
                return "[x]"
            if self.status == "failed":
                return "[!]"
            return "[*]" if current else "[ ]"
        if self.status == "done":
            return "[DONE]"
        if self.status == "failed":
            return "[FAILED]"
        return "[CURRENT]" if current else "[PENDING]"

    def render(self, *, current: bool = False, markdown: bool = False) -> str:
        """Render this step as one line with its status marker.

        Two audiences.  Prompts (``markdown=False``) include everything a
        model may need (description, success criteria, suggested tools,
        dependencies).  The status pane (``markdown=True``) stays minimal -
        marker, number, description and dependencies (execution order) - with
        the rest left to the inspection pane.

        :param current: Whether this is the plan's current step.
        :param markdown: ``True`` for the status-pane render: ``[marker] Step
            N: description (depends on: ...)``.  ``False`` for the prompt
            render: ``[STATUS] N. description (success criteria: ...;
            suggested tools: ...; depends on: ...)``, or ``...; kind:
            reasoning; ...`` for a reasoning step (which names no tools).
        :returns: The prompt line, or the minimal status-pane line.
        """
        depends = (
            ", ".join(str(step) for step in self.depends_on)
            if self.depends_on
            else "(none)"
        )
        marker = self.status_label(current=current, markdown=markdown)
        if markdown:
            return (
                f"{marker} Step {self.step_number}: {self.description} "
                f"(depends on: {depends})"
            )
        criteria = self.success_criteria or "(none)"
        if self.kind == "reasoning":
            detail = (
                f"{self.description} (success criteria: {criteria}; "
                f"kind: reasoning; depends on: {depends})"
            )
        else:
            tools = (
                ", ".join(self.suggested_tools) if self.suggested_tools else "(none)"
            )
            detail = (
                f"{self.description} (success criteria: {criteria}; "
                f"suggested tools: {tools}; depends on: {depends})"
            )
        return f"{marker} {self.step_number}. {detail}"


class PlanSchema(BaseModel):
    step_list: list[StepSchema] = Field(default_factory=list)
    #: Lifecycle + routing status.  The Planner writes the entry values
    #: (``in_review`` | ``in_progress`` | ``unplannable``); the Evaluator/budget
    #: guards write the terminal ones.  This single field is the post-Planner
    #: routing source; no separate route flag exists.
    status: Literal[
        "not_started",
        "in_review",
        "in_progress",
        "completed",
        "failed",
        "aborted",
        "unplannable",
    ] = Field(default="not_started", validate_default=True)
    current_step_index: int = 0

    def render(self, *, markdown: bool = False) -> str:
        """Render the plan as text (or the status-pane preformatted block).

        Prompts (``markdown=False``) render each step as ``[STATUS] N.
        description (...)`` with plain-word markers, which every model reads
        unambiguously.  The status pane (``markdown=True``) renders ``[marker]
        Step N: description (...)`` lines with symbol markers, separated by a
        blank line and rendered preformatted so tool names stay literal.

        :param markdown: ``True`` for the status-pane render, ``False`` for
            the prompt render.
        :returns: The rendered plan, or ``"(no plan)"`` when there are no steps.
        """
        if not self.step_list:
            return "(no plan)"
        lines: list[str] = []
        if not markdown:
            lines.append("Steps:")
        for index, step in enumerate(self.step_list):
            current = index == self.current_step_index and step.status == "pending"
            lines.append(step.render(current=current, markdown=markdown))
        separator = "\n\n" if markdown else "\n"
        return separator.join(lines)

    def current_step(self) -> StepSchema | None:
        """Return the plan's current step, or ``None`` when there is none.

        :returns: The step at :attr:`current_step_index`, or ``None`` when the
            plan is empty or the index is out of range.
        """
        if not self.step_list:
            return None
        index = self.current_step_index
        if 0 <= index < len(self.step_list):
            return self.step_list[index]
        return None

    def validate_plan(self) -> list[str]:
        """Return structural-consistency errors for this plan (empty if valid).

        The Planner is the sole author of the plan (including per-step status
        and ``current_step_index``); code does not mutate it.  This check
        enforces the machine-checkable invariants so an inconsistent plan is
        rejected and retried instead of silently corrupting execution:

        * step numbers are positive and unique;
        * ``depends_on`` references only step numbers present in this plan;
        * ``current_step_index`` is within ``[-1, len-1]`` and points at the
          first non-``done`` step (``len(step_list)`` when all are done).

        :returns: A list of human-readable error strings.
        """
        errors: list[str] = []
        numbers = [step.step_number for step in self.step_list]
        seen: set[int] = set()
        for number in numbers:
            if number <= 0:
                errors.append(f"step_number {number} must be positive")
            if number in seen:
                errors.append(f"duplicate step_number {number}")
            seen.add(number)
        for step in self.step_list:
            for dep in step.depends_on:
                if dep not in seen:
                    errors.append(
                        f"step {step.step_number} depends on {dep}, "
                        "which is not a step in this plan"
                    )
        # ``current_step_index`` may legitimately be ``len`` when all steps
        # are done; ``-1`` is accepted as "no current step" for an empty plan.
        if not (-1 <= self.current_step_index <= len(self.step_list)):
            errors.append(
                f"current_step_index {self.current_step_index} is out of range "
                f"for {len(self.step_list)} step(s)"
            )
        return errors


class GoalSchema(BaseModel):
    goal: str = ""
    success_criteria: str = ""


class ArtefactSchema(BaseModel):
    """A durable, session-scoped result (ADR-0029; ADR-0035 update 2026-09-19).

    Artefacts are the session-scoped store that survives across graph runs,
    unlike plan-scoped working memory (``step_outputs``), which
    ``InitGraphState`` clears each run.  ``AnswerFromResults`` auto-persists
    the completed task's deliverable here, and later tasks see it through the
    Planner prompt (rendered by ``KleaAgentState.artefacts_text``).

    Conventions:

    * :attr:`id_` is the stable key: writing a new entry with the same id
      supersedes the previous one, so a refined conclusion replaces rather
      than accumulates.
    * :attr:`type_` is the category, for example ``"hypothesis"``,
      ``"result"`` or ``"reference"``.
    * :attr:`content` holds the concise result itself (text or structured
      data); it is not the full user-facing answer.
    * :attr:`metadata` holds provenance/context: the goal, task/plan id,
      source step, timestamp and references (for example file paths or URLs)
      the result depends on.  External resources are referenced, not copied
      into :attr:`content`.
    """

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

    :attr:`assurance` is the structured result-assurance label (ADR-0030
    invariant 4).  General mode is always ``unverified``; Scientific mode
    becomes ``verified`` only once the ADR-0029 verification workflow is
    enforced, so until then it is also ``unverified`` -- an unverified
    answer is never presented as verified.
    """

    requested: Literal["general", "scientific"] = Field(
        default="general",
        description="Explicit operating-mode request from the caller",
    )
    resolved: Literal["general", "scientific"] = Field(
        default="general",
        description="Resolved operating mode at task entry",
    )
    assurance: Literal["unverified", "verified"] = Field(
        default="unverified",
        description="Result assurance level (ADR-0030 invariant 4)",
    )
    note: str = Field(
        default="",
        description="Explanation when a requested mode cannot run",
    )


class RouteSchema(BaseModel):
    """Entry routing decision made by ``RouteDecision`` (ADR-0035).

    ``chat`` is a self-contained general-conversation/knowledge request that
    the router answers inline (``answer``); ``task`` is anything needing the
    current environment/workspace/session and is handed to the Planner.  The
    default is ``task`` (fail-closed: never answer a world-fact from
    assumption).
    """

    route: Literal["chat", "task"] = Field(
        default="task",
        description="Answer inline (chat) or plan and execute (task)",
    )
    answer: str = Field(default="", description="Inline answer when route is 'chat'")


class PlannerOutput(BaseModel):
    """Structured output of the Planner (ADR-0035).

    The Planner is the task path's brain: it writes the immutable ``goal`` and
    an evolvable ``plan``.  ``plan.status`` carries the outcome: ``in_progress``
    (execute the plan), ``in_review`` (await human review), or ``unplannable``
    (no viable plan).  It never answers the user directly -- chat is handled by
    ``RouteDecision`` and the final reply by ``AnswerFromResults``.
    """

    goal: GoalSchema = GoalSchema()
    plan: PlanSchema = PlanSchema()


class EvaluationSchema(BaseModel):
    """Operational verdict produced by the general Evaluator (ADR-0035).

    The Evaluator judges only: ``evaluation`` is the explicit routing outcome
    and ``reason`` a short justification for inspection.  It never generates
    the user-facing answer -- that is a separate synthesis stage
    (``AnswerFromResults``), keeping evaluation independent of generation.
    """

    evaluation: Literal[
        "step_incomplete",
        "step_done",
        "plan_done",
        "need_replan",
        "abort",
    ] = Field(
        default="plan_done",
        description="Operational routing outcome for the current step/task",
    )
    reason: str = Field(default="", description="Short justification for the verdict")


class StepOutput(BaseModel):
    """A result recorded against a plan step.

    :attr:`result` is either a tool call's :class:`CallToolResult` or, for a
    reasoning step (ADR-0035 update 2026-09-19), the ``ReasoningNode``'s text
    conclusion.  :attr:`tool` is the selected tool's name (``CallToolResult``
    does not carry it) and is empty for a reasoning step.  :attr:`displayed`
    records that the server streamed a display event for this result.  That is
    *intent-to-display*, not a render guarantee: a client may not have shown
    it.  The answer node uses the flag to avoid reprinting results the
    interface already showed.
    """

    result: CallToolResult | str
    tool: str = ""
    displayed: bool = False

    def render(self) -> str:
        """Render this result with its tool/displayed metadata.

        The body is the shared single-result text (no batch header) for a tool
        result, or the conclusion text for a reasoning step; the
        ``### <tool> (displayed_to_user: ...)`` heading replaces the generic
        ``Result i/n`` label so the consumer knows which tool ran (or that the
        entry is reasoning) and whether the interface already showed it.
        """
        if isinstance(self.result, CallToolResult):
            body = textualize_tool_results([self.result], include_header=False).strip()
            tool = self.tool or "(unknown tool)"
        else:
            body = str(self.result).strip()
            tool = self.tool or "reasoning"
        shown = "yes" if self.displayed else "no"
        return f"### {tool} (displayed_to_user: {shown})\n{body}"


class KleaAgentState(BaseGraphSchema):
    """The state of the graph

    Inherits the shared fields (query, messages, tool calls/results, usage
    metrics, summary/message fields) from
    :class:`klea_utils.graph.state.BaseGraphSchema` (ADR-0032) and adds the
    agent-specific ones.
    """

    mode: Mode = Mode()
    route: RouteSchema = RouteSchema()
    evaluation: EvaluationSchema = EvaluationSchema()

    # code string if any
    code: CodeSchema = CodeSchema()

    # planning related
    goal: GoalSchema = GoalSchema()
    plan: PlanSchema = PlanSchema()
    step_outputs: dict[int, list[StepOutput]] = Field(default_factory=dict)
    # per-step re-pick counter for the ADaPT tool-error escalation policy
    tool_retry_counts: dict[int, int] = Field(default_factory=dict)
    # per-step non-advancing evaluation counter (semantic loop budget)
    step_attempt_counts: dict[int, int] = Field(default_factory=dict)
    # number of Planner entries in this run (replan budget)
    plan_revisions: int = 0
    # consecutive empty ToolsPicker selections for the current step (picker
    # retry budget).  Lives in state, not on the shared node instance, so it
    # is thread-isolated (ADR-0033).
    picker_attempts: int = 0
    # the plan step index ``picker_attempts`` belongs to, so a new step
    # resets the empty-selection counter
    picker_step: int = -1
    # number of ToolsPicker -> ToolsCaller dispatch rounds in this run
    # (global backstop; a round may contain several parallel tool calls)
    tool_rounds: int = 0
    # why the run failed or could not be planned (for the failure answer)
    failure_reason: str = ""
    # latest human review input (empty unless a plan is under review)
    human_feedback: str = ""
    # global project discovery information
    # only to be updated if files change
    discovery_persistent: Discovery = Discovery()
    # per step cache
    discovery_per_step: Discovery = Discovery()

    # { id -> artefact }
    artefacts: dict[str, ArtefactSchema] = Field(default_factory=dict)

    def observations_text(self) -> str:
        """Render all recorded step outputs as one text block.

        Groups by step (``Step N:``) and renders each :class:`StepOutput` via
        its :meth:`StepOutput.render`.  Used by the Planner, Evaluator and
        AnswerFromResults so all three see identical observations, including
        the tool that produced each result and whether it was displayed to the
        user.
        """
        parts: list[str] = []
        for step_key, entries in self.step_outputs.items():
            if not entries:
                continue
            rendered = ""
            for ind, entry in enumerate(entries):
                rendered += f"\n\nTool call {ind}: {entry.render()}"
            parts.append(f"Step {step_key}:\n{rendered}")
        return "\n\n".join(parts) if parts else "(no observations)"
