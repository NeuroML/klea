---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Declarative path permissions with dual-layer check and deferred interactive policy

## Context and Problem Statement

MCP tools can read/write the filesystem and fetch URLs.  Klea must
confine filesystem tools to the user's project directory and block
SSRF to private hosts, without halting the graph on denial (the LLM
should receive a synthetic error and adapt).  Klea authors only a
subset of the servers it connects to, so any in-tool guard is
author-side only.  The client must also gate third-party tool calls
before they reach the server process.  For servers Klea does not
author there is no path-level confinement at all without OS isolation.

What permission model should Klea provide, and how far should it go
before OS sandboxing is required?

## Decision Drivers

* Path-aware, not just tool-name-aware: ``opencode`` asks "may this
  tool be invoked?" (see ``system/mcp-permissions.md`` opencode
  analysis); Klea's literature use-case needs "may it touch path X?"
* Author-side and client-side must agree on the same boundary (default
  cwd, resolved) and on the same error semantics (non-halting, clear
  message, not an exception that aborts the graph).
* Third-party servers: Klea cannot inspect their internals; the gate
  can only filter arguments the tool declares.
* Network safety: ``web_fetch`` and ``download_file`` must refuse
  loopback/private/reserved hosts by default.
* Interactive ``allow/deny/ask`` (opencode-style ``ctx.ask({permission:
  key})`` with "once"/"always" and visibility filtering) would be
  useful but implies a graph-pause + TUI/web input loop that crosses
  the LangGraph boundary.

## Considered Options

* **1. In-tool ``check_path_access`` only (implemented as layer 1)** --
  ``klea_utils.mcp.tool_impls.permission.check_path_access(path, project_root=None)``
  resolves both sides and denies ``..`` traversal and symlink escapes.
  Every filesystem tool gates its path args and returns a non-halting
  error on denial (e.g. ``klea_utils.mcp.tool_impls.list_files``,
  ``download_file``; ``download_file_to_cache`` boundaries itself to its
  own cache dir, and sandboxed code tools to their sandbox).  Path-aware
  and strict, but only for tools Klea authors (``system/mcp-permissions.md:
  The author-side limit``).

* **2. Client-side pre-dispatch policy layer (per-path half implemented,
  allow/deny/ask deferred) (chosen as layer 2)** -- ``ToolInfo.checkpaths``
  declaratively marks which arguments are paths
  (``@tool_meta(ToolInfo(..., checkpaths=["path"]))``); ``register_tools``
  folds them into the tool's ``_meta.checkpaths``.  At call time
  ``klea_utils.mcp.dispatch.dispatch_tool_calls`` + ``check_tool_arguments_permissions``
  (``klea_utils/mcp/tool_impls/permission.py``) evaluates each declared
  path against the same boundary (default cwd).  Denied calls never reach
  the MCP server; they become a synthetic ``isError:true`` result (same
  shape as ``klea_utils.mcp.tool_result.to_result``).  The gate runs in
  the shared ``ToolsCallerNode`` (``klea_utils/nodes/tools_caller.py``)
  used by both ``klea_agent`` and ``klea_rag``.  The complementary
  ``allow/deny/ask`` ruleset and interactive approval loop (graph pause +
  TUI/web input) are deliberately deferred (``permission.py`` TODO).

* **3. OS-level sandboxing** -- run third-party MCP servers (or the
  whole agent) under ``bubblewrap``/container/``chroot`` with only the
  project directory mounted.  Orthogonal to 1+2; the only hard boundary
  for servers Klea does not author.

* **4. opencode-style name-based gate (not chosen)** -- wrap every
  external tool, match ``allow/deny/ask`` against the tool name
  (``server_tool`` with ``tool-server_*`` wildcards, ``ask`` default
  with "always" persisted per session; see
  ``packages/opencode/src/permission/index.ts`` and visibility
  filtering).  Rejected as the primary gate because it cannot ask
  "path X?" and "always" turns one careless approval into a session-wide
  pass.  It remains the model for the deferred ``allow/deny/ask``
  interactive half of option 2, not the path-aware half.

