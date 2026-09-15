---
status: "proposed"
date: 2026-09-14
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Tool access levels (read_only | full) from MCP annotations, with documented trust limits

## Context and Problem Statement

Klea graphs can invoke MCP tools, some read-only (search, list, fetch) and
some mutating (write files, download, run commands).  Today every
configured tool is disclosed to the Planner and picker and dispatched
regardless of the caller's intent, so there is no way to run a
least-privilege ("read-only") agent.

MCP expresses tool intent through standard `ToolAnnotations`
(`readOnlyHint` / `destructiveHint`, ADR-0004), and `ToolInfo` already
carries `read_only` / `destructive` fields, but
`BaseLangGraph._build_tools_info` drops the annotations when it builds the
per-domain tool metadata, so no consumer sees them.  ADR-0007 established
the *path* axis ("may this tool touch path X?", `checkpaths`) and
explicitly left the *invocation* axis ("may this tool be invoked at all?")
to `tags` + `ToolAnnotations`; ADR-0035 defers a deterministic
`read_only | full` access level to its own ADR (this one).

The MCP specification is explicit that annotations are **hints**: servers
self-report them, so they cannot be trusted against a malicious server.
What access model should Klea adopt, what can it honestly guarantee, and
where does it belong?

Tool *tags* are a separate, earlier mechanism: fastmcp applies a server
entry's `include_tags` / `exclude_tags` when it constructs the MCP server
or proxy, so they decide **which tools exist at all** for a deployment (a
pre-exposure visibility filter, ADR-0004), not **what a tool may do**.
By the time Klea lists tools the excluded ones are already gone, which is
why `ToolInfo` never carries tags and `clean_tool_meta` strips the
leftover fastmcp tag metadata.  Capability is therefore a different
question from exposure.

## Decision Drivers

* Least privilege: a `read_only` run must not be able to mutate the
  workspace, even if the model asks.
* Shared, not app-specific: the same contract must serve the agent, RAG
  and future graphs (ADR-0032 gives the shared state home).
* Declarative: derive the classification from standard MCP metadata, not a
  hand-maintained tool list that drifts.
* Fail-closed: an unannotated tool is not assumed safe.
* Non-halting denials: a denied call becomes a synthetic error result the
  LLM can adapt to (the ADR-0007 error contract), never an exception that
  aborts the graph.
* Honest limits: annotation-based filtering is a least-privilege /
  correctness rail, not a security boundary; OS sandboxing remains the
  only hard boundary (ADR-0007 layer 3).
* Backward compatible: the agent's current behaviour is preserved by a
  `full` default, so the level is opt-in.

## Considered Options

* **1. opencode-style name allow/deny/ask ruleset** -- match rules against
  the tool name with wildcards and a default `ask`.  Rejected as the
  primary mechanism for the same reason ADR-0007 rejected it for paths: it
  answers "may this be invoked?" but not "what may it do?", and "always"
  consent turns one approval into a session-wide pass.  It remains the
  model for the *deferred interactive consent* half.
* **2. Hand-maintained per-app tool allowlist** -- explicit but drifts
  from tool metadata and duplicates the annotation tool authors already
  declare.
* **3. Annotation-driven access level (chosen)** -- derive from the MCP
  `readOnlyHint` / `destructiveHint` already carried by `ToolInfo`;
  `read_only` permits only tools explicitly marked read-only and not
  destructive; `full` permits all.  Missing annotations fail closed.
* **4. No filtering (status quo)** -- no least-privilege option; rejected.
* **5. Dispatch gate without disclosure filtering** -- enforcement only at
  dispatch.  Rejected as incomplete: the model would still be offered and
  tempted by tools it may not call, wasting calls and inviting
  hallucinated names.  Chosen *in addition to* 3, not instead of it.
* **6. OS-level sandboxing only** -- the only hard boundary for servers
  Klea does not author (ADR-0007 layer 3).  Orthogonal and recommended as
  the outer boundary; it does not by itself give a per-run least-privilege
  switch.
* **7. Capability declared via tags** -- reuse the tag vocabulary (reserved
  `read_only` / `destructive` tags) to carry capability.  Rejected: tags
  are a fastmcp exposure/grouping mechanism applied *before* tools reach
  Klea, they do not express the two-value `read_only` / `destructive`
  distinction cleanly, and client-side they are stripped.  Capability gets
  its own explicit declaration instead (see the operator override below).

## Decision Outcome

