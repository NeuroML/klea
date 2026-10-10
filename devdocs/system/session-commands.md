# Session commands: catalogue and contract

Status: implemented (framework).  The framework is decided in ADR-0047
(`adr/0047-session-command-framework.md`); this note holds the command
catalogue and the per-command contract.  The framework, the web routing /
autocomplete, and the first commands (`/help`, `/mode`, `/access`) are
implemented;
further commands are added as they land, and a command whose decision is hard
to reverse may graduate to its own ADR.

## Model (from ADR-0047)

Commands are **user-invoked tool calls**.  The backend publishes its
**server-side** catalogue via `GET /commands`; each frontend owns its
**client-side** commands and merges the two for its menu and validation, so
the graph never needs to know a frontend's commands (a client-side command
reaching the graph is simply unknown).  Each frontend routes an input
starting with `/` by the command's class; the graph exposes a command node
that mirrors `ToolsPicker` / `ToolsCallerNode`.

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

* Frontend: `/`-prefixed input is parsed against the **merged** catalogue
  (the frontend's own client commands plus the server catalogue from
  `GET /commands`); `//` escapes a literal `/`.  `ui`/client commands run
  locally; an unknown command is an error plus `/help`; a server command is
  forwarded as the query.  A client command's output is rendered as a
  `system` block in the active chat, creating one if the command is the
  session's first input (a chat is the transcript host, so the output is not
  a transient notification).  A suggestion list over the merged catalogue
  (filtered by `capabilities`) autocompletes as the user types `/prefix` and
  inserts a command when an item is clicked.  Keyboard navigation (Up/Down,
  Tab, Escape) is deliberately omitted: typing the command and pressing Enter
  is sufficient, and an unconditional Vue key modifier would break Tab focus
  order out of the chat box.
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

## Catalogue

Two sources are merged by each frontend: the **server catalogue** served by
`GET /commands`, and the **client commands** each frontend defines.  A
frontend validates input against the merge and forwards only server commands
to the graph.  Only **implemented** commands are published: a command still
in code as `implemented=False` is filtered out of `GET /commands`, `/help`,
and the menu (`CommandRegistry.available`).

### Server catalogue (`GET /commands`)

| command | klass | persists | while_streaming | summary |
|---|---|---|---|---|
| `/mode [general\|scientific]` | session-state | checkpoint | block | show or set the operating mode |
| `/access [read_only\|full]` | session-state | checkpoint | block | show or set the tool access level |

Planned server commands (present in code as `implemented=False`, not
published yet): `/cwd`, `/file`, `/webfetch`, `/run`, `/compact`, `/init`.
The attachment trio (`/file`, `/upload`, `/webfetch`) plus the session
workspace is **deferred pending a use-case decision** -- see the backlog for
the options (convert-to-workspace vs ingest-and-retrieve vs in-prompt).

### Client commands (frontend-owned)

Each frontend defines these; the backend does not know them.  Only
implemented commands are registered (and so rendered in the ``/``-menu).

| command | klass | capabilities | summary |
|---|---|---|---|
| `/help [cmd]` | ui | - | list commands, or detail one |

Planned client commands (not registered yet): `/commands`, `/new`,
`/sessions`, `/rename`, `/export`, `/model`, `/upload`.

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
  query and persist normally.  `/skills` is **deferred**: skills are not
  designed yet, so the surface is not defined (see backlog).
* A leading `//` is not a command (the frontend and backend both treat it as
  ordinary text and send it as-is); a true single-slash escape is deferred
  since both sides must agree on it.

## Operator configuration

A deployment can disable session commands via the app config
(``general.commands.disabled``, ADR-0047):

```json
{"general": {"commands": {"disabled": ["run", "cwd"]}}}
```

A disabled command is not registered, so it is neither published by
``GET /commands`` nor runnable (a direct call is treated as unknown).  Names
are canonical (lower-case, without the leading slash).

## Pointers

* Decision: `adr/0047-session-command-framework.md`.
* Tool picker/caller pattern: `klea_utils/nodes/tools_picker.py`,
  `klea_utils/nodes/tools_caller.py`; dynamic schema
  `klea_utils/mcp/call_schema.py`.
* Stream events: `system/streams.md` (ADR-0040).
* Path gate and workspace boundary: `system/mcp-permissions.md`,
  `system/file-tools.md`.
