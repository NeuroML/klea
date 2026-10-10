# Klea backlog

Consolidated open-work backlog, so deferred items are not lost across dated
session logs (`.agents/`).  Add items here when a session defers something;
remove them when implemented (git log records the work).

Last updated: 2026-10-10.

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
- Independent UI sessions across tabs: `app.storage.user` is keyed by the
  NiceGUI session cookie, which is shared across tabs of one browser profile,
  so tabs share a `user_id` (and its chats) rather than getting their own.
  Independent sessions need separate windows/profiles (or incognito).  (Parallel
  chats *within* one page are implemented; see `system/web-ui-testing.md`.)

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
- `web_search` configuration: the provider pool uses a fixed default order
  (`tavily`, `exa`, `parallel`, `firecrawl`, then any available keyed
  `brave`/`serper`) and provider keys are read from the process environment.
  A `general.web_search` JSON config (provider order/enable) is not
  implemented because the tool runs in the bundled stdio subprocess, which
  does not receive the app config; it would need `_bundled_server_config()`
  to forward settings (and keys) into the subprocess `env`.  Also deferred:
  an explicit `rank` field and truncating results to `max_results` (Parallel
  ignores the count).  See ADR-0048.
- ADR-0039 deferred items: graph-level read/staleness gate; `apply_patch` /
  multi-edit; per-model edit-format auto-selection; post-edit LSP
  diagnostics/auto-format.
- Offload sync file tools to `asyncio.to_thread` so the tool-call wall-clock
  backstop can fire (one slow/huge edit can otherwise stall the server).
  Precedent: `klea_utils/mcp/tool_impls/ssrf.py:88`.
- Prompt tidy-up: `Planner_system.md` still says "Do not invent tools or
  arbitrary shell commands" (the picker prompt was clarified separately).
- `read_file` byte-offset region reads (Phase 2): Phase 1 (streamed text read,
  done) still scans from byte
  0 to count newlines up to a line `offset`, so a late page costs O(offset) I/O
  even though memory is bounded.  Phase 2 adds true O(page) region reads:
  ripgrep already emits match byte offsets (currently ignored by
  `_parse_match_line`), so grep could return them (the in-house fallback can
  compute them), and `read_file` gains a byte-offset/region mode that `seek()`s.
  Touches both grep backends, the read contract and tests.  Deferred until
  large-file reads prove painful.
- `run_command` whole-tree kill: the process-group kill (SIGTERM then SIGKILL)
  now drains the group, but a descendant that detaches (``setsid``/``setpgid``,
  e.g. `mock`/`systemd-nspawn`) escapes and survives.  Reaching it needs a
  platform-native mechanism (Linux cgroup v2 / `PR_SET_CHILD_SUBREAPER`,
  Windows Job Objects) or a cross-platform best-effort recursive walk (adds a
  `psutil` dependency and is racy).  Deferred; see ADR-0038.

## Scientific mode / correctness / evaluation

- Scientific-mode correctness layer (ADR-0029): grounding, evidence,
  provenance and independent verification are not enforced; `assurance` is
  always `unverified` (ADR-0030/0036).
- Independent inline-answer verifier for `chat`-route answers (ADR-0029
  phase).
- `eval_pkg` harness (see `devdocs/system/agent-evaluation-harness.md`).

## Infrastructure / correctness

- Path-containment limits (`check_path_access`, mcp-permissions): containment
  is path-based and taken as a snapshot.  A hard link inside the root to an
  inode whose other names are outside it is allowed (undetectable path-wise);
  a path swapped for an external symlink between the check and the open
  (TOCTOU) could still be opened; and a file that grows between the `max_bytes`
  stat and the read can exceed that cap.  Hardening would need a per-open check
  (`openat`/`O_NOFOLLOW`) and a bounded read; documented as known limits in
  `permission.py` and `devdocs/system/file-tools.md`.
- Path-permission follow-ups after interactive approval (ADR-0007 update
  2026-10-08): the client-side gate is now the single enforcement point and it
  is advisory.  (a) Standalone `klea-mcp` / `nml-mcp` used by non-Klea clients
  have no path gate; there is no MCP standard that enforces path access
  (`roots`/`elicitation` are server-cooperative), so standalone containment
  would need a server-side middleware reusing the detector.  (b) "allow for
  session" is thread-scoped in `BaseGraphSchema.allowed_dirs`; a
  cross-thread/global allowlist is not implemented.  (c) Visibility filtering
  that hides denied tools from the prompt (opencode `always` model) is still
  deferred.
