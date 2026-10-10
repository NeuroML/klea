---
status: "accepted"
date: 2026-10-10
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Session command framework: a user-invoked command surface

## Context and Problem Statement

Users need an in-session `/command` surface: navigation (list/switch/new
sessions), session settings (mode, access, project root), and workflow
actions (compact/summarise, file/URL attachment, plan).  Claude Code and
opencode make this look trivial because their frontend and backend are a
single process on one host.  Klea is **split**: a frontend (NiceGUI web or
TUI) talks over HTTP to a graph/tools backend (ADR-0026), each app owns its
API and UI (ADR-0031), and the backend may further delegate tool calls to
**external MCP servers that run on other hosts**.

Two consequences drive this decision:

1. A command's **execution host** is not obvious.  Some commands are pure
   UI (they run in the frontend), some change graph state (the backend),
   some read a backend path or fetch a URL (the backend), some bring a file
   from the *user's* machine, and an MCP-mediated command executes on the
   MCP host.
2. A command that changes graph state must be **checkpointed**: the
   LangGraph checkpoint is the source of truth for graph state, and
   `sessions.db` is a projection plus runtime config, not a competing
   source.

How should Klea model, parse, dispatch, persist and render in-session
commands across frontends and hosts, without forking every frontend and
without diverging state?

This ADR fixes the **framework only**.  The concrete command catalogue is
provisional and lives in `devdocs/system/session-commands.md`; a single
command whose decision is hard to reverse may later get its own ADR.

## Decision Drivers

* One authoritative source for graph state: the LangGraph checkpoint
  (ADR-0023, ADR-0043); `sessions.db` stays a projection.
* Frontends may be remote from the backend; the execution host of a command
  must be explicit, not assumed.
* Autocomplete and `/help` need one complete catalogue, not per-frontend
  hardcoded lists that drift.
* Provenance: anything that changes state must be recorded.
* Commands are conceptually the **user-driven analogue of tool calls**: the
  user chooses the "tool".  The mechanism should mirror the existing tool
  picker/caller (ADR-0020, ADR-0044) rather than invent a new one.
* Keep shared mechanics in `klea_utils` so every frontend reuses them; keep
  app-specific commands with their app (ADR-0031).
* Deliver incrementally in small, reviewable, atomic steps.

## Considered Options

Grouped by the axes where a genuine choice existed.  The chosen option is
marked **(chosen)**.

* **A. Where commands are handled**
  * A1 Frontend-only (client parses everything; state changes via REST).
  * A2 Server-only (the frontend forwards `/...`; a backend node does all).
  * **A3 Split: frontend handles UI-local; a graph command node handles
    state/workflow (chosen).**
* **B. Where graph-state settings persist**
  * B1 `sessions.db` authoritative.
  * **B2 Checkpointed graph state via the command node (chosen).**
  * B3 Direct checkpoint write (`aupdate_state`) with no node.
* **C. Command-node placement**
  * **C1 Right after `Initializing` (chosen).**
  * C2 Inside the existing entry router (after guard/mode).
  * C3 Before `Initializing`.
* **D. Framework location and catalogue transport**
  * **D1 Shared `klea_utils` core + a per-app registry; `GET /commands`
    publishes the app's server-side catalogue, and each frontend owns its
    client-side commands and merges them (chosen).**
  * D2 Shared core + a shared Python spec imported by both sides.
  * D3 Per-frontend hardcoded lists.
* **E. Location model for files/commands**
  * **E1 An explicit `side` (execution host) per command, and a named
    command per source (backend path / client file / URL) (chosen).**
  * E2 A single `/file` that assumes one host.
  * E3 opencode-style `@file` / `!shell` prefixes.
* **F. Persistence/provenance of commands**
  * **F1 Per-command `persists` (none | checkpoint | message) (chosen).**
  * F2 All commands ephemeral.
  * F3 All commands persisted as chat messages.
* **G. Delivery order**
  * **G1 Web frontend first; the terminal REPL is left untouched (chosen).**
  * G2 Build both frontends at once.
* **H. Behaviour while a run is streaming**
  * **H1 A per-command `while_streaming` flag; defer prompt queueing
    (chosen).**
  * H2 Build the per-thread prompt queue now.
  * H3 Reject every command while streaming.
* **I. Session workspace location**
  * **I1 User data dir, keyed `{user_data_dir}/sessions/{user_id}/{chat_id}/`
    (chosen).**
  * I2 User cache dir.

## Decision Outcome

Chosen options: **A3, B2, C1, D1, E1, F1, G1, H1, I1**.

