# Stores: domain search tool for the general agent (draft reference note)

Status: **draft reference note**.  Research notes and a locked but not yet
implemented tool contract.  This is internal material: do not link it from
`docs/`, and update the public documentation and `CHANGELOG.md` only when the
tool is implemented (see `devdocs/README.md` and `AGENTS.md` for the
ASCII-only and one-file-per-topic conventions).

Revision: the contract in sections 4-5 was revisited after reading the
existing retrieval layer.  The outcome is recorded in sections 7-10: the
reuse verdict and what must be added instead of a new core module (section 7),
the per-backend reload semantics and the reload mechanism the tool needs
(section 8), the per-session versus host-wide configuration decision with its
injection mechanism (section 9), and the corrected implementation file list
with the description-field move (section 10).  The "store configuration
source" open point of section 4 is thereby resolved and must be read together
with section 9.

Scope: how retrieval from the configured Klea stores is exposed to the two
agent paths (general/operational and scientific), the domain-based contract
that replaces the earlier path/URI idea, and the shared core both paths reuse.

Sources consulted:

- `utils_pkg/klea_utils/stores/config.py`
- `utils_pkg/klea_utils/stores/retrieval/base.py`, `vs.py`, `bm25.py`
- `utils_pkg/klea_utils/stores/utils.py`, `filters.py`
- `utils_pkg/klea_utils/mcp/schemas.py`, `access.py`,
  `tool_impls/permission.py`
- `utils_pkg/klea_utils/mcp/server/bundled_tools.py`,
  `tool_impls/web_search.py`, `tool_impls/search/resolver.py`
- `utils_pkg/tests/test_stores_retrieval.py`
- ADR-0007 (path permissions), ADR-0012 (BM25 hybrid), ADR-0027 (resolver
  cascade), ADR-0034 (tool calling mechanism), ADR-0036 (grounding
  enforcement), ADR-0037 (tool access levels), ADR-0041 (plan steps)

## 1. Two use cases and why a tool cannot enforce mandatory retrieval

There are two distinct requirements on store retrieval:

1. **General (operational) agent**: retrieval is *optional*.  The planner may
   consult the stores when the task needs local knowledge, and must be able to
   skip them when it does not.  This needs a tool the planner can see and
   choose.
2. **Scientific mode**: retrieval is *mandatory* before any grounded answer
   (ADR-0036).  It needs an unconditional graph step, not a suggestion to the
   model.

A tool cannot deliver requirement 2, for structural reasons visible in the
current code:

- Tool choice is the planner's.  Tools are disclosed to the planner as
  descriptions only (`ToolInfo.short_description` / `description`, see
  `mcp/schemas.py`), the picker binds arguments, and invocation follows prompt
  injection with structured emission (ADR-0034).  A model may simply not call
  it.
- Tool visibility is conditional.  Access levels filter the catalogue
  (`filter_tools_info` in `mcp/access.py`), and `read_only` fails closed for
  unannotated tools, so a tool can vanish from a deployment entirely.
- A graph node, by contrast, is an unconditional edge: it always runs, its
  result is in state, and a failure can be routed on (`system/graph-routing.md`).

Conclusion: expose retrieval to the general path as a **tool** and enforce it
in the scientific path as a **node**.  Both call the same core, so the
retrieval behaviour cannot drift between the two paths.

## 2. Why the domain-based contract replaces the earlier path/URI contract

The first sketch had the general-mode tool take a store path (for example
`chroma:/path/to/dir` or the path of a BM25 corpus pickle).  That is rejected:

- **Path/URI is deployment-internal.**  The URI scheme in `StoreInfo.path` only
  selects the backend (`vs.py` module docstring; `instantiate_vector_store` in
  `stores/utils.py`), and the collection name is `StoreInfo.name`.  Neither is
  a fact the model should be inventing.
- **Wrong trust boundary.**  A path argument turns a read-only knowledge tool
  into a filesystem surface and would need the client-side path gate
  (`ToolInfo.checkpaths`, `mcp/tool_impls/permission.py`).  No argument is a
  filesystem path in the domain contract, so no gate is required.
