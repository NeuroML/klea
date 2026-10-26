#!/usr/bin/env python3
"""
Schemas used by the agent

File: klea_agent/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Any, Literal

from fastmcp.client.client import CallToolResult
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.schemas import ToolCallSchema
from klea_utils.tools import last_tool_error_text, textualize_tool_results
from langchain_core.messages import AnyMessage
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
    #: * ``chat`` -- the entry router misclassified a conversational request as
    #:   ``task``.  This is not a task outcome: return no steps and no goal; the
    #:   graph routes back to ``RouteDecision`` (which has conversation memory)
    #:   and forces it to answer as chat.  A request whose facts are already
    #:   known is still a task: plan a ``reasoning`` step to synthesise them.
    #: * ``unplannable`` -- no workable plan with the available tools.
    status: Literal[
        "in_progress",
        "needs_input",
        "unplannable",
        "chat",
    ] = Field(default="in_progress", validate_default=True)

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
      exists.  A code-written failure (``unplannable``) or budget-guidance
      draft (``needs_input``) may retain the last authored ``step_list`` so the
      status pane still shows it; the model-authored
      :class:`PlannerPlanSchema` still rejects ``unplannable`` with steps.
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
        "completed",
        "failed",
        "aborted",
        "user_cancelled",
        "chat",
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


#: Maximum number of superseded plan versions retained for the Planner and the
#: guidance HITL ask.  The active plan is shown separately, so this keeps the
#: visible total at 10 plan versions.  The cap is independent of the automated
#: replan budget, which resets when a human supplies input.
MAX_PREVIOUS_PLANS = 9


class PlanRevision(BaseModel):
    """One superseded plan version and why it was abandoned.

    Stored in :attr:`KleaAgentState.plan_history` as a bounded FIFO.  It lets a
    replan see the approaches that already failed and lets the human at a
    guidance interrupt see what has been tried.  The active plan stays in
    :attr:`KleaAgentState.plan` and is not stored here.
    """

    version: int = 0
    steps: list[StepSchema] = Field(default_factory=list)
    abandoned_reason: str = ""

    def render_body(self) -> str:
        """Render the abandonment reason and steps as a prompt/UI body."""
        reason = self.abandoned_reason.strip() or "(no reason recorded)"
        steps = "\n".join(step.render() for step in self.steps) or "(no steps)"
        return f"Abandoned: {reason}\nSteps:\n{steps}"

    def render(self) -> str:
        """Render the version with its heading for the Planner prompt."""
        return f"### Plan version {self.version}\n{self.render_body()}"


def trim_plan_history(history: list[PlanRevision]) -> list[PlanRevision]:
    """Return the FIFO-capped history of superseded plan versions.

    :param history: The full append-ordered history.
    :returns: The most recent :data:`MAX_PREVIOUS_PLANS` entries.
    """
    return history[-MAX_PREVIOUS_PLANS:]


#: Caps for the bounded continuation evidence record.  This is a feedback-style
#: summary, not a copy of ``step_outputs`` or of raw tool payloads.
MAX_CONTINUATION_STEPS = 30
MAX_CONTINUATION_STEP_CHARS = 240
MAX_CONTINUATION_GOAL_CHARS = 1000
MAX_CONTINUATION_FINAL_ANSWER_CHARS = 3000
MAX_CONTINUATION_ABANDONED_PLANS = 5
MAX_CONTINUATION_ARTEFACT_IDS = 3
MAX_CONTINUATION_RENDER_CHARS = 8000


def _clip_text(text: str, max_chars: int) -> str:
    """Return *text* stripped and clipped to *max_chars* with an ellipsis."""
    value = str(text).strip()
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars].rstrip()} ..."


class ContinuationStepEvidence(BaseModel):
    """One prior plan step's compact outcome for cross-run continuation."""

    step_number: int = 0
    description: str = ""
    success_criteria: str = ""
    kind: Literal["tool", "reasoning", "review"] = "tool"
    status: Literal["pending", "done", "skipped", "failed"] = "pending"
    tool: str = ""
    summary: str = ""
    is_error: bool = False


class AbandonedPlanEvidence(BaseModel):
    """A prior plan version that was abandoned, and why."""

    version: int = 0
    reason: str = ""


