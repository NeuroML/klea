---
status: "proposed"
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
Neither is implemented as real human-in-the-loop today.  `AwaitReview` is
an auto-approve stub that feeds a canned "looks good, proceed" back to the
Planner (`agent_pkg/klea_agent/nodes/await_review.py`), and `needs_input`
ends the run with the question in the answer ("Composing answer" ->
`END`), so the next turn is a fresh run from `START` and the partial plan
is discarded by `InitGraphState`.

LangGraph supports true HITL: a node calls `interrupt(payload)`, the run
suspends with the payload, and `Command(resume=value)` continues the
**same** execution with state intact.  How should Klea model the pause,
deliver the user's answer, and let the user cancel -- without losing the
plan, and without conflating an answer with a new query?

A second, related problem: a plain new query sent while a run is paused
does **not** resume it.  LangGraph re-enters from `START` and abandons the
pending task (verified; see Confirmation), so the interrupt is silently
dropped.  The invariant "an interrupt must not be ignored" must hold in
the backend for every client, not only in the UI.

## Decision Drivers

* The plan (including the partial `needs_input` plan) must survive to be
  resumed; the whole value of an interrupt over "end the run and ask in
  the next turn" is preserved state.
* An answer is not a new query: answer and cancel are distinct actions
  from a fresh turn.
* Cancel must be distinct from approve: it must not execute the remaining
  plan, or it collapses into "ok, proceed".
* One HITL mechanism for both `in_review` and `needs_input`; no duplicate
  node code.
* Reuse the ADR-0043 checkpoint/resume plumbing.
* Provenance: a user cancellation must be distinguishable from an
  evaluator abort.
* Keep the mechanism in shared plumbing (`klea_utils`) so every client
  (web, TUI, Streamlit) reuses it.

## Considered Options

* **A. Fresh-turn HITL** - an interrupt ends the run; the user's answer is
  the next turn.  (This is what `needs_input` does today.)
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
* **Answer == same execution.**  `Command(resume={"action": "answer",
  "text": ...})` returns the text from `interrupt()` inside the same
  node; the node writes the answer and routes back to the Planner with the
  plan intact.  `InitGraphState` is **not** re-run, so per-turn state
  survives.