- **Wrong granularity.**  Retrieval is already organised by domain:
  `RetrieverConfig.domains` is the key, each `PerDomainConfig` holds a list of
  vector stores and BM25 stores, and `BaseKleaRetriever.retrieve` loads the
  domain lazily and unions all of its stores (`retrieval/base.py`).  The model
  should choose a domain; the deployment decides which stores back it.
- **Per-store tuning belongs to config.**  `default_k`, `k_max`, `k_inc` are
  per-store (with manager-wide fallbacks) and drive `inc_k`/`can_inc_k`
  (`retrieval/base.py`).  A path argument would tempt the model into
  controlling depth and store selection.
- **Naming collision.**  `tools_info` already groups tools by "domain" in the
  MCP metadata.  The tool is therefore named `search_stores`, keeping the store
  name distinct from the per-tool domain grouping in prompts.

A domain key is stable across backends, survives re-indexing, and is the same
unit the scientific-mode node will use.

## 3. Domain description ownership

Decision: a single optional `description: str = ""` goes on the **shared**
`PerDomainConfig` in `utils_pkg/klea_utils/stores/config.py`.

Rationale:

- The text is consumed by LLM-facing domain selection on *both* paths: the RAG
  query-domain classifier today and the general-mode tool description next.
  An app-side subclass field would fork the same JSON key into two divergent
  copies.
- Precedent in the same module: `FilterFieldInfo.description` is already a
  human-readable, LLM-facing field.
- It is metadata, not behaviour: optional, defaulted, never consulted by
  validation or retrieval.  Configs without the key keep loading unchanged,
  so the model stays lean.
- Fallback when a domain has no description: the catalogue renders the bare
  domain key (optionally with its configured store count/types).  No text is
  invented.  The domain is still accepted and listed, and the unknown-domain
  error still enumerates it.
- Consumers must read it defensively, `getattr(cfg, "description", "") or ""`,
  so a deployment that supplies an app-side subclass field still surfaces it.

The description-field move is now part of this change rather than a
follow-up: the app-side subclass field in `rag_pkg/klea_rag/config.py` stops
shadowing the shared field and relies on it directly, so there is one
definition and one consumer per path (section 10).

## 4. Locked tool contract: `search_stores`

Module layout, mirroring the bundled-tool pattern (superseded by the file
list in section 10: there is no separate core module, see section 5.1):

- `utils_pkg/klea_utils/mcp/tool_impls/stores.py` -- the `search_stores`
  wrapper `async def search_stores(ctx: Context, ...) -> ToolResult`
  returning `to_result(result)`, built on the existing retrieval layer.
- Registered in `mcp/server/bundled_tools.py`.

Pattern references: `bundled_tools.py` (annotated signature, `to_result`) and
`tool_impls/web_search.py` (lifespan context lookup).

### Parameters

All parameters are `Annotated[..., Field(...)]` as in `bundled_tools.py`.

| Parameter | Type / default | Meaning |
|---|---|---|
| `query` | `str`, required, `min_length=1` | Free-text query. |
| `domain` | `str = ""` | Empty means every configured domain.  Validated against `RetrieverConfig.domains`. |
| `mode` | `Literal["vector", "bm25", "hybrid"] = "hybrid"` | Hybrid fuses whichever of the two retrievers the domain configures; it degrades to the single configured one with a `note`. |
| `top_k` | `int = 5`, `ge=1, le=50` | Per-store depth handed to `retrieve()`, still bounded by per-store `k_max`. |
| `rerank` | `bool = False` | Cross-encoder re-rank; off by default because the weights download on first use. |
| `metadata_filter` | `dict[str, Any] | None = None` | Filter DSL, validated/restricted per domain before retrieval. |
| `max_chars` | `int = 8000`, `ge=500, le=100000` | Global character budget for the serialized results. |

