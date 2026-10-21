---
status: "accepted"
date: 2026-10-21
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Cross-run continuity for "plan now, implement later"

## Context and Problem Statement

Each user turn is a separate graph invocation. `InitGraphState` resets the
plan-scoped working set (`plan`, `goal`, `plan_history`, `step_outputs`,
retry/evaluation state, tool calls and results). Only user-visible messages,
the context summary, discovery and session artefacts survive.

This creates a gap for the workflow:

1. User: "make a plan to do X."
2. The agent investigates and returns a plan.
3. The graph ends.
4. User: "now implement it."

The next turn starts with no structured plan, no goal, and no prior tool
outputs. The Planner can read the final plan text from the conversation, but
it cannot reuse the investigation evidence, so it usually re-plans and may
re-run tool calls.

There is also no "meta-plan" representation. The Planner's `plan` is the
execution plan for the current goal; for a "make a plan" request, the plan for
X is produced by reasoning or answer synthesis and lives in observations,
messages or an artefact. Cross-run continuity therefore must retain evidence
from the previous execution, not just a `PlanSchema`.

A HITL review can continue the same graph run, preserving the plan and step
outputs. That is the right mechanism for "approve this plan and implement it
now". It does not help when the user decides to implement in a later turn.

## Decision Drivers

- General continuity between graph invocations, not a special plan-document
  feature.
- No never-ending graph run and no long-lived paused session.
- No full session state or full `step_outputs` retained or injected into the
  Planner prompt.
- The next request gets a fresh goal; a stale goal must not anchor the new
  implementation.
- Re-planning may be acceptable, but repeating the investigation must be
  avoidable.
- Same-run HITL plan approval must remain separate and unchanged.
- Bounded prompts, bounded checkpoints, and no leakage of unnecessary raw tool
  output or secrets.
- Stale evidence must be distinguishable from live state.

## Considered Options

- A. Never-ending graph with a HITL after `AnswerUser` that returns to the
  decision router.
- B. Treat the generated plan as a first-class plan document or target-plan
  artefact, and implement from that.
- C. Retain the full previous session state, including all `step_outputs`.
- D. Retain separate session-wide snapshot fields (`retained_plan`,
  `last_goal`, `last_step_outputs`, ...) and let the Planner read them.
- E. Add a `continue` intent to `RouteDecision`, move task cleanup to a
  `ResetState` boundary, and retain a bounded feedback-style continuation
  evidence record (chosen).
- F. Make `continue` resume the exact prior plan or have the Planner treat
  the prior plan as the active plan.

## Decision Outcome

Chosen option: **E**, because it provides general cross-run continuity without
changing the graph-run lifecycle, without introducing a second plan schema,
and without retaining or injecting full execution state.

### Intent routing

`RouteDecision` grows an intent-only `continue` route:

- `chat`: conversational reply, no task state reset.
- `task`: a new task; full task reset.
- `continue`: continue the immediately preceding work
  (`implement`, `go ahead`, `continue`, `do it`); re-plan for the new goal
  using prior evidence.

`RouteDecision` does not receive the plan or evidence. It reads the user
request and conversation history and decides intent only. If `continue` is
chosen without prior work, `ResetState` falls back to the full task reset.

### Task boundaries

`InitGraphState` no longer clears plan-scoped task state. It keeps only
per-turn work: appending the new user message, refreshing discovery, and
clearing transient delivery/routing fields. The task boundary moves to a
`ResetState` node after `RouteDecision`:

- `task`: reproduce the old full task reset (`plan`, `plan_history`, `goal`,
  `step_outputs`, evaluation and retry counters, picker/binding state,
  feedback, failure reason, tool calls and results).
- `continue`: clear the active goal, plan and step outputs (they belong to the
  previous task), clear transient fields, and preserve the bounded continuation
  evidence record. The Planner starts a fresh plan for the new goal.
- `chat`: no task reset; prior evidence remains available for a later
  `continue`.

The new goal is always written by the Planner on a `continue`; the previous
goal is only part of the evidence. This avoids a stale goal locking the new
implementation.

`continue` means **replan for the new goal using prior evidence**. It does not
mean resume the previous plan. Exact plan continuation remains the same-run
HITL approve/execute flow.

### Continuation evidence

Evidence is a bounded execution record, not full state and not raw
`step_outputs`. It is intentionally similar to the feedback the Evaluator
gives the Planner: concise, summarised and sufficient to decide what to do
next. It contains:

- the previous goal and success criteria;
- the previous plan steps with statuses and success criteria;
- abandoned-plan/replan reasons (`plan_history`), so failed approaches are not
  repeated;
- per-step outcomes: tool/action, a capped result summary, reasoning
  conclusion, and failure reason;
- the final answer and/or session artefact id;
- project root and a timestamp for staleness.

It excludes full tool payloads, duplicate transcript content, internal
evaluation chatter, transient counters and picker/binding state. Rendering is
capped and redacted using the same conventions as other user/LLM-facing
outputs. Evidence is a snapshot, not live truth: file/URL-dependent claims must
be revalidated before implementation.

