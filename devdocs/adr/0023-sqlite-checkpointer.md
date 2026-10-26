---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# SQLite checkpointer and session store for graph resumption

## Context and Problem Statement

LangGraph graphs are stateful across turns: the next ``ainvoke`` must
see prior messages, the compiled graph's checkpoints, and per-user/chat
model overrides (``model``, ``api_key``, ``base_url`` per ADR-0014).
Early Klea used LangGraph's ``InMemorySaver`` only, so restarts lost
history and deployments with multiple users could not isolate sessions.
LangGraph's checkpointing and Klea's own chat persistence were also
divergent: the checkpointer lived in the graph while chat history lived
in NiceGUI's ``app.storage.user`` (ephemeral, per-process).

How should checkpoints and chat/session state be persisted, scoped per
user/chat, and reused across graph resumptions?

## Decision Drivers

* Resumption: ``graph.ainvoke`` / ``graph.astream_events`` with
  ``RunnableConfig(configurable={"thread_id": ...})`` must replay prior
  checkpoints.
* Per-user/chat isolation: a public HuggingFace Space and local single-
  user must both isolate ``user_id``/``chat_id`` and per-chat model
  overrides (``KLEA_*_*_MODEL`` per session vs global env).
* Web/TUI persistence: chat history, ``dark_mode``, and the active
  ``chat_id`` must survive server restarts and browser reloads; NiceGUI
  ``.nicegui/storage-user-*.json`` is only a pointer to server-side state.
* Operational simplicity: the chosen store should not require an
  external DB (Redis/Postgres) on ``cpu-basic``.

## Considered Options

* **A. ``InMemorySaver`` only** -- the graph's default checkpointer
  holds checkpoints in process memory; chat history lives in
  ``app.storage.user``.  Rejected: restart loses checkpoints and chat
  history; ``app.storage.user`` is not durable across processes and
  cannot scope ``user_id``/``chat_id`` server-side.
* **B. ``AsyncSqliteSaver`` checkpointer + SQLite session store (chosen)**
  -- ``BaseLangGraph._setup_checkpointer`` selects between
  ``InMemorySaver`` (``checkpoint="inmemory"``), ``AsyncSqliteSaver``
  (``checkpoint="sqlite"`` at ``{user_data_dir}/checkpoints.db`` via
  ``aiosqlite``), or ``None`` (``checkpoint="none"`` → ``memory=False``).
  A dedicated ``klea_utils/api/sessions_db.py`` ``SessionStore``
  (SQLite) owns ``chat_sessions`` (including ``model overrides`` JSON
  blob per chat) and ``messages`` tables, exposed via
  ``klea_utils/api/sessions.py`` ``/sessions`` / ``/chat`` / ``/messages``
  endpoints.  ``user_id`` is excluded from the backend and derived from
  the ``.nicegui/storage-user-*.json`` pointer only.
* **C. External DB (Redis/Postgres) for both** -- Rejected: adds
  operational cost for ``cpu-basic`` demos and institutional
  deployments that already have path-based user-data dirs via
  ``platformdirs``.

## Decision Outcome

Chosen option: "B. ``AsyncSqliteSaver`` checkpointer + SQLite session store".

* ``utils_pkg/klea_utils/graph/base.py:549`` ``_setup_checkpointer``
  branches on ``self.checkpointer_mode`` (from ``BaseLangGraph.__init``
  ``checkpoint``).  ``AsyncSqliteSaver`` opens
  ``{user_data_dir}/checkpoints.db`` (``init_dir`` + ``aiosqlite``);
  ``InMemorySaver`` is the default; ``None`` disables checkpointing.
* ``klea_utils/api/sessions_db.py`` ``SessionStore`` owns the two
  tables; ``api/sessions.py`` serves the CRUD endpoints used by the
  NiceGUI/TUI runners and the ``klea-rag-serve`` SSE path.
* Checkpointing and session memory are orthogonal: ``memory`` flag
  (``checkpoint != "none"``) governs whether nodes receive
  ``memory=True`` (turn-faithful prompts via ``BaseMessage`` objects
  per ADR-0018); the checkpointer governs LangGraph resumption.
* ``BaseLangGraph.setup`` calls ``_setup_checkpointer`` before
  ``_load_env`` so the checkpointer is available even when env
  resolution fails.

