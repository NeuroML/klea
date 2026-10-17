#!/usr/bin/env python3
"""
Schemas used by the agent

File: klea_agent/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from graphlib import CycleError, TopologicalSorter
from typing import Any, Literal

from fastmcp.client.client import CallToolResult
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.tools import last_tool_error_text, textualize_tool_results
from pydantic import BaseModel, Field


class CodeSchema(BaseModel):
    code: str = ""
    version: int = 0
    patches: list[str] = []


class StepSchema(BaseModel):
    step_number: int = 1
    description: str = ""
    success_criteria: str = ""
    #: How this step executes (ADR-0035 update 2026-09-19; ADR-0051).  A
    #: ``tool`` step names at least one tool in :attr:`suggested_tools` and
    #: runs through the picker/caller; a ``reasoning`` step names none and is
    #: handled by the ``ReasoningNode``, its conclusion recorded as a
    #: :class:`StepOutput`; a ``review`` step is a human checkpoint -- its
    #: "execution" is the user's approve/revise decision at that point in the
    #: plan (whole-plan review is a ``review`` step with no ``depends_on``;
    #: "review after step N" is a ``review`` step depending on ``N``).  It
    #: names no tools and is not judged by the Evaluator.
    kind: Literal["tool", "reasoning", "review"] = Field(
        default="tool", validate_default=True
    )
    suggested_tools: list[str] = Field(default_factory=list)
    depends_on: list[int] = []
    status: Literal["pending", "done", "skipped", "failed"] = Field(
        default="pending", validate_default=True
    )
    #: Questions this step needs the user to answer before it can run
    #: (ADR-0046).  A step with questions is *blocked*: it is part of a draft
    #: plan and is not executed (and is exempt from the executable-step checks)
    #: until the Planner re-authors it without questions.  Empty once runnable.
    needs_input: list[str] = Field(default_factory=list)

    def status_label(self, *, current: bool = False, markdown: bool = False) -> str:
        """Return the step status as a bracketed marker.

        Two styles by audience.  Prompts (``markdown=False``) use plain-word
        markers, which every model reads unambiguously.  The status pane
        (``markdown=True``) uses compact symbols so the markers align and do
        not dominate the line.

        :param current: Whether this is the plan's current step.
        :param markdown: ``True`` for symbol markers (user-facing render),
            ``False`` for word markers (prompt render).
        :returns: Word form ``[DONE]``/``[SKIPPED]``/``[FAILED]``/``[CURRENT]``/
            ``[PENDING]`` or symbol form ``[x]``/``[-]``/``[!]``/``[*]``/``[ ]``.
        """
        if markdown:
            if self.status == "done":
                return "[x]"
            if self.status == "skipped":
                return "[-]"
            if self.status == "failed":
                return "[!]"
            return "[*]" if current else "[ ]"
        if self.status == "done":
            return "[DONE]"
        if self.status == "skipped":
            return "[SKIPPED]"
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
        if self.kind == "review":
            detail = f"{self.description} (kind: review; depends on: {depends})"
        elif self.kind == "reasoning":
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
    # The plan as the Planner authors it: the steps and the status the Planner
    # may set.  Base for the runtime PlanSchema (which widens the status with
    # lifecycle values and adds the plan-history counters); render/current_step/
    # validate_plan live here.
    step_list: list[StepSchema] = Field(default_factory=list)
    #: The statuses the Planner may set:
    #:
    #: * ``in_progress`` -- run the plan (default).  A plan that needs human
    #:   review carries a ``review`` step at the relevant position instead of
    #:   a special status (ADR-0051);
    #: * ``needs_input`` -- cannot finalise a plan without a missing fact from
    #:   the user; the questions are carried on the blocked step(s) and the
    #:   run pauses to ask;
    #: * ``no_plan_required`` -- no execution steps are needed: the goal is
    #:   already satisfiable from the available information (for example the
    #:   session ``artefacts``).  A success, distinct from ``unplannable`` (the
    #:   goal cannot be met); the answer-composer writes the reply.  The Planner
    #:   still sets the goal.
    #: * ``unplannable`` -- no workable plan with the available tools.
    status: Literal["in_progress", "needs_input", "no_plan_required", "unplannable"] = (
        Field(default="in_progress", validate_default=True)
    )

    def render(
        self, *, markdown: bool = False, current_numbers: set[int] | None = None
    ) -> str:
        """Render the plan as text (or the status-pane preformatted block).

        Prompts (``markdown=False``) render each step as ``[STATUS] N.
        description (...)`` with plain-word markers, which every model reads
        unambiguously.  The status pane (``markdown=True``) renders ``[marker]
        Step N: description (...)`` lines with symbol markers, separated by a
        blank line and rendered preformatted so tool names stay literal.

        Steps whose number is in *current_numbers* are marked as current.  By
        default that is the next runnable step (the first in the frontier); a
        node judging a whole batch passes the batch's step numbers instead
        (ADR-0041).

        :param markdown: ``True`` for the status-pane render, ``False`` for
            the prompt render.
        :param current_numbers: Step numbers to mark as current; defaults to
            the next runnable step.
        :returns: The rendered plan, or ``"(no plan)"`` when there are no steps.
        """
        if not self.step_list:
            return "(no plan)"
        if current_numbers is None:
            current = self.current_step()
            current_numbers = {current.step_number} if current is not None else set()
        lines: list[str] = []
        if not markdown:
            lines.append("Steps:")
        for step in self.step_list:
            is_current = (
                step.step_number in current_numbers and step.status == "pending"
            )
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
        ``done`` or ``skipped`` (both resolved).  ``done``/``skipped``/
        ``failed`` steps and steps with unmet dependencies are excluded.
        ``depends_on: []`` needs nothing, so a step with no declared
        dependencies is unblocked from the start.

        :param max_steps: Optional cap on the number of steps returned (the
            batch-selection cap, ADR-0041).
        :returns: The runnable steps, in ``step_list`` order.
        """
        resolved = {
            s.step_number for s in self.step_list if s.status in ("done", "skipped")
        }
        runnable = [
            s
            for s in self.step_list
            if s.status == "pending" and all(dep in resolved for dep in s.depends_on)
        ]
        return runnable[:max_steps] if max_steps is not None else runnable

    def review_frontier(self) -> list[StepSchema]:
        """Return the pending ``review`` steps at the head of the frontier.

        A ``review`` step is a human checkpoint (ADR-0051): its "execution" is
        the user's approve/revise decision.  Because ``next_batch`` returns the
        maximal same-kind prefix of the frontier, the review steps at the head
        form their own batch; this returns them so routing can pause and the
        checkpoint can be resolved.  Returns ``[]`` when the frontier head is
        not a review step.

        :returns: The pending review steps at the frontier head, in order.
        """
        frontier = self.frontier()
        if not frontier or frontier[0].kind != "review":
            return []
        return [step for step in frontier if step.kind == "review"]

    def next_batch(self, max_steps: int | None = None) -> list[StepSchema]:
        """Return the maximal same-kind prefix of the frontier (ADR-0041).

        Parallel work is bound in one batch, but a batch must be uniform: tool
        steps go through the picker/caller, reasoning steps through the
        reasoning node, so only a run of steps of the same ``kind`` (from the
        start of the frontier) is returned.  A mixed frontier is therefore
        drained one kind at a time, in step order.

        :param max_steps: Optional cap on the batch size.
        :returns: The batch steps, in step order (possibly empty).
        """
        frontier = self.frontier()
        if not frontier:
            return []
        kind = frontier[0].kind
        batch: list[StepSchema] = []
        for step in frontier:
            if step.kind != kind:
                break
            batch.append(step)
            if max_steps is not None and len(batch) >= max_steps:
                break
        return batch

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
        "in_progress",
        "needs_input",
        "no_plan_required",
        "completed",
        "failed",
        "aborted",
        "user_cancelled",
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

    def reviewed_through(self) -> int:
        """Return the highest approved review step number (0 when none).

        A ``review`` step's "execution" is the user's decision (ADR-0051), so a
        ``done`` review step has been approved and the work it gated has been
        reviewed.  The highest such step number scopes the review-progress
        digest to work completed **since the last review**; no separate marker
        is needed because the resolved gate already records the position.

        :returns: The highest done review step number, or ``0`` when no review
            has been completed yet.
        """
        return max(
            (
                step.step_number
                for step in self.step_list
                if step.kind == "review" and step.status == "done"
            ),
            default=0,
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


class DiscoveryItem(BaseModel):
    """One piece of project context gathered for the session.

    :attr:`source` names where it came from (for example ``AGENTS.md``) and is
    used as the block heading when rendered; :attr:`content` is the text.
    """

    source: str = ""
    content: str = ""


class Discovery(BaseModel):
    """Project-wide context gathered for the session (ADR-0035).

    Session-scoped: preserved by ``InitGraphState`` across turns and refreshed
    only when a source file changes (``timestamp`` records the last source's
    mtime).  Rendered into the Planner and answer-composer prompts via
    :meth:`render`; a later task can build on it (files, scripts, semantic
    info) without re-discovering the project.
    """

    #: mtime of the last-refreshed source file (0 = never refreshed).
    timestamp: int = 0
    items: list[DiscoveryItem] = Field(default_factory=list)

    def upsert(self, source: str, content: str) -> None:
        """Add or replace the item for *source*."""
        for item in self.items:
            if item.source == source:
                item.content = content
                return
        self.items.append(DiscoveryItem(source=source, content=content))

    def render(self) -> str:
        """Render every item as prompt-ready text, or a placeholder when empty."""
        if not self.items:
            return "(no project context available)"
        return "\n\n".join(
            f"### {item.source or 'Project context'}\n\n{item.content}".rstrip()
            for item in self.items
        )


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
        description="Result assurance level",
    )
    note: str = Field(
        default="",
        description="Explanation when a requested mode cannot run",
    )


