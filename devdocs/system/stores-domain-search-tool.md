# Stores: domain search tool for the general agent (draft reference note)

Status: **draft reference note**.  Research notes and a locked but not yet
implemented tool contract.  This is internal material: do not link it from
`docs/`, and update the public documentation and `CHANGELOG.md` only when the
tool is implemented (see `devdocs/README.md` and `AGENTS.md` for the
ASCII-only and one-file-per-topic conventions).

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
  MCP metadata.  The tool is therefore named `search_domains` (not
  `search_stores`/`store`) so the two meanings stay separable in prompts.

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

Follow-up (not part of this change): the RAG subclass field in
`rag_pkg/klea_rag/config.py` should stop shadowing the shared field and rely on
it directly.

## 4. Locked tool contract: `search_domains`

Module layout, mirroring the bundled-tool pattern:

- `utils_pkg/klea_utils/mcp/tool_impls/stores/core.py` -- framework-agnostic
  `async def search_domains(*, config, query, ...) -> dict[str, Any]`.
- `utils_pkg/klea_utils/mcp/tool_impls/stores.py` -- thin FastMCP wrapper
  `async def search_domains(ctx: Context, ...) -> ToolResult` returning
  `to_result(result)`, registered in `mcp/server/bundled_tools.py`.

Pattern references: `bundled_tools.py` (annotated signature, `to_result`) and
`tool_impls/web_search.py` (impl/wrapper split, lifespan session lookup).

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

### Store configuration source (open point)

There is no stores key in the FastMCP lifespan context.  The impl must
resolve `RetrieverConfig` lazily: a deployment-configured stores JSON path from
an environment variable, cached per process, with a lifespan-context override
only if one is added later.  The path must never become a tool parameter.
Tests point it at a temporary JSON in the style of
`utils_pkg/tests/test_stores_retrieval.py` (`STORES_TEST_CONFIG`).

## 5. Shared core and reused helpers

The core calls the existing retriever contract rather than reimplementing it:

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