- Sensitive-file prompting (ADR-0007 update 2026-10-08) is implemented as the
  client-side second round (`klea_utils/mcp/sensitive.py`,
  `BaseGraphSchema.allowed_files`).  Considered but not implemented: redacting
  secret-looking content from tool *results* before they reach the model
  (reuse `plogging.mask_sensitive` patterns).  Two concerns: it means
  inspecting every tool output, and a user who approved a sensitive file has
  arguably accepted that the model sees it.  Low priority unless a concrete
  exfiltration case appears.
- Token-usage tracking / benchmarking: the `usage` stream event and DEBUG log
  carry per-node counts, but no consumer aggregates them per node (CLI drops
  `usage`; web UI sums one total; graph state keeps a run-level total).
  Decide the durable mechanism (consume stream events in a benchmark utility
  vs persist a per-run per-node summary) and whether to record usage per LLM
  call so retries/truncations count (today only the final call per node is
  measured).  `TokenUsage` now carries `reasoning_tokens`/`cached_tokens`/
  `role`; a stable node key is still needed.
- Sphinx build: importing `mcp` / `fastmcp` fails only inside the Sphinx
  process (not reproduced in isolation; 2026-10-02 session).  Unresolved;
  revisit if the MCP autodoc paths must build.
- Session deletion cleanup: `DELETE /chat/{user_id}/{chat_id}` removes the
  store row but **not** the chat's LangGraph checkpoint (only
  `DELETE /chat/{user_id}` purges checkpoints via `adelete_thread`).  Fix the
  per-chat delete to purge its checkpoint, and -- once the session workspace
  lands (ADR-0047) -- clear `{user_data_dir}/sessions/{user_id}/{chat_id}/`.

## Configuration / UX

- Mode and access selectors in the terminal frontends (the web UI has them;
  the terminal REPL and the planned Textual TUI do not -- see Frontends / TUI).
- Optional `KLEA_AGENT_ACCESS_LEVEL` env default (deferred; JSON config
  default used instead).
- Third-party trust roadmap (consent loop, sandbox-by-default, curated server
  registry), deferred per ADR-0037.
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
- Verify the HuggingFace local (`:local`) backend end to end once a working
  local `torch` is available; the maintainer's env has a broken CUDA build
  (`undefined symbol: ncclCommResume`).

## Sessions / working directory

- Per-session project root (needs an ADR).  Today there is no project-root
  concept: the file-tool permission boundary and the bundled MCP subprocess
  both inherit the API server's process cwd, so a session can only target the
  folder `klea web` / `klea-serve` was started in.  Parallel sessions should be
  able to target different project folders.
  - Surface (code map): `check_path_access` / `check_tool_arguments_permissions`
    are already parameterised by `project_root`, but production never supplies
    it -- the bundled tool wrappers omit it and `ToolsCallerNode` /
    `dispatch_tool_calls` pass `None`; the bundled MCP subprocess is spawned
    once per server with no cwd; and two boundary layers (the client-side
    pre-dispatch gate in the API process and the author-side gate in the MCP
    process) must both receive the root or they disagree.
  - Constraint: the MCP client/subprocess is process-level, so a per-session
    root must be conveyed per tool call (or via a per-session server).  It
    touches tool schemas / `checkpaths`, the picker/caller, dispatch, tool
    impls, discovery (`AGENTS.md`), and cwd-relative config/env and
    vector-store paths.
  - Central ADR question: what a "session root" means -- a per-chat
    user-selected folder (with an operator-configured base/allowlist for
    safety) versus one global base directory with per-chat subfolders.
  - Security: `check_path_access` is the only non-OS confinement for Klea file
    tools; `run_command` is advisory, not a sandbox.  A per-session root must
    let the operator bound what a session may choose, not let the model name an
    arbitrary root.

## Frontends / TUI

- Terminal client status: the `cli` subcommand runs a lightweight
  `input()`-based REPL (`klea_utils/ui/tui/repl.py`), a temporary tool for
  quick testing with simple queries.  It is **not** the TUI -- the historical
  "Textual" wording in ADR-0013/0026 and the `-tui` process identity are stale
  -- and it will be dropped once the TUI lands.
- Full Textual TUI: build the real TUI as a new **module** in
  `klea_utils/ui/tui`, following the web-UI pattern -- shared components (like
  `klea_utils/ui/web/nicegui/components`), each app composing and customising
  its own screen (`klea_agent`, `klea_rag`).  Planned: session sidebar
  (list/switch/create), streaming transcript, inspect/status panes,
  mode/access/model selectors (see the Configuration/UX item), tool results,
  and permission + HITL forms.  Replaces the REPL.