No path, URI, scheme or collection name is ever a parameter: those stay in
`StoreInfo.path` / `StoreInfo.name`.  `rerank_by_recency` is deliberately not
exposed; recency ordering is a path-level policy, not a model choice.

### Return payload

Returned as a dict, wrapped by `to_result`:

- `query: str`
- `domain: str` (as requested; `""` when all domains)
- `domains_searched: list[str]`
- `mode: str`
- `results: list[{rank: int, domain: str, store: str, score: float,
  content: str, metadata: dict}]`
- `truncated: bool`
- `error: str`
- `note: str`

### Annotations

`ToolInfo(tags={BUNDLED_TAG, "local", "stores"}, read_only=True,
idempotent=True, open_world=False)`.

- `read_only=True` is mandatory: `tool_permits` is fail-closed in read-only
  mode, and an unannotated tool is not permitted (`mcp/access.py`).
- `open_world=False`: it reads only the configured local backends.
- `destructive` is left unset.
- No `checkpaths`: no argument is a filesystem path, so the client-side gate
  does not apply.

### Error mapping

The retrieval engines are fail-soft: VS logs and returns `[]`, BM25 skips a
missing corpus and drops zero-score documents, and `retrieve` returns the
union of whatever loaded.  The tool must not be fail-soft: every one of the
following produces exactly one synthetic, non-halting ADR-0007 error result
(never a raised exception, never a silently empty success).

| Condition | `error` |
|---|---|
| Unknown domain | `Unknown domain '<d>'. Available domains: <list>` |
| Domain has no store of the requested mode | `Domain '<d>' has no <mode> stores configured; try mode='hybrid' or 'vector'` (from the store lists, never inferred from an empty result) |
| Stores configured but none loaded (missing/unreadable corpus, backend unreachable) | `No <mode> store in domain '<d>' could be loaded: <paths>` |
| Stores queried, zero documents | `No matching documents in domain(s) <ds> for query '<q>'` |
| Retrieval or filter raised | `<ExcType>: <msg>`, with domain and mode in `note` |
| Invalid metadata filter | `Invalid metadata filter: ...`, before any retrieval |

Non-errors: partial coverage sets `error=""` and a `note` naming the domains
that returned nothing; character truncation sets `truncated=True` with a
`note` and `error=""`.

### Store configuration source (resolved; see section 9)

The impl resolves `RetrieverConfig` per call: the per-session `_meta` value
first, then the `KLEA_STORES_CONFIG_FILE` deployment default exported by
`agent_pkg`, and otherwise it fails closed (section 9).  The reserved FastMCP
lifespan stores key is not implemented.  The path must never become a tool
parameter.  Tests point the loader at a temporary JSON in the style of
`utils_pkg/tests/test_stores_retrieval.py` (`STORES_TEST_CONFIG`).

## 5. Shared core and reused helpers

### 5.1 Reuse verdict (no new core module)

The earlier sketch put the framework-agnostic logic in a new
`mcp/tool_impls/stores/core.py`.  Reading the existing retrieval layer shows
that everything that module would have contained already exists:

- the retriever contract itself (`setup`, `load`/`load_all_stores`,
  `retrieve`, `inc_k`, `can_inc_k`) on `BaseKleaRetriever`;
- fusion (`rrf_merge`), re-rank (`cross_encoder_rerank`, `load_cross_encoder`),
  budgeting and serialization (`truncate_reference_material`,
  `serialize_reference_material`);
- the filter DSL (`validate_metadata_filter`, `restrict_metadata_filter`,
  `translate_metadata_filter`, `filter_docs_by_metadata`);
- per-store depth policy (`default_k`, `k_max`, `k_inc`, `inc_k`,
  `can_inc_k`).

A second copy would duplicate that policy and drift from the scientific path.
Verdict: reuse the existing utils retrieval layer; do not add
`tool_impls/stores/core.py`.  The tool is a thin wrapper over that layer, and
only the additions listed in section 7 are new code.

### 5.2 Reused helpers

