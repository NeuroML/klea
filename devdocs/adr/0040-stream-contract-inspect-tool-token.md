---
status: "accepted"
date: 2026-09-18
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Stream contract: `inspect` events, chat-renderable `tool` events, gated `token`

## Context and Problem Statement

ADR-0013 defined Klea's per-node inspection stream with **two** payload events
(`info` = one-line summary, `debug` = `info` plus prompts/raw output) plus
`state`, `usage`, `token`, `progress` and `complete`, and an intended UI split
("info by default, debug opt-in").  In practice the implementation drifted:

* the web inspector consumed only `debug` and ignored `info`, so `info` was
  dead weight and non-LLM nodes that emitted only `info` were invisible;
* `_post_exec_stream` emitted `info` and `debug` unconditionally, so `debug`
  was never "opt-in" - full prompts and raw model output always crossed the
  wire;
* `token` was emitted for **every** LLM call, including structured-output nodes
  whose deltas are JSON fragments, and no frontend consumed it;
* there was no channel for tool output that belongs in the **chat** (a file
  edit's diff), so the synthesised answer either had to reprint it or lose it.

`NodeStreamData` already encodes two display tiers (`heading`/`summary` always
shown, `details` collapsible), so the separate `info`/`debug` events were
duplicative rather than two genuine tiers.

## Decision Drivers

* Keep the inspection pane complete: the full flow must stay visible to the
  user (nothing removed from what the inspector already showed).
* One event per concern, named for its consumer: `inspect` -> inspection pane,
  `state` -> status pane, `tool` -> chat, matching the pane it feeds.
* No wasted stream traffic: a payload a consumer cannot use must not be sent
  (structured token fragments; `info` when `debug` subsumes it).
* Generalise tool output rendering without reinventing a type system: reuse
  MIME types (and MCP's own `mimeType` on typed content blocks) so third-party
  tools have a standard path.
* Keep the wire contract small and portable: a client that cannot render a
  MIME type must still be able to show something.

## Considered Options

* **A. Keep `info` + `debug`, fix the consumer.**  Rejected: the two are not
  tiers (`debug` is a strict superset) and both were already emitted always;
  the "tier" is summary vs `details`, which one event carries.
* **B. One `inspect` event + gated `token` + `tool` event (chosen).**
* **C. Per-tool events (`tool_diff`, `tool_image`, ...).**  Rejected: a
  proliferation of event types where a `mime` discriminator on one `tool`
  event suffices.

## Decision Outcome

Chosen option: "B. One `inspect` event + gated `token` + `tool` event".

* **`inspect` replaces `info` and `debug`.**  `NodeStreamEvent.type` is
  `Literal["inspect", "state", "usage"]`; a node implements a single
  `_get_inspect()` returning `NodeStreamData`.  The runner forwards
  `inspect`/`state`/`usage` (plus `progress`) from the custom channel.  The web
  inspector renders `inspect` entries; the summary is shown and `details` is
  collapsible.  Nodes that previously emitted only `info` now emit `inspect`
  and appear in the inspector.
* **`NodeStreamData` gains two fields.**  `key` lets several nodes update one
  status-pane section in place (empty = key by node label), used for a single
  live plan section shared by the Planner and Evaluator.  `preformatted`
  renders `display` as monospace `<pre>` instead of markdown, so aligned
  literal content (the plan, with tool names containing `_`) is not mangled;
  the plan was moved to this to avoid a syntax-highlighted `<pre>` box.
* **`tool` event (custom channel).**  `ToolsCallerNode` emits one `tool` event
  per dispatch round carrying `data["tools"]`, a list of entries
  `{tool, title, header, mime, data, meta, display}`.  Entries are built from
  typed MCP content blocks first (`ImageContent`/`AudioContent` ->
  `mimeType` + base64; `EmbeddedResource`/`ResourceLink` -> `mimeType` + uri),
  then structured conventions (`diff` -> `text/x-diff`; `code` + `language` ->
  `text/x-<language>`; a self-describing `display` dict
  `{"mime","data","meta"}`), then a string `display` -> `text/markdown`.  Plain
  `TextContent` is not surfaced (it is the JSON dump).  The web renders by
  MIME: `text/x-diff`/`text/x-patch` via Pygments' diff lexer, `text/markdown`
  as markdown, `text/x-<lang>` as code; media is deferred.  `display` is a
  preformatted text fallback for clients that cannot render the MIME.
* **MIME is the type vocabulary.**  `text/x-diff` and `text/x-patch` are the
  de-facto (not IANA-registered) types for unified diffs; MIME types come from
  MCP where available.  `image/svg+xml` is script-capable and must be
  sanitised before any inline rendering.
* **`token` is opt-in.**  `AbstractLangGraphNode.stream_tokens: ClassVar[bool]
  = False`; a free-text node sets it `True` (`AnswerGeneral` does), and the app
  adds its label to `BaseLangGraph.token_stream_nodes` at graph build time.
  The runner forwards `token` only for labels in that set, so structured-output
  nodes never emit JSON fragments.  No node on the agent task path opts in
  (its answer node is structured); RAG's general-answer node does.

### Consequences

* Good, because one `inspect` event removes the dead `info` tier and the
  always-on `debug` duplication while keeping the inspector's full detail
  (summary visible, `details` collapsed).
* Good, because `tool` gives file changes and other renderable output a
  first-class chat surface; the answer no longer has to reprint them.
* Good, because MIME dispatch (and MCP's `mimeType`) lets third-party tools
  participate without a Klea-specific schema, and `display` keeps every client
  able to show something.
* Good, because gating `token` removes per-delta SSE traffic and JSON-fragment
  noise from structured nodes.
* Bad, because `text/x-diff`/`text/x-patch` are conventions, not standards, and
  inline media/`<pre>` rendering needs sanitisation and CSS work.
* Bad, because the wire contract changed (a breaking change for any external
  consumer built on `info`/`debug`); pre-1.0, so accepted.

### Confirmation

* `utils_pkg/tests/test_stream_events.py`, `test_nodes_tools_caller.py`,
  `test_nodes_tools_picker.py`, `test_graph_base.py`; RAG/agent node tests.
* End-to-end: `klea cli` shows a live plan (`inspect` state sections), tool
  blocks for edits, and no token noise.

## More Information

* Amends ADR-0013 (inspection features).  Related: ADR-0019 (node contract),
  ADR-0020 (picker/caller), ADR-0031 (wire contract),
  `devdocs/system/streams.md` (event catalogue), ADR-0038 (a non-zero
  `run_command` exit is a normal result, not an error).
* Deferred: inline image/audio rendering; dependency-frontier batched
  execution (a plan-round design, not a stream concern).
