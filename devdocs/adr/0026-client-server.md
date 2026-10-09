---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Client-server architecture over monolithic app

## Context and Problem Statement

Industry coding agents (``Claude Code``, ``opencode`` etc.) are
monolithic apps: the LLM loop, file tools, and UI run in one process,
tool access is in-process function calls, and persistence is via the
local filesystem.  Klea instead serves ``klea-lang-graph`` instances
(``BaseLangGraph``, see ``ADR-0016``) as FastAPI apps, exposes vector
stores and MCP tool servers as backends, and consumes results via MCP
clients + Server-Sent Events.  Researchers launch CLIs that auto-spawn
a server, then connect over HTTP/SSE from a TUI, NiceGUI web UI, or an
external MCP client (``External AI Agents / MCP Clients`` in the C4
model).

Should Klea be a single monolithic app or a client-server product?

## Decision Drivers

* Must support remote and shared deployments: a curated RAG or agent
  run on HuggingFace Spaces (single container with ``nml-mcp &`` +
  ``klea-rag-serve &`` + ``klea-rag web`` foreground), on an
  institution's platform, and via external ``MCP Clients`` that
  consume ``nml-mcp`` or the RAG service over HTTP/MCP.
* Must keep the graph, the API, and the UIs on independent lifecycles
  (``graph/base.py`` ``run_graph_*`` seam, ``api/sse.py`` SSE, NiceGUI
  web UI vs the terminal REPL, a Textual TUI being planned) so the same
  orchestrator can be served locally, in a container, or on HF without
  forking the app.
* Must let tools run in isolated servers (``nml-mcp`` stdio-subprocess
  via ``BaseLangGraph._bundled_server_config`` per ``ADR-0004``, plus
  per-domain third-party MCP servers via ``MCPConfig``) and be
  selectable per domain via the same ``include_tags``/``exclude_tags``
  contract.
* Must not pay monolith cost of an always-attached vector-store
  backend in the CLI process (``rag`` stores can be Chroma/Qdrant/
  pgvector/BM25 across ``_pkg/``).

## Considered Options

* **A. Monolithic app (Claude Code/opencode style)** -- the LLM loop,
  file tools, and TUI run in one process; persistence is the local
  filesystem.  Rejected: no HTTP/SSE seam for external agents; cannot
  serve a single RAG as a HuggingFace Space or institutional platform
  service; ``mcp_pkg`` ``nml-mcp`` would be in-process code rather than
  a queriable MCP service; vector stores would be local-only.
* **B. Client-server with FastAPI + MCP + SSE (chosen)** -- the
  orchestrator (``BaseLangGraph``) is served as a FastAPI app
  (``klea_utils.api``: ``/query``, ``/query/stream`` SSE,
  ``/health/ready``, session/message stores).  Tools run in
  ``neuroml_mcp`` (``nml-mcp``) and ``klea-mcp`` MCP servers; clients
  (CLIs, TUI, NiceGUI web, external ``MCP Clients``) connect over
  HTTP/SSE or stdio MCP.  UIs are frontends, not owners: every CLI is
  really a thin ``spawn_server`` + ``Client`` wrapper.
* **C. Hybrid (server by default, local monolithic fallback)** -- variant
  of B where a bare CLI without a server runs in-process.  Rejected
  implicitly: the server's ``spawn_server`` auto-detection (``split_
  server_url``/``is_loopback_host``) already makes B behave as a
  monolith for local single-user (auto-spawns the server, reuses an
  already-running one), so a separate fallback is unnecessary.

## Decision Outcome

Chosen option: "B. Client-server with FastAPI + MCP + SSE".

* Orchestrator: ``utils_pkg/klea_utils/graph/base.py:70``
  ``BaseLangGraph`` (see ``ADR-0016``) plus ``klea_utils/api`` (``make_app``
  + ``sse.py`` ``/query/stream`` + ``sessions_db.py`` sessions).  Apps
  are ``KleaAgent`` (``klea``, ``klea-serve`` ``:8006``) and ``RAG``
  (``klea-rag``, ``klea-rag-serve`` ``:8005``) per ``AGENTS.md:32``.
* Tool servers: ``mcp_pkg/neuroml_mcp/server/main.py`` ``nml-mcp``
  (``:8542`` streamable-HTTP) + ``klea_utils.mcp.server.bundled``
  (``klea-mcp`` stdio per ``ADR-0004``) plus any per-domain third-party
  ``MCPConfig`` servers.  Tool filtering and permission gating sit in
  the client (``ADR-0004`` tag filtering + ``ADR-0007`` ``checkpaths``
  dual layer), not in the server process.
* Clients: ``klea_utils/ui/cli.py`` CLI auto-spawns the server when the
  ``--server`` URL is loopback and none is serving (``utils_pkg/
  klea_utils/api/utils.py:87`` ``check_api_is_ready`` + ``split_server_url``);
  external ``MCP Clients`` (``Person(extagent)`` in ``c4-system-context.md``)
  consume ``nml-mcp``/RAG over HTTP/MCP without a Klea CLI.  NiceGUI
  (``klea-rag web``) and the terminal REPL (``klea-rag cli``; a full
  Textual TUI is planned) are both SSE consumers of the same
  ``/query/stream``.