The wrapper calls the existing retriever contract rather than reimplementing
it:

- `VSRetriever` (`retrieval/vs.py`) and `BM25RetrieverManager`
  (`retrieval/bm25.py`), both from `BaseKleaRetriever`
  (`retrieval/base.py`): `setup`, `load`/`load_all_stores`,
  `retrieve(domain, query, metadata_filter, k_values)`, `inc_k`,
  `can_inc_k`.
- Fusion: `rrf_merge` (`stores/utils.py`).
- Re-rank: `cross_encoder_rerank` and `load_cross_encoder` (`stores/utils.py`),
  used only when `rerank` is set.
- Budgeting and serialization: `truncate_reference_material` and
  `serialize_reference_material` (`stores/utils.py`).
- Filter DSL: `validate_metadata_filter`, `restrict_metadata_filter`,
  `translate_metadata_filter`, `filter_docs_by_metadata` (`stores/filters.py`).
  Vector backends filter natively; BM25 post-filters with a
  `BM25_FILTER_MARGIN` over-fetch, which the core must tolerate.

Because the general tool and the scientific node call the same core, the
difference between them is policy (optional vs mandatory, failure handling),
not retrieval semantics.

## 6. Scientific-mode prerequisites and open questions

The scientific path needs the same domain retrieval, but as a mandatory node.
Open items before implementing it:

1. **`source_available` atomicity.**  The flag that records whether evidence
   was retrieved must be set by the retrieval step itself and must not be
   settable by an earlier or later node; otherwise a later node can claim
   evidence that was never retrieved.  Define it as a single state field
   written only here.
2. **Node placement.**  Decide where the node sits relative to the guard and
   the query/domain classifier (ADR-0010, ADR-0011): before query generation
   (needs its own expansion) or after (needs the generated query).  Placement
   determines whether an extra LLM call is needed.
3. **Domain choice in scientific mode.**  Mandatory retrieval still needs a
   domain.  The classifier already produces the domain list; confirm the node
   consumes it rather than asking the user, and what it does when the
   classifier returns nothing.
4. **Failure UX.**  A mandatory retrieval that returns nothing must become a
   user-facing non-answer rather than a confident answer (ADR-0009).  Decide
   whether to retry with `inc_k` while `can_inc_k` is true before failing, and
   which error text the user sees when it does.
5. **Cross-encoder cost.**  If the node enables re-ranking by default, account
   for the first-use weight download in latency and in tests.
6. **Config plumbing.**  The node and the tool must resolve configuration the
   same way, so both need the same lazy `RetrieverConfig` source described in
   section 4.

## 7. What must be added instead of a new core module

With the reuse verdict of section 5.1, the only new code is:

1. **Domain catalogue and validation.**  Enumerate `RetrieverConfig.domains`,
   render each key with its optional `description` (or the bare key when it is
   unset) and reject an unknown `domain` with the error text of section 4.
   Nothing else is inferred about a domain.
2. **Mode selection per domain.**  `mode` is resolved against the store lists
   of the chosen domain (`vector` stores vs BM25 stores in `PerDomainConfig`),
   never against an empty result set, so "no stores of that mode configured"
   and "no matching documents" stay distinguishable.
3. **Strict error mapping.**  The engines are fail-soft; the wrapper converts
   each condition tabled in section 4 into one synthetic error result, never a
   raised exception and never a silently empty success.
4. **Global character budget.**  `max_chars` applies to the merged result list,
   so truncation happens after fusion and re-rank and sets `truncated=True`.
5. **Configuration resolution and reload glue** (sections 9 and 8).

No retrieval, fusion, re-rank, filter or serialization logic is written here:
all of it is imported from `stores/retrieval/` and `stores/utils.py`.

## 8. Store reload semantics

The backends do not share one freshness model, which the tool must respect:

- **Vector stores** query the persisted index through the backend client, so a
  re-indexed collection is visible on the next query without any action by the
  tool.  Nothing to reload on this path.