## Decision Outcome

Chosen option: "1 + 2 together, with 3 as the outer boundary".

* Layer 1 author-side: ``check_path_access`` (both sides ``Path.resolve``,
  ``error`` field, ``PermissionDeniedError``) is mandatory in every
  Klea-authored filesystem tool; clients agree on ``project_root=None``
  (cwd) by default so both layers share the boundary; ``system/``
  contracts document that ``download_file_to_cache`` and sandboxed tools
  are self-contained helpers with their own boundary.
* Layer 2 client-side: ``check_tool_arguments_permissions(tool_meta,
  arguments, project_root=None)`` reads ``tool_meta["checkpaths"]`` and
  returns a denial list without raising; ``dispatch_tool_calls`` injects
  synthetic ``CallToolResult(is_error=True)`` denials; the gate only
  applies when the tool declares ``checkpaths``.  ``ToolsPicker`` writes
  ``tool_calls: list[ToolCallSchema]`` and ``ToolsCallerNode`` writes
  ``tool_results: list[CallToolResult]`` (``klea_utils.mcp.schemas``)
  for both apps (see ``system/mcp-permissions.md: Standardised tool
  call state``; RAG's additional ``post_dispatch`` per-plan-step callback
  applies in the agent).
* SSRF: ``klea_utils.mcp.tool_impls.ssrf.check_ssrf`` /
  ``is_private_or_reserved`` guards ``web_fetch``/``download_file`` for
  loopback, private, link-local, reserved, multicast unless
  ``allow_internal_hosts=True``.  Known best-effort limitation accepted
  for now: only the *initial* URL is checked; an ``httpx`` redirect
  (``follow_redirects=True``) could hop to an internal host -- fix would
  be manual hop-by-hop redirect with re-check (see
  ``system/mcp-permissions.md:92``).

Deferred half of 2: the ``allow/deny/ask`` ruleset and interactive
approval loop (graph pause + TUI/web input, visibility filtering that
hides denied tools from the system prompt) are out of scope until a
LangGraph pause/input design is settled.  ``kanban`` board + ``permission.py``
``TODO`` track it.

### Consequences

* Good, because every Klea-authored tool is path-aware and strict via
  both layers; declarative ``checkpaths`` makes the LLM's per-path
  errors actionable without halting the graph.
* Good, because path vs name concerns are separated: ``ToolInfo.tags``
  + ``ToolAnnotations`` (``ADR-0004``) handle "may this tool be
  invoked?"; ``checkpaths`` handles "may it touch path X?" (see
  ``../system/c4-container.md`` two-axis note).
* Bad, because a third-party server that does not declare ``checkpaths``
  (or any server Klea does not author) is not path-gated by 1 or 2;
  only ``3`` (OS sandbox / container) can confine it.  The posture is
  documented as trust-dependent: never connect to a server you do not
  trust.
* Bad, because ``allow/deny/ask`` UX (once/always, visibility filtering)
  remains missing until the graph-pause design lands; users cannot yet
  interactively approve per-tool/per-path calls.
* Bad, because SSRF redirect hopping is best-effort until hop-by-hop
  checking is added.

### Confirmation

* ``klea_utils.tests.test_tools_permission`` + ``test_tools_caller``
  (dispatch gate) + ``test_bundled_server`` (wrappers via ``to_result``)
  exercise both layers; ``mcp_pkg/tests/test_tool_tags.py:22`` bis
  asserts ``checkpaths`` propagation to ``_meta`` (see
  ``klea_utils.mcp.registry.register_tools``).
* ``system/mcp-permissions.md:49`` ``flowchart TD`` renders on GitHub
  after ``mermaid/mcp-permissions.md:53`` quote fix
  (``{"check_tool_arguments_permissions\n(checkpaths?)"}``) and passes
  ``mmdc``; ``docs/concepts/mcp.rst`` error-handling note now references
  the dual layer and the ``isError:true`` contract.
