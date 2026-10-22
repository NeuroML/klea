# Changelog

> **Note:** `klea_agent` (`klea` / `klea-serve`) and `neuroml_mcp` (`nml-mcp`) are WIP and unreleased  ---  no PyPI releases. Tagged releases below are `klea_utils` and `klea_rag` only (`klea_utils-v*`, `klea_rag-v*`).

## Unreleased  ---  `klea_utils` / `klea_rag`

### Breaking changes

- The lightweight `cli` subcommand (the `input()`-based REPL) is removed. The Textual TUI is the terminal client: bare `klea` / `klea-rag` launches it, and `klea tui` / `klea-rag tui` remains the explicit form. The agent and RAG packages also expose a `tui` extra. The opt-in `agent_pkg/e2e` harness is retained for its scenarios and checks, but still targets the removed CLI and must be retargeted to the API/server before use.

### Added

- Terminal UI: all free-standing text (transcript, status/inspector panes, dialogs, HITL prompts and app chrome) can be selected with the mouse and copied with Ctrl+C; copying uses OSC 52, so terminals without support may need their native selection.
- `read_file` can read from the end with a negative `offset` (tail): `-1` is the last line and `-N` the Nth from the end. Tail pages keep signed line numbers and `next_offset`, reach EOF without a full scan, and report `total_lines` when the backward scan reaches the start; converted documents use exact totals.
- Agent cross-run continuity: `RouteDecision` recognises explicit "implement/continue" requests, and a bounded continuation evidence record plus the new `ResetState` task boundary let the Planner re-plan from prior work across graph runs; new tasks still start from a fresh goal and plan. See ADR-0054.
- Agent task planning: the Planner is shown the last 9 abandoned plan versions with the reason each failed, and the guidance HITL ask displays them so the user can see what was tried. The Planner is told not to repeat an abandoned approach; there is no deterministic semantic repeat detector.
- Agent step execution: an Evaluator `step_incomplete` reason is passed to the tool binder and reasoning node as `step_feedback`, so a re-run can address the specific gap instead of repeating the same call or conclusion.
- User-facing answer nodes report the agent identity (`Klea`) at the top of their system prompt and the effective model that produced the reply, so questions like "which agent/model are you running" are answerable: `AnswerFromContext` (RAG), the shared `AnswerGeneral`, and the agent's `RouteDecision`/`AnswerFromResults`.
- Terminal UI: `klea tui` / `klea-rag tui` launch a full-screen Textual client over the same backend as the web UI. It renders a MIME-aware transcript with an inspector view, lists / switches / renames / deletes sessions, edits per-role models and provider credentials, presents HITL review / input / permission modals, and blocks a send until the required models and API keys are set (ADR-0050). Requires the new optional `tui` extra.
- TUI: the client `/new` command starts a fresh chat (refused while a run is streaming), matching the web frontend.
- Session lists show a truncated preview of each chat's first query, from the `GET /chat/{user_id}` response: in the TUI session picker, the highlighted chat's preview is shown below the Rename/Delete/Cancel buttons, and the web chat-list tooltip gets an extra line.
- Bundled read-only `search_papers` tool: academic paper search by keywords, returning title, authors, year, journal, abstract, DOI, link and a `peer_reviewed` flag. Peer reviewed sources come first: OpenAlex and Crossref (plus Semantic Scholar when `SEMANTIC_SCHOLAR_API_KEY` is set) are rotated per call with fallback on rate limits, errors or results without abstracts, and `domain: "life-sciences"` searches Europe PMC first, then PubMed (NCBI E-utilities) as its fallback. Preprints (arXiv, and Europe PMC preprints such as bioRxiv) are only searched with `preprints: true` and are listed after the peer reviewed results.
- Cross-encoder reranking: fused retrieval results are re-scored by a local cross-encoder (default `cross-encoder/ms-marco-MiniLM-L-6-v2`, configurable via `general.cross_encoder_model`) before the recency blend; the model is loaded once at startup and its weights are downloaded and cached by Hugging Face on first use.
- Custom model endpoints (`custom:<model>:<url>`) accept a full endpoint URL and select the wire API from its path (`/chat/completions`, `/responses`, or `/v1/messages`); a bare base URL still defaults to OpenAI Chat Completions.
- New optional `anthropic` extra (`langchain-anthropic`) for the native Anthropic provider and custom `/messages` endpoints.
- Tool access levels (`read_only` | `full`) derived from standard MCP annotations, enforced both when tools are disclosed to the model and before dispatch; deployments can override a tool's capability per tool via `general.tool_access` (ADR-0037).
- Shared base graph state `BaseGraphSchema` (`klea_utils.graph.state`) holding the fields common to all application graph states.
- General `run_command` bundled tool: shell command execution (full-mode only, destructive) so environment and coding tasks are answerable via the agent's task path; the timeout ceiling is configurable via `KLEA_RUN_COMMAND_MAX_TIMEOUT` (ADR-0038).
- Klea-authored MCP tools refuse to run when the server process is root (uid 0) unless `KLEA_ALLOW_ROOT_TOOLS` is set, with a startup warning; this covers the bundled and NeuroML servers (ADR-0038).
- Per-call wall-clock backstop for tool calls (default 900 s, `KLEA_TOOL_CALL_TIMEOUT`; `0` disables) so a hung tool returns a non-halting error instead of stalling the graph.
- Bundled read-only `grep` (regex content search) and `find_files` (path glob) tools; they use a pinned ripgrep binary when the new optional `search` extra (`astral-dev-toolchain-ripgrep`) is installed, otherwise an in-house walker. Both skip VCS/cache directories and symlinks, and the ripgrep backend honours `.gitignore` in a git repository unless the call passes `include_ignored: true`.
- Bundled `write_file` (create/overwrite a whole file) and `edit_file` (exact search/replace with `replace_all`) tools, full-mode only and destructive; writes are atomic and preserve mode, line endings and BOM. `edit_file` falls back to a bounded replacer chain (line-trimmed, block-anchor, whitespace-normalised, indentation-flexible, context-aware) before refusing, and reports the matcher used (ADR-0039).
- Bundled `web_search` tool: web discovery over a keyless-first provider pool (Tavily, Exa, Parallel and Firecrawl hosted MCP endpoints, no key required) that falls back across providers on a rate limit or failure, with optional keyed providers (Brave, Serper) used when their API key is set; results are normalised to title/url/snippet/published/score (ADR-0048).
- `read_file` gains a `line_numbers` option (default true); set it false to get raw text for an `edit_file` `old_string`.
- Rich stream contract (ADR-0040): `inspect` replaces the `info`/`debug` events (one event, summary shown and `details` collapsible); a `tool` event carries chat-renderable tool output (file-edit diffs, and MCP image/audio/resource blocks), rendered by MIME type with a text fallback; `NodeStreamData` gains `key` (shared status-pane sections) and `preformatted` (monospace `<pre>`); `token` streaming is now opt-in per node.
- MCP tools accept an explicit `null` for a non-nullable optional argument (for example `max_chars: null`), treating it as "use the default" instead of failing validation.
- LLM invoke retries (context overflow, truncation, empty, empty structured, transient timeout) are surfaced to the user: each retry emits a progress line (`<label> (retry n/m: reason)`) and an inspect entry with the structured details, instead of being visible only in the logs.
- Model strings for providers in the models.dev catalog (`<provider>:<model>`, e.g. `openrouter:...`) resolve their endpoint from the catalog, so no URL is needed; OpenAI-compatible providers use the OpenAI surface and `OPENAI_API_KEY`, Anthropic-style providers use the Anthropic surface. The wire surface is resolved per model, so gateways that serve different models on different APIs (e.g. OpenCode Go) select Chat Completions, Responses, or Messages correctly.
- Three-layer model resolution: graph/env defaults, per-session default overrides (`/chat/{user_id}/models/...`), then per-chat overrides; saving a model in a chat updates the session default ("last used") so new chats inherit it.
- Provider-scoped API credentials (per user, keyed by provider plus the endpoint for custom/explicit-URL models) with a `/credentials/{user_id}` API; keys are never returned raw (masked suffix and a `user`/`env`/`none` source only).
- Stored credentials expire after a configurable TTL (`KLEA_CREDENTIAL_TTL_DAYS`, default 7 days; `0` disables) once unused, swept at startup and on access.
- Web UI: the model selector works before any chat, a first-run "Choose models" prompt appears when setup is incomplete, and send is disabled until the required models and API keys are configured.
- Web UI: the model dialog picks each role's model with provider, model and optional custom-URL dropdowns filled from the models.dev catalogue, while still accepting free text for uncatalogued models (for example `ollama:` or a self-hosted endpoint).
- Web UI: for a `huggingface` role the model dialog offers a **Run** choice (hosted inference providers, or local) and an **inference provider** field (`auto`/`cheapest`/`fastest`/`preferred`, or a specific provider), with a "pick one or type in" hint on the editable fields.
- Web UI: `klea web` and `klea-rag web` accept `--favicon` (a file path, `http(s)`/`data:` URL, raw SVG or a single character) for the browser-tab icon, which now defaults to the bundled Klea icon instead of NiceGUI's.
- Resumable runs: `/query` and `/query/stream` accept `resume=true` (no query) to continue a failed run from its checkpoint, re-running only the failed node; the web UI shows a Retry action on a resumable error (ADR-0043).
- Cancel a running query: `POST /query/cancel` stops the active run for a chat (the web UI's send button becomes a Stop button while streaming), leaving the thread clean for the next query; only one run may be active per chat, so a concurrent same-chat request is rejected with `409` (ADR-0043).
- Session commands support first-class subcommands (verbs): a command can declare verbs (for example `/sessions switch`), the framework resolves the verb and its arguments (rejecting an unknown verb with a usage message), `/help <command>` lists the verbs, and the `/`-menu autocompletes them.
- Human-in-the-loop pauses that resume the *same* run with its state intact (ADR-0046). The agent's plan review pauses for an explicit approve/revision decision (approval runs the plan, a revision returns the feedback to the Planner), and a `needs_input` plan pauses with the questions its blocked step carries. New chat payload fields `interrupt_response` / `interrupt_cancel` (and `interrupt_id`) are distinct from `resume`, and the backend rejects a plain query while a chat awaits an answer (`409`). The web UI and TUI render the ask and submit the answer, and reloading the page re-presents a pending ask.
- Plan review is now a plan step (`kind="review"`, ADR-0051): the Planner can place an approval before any work or a progress checkpoint after an earlier step (for example "review after step 3"), making review positional and repeatable. An approval resolves the review step and continues without a Planner round-trip; a revision returns the feedback to the Planner. The `in_review` plan status is removed, and a `review` step that follows completed work shows the progress so far.
- Web UI: several chats in one page can now run at once. Each chat has its own run state (send / Stop / Retry apply per chat), the chat list shows a running / awaiting-input indicator, and deleting a chat cancels its run; a background chat's stream no longer blocks the input or repaints the active chat.
- Interactive path approval: before a tool call touches a path outside the project directory, or a sensitive file (env files, private keys, cloud config) inside it, the agent pauses and asks the user, per path, to allow once, allow for the session, or deny. A denied path returns a non-halting error and the run continues; session approvals are remembered for the chat. The ask is shown in the web UI and TUI, recorded in the inspect pane, and the sensitive-file set is extendable via `KLEA_SENSITIVE_PATTERNS` (ADR-0007).
- Session commands (ADR-0047): an in-session `/`-command surface shared by the web frontend and the graph. The backend publishes its server-side catalogue at `GET /commands` and the graph runs a command node; the web UI merges that catalogue with its own client commands, offers a `/`-autocomplete menu, and renders command output as a system block in the active chat (creating one if the command is the session's first input). `/help` (client) lists the catalogue, `/new` (client) starts a new session, `/rename` (client) renames the current session, `/model` (client) opens the model and API-key configuration, `/export` (client) downloads the session transcript as Markdown, `/mode` (server) shows or sets the operating mode, and `/access` (server) shows or sets the tool access level. Graph commands are ephemeral (their state change is checkpointed, no chat rows); a deployment can disable commands via `general.commands.disabled`.
- Session commands (continued): `/sessions` (client) lists the sessions (pinned first), switches the active one, and deletes one after confirmation; `/compact` (server) summarises the conversation memory on demand; `/init` (server) generates project guidance (`AGENTS.md`) for the current project.
- A graph command handler may be `async` and may hand off to another graph node (via `GraphCommandResult.route` and the transient `command_route` state field), so a command can run further nodes before the run ends (for example `/compact` -> the compaction node; `/init` -> the normal planner/tool path).
- The summarise-memory node reports its outcome to the inspect pane (summarised / nothing new / no usable summary) instead of writing the chat, and can optionally set a chat confirmation for a manual compaction.
- The TUI and web UI share a host-local identity file (`PlatformDirs(<server-app>)/user_id`, used only for a loopback server) instead of relying on the browser session cookie, so a session opened in either frontend appears in both and survives new windows/browsers; both frontends accept `--user-id` (ADR-0052).
- Per-session project root (ADR-0053): `/cwd [path]` shows or sets the directory the file tools operate in, which is also the default path-permission boundary; relative tool arguments are resolved against it (the tools run in a subprocess whose working directory is fixed), and project discovery (`AGENTS.md`) is read from it. The root is shown in the web status pane and the TUI status panel.
- Web UI: `klea web` / `klea-rag web` open in the default browser once ready (disable with `--no-open`) and accept `--resume <chat_id>` / `--continue` to open a specific or the most recently updated session on load, matching the TUI.
- `/exit` session command in the web UI and TUI: it stops any active run and then exits (the TUI equivalent of `Ctrl+Q`; the web frontend also stops the server it launched and closes the tab on a best-effort basis). The web header also gains an exit button, which confirms before exiting.
- The tool path-permission ask now shows the tool call (`tool: argument = value (confidence)`) and, for a heuristically guessed value, offers a **Not a path** dismissal that lets the call proceed without approving it (ADR-0007 update 2026-10-14).
- Web UI and TUI: reloading a chat now restores the full session, not only the user/assistant transcript -- the inspection and status panes and the chat's tool/diff blocks are persisted and replayed on load, and the session's cumulative token totals are restored from the checkpoint (ADR-0032 update).
- Terminal UI: the query input is now a multi-line, soft-wrapping text area that grows with its content (then scrolls), with the cursor on the bottom line so the box grows upward; `Enter` sends and `Ctrl+J` (or `Shift+Enter` on supporting terminals) inserts a newline, so pasted multi-line text can be edited before sending.
- Terminal UI: the status panel lists the chat's selected models (with a warning when a required model or API key is missing), matching the web status pane.
- Terminal UI: the status panel header shows the active chat's name and id.
- Terminal UI: a long transcript message is collapsed to a few lines and expands/collapses on click or `Enter`, matching the web UI.
- Klea's processes set their OS process title to their role (`klea-agent-tui` / `klea-rag-tui`, `klea-agent-web` / `klea-rag-web`, `klea-agent-serve` / `klea-rag-serve`) instead of `python`, so they are identifiable in `ps`/`top`; an auto-spawned server gets it via the `KLEA_PROC_TITLE` environment variable.

### Changed

- Terminal UI: Ctrl+C now copies selected text instead of stopping the run; Ctrl+Shift+C stops a run, with F12 as the fallback for terminals that intercept Ctrl+Shift+C.
- Token usage totals now show cached prompt reads as a subset of input (`<n> cached`) in the web and TUI status lines; cache-creation writes are not counted as cached.
- Path approval asks now show the full target path and the session approval directory separately, with bounded previews for pathological values, plus the tool-call context, detected token and discovery source. Full values remain the decision keys; guessed values keep session approval and the "Not a path" dismissal.
- `grep` now returns grouped per-file hunks with two lines of context by default (`context_lines`, 0-7) instead of bare matching lines, so callers usually have the surrounding code without a follow-up `read_file`; overlapping windows are merged and context counts toward the character budget.
- LLM output budgets: the first-attempt max output tokens for `chat`, `plan` and `tool_picker` are now 32768 (up from 16384), and the truncation-retry ceiling is 65536, so long or reasoning-heavy completions usually finish on the first call instead of paying a retry round-trip. HuggingFace keeps a 16384 default because its total-budget accounting penalises large reservations (ADR-0017).
- Web UI: the user's own messages are rendered verbatim (no Markdown decoration), so literal underscores, `#`, lists, line breaks and pasted code are preserved; bare URLs stay clickable. Agent and system messages remain Markdown.
- Entry routing: the `chat` route is now reserved for self-contained conversational/general-knowledge replies (including direct conversational edits such as shortening or rephrasing); a request that derives a result from earlier work or turns (summarise, compile, compare, synthesise) is routed to the `task` path so it is planned/checked and its deliverable persisted, instead of being answered inline by the router.
- The agent client identities are now `klea-agent-tui` / `klea-agent-web` (previously `klea-tui` / `klea-web`), matching the `klea-{agent,rag}-{tui,web}` scheme the RAG clients already use; the log file and NiceGUI user-data directory names change accordingly, and existing agent web storage under `klea-web` is not migrated.
- `read_file` returns one bounded page by default and enforces a server-side character budget; the `max_chars` parameter is no longer exposed to the model, so a large file is read by paging (via the result's `next_offset`/`next_char_offset`) instead of in a single call.
- `read_file` streams plain-text files lazily instead of loading the whole file: memory is bounded by the page size, files larger than `max_bytes` are pageable rather than refused (`max_bytes` is now a per-call scan cap), and `total_lines` is `null` when a page does not reach EOF.  A large-file page that is truncated suggests using `grep` to locate the region and then reading around it with `offset`/`limit`.
- The RAG embedding model is now fixed (not user-modifiable): vector stores are embedded once at startup, so a per-request/chat embedding override could not be honoured; the model dialog shows the role as locked.
- The tool caller node is skipped entirely when there is nothing to dispatch (no tool calls, or no MCP client): it no longer emits progress/inspection events or writes an empty tool-results update for a skipped round, consistent with the other nodes.
- Destructive tool results are now surfaced in the chat: `run_command` renders one shell block with the command, its full stdout/stderr and exit status (shown when there is output or a non-zero exit; the UI collapses long blocks), and `download_file` shows the saved path or failure. A destructive third-party MCP tool that declares no display convention gets a minimal `tool(arg=...)` shell block, so a risky call is never silently invisible.
- Web UI: tool result blocks collapse to a one-line preview (the block title plus the first line) and expand on demand; an errored tool block's title is shown in the theme's negative colour.
- Web UI: the status pane lists the tools in a round as they run, marking each `running` and replacing it with `ok`/`error` when the round ends.
- Web UI: the model dialog can copy one role's model configuration to all roles.
- RAG retrieval runs each retriever call and the cross-encoder inference in worker threads, so a slow store or model no longer blocks the server event loop for other sessions.
- A streaming run records the user turn when it starts (not only on completion), so a turn that fails mid-run is visible and retryable; the assistant reply is recorded on completion.
- A custom `/v1/messages` endpoint uses the `anthropic` provider and copies `OPENAI_API_KEY` to `anthropic_api_key`, with an explicit per-chat `api_key` override taking precedence.
- `anthropic_api_key` and `huggingfacehub_api_token` are masked in log output.
- Tool dispatch takes the per-tool `ToolInfo` (path metadata plus the read-only/destructive capability) instead of a raw metadata map.
- RAG graphs run with `read_only` access: destructive tools are never offered or dispatched.
- The project is now released under the MIT License (previously GPL-3.0-or-later); license text, package metadata, and docs were updated.
- `run_command` treats a non-zero exit as a normal command result (reported via `returncode`), not an MCP error; `isError` is reserved for a timeout, a denied working directory, or a spawn failure, so commands such as `diff`/`grep` no longer trigger retry/replan loops.
- Web UI: the chat transcript uses full-width blocks (user tinted, agent plain, tool neutral), file-edit diffs render as their own blocks, code lines wrap instead of scrolling horizontally, the input is resizable, and the disclaimer and credits share one footer line.
- Structured-output failures (`ValidationError` / JSON parse, or an endpoint refusing `response_format`) now fall back to the plain, tolerantly-parsed invoke instead of aborting the run; endpoints seen refusing the parameter are cached per `(provider, model, base_url)` for the process lifetime, so the structured attempt is not repeated on every node call.
- Output-token budgets now default to 16384 (guard stays at 2048) and the frozen per-node `max_output_tokens` caps are removed, so reasoning models no longer truncate even tiny structured calls; the budget is still clamped per model to its catalog output limit and context headroom, and remains tunable via the `providers` config.
- Tool parameter lists shown to the model now label every argument `required` or `optional` explicitly and show non-null schema defaults (e.g. `pattern (string, optional, default "*")`), so the tool picker does not have to infer optionality from an absent flag or narrow a listing by setting a filter that defaults to "all".
- Tool dispatch groups a batch's calls by resource (a tool's `checkpaths` arguments, compared lexically) and runs calls that share a resource sequentially in call order, dispatching the rest concurrently, so a missed dependency edge costs parallelism rather than a lost update.
- Bundled `list_files` returns the unfiltered listing (with a `note`) when a `pattern`/`include_*` filter matches nothing but the directory is not empty, so an empty filtered result is not mistaken for an empty directory.
- Bundled `read_file`/`edit_file` report `nearby` entries (the nearest existing directory's contents) and a `note` when the target is missing, so the caller sees what actually exists instead of only an error.
- Model overrides store only the model string; provider API keys are managed through the provider-scoped credential store and injected per run.
- A bare `huggingface:<model>` now resolves to the hosted Inference Providers API (`auto`) instead of the local pipeline backend, which downloaded the model as a side effect; `:local` opts into running locally, and `:<provider>` / `:cheapest|:fastest|:preferred` select the inference provider or routing policy.
- `GET .../models/overrides` masks any legacy inline `api_key`; a startup migration moves legacy inline keys into the credential store and strips them from overrides.
- Web UI: the inspect pane updates live as the graph runs and keeps a collapsible section per query (timestamp plus the query text) for the browser session, instead of showing entries only when a query completes.
- Web UI: the inspect pane renders streamed `details` as readable JSON - multi-line string values (prompts) are expanded and JSON-encoded values inlined - with syntax highlighting and a copy button, instead of escaping every value into one long line.
- Graph diagrams (`*-lang-graph.png` and their `.mmd` source) are written only when debug logging is enabled (`--debug` / `KLEA_LOG_LEVEL=debug`), instead of on every graph build.
- Path permission is now a single client-side gate before dispatch instead of a per-tool check: file tools no longer enforce the project-directory boundary themselves, and a tool that declares no path metadata is still gated (declared `checkpaths`, paths the tools picker declares, and heuristics such as shell-command tokens).
- Terminal UI: the status panel no longer shows a `context` section (the web UI does not either); the graph context still drives the mode / access / project-root rows.
- Terminal UI: the transcript/inspector toggle now reads **Chat** / **Inspect** and is centred; the status panel's mode and access rows are combined into one line (for example `General | Full access`, with full access in the error colour); the status pane lists one model per role, spaces its groups, and no longer draws streamed state sections on a separate background.
- Terminal UI: `Ctrl+Q` now asks to confirm before quitting; the `/exit` command quits immediately (typing it is deliberate), matching the web UI.
- The TUI and web status panes list the chat's models as a bulleted per-role list (`- Chat: gpt-4o`), so the two frontends match.
- The TUI and web status panes match further: the models list is a collapsible group (collapsed by default) showing each role's provider with its model beneath; everything down to the token totals stays fixed while only the state sections scroll; and the token totals are unmuted and thousands-separated.
- The web status pane's project-root row is styled like the operating-mode and tool-access rows (same bold label, muted value).
- Terminal UI: user messages render as a tinted block, every transcript block has vertical padding, and the query input uses the same tint (one row of padding, growing with the text).
- The plan-review / approval dialog now shows only the **pending** steps the approval unblocks (the whole plan for a whole-plan approval, or the remaining steps for a mid-plan checkpoint); completed work and the step findings stay in the transcript and the inspection pane. A `review` step must gate pending work -- the Planner rejects a trailing review, and the router resolves one defensively instead of pausing on a gate that gates nothing.

### Fixed

- Tool path detection now splits shell command strings with a platform-aware tokeniser and recognises Windows backslash paths, so `path_detect` and `parse_command` no longer miss or mangle arguments such as `C:\Users\me\file.txt`.
- TUI text selection now highlights Rich Markdown/Syntax output, supports drags ending on blank lines, and copies to the OS clipboard through `wl-copy`/`xclip`/`xsel`/`pbcopy`/`clip` when OSC 52 is unavailable; a brief notification confirms the copy.
- `read_file` no longer implies that `limit=None` reads to the end of the file: `None` removes only the line limit, while the server character budget still caps the response. Results now report the effective `char_budget` and a `truncation_reason` (`char_budget`, `line_limit` or `scan_cap`), and a character-capped `limit=None` note explains how to continue.
- The generic LLM-node validation retry loop now performs exactly `max_validation_retries` re-invokes (previously one extra) and leaves the final validation error in `ctx.validation_feedback` for `_update_state`, instead of clearing it, so a node can fail closed rather than accept an invalid result.
- A path-permission decision of "not a path" now renders as `Not a path: <key>` in the transcript and is counted as dismissed in the audit summary, instead of being shown as `Denied`.
- `DELETE /chat/{user_id}/{chat_id}` now cancels the chat's active run and purges its LangGraph checkpoint as well as the chat row, so re-creating a chat does not inherit stale run state.
- Token usage is reported accurately again: model streaming is disabled, because the graph's event stream forces chat models to stream and LangChain's streamed `usage_metadata` merge sums input/total tokens across chunks for providers that report usage per chunk (VLLM, HuggingFace Inference Providers, ...), inflating the counts massively (langchain#30429).
- Path-permission confirmations render each decision on its own line, with the path in code formatting, instead of one `;`-joined line.
- The answer-composer (`AnswerFromResults`) now receives the session artefacts, so a task whose reply builds on earlier tasks' deliverables (for example compiling results gathered in prior turns) can use them; previously only the Planner saw them.
- Messages that contain `{...}` (for example a user query or an answer with JSON) no longer break a later run: dynamic text concatenated into a node's prompt template -- the conversation summary and configured domain text -- has its braces escaped, so it is not parsed as a template variable.
- Web UI: the chat-list running indicator now stops spinning (or switches to the awaiting-input icon) as soon as a run finishes, pauses, or errors, instead of staying on the spinner until an unrelated refresh.
- HITL answers in the chat transcript now render readably (for example `Allowed once: /tmp`) instead of raw JSON, and a path-permission answer no longer prints its wire mapping.
- Web UI: the chat-list running/awaiting indicator and the active-chat marker now show when the sidebar is collapsed to its icon rail (the avatar cell is the only item section Quasar keeps in mini mode).
- `read_file` refuses binary files and text that is not valid UTF-8 with a clear error (instead of returning replacement-character garbage) and strips a leading UTF-8 BOM.
- The file tools share one line definition: lines end at `\n` after normalising CRLF and lone-CR endings (matching editors and `wc -l`), so `read_file` and `grep` no longer count form feeds, vertical tabs or Unicode separators as line breaks.
- Path checks now deny an unresolvable target (for example a symlink loop) instead of letting the tool crash; the client-side gate reports it as a non-halting denial.
- `read_file` reads at most its configured `max_bytes` (instead of reading the whole file after the size check), bounding memory and closing the stat/read race for a file that grows mid-read.
- `read_file` paging is now accurate and resumable: a truncated read is a partial success (empty `error`) whose `line_end` is the last line actually returned, with a `next_offset` and a `note` telling the caller how to continue. A single line longer than the character cap is paged by characters via a new `char_offset`/`next_char_offset`. Previously the cap cut mid-line while `line_end` still reported the full slice end (so a truncated read looked complete and could not be resumed), a non-positive `limit` read the whole file, and an offset past the end produced an unexplained inverted range.
- A query whose tool runs for several minutes no longer fails with a connection error: the stream is kept alive with a heartbeat while a node is busy, and a genuinely lost connection is now reported as resumable (Retry).
- Web UI: an operating mode or tool access level chosen before the first message is carried onto the new chat and sent with it, instead of being reset to the defaults when the chat is created.
- RAG retrieval query generation no longer fails with `INVALID_PROMPT_INPUT`: a literal `{"gte": ..., "lte": ...}` range example in the prompt was being parsed as a template variable and is now escaped.
- RAG retrieval depth (`k`) is now tracked per conversation instead of on the shared retriever, so growing `k` in one chat no longer affects concurrent chats or the next query on the same thread.
- RAG evaluator score-based routing overrides now take effect: the effective route is recorded in state and read by the retrieval and answer nodes, so `k` grows only when retrieving more (not on a query rewrite) and best-effort answers carry their warning.
- RAG retrieval filters work again: the query generator builds its structured-output schema per run from the domain's configured filter fields, so strict structured-output modes no longer close the `filters` object to `{}`; scalar, list and numeric-range constraints are all supported.
- Tool picker calls bind their arguments again under strict structured output: a free-form `args` object was closed to `{}` by every provider's strict mode, so calls ran with empty parameters. The picker now builds a per-run, strict-safe schema from the disclosed tools (each tool's parameters as typed fields), so calls bind correctly.
- Transient empty LLM responses (common with HuggingFace) are retried up to twice, and a persistently empty answer now returns a clear "please retry" message instead of a blank reply.
- A transient LLM timeout (including a stream that stalls after its response starts) is retried with bounded backoff instead of aborting the run; timeouts are also matched by exception type, not only message text.
- Per-node token usage is now read from `response_metadata.token_usage` when `usage_metadata` is empty (gateways and OpenAI-compatible endpoints that report counts only in the raw payload), so token usage and reasoning-token counts are tracked and logged instead of being silently dropped.
- Structured output that cannot be parsed (a blank or unrecoverable model response) now degrades to the node's typed fail-closed default instead of raising a parser error that aborted the run.
- Web UI: theme-aware design tokens in the shared NiceGUI theme, so icon buttons, segmented mode/access controls, muted text, secondary greys, the panel/page background and the footer surface follow dark mode instead of Quasar's fixed palette.
- Web UI: a brand-new session can configure models before any chat exists, so the first query no longer fails with "No model configured".
- Web UI: opening the model/API-key dialog from a background task (for example the first-run prompt) no longer fails; dialogs are built in an explicit slot.
- Web UI: the chat setup prompt and status pane update as soon as models or API keys are configured (and after deleting the user session), instead of showing stale configuration.
- Web UI: the model dialog updates a role's API-key status live while a model is typed, lets "Manage API keys" configure a provider that is not saved yet, saves all changed roles at once, and keeps the dialog open when resetting a role.
- HuggingFace credentials are read from `HF_TOKEN` instead of the non-existent `HUGGINGFACE_API_KEY`, and a local HuggingFace backend is reported as needing no key.
- Web UI: long tooltips wrap instead of being clipped on the right.
- Terminal UI: library request/connection logging (`httpx`, `httpcore`) is quieted to warnings by default (`--debug` / `KLEA_LOG_LEVEL=debug` restores it), and the TUI now logs to its rotating file only while discarding the auto-spawned server's output, so log lines no longer overwrite the full-screen interface.
- Terminal UI: the status panel's top section (mode / access / project root) is sized to its content instead of taking half the pane, and the panel scrolls to the newest state update when its sections overflow.
- Terminal UI: an errored tool-result header uses the active theme's error colour instead of a fixed red.
- Terminal UI: the query input no longer draws a box; it uses the theme's raised surface (`$boost`) and sits a row clear of the key hints.
- Terminal UI: `Ctrl+I` (which a terminal sends as `Tab`) now toggles the transcript/inspector view from anywhere on the screen -- it no longer cycles focus when the query input is not focused. Previously the `Screen` focus binding swallowed it, so the documented shortcut did nothing.
- Terminal UI: the model dialog's per-role panels size to their content instead of ballooning (the per-role button row was a `1fr` container filling the panel), and the query input has a few-row minimum height.
- Terminal UI: deleting a session from the picker now asks for confirmation before it is removed, matching the web UI.
- Terminal UI: streamed `state`/`inspect` details containing `[` or `=` (for example a bracketed model repr) no longer crash the status/inspector panes with a Rich markup error; such text is now rendered literally.
- Terminal UI: the review/input (HITL) and path-permission dialogs now scroll, so their action buttons stay reachable when the plan/progress or the list of paths is long (previously the dialog was clipped at 80% height and the buttons fell off-screen).

### Dependencies

- `klea_utils[tui]` (`textual`) provides the terminal UI; it is included in the `full` extra.
- `setproctitle` (a new `klea_utils` dependency) sets a friendly process title for the CLIs.
- `klea_rag` depends on `klea_utils[rerank]` (`sentence-transformers`) for cross-encoder reranking.
- Python 3.14 is supported and tested in CI. Python 3.15 is not supported yet: `onnxruntime` (required by `chromadb`) has no 3.15 wheels.

## Unreleased  ---  `klea_agent` (WIP, unreleased)

### Added

- The agent's user-facing answer nodes (`RouteDecision` for inline chat, `AnswerFromResults` for task answers) report the effective model that produced the reply, so questions like "which model are you running" are answerable.
- The chat transcript shows concise progress notices for evaluator verdicts and reasoning conclusions via a new `notice` stream event/role. Notices are persisted as transcript rows but kept out of graph conversation memory.
- Agent terminal UI (`klea tui`): a full-screen Textual client with the operating-mode and tool-access selectors in the status panel (choosing one sends `/mode` or `/access`), per-role model and credential dialogs, and the HITL / permission modals (ADR-0030/0037/0050).
- Agent operating modes (Scientific / General) with `Mode{requested,resolved,note}`, mode router, CLI `--mode` and web selector/badge (ADR-0030).
- App-owned web mode UI (`ui/web/mode_ui.py`, `page.py`, `app.py`) and `GET /chat/{user}/{chat}/context` hydration on the agent side.
- General-path entry router (`RouteDecision`): a narrow, fail-closed `chat` vs `task` decision that answers trivial chat inline (2-call floor) and hands everything else to the `Planner`, which plans and fixes the goal but never answers the user (ADR-0035).
- Human plan-review step (`AwaitReview`; auto-approve stub, real pause/resume deferred) and an `in_review` plan state.
- Deterministic loop budgets and a terminal failure answer, so a stuck run stops and explains instead of looping.
- Per-request tool `access_level` (chat payload) plus `general.access_level` / `general.tool_access` config; the agent defaults to `full` (ADR-0037).
- Environment and coding requests (working directory, builds, scripts) are now answerable in full mode via the bundled `run_command` tool instead of ending `unplannable` (ADR-0038).
- Per-step observations record the tool that produced each result and whether the interface already displayed it, and the final reply avoids reprinting output the chat already showed.
- Reasoning steps: a plan step can be a `reasoning` step that produces a conclusion (analysis, decision, hypothesis, design) instead of a tool call, handled by a dedicated `ReasoningNode` and recorded alongside tool results.
- `needs_input` plan outcome: when a missing fact only the user can supply blocks planning, the agent asks for it instead of failing, carrying the question in `pending_question`.
- Session-scoped artefacts: a completed task's deliverable is persisted as a concise artefact and is available to later tasks in the same session.
- The agent loads the project instruction file (`AGENTS.md`, falling back to `CLAUDE.md`) into session-scoped discovery (re-read only when it changes) and renders it into the Planner and final-answer prompts, so project conventions inform both planning and the reply.
- Unified replan reason: automated replans (tool failure or `need_replan`) carry a concrete reason back to the Planner.
- Agent chat payload accepts `resume` to resume a failed run from its checkpoint on `/query` or `/query/stream` (no query sent); a resume request carrying a query is rejected.
- Conversation-memory tuning via `general.memory` (`summarisation_threshold_chars`, default 40000; `num_history_chars`, default 20000), so the agent keeps more conversation verbatim and summarises less often.

### Changed

- Session artefacts rendered into the answer-composer prompt are now bounded (the most recently written 20 entries and 12,000 characters, with a note when older entries are omitted), so a long session no longer grows that prompt without limit; the checkpoint still retains every artefact.
- Internal run history (plan versions, evaluator verdicts and reasoning conclusions) is now kept in `messages_internal` and summarised alongside the user-visible conversation; it is no longer injected into prompts via conversation memory. The verbatim recent-memory window is bounded (10k chars) instead of including the whole current run.
- A failed plan no longer disappears from the status pane: validation and Planner-decided `unplannable` failures preserve the last steps and change only the status. Replan-budget exhaustion now preserves the plan, appends a blocked guidance step, and pauses at the input HITL for human help instead of ending the run.
- The Planner no longer has a zero-step `no_plan_required` status, and its prompt no longer carries the artefacts block: a goal whose facts are already known is planned as a single `reasoning` step, whose conclusion reaches the answer-composer through observations. This removes the asymmetry where the Planner decided from conversation history the answer-composer could not see.
- The tool picker runs on a dedicated `tool_picker` model role instead of borrowing the chat/plan model; there is no silent fallback, so the role must be configured.
- Tool selection: the Planner selects the tool for each step, and the agent's `ToolCallBinder` binds arguments only for one contiguous group of steps sharing the same suggested-tool set (capped at 4). The binder sees only that group's tools and never substitutes; off-contract selections are retried twice, then escalate to the Planner with the reason. RAG's shared picker is unchanged.
- The plan revision budget counts automated replans only; human review resets it, so user iteration is not charged against the failure budget.
- Plan state is now `PlanSchema` extending the authored `PlannerPlanSchema`, with run-history counters (`plan_version`, `human_feedback_rounds`, `automated_plan_revisions`) carried on the plan; `human_feedback`/`replan_reason` remain documented transient node-to-node signals.
- Tool failures are summarized for the binder, Planner and Evaluator as one line per failed call (tool, path/URI arguments and error), replacing the last-error-only feedback; URI secrets are redacted, the digest is bounded, and free-text arguments are omitted.
- The tool picker escalates to a replan instead of re-dispatching a call that repeats a previously failed tool and arguments, even when it appears in a subset, reordered batch or alongside new calls, so a non-correctable error does not burn the retry budget on an identical retry.
- Per-step observations, retry counters and status use the plan step's 1-based number, and per-step state is cleared when the planner writes a plan, so replans no longer merge stale outputs.
- Plan execution follows the dependency DAG (ADR-0041): per-step `depends_on` defines a frontier, work is bound in same-kind frontier batches (cap 8 steps/16 calls), the binder tags each call with its originating step, the Evaluator returns a per-step verdict map, and same-resource calls are serialised at dispatch. Reasoning steps remain serial.
- `klea_agent` graph and nodes synced to `BaseLangGraph`/`BaseLLMNode` contracts (shared `ToolsPicker`/`ToolsCaller`, the `ToolCallBinder` subclass, lifecycle parity).
- The agent runs without a JSON config file: with no `--profile` (and no `KLEA_AGENT_APP_CONFIG_FILE`) it starts from built-in defaults plus the environment, instead of requiring `klea_agent.json`.
- The global tool-round cap and the fixed plan-size limit were removed: long-horizon plans may have as many focused steps as needed, while the per-step and replan budgets still bound execution. Tool-error re-picks per step increased from 2 to 3 (ADaPT).
- Planner steps are now defined as one action with one observable outcome; the prompt splits independent work and requires bounded reasoning conclusions. The Reasoning node starts with the 65536 output-token ceiling (provider-clamped), since hidden reasoning tokens previously truncated the 32768 first attempt before the conclusion.

### Fixed

- The Planner no longer accepts an invalid plan after the validation-retry budget is exhausted: it now fails closed as `unplannable`, with the validation error in the failure reason, instead of writing and executing the last invalid plan.
- Plan steps may name only tools disclosed at the run's access level: an unknown or `read_only`-filtered tool, or any tool step when no tools are available, is now rejected and retried, then fails closed as `unplannable` instead of reaching the picker.
- `/cwd` now reconciles session discovery against the new project root, so the previous root's `AGENTS.md`/`CLAUDE.md` no longer persists (a root with no instruction file clears it, and a root with a different instruction file replaces it).
- A conversational turn that the entry router classified as a task is now handed back to the router instead of being answered by the answer-composer (which has no conversation history): the Planner returns a new `chat` status, and the router is forced to reply as chat.
- The final delivery node never emits a blank reply: an empty `message_for_user` (e.g. a `chat` route whose inline answer was empty) falls back to a clear "please retry" message.
- Answer synthesis reports the correct success/failure outcome and no longer duplicates the failure-reason label.
- Superseded failed tool outputs are dropped from a step's observations once a call succeeds, so stale errors no longer confuse evaluation or the final answer.
- The evaluator is given the list of tools actually executed, and an empty retrieval renders a clear "no context" marker instead of a blank context section.
- Reasoning steps now carry their `rationale` into `observations` (`StepOutput.rationale`, rendered as `Rationale: ...`), so the Evaluator and Planner see the justification instead of judging a bare conclusion and looping on "missing justification".
- The final answer is given a plan-history signal (plan version and human-review round counts) and no longer claims a reviewed or revised plan was executed without the user's approval.

## v0.5.0 (2026-09-09)  ---  `klea_utils` / `klea_rag`

### Added

- Graph-level context events: `context_snapshot` projection, `extra_state` on runners, generic `GET /chat/{user}/{chat}/context` with checkpoint hydration and streamed `context` events (ADR-0032).
- Per-request model overrides via LangGraph Runtime `KleaRunContext` (`context_schema`) replacing hand-rolled contextvar (ADR-0033).
- App-owned API/UI composition: each app mounts its own chat/context/web pages; utils provides `chat_core`, `context` router and reusable NiceGUI components (ADR-0031).
- JSON-repair fallback for LLM structured-output parsing and import checker for optional dependencies.
- `localonly` live test for Anthropic prompt caching.

### Changed

- Chat streaming split into `chat_core` + `context` router; `stream_events`/`run_graph_stream` carry generic `context_fields`/`extra_state`.
- `mask_sensitive` now recursive so nested `model_overrides.*.api_key` is redacted in logs.
- API/server emit structured INFO logging for `/query`, `/query/stream`, lifespan and readiness.

### Fixed

- Anthropic `cache_control` now placed on structured system content blocks rather than `additional_kwargs` which `langchain-anthropic` drops.
- Docs API pages updated for new `chat_core` and `context`/`graph.context` modules.

## v0.4.0 (2026-09-02)  ---  `klea_utils` / `klea_rag`

### Breaking changes

- Person-name metadata fields (currently `authors`) are now expanded at
  store time with per-word and lowercase variants (e.g. an author stored
  as `"Ankur Sinha"` also matches filters for `"Sinha"`, `"sinha"`,
  `"Ankur"` or `"ankur sinha"`).  Existing stores predate this and are
  stale -- regenerate them (`klea-stores-create store --force`,
  re-running `chunk` first).
- The built-in bibliographic retrieval filter fields (`journal`,
  `authors`, `keywords`, `year_from`/`year_to`) are removed.  Filter
  fields are now declared per domain under `filter_fields`; deployments
  that relied on automatic bibliographic filtering must declare the
  fields they want (e.g. `journal`, `authors`, `year`) explicitly.
- Package `klea_code` renamed to `klea_agent`; CLIs `klea-code` ->
  `klea`, `klea-code-serve` -> `klea-serve`; log ids `klea-code-tui`/
  `klea-code-web` -> `klea-tui`/`klea-web`; env prefix `KLEA_CODE_` ->
  `KLEA_AGENT_`. Agent data moves from `~/.local/share/klea-code/` to
  `~/.local/share/klea/`.
- Store builder renamed: `VSBuilder`/`klea-vs-create` ->
  `StoresBuilder`/`klea-stores-create`; `build|store --bm25-store`
  writes the combined corpus for keyword retrieval.
- `setup_llm()` replaced by `create_configurable_model()`.
- Chunk text and headings are now normalized for retrieval; existing
  `.klea-cache` entries are stale -- re-run
  `klea-stores-create ... --force` once to re-chunk.
- Bibliographic metadata key `venue` renamed to `journal` (matches the
  retrieval filter schema); existing `metadata-map.template.json`,
  `metadata-map.json`, `.klea-cache` and `doi-cache.json` entries with
  `venue` are stale -- re-run `klea-stores-create chunk --force` once to
  regenerate.
- Ingestion now enforces a stored-metadata key policy: `_`-prefixed
  internal keys and the provenance keys `source_path`, `source_type`,
  `source_url` are never stored.  `build` is a one-shot quick start: it
  generates `metadata-map.template.json` from the extraction and consumes
  it without a review step.  `store` requires a metadata map (an explicit
  `--metadata-map` or the template generated by `klea-stores-create
  chunk` -- run `chunk` first), aborting when none exists, when the map is
  empty, or when a source file is missing from it.
- `neuroml_mcp` tool names dropped the `_tool` suffix (e.g.
  `get_models_from_neuromldb_tool` -> `get_models_from_neuromldb`); clients
  that call tools by hardcoded name must update them.
- The auto-reload flag was renamed to `--reload` so `--debug` could mean
  verbose logging across all CLIs: the NiceGUI web clients
  (`klea web`, `klea-rag web`) dropped `--debug`/`-d` in favour of
  `--reload`/`-r`, and the serve commands (`klea-serve`,
  `klea-rag-serve`) dropped `--dev` in favour of `--reload`.

### Added

- Read-only sqlite query tools in `klea_utils` (`sqlite_query`,
  `sqlite_schema`): single `SELECT` statements only (statement separators
  inside quoted strings or comments are ignored), database opened in
  read-only mode, a row-return limit and a configurable VM-instruction
  work budget (`max_ops`) to bound pathological queries, permission-gated
  database path, and helpful unknown-table/column errors.  Usable directly
  by agents and wrapped by deployments into MCP tools.
- Configurable per-domain retrieval filter fields (`filter_fields` in the
  domain config): the query generator only proposes the declared metadata
  fields, and each domain only receives the filter clauses on its own
  fields at retrieval (per-domain filter routing), so cross-domain
  queries never apply one domain's fields to another's stores.
- Automatic bibliographic metadata extraction (`klea_utils.biblio`;
  DOI services, PDF Info, Docling, regex tiers) pre-fills the `DEFAULT`
  entries of `metadata-map.template.json`; `KLEA_INGEST_MAILTO` opts
  into DOI polite pools; `--no-ocr` speeds up text-based PDFs.
- `klea-stores-create map-lint <dir>`: LLM-free health checks on the
  metadata map (missing fields, suspicious titles/DOIs, year/filename
  mismatches, stale `venue` keys, excess `url*` keys, placeholder
  counts), printed automatically at the end of `chunk`.
- `klea-stores-create pre-check <dir>`: classifies each PDF by whether it
  needs OCR (based on whether it carries an embedded text layer) instead
  of guessing by publication year; `--organise` copies files into
  `ocr/`/`no-ocr/` subdirectories (originals untouched) so each can be
  chunked with the right `--ocr`/`--no-ocr` flag and stored into the same
  collection.
- Ingestion warns when a file converts to zero chunks (typically a
  scanned PDF with OCR disabled) on both the `chunk` and `store` paths.
- Metadata-map resolution now matches the autogenerated heading-chain
  keys (most specific first), walking up to the nearest populated
  ancestor when a leaf section has no metadata, with full fall-through
  debug logging.
- `klea-stores-create store-lint <corpus.pkl>`: LLM-free post-store
  review of a BM25 corpus (summary, suspicious chunks, structural
  checks) plus `--samples` evenly-spaced windows of contiguous chunks;
  auto-printed at the end of `store`.
- `klea_agent` is now the main application (general-purpose agent with
  coding capabilities, WIP/unreleased); `klea_rag` is consumed by it.
- Configurable model system (per-node `model_defaults`, dynamic provider
  field filtering, HuggingFace auto-derivation); optional guard node
  (skipped when `guard_model` unset, `guard_decision` now defaults to
  `safe`).
- BM25 keyword retrieval fused with vector search via reciprocal rank
  fusion; per-domain `bm25_stores`; new deps `langchain-community`,
  `rank_bm25`.
- NiceGUI web UI (3-column layout, model config, inspector); SSE
  streaming endpoint (`/query/stream`) with per-node progress consumed
  by the CLI and Streamlit frontends.
- API and UI code consolidated into shared `klea_utils` routers.
- `max_refs_size` config option (default 20000 chars): the reference
  material sent to the answer LLM is now bounded by total characters
  instead of a fixed 10-document cap, so raising `k` surfaces more chunks
  up to the character budget.
- Shared MCP tool implementations in `klea_utils` (`web_fetch`,
  `list_files`, `download_file`) that any app can wrap into FastMCP
  tools, plus shared tool-registration helpers
  (`klea_utils.mcp.registry`) and an httpx session lifespan
  (`make_http_session_lifespan`); new `[mcp]` extra (`httpx[http2]`,
  `beautifulsoup4`). `klea_agent`'s bundled tools server and
  `neuroml_mcp` both use them.
- `web_fetch` hardened: status-aware retries, 5 MB download cap
  (separate from the character cap), safe decoding, SSRF guard,
  browser User-Agent rotation (daily-refreshed from an upstream list
  with an honest-client fallback) and a Cloudflare-challenge retry.
- `download_file` / `download_file_to_cache` shared implementations for
  downloading URLs to disk with transient-error retries.
- `download_files` shared helper: downloads a list of files (as returned
  by the repository source functions below) into a chosen destination
  directory with bounded concurrency and per-file results.
- Repository file/version listing in `klea_utils`
  (`github_list_files`/`github_list_versions`, `figshare_list_files`/
  `figshare_list_versions`, `dandi_list_files`/`dandi_list_versions`,
  `biomodels_list_files`/`biomodels_list_versions`): framework-agnostic
  functions that list the versions and files of archival repositories
  (GitHub, FigShare, DANDI Archive, BioModels) with direct download URLs,
  ready to be wrapped into MCP tools.
- `read_file` shared MCP tool implementation: converts documents
  (PDF/Office/EPUB/CSV via anydoc, HTML via BeautifulSoup) and plain text
  files to text with paged, line-numbered output (`offset`/`limit`,
  `line_start`/`line_end`/`total_lines`); document conversion results are
  cached per process.  Adds `firecrawl-anydoc` to the `[mcp]` extra.
- Path-permission layer for MCP tools: tools can declare which arguments
  are file paths (`checkpaths` in `ToolInfo`); `klea_agent` and `klea_rag`
  now check those paths against the working directory before dispatch and
  refuse out-of-boundary calls without invoking the tool.
- The agent's bundled tool server now also serves `read_file` (paged
  document and plain-text reading).
- At startup the server logs a warning listing any required model roles
  that have no default model set, along with the model env vars to set,
  instead of crashing; the web UI status pane marks unset roles "Not set".
- `GET /chat/{user}/{chat}/models/active` now reports whether each model
  role is `required`.
- Standalone `klea-mcp` CLI: runs the shared bundled tools server
  (web fetch, file list/read, download) over stdio or HTTP, reusing the
  same server that apps auto-launch in-process.
- Tag-based MCP tool filtering: a server entry can list
  `include_tags`/`exclude_tags`, and the shared bundled server is
  configured under `general.bundled_tools` (`enabled`, `include_tags`,
  `exclude_tags`) -- enabled for `klea_agent` by default (batteries
  included) and opt-in for `klea_rag` (RAGs are domain specific).  Tags
  carry a documented scope (`local`/`web`) and domain/functional
  vocabulary (`files`, `code`, `download`, `echo`, `neuroml`,
  `neuroml-db`, `osb`, `bundled`).
- Standard MCP tool annotations: `ToolInfo` gains `read_only` /
  `destructive` / `idempotent` / `open_world` flags that `register_tools`
  folds into the tool's `readOnlyHint` / `destructiveHint` /
  `idempotentHint` / `openWorldHint`, so any MCP host sees the
  read-only/destructive contract of Klea's tools without adopting Klea's
  tag vocabulary.  Read-only tools (search, file read/list, web fetch)
  and destructive tools (download, code/simulation execution) are
  annotated accordingly.

### Changed

- All Klea CLIs now default to `INFO` console logging (progress on
  stdout, warnings/errors on stderr) instead of debug noise.  Pass
  `--debug` or set the `KLEA_LOG_LEVEL` env var (a level name or
  number) on any CLI to see full debug logs; `--debug` wins over the
  env var.  The rotating log file still captures `DEBUG` regardless of
  the console level.
- `klea-stores-create map-lint <dir>` now verifies the map's top-level
  keys are the actual source filenames: a source file with no entry is
  fatal (the `store` step would fail), so the full report is printed
  first and the command then exits non-zero; keys that are not source
  files (heading-keyed or stale entries) are flagged, and old
  heading-keyed flat maps are reported as a single structural issue
  instead of per-heading missing-field noise.

- Default models are now set per role through environment variables
  (`KLEA_<APP>_<ROLE>_MODEL`, e.g. `KLEA_AGENT_CHAT_MODEL`); the env file
  is optional, falling back to shell env vars.  The env schema is generated
  from the graph's `llm_models` declaration, so roles and env vars cannot
  drift.  Agent role `reasoning_model` renamed to `plan_model`.

- JSON config files are now selected by profile: `--profile <name>`
  loads `<name>.json` from the current directory, then the per-app
  config directory, instead of the `*_APP_CONFIG_FILE` key in the env
  file (the env var is still honored when set in the environment).
  `--profile template` scaffolds a ready-to-fill config into the current
  directory.
- `neuroml_mcp` and `klea_agent` register their tools through the shared
  `klea_utils.mcp.registry` (`tool_meta` / `register_tools`) instead of
  package-local copies; tools are discovered by their `@tool_meta`
  decoration (function name is the tool name) rather than a `_tool` name
  suffix.
- `download_file` now sends an honest User-Agent and refuses internal and
  private hosts by default (shared SSRF guard, shared with `web_fetch`;
  `allow_internal_hosts` opts out).
- Reference material is serialized grouped by source file: shared
  bibliographic metadata (authors, year, journal, ...) is emitted once
  per file with chunks numbered beneath, each carrying its own inline
  relevance score; `serialize_vs_retrieval` renamed
  `serialize_reference_material`.
- Conversation memory is bounded by characters instead of message counts:
  recent history is injected as real message objects (a character-budgeted
  verbatim window between the system and human prompts), older history is
  compressed into a running context summary that never overlaps that
  window, and summarisation triggers on accumulated characters.
- HTTP stack consolidated on httpx: aiohttp removed everywhere; shared
  sessions use HTTP/2 (`http2=True`) with tuned connection limits; a
  single `_make_retryer_httpx` helper backs the bundled tools.
- Shared MCP tool implementations moved from `klea_utils.mcp.tools` to
  `klea_utils.mcp.tool_impls`, and the bundled tools server (wrappers +
  server) now lives in `klea_utils.mcp.server` (`bundled.py` +
  `bundled_tools.py`); `neuroml_mcp` tools were retagged with the
  documented scope/domain vocabulary.
- Retrieved references are now re-ranked with a small recency bias:
  `rrf_merge` stays pure relevance, then `rerank_by_recency` normalizes
  the RRF score to `[0, 1]` and blends it
  `0.9 * relevance + 0.1 * time`, where the time term is
  `(year - year_min) / (year_max - year_min)` across the retrieved set
  (missing year -> `0.5`).  Newer work outranks older work of equal
  relevance, reflecting that papers build on and correct earlier results.
- Relevance scores are no longer included in the reference material sent
  to the answer/evaluator LLM (the ranked order conveys priority); they
  remain visible in the UI/reference display and the per-source scores
  stay in document metadata for debugging.
- `klea-stores-create chunk` no longer accumulates every file's chunks
  in memory: each file's chunks are released once cached, so chunk runs
  stay bounded in memory regardless of corpus size.
- `klea-stores-create chunk` and `build` now convert uncached files in
  short-lived subprocess workers bounded by `--worker-mem-limit`
  (default 4 GiB, includes the models) and `--worker-batch-size`
  (default 200, whichever comes first); `build` now composes the two
  bounded paths (worker chunk + streaming store) instead of holding the
  whole corpus.
- `klea-stores-create store` streams cached chunks per file instead of
  holding the whole corpus in memory.  BM25 corpora are written inline
  in bounded per-batch pickles and read back with a looping unpickle;
  existing single-list corpora remain loadable.
- `DoiResolver` cache writes are now atomic and batched (every 25
  resolutions plus on close), so a crash can no longer tear the cache.

### Fixed

- A query with no model configured now returns a clear "No model configured
  for role 'x'" error instead of a misleading provider-level
  "Missing credentials" error.
- `run_graph_stream` now iterates the async generator correctly.
- `AnswerGeneral` / `GenerateRetrievalQuery` handle list-form content
  blocks from newer langchain-ollama.
- `ToolsCaller` no longer emits a stream event when pre-exec checks skip.
- `k` was increased twice per `retrieve_more_info` cycle (the router and
  the retrieval node both incremented it); it is now increased once per
  cycle, at retrieval.
- `download_file` no longer corrupts binary downloads by writing them
  decoded as text; the raw response body is saved as bytes.
- Empty `{}` heading placeholders in the metadata map no longer strip
  the `DEFAULT` metadata from matching chunks; per-heading entries are
  merged over `DEFAULT` (gap-fill), so a heading that sets only a `url`
  keeps the file's authors/year/journal.
- Orphaned `.klea-cache` entries (whose source file no longer exists)
  are now auto-pruned on every `chunk`/`store`/`build` run.
- `klea-stores-create store` is now cache-only: it no longer converts
  files on the fly (conversion settings `--max-tokens` and `--ocr`
  were removed; `chunk` owns them).  Run `chunk` (or `build`) first so
  every file is cached.
  Internally, cache loading + metadata-map folding were extracted from
  `chunk_all` into a shared `_fold_metadata_map` helper and a dedicated
  cache-only `_load_and_fold_results` path used by `store`.
- `store` is now incremental via a per-collection store manifest
  (`.klea-cache/<collection>.manifest.json`): unchanged files are
  skipped, changed files have their old chunk IDs deleted and are
  re-added in place, and files absent from the source are never pruned.
  Chunk IDs are deterministic (`<file_name>:<chunk_index>`).  `store
  --force` drops the whole collection and rebuilds it (portable across
  backends via a `drop_collection` dispatch helper), which fixes the
  previous `--force` duplicate-on-re-add behaviour.
- `metadata-map.template.json` is now generated into
  `<source_dir>/.klea-cache/` instead of the source directory; copy it
  out to review and pass the reviewed copy via `--metadata-map`.
- A corrupt or truncated `.klea-cache` entry (e.g. left by a crash or
   an interrupted run) no longer aborts the whole `chunk`/`build` run:
   it is moved aside as `<hash>.pkl.corrupt`, the source is re-converted
   on the fly, and the artifact is pruned once the entry is regenerated.
   Cache writes are now atomic, so an interrupted run can no longer
   leave a truncated entry behind.
- MCP tool execution failures now correctly report `isError: true` per the
   MCP spec (previously every failure returned `isError: false`).
- `list_files` now returns a boolean `truncated` flag for consistency with
   other tools (previously `"True"`/`"False"` strings).

---

## v0.3.0 (2026-07-02)

### Changed

- Uniform `label` attribute on all nodes (~25 classes) -- doubles as the
  LangGraph node name for UI progress display; `write_custom_stream()`
  helper added for streaming node events.

### Fixed

- Fast CLI `--help` via lazy imports; improved error handling and logging
  throughout.

---

## v0.2.0 (development, unreleased)

### Added

- Vector store backends (Chroma, PGVector, Qdrant) in a `stores`
  sub-package; document ingestion pipeline (`VSBuilder`, chunking,
  metadata) with the `klea-vs-create` CLI.
- MCP client integration (FastMCP) with per-domain vector store and MCP
  server configuration.
- Evaluator node with re-ranking and query-modification loop; structured
  output schemas for LLM nodes.
- Memory support (context summary, conversation history); session
  checkpointing via InMemorySaver.

---

## v0.1.0 (2026-06-30)

Initial release of `klea_utils` and `klea_rag` to PyPI:

- `BaseLangGraph` orchestrator with setup/run template methods
- RAG pipeline: guard, classify, retrieve, generate, evaluate
- Ollama and HuggingFace inference providers; Chroma, PGVector, Qdrant
  vector store backends
- CLI client and Streamlit web interface; health check endpoints
- Sphinx documentation at https://neuroklea.org