- **BM25** reads a corpus pickle when the retriever is loaded
  (`load`/`load_all_stores`) and then memoises it on the retriever instance.
  A long-lived instance keeps serving the corpus it loaded, and a rebuilt or
  missing corpus is not picked up until the retriever is set up and loaded
  again.
- **Domain configuration** is read lazily per domain and cached by the
  retriever, so a changed stores JSON is equally invisible until the retriever
  is rebuilt.

Recorded limitation: between an operator reindex and the next call a
retriever is rebuilt from the configured corpus path, so a stale handle cannot
outlive one call; what can still be stale is the *content* of the config file,
which the loader re-checks (see below).  The wrapper cannot detect a corpus
that changed underneath a valid path, and the `note` field must not claim
otherwise.  The mechanism the tool uses:

- build retrievers **per call** from the resolved configuration (section 9),
  keyed by domain and backend, never one process-wide singleton, so no
  long-lived instance memoises a corpus;
- cache only the parsed configuration, keyed by (resolved path, mtime, size),
  and rebuild when any of those changes, so an edited stores JSON is picked up
  on the next call without a backend restart;
- trigger the rebuild explicitly, never on a timer: at the start of each call
  via the cache key, and on an operator action such as reindexing.  No
  retriever exposes a freshness check today, so mtime-based invalidation of the
  *corpus* is not assumed, only of the config file itself.

## 9. Store configuration scope and resolution order

Decision: the stores configuration is **layered**, not host-wide only.  A
host-wide deployment configuration is the default floor, and a loopback client
may override it per session through the out-of-band MCP request metadata
(`_meta`) that rides every tool call.  This supersedes both the earlier
"host-wide, resolved once per process" verdict and the client-environment
transport tried after it, which has been reverted.

### Per-session store source: MCP request metadata (`_meta`)

The per-session store source travels in the MCP request metadata, never in a
client environment variable and never as a tool parameter.

Injection point (client): the client passes `meta=...` to `call_tool`, and
fastmcp forwards it untouched to `ClientSession.call_tool(..., meta=...)`, so
the value is scoped to one request rather than to the subprocess environment.

Access point (server): the tool reads the plain dict back as
`ctx.request_context.meta` and may also key on `ctx.session_id`.  Because the
bundled server is spawned per TUI session as a stdio subprocess,
`ctx.session_id` already identifies exactly one client TUI, so a session-keyed
store pool would be redundant: one process holds one session, and the `_meta`
payload alone is enough to select the stores file.  A host-level default
remains the floor whenever no metadata is sent.

Validation: the value is resolved to an **absolute path** against the
launching client's working directory, must exist and be a regular file that
parses as a stores JSON (a domains mapping).  A configured-but-invalid value
raises `ValueError` naming both the metadata key and the offending path, and
there is no fallback to the deployment default: a present key either resolves
or fails closed.

Trust caveat: `_meta` is client-supplied metadata and therefore untrusted
input.  The server validates the path, and the value must never influence
permissions or `read_only` annotations -- those stay fail-closed in
`access.py`.  `_meta` never appears in the tool schema or listing, so it
cannot be advertised in a description; it uses the documented, versioned key
named in the resolution order, and its absence falls through to the
deployment default.

### Reverted: the `--stores-config` flag and its environment forwarding

An earlier iteration added an agent-TUI flag `--stores-config PATH` to the
shared `tui` command factory in `utils_pkg/klea_utils/ui/cli.py`, with an
agent-side gate in `agent_pkg/klea_agent/ui/cli.py` keeping it out of
`klea-rag tui --help`, and forwarded the resolved value to the client
environment.  That is gone: the option is not defined, the forwarded client
environment carries no stores entry, and both `klea tui --help` and
`klea-rag tui --help` advertise no such flag.  Passing a session setting
through an environment variable was the hacky part;
the per-session override now rides `_meta` as described above, and the host
file remains the validated default.

The `_meta` transport is implemented in this tree (section 11.4): the tool
reads the versioned key from `ctx.request_context.meta` as described in the
resolution order below.