Chosen option: **3 (annotation-driven level) enforced at both disclosure
(5) and dispatch, with 6 as the outer boundary.**

### The level

* `klea_utils.mcp.access` defines
  `AccessLevel = Literal["read_only", "full"]`,
  `DEFAULT_ACCESS_LEVEL = "full"`, and the helpers
  `tool_permits(read_only, destructive, level)`,
  `filter_tools_info(tools_info, level)` and
  `check_tool_access(name, read_only, destructive, level)`.
* The rule: `full` permits every tool; `read_only` permits a tool only
  when `read_only is True and destructive is not True`.  A tool with no
  annotation (`None`) is **not** permitted in `read_only` (fail-closed).
* `access_level` lives on `BaseGraphSchema` (ADR-0032), so every app and
  every future graph inherits it.  The agent defaults to `full`
  (overridable per request and by `KLEA_AGENT_ACCESS_LEVEL`); RAG is fixed
  at `read_only` -- it retrieves, and must never mutate (its
  `download_file` is therefore never offered).

### Propagation

`BaseLangGraph._build_tools_info` copies each MCP tool's
`annotations.readOnlyHint` / `annotations.destructiveHint` into the
`ToolInfo` it builds, so the classification is available to the nodes and
to dispatch without any new metadata channel.

### Operator capability override

A deployment may declare a tool's capability explicitly, for tools whose
server does not annotate (or annotates inaccurately).  A
`ToolAccessOverride` model in `klea_utils.mcp.access`, exposed as a
per-tool map in the app config (`general.tool_access`), maps a tool name
to `read_only` / `destructive` booleans:

```json
"tool_access": {
    "myserver_search": {"read_only": true},
    "other_fetch": {"read_only": false, "destructive": true}
}
```

Precedence is **explicit `tool_access` override > tool annotation >
fail-closed**.  An override may relax (treat an unannotated tool as
read-only) or restrict (mark an over-optimistic tool destructive); the
operator who wrote the config is the trust root for that declaration.
The map is per-tool only -- a per-server default is all-or-nothing and
therefore adds nothing over "no annotations" / `full`.  The override is
applied once when `ToolInfo` is built, so disclosure filtering and the
dispatch gate agree.  This is deliberately not annotation-only: without
it, a tool from a trusted server that simply lacks annotations would be
hidden in `read_only` or force the whole run to `full`.

### Enforcement points

* **Disclosure (visibility):** the Planner (short tool descriptions) and
  the shared `ToolsPicker` (full descriptions) filter `tools_info` by the
  state's `access_level` before building their prompts, so a disallowed
  tool is never shown to the model.
* **Dispatch (hard gate):** `ToolsCallerNode` passes the level and the
  flattened `{tool_name: ToolInfo}` map (which already carries the
  `read_only`/`destructive` capability and the `checkpaths` metadata) to
  `dispatch_tool_calls`, which rejects a call to a disallowed tool with the
  same synthetic `is_error` result used for `checkpaths` denials
  (ADR-0007); the call never reaches the MCP server.  This covers a model
  naming a hidden tool and keeps the failure non-halting.

### Trust model and limitations

