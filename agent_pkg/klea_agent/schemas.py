#!/usr/bin/env python3
"""
Schemas used by the agent

File: klea_agent/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from graphlib import CycleError, TopologicalSorter
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


class PlannerPlanSchema(BaseModel):
    """The plan as the Planner authors it (ADR-0035).

    The core plan: the steps, the status the Planner may set, and the current
    step index.  This is both the Planner's structured-output contract and the
    base for the runtime :class:`PlanSchema`, which widens ``status`` with the
    lifecycle values written by code and adds the plan-history counters.  All
    shared behaviour (``render``, ``current_step``, ``validate_plan``) lives
    here.
    """

    step_list: list[StepSchema] = Field(default_factory=list)
    #: The statuses the Planner may set:
    #:
    #: * ``in_progress`` -- run the plan (default);
    #: * ``in_review`` -- the plan should be reviewed before it runs;
    #: * ``needs_input`` -- cannot finalise a plan without a missing fact from
    #:   the user; the question is carried in the Planner's ``reason`` and may
    #:   be accompanied by a partial plan;
    #: * ``unplannable`` -- no workable plan with the available tools.
    status: Literal["in_progress", "in_review", "needs_input", "unplannable"] = Field(
        default="in_progress", validate_default=True
    )

    def render(self, *, markdown: bool = False) -> str:
        """Render the plan as text (or the status-pane preformatted block).

        Prompts (``markdown=False``) render each step as ``[STATUS] N.
        description (...)`` with plain-word markers, which every model reads
        unambiguously.  The status pane (``markdown=True``) renders ``[marker]
        Step N: description (...)`` lines with symbol markers, separated by a
        blank line and rendered preformatted so tool names stay literal.

        The next runnable step (the first in the frontier, ADR-0041) is marked
        as current.

        :param markdown: ``True`` for the status-pane render, ``False`` for
            the prompt render.
        :returns: The rendered plan, or ``"(no plan)"`` when there are no steps.
        """
        if not self.step_list:
            return "(no plan)"
        current = self.current_step()
        current_number = current.step_number if current is not None else None
        lines: list[str] = []
        if not markdown:
            lines.append("Steps:")
        for step in self.step_list:
            is_current = step.step_number == current_number and step.status == "pending"
            lines.append(step.render(current=is_current, markdown=markdown))
        separator = "\n\n" if markdown else "\n"
        return separator.join(lines)

    def current_step(self) -> StepSchema | None:
        """Return the next runnable step, or ``None`` when there is none.

        The current step is the first step in the dependency frontier
        (ADR-0041): the first pending step whose dependencies are all ``done``.
        The single-``current_step_index`` cursor is gone; the position is
        derived from per-step statuses and ``depends_on``.

        :returns: The next runnable step, or ``None`` when the plan is empty or
            no pending step is unblocked.
        """
        frontier = self.frontier()
        return frontier[0] if frontier else None

    def frontier(self, max_steps: int | None = None) -> list[StepSchema]:
        """Return the unblocked pending steps, in step order (ADR-0041).

        The dependency frontier used for parallel execution: a step is
        unblocked when every step number in its ``depends_on`` has status
        ``done``.  ``done``/``failed`` steps and steps with unmet dependencies
        are excluded.  ``depends_on: []`` needs nothing, so a step with no
        declared dependencies is unblocked from the start.

        :param max_steps: Optional cap on the number of steps returned (the
            batch-selection cap, ADR-0041).
        :returns: The runnable steps, in ``step_list`` order.
        """
        done = {s.step_number for s in self.step_list if s.status == "done"}
        runnable = [
            s
            for s in self.step_list
            if s.status == "pending" and all(dep in done for dep in s.depends_on)
        ]
        return runnable[:max_steps] if max_steps is not None else runnable

    def validate_plan(self) -> list[str]:
        """Return structural-consistency errors for this plan (empty if valid).

        The Planner is the sole author of the plan (including per-step statuses
        and ``depends_on``); code does not mutate it.  This check enforces the
        machine-checkable invariants so an inconsistent plan is rejected and
        retried instead of silently corrupting execution:

        * step numbers are positive and unique;
        * ``depends_on`` references only step numbers present in this plan, and
          only *earlier* step numbers, so dependencies point one way;
        * the plan is acyclic (``graphlib``); redundant with the
          strictly-earlier rule, kept as a deterministic backstop (ADR-0041).

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
                elif dep >= step.step_number:
                    errors.append(
                        f"step {step.step_number} depends on {dep}, "
                        "which is not an earlier step (dependencies must point "
                        "to earlier step numbers)"
                    )
        # Acyclic backstop; the strictly-earlier rule above already prevents
        # cycles, so this only fires on an inconsistent set of errors.
        try:
            TopologicalSorter(
                {step.step_number: set(step.depends_on) for step in self.step_list}
            ).prepare()
        except CycleError as exc:
            errors.append(f"steps form a dependency cycle: {exc}")
        return errors