### Resolution order (highest precedence first)

Resolved per call by the tool's configuration loader, first hit wins:

1. **FastMCP lifespan context key** for stores (operator/deployment pin).  Not
   implemented: the key stays reserved and unset, since
   `make_http_session_lifespan` yields only `{"http_session": ...}`, so this
   is an additive hook that no code path reads yet.
2. **Per-session metadata value**: `ctx.request_context.meta` carrying the
   shipped key `klea/stores-config-path/v1`, an absolute path to the stores
   JSON supplied by the client (scoped by the bundled process's single
   `ctx.session_id`).  No client environment variable and no tool parameter
   carries a stores path.
3. **Deployment default**: `KLEA_STORES_CONFIG_FILE`, exported by the agent
   package when it spawns the bundled server; the host default is therefore
   agent-only (see below).
4. **Fail closed**: with nothing configured the tool returns one error result
   naming that no stores are configured -- never an empty success.

The host-only resolver in `utils_pkg/klea_utils/stores/utils.py` is unchanged:
it reads the deployment-configured stores path, with its (path, mtime, size)
cache key.  The metadata-carried per-session source of item 2 is layered above
it inside the tool, so the resolver's behaviour is untouched by the revert.

The deployment default is agent-only: `agent_pkg` exports
`KLEA_STORES_CONFIG_FILE` when it spawns the bundled server, so the host floor
lives on the agent path.  `rag_pkg` is unchanged -- it keeps its retrieval node
and its inline `domains` configuration, and adds no per-session stores override
and no `_meta` handling.

A stores JSON path is therefore configuration, never a tool parameter (section
4).  Store edits are pick-up-on-next-call: point the deployment (and each
client) at a stable separate stores JSON and edit that file.  The agent's app
config JSON is still read once at start-up, so it should hold only the path,
never the per-project store definitions; two research projects are two clients
attaching different stores files per session through `_meta` against one
backend, each chat getting its own bundled subprocess.  No backend restart is
needed after editing the stores file.

### Reload

Retrievers are built per call from the resolved configuration (section 8), so
no long-lived handle memoises a corpus; only the parsed configuration is
cached, keyed by (path, mtime, size).  A different config path changes the
client-context fingerprint, so the session pool creates a fresh pooled client
and subprocess for that chat.  No timer and no tool parameter are involved.

### Shared resolver with the scientific path

The tool and the scientific-mode node must call one resolver, so the two paths
cannot diverge: the node reads the same value from
`KleaRunContext.client_context` and the same host fallback.

### Per-chat store attachment (deferred)

Attaching a stores configuration to an individual chat is **deferred**.  The
mechanism already exists in outline -- the session pool is keyed by the
user+chat thread, and per-chat state has the `chat_sessions` JSON-blob
precedent used by model overrides -- but it needs new API endpoints, a UI
selector, and resolution before the MCP client is created.  The per-session
environment already covers the motivating case (two projects, two clients),
and the locked domain-based tool contract does not change when per-chat
attachment lands later.

### Recorded risks

- Loopback-only: remote and multi-user deployments keep the host-wide
  configuration by design.
- A client-supplied path is client authority over what the server may read,
  equivalent to the cwd/interpreter it already supplies.  Acceptable under the
  current trusted-loopback model, but the `note` field must state it.
- Domain catalogues may now differ per session by intent, so the tool
  description must render the **resolved** config's domains, not a cached
  global list.  For a meta-only session the description cannot be
  session-aware: `_meta` rides tool calls only, never the listing, so the
  catalogue shows the deployment default's domains (or none) and the
  per-session catalogue is rendered only when the tool actually runs.

## 10. Revised implementation file list and the description-field move

Files (replacing the `stores/core.py` + `stores.py` split of section 4):

- `utils_pkg/klea_utils/stores/config.py` -- add the optional
  `description: str = ""` to the shared `PerDomainConfig` (section 3).