* MCP `ToolAnnotations` are **self-reported hints**
  (`mcp.types.ToolAnnotations` docstring: "all properties ... are hints
  ... Clients should never make tool use decisions based on
  ToolAnnotations received from untrusted servers").  For Klea-authored
  (bundled) tools the annotations are Klea's own declarations and are
  authoritative; for third-party servers they are only as trustworthy as
  the server, consistent with ADR-0007's "only connect to servers you
  trust".
* The access level is therefore a **least-privilege / correctness rail for
  trustworthy tools**, not a security boundary against a malicious server:
  a server that lies about its annotations (or a tool with no annotations)
  cannot be confined by this mechanism.  The only hard boundary remains
  OS-level sandboxing (ADR-0007 layer 3), which Klea still requires for
  servers it does not author.
* Because unannotated tools fail closed, adopting `read_only` may hide
  third-party tools that simply do not declare annotations; tool authors
  should declare `readOnlyHint` / `destructiveHint` (Klea's `ToolInfo`
  already exposes them, ADR-0004).
* The `tool_access` override is an operator declaration, not a server
  claim: it is only as trustworthy as the person who reviewed the tool and
  wrote the config.  It does not change the boundary analysis above -- a
  malicious server is still confined only by OS sandboxing.

### Deferred roadmap (not part of this ADR)

Named so the limitation above is not mistaken for a solution:

* Interactive allow/deny/ask consent with a graph pause and TUI/web input
  (the deferred half of ADR-0007, opencode-style), for tools the user has
  not pre-approved.
* Sandbox-by-default for third-party MCP servers (container / bubblewrap /
  chroot with only the project mounted) and/or credential and environment
  scoping.
* A curated/vetted MCP server registry so non-technical users choose
  declared-safe servers rather than arbitrary ones.
* Capability transparency in the UI (server provenance + annotations) to
  support informed consent.

### Consequences

* Good, because a read-only run cannot invoke a mutating tool, and the
  contract is shared across apps and future graphs.
* Good, because classification is declarative from standard metadata and
  fails closed when metadata is absent.
* Good, because enforcement is non-halting and consistent with the path
  gate's synthetic-error contract.
* Good, because the `full` default preserves existing behaviour, so the
  change is additive.
* Good, because `tool_access` lets a trusted operator enable unannotated
  third-party tools in `read_only` without granting the whole run `full`.
* Bad, because unannotated third-party tools are unavailable in
  `read_only` unless the operator declares them via `tool_access`; the
  default stays fail-closed.
* Bad, because `tool_access` shifts responsibility onto the operator and
  can be misconfigured.
* Bad, because annotation accuracy is trust-based, so this cannot be sold
  as protection against a malicious server; only sandboxing can.
* Neutral, because the safety is opt-in for the agent (`full` default);
  RAG is safe by default.

### Confirmation

* Unit tests: `tool_permits` / `filter_tools_info` / `check_tool_access`
  including fail-closed unannotated tools; annotation propagation in
  `_build_tools_info`; `tool_access` precedence (relax an unannotated tool
  to read-only, restrict an over-optimistic one); Planner and
  `ToolsPicker` disclosure filtering; `dispatch_tool_calls` denials under
  `read_only` and allowance under `full`.
* Integration: a RAG graph never offers `download_file`; an agent graph in
  `read_only` hides it and rejects a forced call.
* Lint/type: `ruff` and `ty` clean; `pytest -m "not localonly"` for the
  package suites.

## Pros and Cons of the Options

### Annotation-driven access level (3, chosen)

* Good, because declarative from the standard hints tool authors already
  declare.
* Good, because fail-closed and app-agnostic.
* Bad, because it inherits the trust limits of self-reported hints; not a
  boundary vs untrusted servers.

### Dispatch gate only (5, rejected as sole mechanism)

* Good, because it catches the last line regardless of disclosure.
* Bad, because without disclosure filtering the model wastes calls on
  hidden tools and may hallucinate names; less predictable.

### Name-based ruleset (1, rejected as primary)

* Good, because fine-grained per-name control and interactive consent.
* Bad, because it cannot express capability and is trust- and
  consent-based rather than confinement (ADR-0007); deferred as the
  interactive half.

### Hand-maintained allowlist (2, rejected)

* Good, because simple and explicit.
* Bad, because it duplicates tool metadata and drifts.

### No filtering (4, rejected)

* Good, because no new machinery.
* Bad, because no least-privilege option at all.

### OS sandbox only (6, outer boundary)

* Good, because the only hard boundary for untrusted servers.
* Bad, because it is per-deployment ops work and not a per-run switch.

## More Information

* Related: ADR-0004 (MCP annotations, `ToolInfo`), ADR-0007 (path
  permissions and the deferred interactive half), ADR-0032 (shared state),
  ADR-0035 (deferred the level), `devdocs/system/mcp-permissions.md`
  (opencode analysis, trust limits), `docs/concepts/mcp.rst` (tool
  description and annotation conventions).
* Code loci: `utils_pkg/klea_utils/mcp/access.py`,
  `utils_pkg/klea_utils/graph/state.py` (`access_level`),
  `utils_pkg/klea_utils/graph/base.py` (`_build_tools_info` propagation +
  `tool_access` override),
  `utils_pkg/klea_utils/nodes/tools_picker.py`,
  `utils_pkg/klea_utils/nodes/tools_caller.py`,
  `utils_pkg/klea_utils/mcp/dispatch.py`,
  `agent_pkg/klea_agent/api/chat.py` (request),
  `agent_pkg/klea_agent/config.py` / `rag_pkg/klea_rag/config.py`
  (`general.tool_access`).  Tag exposure (a different mechanism) is
  documented in `docs/concepts/mcp.rst` and stripped client-side by
  `klea_utils.tools.clean_tool_meta`.
* Status: proposed 2026-09-14; flip to accepted once reviewed.
