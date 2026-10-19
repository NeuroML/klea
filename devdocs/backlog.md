# Klea backlog

Consolidated open-work backlog, so deferred items are not lost across dated
session logs (`.agents/`).  Add items here when a session defers something;
remove them when implemented (git log records the work).

Last updated: 2026-10-16.

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
- Full session export (needs review): a `full` export of the whole
  session/graph state (the transcript plus `artefacts`, plan/observations,
  `discovery`, mode/access and usage), rather than the transcript-only `/export`
  (ADR-0047).  It is server-side -- the state lives in the LangGraph checkpoint
  and only the `context_snapshot` projection is exposed today -- so it needs a
  new endpoint and a serialisation design.  Research whether LangChain/LangGraph
  checkpoint/state serialisation (or replay) can produce it directly.
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

- Checkpoint store growth: `checkpoints.db` grows with every super-step (each
  checkpoint stores the full state, so a long agent session reached ~480 MB
  for ~880 checkpoints).  Investigate compaction / retention: does LangGraph
  offer a more efficient mechanism (e.g. `DeltaChannel` delta storage, a
  per-thread checkpoint retention/pruning policy, or `checkpoint_during`),
  and is the full per-super-step history needed for resume?  A pragmatic
  fallback is a retention policy (keep the last N checkpoints per thread) plus
  SQLite `VACUUM`; confirm it does not break resume (ADR-0043) or HITL
  (`interrupt`/`Command(resume=...)`, ADR-0046).  Measure first (per-checkpoint
  size, super-step count) before choosing.

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

## Configuration / UX

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

- Per-session project root follow-ups (ADR-0053):
  - **Operator allowlist** (needs auth): without authentication the root is the
    user's free choice (they run the server).  An authenticated/multi-tenant
    deployment should bound what a session may choose before `/cwd` is exposed
    to untrusted users.
  - **MCP roots interop**: Klea sends absolute paths and gates client-side;
    servers that rigidly enforce MCP roots or prefix all paths with a root are
    incompatible (ADR-0053).  Advertise roots only if such servers become a
    target.
  - **cwd-dependent / remote servers**: a tool that resolves relative paths
    against its own cwd for non-argument work, or a remote server that does not
    share the client's filesystem, is out of scope; only OS sandboxing is a hard
    boundary (advisory gate, ADR-0007).

## Frontends / TUI

- Status-pane plan persistence: the streamed plan section is cleared at each
  turn start (`state_sections = {}` -- web `stream.py`, TUI `app.py`), so it
  disappears until the Planner re-emits it, and both panes auto-scroll to the
  bottom (`scroll_end`), hiding the plan (the first section).  Keep it visible
  "until changed": (a) clear all sections *except* the shared `plan` key at
  turn start (or do not reset at all, letting nodes overwrite by key), and/or
  (b) render the plan as a pinned element at the top of the status pane,
  outside the scrolling state body.  The review ask no longer depends on the
  status pane for the plan: it carries the synthesized progress digest and all
  pending steps (ADR-0051 update), so this is now a general status-pane UX gap
  rather than a HITL one.
- Follow-theme styling: the TUI uses Textual theme variables throughout (panel
  backgrounds `$surface`/`$panel`, muted text `$text-muted`, the errored
  tool-header `$error`).  Two gaps remain, both deferred.  (a) Some Textual
  themes (for example `solarized-dark`/`solarized-light`) set
  `$surface == $panel`, so the chat/status panel split is invisible; this is an
  upstream theme-design choice, not something Klea can fix without overriding
  the theme's own tokens.  (b) Content rendered through Rich (`Markdown`, and
  `Syntax` for diffs/code) carries Rich's own palette (for example the pygments
  syntax style) rather than the Textual theme, so code blocks do not follow a
  theme change; making them follow needs Textual-native rendering (or a
  re-render on theme change).
- Client-side command output (`system` bubbles) is not persisted (the server
  never sees client commands), so it is lost on reload.  Decide whether to
  persist it.
- `chat_events` retention/pruning: the per-chat UI-event store grows with
  every run; add a retention/size policy (cf. the checkpoint-growth item).
- Terminal client status: the `cli` subcommand still runs a lightweight
  `input()`-based REPL (`klea_utils/ui/tui/repl.py`), a temporary tool for
  quick testing with simple queries.  It is **not** the TUI; the historical
  "Textual" wording in ADR-0013/0026 and the `-tui` process identity are stale.
  Drop the REPL (and retire its `-tui` identity) once TUI parity is confirmed.