- `utils_pkg/klea_utils/mcp/tool_impls/stores.py` -- the whole tool: the
  FastMCP wrapper, catalogue rendering, mode selection, strict error mapping,
  the global character budget and the per-call retriever construction, all on
  top of the existing retrieval layer (section 7).  No
  `tool_impls/stores/core.py`.
- `utils_pkg/klea_utils/mcp/server/bundled_tools.py` -- registration with the
  annotations of section 4.
- `utils_pkg/klea_utils/stores/utils.py` -- the host-only configuration
  resolver and the (path, mtime, size) config cache.  It reads the app config
  path, and is unchanged by the revert; the metadata-carried per-session
  source of section 9 is layered above it inside the tool.  It belongs next to
  the existing loaders, not in a new module.
- no client CLI change (reverted): an earlier iteration added a
  `--stores-config PATH` option to the shared `tui` command factory in
  `utils_pkg/klea_utils/ui/cli.py`, with an agent-side gate in
  `agent_pkg/klea_agent/ui/cli.py`, and forwarded the resolved path to the
  client environment.  The option and the environment forwarding were reverted
  (section 9), so neither file is part of this change; the per-session override
  rides `_meta` and the host file stays the validated default.
- `utils_pkg/klea_utils/mcp/lifespan.py` -- only when the reserved stores key
  is actually populated; today the hook is additive and no change is needed.
- `rag_pkg/klea_rag/config.py` -- the description-field move: drop the
  app-side subclass field so `PerDomainConfig.description` is the single
  definition, and keep consumers reading it defensively,
  `getattr(cfg, "description", "") or ""`.
- `rag_pkg/` -- unchanged by this cut: it keeps its retrieval node and its
  inline `domains` configuration, and gains no per-session stores override and
  no `_meta` handling (section 9).
- `utils_pkg/tests/` -- new tool tests beside `test_stores_retrieval.py`,
  reusing its temporary-config style, and covering the resolution order of
  section 9 (the `_meta` per-session value beats the host file; nothing
  configured fails closed).

Description-field move, in one line: `description` stops being an app-side RAG
subclass concern and becomes a shared `PerDomainConfig` field consumed by both
the RAG query-domain classifier and the `search_stores` catalogue, so the two
cannot diverge.

Still open: the section 6 items (node placement, domain choice for the
scientific path, failure UX, cross-encoder cost) and the reload trigger for
long-lived deployments (section 8).

## 11. Comparison: in-process retrieval node vs shipped MCP tool (decided)

Status of this section: **decided**.  It records the comparison that led to
the architecture chosen in section 11.4; the decision does not change the
locked contract of sections 4-5.

### 11.1 Two delivery shapes

- **MCP tool** (the shipped shape, sections 4-10): `search_stores` runs in the
  bundled MCP subprocess, is disclosed to the planner as a description, and is
  invoked only when the picker selects it.
- **In-process node**: a graph node inside the agent process that holds a
  `RetrieverConfig` in graph state and calls the same retrieval functions
  directly, with no subprocess, no transport and no planner step.

Both call the shared retrieval layer of section 5; only the delivery differs.

### 11.2 Comparison

| Concern | MCP tool | In-process node |
|---|---|---|
| Config delivery | carried per request in the MCP `_meta` request metadata, read server-side as `ctx.request_context.meta` with the host file as the default floor (section 9); no client environment variable | written once into graph state by the setup/build step and read directly, no transport |
| Planner step typing cost | one plan step, one prompt injection and one structured emission per call (ADR-0034, ADR-0041); the planner must decide to call it | no planner step; the node is an edge, so it costs no tokens and cannot be skipped |
| Discretion vs structural guarantee | discretionary: the model may choose not to call it and access levels may hide it (ADR-0037) | structural: an unconditional edge always runs, its result is in state and a failure can be routed (ADR-0036) |
| Testability | exercised through the MCP boundary; needs a tool test plus a transport/lifespan stub | a plain function of state in, state out; unit-testable without a server |
| Checkpoint safety | stateless per call; nothing to checkpoint, but nothing is recorded in the graph either | the retrieved evidence is a state field, so it is checkpointed with the run and survives resume |
| Scientific-mode reuse | usable, but cannot enforce mandatory retrieval (section 1) | the same call satisfies the mandatory-retrieval requirement (ADR-0036) |

