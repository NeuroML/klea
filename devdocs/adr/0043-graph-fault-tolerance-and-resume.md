---
status: "accepted"
date: 2026-09-29
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Graph fault tolerance: transient retry and checkpoint resume

## Context and Problem Statement

A node exception stops a LangGraph run: the exception propagates out of
`astream_events`, the API turns it into one SSE `error`, and the run ends.
Because the app re-enters at `START` (the entry node, which resets per-turn
state) on the next message, the failed turn's plan and observations are
discarded, and (previously) the turn was not even persisted.  A transient
LLM failure was enough to lose the whole task: a stream that stalled after
its `200 OK` (`StreamChunkTimeoutError`, classified `TIMEOUT`) was not
retried by the node (only overflow/truncation/empty were) nor by the
provider SDK (its retries cover the request, not a post-response stall).

The checkpointer already persists state before each super-step, and
LangGraph can resume a thread by invoking with `None`.  How should Klea
handle a failed node - retry it, fail gracefully, and/or let the user
resume the same run - without losing completed work?

## Decision Drivers

* Transient failures (timeouts) should be retried automatically; rate
  limits are already the SDK's job.
* The graph's adaptive retries (output-window shrink/grow,
  structured-to-plain fallback) mutate per-call state and cannot be
  replaced by a node-level `RetryPolicy`.
* A failed run must remain **resumable**: a graph-rendered failure answer
  would run to `END` and leave nothing to resume.
* Resume must not repeat completed tool side effects.
* A failed turn should stay visible and retryable (not silently lost).
* Keep the mechanism in the shared plumbing so every app/clients can reuse
  it, and so it can later back the HITL `interrupt`/`Command(resume=...)`
  work.

## Considered Options

* **A. Framework retry/error handling** - `RetryPolicy` / `error_handler`
  on nodes, routing failures to the answer node.
* **B. Invocation-level retries + checkpoint resume** - keep adaptive
  retries in `_invoke_with_retries`, add a bounded transient retry, let
  the exception stop the graph (resumable), and add a `resume` flag that
  invokes with `None`.
* **C. Status quo** - no transient retry, no resume; failed turns are lost.

## Decision Outcome

Chosen option: **B**, because it fixes the actual failure (transient
timeouts) while preserving the checkpointed work, and it keeps the
adaptive retry logic in the one place that can mutate the per-call
window (option A cannot).

* **Transient retry.** `BaseLLMNode._invoke_with_retries` retries an
  invocation classified `TIMEOUT` up to `MAX_TRANSIENT_RETRIES` (2) with
  exponential backoff, then re-raises.  Timeout classification also
  matches the exception type/class name (a stalled-stream error's message
  may not contain "timeout").  `RATE_LIMITED` stays with the SDK, which
  already retries `429`/`5xx` and honours `Retry-After`.
* **No failure answer.** A node error stops the graph at the failed node,
  so the checkpoint stays resumable.  UX is handled at the API/client
  layer instead.
* **Resume.** `BaseLangGraph.run_graph_astream_events(query=None)` passes
  `None` to `astream_events` (skipping the input merge); the graph
  re-enters the failed node, not the entry node.  A `resume: bool` flag on
  `/query/stream` triggers it; the request carries no query.
* **Persistence.** The user turn is written when the run starts and the
  assistant reply on `complete`, so a failed turn is recorded and a resume
  completes the same turn instead of duplicating it (one user row, one
  assistant row).
* **Client.** The web UI shows an inline error with a **Retry** action
  when the error event is `resumable`; Retry re-streams with
  `resume=true`.

### Consequences

* Good, because a stalled stream recovers without losing the run.
* Good, because completed tool side effects are not repeated on resume
  (only the failed node onward re-runs).
* Good, because a failed turn is visible, persisted, and retryable.
* Good, because the resume path is shared plumbing, ready for the HITL
  interrupt work.
* Bad, because resume is streaming-only (`/query` is synchronous and
  unchanged) and is a flag on the shared chat payload.
* Bad, because `InitGraphState` is skipped on resume, so any per-turn
  bookkeeping it does must not be needed by the resumed node (currently
  fine: it is entry-only).
* Bad, because a resume re-emits the failed node's progress events (the
  UI shows the node again); harmless but visible.

### Confirmation

* Unit tests: `utils_pkg/tests/test_llm_invoke_retries.py` (transient
  retry), `test_llm_invocation_errors.py` (type-based timeout),
  `test_graph_base.py` (`None` input), `test_chat_core.py`
  (resume + persistence), `test_stream_events.py` (resume payload);
  `agent_pkg/tests/test_chat_api.py` (payload validation, resume input,
  failed-turn persistence).
* Semantics recorded in `devdocs/system/graph-resume.md`.
* Manual/E2E: a fault-injection hook to force a node failure is tracked in
  `devdocs/backlog.md` (LangGraph has no external pause API).

## Pros and Cons of the Options

### Framework retry/error handling

* Good, because it is LangGraph-idiomatic and declarative.
* Bad, because `RetryPolicy` re-runs the whole node (duplicate progress
  events) and resets the per-call output window, losing the adaptive
  overflow/truncation logic.
* Bad, because routing a failure to the answer node ends the graph and
  forecloses resume.

### Invocation-level retries + checkpoint resume

* Good, because retries stay per-call (no duplicate node events) and can
  mutate the window.
* Good, because the checkpoint preserves completed work and resume is
  precise.
* Neutral, because resume needs API/UI plumbing.
* Bad, because it adds a `resume` flag and a persistence-order change.

### Status quo

* Good, because no change.
* Bad, because transient stalls lose whole runs.
* Bad, because failed turns are not persisted.

## More Information

* LangGraph fault tolerance: https://docs.langchain.com/oss/python/langgraph/fault-tolerance
* LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
* `devdocs/system/graph-resume.md` (confirmed semantics),
  `devdocs/adr/0017-llm-invoke-retry.md` (adaptive retries),
  `devdocs/adr/0023-sqlite-checkpointer.md` (checkpointer),
  `devdocs/adr/0035-general-operational-agent-path.md` (loop budgets).
* HITL `interrupt`/`Command(resume=...)` should reuse this resume path;
  see `devdocs/backlog.md`.