* **Cancel == terminal, no execution.**  `Command(resume={"action":
  "cancel"})` sets `plan.status = "user_cancelled"`, appends a note-only
  message to `messages` ("user cancelled the run while awaiting an answer
  to: <question>"; no plan content), and routes to a terminal reply ->
  `END`.  The remaining plan never executes.  `user_cancelled` is added to
  the `PlanSchema.status` literal so a user cancel is distinguishable from
  an evaluator `aborted`.
* **Next turn after cancel is a fresh run.**  The structured plan is
  discarded (there is no use case for carrying an abandoned plan), but
  `messages`, `context_summary`, `mode`, and the session-scoped
  `artefacts` survive, so the Planner is aware of the prior cancellation.
  The cancellation note is written explicitly into `messages` because
  `AnswerFromResults` does not append the assistant reply to `messages`.

### Topology: one class, two instances

A single `AwaitHuman` node class (generalising `AwaitReview`) is
instantiated twice and registered by label:

| Trigger (`plan.status`) | Instance label | `kind` | Writes |
|---|---|---|---|
| `in_review` | "Awaiting review" | `review` | `human_feedback` |
| `needs_input` | "Awaiting input" | `input` | `human_input` |

Both instances loop back to the Planner (which owns every plan
transition).  Two instances (rather than one status-branching node) give
distinct labels in the stream/status/inspector and let a reloaded client
tell which ask is pending from `aget_state().tasks[*].name` alone.  The
`interrupt` payload carries `kind` and the ask text (the question, and for
review the rendered plan).

`human_input` is a new transient state field distinct from
`human_feedback`: a `needs_input` answer is new information, not review
feedback, and must not advance the review-round counter.  The Planner
consumes it (prompt variable plus finalising the partial `needs_input`
plan).

### API contract

`resume` stays as it is (ADR-0043): continue a graph from its last
checkpoint after a failure (invoke `None`; no query).  HITL is a distinct
set of fields on the chat payload:

* `interrupt_response: str | None` - the answer text.
* `interrupt_id: str | None` - the `id` of the interrupt being answered
  (framework-generated; echoed back for stale-answer safety).
* `interrupt_cancel: bool` - cancel the pending interrupt.

Exactly one of `resume`, `interrupt_response`, or `interrupt_cancel` may be
set.  Internally, answer -> `Command(resume={"action": "answer", "text":
...})` and cancel -> `Command(resume={"action": "cancel"})`.  The graph
wrappers (`run_graph_invoke`, `run_graph_astream_events`) accept a
`Command` as their input alongside `dict | None`.

### Backend invariant

`chat_core` reads `graph.graph.aget_state(config)` before starting a run.
If the thread has a pending interrupt (`tasks[*].interrupts` non-empty):

* a request carrying `interrupt_response` or `interrupt_cancel` resumes
  it (the echoed `interrupt_id`, when present, must match the pending one
  or the request is rejected);
* any other request (a plain query) is rejected with HTTP `409` ("this
  chat is awaiting your answer"), independent of the client.

This check is separate from, and runs before, the ADR-0043 single-flight
registry.

### Stream contract

Under `astream_events` v3 a paused run emits a `values` event whose
`params["interrupts"]` is non-empty.  `run_graph_astream_events` gains:

* a new event `{"type": "interrupt", "node": <label>, "data": {"kind":
  ..., "question": ..., "interrupt_id": ...}}`;
* suppression of the spurious `complete` frame it currently emits after a
  pause (`utils_pkg/klea_utils/graph/base.py`).

The on-pause event lets `chat_core` persist the question as an assistant
row and skip writing a final answer.

### Persistence

* Pause: the interrupt question is written as an assistant row.
* Resume (answer): the answer is written as a user row; the assistant row
  is written on a real `complete`.
* Cancel: the terminal "cancelled" reply is written on `complete`.
* Failure resume (ADR-0043) is unchanged: no user row.

### Consequences

* Good, because a paused plan is resumed with its state intact instead of
  being discarded.
* Good, because one class covers review and input.
* Good, because cancel is explicit, recorded (`user_cancelled`), and
  cannot be mistaken for approval.
* Good, because the "do not ignore an interrupt" invariant is enforced in
  the backend for every client.
* Good, because it reuses the ADR-0043 resume plumbing.
* Bad, because it adds state fields (`human_input`, a `user_cancelled`
  status) and three payload fields.
* Bad, because a resume skips `InitGraphState` (correct, but per-turn
  bookkeeping must remain entry-only).
* Bad, because pause/resume adds checkpoints to the append-only thread
  history (no prune; acceptable at current scale).
* Bad, because `interrupt_id` adds an API field and a validation path.

### Confirmation

* Unit tests: base wrapper emits the `interrupt` event and suppresses
  `complete`; `chat_core` returns `409` for a plain query while paused and
  maps `interrupt_response`/`interrupt_cancel` to `Command(resume=...)`;
  payload validation (exactly one action); `AwaitHuman` writes
  `human_feedback` / `human_input`; cancel sets `user_cancelled` and does
  not execute; a stale `interrupt_id` is rejected; reload re-presents the
  pending question from the checkpoint.
* Empirical semantics recorded in `devdocs/system/graph-resume.md`
  (`langgraph` 1.2.12): interrupt pause/resume, append-only history, and
  the plain-input-while-pending behaviour.
* E2E: the `needs_input` path is a deterministic interrupt trigger, so the
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
  lifecycle, `in_review`/`needs_input`, the current stub),
  `devdocs/adr/0041-plan-step-granularity-and-parallelism.md`
  (plan step granularity), `devdocs/backlog.md` (HITL item).
* ADR-0035 (line 349) and ADR-0041 (line 241) cite a stale ADR number for
  the HITL interrupt work; this is that ADR.  Those references and
  `devdocs/system/agent-general-path-control-flow.md` are updated in the
  documentation cascade for this change.
