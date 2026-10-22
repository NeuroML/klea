---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Bundled stdio MCP server and tag-filterable tool filtering

## Context and Problem Statement

Klea needs a small set of common tools (web fetch, file list/read,
download) available to every domain without requiring a separately
deployed server.  Each RAG domain also needs to expose only the subset
its LLM should see, and the same tool set must be reusable by external
hosts (e.g. a remote HuggingFace deployment).  Tool filtering must be
declarative in config, while behavioural intent (read-only,
destructive) must be visible to any MCP client without adopting Klea's
vocabulary.

How should Klea package and filter its common tools, and how should
intent be signalled?

## Decision Drivers

* No extra setup for local single-user RAG (``klea-rag tui``/``web`` auto-spawns
  the server when none is running, see ``utils_pkg/klea_utils/graph/base.py``
  ``spawn_server``).
* Must be deployable on shared infrastructure (HuggingFace Spaces runs
  three services in one container: ``nml-mcp &``, ``klea-rag-serve &``,
  ``klea-rag web`` foreground -- see ``deployments/huggingface/``).
* Per-domain tool subsets without editing server code (a domain that only
  wants web tools should not see local file tools).
* Effect/intent (read-only, destructive, idempotent, open-world) must be
  carried by standard MCP ``ToolAnnotations`` (``readOnlyHint``,
  ``destructiveHint``) so any compliant host can enforce it.
* Testability: bundled tools must be testable via ``Client(bundle_server)``
  and via the ``klea-mcp`` CLI.

## Considered Options

* **A. Separate HTTP bundled server (always)** -- a long-running HTTP MCP
  server that every app dials.  Rejected: requires manual launch for local
  dev, complicates the container entrypoint order (MCP -> RAG -> frontend),
  and adds an extra port to manage per app.
* **B. No bundled server -- each app imports ``tool_impls`` directly** --
  apps call ``klea_utils.mcp.tool_impls.*`` functions inline.  Rejected:
  loses the MCP abstraction (tool listing, schema, annotations, stdio/HTTP
  transport), and forces apps to duplicate filtering.
* **C. Bundled stdio subprocess auto-launched per app (chosen)** -- the
  common tools live in ``klea_utils.mcp.server.bundled`` as a FastMCP
  server (``bundle_server``).  At startup each app calls
  ``BaseLangGraph._bundled_server_config()`` (``utils_pkg/klea_utils/graph/base.py:490``)
  which returns a dict-form ``StdioMCPServer`` entry ``{command:
  sys.executable, args: ["-m", "klea_utils.mcp.server.bundled"]}``.  The
  entry is merged into ``MCPConfig.mcpServers``.  ``register_tools`` is
  never called directly in apps -- the tools are discovered via the MCP
  client like any other server.  The same module also serves over HTTP via
  ``klea-mcp --transport http`` (``klea_utils.mcp.server.bundled:app``)
  for remote deployments.
* Tag filtering: ``ToolInfo.tags`` carries Klea-local categories
  (``bundled``, ``local``/``web``, ``files``/``code``/``download``/
  ``neuroml``/``neuroml-db``/``osb``).  ``MCPConfig`` per-server
  ``include_tags``/``exclude_tags`` are applied by FastMCP's
  ``TransformingStdioMCPServer`` (selected automatically when tag filters
  are present).  Behavioural intent is not tagged; it is carried by
  ``ToolInfo.read_only``/``destructive``/``idempotent``/``open_world``
  mapped by ``register_tools`` to MCP ``ToolAnnotations``.
* Alternatives considered for filtering: a single vocabulary for both
  purposes (rejected -- conflates config selection with universal intent;
  see ADR-0006 discussion of ``bundled_tools.include_tags``); filtering
  via env vars (rejected -- not declarative per domain).

## Decision Outcome

Chosen option: "C. Bundled stdio subprocess with two-axis filtering
(tags for config, annotations for intent)".

* ``utils_pkg/klea_utils/mcp/server/bundled.py:31`` -- ``bundle_server =
  FastMCP("KleaBundled", lifespan=make_http_session_lifespan())``; ``register_tools(bundle_server, [bundled_tools])``.
* ``utils_pkg/klea_utils/mcp/server/bundled_tools.py`` -- four wrappers
  (``web_fetch``, ``list_files``, ``read_file``, ``download_file``) each
  ``@tool_meta(ToolInfo(tags={...}, ...))``; ``list_files``/``read_file``
  and ``download_file`` declare ``checkpaths`` for the permission layer.
* ``utils_pkg/klea_utils/graph/base.py:490`` -- ``_bundled_server_config()``
  builds the per-app ``mcpServers`` entry (returns ``None`` when
  ``general.bundled_tools.enabled`` is false).  The dict form (not a typed
  ``StdioMCPServer``) lets ``MCPConfig``'s union validator pick
  ``TransformingStdioMCPServer`` when filters exist, plain ``StdioMCPServer``
  otherwise; downstream ``list_tools`` still advertises the bundled tools
  under the prefixed name (``bundled_*`` is not a visible prefix; the
  bundled server is a single unnamed entry merged per domain).
