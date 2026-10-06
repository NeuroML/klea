---
status: "accepted"
date: 2026-10-05
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Human-in-the-loop: interrupt and resume

## Context and Problem Statement

The general operational path has two points where the Planner needs the
user before it can continue: `in_review` (the plan should be reviewed
before it runs) and `needs_input` (a fact only the user can supply).
Neither was implemented as real human-in-the-loop.  `AwaitReview` was an
auto-approve stub that fed a canned "looks good, proceed" back to the
Planner, and `needs_input` ended the run with the question in the answer
("Composing answer" -> `END`), so the next turn was a fresh run from
`START` and the partial plan was discarded by `InitGraphState`.

LangGraph supports true HITL: a node calls `interrupt(payload)`, the run
suspends with the payload, and `Command(resume=value)` continues the
**same** execution with state intact.  How should Klea model the pause,
deliver the user's answer, and let the user cancel -- without losing the
plan, and without conflating an answer with a new query?

A second, related problem: a plain new query sent while a run is paused
does **not** resume it.  LangGraph re-enters from `START` and abandons the
pending task (verified; see `devdocs/system/graph-resume.md`), so the
interrupt is silently dropped.  The invariant "an interrupt must not be
ignored" must hold in the backend for every client, not only in the UI.

## Decision Drivers

* The plan (including a partial `needs_input` plan) must survive to be
  resumed; the whole value of an interrupt over "end the run and ask in
  the next turn" is preserved state.
* An answer is not a new query: answer and cancel are distinct actions
  from a fresh turn.
* Cancel must be distinct from approve: it must not execute the remaining
  plan, or it collapses into "ok, proceed".
* A question must be tied to the step it blocks, so the plan makes the
  blockage explicit instead of floating questions.
* One HITL mechanism for both review and input; no duplicate node code.
* Reuse the ADR-0043 checkpoint/resume plumbing.
* Provenance: a user cancellation must be distinguishable from an
  evaluator abort.
* Keep the mechanism in shared plumbing (`klea_utils`) so every client
  (web, TUI, Streamlit) reuses it.

## Considered Options

* **A. Fresh-turn HITL** - an interrupt ends the run; the user's answer is
  the next turn.
* **B. Interrupt with same-run resume, cancel as a terminal outcome** -
  answer resumes the paused run with state intact; cancel ends it without
  executing.
* **C. Status quo** - auto-approve review stub; `needs_input` ends the run.

## Decision Outcome

Chosen option: **B**, because it is the only option that preserves the
plan (the point of an interrupt) while keeping cancel meaningfully
different from approval, and it reuses the resume path ADR-0043 already
built.

### Semantics

* **Interrupt == pause.**  A node calls `interrupt(payload)`; the run
  suspends before the next super-step with `next == (<node>,)` and a
  pending task.  The payload is transient (it is not retained as state).
* **Answer == same execution.**  `Command(resume=<mapping>)` returns the
  mapping from `interrupt()` inside the same node; the node writes the
  answer and routes on with state intact.  `InitGraphState` is **not**
  re-run, so per-turn state survives.  The mapping is kind-specific (see
  Response schemas).