- Cross-frontend sessions: any frontend should list existing sessions and load
  one.  Sessions already live server-side in
  `{graph.paths.user_data_dir}/sessions.db` keyed by `(user_id, chat_id)` and
  are reached over HTTP, so this is **not** solved by a shared client data
  folder -- the client folders hold only logs and the web's browser -> `user_id`
  map.  The missing piece is a shared, persistent `user_id` (the web generates
  a random per-browser UUID in `app.storage.user`; the REPL sends `""`).  Plan:
  a stable identity (config/env such as `KLEA_USER_ID`, with a `--user-id`
  override) shared by the frontends, plus a `klea sessions` subcommand to
  list/delete sessions (the user can then load a chosen one in the TUI).
  Caveat: unifying the frontend folders is not the fix -- `app_name` is both
  the data dir *and* the log file name, so the server, web and TUI cannot share
  one `app_name` without colliding on `{app_name}.log`.  Not needed now; settle
  the design (likely an ADR) before starting.
- Session-command framework (ADR-0047; catalogue in
  `system/session-commands.md`): a `/`-command surface shared by all frontends
  (web first; the temporary REPL is not touched).  Commands are user-invoked
  tool calls -- UI-local ones run in the frontend, graph ones in a command node
  (mirroring the tool picker/caller) right after `Initializing`; graph-state
  changes are checkpointed (no `sessions.db` divergence).  A universal
  catalogue is served via `GET /commands`; each frontend filters by capability.
  Phases: P1 framework (shared core, `GET /commands`, web routing + `/`-menu,
  `/help`, documented stubs) and P2 (graph command node + `/mode`,`/access`,
  unified with the selectors) are **done**; P3 and P4 remain (below).
  `/cwd` (project root) reuses the framework once the per-session-root ADR
  lands.  `/runlocal` is intentionally omitted.

- Session workspace + attachment commands (`/file`, `/upload`, `/webfetch`;
  P3 of ADR-0047): **deferred -- decide the use cases before implementing.**
  Do not build these until we agree what they are *for*; they otherwise
  duplicate existing tools (`/file` vs `read_file`, `/webfetch` vs
  `web_fetch`/`download_file`).  Strongest candidate: the ChatGPT-style
  "attach a document" flow (the user attaches a PDF -- local, uploaded, or
  fetched -- and the agent analyses it), which also fills a real gap: the
  agent cannot parse a PDF today (`read_file` is binary-blind; Docling lives
  in the batch `StoresBuilder`, not a single-file helper).  Design options:
  (A) Docling-convert the file to Markdown in the session workspace and let
  the agent read it on demand via `read_file`/`grep` -- works now, no prompt
  bloat, token-friendly; (B) chunk/embed into a store and retrieve -- needs
  the ADR-0029 retrieval phase (not built; the agent has no retrieval node);
  (C) put the text in the always-rendered `Discovery` context -- simplest but
  bloats every Planner/answer prompt, which is why the "save tokens"
  rationale argues *against* it.  Open questions: pick an option; keep the
  original file alongside the converted text; add a small `Discovery` pointer
  so the agent knows the attachment exists; `/upload` needs the frontend
  `file_picker` capability plus an upload widget; the `[ingest]` extra
  (Docling) becomes a dependency.  Coupled fix: per-chat `DELETE` must purge
  the LangGraph checkpoint and clear the workspace (see Session deletion
  cleanup).

- Remaining session commands (P4 of ADR-0047): `/run`, `/compact`, `/plan`,
  `/init`, `/export`, `/skills`.  `/run` (server-side `run_command`, no chat
  LLM round-trip) is the cleanest token-free command; `/compact` uses the
  summarise node to shorten memory; `/plan` enters the existing HITL plan
  review; `/init` writes project guidance to `AGENTS.md`; `/skills` is the
  custom prompt-template surface.  Client-only commands (`/new`, `/sessions`,
  `/rename`, `/model`, `/theme`, `/commands`) are small, frontend-owned wins.

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
- Token streaming has no consumer: nodes marked `stream_tokens = True`
  (e.g. `AnswerGeneral`) emit token deltas, but neither the web nor the TUI
  renders them ("exists for a future live-typing UI").  Wiring a live-typing
  render is deferred (`system/streams.md`).
- Inline media rendering: `image/*` / `audio/*` results fall back to the
  `display` text path; a `display` tool plus media rendering (and
  `image/svg+xml` sanitisation) is deferred (ADR-0040, `system/streams.md`).
- TUI `tool` parity: `tool` stream entries render in the web status/inspect
  panes only; the TUI ignores them (`system/streams.md`).

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
