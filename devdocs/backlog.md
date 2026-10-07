# Klea backlog

Consolidated open-work backlog, so deferred items are not lost across dated
session logs (`.agents/`).  Add items here when a session defers something;
remove them when implemented (git log records the work).

Last updated: 2026-10-07.

## Cancellation / concurrency

- Per-thread queueing: today a second same-thread query is rejected with
  HTTP 409 while a run is active (ADR-0043).  A per-thread FIFO that runs
  queued turns after the current one is a possible future UX (opencode
  queues; ChatGPT/Gemini stop-and-disable).  Deferred: adds ordering,
  backpressure, and cancel-interaction complexity.
- Server-side tool cancellation: cancelling a run stops the node but the
  MCP server-side tool may keep running (per-call timeout is the backstop).
  True cancellation needs the FastMCP task API / `Client.cancel(request_id)`
  (MCP `notifications/cancelled`), which requires capturing the JSON-RPC
  request id; fold this into the planned FastMCP v4 upgrade
  (PrefectHQ/fastmcp#1305).  See ADR-0043.
- Multi-worker deployments: the active-run registry is in-process, so
  cancellation and single-flight only work with one uvicorn worker.  A
  multi-worker setup would need sticky routing or a distributed cancel
  signal.  Not needed at current scale.
- Parallel chats in one page: the server allows concurrent runs for
  different threads (`active_runs` is keyed per `thread_id`), but the
  NiceGUI frontend serialises one page to a single stream.  Stream state
  (`is_streaming`, `stream_task`, `streaming_chat_id`, `turn_retry_cb`)
  is page-scoped on `PageContext`, so while chat A streams the send/Stop
  button and input gating apply page-wide and chat B cannot start; Stop
  targets whichever run last started, and a finishing run clears the
  shared flags.  Rendering is already per-chat (each `ChatData` carries its
  own `status`, transcript, inspector, usage), so the remaining work is
  frontend coordination: move the task/streaming/retry state into
  `ChatData` (or derive "is this chat streaming" from `status.kind ==
  "progress"`), gate the input and toggle the button per current chat,
  cancel the current chat's task, add a chat-list running indicator, and
  guard background-task renders so a hidden chat's updates do not repaint
  the visible one.  Independent sessions require separate browser
  windows/profiles (or incognito): `app.storage.user` is keyed by the
  NiceGUI session cookie, which is shared across tabs of one profile, so
  tabs share a `user_id` (and its chats) rather than getting their own.
  This item is only about concurrent runs within one page; no server
  change needed.

## Agent general path

- ADR-0041 residual: reasoning steps remain serial (one `ReasoningNode`
  conclusion per round); batching a reasoning prefix into one call (a per-step
  conclusion map) is deferred.
- ADR-0041 measurement: the `parallel_writes` E2E scenario observes a
  two-independent-write batch; a dedicated evaluation-harness case still needs
  the `eval_pkg` harness (below).
- Phase-2 deterministic tool-substitution backstop; planner
  catalogue-membership validation.
- `add_artefact` tool; promote `_slug` / artefact-id derivation to a shared
  helper or `ArtefactSchema`.
- Session-as-invocation / "what next" ADR; cached project explorer.
- Revisit the `max_automated_plan_revisions` default (4 automated replans).
- Absence handling as a class: bound search escalation when a requested path
  is missing (an agent may reach for `find /`).  `read_file`/`edit_file` now
  report nearby entries on a missing target, and the picker escalates identical
  failed calls, but `run_command` is still not boundary-checked like the file
  tools, so consider a guard; gather a concrete case before coding.

## Tools

- Retrieval tool: no `search_stores`-style tool wires the RAG vector stores
  into the agent yet (retrieval is optional/deferred per the control-flow
  note).
- ADR-0039 deferred items: graph-level read/staleness gate; `apply_patch` /
  multi-edit; per-model edit-format auto-selection; post-edit LSP
  diagnostics/auto-format.
- Offload sync file tools to `asyncio.to_thread` so the tool-call wall-clock
  backstop can fire (one slow/huge edit can otherwise stall the server).
  Precedent: `klea_utils/mcp/tool_impls/ssrf.py:88`.
- Prompt tidy-up: `Planner_system.md` still says "Do not invent tools or
  arbitrary shell commands" (the picker prompt was clarified separately).

## Scientific mode / correctness / evaluation

- Scientific-mode correctness layer (ADR-0029): grounding, evidence,
  provenance and independent verification are not enforced; `assurance` is
  always `unverified` (ADR-0030/0036).
- Independent inline-answer verifier for `chat`-route answers (ADR-0029
  phase).
- `eval_pkg` harness (see `devdocs/system/agent-evaluation-harness.md`).

## Infrastructure / correctness

- Token-usage tracking / benchmarking: the `usage` stream event and DEBUG log
  carry per-node counts, but no consumer aggregates them per node (CLI drops
  `usage`; web UI sums one total; graph state keeps a run-level total).
  Decide the durable mechanism (consume stream events in a benchmark utility
  vs persist a per-run per-node summary) and whether to record usage per LLM
  call so retries/truncations count (today only the final call per node is
  measured).  `TokenUsage` now carries `reasoning_tokens`/`cached_tokens`/
  `role`; a stable node key is still needed.

## Configuration / UX

- TUI/CLI mode and access selectors (the web UI has them; TUI/CLI do not).
- Optional `KLEA_AGENT_ACCESS_LEVEL` env default (deferred; JSON config
  default used instead).
- Third-party trust roadmap (consent loop, sandbox-by-default, curated server
  registry), deferred per ADR-0037.
- Model/provider picker residual: the catalog-backed picker, the backend
  `/models/catalogue` endpoint and the free-text "custom" fallback are
  implemented.  Remaining: mark local/on-device providers (Ollama, LM Studio,
  ...) via an offline probe in the provider list.
- Credentials follow-ups (ADR-0042): per-request/session-only keys that are
  never persisted server-side (web/TUI parity); OS keyring support (desktop
  only, absent on headless servers); `{env:VAR}` references in place of stored
  secrets; and encryption at rest with a key from the deployment's auth
  provider (Keycloak) once deployments authenticate.
- Model variants (design/ADR first): `ParsedModelName.variant` is unused.  The
  de-facto standard is opencode's `provider/model#variant`, a named request
  overlay deep-merged into provider/model settings.  opencode sources variant
  names from models.dev `reasoning_options` (Klea's catalog already carries
  this field; the client just does not read it yet), plus hardcoded heuristics
  and config `variants` overrides, and maps each variant to provider-specific
  request kwargs.  Klea would need: `reasoning_options` access in
  `klea_utils/models_catalog.py`; provider -> LangChain kwargs mapping (e.g.
  ChatOpenAI `reasoning_effort`/`reasoning`, ChatAnthropic
  `thinking`/`reasoning_effort`); a config `variants` section; `#variant`
  parsing in `parse_model_name` (`provider:model#variant[:suffix]`); picker
  support (dropdown from `reasoning_options`, free-text fallback); and an ADR.
- HuggingFace model discovery: the picker's model list is models.dev's
  `huggingface` provider (78 curated, mostly large models); small/on-device
  models (SmolLM2, Qwen2.5-0.5B, ...) are absent and must be typed as free
  text.  Consider a curated small-model suggestion list (offline) and/or an HF
  Hub-backed list
  (`GET https://huggingface.co/api/models?filter=text-generation&sort=downloads`)
  behind a cached server endpoint with an offline fallback.
- Verify the HuggingFace local (`:local`) backend end to end once a working
  local `torch` is available; the maintainer's env has a broken CUDA build
  (`undefined symbol: ncclCommResume`).

## Streaming / tool UX

- Durable / resumable SSE stream (architecture).  The graph run currently
  lives inside the HTTP `StreamingResponse` generator
  (`chat_core.stream_response`), so a genuine disconnect (network drop, server
  restart, closed tab) loses the stream; the 15 s `ping` heartbeat only
  prevents idle-timeout/proxy drops during long-running nodes, and retrying
  mid-run can hit the 409 single-flight guard.  Robust long-running tasks need
  the run decoupled from the connection: start the graph as a background task
  keyed by thread, append events to an id-stamped buffer, and make the SSE
  response a subscriber carrying `id:` (`Last-Event-ID`) so a reconnect replays
  from `since` and then continues live.  Also enables attaching to an
  in-progress run from another tab and connection-independent cancel.
  Deferred: sizable change (event buffer + subscription + replay + reconnect
  logic in both frontends).  Buffer/pub-sub backend options: in-process
  (single-worker only; cf. the multi-worker item above), Valkey (the
  open-source Redis fork -- not Redis itself), or a log/broker such as Kafka
  or RabbitMQ.  The heartbeat landed as the interim mitigation.
- Exact live per-tool status (to think about - UX, optional).  A coarse
  `running` status is now emitted at the round start and replaced by
  `ok`/`error` at the end (status pane), so a same-resource call that is
  serialised (ADR-0041) or a gated/rejected call reads `running` until the
  round finishes.  Finer per-call start/finish would need optional callbacks
  in `dispatch_tool_calls` (`klea_utils/mcp/dispatch.py`) and emission from
  `gather` child tasks (contextvar / `get_stream_writer` risk; `asyncio.Queue`
  + node-side drainer fallback).  Not a committed item - add only if the
  coarse signal proves insufficient.
- Non-destructive tool calls in the chat (to think about - UX, needs user
  feedback/iteration).  Today read-only context tools (`read_file`,
  `list_files`, `grep`, `find_files`, `web_fetch`) appear only in the status
  and inspect panes; the chat carries artifacts (diffs, command output,
  images) and destructive results only.  opencode shows context tools as a
  collapsed group because its transcript is its activity log; Klea has
  dedicated panes, so this may just be redundant noise.  If pursued, prefer a
  single collapsed summary row ("read 4 files, searched 3 patterns") over one
  row per call, in the status pane or as one chat group.  Not a committed
  item.

## Testing

- Fault-injection hook to exercise the resume path end to end: LangGraph has
  no external interrupt/pause API (only `interrupt()` inside a node, or
  LangSmith), so a mid-run node failure is hard to trigger by hand.  Add a
  deterministic test hook (e.g. a debug/fault-injection env var that makes a
  chosen node raise once) so `resume=true` can be tested: the web Retry action,
  checkpoint continuation from the failed node (not the entry node), and
  single-turn persistence (one user row, one assistant row).  (A run *is*
  stopped externally by cancelling its `asyncio.Task` -- see ADR-0043 -- but
  that is cancellation, not the failed-node resume this hook targets.)
- E2E review task must drive the HITL interrupt (ADR-0046): the `review` task
  in `agent_pkg/e2e/tasks.py` now pauses at `AwaitHuman`, so the harness must
  answer (approve/revise) or cancel and assert the resumed run.
- Web UI (NiceGUI) tests: tier 1 (in-process user simulation + fake backend,
  `*/tests/ui/web/`; see `devdocs/system/web-ui-testing.md`) is implemented --
  page render, chat empty-state, hydration, mode/access controls, send flow,
  resumable-error Retry, and the HITL form.  Remaining tier-1 coverage:
  model-dialog rebuild on credential/model change, send<->Stop toggle while
  streaming, and inspector/status-pane refresh detail.  Tier 2 (API/
  orchestrator scenario scripts with multi-turn + HITL and metrics) belongs to
  the `eval_pkg` harness; tier 3 (browser-level `nicegui.testing.Screen` /
  Selenium) is deferred.  The interactive `agent_pkg/e2e/` suite stays a CLI
  smoke runner.
- `rag_pkg/klea_rag/nodes/generate_retrieval_query.py`:
  `_get_default_error_result` returns an all-default `RetrievalQueryOutput()`
  (empty `search_query`); decide whether that should degrade to a clear
  failure/known sentinel.