class ContinuationEvidence(BaseModel):
    """Bounded evidence from the previous terminal run for a later continue.

    The record is a compact execution summary, not the full state.  It is
    written at the end of a task and used to re-plan for a new goal; full
    ``step_outputs`` and raw tool payloads are deliberately not retained here.
    """

    goal: str = ""
    success_criteria: str = ""
    plan_version: int = 0
    plan_status: str = ""
    steps: list[ContinuationStepEvidence] = Field(default_factory=list)
    steps_omitted: int = 0
    abandoned_plans: list[AbandonedPlanEvidence] = Field(default_factory=list)
    final_answer: str = ""
    artefact_ids: list[str] = Field(default_factory=list)
    project_root: str = ""
    captured_at: str = ""

    def render(self) -> str:
        """Render the evidence as a bounded prompt-ready text block."""
        lines: list[str] = []
        if self.goal:
            lines.append(f"Goal: {self.goal}")
        if self.success_criteria:
            lines.append(f"Success criteria: {self.success_criteria}")
        plan_line = f"Plan version {self.plan_version}"
        if self.plan_status:
            plan_line += f" ({self.plan_status})"
        lines.append(plan_line)
        if self.steps:
            lines.append("Steps:")
            for step in self.steps:
                line = f"- Step {step.step_number} [{step.status}, {step.kind}]"
                if step.tool:
                    line += f" {step.tool}"
                line += f": {step.description}"
                if step.success_criteria:
                    line += f" (criteria: {step.success_criteria})"
                if step.summary:
                    line += f" -- {step.summary}"
                if step.is_error:
                    line += " [error]"
                lines.append(line)
            if self.steps_omitted:
                lines.append(f"- ... {self.steps_omitted} more step(s) omitted")
        else:
            lines.append("Steps: (none)")
        if self.abandoned_plans:
            lines.append("Abandoned plans:")
            for item in self.abandoned_plans:
                lines.append(
                    f"- version {item.version}: {item.reason or '(no reason recorded)'}"
                )
        if self.final_answer:
            lines.append("Final answer:")
            lines.append(self.final_answer)
        if self.artefact_ids:
            lines.append(f"Artefacts: {', '.join(self.artefact_ids)}")
        if self.project_root:
            lines.append(f"Project root: {self.project_root}")
        if self.captured_at:
            lines.append(f"Captured at: {self.captured_at}")
        return _clip_text("\n".join(lines), MAX_CONTINUATION_RENDER_CHARS)


class GoalSchema(BaseModel):
    goal: str = ""
    success_criteria: str = ""