class PlanSchema(PlannerPlanSchema):
    """The runtime plan: the authored plan plus lifecycle and history counters.

    Extends :class:`PlannerPlanSchema` with:

    * the widened lifecycle ``status`` written by code (``not_started`` from
      ``InitGraphState``; ``completed``/``failed``/``aborted`` from the
      Evaluator and budget guards) alongside the Planner's entry values.  This
      single field is the post-Planner routing source; no separate route flag
      exists.
    * run-history counters.  These let a downstream node (the answer synthesis)
      be told the final plan is a settled artifact reached through iterations
      *without* being handed the raw feedback or every plan version - feedback is
      only meaningful attached to the version it refers to, which the answer
      does not receive, so a count is the right abstraction.
    """

    status: Literal[
        "not_started",
        "in_review",
        "in_progress",
        "needs_input",
        "completed",
        "failed",
        "aborted",
        "unplannable",
    ] = Field(default="not_started", validate_default=True)

    #: Monotonic count of plans authored by the Planner in this run: ``0`` is
    #: the reset (no plan) state, the first authored plan is ``1``.
    plan_version: int = 0
    #: Monotonic count of human review rounds the Planner has processed.
    human_feedback_rounds: int = 0
    #: Consecutive automated replans since the initial plan or the last human
    #: review (budget; reset to 0 on the first plan and on human review).  This
    #: is the system's own revision count, with no human feedback involved.
    automated_plan_revisions: int = 0

    def revision_summary(self) -> str:
        """Return a context-free plan-history signal for the answer node.

        The answer synthesis must not claim the plan was executed unilaterally
        when it followed iterations or human review, but it must not be handed
        the raw feedback either: feedback is only meaningful attached to the
        plan version it refers to, which the answer does not receive.  A count
        is the right abstraction - it signals that the final plan is a settled
        artifact without pretending to explain the iterations.

        :returns: A one-sentence summary, or ``""`` when the plan was authored
            once with no human review (nothing to signal).
        """
        if self.plan_version <= 1 and self.human_feedback_rounds == 0:
            return ""
        return (
            f"This final plan is version {self.plan_version}, reached after "
            f"{self.human_feedback_rounds} human review round(s)."
        )


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

    :attr:`plan` is a :class:`PlannerPlanSchema` so the model only sees the
    statuses it may set.  :attr:`reason` is a free-form place for the Planner's
    own reasoning/justification; it becomes the failure explanation when the
    plan is ``unplannable`` and is recorded in the run history otherwise.
    """

    goal: GoalSchema = GoalSchema()
    plan: PlannerPlanSchema = PlannerPlanSchema()
    reason: str = ""


class ReasoningSchema(BaseModel):
    """Structured output of a reasoning step (ADR-0035 update 2026-09-19).

    A reasoning step produces a conclusion from what is already known
    (interpretation, decision, hypothesis, design, synthesis).  It is
    deliberately general -- not every conclusion has evidence references or a
    confidence -- so only the conclusion and a short rationale are required.
    The conclusion is recorded as a :class:`StepOutput` (like a tool result) so
    later steps and the Planner see it in ``observations``.
    """

    conclusion: str = ""
    rationale: str = ""


class StepEvaluation(BaseModel):
    """A verdict for one plan step in the evaluated batch (ADR-0041)."""

    verdict: Literal["step_done", "step_incomplete", "need_replan"] = Field(
        default="step_done",
        description="Outcome for this step",
    )
    reason: str = Field(default="", description="Short justification for the verdict")


class EvaluationSchema(BaseModel):
    """Per-step verdicts from the general Evaluator (ADR-0035/ADR-0041).

    The Evaluator judges only: ``evaluations`` maps a 1-based plan step number
    to its :class:`StepEvaluation`, and ``overall`` carries a whole-plan
    outcome (``plan_done`` when the goal is met, ``abort`` when it cannot be
    achieved) while ``reason`` explains it.  ``overall`` is empty while work
    continues.  The Evaluator never generates the user-facing answer -- that is
    a separate synthesis stage (``AnswerFromResults``), keeping evaluation
    independent of generation.
    """

    evaluations: dict[int, StepEvaluation] = Field(
        default_factory=dict,
        description="Verdicts keyed by 1-based plan step number",
    )
    overall: Literal["", "plan_done", "abort"] = Field(
        default="",
        description="Whole-plan outcome; empty while work continues",
    )
    reason: str = Field(default="", description="Short justification for `overall`")


class StepOutput(BaseModel):
    """A result recorded against a plan step.

    :attr:`result` is either a tool call's :class:`CallToolResult` or, for a
    reasoning step (ADR-0035 update 2026-09-19), the ``ReasoningNode``'s text
    conclusion.  :attr:`rationale` carries the reasoning step's justification
    (empty for tool results): the ``ReasoningNode`` prompt keeps the conclusion
    concise and the justification separate, so it must be surfaced here for the
    Evaluator and Planner, which read ``observations`` and never see the node's
    raw ``rationale`` field otherwise.  :attr:`tool` is the selected tool's name
    (``CallToolResult`` does not carry it) and is empty for a reasoning step.
    :attr:`displayed` records that the server streamed a display event for this
    result.  That is *intent-to-display*, not a render guarantee: a client may
    not have shown it.  The answer node uses the flag to avoid reprinting
    results the interface already showed.
    """

    result: CallToolResult | str
    tool: str = ""
    displayed: bool = False
    rationale: str = ""

    def render(self) -> str:
        """Render this result with its tool/displayed metadata.

        The body is the shared single-result text (no batch header) for a tool
        result, or the conclusion text for a reasoning step; a reasoning step's
        ``rationale`` is appended so the justification is visible in
        ``observations``.  The ``### <tool> (displayed_to_user: ...)`` heading
        replaces the generic ``Result i/n`` label so the consumer knows which
        tool ran (or that the entry is reasoning) and whether the interface
        already showed it.
        """
        if isinstance(self.result, CallToolResult):
            body = textualize_tool_results([self.result], include_header=False).strip()
            tool = self.tool or "(unknown tool)"
        else:
            body = str(self.result).strip()
            tool = self.tool or "reasoning"
        if self.rationale.strip():
            body = f"{body}\n\nRationale: {self.rationale.strip()}"
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

    # --- Transient node-to-node signals --------------------------------
    # Carry information from one node to the next and are consumed (cleared)
    # within the run; not durable run state and not rendered into downstream
    # prompts.  The plan's counters are the durable history signal instead.
    #
    #: Latest human review input (empty unless a plan is under review).  Written
    #: by ``AwaitReview``; read and cleared by the Planner, which interprets it
    #: and records the round on ``plan.human_feedback_rounds``.
    human_feedback: str = ""
    #: Why the Planner is being re-entered for an automated replan: set by the
    #: Evaluator on ``need_replan`` and by the tool-round recorder when a batch
    #: had a failed call; read and cleared by the Planner.  Empty on the first
    #: plan and after a human review (which supplies ``human_feedback``).
    replan_reason: str = ""
    # the question the Planner needs answered when ``plan.status`` is
    # ``needs_input`` (carried from the Planner's ``reason``); presented by
    # ``AnswerFromResults`` and, once the HITL interrupt lands, answered by the
    # user to resume the run.
    pending_question: str = ""
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

    def artefacts_text(self) -> str:
        """Render the session-scoped artefacts as one text block.

        Used by the Planner so a later task can build on earlier tasks'
        durable results.  Each artefact is rendered with its id, type and
        metadata (provenance/references) so the model can cite them; content is
        the concise result.  Returns ``"(no artefacts)"`` when the session has
        none, so the prompt always has a value.
        """
        if not self.artefacts:
            return "(no artefacts)"
        parts: list[str] = []
        for artefact_id, artefact in self.artefacts.items():
            key = artefact.id_ or artefact_id
            header = f"{key} (type: {artefact.type_ or 'unknown'})"
            lines = [header, str(artefact.content)]
            if artefact.metadata:
                rendered_meta = "; ".join(
                    f"{name}={value}" for name, value in artefact.metadata.items()
                )
                lines.append(f"metadata: {rendered_meta}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)
