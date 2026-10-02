# Graph fault tolerance and resume

Status: reference note.  Confirms LangGraph semantics we rely on for
fault tolerance; the transient-retry and resume-by-flag work is tracked in
`devdocs/backlog.md`.

Last updated: 2026-10-03.

## Scope

What happens when a node raises mid-run, and what it means to resume a
checkpointed graph.  Verified empirically against the installed
`langgraph` (see the spike in the session log); this note records the
findings so the resume wiring does not have to re-derive them.

## Confirmed semantics

A minimal graph (`init -> a -> boom -> done`, in-memory checkpointer,
`boom` raises on its first attempt) behaves as follows.

* **The exception stops the run at the failed node.**  After the failure,
  `graph.aget_state(config)` reports `next == ("boom",)`, values at the
  **last successful super-step** (`log == ["init", "a"]`), and `tasks`
  contains the failed task with its error.  The failed node's partial
  writes are **not** committed.
* **Resume with `None` re-runs only the failed node and downstream.**
  `await graph.astream_events(None, config=config)` continues from the
  checkpoint: only `boom` (and then `done`) execute; `init`/`a` are **not**
  re-run.  Completed tool side effects before the failure are therefore
  not repeated.
* **A fresh input after a failure starts a clean turn.**  Invoking with a
  new `{"query": ...}` runs from `START` again (including the entry node
  that resets per-turn state) and appends to the same thread state; the
  failed run is abandoned.  There is no "pending task" error - which is
  why the app already works when the user types a new message after an
  error.
* **No failure answer if resume must stay possible.**  Routing a node
  error to the final-answer node would run the graph to `END`, leaving no
  pending task to resume.  To keep a failed run resumable, let the
  exception stop the graph (state preserved) and handle UX/persistence at
  the API/client layer.

## Edge cases

* Resume with **nothing pending** (`next == ()`, e.g. after completion) is
  a harmless no-op returning the current state.
* Resume with **no checkpoint at all** raises
  `EmptyInputError: Received no input for __start__`.  A resume endpoint
  must guard this and return a clear message instead of a 500.

## Wrapper detail

`CompiledStateGraph.astream_events` is an async function that returns the
event stream, so it is awaited and then iterated - exactly as
`BaseLangGraph.run_graph_astream_events` does
(`utils_pkg/klea_utils/graph/base.py`).  Resume needs only the first
argument to become `None` (skip building/merging the input state); the
`thread_id` config is unchanged.

Because resume does not re-enter the entry node, per-turn state that the
entry node resets (plan, observations, counters) survives across a
resume.  A resume therefore continues the same run with state intact,
whereas a new query is a new turn.

## Cancellation

Verified against the installed `langgraph`: the checkpointer is a log, not
a mutex, and there is no graph-level cancel/pause API.  A run is stopped by
cancelling the `asyncio.Task` that drives it (the request task for
`/query`, the `StreamingResponse` generator's task for `/query/stream`).

* **`CancelledError` reaches the node.**  It is raised at the node/tool's
  next `await` and propagates through `astream_events`; LangGraph leaves a
  recoverable checkpoint.  A node doing synchronous, non-awaiting work (or
  one that swallows `CancelledError`) is not interruptible until it yields.
* **The checkpoint is left resumable.**  After a cancel the thread reports
  `next == (<node>,)` with the values from the last completed super-step.
* **No reset is needed for the next turn.**  A new `{"query": ...}` on the
  same thread starts from `START` and the abandoned half-run is discarded;
  state simply appends to the thread.
* **No resume-from-cancel.**  Klea does not offer resuming a user-cancelled
  run: a resume would re-run the cancelled node and could repeat a
  server-side tool side effect that is still running (server-side tool
  cancellation is deferred, ADR-0043).  Cancel shows a plain "Stopped"
  marker; Retry remains for the *error* path only.
* **Single-flight.**  Two concurrent same-thread runs would race their
  checkpoint writes; the per-thread registry (`klea_utils/api/runs.py`)
  rejects the second with HTTP 409, and `POST /query/cancel` cancels the
  active one.

## Implications for the planned work

* Transient LLM failures that currently abort the run (for example a
  post-200 streaming stall, `StreamChunkTimeoutError`) should be retried at
  the invocation level (`BaseLLMNode._invoke_with_retries`) before the
  graph is allowed to stop.
* The resume endpoint is a flag on the chat endpoints (`/query` and
  `/query/stream`); it passes no query (`None` input) and only the identity
  needed to derive the `thread_id`.
* Persistence: record the user turn when the run starts and the assistant
  reply when it completes, so a failed turn is visible/retryable and a
  resume completes the same turn rather than duplicating it.

## References

* LangGraph fault tolerance: https://docs.langchain.com/oss/python/langgraph/fault-tolerance
* LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
* `devdocs/adr/0017-llm-invoke-retry.md` (LLM retry policy),
  `devdocs/adr/0023-sqlite-checkpointer.md` (checkpointer),
  `devdocs/adr/0035-general-operational-agent-path.md` (loop budgets)
* `devdocs/system/agent-general-path-control-flow.md` (node/edge topology)
* `devdocs/backlog.md` (HITL interrupt/resume semantics; shares the resume
  path)
