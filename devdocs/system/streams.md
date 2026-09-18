# Streaming contract: events, sources, and consumers

Status: implemented.  Governs the events the graph streams to clients over the
`/query/stream` SSE endpoint (`klea_utils/api/sse.py`), emitted by
`BaseLangGraph.run_graph_astream_events` (`klea_utils/graph/base.py`) and
consumed by the web (`klea_utils/ui/web/nicegui/components/stream.py`) and TUI
clients.  The decision record is ADR-0040 (which amends ADR-0013).

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
| `progress` | node (custom) | `{type, node}` | streaming spinner label | spinner text |
| `inspect` | node (custom) | `{type, node, data{heading, summary, details}}` | inspection pane entry (summary shown, `details` collapsed) | ignored |
| `state` | node (custom) | `{type, node, data{heading, summary, display, key, preformatted}}` | status pane section | ignored |
| `usage` | node (custom) | `{type, node, data{...tokens}}` | token totals | ignored |
| `tool` | node (custom, `ToolsCallerNode`) | `{type, node, data{tools:[{tool, title, header, mime, data, meta, display}]}}` | chat blocks (one per entry) | ignored |
| `token` | LLM (messages), opt-in only | `{type, content, node}` | ignored (no live typing) | ignored |
| `context` | runner (values) | `{type, data}` | status pane (mode/access) | ignored |
| `complete` | runner (terminal) | `{type, message_for_user}` | chat bubble + persisted | printed |
| `error` | `chat_core` (exception) | `{type, message, error_type, node}` | notification | spinner fail |

### `inspect` (replaces `info`/`debug`)

One inspection event per node execution.  `_get_inspect()` returns
`NodeStreamData`; the two display tiers live *inside* the payload
(`summary` visible, `details` collapsible), not in two event types.  ADR-0013's
`info` (concise) and `debug` (full) were not tiers - `debug` is a strict
superset and both were emitted unconditionally - so they were collapsed.

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

* Node instances are singletons shared across concurrent runs; the `_last_*`
  snapshot fields used by the streaming hooks are instance state, so two
  concurrent runs on one orchestrator can interleave.  Per-run mutable state
  (counters, picker attempts, step outputs) lives in graph state instead
  (ADR-0033).  Refactoring the `_last_*` snapshots is outstanding.
* Inline media rendering (`image/*`, `audio/*`) is deferred to the `display`
  text fallback; `image/svg+xml` needs sanitisation.
* `tool` entries render in the web only; TUI ignores them.

## Pointers

* Emission: `klea_utils/graph/base.py` (`run_graph_astream_events`,
  `_CustomChannelEnabler`), `klea_utils/nodes/abstract.py`
  (`NodeStreamData`, `NodeStreamEvent`, `_get_inspect`, `stream_tokens`),
  `klea_utils/nodes/tools_caller.py` (`tool` entries).
* Transport: `klea_utils/api/sse.py`, `klea_utils/api/chat_core.py`.
* Consumers: `klea_utils/ui/web/nicegui/components/{stream,chat_area,chat_bubble,status_pane,inspector}.py`.
* Decisions: ADR-0013 (amended), ADR-0040, ADR-0019, ADR-0020, ADR-0031,
  ADR-0032, ADR-0033.