### 11.3 Recognised patterns and how each mode maps onto them

- **Tool-mediated retrieval** (ADR-0034 tool calling, ADR-0037 access levels):
  the model is offered a capability and picks it.  *General mode* is exactly
  this: optional, discretionary, prompt-visible, access-filtered.
- **Deterministic graph step** (unconditional edge, `system/graph-routing.md`,
  ADR-0036 grounding enforcement): the capability is a node that always runs
  and writes evidence into state.  *RAG/scientific mode* is exactly this.
- **State-held configuration** (the `chat_sessions` JSON-blob precedent used by
  model overrides): configuration is carried in state rather than forwarded
  through a process environment.
- The *proposal* maps general mode onto tool-mediated retrieval (unchanged) and
  scientific mode onto the deterministic node with a state-held config,
  replacing the environment-forwarding hop of section 9 for the scientific
  path only.

### 11.4 Decision (approved)

**Decided.**  The approved architecture is the hybrid above: keep the shipped
MCP tool for discretionary general-mode retrieval and add an in-process node
for mandatory scientific-mode retrieval, sharing one resolver.

- **General mode: the MCP tool** `search_stores` stays the discretionary,
  prompt-visible, access-filtered retrieval surface; its contract in sections
  4-10 stands unchanged.
- **Scientific / RAG mode: an in-process graph node** that holds a
  `RetrieverConfig` in graph state and calls the same shared retrieval layer
  directly, on an unconditional edge, so mandatory retrieval is a structural
  guarantee rather than a hopeful tool call.  Implementation remains gated on
  the section 6 items.
- **Configuration delivery: one resolver, two carriers.**  The host stores file
  is the validated default; the per-session override rides the MCP request
  metadata (`_meta`) for the tool (section 9) and the state-held
  `RetrieverConfig` for the node.  No client environment variable carries a
  stores path.

Files this implies:

- `utils_pkg/klea_utils/stores/config.py` -- add the optional
  `description: str = ""` to the shared `PerDomainConfig`.
- `utils_pkg/klea_utils/mcp/tool_impls/stores.py` -- the `search_stores` tool
  plus the `_meta` per-session override, layered above the host resolver.
- `utils_pkg/klea_utils/mcp/server/bundled_tools.py` -- registration with the
  section 4 annotations.
- `utils_pkg/klea_utils/stores/utils.py` -- the host-only resolver and the
  (path, mtime, size) cache; unchanged by the revert.
- `rag_pkg/klea_rag/config.py` -- the description-field move onto the shared
  schema.
- the scientific-mode in-process node -- a new graph node in the agent node set
  (beside `utils_pkg/klea_utils/nodes/tools_caller.py`) that reads
  `RetrieverConfig` from state and calls the shared retrieval layer.
- `utils_pkg/tests/` -- tool, resolver and `_meta` resolution-order tests.

State fields:

- `RetrieverConfig` carried in the graph state, written once by the setup/build
  step, so the node never forwards configuration through a process
  environment.
- the retrieved evidence as a single state field, so it is checkpointed with
  the run and survives resume.

Client-side injection point: the tool call passes `meta=...` to `call_tool`,
which fastmcp forwards untouched to `ClientSession.call_tool(..., meta=...)`;
the server reads it back as `ctx.request_context.meta` and may also key on
`ctx.session_id` (section 9).

Pool and lifespan: no session-keyed store pool is built -- the bundled server is
already one process per TUI session, so `_meta` plus `ctx.session_id` suffices.
`SessionMCPClients` is unchanged.  The reserved FastMCP lifespan stores key
stays additive and unset, so no `lifespan.py` change is needed for this cut.