Commands are modeled as **user-invoked tool calls**.  The backend publishes
its **server-side** catalogue via `GET /commands`; each frontend owns its own
**client-side** commands and merges the two for its menu and validation, so
the graph never needs to know a frontend's commands (no circular dependency;
a client-side command reaching the graph is simply unknown).  Each frontend
routes an input that starts with `/` by the command's class, and the graph
exposes a command node that is the user-facing counterpart of the tool
picker/caller.

### Taxonomy

Each catalogue entry declares:

```
name, aliases, summary, arg_hint,
side: server | client,          # execution host, not "which frontend"
klass: ui | session-state | workflow | prompt,
while_streaming: block | allow,
persists: none | checkpoint | message,
capabilities?: {local_fs, local_shell, file_picker, ...}
```

* `side` is the execution host: `server` = the graph/tools backend;
  `client` = wherever the frontend process runs (the user's machine for a
  local TUI; still "the user's side" for a TUI over SSH).
* `klass` decides handling: `ui` and client-side `session-state` commands
  run in the frontend; `server`/`workflow` commands run in the graph.
* `capabilities` is a small, centrally-defined vocabulary so frontends
  filter consistently (the browser has no `local_fs`/`local_shell`; both
  frontends can do `file_picker`).

### Dispatch

* **Frontend**: an input beginning with `/` is parsed against the merged
  catalogue (the frontend's own client commands plus the server catalogue
  fetched from `GET /commands`), with a `//` escape for a literal leading
  slash.  `ui` / client commands are handled locally; an unknown command is an
  error plus `/help`; a server command is forwarded **as the query** ("the
  frontend treats it as text input").  A `/`-menu over the merged catalogue
  provides autocompletion.
* **Backend**: `chat_core` recognises a leading `/` that is a known graph
  command and marks the turn accordingly; the graph's **command node**
  handles it.  The node mirrors `ToolsPicker` / `ToolsCallerNode`: a
  registry of commands, validate -> resolve -> dispatch -> emit events.
  It emits `inspect` events (ADR-0040, `_CustomChannelEnabler`) so the
  inspection history records every command and state change; no separate
  command log is added.
* **Wiring**: a conditional edge right after `Initializing`
  (`Initializing -> {command | continue}`) plus `add_edge(CommandNode, END)`
  (agent; the analogue after `Initializing` in RAG).  Running after init is
  required because `InitGraphState` resets `message_for_user`; commands skip
  guard/mode/planner (trusted user input, no LLM).

### State and persistence

* Graph-state settings (mode, access, project root) are changed by **graph
  commands** and live in the checkpoint only (B2).  The UI selectors
  dispatch the same command, so there is one path and no drift; an initial
  selection before the first run is a client-side pending value until a
  checkpoint exists.
* `persists` is per command: `none` for pure UI actions, `checkpoint` for
  state changes (the default for graph commands; no chat row), `message`
  when a command should enter the LLM's action history as a real turn.
* `prompt` commands (custom templates) are expanded by the frontend into a
  normal query and persist as a normal turn.

### Output contract (frontend-agnostic)

Client-side handlers return a `CommandResult` (`output: list[str]`,
`error: str`, `notice: str`, `handled: bool`); frontend-specific effects
(open the model dialog, switch chat) are performed by the handler through
the `CommandContext` capabilities.  Server-side commands produce no
`CommandResult`: their text is the graph's `message_for_user` plus streamed
`inspect` events, rendered by each frontend like any other output.  How a
frontend renders (a transcript bubble, a toast, a pane) is a per-frontend
detail, not part of this decision.

### Host caveat (documented for users)

A `server` command's effect follows the *executing host*: bundled tools
share the graph backend's host, but an external MCP server runs on its own
host, so a command mediated by it acts relative to that host.  Nothing in
Klea can bridge this; MCP servers must expose tools to reach other machines.

### Mid-stream behaviour

Graph commands contend with the existing single-active-run guard and are
`while_streaming: block`; commands that do not need the graph (`/help`,
`/stop`, UI actions) are `while_streaming: allow`.  A per-thread prompt
queue (which would let graph commands and prompts queue behind a run) is a
separate decision, deferred.

### Consequences

* Good, because there is one authoritative source for graph state (the
  checkpoint) and one catalogue for every frontend.
* Good, because the execution host of a command is explicit (`side`), which
  is honest about the frontend/backend/host split.
* Good, because the command node reuses the tool picker/caller pattern and
  the existing event/stream contract, so provenance comes for free.
* Good, because commands stay in shared plumbing plus per-app registries,
  matching ADR-0031.
* Bad, because a graph command is a (cheap) run and is blocked while a run
  is streaming until a prompt queue exists.
* Bad, because the split means a user must distinguish a backend path from
  a client file (e.g. `/file` vs `/upload`).
* Bad, because an external-MCP command's effect can land on another host,
  which users must understand.

### Confirmation

* Unit tests: parsing, aliases, `//` escape, `/help`, capability filtering.
* Web tier-1 tests: `/help`, unknown command, a graph stub forwarded as a
  query, `//` escape, capability filtering.
* A test that a `persists: checkpoint` graph command writes no chat rows
  while still updating checkpointed state.
* Review: a new command's `side`/`klass`/`persists` are checked against the
  host model above when it is added.

## Pros and Cons of the Options

### A1 Frontend-only

* Bad, because it cannot cleanly mutate checkpointed graph state (the
  command would need an endpoint or a direct checkpoint write), and
  workflow commands (`/compact`, `/plan`) need the graph.

### A2 Server-only

* Bad, because UI actions (open a dialog, `/stop`) cannot run in the
  backend.

### A3 Split (chosen)

* Good, because each class runs where its capability lives.

### B1 `sessions.db` authoritative

* Bad, because it diverges from the checkpoint, making the store a second
  source of truth for graph state.

### B2 Checkpoint via command node (chosen)

* Good, because the change is checkpointed memory and survives session exit,
  with one source of truth.

### B3 Direct checkpoint write (`aupdate_state`)

* Good, because it is cheap and needs no run.
* Bad, because it bypasses the node's resolution logic, needs an existing
  checkpoint, and adds a second write path.  Kept as a fallback idea if a
  command proves too expensive as a run.

### C1 After `Initializing` (chosen)

* Good, because it is the earliest interception and skips guard/mode/planner.

### C2 In the entry router

* Bad, because commands should not pass guard or mode resolution.

### C3 Before `Initializing`

* Bad, because `InitGraphState` resets `message_for_user`.

### D1 Shared core + `GET /commands` (chosen)

* Good, because the API publishes the server-side catalogue for any frontend
  and the graph keeps only server-side commands (no circular dependency on a
  frontend's command list), while client-side commands stay with each
  frontend and handlers stay with their app.

### D2 Shared Python spec

* Bad, because it couples frontends to app internals and gives no transport
  for remote frontends.

### D3 Hardcoded per-frontend lists

* Bad, because they drift.

### E1 Explicit `side` (chosen)

* Good, because it is honest about where each command operates; a named
  command per source (`/file`, `/upload`, `/webfetch`) disambiguates.

### E2 Single `/file`

* Bad, because it is wrong across the frontend/backend split.

### E3 `@file` / `!shell` prefixes

* Bad, because the location is ambiguous in a split architecture; the `/`
  prefix plus a named command carries the intent.

### F1 Per-command `persists` (chosen)

* Good, because state changes need no chat noise while some commands need
  to enter the LLM's action history.

### F2 All ephemeral

* Bad, because some commands must be visible to the model as actions.

### F3 All persisted

* Bad, because "user changed mode" is noise.

### G1 Web first (chosen)

* Good, because the terminal REPL is temporary and will be replaced by a
  Textual TUI, so effort there would be wasted.

### H1 `while_streaming` + defer queue (chosen)

* Good, because `/stop` and UI commands still work mid-run while graph
  commands wait, consistent with today's "wait for the run to stop".

### H2 Build the queue now

* Deferred: ordering, backpressure and cancel interaction are their own
  decision.

### H3 Reject everything while streaming

* Bad, because UI/stop commands must work mid-run.

### I1 Data dir (chosen)

* Good, because uploads are user-provided and not regenerable; the cache
  dir is for regenerable/derived data (catalog, UA list, ingestion, DOI).

### I2 Cache dir

* Bad, because cache content is regenerable and may be auto-cleaned.

## More Information

* Command catalogue and per-command spec: `devdocs/system/session-commands.md`
  (provisional; evolves per phase).
* Related: ADR-0020 (unified tool caller), ADR-0026 (client-server),
  ADR-0031 (apps own API/UI), ADR-0032/0033/0045 (context/state ownership),
  ADR-0040 (inspect/tool/token stream contract), ADR-0043 (checkpoint
  resume), ADR-0044 (dynamic tool-call schema), ADR-0046 (HITL).
* Tool picker/caller pattern to mirror: `klea_utils/nodes/tools_picker.py`,
  `klea_utils/nodes/tools_caller.py`; dynamic schema in
  `klea_utils/mcp/call_schema.py`.
* The per-session project root will be a separate ADR and reuses the session
  workspace and the path gate (`mcp-permissions.md`, `system/file-tools.md`).
* Delivery order and the concrete phases are tracked in `devdocs/backlog.md`
  (the open-work list), not here.
