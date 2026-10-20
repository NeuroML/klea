# Streaming contract: events, sources, and consumers

Status: implemented.  Governs the events the graph streams to clients over the
`/query/stream` SSE endpoint (`klea_utils/api/sse.py`), emitted by
`BaseLangGraph.run_graph_astream_events` (`klea_utils/graph/base.py`) and
consumed by the web (`klea_utils/ui/web/nicegui/components/stream.py`) and TUI
clients.  The decision record is ADR-0040 (which amends ADR-0013).  The
transport layer additionally injects a periodic `ping` heartbeat to keep the
connection alive during long-running nodes; it is not a graph event.

A stream covers exactly one turn and terminates at `complete`/`error` (or
`interrupt`); there is no persistent API connection across turns -- continuity
is server-side (the checkpoint `thread_id`).  The request/lifecycle view is
`system/api-sse-sequence.md`.

## Channels

LangGraph's `astream_events(version="v3")` exposes three channels the runner
reads:

* **custom** - node-authored events (`write_custom_stream`): `progress`,
  `inspect`, `state`, `usage`, `tool`, `notice`.  `_CustomChannelEnabler`
  (`graph/base.py`) declares `required_stream_modes = ("custom",)` so the
  channel is enabled.
* **messages** - LLM token deltas (`content-block-delta`); forwarded as `token`
  only for opted-in nodes.
* **values** - state snapshots; projected through `context_snapshot` into
  `context` events (ADR-0032).

The runner then emits a terminal `complete`; `chat_core` emits `error` on an
exception.

## Events

| Event | Source | Shape | Web consumer | TUI |
|-------|--------|-------|--------------|-----|
| `progress` | node (custom) | `{type, node, data{heading}}` | streaming spinner label (`data.heading`) | turn-status heading |
| `ping` | `chat_core` (heartbeat) | `{type}` | ignored (keeps the SSE alive) | ignored |
| `inspect` | node (custom) | `{type, node, data{heading, summary, details}}` | inspection pane entry (summary shown, `details` collapsed) | inspector view entry (chat-panel toggle) |
| `state` | node (custom) | `{type, node, data{heading, summary, display, key, preformatted}}` | status pane section | status-panel section |
| `usage` | node (custom) | `{type, node, data{...tokens}}` | token totals | token totals |
| `tool` | node (custom, `ToolsCallerNode`) | `{type, node, data{tools:[{tool, title, header, mime, data, meta, display}]}}` | chat blocks (one per entry) | transcript block (MIME-aware) |
| `notice` | node (custom) | `{type, node, data{heading, summary, details{content, kind}}}` | compact chat progress note | muted transcript line |
| `token` | LLM (messages), opt-in only | `{type, content, node}` | ignored (no live typing) | ignored (no live typing) |
| `context` | runner (values) | `{type, data}` | status pane (mode/access) | stored on the chat; status-panel mode/access slots refresh |
| `interrupt` | node (HITL `interrupt()`) | `{type, node, data{kind, headline, question/questions, sections, interrupt_id, hitl_response_schema}}` | ask form replacing the composer | inline ask panel replacing the composer |
| `complete` | runner (terminal) | `{type, message_for_user}` | chat bubble + persisted | transcript message |
| `error` | `chat_core` (exception) | `{type, message, error_type, node}` | notification | turn-status error (+ Retry) |

`interrupt` is the HITL pause (ADR-0046): the stream ends at the pause and is
continued by a later `interrupt_response`/`interrupt_cancel` request.  Its
`kind` is authored by the interrupting node: `review` (a `review` step:
plan approval or a progress checkpoint) / `input` (a blocked step's questions)
from the agent's `AwaitHuman` nodes, or `permission` (the shared
`ToolsCallerNode`, pausing before a call that would touch a path outside the
permitted roots; ADR-0007 update 2026-10-08).  A `review` ask carries a
`headline` and a list of `sections` (`{title, body}`) for the **pending** steps
the approval unblocks (ADR-0051): a review before any work presents the whole
plan under approval; a checkpoint shows the remaining steps.  Every pending
step is listed, including the `review` gate steps themselves (marked as human
review), so the plan is not shown incomplete.  The headline counts the pending
work steps only: the gate is the approval action, not work it unblocks.  A
checkpoint may also carry a `Progress` section: a synthesized digest of the
work completed since the last review, so the decision does not require reading
the whole transcript.  Completed steps and the step findings are not otherwise
included (the findings are in the transcript and the inspection pane).  Both
frontends render the sections the same way, and both show the ask in place of
the composer (the input is hidden while an ask is active, so the transcript
stays scrollable).  A review step must gate pending work; a *trailing*
review is rejected by the Planner's validator and resolved defensively by the
router.  A permission ask shows the tool call
(`tool: argument = raw (confidence)`) and its per-path decisions are
`once`/`session`/`deny`, plus `not_a_path` (a per-dispatch dismissal for a
guessed value; ADR-0007 update 2026-10-14).  See ADR-0051 for review steps.