class RouteSchema(BaseModel):
    # Entry routing decision made by the route-decision node.  Semantics and
    # rules live in RouteDecision_system.md.
    route: Literal["chat", "task"] = "task"
    answer: str = ""


class PlannerOutput(BaseModel):
    # The Planner's structured output: the immutable goal and an evolvable
    # plan.  Status meanings live in Planner_system.md.
    goal: GoalSchema = GoalSchema()
    plan: PlannerPlanSchema = PlannerPlanSchema()
    reason: str = ""


class ReasoningSchema(BaseModel):
    # A reasoning step's conclusion and short rationale.  Semantics live in
    # ReasoningNode_system.md.
    conclusion: str = ""
    rationale: str = ""


class StepEvaluation(BaseModel):
    # One judged step's verdict.  Verdict meanings live in
    # OperationalEvaluator_system.md.  A typed list (not a step_number-keyed
    # dict) is required: a dynamic-key object is closed to ``{}`` by strict
    # structured-output modes (ADR-0044).  ``step_skipped`` resolves a step
    # that is not needed to meet the goal (so ``plan_done`` may be set with
    # no pending step left).
    step_number: int = 0
    verdict: Literal["step_done", "step_skipped", "step_incomplete", "need_replan"] = (
        "step_done"
    )
    reason: str = ""


