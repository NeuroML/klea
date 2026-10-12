# External source pools: keyless-first providers with sequential fallback

## Scope

Several Klea subsystems query a set of external services that share the same
problem shape: no single free API is complete or unthrottled, so a small pool
is queried in order and the first usable answer wins.  This note records the
common architecture and the per-domain adaptations, so the shared pattern has
one home and the individual decisions stay in their ADRs.

| Subsystem | Code | ADR |
|-----------|------|-----|
| Bibliographic DOI resolution | `klea_utils/biblio/doi.py` (`DoiResolver`) | ADR-0027 |
| Web search | `klea_utils/mcp/tool_impls/search/resolver.py` (`WebSearchResolver`) | ADR-0048 |
| Academic paper search | `klea_utils/mcp/tool_impls/papers/search.py` (`PaperSearchResolver`) | ADR-0049 |

## The common pattern

- **Keyless-first.** Free, no-account services are the default.  A keyed
  service joins the pool only when its API key is set, and a key only *raises
  limits* -- it is never a prerequisite for the tool to work.
- **Sequential fallback.** Sources are queried in a defined order and the
  first usable answer wins; the next source is tried when the current one is
  rate-limited (HTTP 429), errors, returns nothing, or (paper search) returns
  results without the wanted field (an abstract).  There is no per-source
  retry/backoff: moving on to the next source is faster than backing off on
  the same one.
- **Honest identity.** Every request carries a per-tool `User-Agent`
  (`klea-ingest/`, `klea-web-search/`, `klea-paper-search/`) built by
  `klea_utils.mcp.tool_impls.user_agent.klea_user_agent`, so a server operator
  can attribute load and identify the caller.
- **Fixed hosts.** The request URLs are code constants; the model cannot pick
  them, so no SSRF guard is needed inside the pool.  Fetching a model-chosen
  URL remains `web_fetch`/`download_file`'s job, with its own guard
  (`system/mcp-permissions.md`).
- **Framework-agnostic.** Implementations depend on a minimal session
  interface (`SessionLike`) rather than a concrete client, so they are
  testable with fakes and reusable outside MCP.
- **Expected failure is data, not an exception.** A tool impl returns a dict
  with a non-empty `error` (and, for paper search, a `note`) that
  `klea_utils.mcp.tool_result.to_result` maps to MCP `isError`.

## Per-domain adaptations

| Aspect | DOI (ADR-0027) | Web search (ADR-0048) | Paper search (ADR-0049) |
|--------|----------------|-----------------------|-------------------------|
| Ordering | round-robin across calls | fixed priority | general sources round-robin across calls; life-sciences pins Europe PMC/PubMed first |
| Cache | on-disk (`doi-cache.json`) | none (results are time-sensitive) | none |
| HTTP client | own short-lived synchronous client inside ingestion workers | app lifespan session + shared retryer | app lifespan session |
| Transport | REST GET | hosted-MCP JSON-RPC + keyed REST | REST GET + XML (NCBI E-utilities, arXiv) |
| Domain rules | tiered cascade with pdf/docling/regex | provider identity in results | peer-reviewed-first, preprint gating, reuses the DOI-service normalisers |

## Why not one shared implementation

The three share the *pattern* but not the *decisions*.  Rotation exists for
bulk (DOI) or search variety (paper search) but not for interactive web
search; a disk cache is correct for immutable DOIs and wrong for
time-sensitive web results; the DOI resolver deliberately runs its own
synchronous client in ingestion workers while the two tools reuse the app
lifespan session (ADR-0005); and the transports differ (plain REST vs hosted
MCP).  Folding them into one component would force per-caller flags through a
common interface and hide the domain rationale.  Keeping one ADR per decision
and this note for the shared shape is the lower-maintenance split.

## When to reuse this pattern

Add a new pool (and, if it makes a non-obvious decision, a new ADR) when a
tool needs data from several interchangeable external APIs and no single one
is reliable or complete.  Reuse the pieces that already exist:

- the session protocol (`klea_utils.mcp.tool_impls.session.SessionLike`);
- the honest `User-Agent` helper
  (`klea_utils.mcp.tool_impls.user_agent.klea_user_agent`);
- the bibliographic normalisers (`klea_utils.biblio.normalize`) for
  Crossref/OpenAlex/Semantic-Scholar response shapes;
- the return-dict contract (`error`, optional `note`) consumed by
  `klea_utils.mcp.tool_result.to_result`.

## References

- ADR-0027 -- DOI/bibliographic resolver: round-robin tiered cascade with
  disk cache.
- ADR-0048 -- Web search tool: keyless-first provider pool with sequential
  fallback.
- ADR-0049 -- Paper search tool: peer-reviewed-first scholarly source pool
  with preprint gating.
- ADR-0005 -- Single HTTP stack on httpx with shared retry and lifespan
  session.
- `system/mcp-permissions.md` -- the SSRF/network boundary for
  model-chosen URLs.