* Deployment: local ``uv pip install -r requirements-dev.txt`` + per-app
  ``graph.base`` + session SQLite (``sessions.db``) vs shared
  ``deployments/huggingface/Dockerfile`` container (``HuggingFace`` ->
  ``/v1`` model pulls, ``vector-stores`` via ``git lfs``/``xet`` per
  ``AGENTS.md:141``) -- same FastAPI + ``uvicorn`` services, differing
  only in ``KLEA_*_ENV_FILE``/``--profile`` and where the
  LLM/vector-store backends live.
* Monoliths are the counter-example: ``opencode`` (``sst/opencode``
  ``packages/opencode/src``) and ``Claude Code`` are single-process
  tool loops that own the filesystem; Klea is a general platform whose
  RAG is consumed both as a CLI and as an HTTP/MCP service by agents
  (``klea_agent -> klea_rag`` integration is still forward-ref'd as
  ``devdocs/system/c4-container.md:135`` ``adr/0003-agent-rag-integration``
  pending).

### Consequences

* Good, because the same code serves local single-user,
  ``HuggingFace``-as-a-Space, and institutional/shared infra with
  differing backends and model overrides (``ADR-0014``) -- same Python
  package, not a compiled binary.
* Good, because ``nml-mcp`` is a reusable service: external agents that
  never run Klea can still query ``neuroml-db.org``/``osb`` via MCP
  over HTTP.
* Good, because ``/query/stream`` SSE unifies TUI, NiceGUI, and external
  agents on one event stream (``ADR-0013`` inspection), and graph
  checkpointing (``AsyncSqliteSaver`` vs ``InMemorySaver``) is a server
  concern, not a CLI concern.
* Bad, because every local invocation pays the client-server handshake
  (health probe ``GET /health/ready`` + SSE).  ``spawn_server``
  mitigates it by pre-probing and reusing an already-running loopback
  server rather than spawning a new one.
* Bad, because a deployment now needs to pick ports (``:8005`` RAG,
  ``:8006`` agent, ``:8542`` ``nml-mcp``) and manage their lifecycle;
  the monorepo ``AGENTS.md`` workflow and ``devdocs/system/c4-container.md``
  container diagram are what make that operable.

### Confirmation

* ``klea-rag cli --single-query`` / ``klea-rag web`` both reuse an
  already-running ``klea-rag-serve`` when ``:8005`` is loopback+serving;
  otherwise they autospawn and connect over SSE; external ``Client(
  transport="http", url=":8542/mcp")`` reaches ``nml-mcp`` without a
  Klea CLI.
* ``klea_utils/api/app.py`` ``make_app`` + ``klea_utils/api/sse.py``
  ``/query/stream`` still serve ``AgentState`` ``message_for_user``;
  ``ty`` ``extra-paths`` still resolve cross-package imports for the
  FastAPI/MCP client seam; ``ruff`` clean for ``api/``.
* ``docs: make html`` ``build succeeded``; HuggingFace ``Dockerfile``
  builds the three-service container (``nml-mcp &`` + ``klea-rag-serve &``
  + ``klea-rag web`` foreground) per ``deployments/huggingface/``.

## Pros and Cons of the Options

### Client-server with FastAPI + MCP + SSE (chosen)

* Good, because same code serves local, HuggingFace, and shared infra
* Good, because ``nml-mcp`` is reusable over HTTP/MCP without Klea
* Good, because the terminal client (REPL; TUI planned), NiceGUI, and
  external agents share one SSE stream
* Bad, because per-invocation health-probe + SSE handshake even locally

### Monolithic app (Claude Code/opencode style)

* Good, because minimal operational complexity (one process)
* Bad, because no HTTP/MCP service reuse and no shared-infra story

## More Information

* Update (2026-10-09): the terminal client is a lightweight
  ``input()``-based REPL (``utils_pkg/klea_utils/ui/tui/repl.py``), not a
  Textual TUI.  A full Textual TUI -- shared components in
  ``klea_utils/ui/tui``, composed per app like the NiceGUI components -- is
  planned; see ``devdocs/backlog.md``.
* Code: ``utils_pkg/klea_utils/graph/base.py:70`` (``BaseLangGraph``),
  ``klea_utils/api/*`` (``make_app``, ``sse.py``, ``chat.py``,
  ``sessions_db.py``), ``klea_utils/ui/cli.py`` (``spawn_server`` +
  ``split_server_url``), ``mcp_pkg/neuroml_mcp/server/main.py``
  (``nml-mcp``), ``AGENTS.md:48`` (``uv pip install``),
  ``devdocs/system/c4-system-context.md`` (external systems) +
  ``c4-container.md`` (container diagram: ``agent``, ``rag``, ``nml-mcp``,
  ``bundled`` + ``Vector/BM25`` + ``Session/Checkpoint`` stores).
* Related: ``ADR-0004`` (bundled stdio ``klea-mcp``), ``ADR-0008``
  (always retrieve -- served via the same ``/query/stream``),
  ``ADR-0016`` (``BaseLangGraph`` that runs inside the server),
  ``.agents/2026-06-11.md`` (``CallToolResult.data``/``content``
  redundancy) and the refactored ``code_ai.py`` discussion.
* Commits: early Klea re-architecting away from a ``code_ai.py``
  monolith to ``BaseLangGraph`` + ``klea_utils.api`` + ``mcp_pkg``
  (``2026-03..06`` split), codified ``2026-08-28`` (client-server noted
  explicitly as a counter-example to ``opencode``/``Claude Code``).
