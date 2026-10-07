# Streaming contract: events, sources, and consumers

Status: implemented.  Governs the events the graph streams to clients over the
`/query/stream` SSE endpoint (`klea_utils/api/sse.py`), emitted by
`BaseLangGraph.run_graph_astream_events` (`klea_utils/graph/base.py`) and
consumed by the web (`klea_utils/ui/web/nicegui/components/stream.py`) and TUI
clients.  The decision record is ADR-0040 (which amends ADR-0013).  The
transport layer additionally injects a periodic `ping` heartbeat to keep the
connection alive during long-running nodes; it is not a graph event.

## Channels

LangGraph's `astream_events(version="v3")` exposes three channels the runner
reads:

* **custom** - node-authored events (`write_custom_stream`): `progress`,
  `inspect`, `state`, `usage`, `tool`.  `_CustomChannelEnabler`
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
| `progress` | node (custom) | `{type, node, data{heading}}` | streaming spinner label (`data.heading`) | spinner text |
| `ping` | `chat_core` (heartbeat) | `{type}` | ignored (keeps the SSE alive) | ignored |
| `inspect` | node (custom) | `{type, node, data{heading, summary, details}}` | inspection pane entry (summary shown, `details` collapsed) | ignored |
| `state` | node (custom) | `{type, node, data{heading, summary, display, key, preformatted}}` | status pane section | ignored |
| `usage` | node (custom) | `{type, node, data{...tokens}}` | token totals | ignored |
| `tool` | node (custom, `ToolsCallerNode`) | `{type, node, data{tools:[{tool, title, header, mime, data, meta, display}]}}` | chat blocks (one per entry) | ignored |
| `token` | LLM (messages), opt-in only | `{type, content, node}` | ignored (no live typing) | ignored |
| `context` | runner (values) | `{type, data}` | status pane (mode/access) | ignored |
| `complete` | runner (terminal) | `{type, message_for_user}` | chat bubble + persisted | printed |
| `error` | `chat_core` (exception) | `{type, message, error_type, node}` | notification | spinner fail |

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

## Known limitations

* Node instances are singletons shared across concurrent runs.  Per-run
  execution scratch (the prompt/LLM/output snapshot the streaming and
  inspection hooks read) now lives in a per-run `NodeContext` created by
  `execute` and passed to each hook, so concurrent runs no longer
  interleave (ADR-0045).  Durable per-run mutable state (counters, picker
  attempts, step outputs) lives in graph state instead (ADR-0033).
* Inline media rendering (`image/*`, `audio/*`) is deferred to the `display`
  text fallback; `image/svg+xml` needs sanitisation.
* `tool` entries render in the web only; TUI ignores them.

## Pointers

* Ordered request/lifecycle view: `system/api-sse-sequence.md` (C4 dynamic:
  bootstrap, `POST /query/stream`, interrupt/resume/cancel) -- this file is the
  event catalogue those sequences draw from.
* Emission: `klea_utils/graph/base.py` (`run_graph_astream_events`,
  `_CustomChannelEnabler`), `klea_utils/nodes/abstract.py`
  (`NodeStreamData`, `NodeStreamEvent`, `_get_inspect`, `stream_tokens`),
  `klea_utils/nodes/tools_caller.py` (`tool` entries).
* Transport: `klea_utils/api/sse.py`, `klea_utils/api/chat_core.py`.
* Consumers: `klea_utils/ui/web/nicegui/components/{stream,chat_area,chat_bubble,status_pane,inspector}.py`.
* Decisions: ADR-0013 (amended), ADR-0040, ADR-0019, ADR-0020, ADR-0031,
  ADR-0032, ADR-0033.