class ArtefactSchema(BaseModel):
    """A durable, session-scoped result (ADR-0029; ADR-0035 update 2026-09-19).

    Artefacts are the session-scoped store that survives across graph runs,
    unlike plan-scoped working memory (``step_outputs``), which
    ``InitGraphState`` clears each run.  ``AnswerFromResults`` auto-persists
    the completed task's deliverable here, and later tasks see it through the
    answer-composer prompt (rendered by ``KleaAgentState.artefacts_text``).

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


#: Prompt caps for :meth:`KleaAgentState.artefacts_text`.  Artefacts are
#: session-scoped and accumulate across tasks; the rendered block must stay
#: bounded or the answer-composer prompt grows without limit.
#: The rendered text is selected newest-first (within these caps) and then
#: emitted in chronological order.  The count cap bounds many small
#: artefacts; the character cap bounds a few large ones.
MAX_ARTEFACTS_IN_PROMPT = 20
MAX_ARTEFACTS_PROMPT_CHARS = 12_000


def _render_artefact(artefact_id: str, artefact: ArtefactSchema) -> str:
    """Render one artefact for a prompt.

    :param artefact_id: The dict key (used when the artefact carries no id).
    :param artefact: The artefact to render.
    :returns: The rendered block.
    """
    key = artefact.id_ or artefact_id
    header = f"{key} (type: {artefact.type_ or 'unknown'})"
    lines = [header, str(artefact.content)]
    if artefact.metadata:
        rendered_meta = "; ".join(
            f"{name}={value}" for name, value in artefact.metadata.items()
        )
        lines.append(f"metadata: {rendered_meta}")
    return "\n".join(lines)


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
    route: Literal["chat", "task", "continue"] = "continue"
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


#: User-facing labels for :attr:`StepEvaluation.verdict` values.
STEP_VERDICT_LABELS: dict[str, str] = {
    "step_done": "DONE",
    "step_skipped": "SKIPPED",
    "step_incomplete": "INCOMPLETE",
    "need_replan": "NEEDS REVISION",
}


class EvaluationSchema(BaseModel):
    # The Evaluator's per-step verdicts and optional whole-plan outcome.
    # Verdict and outcome semantics live in OperationalEvaluator_system.md.
    evaluations: list[StepEvaluation] = Field(default_factory=list)
    overall: Literal["", "plan_done", "abort"] = ""
    reason: str = ""
    #: Review-readiness judgement, set only when the next pending step after
    #: the judged batch is a ``review`` step (ADR-0051).  ``ready`` means the
    #: plan already produces the evidence the reviewer needs; ``not_ready``
    #: means a concrete evidence step is missing, named in :attr:`review_reason`,
    #: so the plan is sent back to the Planner to add it.  ``""`` when no review
    #: is next (or the model did not judge readiness).
    review_readiness: Literal["", "ready", "not_ready"] = ""
    review_reason: str = ""

    def step_incomplete_feedback(self, step_numbers: set[int]) -> str:
        """Render ``step_incomplete`` reasons for the given step numbers.

        Used by the actor that re-runs those steps (binder or reasoning) so it
        can address the evaluator's specific objection.  Verdicts for other
        steps or other outcomes are ignored; an empty result means the section
        should be omitted.

        :param step_numbers: Step numbers to include (the current binding
            group or the current reasoning step).
        :returns: A prompt-ready block, or ``""`` when no matching step was
            judged incomplete.
        """
        lines = [
            f"- Step {item.step_number}: {item.reason.strip() or 'not yet complete'}"
            for item in self.evaluations
            if item.verdict == "step_incomplete" and item.step_number in step_numbers
        ]
        if not lines:
            return ""
        return "The evaluator judged these step(s) incomplete:\n" + "\n".join(lines)


#: User-facing labels for :attr:`EvaluationSchema.overall` values.
EVALUATION_OVERALL_LABELS: dict[str, str] = {
    "plan_done": "PLAN COMPLETE",
    "abort": "CANNOT COMPLETE",
}


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

    #: Internal run history: plan versions, evaluation verdicts and reasoning
    #: conclusions.  Not user-visible; retained for provenance and fed to the
    #: summariser alongside ``messages``.  Checkpointed across turns.
    messages_internal: list[AnyMessage] = Field(default_factory=list)
    #: Cursor for ``messages_internal``, parallel to ``summarised_till``.
    summarised_internal_till: int = 0

    # code string if any
    code: CodeSchema = CodeSchema()

    # planning related
    goal: GoalSchema = GoalSchema()
    plan: PlanSchema = PlanSchema()
    #: Superseded plan versions, newest last, capped by
    #: :data:`MAX_PREVIOUS_PLANS`.  Reset per turn; survives human input and
    #: review revisions so a later replan can see what already failed.
    plan_history: list[PlanRevision] = Field(default_factory=list)
    #: Bounded evidence from the previous terminal run, used by a later
    #: ``continue`` intent to re-plan for a new goal.  Session-scoped: it is
    #: not cleared with the active plan; a new task reset clears it.
    continuation_evidence: ContinuationEvidence | None = None
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
    # calls bound so far in the current batch (binder mode).  Accumulated across
    # one invocation per tool-set group, then consumed by the caller; cleared
    # after dispatch and when the Planner writes a new plan.
    binding_calls: list[ToolCallSchema] = Field(default_factory=list)
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

    def previous_plans_text(self) -> str:
        """Render the superseded plan versions for the Planner prompt.

        :returns: The rendered versions, or ``""`` when none were superseded.
        """
        return "\n\n".join(
            revision.render() for revision in trim_plan_history(self.plan_history)
        )

    def plan_history_sections(
        self, *, include_current: bool = True
    ) -> list[dict[str, str]]:
        """Return previous (and optionally active) plans as HITL sections.

        The same rendering feeds the guidance ask so the user can see what was
        already tried and why it was abandoned.

        :param include_current: Include the active plan as a final section.
        :returns: ``{"title": ..., "body": ...}`` sections in plan order.
        """
        sections = [
            {
                "title": f"Plan version {revision.version} (abandoned)",
                "body": revision.render_body(),
            }
            for revision in trim_plan_history(self.plan_history)
        ]
        if include_current and self.plan.step_list:
            body = (
                "\n".join(step.render() for step in self.plan.step_list) or "(no steps)"
            )
            sections.append(
                {
                    "title": f"Current plan version {self.plan.plan_version}",
                    "body": body,
                }
            )
        return sections

    def build_continuation_evidence(
        self,
        final_answer: str,
        *,
        captured_at: str = "",
        artefact_ids: list[str] | None = None,
    ) -> ContinuationEvidence:
        """Build the bounded evidence record for a later ``continue``.

        Only compact per-step summaries and plan outcomes are retained; raw
        tool outputs are not copied.  The final answer is included so a
        plan-like deliverable remains available for the next goal.

        :param final_answer: The terminal answer for this run.
        :param captured_at: ISO timestamp; defaults to now (UTC).
        :param artefact_ids: Artefact ids to record; defaults to the most
            recent session artefacts.
        :returns: The bounded :class:`ContinuationEvidence`.
        """
        steps: list[ContinuationStepEvidence] = []
        for step in self.plan.step_list[:MAX_CONTINUATION_STEPS]:
            entries = self.step_outputs.get(step.step_number, [])
            tools = sorted({entry.tool for entry in entries if entry.tool})
            summary = " | ".join(entry.summary() for entry in entries)
            steps.append(
                ContinuationStepEvidence(
                    step_number=step.step_number,
                    description=_clip_text(
                        step.description, MAX_CONTINUATION_STEP_CHARS
                    ),
                    success_criteria=_clip_text(
                        step.success_criteria, MAX_CONTINUATION_STEP_CHARS
                    ),
                    kind=step.kind,
                    status=step.status,
                    tool=_clip_text(", ".join(tools), MAX_CONTINUATION_STEP_CHARS),
                    summary=_clip_text(
                        summary or "(no recorded output)",
                        MAX_CONTINUATION_STEP_CHARS,
                    ),
                    is_error=any(
                        isinstance(entry.result, CallToolResult)
                        and entry.result.is_error
                        for entry in entries
                    ),
                )
            )
        history = [
            AbandonedPlanEvidence(
                version=item.version,
                reason=_clip_text(item.abandoned_reason, MAX_CONTINUATION_STEP_CHARS),
            )
            for item in self.plan_history[-MAX_CONTINUATION_ABANDONED_PLANS:]
        ]
        ids = (
            artefact_ids
            if artefact_ids is not None
            else list(self.artefacts)[-MAX_CONTINUATION_ARTEFACT_IDS:]
        )
        return ContinuationEvidence(
            goal=_clip_text(self.goal.goal, MAX_CONTINUATION_GOAL_CHARS),
            success_criteria=_clip_text(
                self.goal.success_criteria, MAX_CONTINUATION_GOAL_CHARS
            ),
            plan_version=self.plan.plan_version,
            plan_status=self.plan.status,
            steps=steps,
            steps_omitted=max(0, len(self.plan.step_list) - MAX_CONTINUATION_STEPS),
            abandoned_plans=history,
            final_answer=_clip_text(final_answer, MAX_CONTINUATION_FINAL_ANSWER_CHARS),
            artefact_ids=list(ids)[-MAX_CONTINUATION_ARTEFACT_IDS:],
            project_root=str(self.project_root or Path.cwd()),
            captured_at=captured_at or datetime.now(UTC).isoformat(timespec="seconds"),
        )

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
        """Render the session-scoped artefacts as one bounded text block.

        Used by the answer-composer so a later task can build on earlier
        tasks' durable results.  Each artefact is rendered with its id, type and
        metadata (provenance/references) so the model can cite them; content is
        the concise result.

        Artefacts accumulate across a session, so the block is bounded: the
        most recently written artefacts are selected up to
        :data:`MAX_ARTEFACTS_IN_PROMPT` and :data:`MAX_ARTEFACTS_PROMPT_CHARS`,
        then emitted in chronological order (oldest selected first, newest
        last).  The newest artefact is always included even when it alone
        exceeds the character cap.  When older artefacts are dropped, a short
        note tells the model the block is incomplete.  Dict insertion order is
        write order (``AnswerFromResults`` moves a superseded id to the end).

        :returns: The rendered artefacts, or ``"(no artefacts)"`` when the
            session has none.
        """
        if not self.artefacts:
            return "(no artefacts)"
        items = list(self.artefacts.items())
        selected: list[str] = []
        used = 0
        for artefact_id, artefact in reversed(items):
            rendered = _render_artefact(artefact_id, artefact)
            if selected and (
                len(selected) >= MAX_ARTEFACTS_IN_PROMPT
                or used + len(rendered) > MAX_ARTEFACTS_PROMPT_CHARS
            ):
                break
            selected.append(rendered)
            used += len(rendered)
        selected.reverse()
        text = "\n\n".join(selected)
        omitted = len(items) - len(selected)
        if omitted:
            noun = "artefact" if omitted == 1 else "artefacts"
            text += f"\n\n({omitted} older {noun} omitted from this prompt.)"
        return text
