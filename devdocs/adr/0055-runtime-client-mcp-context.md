---
status: "accepted"
date: 2026-10-26
decision-makers: Ankur Sinha
consulted: "opencode agent"
informed: ""
---

# Runtime client context for the bundled MCP server

## Context and Problem Statement

Klea supports one backend per storage root (ADR-0023 amendment), so a user
runs a single `klea-serve`/`klea web` process for a given `KLEA_*_HOME`.  The
bundled MCP tools server was spawned with the backend's working directory,
environment, and Python interpreter, so every chat's tools saw the same
environment regardless of where the TUI was launched.  Starting a second
backend just to get a second tool environment conflicts with the
single-backend model and splits chat history.

Users need two TUIs connected to one backend to operate in different projects
or virtual environments: "run Klea in this directory/project" must include the
project's custom environment variables, not only Python-venv variables.

## Decision Drivers

* One backend can serve several chat sessions with different tool
  environments.
* The client's full environment matters: projects may use arbitrary custom
  variables, not only `VIRTUAL_ENV`/`PYTHONPATH`.
* The context is runtime data, not durable session metadata: it belongs to the
  frontend currently driving the chat.
* No new persistence/migration and no leakage of environment values into the
  checkpoint database.
* The trust boundary must be explicit: selecting a child process's cwd,
  interpreter, and environment is equivalent to selecting code to run.
* A changed environment between turns must not silently reuse a stale tool
  server.

## Considered Options

* **A. Persist a client context per chat, immutable at creation.**  Rejected:
  it turns runtime data into session state, requires a schema migration, and
  makes an old chat depend on a machine that may no longer exist.
* **B. Accept client context from any peer.**  Rejected: it hands arbitrary
  code selection to a remote caller; there is no use case where a remote
  backend shares the client's filesystem/interpreter.
* **C. Forward only a fixed allowlist of environment variables.**  Rejected:
  it misses project-specific variables, which are the main reason to preserve
  the environment.
* **D. Send the full client environment with each query (chosen).**

## Decision Outcome

Chosen option: **D**, because it keeps the context runtime-only, follows the
active frontend, and covers arbitrary project variables.

* The TUI builds a `ClientContext` from its current directory, its full
  environment, and its interpreter (`sys.executable`).  `--cwd`,
  repeatable `--env KEY=VALUE`, and `--interpreter` override these defaults;
  `--no-client-context` disables forwarding.  `--env` wins over the
  environment.
* The context is sent with every chat request (`/query`, `/query/stream`,
  retries, and HITL resumes) through the existing request-extra channel.
* The server accepts it only from a loopback peer (`is_loopback_host`),
  rejecting remote context with **403**; malformed context is **400**.  The
  TUI skips forwarding to a non-loopback server entirely.
* `chat_core` validates the context and adds it to the per-run
  `KleaRunContext` as `client_context`.  It is never placed in graph state or
  the checkpoint.  Environment values are never logged (only cwd,
  interpreter, and env-key count).
* `BaseLangGraph.new_mcp_client(client_context)` rebuilds only the `bundled`
  server entry with `spawn.bundled_server_config(env=..., cwd=...,
  interpreter=...)`; external MCP servers keep their configured invocation.
* `SessionMCPClients` fingerprints the context per session.  A changed
  fingerprint closes the old client and creates a new one, so a turn never
  uses a stale environment.
* When `BaseGraphSchema.project_root` is empty, the client cwd is the
  effective path-permission root and discovery root for the run.  An explicit
  `/cwd` wins.  The fallback is not written back to state, so relaunching the
  TUI in another project is followed on the next turn.
* The TUI status row shows the client cwd when no `/cwd` is checkpointed.

### Consequences

* Good, because parallel chats can target different projects/environments from
  one backend without splitting chat history.
* Good, because the context is private to the run: no schema change, no stale
  machine paths, and deleting a chat needs no context cleanup.
* Good, because arbitrary custom environment variables are preserved.
* Bad, because the full environment crosses the loopback HTTP boundary and may
  contain secrets.  It is accepted only from the same local machine, never
  logged or persisted, but it is present in process memory.
* Bad, because two TUIs driving the same chat with different contexts force a
  tool-server respawn between turns.
* Neutral: browser/web sessions send no context and keep the backend
  environment, which is appropriate because a browser has no local tool
  environment to describe.

### Confirmation

* `utils_pkg/tests/test_client_context.py` (validation, loopback, bounds,
  fingerprint).
* `utils_pkg/tests/test_mcp_session_pool.py` (reuse on the same fingerprint,
  respawn on change, respawn on removal).
* `utils_pkg/tests/test_bundled_config.py` (only the bundled entry is rebuilt;
  external servers are unchanged).
* `utils_pkg/tests/test_chat_core.py` and both apps' `test_chat_api.py`
  (runtime-context placement, 400/403 mapping, resume passthrough).
* `utils_pkg/tests/test_nodes_tools_caller.py`, agent `test_init_graph.py`,
  and `test_tui_project_root_ui.py` (client cwd as the default root,
  `/cwd` precedence, no fallback persistence).
* `utils_pkg/tests/test_client_cli.py` and `tests/ui/tui/test_app.py` (TUI
  flags, full env/override payload, remote skip, retry resend).

## More Information

* Supersedes the persisted/immutable `client_context` proposal in
  `.agents/2026-10-25-0314.md`.
* Related: ADR-0023 (one backend per storage root), ADR-0033 (per-run runtime
  context), ADR-0053 (per-session project root), ADR-0050 (Textual TUI).
* Code: `utils_pkg/klea_utils/client_context.py`,
  `utils_pkg/klea_utils/graph/context.py`,
  `utils_pkg/klea_utils/graph/base.py`,
  `utils_pkg/klea_utils/mcp/session_pool.py`,
  `utils_pkg/klea_utils/mcp/dispatch.py`,
  `utils_pkg/klea_utils/nodes/tools_caller.py`,
  `utils_pkg/klea_utils/api/chat_core.py`,
  `utils_pkg/klea_utils/ui/cli.py`,
  `utils_pkg/klea_utils/ui/tui/`.
* Deferred: folding the web's `mode`/`access_level` request fields into the
  same client-facing envelope; model overrides remain server-resolved and
  persisted.
