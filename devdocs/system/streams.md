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
| `usage` | node (custom) | `{type, node, data{details{input_tokens, output_tokens, total_tokens, cached_tokens, reasoning_tokens, role}}}` | token totals | token totals |
| `tool` | node (custom, `ToolsCallerNode`) | `{type, node, data{tools:[{tool, title, header, mime, data, meta, display}]}}` | chat blocks (one per entry) | transcript block (MIME-aware) |
| `notice` | node (custom) | `{type, node, data{heading, summary, details{content, kind}}}` | collapsible chat progress note (full summary) | muted transcript line (collapsible) |
| `token` | LLM (messages), opt-in only | `{type, content, node}` | ignored (no live typing) | ignored (no live typing) |
| `context` | runner (values) | `{type, data}` | status pane (mode/access) | stored on the chat; status-panel mode/access slots refresh |
| `interrupt` | node (HITL `interrupt()`) | `{type, node, data{kind, headline, question/questions, sections, steps, interrupt_id, hitl_response_schema}}` (`steps` permission-only) | ask form replacing the composer | inline ask panel replacing the composer |
| `complete` | runner (terminal) | `{type, message_for_user}` | chat bubble + persisted | transcript message |
| `error` | `chat_core` (exception) | `{type, message, error_type, node}` | notification | turn-status error (+ Retry) |

`interrupt` is the HITL pause (ADR-0046): the stream ends at the pause and is
continued by a later `interrupt_response`/`interrupt_cancel` request.  Its
`kind` is authored by the interrupting node: `review` (a `review` step:
plan approval or a progress checkpoint) / `input` (a blocked step's questions)
from the agent's `AwaitHuman` nodes; `permission` (the shared
`ToolsCallerNode`, pausing before a call that would touch a path outside the
permitted roots; ADR-0007 update 2026-10-08); or `state_edit` (a session
command editing generic state fields via `klea_utils.commands.state_edit`).  A `review` ask carries a
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
router.  A permission ask shows the full target path (`path`, or a
middle-truncated `path_display` for pathological values), the session
approval directory separately (`directory`/`directory_display`) when it
differs, and the tool-call context (`tool: argument = argument_preview
(confidence)`) plus the detected token/source when useful.  Each request
carries the full `path`/`directory` and decision `key` plus bounded
`path_display`, `directory_display`, `raw_preview`, and `argument_preview`
fields.  Display-only plan-step context may also ride the ask: each request
carries a `steps` list of `{step_number, summary}` entries (the plan steps the
dispatching call was bound for), and the payload hoists a de-duplicated,
step-number-ordered `steps` list of the same shape, empty when no request
carries one.  It never contributes to a decision key (decisions still key on
`path`/`directory`); each summary is collapsed to one line and cut to 160
characters with an ASCII `...`, and at most 8 entries are carried, so a
pathological plan cannot flood the ask.  Its per-path decisions are
`once`/`session`/`deny`, plus `not_a_path` (a per-dispatch dismissal for a
guessed value; ADR-0007 update 2026-10-14); guessed values keep the `session`
option.  See ADR-0051 for review steps.

An `input` ask carries `kind` plus `questions`, each entry carrying the
blocked step's `step_number`, its `summary` and its `question`, and no
`sections`; review asks carry `sections`.  The resume payload is unchanged
(`answers` / `action`).

`context` carries the checkpointed session context (e.g. the agent operating
mode / tool access, ADR-0030/0032).  The web frontend stores it on the chat and
runs the page's `context_hooks`, so a context control reconciles its per-chat
preference with the value the server just reported - for example after a
`/mode` graph command changes the checkpointed mode (ADR-0047); without it the
stale preference (resolved first) would keep the selector on the old value.
The TUI stores it on the chat and rebuilds the status-panel app slots on a
`context` change (`StatusPanel.set_state(refresh_extras=True)`), and its
mode/access selectors read the resolved value from the chat's context.

### Event identity and attempt metadata

Every node-scoped event (`progress`, `inspect`, `state`, `usage`, `tool`,
`notice`, `token`, `interrupt`) also carries identity fields stamped by the
graph runner in `run_graph_astream_events`
(`utils_pkg/klea_utils/graph/base.py::_stream_identity`), never by a node;
timing and dedup still key on the display `node` label.

* `node_id` - the envelope's `metadata["langgraph_node"]`, falling back to the
  display label when the envelope carries no metadata.
* `run_id` - the yielding invocation's run id when present; per-invocation, so
  repeated executions of one node can be told apart.
* `step` - the integer `metadata["langgraph_step"]`, present only when
  supplied.