class EvaluationSchema(BaseModel):
    # The Evaluator's per-step verdicts and optional whole-plan outcome.
    # Verdict and outcome semantics live in OperationalEvaluator_system.md.
    evaluations: list[StepEvaluation] = Field(default_factory=list)
    overall: Literal["", "plan_done", "abort"] = ""
    reason: str = ""


#: Cap the length of a progress-summary line: the review modal is compact and
#: the full result is available in the inspection pane.
_SUMMARY_MAX_LEN = 80


def _first_line(text: str) -> str:
    """Return the first non-empty line of *text*, truncated to one line.

    Used to compress a tool error or a reasoning conclusion into a single
    readable line for the progress summary.

    :param text: The text to compress.
    :returns: The first non-empty line (truncated with ``...``), or ``""``.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) > _SUMMARY_MAX_LEN:
            return stripped[: _SUMMARY_MAX_LEN - 3].rstrip() + "..."
        return stripped
    return ""


def _count(n: int, noun: str) -> str:
    """Return ``"n <noun>"`` pluralised for the human-facing review headline.

    :param n: The count.
    :param noun: The singular noun.
    :returns: ``"1 step"`` / ``"3 steps"``.
    """
    return f"{n} {noun}" + ("" if n == 1 else "s")


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

    def summary(self) -> str:
        """Return a concise one-line summary for the human-facing progress view.

        Unlike :meth:`render` (the full, LLM-facing body with the
        ``displayed_to_user`` flag), this keeps only the tool name and whether
        the call succeeded (with a short error reason), so the plan-review
        ``progress`` stays readable; the full result is a click away in the
        inspection pane.  A reasoning step renders its conclusion's first line.

        :returns: ``"<tool>: ok"`` / ``"<tool>: error - <reason>"`` for a tool
            result, or ``"reasoning: <first line>"``.
        """
        if isinstance(self.result, CallToolResult):
            tool = self.tool or "(unknown tool)"
            if self.result.is_error:
                reason = _first_line(last_tool_error_text([self.result]))
                return f"{tool}: error - {reason}" if reason else f"{tool}: error"
            return f"{tool}: ok"
        conclusion = _first_line(str(self.result))
        return f"reasoning: {conclusion}" if conclusion else "reasoning"


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
    # why the run failed or could not be planned (for the failure answer)
    failure_reason: str = ""

    # --- Transient node-to-node signals --------------------------------
    # Carry information from one node to the next and are consumed (cleared)
    # within the run; not durable run state and not rendered into downstream
    # prompts.  The plan's counters are the durable history signal instead.
    #
    #: Latest human review input (empty unless a plan is under review).  Written
    #: by the ``AwaitHuman`` review instance; read and cleared by the Planner,
    #: which interprets it and records the round on
    #: ``plan.human_feedback_rounds``.
    human_feedback: str = ""
    #: Answers supplied by the user to resume a ``needs_input`` interrupt
    #: (HITL, ADR-0046), grouped by the blocked step's number.  Written by the
    #: ``AwaitHuman`` input instance on resume; read and cleared by the
    #: Planner, which re-authors the draft plan with them.  Distinct from
    #: ``human_feedback`` so supplied facts are not counted as a review round.
    human_input: dict[int, list[str]] = Field(default_factory=dict)
    #: Why the Planner is being re-entered for an automated replan: set by the
    #: Evaluator on ``need_replan`` and by the tool-round recorder when a batch
    #: had a failed call; read and cleared by the Planner.  Empty on the first
    #: plan and after a human review (which supplies ``human_feedback``).
    replan_reason: str = ""
    #: Synthesized, human-facing digest of the work completed since the last
    #: review.  Written by the review-progress stage immediately before a review
    #: interrupt and rendered as the ask's "Progress" section, so the user can
    #: decide on the gate without reading the whole transcript.  Empty for a
    #: whole-plan approval (no work yet) and cleared on a new plan.
    review_progress: str = ""
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

    def work_since_review(self) -> list[tuple[StepSchema, list[StepOutput]]]:
        """Return completed steps and their outputs since the last review.

        Scoped by :meth:`PlanSchema.reviewed_through`: only steps numbered after
        the last approved review gate are included, so a checkpoint digest
        covers the work done since the previous gate rather than repeating all
        earlier progress.  Steps are returned in step order; a step with no
        recorded output is omitted.

        :returns: ``(step, outputs)`` pairs in step order.
        """
        since = self.plan.reviewed_through()
        result: list[tuple[StepSchema, list[StepOutput]]] = []
        for step in sorted(self.plan.step_list, key=lambda item: item.step_number):
            outputs = self.step_outputs.get(step.step_number)
            if step.step_number > since and outputs:
                result.append((step, outputs))
        return result

    def work_since_review_text(self) -> str:
        """Render :meth:`work_since_review` as prompt-ready text.

        Each step is headed by its description so the digest can attach the
        observations to what the step was meant to do; the observations use the
        same :meth:`StepOutput.render` body as the Evaluator and Planner see, so
        the digest can draw on any tool result (including third-party MCP
        tools), not just file edits.

        :returns: The rendered work, or a placeholder when nothing ran since the
            last review.
        """
        chunks: list[str] = []
        for step, outputs in self.work_since_review():
            rendered = "".join(
                f"\n\nTool call {index}: {entry.render()}"
                for index, entry in enumerate(outputs)
            )
            chunks.append(f"Step {step.step_number}: {step.description}{rendered}")
        return "\n\n".join(chunks) if chunks else "(no work since the last review)"

    def review_sections(self) -> tuple[str, list[dict[str, str]]]:
        """Return the headline and content sections for a plan-review interrupt.

        The review approves **all pending** work (ADR-0051): the ask carries
        every pending step -- the whole plan for a whole-plan approval, or the
        remaining steps for a mid-plan checkpoint.  ``review`` gate steps are
        included and marked as human checkpoints, so the plan is shown complete
        rather than with its gates silently omitted.  Completed steps are not
        shown (their findings are in the transcript and the inspection pane).

        A checkpoint also carries a "Progress" section with the synthesized
        digest of work since the last review (:attr:`review_progress`), so the
        user can decide without reading the whole transcript.

        The headline counts the pending **work** steps only: the review gate is
        the approval action itself, not work the approval unblocks.

        :returns: ``(headline, sections)``; each section is a
            ``{"title": ..., "body": ...}`` dict.  The progress body is the
            synthesized digest; the pending body is the status-pane-style
            render of the pending steps.
        """
        pending = [step for step in self.plan.step_list if step.status == "pending"]
        work_pending = [step for step in pending if step.kind != "review"]
        lines: list[str] = []
        for step in pending:
            line = step.render(markdown=True)
            if step.kind == "review":
                line += "  (human review)"
            lines.append(line)
        body = "\n".join(lines) or "(no pending steps)"
        count = _count(len(work_pending), "step")
        sections: list[dict[str, str]] = []
        progress = self.review_progress.strip()
        if progress:
            sections.append({"title": "Progress", "body": progress})
        if any(step.status in ("done", "skipped") for step in self.plan.step_list):
            sections.append({"title": "Remaining", "body": body})
            return f"Approve the remaining {count}", sections
        sections.append({"title": "Plan", "body": body})
        return f"Approve this plan ({count})", sections

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