The evidence record is written when a task run reaches its final answer
(success or failure) and is overwritten by the next terminal run. A full task
reset clears it. A `continue` run uses it to author the new goal/plan and then
replaces it with the new run's evidence. A `chat` turn does not clear it.

### Planner

On `continue`, the Planner receives a conditional `previous_work` block
containing the bounded evidence. Its prompt states:

- this is prior work, not the current plan;
- the goal must be set for the new request;
- use the evidence to avoid repeating investigation;
- do not blindly carry previous step statuses or plan structure forward.

The prior plan is evidence, not the active plan. This avoids step-number
collisions and stale statuses. The evidence remains available for planning; a
separate long-term per-step evidence store for execution is not part of this
decision.

### HITL

Plan review remains a plan step and a same-run HITL pause. An approval continues
the same run with the active plan and step outputs intact. `continue` is not a
review outcome and does not replace HITL.

## Consequences

- Good: general continuity works for plan-then-implement and other follow-up
  work without a never-ending run.
- Good: no plan-document schema and no special target-plan representation.
- Good: full `step_outputs` are not retained or injected; prompt and checkpoint
  growth stay bounded.
- Good: a fresh goal prevents stale-goal anchoring.
- Bad: `RouteDecision` must classify intent reliably; an implementation
  request misrouted to `task` starts from a full reset.
- Bad: evidence is lossy and may omit details needed for implementation;
  some re-reads may still be required.
- Bad: evidence can be stale; revalidation is required.
- Bad: `InitGraphState`/`ResetState` state ownership is more complex, and the
  evidence capture/clear lifecycle must be maintained.
- Bad: re-planning still occurs; this is continuity of evidence, not exact plan
  replay.

## Pros and Cons of the Options

### A. Never-ending graph with post-answer HITL

- Good: one long-lived state retains the plan and all observations without a
  cross-run model.
- Bad: the graph pauses after `AnswerUser`, so the terminal `complete` event and
  assistant-row persistence still need redesign; the answer is in state but is
  not a completed turn.
- Bad: the next user query must be delivered as an interrupt response, changing
  the API/UI input model and the one-active-run-per-chat behaviour.
- Bad: a new unrelated task still needs a task boundary and goal reset.
- Bad: Planner re-authoring still clears `step_outputs`; a direct implementation
  route is still required.
- Bad: long-lived checkpoints, cancellation, reload and memory growth become
  harder.

### B. Plan document or target-plan artefact

- Good: structured target plan could be approved and executed later.
- Bad: not a general continuity mechanism; it solves only plan-generation
  requests.
- Bad: introduces a second plan representation and a meta-plan concept the
  architecture does not have.
- Bad: does not retain investigation evidence for other follow-up work.

### C. Retain the full session state and all `step_outputs`

- Good: maximum chance of avoiding re-work.
- Bad: unbounded prompt/checkpoint growth.
- Bad: stale observations are treated as current; difficult to invalidate.
- Bad: greater risk of exposing raw tool output or secrets.
- Bad: step-number and status collisions when the Planner authors a new plan.

### D. Separate session-wide snapshot fields

- Good: clear distinction between active and retained state.
- Bad: duplicates `plan`/`step_outputs` and needs its own lifecycle and
  consistency rules.
- Bad: still has stale/full-output problems unless a bounded evidence projection
  is defined.
- Neutral: compatible with the chosen evidence approach, but the chosen design
  avoids retaining a second full plan/work state.

### F. Resume the exact previous plan or treat it as active

- Good: no replan and no re-research.
- Bad: the prior goal is stale for the new request, and the goal lock prevents
  replacing it.
- Bad: the previous plan may be completed or structurally wrong for the new
  goal.
- Bad: conflicts with ADR-0035's plan ownership and with the separate HITL
  same-run resume flow.

## Confirmation

- Unit tests: `RouteDecision` intent examples route implement/continue requests
  to `continue`; new work routes to `task`.
- Unit tests: `InitGraphState` no longer clears plan/evidence; `ResetState`
  applies the full reset for `task`, replaces the goal and clears active plan
  state for `continue`, and falls back safely when no prior evidence exists.
- Planner tests: a continuation receives the bounded `previous_work` block,
  sets a new goal, authors a new plan, and does not carry stale statuses.
- Evidence tests: caps and redaction prevent full `step_outputs`/raw payloads
  from entering the prompt or checkpoint; timestamps/project root are present.
- Graph tests: `task` and `continue` both pass through `ResetState` to the
  Planner; `chat` bypasses the task boundary.
- Checkpoint serialization includes any new evidence model.
- Manual: plan a task, answer the final plan, then send "implement"; verify the
  new goal is set, prior evidence is visible in the Planner prompt, and the
  research tool calls are not repeated unnecessarily.

## More Information

Related decisions: ADR-0032 (graph state ownership), ADR-0035 (general
operational path and plan ownership), ADR-0041 (plan step granularity),
ADR-0046 (HITL interrupt/resume), ADR-0051 (review as a plan step).

The target plan produced by a "make a plan" request remains an answer/artefact
and is included in the continuation evidence; it is not promoted to the
Planner's execution plan. The active goal on a continuation is always a new
goal written by the Planner.