### Consequences

* Good, because graph resumption works across restarts (LangGraph
  checkpoints are durable) and chat sessions survive server restarts
  (``sessions.db``) rather than living only in NiceGUI's ephemeral
  ``.nicegui/`` pointer files.
* Good, because per-chat model overrides (``model``, ``api_key`` per
  ``ADR-0014``) are stored per ``chat_id`` in ``chat_sessions`` and
  replayed via ``RunnableConfig(configurable={"thread_id": ...})``.
* Good, because no external DB is required on ``cpu-basic``; the
  store is a local ``.db`` file in the platform user-data dir.
* Bad, because SQLite is single-host: true horizontal scaling would
  need an external checkpointer/session store.
* Bad, because ``checkpoints.db`` and ``sessions.db`` add on-disk
  state that must be pruned externally; there is no automatic
  retention window today.

### Confirmation

* ``klea_utils/api/sessions_db.py`` unit coverage for CRUD
  (``chat_sessions`` including overrides blob, ``messages``) and
  ``graph/base.py:549`` ``AsyncSqliteSaver`` wiring.
* ``docs: make html`` still renders the pipeline figure;
  ``ty`` extra-paths for ``graph/base.py`` + ``api/sessions*``.
* Manual: ``klea-rag-serve`` with ``checkpoint="sqlite"`` writes
  ``checkpoints.db``; browser reload with ``.nicegui/`` pointer reopens
  the same ``chat_id``/model overrides; ``InMemorySaver`` and
  ``checkpoint="none"`` paths still work.

## Pros and Cons of the Options

### AsyncSqliteSaver + SQLite session store (chosen)

* Good, because durable resumption across restarts
* Good, because per-chat model overrides survive restarts
* Good, because no external DB on ``cpu-basic``
* Bad, because single-host SQLite (not horizontally scalable)

### InMemorySaver only

* Good, because zero disk state
* Bad, because restart loses checkpoints and chat history

## More Information

* Code: ``utils_pkg/klea_utils/graph/base.py:549`` (``_setup_checkpointer``),
  ``api/session_store.py`` / ``api/sessions.py`` / ``api/sessions_db.py``
  (session store + endpoints), ``api/app.py`` (FastAPI wiring),
  ``graph/base.py:40`` (``model_overrides_ctx`` per-session overrides
  consumed via ``RunnableConfig``).
* Related: ``ADR-0014`` (runtime model switching stored per chat),
  ``ADR-0018`` (``BaseMessage``-object memory), ``ADR-0013`` (inspection
  stream shows ``usage`` per node).
* Commits: ``d0b6dbe`` (add sqlite checkpointer), ``6fa52e4`` (add db
  CRUD), ``901aaff`` (add session endpoints), ``2772039`` (update chat
  to use session store), ``0255bea``/``a74487b`` (session plumbing).
* Codified ``2026-08-28``; checkpointer/session store landed
  ``2026-07-22..23`` during the session-persistence sprint.

## Amendment (2026-10-25): one backend per storage root

**Status:** accepted -- amends the decision above; the `AsyncSqliteSaver` + SQLite session-store choice stands. Supersedes the earlier (internal) `ThreadRoutingSqliteSaver` proposal.

### Context

Two problems were observed with multiple `klea serve` backends:

1. A second `klea serve` fails with SQLite lock errors, because every backend opens the same `<user_data_dir>/checkpoints.db` and LangGraph's `AsyncSqliteSaver` sets no `busy_timeout` (no WAL, no retry).
2. A single backend forces every `klea-tui` session's MCP tool calls into that backend's venv: `_bundled_server_config` spawns `sys.executable -m klea_utils.mcp.server.bundled` with no `env` and no `cwd` (only cwd is bridged, via absolute-path rewriting).

Tracks considered: A1 (WAL + `busy_timeout`), A2 (per-thread checkpoint routing, `ThreadRoutingSqliteSaver`), A3 (PID lockfile), B1/B2 (env + cwd passthrough / per-session MCP client).

### Decision

Support exactly **one backend per storage root**, and make that safe and explicit rather than assumed:

