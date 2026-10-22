---
status: "accepted"
date: 2026-10-14
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Plan review as a plan step

## Context and Problem Statement

The general path has two points where the Planner needs the user before it
can continue (ADR-0046): `in_review` (a plan should be reviewed before it
runs) and `needs_input` (a fact only the user can supply).  Both are
Planner-derived, both pause the run for human input, and the Planner chooses
between them.

This overlaps at the *intent* level.  `in_review` is a plan-level gate with
no position -- it can only pause *before* execution.  `needs_input` is
step-level but is meant for **facts**.  A request like "stop for review after
step 3" is neither: it is a mid-plan **approval checkpoint**, for which the
plan has no representation.  The model therefore improvises -- flagging the
whole plan `in_review` (an up-front approval) and/or attaching approval-style
"questions" to steps via `needs_input` -- producing an unexpected sequence of
review/input pauses before the requested step.

A second problem: because approval is not a position in the plan, review
cannot be requested more than once, or at a meaningful point in the work.

## Decision Drivers

* One mental model: the plan should say *where* the human is needed; the
  human is another dependency in the plan DAG, not a special plan status.
* Mid-plan review must be expressible ("review after step N").
* Approval must stay deterministic (no Planner round-trip) and must not be
  conflated with a fact-only question that returns to the Planner.
* Reuse the existing frontier/dependency execution model (ADR-0041) and the
  HITL interrupt/resume mechanism (ADR-0046).
* Keep `needs_input` for its real purpose: information only.

## Considered Options

* **A. Review as a plan step.**  Add a `review` step kind; retire the
  `in_review` status.  Whole-plan review is a `review` step with no
  dependencies; a checkpoint is a `review` step depending on an earlier step.
* **B. Collapse to `needs_input` only.**  Approval is a free-text question;
  answering it re-enters the Planner.
* **C. Prompt-only clarification.**  Keep both states; forbid the model from
  faking mid-plan review.  Mid-plan review stays unsupported.

## Decision Outcome

Chosen option: **A**, because it unifies the mental model and makes review
positional while preserving the deterministic approval that ADR-0046
established.  Option B costs an extra Planner round-trip on every approval and
lets the Planner mutate a plan the user just approved; option C leaves the
requested capability unimplemented.

### Semantics

* **`review` is a step kind** (`StepSchema.kind: "tool" | "reasoning" |
  "review"`), alongside `tool` and `reasoning`.  A `review` step names no
  tools, has no `success_criteria`, and is not executed or judged by the
  Evaluator -- its "execution" is the user's decision.
* **Position is a dependency.**  A review step with no `depends_on` is a
  whole-plan approval (the first frontier head, before any work).  A review
  step after step N uses `depends_on: [N]`: the run executes up to N, then
  pauses at the checkpoint.
* **The `in_review` status is retired.**  The Planner no longer sets it; a
  review request is a review step.  Routing is derived from the frontier's
  step kind: a pending `review` step at the frontier head pauses the run.
* **Approval is deterministic.**  Approve marks the review step(s) at the
  frontier `done` and dispatches straight into the execution loop -- no
  Planner call.  The plan status stays `in_progress` throughout; the review
  step is the gate.
* **Revision returns to the Planner.**  A revision leaves the review step
  pending and writes `human_feedback` (always non-empty, so the router has a
  reliable revision signal); the Planner re-authors.
* **Cancel is terminal** (`user_cancelled`), as in ADR-0046.
* **The ask adapts.**  A review only approves **pending** work.  The interrupt
  carries a `headline` and `sections` (`{title, body}`) built by
  `KleaAgentState.review_sections`: a review before any work presents the whole
  plan under approval (`Approve this plan (N steps)`); a checkpoint shows the
  remaining steps (`Approve the remaining N steps`; N counts the pending work
  steps -- the gate is the approval action itself).  Every pending step is
  listed, including the `review` gate steps themselves (marked as human
  review), so the plan is not shown incomplete.  Completed steps and their
  findings are not shown (they live in the transcript and the inspection
  pane).  A review step must gate
  pending work -- a *trailing* review (nothing after it) is rejected by the
  Planner's validator and resolved defensively by the router.  (2026-10-16
  update: the payload was `plan` + `progress`, then `headline` + `sections`
  with completed + remaining; it became `headline` + pending-only `sections`,
  with no pre-release shim.  2026-10-17 update: that pending-only filter also
  dropped the review gates; the sections now include all pending steps.)
* **`needs_input` is for facts only.**  It supplies information the Planner
  cannot obtain; it is never used for approval or confirmation.  Answers
  re-enter the Planner, which re-authors.  There is deliberately no bound on
  the number of `needs_input` rounds: the Planner asks as often as it needs,
  because proceeding on a non-concrete plan is worse than asking again.

(2026-10-22 update: whole-plan *display* is no longer a review use case.  With
`continue` as the default entry route (ADR-0054), "make a plan and show it /
present it for review" is a deliverable written by the answer-composer; the
user then asks for changes in a later turn, which re-plans from the retained
evidence.  A `review` step is reserved for a **same-run approval gate** that
unblocks work after it ("approve before the work runs", "review after step N").
`needs_input` remains facts-only and is not a substitute for approval.)

### Consequences

* Good, because mid-plan review ("stop for review after step N") is
  representable and review becomes repeatable (multiple review steps).
* Good, because there is a single currency for human interaction: plan steps.
* Good, because approval stays deterministic and the frontier/dependency
  model is reused.
* Bad, because `PlanSchema`/`PlannerPlanSchema` gain a step kind and lose a
  status; the routers, the planner prompt, tests and the C4/control-flow docs
  change together.
* Bad, because an approval that unblocks a *further* review step must route
  back to the review node (a review -> review edge), and the revise signal
  must be carried by `human_feedback` rather than by a status.

### Confirmation

* Unit tests: a review step keeps the plan `in_progress`; the Planner
  validates that a review step has a description and names no tools; approve
  marks the frontier review step done and returns no feedback; revise leaves
  it pending and sets non-empty feedback; a checkpoint ask carries `progress`
  while a plan approval does not; the routers dispatch a review-kind frontier
  head to the review node and handle the Evaluator -> review edge.
* The `in_review` status is absent from both status enums.

## More Information

* Supersedes the `in_review` status and the single-instance review flow of
  ADR-0046; the interrupt/resume mechanism, the input instance, and the
  deterministic-approve principle are unchanged.
* `devdocs/system/agent-general-path-control-flow.md` (control flow and the
  review section) and `devdocs/system/c4-component-agent.md` are updated in
  the same change.