`context` and `complete` are graph-level and carry `run_id` only.  All three
fields are additive: consumers that do not need them may ignore them, and a
persisted `inspect`/`state` event replays unchanged.

Two attempt counters ride the same payloads:

* `usage.data.details.llm_calls` - integer, the LLM invocations folded into
  this node execution's token totals (validation retries included), present
  only with a usage event, so it is >= 1 whenever present.  Usage is
  attributed per node execution; the turn-level totals in `api/context.py` and
  `ui/stream_events.py` stay aggregates, not per-call attribution.
* `retry_count` - the 0-based count of the plan step's previous consecutive
  failures from ADaPT (`tool_retry_counts[step]`), cleared when the step makes
  progress, and stamped on every `tool` entry and on the tools caller's and
  triage router's `inspect`/`state` payloads.  Consumers add one for a 1-based
  attempt number.  It is `0` on a plan-less run such as Klea RAG, escalation
  tests it against `max_retries`, and HTTP/LLM transport retries never affect
  it.

## Persistence and hydration (ADR-0032 update)

A reloaded chat must match the live session, so the UI-visible outputs are
persisted server-side and rehydrated on load:

* **Transcript** - `sessions_db.messages`: the `user`/`assistant` turns
  written by `chat_core`, plus one `tool` row per chat-renderable tool entry
  (`role="tool"`, `content` = the display fallback, rendering keys
  `header`/`mime`/`data`/`meta`/`is_error` in the row's `metadata`), plus a
  `notice` row for each user-facing progress note (`role="notice"`, with the
  emitting node as `header`), plus a `tool` row for each call still running
  when the run was stopped (see `state` above).  Hydrated via
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
  plus the cumulative `token_usage_ledger`.  The ledger is authoritative: it
  maps the effective model string a node reports (`unknown` when no model is
  configured) to that model's `TokenUsage`, and is reduced by
  `add_usage_ledger`, so nodes writing different models coexist and per-model
  totals stay additive.  The endpoint projects the flat thread total via
  `ledger.total()` (which the frontends seed `token_usage` from) and the
  per-model breakdown via `usage_by_model`: per-model `TokenUsage` dumps keyed
  by model name, `None` when the ledger is absent; `usage` remains the thread
  total.  The frontends render the usage group as a collapsible whose heading shows
  the thread total as a compact abbreviated figure (for example `3 in/1 out/2 cached`),
  while the exact counts stay in the expanded rows: the body groups one entry per
  model under its provider and gives each model its input figure (with the cached
  subset when non-zero) and its output figure on separate indented lines beneath
  the model name (a key carrying no provider namespace falls back to a
  provider-less group, so no model entry is dropped).  The live
  `usage` stream event carries no model identity, so the breakdown is
  hydration-sourced only.
  `cached_tokens` is the cache-read subset of `input_tokens`, not an
  additional quantity; the status lines show it as `(<n> cached)` only when
  non-zero.  `usage` events are therefore **not** stored: they are a projection
  of checkpointed state (same reasoning as `context`; see ADR-0032).

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

On a step-incomplete retry the Evaluator emits its normal `inspect` entry with
additive retry keys and no new event type: `kind: "evaluator_retry"`,
`plan_step` (the step being retried), `attempt` (1-based, from
`step_attempt_counts`) and `max_attempts` (the evaluator's cap).  The reason
still rides `evaluations[step]["reason"]`.  These keys are additive, so no
web/TUI change is needed: the shared reducer (`ui/stream_events.py:88-107`)
sums only its fixed numeric key set, and `format_details`
(`ui/inspect_format.py:118-142`) renders arbitrary keys except
`retry_count`/`max_retries`.

Some `details` keys are evaluation/harness-only: `retry_count` and
`max_retries` ride the `inspect`, `state` and `tool` payloads for the harness
and are not user-facing, and `details` itself is excluded because it is the
`NodeStreamData` container echoing itself, never user data.  No frontend may
render them.  The enforcement point
is `klea_utils/ui/inspect_format.py`: `NOT_DISPLAYED_DETAIL_KEYS` lists the
keys, `drop_not_displayed` strips them from a `details` mapping at any depth, and
`format_details`
(the `details` renderer) applies it, so a frontend that formats through
`format_details` never shows them.

### `state`

Status-pane content.  `NodeStreamData.key` lets several nodes update one
section in place (empty = key by node label); the Planner and Evaluator share
`key="plan"` so the plan section is live but singular.  `NodeStreamData.preformatted`
renders `display` as monospace `<pre>` (the plan) so tool names with `_` are
not parsed as markdown.

The tool round's `running` section (`_get_running_tools`, key
`TOOL_ROUND_SECTION_KEY`) also lists each dispatched call in
`details.running_calls` (`tool`, `title` and the `tool(arg=...)` call line).
The section emitted before the gates has an empty list; `dispatch_tool_calls`
calls `on_dispatch` with the calls that passed every gate just before sending
them, and the node re-emits the section with only those, so a call denied by
a gate or waiting on a permission ask is never reported as still running.
The end-of-round `ok`/`failed` section replaces it and has none.  When a run
is stopped mid-round, `ui/stream_events.py:stopped_tool_entries` turns those
calls into error `tool` entries with `STOPPED_TOOL_NOTE` ("Run cancelled but
this tool may still be running. Please terminate it manually."), because
Stop does not yet cancel a tool on the MCP server (#254).  `chat_core` stores
them as `tool` rows, and the frontends show the same blocks on Stop through
`apply_stopped_tool_round`, so the live chat and a reload match.

The shared `ToolsCallerNode` also emits its own `state` event at the start of a
dispatch round, alongside (not inside) the running-tools entry, marking the
steps in the batch.  Its data is a `NodeStreamData` with `key="plan"`.  It
renders the plan with `current_numbers` set to the distinct `.step` numbers
stamped on `state.tool_calls`, so every pending step of a parallel batch is
marked current instead of only the next runnable one.  Those same `.step`
numbers label a path-permission ask's `steps` context (see above), whose
summaries come from the plan keyed by the step the dispatching call was bound
for, so the plan pane and the ask agree on which step wants the path.  When the run carries a
goal (`state.goal.goal`), the section begins with a single `Goal: ...` line
and a blank line before the steps.  The line comes from `GoalSchema.goal_label`
(`agent_pkg/klea_agent/schemas.py`), which renders the label and owns the
whitespace-collapse and 80-visible-character truncation rule (cut with an ASCII
`...` when longer).  The `klea_utils` fallback obtains it by duck typing the
goal object's `goal_label` attribute, so it stays independent and never imports
`klea_agent`.  Both the plaintext `display`
and the agent-side structured content carry that line and the blank
line after it.  This is **render-only**:
no plan write and no persisted `in_progress` status -- the plan is still
authored, advanced and resolved exclusively by the Planner and the Evaluator,
whose own plan event follows the round.  The entry is emitted only when the
state carries a plan and the batch stamped at least one step number, so a
plan-less run (Klea RAG) emits exactly the status entries it emitted before:
no plan, no render, no dispatched step numbers.

The payload is built in the overridable `_get_running_steps_state` hook, which
returns `None` when its guard fails (`execute` then emits nothing).  The caller
lives in the lower `klea_utils` package, so the base hook contributes only the
preformatted `display` fallback and no structured content blocks: those stay
with the agent-side emitters (`klea_agent.nodes.plan_content`), which this
package must not import.  Klea Agent subclasses the caller
(`AgentToolsCaller`) and overrides the hook to forward the state's goal and the
batch's dispatched step numbers as `current_numbers`, then attaches
`content=plan_content_blocks(plan, goal=goal, current_numbers=...)`, so all three
`key="plan"` emitters (the Planner, the Evaluator and the tools caller) carry
structured blocks and the tools caller's structured content leads with the goal
line too.  Its step items read `Step N/total: summary` (`N` the dispatched step
number, `total` the number of plan steps), the same progress text the plaintext
`display` shows, so the glyphed and plain renderings report the same position.
RAG keeps the base behaviour and its payload is unchanged.

The tool-execution status section body renders one markdown bullet per distinct
tool, `- **<title>**: ok xN, failed xM`, grouped by the raw tool name, in
first-seen call order, with a zero count omitted (a tool with no failures shows
only `ok xN`); per-call detail stays in the section's `details` payload behind
the collapsible.

### `usage`

Token accounting for one node execution.  `details` carries the top-level
totals (`input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens`,
`reasoning_tokens`, `role`), the `llm_calls` counter (above), and the additive
`llm_usage` list: one dict per LLM invocation in the node execution, in call
order and with validation retries included, each carrying that invocation's
`input_tokens`, `output_tokens`, `total_tokens`, `cached_tokens` and
`reasoning_tokens`.  The top-level totals and `llm_calls` are unchanged, so a
consumer that ignores `llm_usage` sees the event it always saw; the peak
context of the step is derivable as `max(input_tokens + output_tokens)` over
the list.  The list is empty when no usage was recorded.  `llm_usage` is
live-stream only: `usage` events are not persisted (`chat_core` stores only
`inspect`/`state`), so a reloaded chat sees only the checkpointed cumulative
totals, never the per-call list.

### `notice` (chat progress note)

A concise, user-facing progress note derived from an internal node result: the
Planner's newly fixed goal (with its success criteria when set), an evaluator
verdict/reason (`Evaluating`), or a reasoning conclusion (`Reasoning`).  It is
append-only transcript history, not a status snapshot: the status pane keeps
the current plan/state, while notices explain what happened in order.
Evaluator notices use user-facing step labels (`DONE`, `SKIPPED`, `INCOMPLETE`,
`NEEDS REVISION`) and an effective overall label (`PLAN COMPLETE`,
`CANNOT COMPLETE`), never the schema verdict literals.  `chat_core` persists
each notice as a `role="notice"` transcript row; it is **not** added to graph
`messages` or `messages_internal` (the underlying evaluator/reasoning data is
already there).  Long content is truncated by the emitting node to keep the chat
usable.

### `tool` (chat-renderable output)

One event per dispatch round; one entry per result.  `mime` is the type
vocabulary, sourced from typed MCP content blocks (`ImageContent` /
`AudioContent` / `EmbeddedResource` / `ResourceLink` carry `mimeType`) or from
structured conventions (`diff` -> `text/x-diff`, `code` -> `text/x-<language>`,
self-describing `display` dict).  The web renders by MIME; `display` is a text
fallback for clients that cannot.  `text/x-diff` / `text/x-patch` are de-facto,
not IANA.  Plain `TextContent` is not the primary payload (the JSON dump is).

Every result now yields at least a bounded generic entry, so no tool result is
silently invisible: a `text/x-shell` call line (`tool(arg=...)`, each argument
value summarised) plus a bounded plain-text projection of the output, cut with
an explicit `[truncated]` marker well below the tools' own output budget.
Typed MIME/media results keep their typed block and show the call line only,
so base64 is never dumped into the chat as text.  Errored results are included
too and carry `is_error`, which the web uses to style the block: a failed call
is evidence the user can read in the chat instead of only in `inspect`.  See
ADR-0040 for the full rules.

Note: a tool result is also recorded in graph state as a StepOutput (result,
tool, signature) and rendered into observations by
KleaAgentState.observations_text(), which delegates to
evidence.render_step_observations (agent_pkg/klea_agent/evidence.py:146).
Selected steps render in full; there is no character budget (the model's context
window is the bound), so no payload is replaced by a summary. Every tool result
is shown inline in the chat, so the synthesised answer summarises it rather than
reprinting it.

### Content blocks

A `tool` entry may carry a structured content block, so a consumer renders the
result from data instead of re-parsing free text.  The block is a union
discriminated by `type`:

* `text` - `{type: "text", text, role?}`: one run of text.
* `items` - `{type: "items", items, role?}`: an ordered list of `Item`s.
* `fields` - `{type: "fields", fields, role?}`: an ordered list of
  `{label, value}` pairs.

An `Item` is either a plain string or an object whose `role` is a plain string
(never a nested object or a serialised enum), and an item with no `role` of its
own inherits the enclosing block's `role`.  Keeping `role` a plain string means
a reader that does not know the vocabulary still renders the payload.

The role vocabulary is:

| Role | Meaning |
|------|---------|
| `ok` | the step or item succeeded |
| `error` | the step or item failed |
| `current` | the item in progress, or the most recently reached |
| `pending` | not started yet |
| `plain` | no status meaning; the default when `role` is absent or unknown |

A frontend renders each role as the marker from its shared glyph table -- `ok`
is `[\u2714]`, `error` `[\u2718]`, `current` `[\u203a]`, `pending` `[\u25cb]`
and `plain` `[\u00b7]` in the Unicode table; the ASCII fallback brackets its own
markers (`[x]`, `[!]`, `[*]`, `[ ]`, `[-]`).  Every marker is bracketed, so it
is distinguished from the item's own text.  An unknown or non-string `role`
renders the `plain` marker instead of failing, and consecutive items in one
section are separated, with no separator before the first item or after the
last.

Every block also carries `content_version`; see the next subsection.

### Contract versions

`content_version` is the **contract version** of the content-block payload, not
a counter of runs, events or items: it identifies the shape the emitter
produced, and it changes only when that shape changes incompatibly, so one
value always means one payload shape.

Readers compare it with the versions they know.  An unknown version is not an
error: the reader skips the structured block and falls back to the entry's
`display` text, so an older frontend keeps rendering a newer payload as plain
text instead of guessing at fields it does not understand.

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
