---
status: "accepted"
date: 2026-10-14
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Per-session project root

## Context and Problem Statement

Klea's filesystem tools run in the bundled MCP server, a stdio **subprocess**
spawned once per app process.  The subprocess's working directory is fixed at
spawn (inherited from the API server), so every session resolves relative paths
against the same directory, and the client-side permission boundary
(`project_root`) defaults to that directory too.  A session can therefore only
target the folder `klea web` / `klea-serve` was started in; parallel sessions
cannot target different folders.

We want a session to operate in a chosen folder: the file tools resolve
relative paths there, `AGENTS.md`/`CLAUDE.md` discovery reads from there, and it
is the default permission boundary (paths outside it go through the existing
approval gate).  The model must never choose the root; the user does.

## Decision Drivers

* Let parallel sessions target different project folders.
* Keep the single shared MCP client (reentrant; concurrent calls multiplexed by
  request id) -- no per-session connection pool.
* The tools must operate under the chosen root even though the subprocess cwd
  is fixed.
* The root is the default permission boundary; outside paths still use the
  ADR-0007 approval gate.
* Do not let the model name an arbitrary root.
* Reuse the existing MCP spec where it fits.

## Considered Options

* **A. Resolve relative path arguments to absolute client-side.**  Store the
  root in state; before dispatch, resolve relative path arguments against it.
* **B. Convey the root per call out-of-band** (MCP request `meta` -> a server
  contextvar) and resolve relative paths server-side.
* **C. A per-root MCP subprocess** spawned with `cwd=<root>`.
* **MCP roots** (the spec's client-declared filesystem boundaries).

## Decision Outcome

Chosen option: **A**, because it needs no server changes, works with the shared
client, and is general enough to fix any local tool that takes path arguments
(not only Klea-authored ones).

### Semantics

* **State.** `BaseGraphSchema.project_root` (session-scoped; kept in the thread
  checkpoint, not reset by init nodes).  Empty means the server process working
  directory.
* **Set it.** The `/cwd [path]` graph command (ADR-0047) reports or sets it
  (must be an existing directory; relative resolves against the current root).
  There is no operator base/allowlist yet -- without auth, the person running
  the server already controls the host (see Consequences).
* **Boundary.** `ToolsCallerNode` passes `state.project_root` (falling back to
  cwd) as `dispatch_tool_calls`'s `project_root`; the gate treats paths inside
  it as permitted and outside paths as before (ADR-0007).
* **Convey per call.** `resolve_path_arguments` (`klea_utils/mcp/path_detect.py`)
  makes relative path arguments absolute against the root before detection and
  dispatch: a declared `checkpaths` value is always resolved (string or
  `list[str]`); other arguments are resolved heuristically when the argument
  **name is path-ish** and the value is a single-token, non-URL/non-flag/non-glob
  string.  Multi-token shell command strings are never rewritten, so
  `run_command.command` and regexes like `sed '/x/'` are safe;
  `run_command.working_directory` defaults to the root when unset.  Absolute
  paths and URLs are left untouched.
* **Discovery.** `InitGraphState` passes the effective root to
  `refresh_project_files`, so `AGENTS.md`/`CLAUDE.md` is read from the root.

### Why not MCP roots

MCP **Roots** is the spec's client-declared filesystem boundary, but it encodes
**server-side confinement** ("I only operate within these roots"), which
conflicts with Klea's model: the **client** resolves absolute paths and gates
permissions, and must be able to grant access **outside** the root after user
approval.  A root-honouring server would prefix our absolute paths and cap
itself to the root, defeating outside-root approvals; and roots are
per-connection and advisory, so with one shared client it cannot express a
per-session root.  Klea therefore sends absolute paths and enforces the boundary
client-side; roots is not used.

### Consequences

* Good, because a session can target any folder and parallel sessions can differ.
* Good, because no server change is needed and the shared client is reused.
* Good, because the same mechanism fixes third-party local tools that take path
  arguments (absolute paths are unambiguous regardless of the server's cwd).
* Bad, because an unannotated tool whose argument name is not path-ish, or a
  tool that resolves relative paths against its own cwd for non-argument work,
  is not covered; a tool that rigidly enforces MCP roots or prefixes all paths
  is incompatible.
* Bad, because a **remote** server may not share the client's filesystem at all,
  so the root is meaningless to it.
* Bad, because there is no operator allowlist: a shared/authenticated
  deployment would want to bound what a session may choose.
* Neutral: only OS sandboxing is a hard boundary (as with ADR-0007); the gate
  is advisory for tools that ignore their arguments.

### Confirmation

* `utils_pkg/tests/test_path_resolve.py` (declared/heuristic/list values,
  command strings untouched, `working_directory`, URLs/absolute untouched);
  `tests/test_nodes_tools_caller.py` (the state root is the boundary);
  `agent_pkg/tests/test_init_graph.py` (discovery reads from the root);
  `agent_pkg/tests/test_commands.py` (`/cwd`).
* Per-session permission decision (`not_a_path`) is in ADR-0007's update note.

## More Information

* ADR-0007 (path permissions), ADR-0037 (tool access), ADR-0047 (session
  commands, `/cwd`), ADR-0031 (per-app frontends).
* `devdocs/system/{mcp-permissions,file-tools,session-commands,tui}.md`.