- **B2 -- per-session MCP client with env/cwd passthrough**, so a session's tool environment follows the session, not the backend's venv.
- **A1 -- SQLite pragmas** (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout`) so restart, crash and incidental-overlap windows wait instead of raising `SQLITE_BUSY`.
- **A3 -- PID lockfile** with pid + process start-time owner identity, stale detection and defined loser behaviour, so "one backend per storage root" is enforced and legible.
- **`KLEA_AGENT_HOME`** as an opt-in *relocation* override (data *and* runtime artifacts: endpoint, pidfile, logs), letting users run deliberately independent, **irreconcilable** instances. It is not a concurrency mechanism.

### Alternatives rejected

- **A2 -- `ThreadRoutingSqliteSaver` proxy saver** (one DB file per `thread_id` under `user_data_dir/checkpoints/<hash>.db`, lazy `aiosqlite.connect` + `setup()`, `aclose()`, `alist(None)` scoping, sanitisation, LRU handle bounding, migration script). Rejected: it solves the wrong problem -- making one store safely writable by N backends -- instead of removing the need for N backends on one root. It is a permanent maintenance burden (handle lifecycle, fd bounding, path sanitisation, migration) and its only unique benefit, *simultaneous* cross-process view of a chat, was never a requirement. No upstream or community thread-routing saver exists to build on (official integrations are single-store adapters; closest prior art is `langgraph-tenancy` key-prefixing and `CachedCheckpointer` caching).
- **Informal "do not run two backends" policy.** Rejected: an unenforced rule cannot be assumed. Any enforcement is a lockfile with owner identity and stale detection, i.e. it *is* A3.
- **`KLEA_AGENT_HOME` as the primary concurrency answer.** Rejected: it is an *isolation* tool, not a *safety* tool. It does not make same-home contention safe, and two backends pointed at the same home (the likely mistake) stay unprotected without A1 + A3.
- **Concurrent backends on SQLite for shared/multi-user deployments.** Rejected: that is building multi-user semantics on a single-writer file. Shared/hosted deployments use the Postgres/Redis checkpointer instead.
- **Per-backend `--data-dir` / option C.** Rejected as a hacky, non-transparent workaround: each backend sees a different file for the same chat, so history does not follow the user and reconciliation is pushed onto the user.

### Consequences

* Good, because one code path and one shared `checkpoints.db`: unified history, every chat visible to the next backend, with no sync, registry or ownership map.
* Good, because contention becomes bounded and legible (A3 diagnostic or A1 bounded wait) instead of an opaque `SQLITE_BUSY`.
* Good, because upgrades still work: stop/restart is the accepted workflow, and the brief overlap of an upgrade is absorbed by WAL.
* Good, because B2 removes the main *reason* people start a second backend (per-session tool environments).
* Good, because shared deployments have a clean, correct answer (Postgres/Redis) instead of a SQLite workaround.
* Bad, because a session cannot run against two different *backend-side* dependency stacks at once (graph/agent/RAG code, not tools) without a second `KLEA_AGENT_HOME` and a split history.
* Bad, because simultaneous cross-process view of one chat is impossible, and users who split homes have irreconcilable state.
* Bad, because A1 + A3 are new moving parts (pragma setup, lockfile, stale detection, loser behaviour) that need tests and clear error messages.

### Open items

- Postgres/Redis checkpointer implementation -- the sanctioned path for shared/multi-user/hosted deployments.
- Authentication/authorisation for shared deployments (deliberately out of scope here; a saver choice is not an authn/authz model).
- Option precedence for the home override (`flag > env > default`) and its interaction with existing `--data-dir` semantics.  (The env > default path is implemented; there is no `--data-dir` flag on `klea-serve`.)
- Merge/reconciliation story for users who already split homes -- or an explicit statement that none exists.
- Precedent: OpenCode V2 (one shared per-user server, WAL, `busy_timeout=5000`, `SQLITE_BUSY` retries, per-session sharding), Claude Code (`daemon.lock`, roster, stale checks), Codex (one app-server per `CODEX_HOME`).

### Implementation note (2026-10-26)

`KLEA_AGENT_HOME` is wired through `BaseLangGraph.storage_root`: the agent sets
`storage_root_env` to that variable.  Checkpoints, the session database
(including the stored provider credentials), the backend lockfile, and the
logs all use the resolved root.  The default remains the platform user-data
directory for `klea-agent`, so existing installations are unchanged; RAG has no
relocation variable yet.  The pre-existing `resolve_storage_root` helpers were
previously unused.