`context` carries the checkpointed session context (e.g. the agent operating
mode / tool access, ADR-0030/0032).  The web frontend stores it on the chat and
runs the page's `context_hooks`, so a context control reconciles its per-chat
preference with the value the server just reported - for example after a
`/mode` graph command changes the checkpointed mode (ADR-0047); without it the
stale preference (resolved first) would keep the selector on the old value.
The TUI stores it on the chat and rebuilds the status-panel app slots on a
`context` change (`StatusPanel.set_state(refresh_extras=True)`), and its
mode/access selectors read the resolved value from the chat's context.

## Persistence and hydration (ADR-0032 update)

A reloaded chat must match the live session, so the UI-visible outputs are
persisted server-side and rehydrated on load:

* **Transcript** - `sessions_db.messages`: the `user`/`assistant` turns
  written by `chat_core`, plus one `tool` row per chat-renderable tool entry
  (`role="tool"`, `content` = the display fallback, rendering keys
  `header`/`mime`/`data`/`meta`/`is_error` in the row's `metadata`), plus a
  `notice` row for each user-facing progress note (`role="notice"`, with the
  emitting node as `header`).  Hydrated via
  `GET /chat/{user_id}/{chat_id}/messages`;
  `server_message_to_data` rebuilds the frontend message shape.
* **Inspection/status panes** - `sessions_db.chat_events`:
  `chat_core.stream_response` persists the pane events (`inspect`, `state`)
  plus a leading per-turn `query` marker (only on a new turn), flushed when
  the run ends.  `GET /chat/{user_id}/{chat_id}/events` returns them, and the
  frontends replay them through the shared reducer
  (`ui/stream_events.py:replay_chat_events`): a `query` event opens a new
  inspector section (the live marker), the rest reduce exactly as live.
* **Session context and token totals** - checkpointed state, projected by
  `GET /chat/{user_id}/{chat_id}/context`: the app `context_snapshot` hook
  plus the cumulative `usage_metrics` (the frontends seed `token_usage` from
  it).  `usage` events are therefore **not** stored: they are a projection of
  checkpointed state (same reasoning as `context`; see ADR-0032).

Only these UI-relevant events are persisted; `progress`/`ping`/`token` are
transient, and `complete`/`tool`/`notice`/`interrupt` are covered by the
transcript.
Deleting a chat (or all of a user's chats) purges both `messages` and
`chat_events`.

### `progress`

`node` is the emitting node's label and is the stable identity the runner
keys timing and dedup on; `data.heading` is the line shown while the node
runs (it defaults to the label).  A same-node heading change - an LLM invoke
retry - is forwarded without resetting the node timer, so retries appear as
`<label> (retry n/m: <reason>)` on the spinner (`_emit_progress` on
`AbstractLangGraphNode`, `_emit_retry` on `BaseLLMNode`).

### `ping` (heartbeat)

Injected by `chat_core.stream_response` (not by a node or the graph runner).
A long-running node (e.g. `run_command` with a large timeout) emits no graph
events while it runs, so `_heartbeat` wraps the event stream and, whenever no
event arrives within `HEARTBEAT_INTERVAL_SECONDS` (15 s), forwards a
`{"type": "ping"}` frame.  This keeps the client's idle read timeout
(`STREAM_READ_TIMEOUT_SECONDS`, 300 s, in `sse.py`) and intermediary proxies
from dropping the stream mid-task.  Consumers ignore `ping`; it carries no
payload and is not persisted.

The heartbeat reads the next graph event in a separate task and waits with
`asyncio.wait`, so the timeout never cancels the running node/tool (unlike
`asyncio.wait_for`/`asyncio.timeout`, which would).  The wrapped stream is
closed via `contextlib.aclosing`, so a client disconnect or Stop still cancels
the graph run.

### `inspect` (replaces `info`/`debug`)

One inspection event per node execution.  `_get_inspect()` returns
`NodeStreamData`; the two display tiers live *inside* the payload
(`summary` visible, `details` collapsible), not in two event types.  ADR-0013's
`info` (concise) and `debug` (full) were not tiers - `debug` is a strict
superset and both were emitted unconditionally - so they were collapsed.
LLM invoke retries also emit an `inspect` entry mid-run (`heading="Retry"`,
`details` = `{attempt, max, reason, action?, max_tokens?}`) so the retry
history is visible alongside the node's own inspection entry.

### `state`

Status-pane content.  `NodeStreamData.key` lets several nodes update one
section in place (empty = key by node label); the Planner and Evaluator share
`key="plan"` so the plan section is live but singular.  `NodeStreamData.preformatted`
renders `display` as monospace `<pre>` (the plan) so tool names with `_` are
not parsed as markdown.

### `notice` (chat progress note)

A concise, user-facing progress note derived from an internal node result: an
evaluator verdict/reason (`Evaluating`) or a reasoning conclusion
(`Reasoning`).  It is append-only transcript history, not a status snapshot:
the status pane keeps the current plan/state, while notices explain what
happened in order.  `chat_core` persists each notice as a `role="notice"`
transcript row; it is **not** added to graph `messages` or
`messages_internal` (the underlying evaluator/reasoning data is already
there).  Long content is truncated by the emitting node to keep the chat
usable.

### `tool` (chat-renderable output)

One event per dispatch round; one entry per renderable result.  `mime` is the
type vocabulary, sourced from typed MCP content blocks (`ImageContent` /
`AudioContent` / `EmbeddedResource` / `ResourceLink` carry `mimeType`) or from
structured conventions (`diff` -> `text/x-diff`, `code` -> `text/x-<language>`,
self-describing `display` dict).  The web renders by MIME; `display` is a text
fallback for clients that cannot.  `text/x-diff` / `text/x-patch` are de-facto,
not IANA.  Plain `TextContent` is never surfaced.  Errors are not displayed
(their diagnosis is in `inspect`).  See ADR-0040 for the full rules.

Note: a tool result is also recorded in graph state as a `StepOutput`
(`result`, `tool`, `displayed`) and rendered into observations by
`KleaAgentState.observations_text()` with `displayed_to_user: yes|no`, so the
synthesised answer can avoid reprinting what the chat already showed.

### `token` (gated)

Emitted from the `messages` channel only for node labels in
`BaseLangGraph.token_stream_nodes`.  A node opts in with
`stream_tokens = True`; free-text answer nodes do (`AnswerGeneral`), structured
output nodes do not (their deltas are JSON fragments).  No web/TUI consumer
today; it exists for a future live-typing UI.

**Currently disabled.**  Klea disables streaming on the model *instance* in
`LLMModel.build_config` (`disable_streaming=True`; HuggingFace uses its
`streaming=False` field instead, which does not leak into the inference
params): the graph's `astream_events` forces chat models to stream, and
LangChain's streamed `usage_metadata` merge then sums input/total tokens across
chunks for providers that report usage per chunk (VLLM, HuggingFace Inference
Providers, ...), inflating the reported token counts (langchain#30429).  It
must be an instance field, not a per-call `stream=False` kwarg:
`with_structured_output` wraps the model in a `RunnableMap` that drops call
kwargs, so the flag would be ignored on the structured path.  No `token` events
are emitted until the upstream bug is fixed; see the backlog.

## Known limitations

* Node instances are singletons shared across concurrent runs.  Per-run
  execution scratch (the prompt/LLM/output snapshot the streaming and
  inspection hooks read) now lives in a per-run `NodeContext` created by
  `execute` and passed to each hook, so concurrent runs no longer
  interleave (ADR-0045).  Durable per-run mutable state (counters, picker
  attempts, step outputs) lives in graph state instead (ADR-0033).
* Inline media rendering (`image/*`, `audio/*`) is deferred to the `display`
  text fallback; `image/svg+xml` needs sanitisation.

## Pointers

* Ordered request/lifecycle view: `system/api-sse-sequence.md` (C4 dynamic:
  bootstrap, `POST /query/stream`, interrupt/resume/cancel) -- this file is the
  event catalogue those sequences draw from.
* Emission: `klea_utils/graph/base.py` (`run_graph_astream_events`,
  `_CustomChannelEnabler`), `klea_utils/nodes/abstract.py`
  (`NodeStreamData`, `NodeStreamEvent`, `_get_inspect`, `stream_tokens`),
  `klea_utils/nodes/tools_caller.py` (`tool` entries and the `permission`
  interrupt).
* Transport: `klea_utils/api/sse.py`, `klea_utils/api/chat_core.py`.
* Consumers: `klea_utils/ui/web/nicegui/components/{stream,chat_area,chat_bubble,status_pane,inspector}.py`
  (web) and `klea_utils/ui/tui/app.py` + `ui/tui/components/*` (TUI); the
  framework-agnostic reducer is `klea_utils/ui/stream_events.py`.
* Decisions: ADR-0013 (amended), ADR-0040, ADR-0019, ADR-0020, ADR-0031,
  ADR-0032, ADR-0033.