* Live: denied path via ``check_tool_arguments_permissions`` surfaces
  as a synthetic error never touching the MCP server; denied path
  inside the tool surfaces as ``Error``/``error`` ``ToolResult`` --
  both render as ``**Error:**`` in ``klea_utils.tools.textualize_tool_results``.
* Lint/type: ``ruff``/``ty`` clean for ``permission.py``/``dispatch.py``/
  ``tools_caller.py``; ``pytest -m "not localonly"`` still required for
  MCP tests (asyncio + single-process ``addopts = -n 1``).

## Pros and Cons of the Options

### In-tool path checks only (layer 1, implemented)

* Good, because path-aware and strict for Klea-authored tools
* Bad, because no path confinement for third-party servers (``author-side limit``)

### Client-side per-path policy layer, deferred interactive loop (layer 2, chosen complement)

* Good, because declarative ``checkpaths`` travels to clients via tool
  ``_meta``; per-path denials are pre-dispatch (never invoke server)
* Good, because non-halting synthetic results let the LLM adapt
* Bad, because tools that do not declare ``checkpaths`` are not gated
* Bad, because ``allow/deny/ask`` interactive loop remains deferred
  (graph pause + TUI input)

### OS-level sandboxing (outer boundary, recommended for third-party servers)

* Good, because only hard boundary for servers Klea does not author
* Bad, because per-server container/bwrap config is additional ops burden
  (orthogonal to 1+2)

### opencode-style name-based allow/deny/ask only (not chosen as sole gate)

* Good, because ``allow/deny/ask`` UX exists today in ``opencode``
* Bad, because it asks "may this tool be invoked?" not "may it touch
  path X?"; ``ask``/"always" consent is not confinement

## More Information

* Amended ``2026-09-14``: the client-side caller now passes a single
  ``{tool_name: ToolInfo}`` map to ``dispatch_tool_calls`` (and
  ``ToolsCallerNode``), replacing the separate raw ``tools_meta`` map.
  ``ToolInfo`` carries both the ``checkpaths`` metadata (in ``meta``) and
  the ADR-0037 ``read_only``/``destructive`` capability, so the path gate
  and the access-level gate share one classification.
  ``check_tool_arguments_permissions`` is unchanged.
* Design note promoted here: ``../system/mcp-permissions.md`` (current
  state, limits, opencode reference ``anomalyco/opencode
  2026-08`` ``packages/opencode/src/permission/index.ts`` and
  ``src/session/tools.ts``, options 1-3, recommended posture ``1+2`` +
  sandbox).  This ADR records the accepted posture; that file remains
  the implementation-facing contract and its mermaid diagram.
* Code: ``klea_utils/mcp/tool_impls/permission.py`` (``check_path_access``,
  ``check_tool_arguments_permissions``), ``klea_utils/mcp/dispatch.py``
  (``dispatch_tool_calls`` + ``_denied_result`` ``is_error:true``),
  ``klea_utils/nodes/tools_caller.py`` (shared ``ToolsCallerNode``),
  ``klea_utils/mcp/schemas.py`` (``ToolInfo.checkpaths``,
  ``ToolCallSchema``/``ToolCallsSchema``), ``klea_utils/mcp/tool_impls/ssrf.py``
  (``check_ssrf``/``is_private_or_reserved``), ``utils_pkg/klea_utils/graph/base.py``
  (standardised ``tool_calls``/``tool_results`` state fields), ``AGENTS.md``
  HTTP + permissions conventions.
* Related: ``docs/concepts/mcp.rst`` (error handling ``isError``,
  implemented ``ADR-0003``), ``devdocs/system/c4-container.md`` / ``docs/developer-info.rst`` (``klea -> bundled`` / ``rag ->
  bundled`` edges), ``AGENTS.md`` permissions conventions.
* Codified ``2026-08-28``; design note originally ``2026-08-26``
  (``system/mcp-permissions.md`` ``Last updated``); implementation days
  ``2026-08-14..17`` (``f782409``, ``15cf08e``).
