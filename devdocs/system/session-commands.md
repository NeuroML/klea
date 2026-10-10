# Session commands: catalogue and contract

Status: in progress.  The framework is decided in ADR-0047
(`adr/0047-session-command-framework.md`); this note holds the concrete,
**provisional** command catalogue and the per-command contract.  It evolves
as each command is implemented; a command whose decision is hard to reverse
may graduate to its own ADR.

## Model (from ADR-0047)

Commands are **user-invoked tool calls**.  A single catalogue is shared by
all frontends via `GET /commands`; each frontend routes an input starting
with `/` by the command's class, and the graph exposes a command node that
mirrors `ToolsPicker` / `ToolsCallerNode`.

Catalogue entry fields:

```
name, aliases, summary, arg_hint,
side: server | client,          # execution host, not "which frontend"
klass: ui | session-state | workflow | prompt,
while_streaming: block | allow,
persists: none | checkpoint | message,
capabilities?: {local_fs, local_shell, file_picker, ...}
```

* **side** = execution host: `server` (graph/tools backend) or `client`
  (the frontend process host).  A `server` command's effect follows the
  executing host; an external MCP server runs on its own host.
* **klass**: `ui` and client `session-state` run in the frontend;
  `server`/`workflow` run in the graph; `prompt` is a frontend-expanded
  template that becomes a normal query.
* **persists**: `none` (pure UI), `checkpoint` (state change, no chat row),
  `message` (enters the LLM's action history as a real turn).
* **capabilities**: standard vocabulary, so frontends filter consistently.
  Starting set: `local_fs` (read/write the frontend host FS), `local_shell`
  (run on the frontend host), `file_picker` (native picker / upload).

## Dispatch

* Frontend: `/`-prefixed input is parsed against the catalogue (`//` escapes
  a literal `/`).  `ui`/client commands run locally; an unknown command is
  an error plus `/help`; a graph command is forwarded as the query.  A
  `/`-menu (from `GET /commands`) provides autocompletion and filters by
  `capabilities`.
* Backend: `chat_core` recognises a known leading-`/` graph command; the
  graph command node (conditional edge right after `Initializing`, then
  `END`) resolves, dispatches to the app registry, mutates state, sets
  `message_for_user`, and emits `inspect` events for provenance.  Commands
  skip guard/mode/planner.

## Command node

The command node is the user-facing counterpart of the tool picker/caller:
an app-built `CommandRegistry` (handlers with access to that app's nodes and
state), a validate/resolve/dispatch step, and `inspect` stream events.  No
separate command log is kept.

## Session workspace

Session-scoped file content (uploads and session fetches) lives under the
user data dir, keyed by user and chat:

```
{graph.paths.user_data_dir}/sessions/{user_id}/{chat_id}/
```

It is added to the tool `allowed_dirs` (the path gate) and cleared when the
session is deleted.  Discovery (`discovery_persistent`) references the
stored files.  Uploads are user-provided and not regenerable, hence the
data dir rather than the cache dir (cache holds regenerable/derived data:
catalog, UA list, ingestion, DOI).

## Catalogue (provisional)

| command | side | klass | persists | while_streaming | capabilities | summary |
|---|---|---|---|---|---|---|
| `/help [cmd]` | client | ui | none | allow | - | list commands (or detail one) |
| `/commands` | client | ui | none | allow | - | list the catalogue |
| `/new` | client | ui | none | allow | - | start a new chat |
| `/sessions` | client | ui | none | allow | - | list and switch sessions |
| `/rename <name>` | client | ui | none | allow | - | rename the current chat |
| `/export` | client | ui | none | allow | - | export the transcript to Markdown |
| `/model` | client | ui | none | allow | - | open the model picker |
| `/theme` | client | ui | none | allow | - | choose the web theme |
| `/upload <path>` | client | session-state | checkpoint | allow | `file_picker`, `local_fs` | attach a client file to discovery |
| `/mode [general\|scientific]` | server | session-state | checkpoint | block | - | set the operating mode |
| `/access [read_only\|full]` | server | session-state | checkpoint | block | - | set the tool access level |
| `/cwd [path]` | server | session-state | checkpoint | block | - | set the session project root (own ADR) |
| `/file <path>` | server | workflow | checkpoint | block | - | add a backend file to discovery |
| `/webfetch <url>` | server | workflow | checkpoint | block | - | fetch a URL into discovery |
| `/compact` | server | workflow | checkpoint | block | - | summarise/shorten memory |
| `/plan` | server | workflow | checkpoint | block | - | enter plan/review |
| `/init` | server | workflow | message | block | - | write `AGENTS.md` |
| `/run <cmd>` | server | workflow | message | block | - | run a command via the `run_command` tool |
| `/skills` | - | - | - | - | - | deferred (kanban): prompt/skill templates |

Notes:

* `/file` reads a **backend** path; `/upload` brings a **client** file to
  the backend; `/webfetch` fetches a URL server-side.  All three feed
  session discovery.  `/file` works directly in the common local
  `klea web` case and for backend files.
* `/run` executes server-side through `run_command` (permission gate +
  timeout + audit).  A client-side `/runlocal` is **omitted**: the browser
  cannot run a shell, it would bypass the gate, and the terminal REPL is
  being replaced.
* `/mode`/`/access`/`/cwd` change checkpointed graph state; the UI selectors
  dispatch the same command so there is one path.
* Custom `/prompt` commands (opencode-style templates) expand to a normal
  query and persist normally; `/skills` is the placeholder for that surface.

## Pointers

* Decision: `adr/0047-session-command-framework.md`.
* Tool picker/caller pattern: `klea_utils/nodes/tools_picker.py`,
  `klea_utils/nodes/tools_caller.py`; dynamic schema
  `klea_utils/mcp/call_schema.py`.
* Stream events: `system/streams.md` (ADR-0040).
* Path gate and workspace boundary: `system/mcp-permissions.md`,
  `system/file-tools.md`.