- Cross-frontend sessions / shared identity follow-ups (ADR-0052): (a) a
  remote/multi-user deployment still uses the per-browser NiceGUI session
  cookie, so chats can "disappear" on a new window/browser/profile; a stable
  per-user id requires authentication (the user's username/id) instead of a
  random, browser-bound cookie.  (b) a `klea sessions` subcommand to
  list/delete sessions is a companion nicety.  Caveat: unifying the frontend
  data folders is still not the fix -- `app_name` is both the data dir and the
  log file name, so the server, web and TUI cannot share one `app_name` without
  colliding on `{app_name}.log`.

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
  (Docling) becomes a dependency.  Coupled fix: per-chat `DELETE` must clear
  the session workspace (the checkpoint purge is done).

- Full-fledged `/init` (deferred).  The current `/init` hands off to the normal
  agent loop with a canned prompt: non-deterministic, uses the full tool loop,
  and needs full access to write.  A dedicated init node -- bounded exploration
  (fixed reads + one chat call), a path-gated write, and refuse/merge on an
  existing `AGENTS.md` -- can replace it later if the cheap version proves
  insufficient.
- User-configurable conversation-memory settings: the summarisation threshold
  and recent verbatim window are now operator config (`general.memory` in the
  agent config, ADR-0018).  A per-session/user command to tune them (and a
  per-model default) is deferred -- it needs a durable setting plus the
  prompt-assembly window kept aligned.  The RAG's summarise node still
  hardcodes the window; expose the same config for `klea_rag` when needed.

- `/run` (P4 of ADR-0047): **deferred -- design under consideration.**  The
  command runs a shell command server-side as a user-invoked action (the
  model does not choose it), but the open question is whether (and how) its
  output should enter the model's context; provenance (a user-run command must
  not look like the model's own tool call) is the crux.  Full notes and the
  chosen "B'" shape: `system/run-command.md`.

- `/skills` (ADR-0047): **deferred** -- skills are not designed yet, so the
  command surface is undefined.  Design skills support first.

## Streaming / tool UX

- Token streaming is disabled pending an upstream fix (langchain#30429: the
  graph runs under `astream_events`, which forces chat models to stream, and
  LangChain's streamed `usage_metadata` merge then inflates input/total tokens
  for providers that report usage per chunk).  Klea disables streaming on the
  model instance in `LLMModel.build_config` (`disable_streaming=True`;
  HuggingFace uses its `streaming=False` field, which does not leak into the
  inference params) so usage stays accurate; `token` events are therefore not
  emitted.  (A per-call `stream=False` kwarg does not work: the
  `with_structured_output` `RunnableMap` drops call kwargs.)  Re-enable
  streaming (and the `token` events) when the upstream bug is fixed and a
  live-typing UI lands.

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
  or RabbitMQ.  The heartbeat landed as the interim mitigation.  Other
  terminal agents also decouple the run from the connection and stream events
  over a shared feed (e.g. opencode v2: one global encoded feed with
  per-connection bounded queues); this is context only, not a design to
  adopt.  Klea's buffer/replay contract should be designed independently --
  in particular a global feed does not fit its multi-tenant, unauthenticated
  `user_id` model.
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
- E2E review task must drive the HITL interrupt (ADR-0046/ADR-0051): the
  `review` task in `agent_pkg/e2e/tasks.py` now pauses at a `review` step
  (`AwaitHuman`), so the harness must answer (approve/revise) or cancel and
  assert the resumed run -- including a mid-plan checkpoint ("review after the
  README, before CONTRIBUTING") that exercises the review step's position.
  Also cover the review workflow added for ADR-0051: the `Progress` digest
  (`ReviewProgress`; skipped for a whole-plan approval) and the Evaluator's
  review-readiness replan (`review_readiness=not_ready`).
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

## Frontends / TUI (deferred commands)

- `/inspect` command (deferred): a command that switches the chat panel to the
  inspector view (the transcript/inspector toggle).  It must be added to both
  frontends -- the TUI and the web UI -- as neither exposes it as a command
  today.  (The TUI's :kbd:`Ctrl+I` / :kbd:`Tab` toggle now works: a terminal
  sends Ctrl+I as Tab, which `Screen`'s default `focus_next` binding had been
  swallowing, so the toggle is bound at the screen level instead.)