* **Cancel == terminal, no execution.**  `Command(resume={"action":
  "cancel"})` sets `plan.status = "user_cancelled"`, appends a note-only
  message to `messages` ("user cancelled the run while awaiting an answer
  to: <question>"; no plan content), and routes to a terminal reply ->
  `END`.  The remaining plan never executes.  `user_cancelled` is added to
  the `PlanSchema.status` literal so a user cancel is distinguishable from
  an evaluator `aborted`.
* **`needs_input` is step-level.**  A step carrying `needs_input`
  questions is *blocked*: it is part of a draft plan and is not executed
  (and is exempt from the executable-step checks) until the Planner
  re-authors it without questions.  The plan status `needs_input` is
  derived from the steps, not taken from the model: `Planner._update_state`
  forces it whenever any step is blocked, and validation rejects
  `needs_input` with no blocked step.  A plan-level question is expressed
  as a clarification step (a step whose only content is its `needs_input`).
* **Review approval is deterministic.**  A human approval of an `in_review`
  plan sets `plan.status = "in_progress"` in the review node (accounting
  the review round and resetting the revision budget) and the router sends
  the run **straight into the execution loop** -- no Planner call.  A
  revision carries the feedback back to the Planner, which re-authors.
* **Next turn after cancel is a fresh run.**  The structured plan is
  discarded (a cancelled plan is not carried forward), but `messages`,
  `context_summary`, `mode`, and the session-scoped `artefacts` survive,
  so the Planner is aware of the prior cancellation.  The cancellation note
  is written explicitly into `messages` because `AnswerFromResults` does
  not append the assistant reply to `messages`.

### Topology: one template, one policy class, two instances

The mechanism lives in a reusable template
(`klea_utils/nodes/await_human.py`, `AwaitHumanNode`): it owns the
`interrupt(payload, response_schema=...)` call, resume parsing, the stream
events, and the answer/cancel envelope.  Policy lives in the agent
subclass (`agent_pkg/klea_agent/nodes/await_human.py`, `AwaitHuman`),
instantiated twice and registered by label:

| Trigger | Instance label | `kind` | Writes |
|---|---|---|---|
| `in_review` | "Awaiting review" | `review` | `human_feedback` |
| `needs_input` (a blocked step) | "Awaiting input" | `input` | `human_input` (step -> answers) |

Routing after the pause differs by instance:
* review: approve -> execution (picker/reasoning, or the answer when no
  step remains); revise -> Planner; cancel -> "Cancelled".
* input: answer -> Planner (which re-authors); cancel -> "Cancelled".

Two instances (rather than one status-branching node) give distinct labels
in the stream/status/inspector and let a reloaded client tell which ask is
pending from `aget_state().tasks[*].name` alone.

`human_input` is a transient state field distinct from `human_feedback`: a
`needs_input` answer is new information, not review feedback, and must not
advance the review-round counter.  It is keyed by step number
(`dict[int, list[str]]`), so the Planner can render each answer against the
step that asked.

### Response schemas

The node passes a Pydantic model to LangGraph's
`interrupt(payload, response_schema=...)`, which validates the resume value
and surfaces the schema on `Interrupt.response_schema`; the wrapper forwards
it on the stream event as `hitl_response_schema` (our name, to avoid
confusion with an LLM/API "response").  LangGraph validates the resume value
against the schema when one is set.

* **review**: `ReviewResponse{action: "answer"|"cancel", decision:
  "approve"|"revise", feedback: str}`.
* **input**: a default `_QuestionsResponse{action, answers: list[str]}`
  (answers positional, aligned with the blocked steps' flattened questions).
  The app may override the hook for a typed form.

A bare-string resume is accepted as an answer for simple clients.

### API contract

`resume` stays as it is (ADR-0043): continue a graph from its last
checkpoint after a failure (invoke `None`; no query).  HITL is a distinct
set of fields on the chat payload:

* `interrupt_response: dict | None` - the answer mapping.
* `interrupt_id: str | None` - the `id` of the interrupt being answered
  (framework-generated; echoed back for stale-answer safety).
* `interrupt_cancel: bool` - cancel the pending interrupt.

Exactly one of `resume`, `interrupt_response`, or `interrupt_cancel` may be
set (a query is required only when none is).  The answer mapping is
kind-specific -- review `{"decision": ...}`, input `{"answers": [...]}` --
with `action` defaulting to `answer`; cancel is
`{"action": "cancel"}`.  The graph wrappers (`run_graph_invoke`,
`run_graph_astream_events`) accept a `Command` as their input alongside
`dict | None`.

### Backend invariant

`chat_core` reads the thread's checkpoint before starting a run.  If the
thread has a pending interrupt (`tasks[*].interrupts` non-empty):

* a request carrying `interrupt_response` or `interrupt_cancel` resumes
  it (the echoed `interrupt_id`, when present, must match the pending one
  or the request is rejected);
* any other request (a plain query, or a failure resume) is rejected with
  HTTP `409` ("this chat is awaiting your answer"), independent of the
  client.

This check is separate from, and runs before, the ADR-0043 single-flight
registry.

### Stream contract

Under `astream_events` v3 a paused run emits a `values` event whose
`params["interrupts"]` is non-empty.  `run_graph_astream_events`:

* emits `{"type": "interrupt", "node": <label>, "data": {"kind": ...,
  "question"|"questions": ..., "interrupt_id": ..., "hitl_response_schema":
  {...}}}`
* suppresses the spurious `complete` frame it otherwise emits after a pause.

The on-pause event lets `chat_core` persist the question as an assistant row
and skip writing a final answer.

### Persistence

* Pause: the interrupt question is written as an assistant row.
* Resume (answer): the answer is written as a user row; the assistant row
  is written on a real `complete`.
* Cancel: the terminal "cancelled" reply is written on `complete`.
* Failure resume (ADR-0043) is unchanged: no user row.

### Clients

* **Shared SSE client** (`klea_utils/api/sse.py`): `stream_events` carries
  the interrupt actions in the request body and yields the `interrupt`
  frame unchanged.
* **Web (NiceGUI)**: the live flow renders the ask in the chat pane's turn
  status region (a review form or per-question inputs), gates the main
  input while paused, and submits the answer/cancel.  A reload re-presents
  the ask: `GET /chat/{user}/{chat}/context` returns `pending_interrupt`
  alongside `context`, and hydration sets the ask + `awaiting_input`.
* **TUI**: `_query_one` detects the `interrupt` frame, prompts
  (approve/revise/cancel or per-question answers), and streams the answer
  in the same turn.

### Consequences

* Good, because a paused plan is resumed with its state intact instead of
  being discarded.
* Good, because questions are linked to the steps they block.
* Good, because review approval is deterministic (no LLM round-trip) and
  cancel is explicit, recorded (`user_cancelled`), and cannot be mistaken
  for approval.
* Good, because the "do not ignore an interrupt" invariant is enforced in
  the backend for every client.
* Good, because it reuses the ADR-0043 resume plumbing and the generic node
  is shared.
* Bad, because it adds state (`human_input`, a `user_cancelled` status, a
  `StepSchema.needs_input` field) and three payload fields.
* Bad, because a resume skips `InitGraphState` (correct, but per-turn
  bookkeeping must remain entry-only).
* Bad, because pause/resume adds checkpoints to the append-only thread
  history (no prune; acceptable at current scale).
* Bad, because `interrupt_id` and `hitl_response_schema` add API/stream
  surface and validation paths.

### Confirmation

* Unit tests: base wrapper emits the `interrupt` event (with
  `hitl_response_schema`) and suppresses `complete`; `chat_core` returns
  `409` for a plain query while paused and maps
  `interrupt_response`/`interrupt_cancel` to `Command(resume=...)`; payload
  validation (exactly one action); `AwaitHuman` writes `human_feedback`
  (review) / `human_input` (input) and forces `needs_input` from a blocked
  step; review approve dispatches and revise returns to the Planner; cancel
  sets `user_cancelled` and does not execute; a stale `interrupt_id` is
  rejected; the context endpoint exposes `pending_interrupt` and the web
  client hydrates it; the TUI prompt maps choices/answers.
* Empirical semantics recorded in `devdocs/system/graph-resume.md`
  (`langgraph` 1.2.12): interrupt pause/resume, append-only history, and
  the plain-input-while-pending behaviour.
* E2E: a blocked step is a deterministic interrupt trigger, so the
  pause/resume/cancel cycle can be driven without a fault-injection hook.

## Pros and Cons of the Options

### Fresh-turn HITL

* Good, because it is simple and matches a flat ReAct loop.
* Bad, because it discards the plan, so the interrupt buys nothing over
  ending the run and asking in the next turn.
* Bad, because "cancel" and "answer" both become just new queries.

### Interrupt with same-run resume, cancel as a terminal outcome

* Good, because the plan and partial `needs_input` plan survive.
* Good, because answer, cancel, and a new turn are three distinct actions.
* Good, because it reuses ADR-0043's checkpoint/resume path.
* Neutral, because it needs API/UI/stream plumbing.
* Bad, because it grows the state and payload surface, and resume skips
  the entry node.

### Status quo

* Good, because no change.
* Bad, because review is a stub and `needs_input` loses the partial plan.
* Bad, because a paused run can be silently dropped by a plain query.

## More Information

* LangGraph persistence and interrupts:
  https://docs.langchain.com/oss/python/langgraph/persistence
* `devdocs/system/graph-resume.md` (confirmed semantics),
  `devdocs/adr/0043-graph-fault-tolerance-and-resume.md` (checkpoint
  resume, single-flight, cancel),
  `devdocs/adr/0035-general-operational-agent-path.md` (Planner/plan
  lifecycle),
  `devdocs/adr/0041-plan-step-granularity-and-parallelism.md`
  (plan step granularity).
* ADR-0035 and ADR-0041 cited a stale ADR number for the HITL interrupt
  work; this is that ADR.  Those references and
  `devdocs/system/agent-general-path-control-flow.md` are updated in the
  documentation cascade for this change.