* Default enablement: ``agent_pkg`` enables the bundled server (batteries
  included); ``rag_pkg`` disables it by default (each RAG deployment
  wires in only the tools it needs via ``include_tags``/``exclude_tags``
  on ``general.bundled_tools``).  ``klea_utils.mcp.server.bundled:app``
  Typer exposes ``klea-mcp [--transport http --port 8000]`` for remote use.
* Tag vocabulary is fixed to scope (``local``/``web``) plus functional
  groups (``bundled``, ``files``, ``code``, ``download``, ``neuroml``,
  ``neuroml-db``, ``osb``); ``bundled`` is on every bundled tool so the
  whole common set is ``include_tags: ["bundled"]``.  Effect is carried
  by annotations (``readOnlyHint``/``destructiveHint``/``idempotentHint``/
  ``openWorldHint``) set from ``ToolInfo``.

### Consequences

* Good, because local single-user flow has zero manual steps (auto-launch
  via ``spawn_server`` with loopback-only guard; pre-probe reuse of an
  already-running server).
* Good, because per-domain filtering is declarative and composes with the
  same ``include_tags``/``exclude_tags`` used for external HTTP MCP servers
  (``klea_utils.mcp.server.config`` passthrough).
* Good, because ``std::io`` transport has lowest latency and no port
  contention per app; the same tool set is also available as HTTP for
  remote deployments without code fork.
* Good, because any MCP host (not just Klea) sees ``readOnlyHint``/
  ``destructiveHint`` without knowing Klea's tag vocabulary.
* Bad, because a dedicated bundled subprocess exists per app (one
  ``python -m klea_utils.mcp.server.bundled`` per ``klea-rag``/``klea``
  instance) -- extra memory per app, but acceptable for the local case
  and invisible on shared infra where the server runs inside the same
  container.
* Bad, because tag vocabularies can drift; ``_bundled_server_config``
  is the single chokepoint that must stay in sync with ``ToolInfo.tags``
  declarations.

### Confirmation

* ``Client(bundle_server).list_tools`` + ``Client(bundle_server).call_tool``
  via ``utils_pkg/tests/test_bundled_server.py`` (four tools) and
  ``mcp_pkg/tests/test_tool_tags.py:22`` tag-vocabulary asserts.
* ``klea-mcp --help`` / ``klea-mcp --transport http --help`` starts
  without eager imports of orchestrators (Typer deferral; see
  ``AGENTS.md`` CLI conventions).
* Live: ``klea-rag tui`` auto-spawns the bundled server when no
  ``klea-rag-serve`` is running and reuses an already-running one when
  it is (loopback-only check via ``split_server_url``/``is_loopback_host``);
  HuggingFace container boots ``nml-mcp &``, ``klea-rag-serve &``, then
  ``klea-rag web`` foreground as documented in
  ``deployments/huggingface/scripts/docker-deploy.sh``.
* Lint/type: ``ruff check``/``ruff format``/``ty`` clean for
  ``bundled.py``/``bundled_tools.py``/``graph/base.py``; ``pytest -v``
  ``mcp_pkg`` and ``utils_pkg -m "not localonly"`` pass.

## Pros and Cons of the Options

### Bundled stdio subprocess auto-launched per app (chosen)

* Good, because zero manual steps for local single-user (auto-launch)
* Good, because per-domain filtering is declarative via ``include_tags``/``exclude_tags``
* Good, because same tool set is also available as HTTP (``klea-mcp --transport http``) without code fork
* Good, because intent is universal via standard ``ToolAnnotations``, not tag vocabulary
* Bad, because extra subprocess per app (acceptable but not zero-cost)

### Separate HTTP bundled server (always)

* Good, because single server for many clients (shared infra case)
* Bad, because manual launch required for local dev; port contention; extra lifecycle to manage per app

### No bundled server, direct tool_impls import

* Good, because no process hop
* Bad, because loses MCP tool listing/schema/annotations/transport; duplicates filtering in each app

## More Information

* Code: ``utils_pkg/klea_utils/mcp/server/bundled.py:31`` (server),
  ``utils_pkg/klea_utils/mcp/server/bundled_tools.py`` (wrappers),
  ``utils_pkg/klea_utils/graph/base.py:490`` (``_bundled_server_config``),
  ``utils_pkg/klea_utils/mcp/lifespan.py`` (``make_http_session_lifespan`` +
  ``http_session`` key), ``klea_utils.mcp.registry.register_tools`` (tag/
  annotation folding).
* Related system doc: ``../system/c4-container.md`` (bundled container,
  ``agent -> bundled``, ``rag -> bundled`` edges); ``docs/concepts/mcp.rst``
  (``The bundled tools server``, ``Tag vocabulary``, ``Behavioral intent``);
  ``docs/developer-info.rst`` (``Architecture``).
* Decisions: tag vs annotation choice predates this ADR and is recorded
  together here because filtering only exists because the bundled server
  exists; alternatives (single vocabulary, env-var filtering) are listed
  in Considered Options.
* Commits: ``f782409`` (move bundled tools to ``klea-mcp`` stdio),
  ``46c46df``/``96db080`` (bundled tool config wiring),
  ``e882407``/``03c1fb1`` (re-tag + annotation split), ``e9e37f3`` (utils
  consolidation).
* Related: ``devdocs/system/mcp-permissions.md`` (permission layer does not
  special-case bundled tools -- same ``checkpaths`` flow), ``devdocs/README.md`` index.
