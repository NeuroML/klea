---
status: "accepted"
date: 2026-10-10
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Web search tool: keyless-first provider pool with sequential fallback

## Context and Problem Statement

The agent general path (ADR-0035) lists web search as an optional tool for
discovery, but no web-search tool existed: the bundled server only exposed
`web_fetch`, which reads a URL once its caller already knows it.  The stale
"web search" claims in the docs (`Readme.md`, `docs/index.rst`,
`mcp_pkg/AGENTS.md`) and the deferred item in `.agents/2026-08-16.md` needed
to be made real.

Every general web-search API with a usable index is either keyed (Tavily
REST, Exa REST, Brave, Serper, Perplexity) or requires self-hosted
infrastructure (SearXNG).  Requiring a sign-up or a running service would
make a bundled, batteries-included tool unusable out of the box.  But Exa,
Parallel, Tavily, and Firecrawl each run a **public hosted MCP endpoint** that
answers keyless (rate-limited), exactly the surface opencode's `websearch`
tool uses (verified: `https://mcp.exa.ai/mcp`, `https://search.parallel.ai/mcp`,
`https://mcp.tavily.com/mcp/` with header `X-Tavily-Access-Mode: keyless`,
`https://mcp.firecrawl.dev/v2/mcp`).

Which providers should the tool use, how should it call them, how should it
degrade when one is rate-limited or down, and how should an operator add a key?

## Decision Drivers

* Zero-setup default: a bundled tool must work with no account, key, or
  service running.
* Resilience: any single provider can 429 or fail; one weak provider must not
  fail the search.  This mirrors the DOI resolver (ADR-0027).
* Coverage/bias: providers have different indexes and result styles; a pool
  is more robust than any one.
* Keys must be optional and must only *raise limits*, never be a prerequisite.
* Framework-agnostic tool implementations: shared impls take the app's
  lifespan `httpx` session and must not couple to a specific MCP client.
* Reuse the shared HTTP stack (ADR-0005): one session, shared retry, honest
  User-Agent; no per-module connection pools.
* Keep the model-facing contract stable and consistent across providers.
* Respect provider etiquette: identify the client; do not send internal
  addresses (fixed provider hosts only).

## Considered Options

* **A. Single keyed provider (e.g. Tavily REST only).**
* **B. Keyless-first provider pool over hosted MCP, plus keyed opt-in, with
  sequential fallback (`WebSearchResolver`).**
* **C. Self-hosted SearXNG.**  Needs an operator-run instance.
* **D. Keyless library scraping (`ddgs`/DuckDuckGo).**  Unofficial; adds
  `primp`/`lxml`; rate-limit/ToS risk.
* **E. `fastmcp.Client` for the MCP calls** instead of a thin JSON-RPC POST.
* **F. Parallel fan-out** to all providers and merge.

## Decision Outcome

Chosen option: "**B. Keyless-first provider pool with sequential fallback**",
because it is the only option that needs no user setup while remaining
resilient, and it matches how opencode and the scholarly DOI resolver already
behave.

* A `web_search` bundled tool (`utils_pkg/klea_utils/mcp/tool_impls/web_search.py`,
  wrapped in `klea_utils/mcp/server/bundled_tools.py`) is tagged
  `{bundled, web, search}`, annotated read-only + open-world (ADR-0037), and
  returns `{query, provider, results, error}` with each result normalised to
  `{title, url, snippet, published, score}` (`search/base.py`).
* The pool (`search/resolver.py` `WebSearchResolver`) is queried in priority
  order: keyless hosted-MCP providers `tavily -> exa -> parallel -> firecrawl`
  (`SERVICE_ORDER`), then any keyed provider in `KEYED_SERVICE_ORDER`
  (`brave`, `serper`) whose API key is present.  The first non-empty result
  set wins; a provider that raises or returns nothing is skipped.
* Each provider is one module (`tavily.py`, `exa.py`, `parallel.py`,
  `firecrawl.py`, `brave.py`, `serper.py`) with a shared hosted-MCP base
  (`hosted.py`) and transport (`transport.py`: `_mcp_call` for the hosted MCP
  endpoints, `_rest_get_json`/`_rest_post_json` for the keyed REST ones).
  Keyless vs keyed is chosen per provider from its env var
  (`TAVILY_API_KEY`, `EXA_API_KEY`, `PARALLEL_API_KEY`, `FIRECRAWL_API_KEY`,
  `BRAVE_API_KEY`, `SERPER_API_KEY`); Brave and Serper are keyed-only and so
  join the pool only when their key exists.
* Calls carry an honest `User-Agent: klea-web-search/<version>` and use the
  shared `_make_retryer_httpx` and the app's lifespan session.  Hosts are
  fixed constants; result URLs are returned as data and never fetched here, so
  `web_fetch`/`download_file` remain the SSRF boundary
  (`system/mcp-permissions.md`).
* Provider identity is kept in the result: it is cheap metadata that can
  support provenance-style phrasing, and it is already logged server-side.
* No disk cache: unlike DOIs (immutable, ADR-0027), web results are
  time-sensitive, so caching would serve stale hits.  Round-robin is likewise
  not used; priority order plus fallback is enough at query time.

### Consequences

* Good, because the tool works with no account, key, or infrastructure.
* Good, because a 429/down provider degrades to the next, matching ADR-0027.
* Good, because keys are a pure, optional upgrade; adding one needs no code
  change.
* Good, because provider modules are small and independent, so a provider
  changing its terms/endpoint is a one-module fix.
* Bad, because the four keyless endpoints are undocumented, anonymous,
  rate-limited services that can change without notice; the pool and optional
  keys are the mitigation.
* Bad, because the pool builds provider instances per search and reads the
  environment each call (cheap; accepts live env changes).
* Bad, because results are returned in the winning provider's order only; the
  pool does not merge or re-rank across providers (accepted: sequential
  fallback is by design).
* Bad, because keys are read from the **process** environment only: the
  bundled server is a stdio subprocess, so a key placed only in the env file
  (`klea_agent.env`) is not inherited.  Keys must be exported.  A JSON
  `general.web_search` config with subprocess env forwarding is deferred
  (`devdocs/backlog.md`).

### Confirmation

* `utils_pkg/tests/test_web_search.py` covers the transport (JSON + SSE parse,
  retries, honest UA, error paths), each provider's parse and keyless/keyed
  header selection, the resolver's order/fallback/all-fail/empty-query cases,
  and the tool impl.
* `utils_pkg/tests/test_bundled_server.py` asserts the tool's name, tags
  (`bundled`/`web`/`search`), read-only + open-world annotations, and the
  `Context` contract.
* Endpoints were verified live during design (keyless Exa/Parallel/Tavily/
  Firecrawl all returned results); the transport is unit-tested against those
  shapes.
* `ruff` and `ty` are clean for the package; the bundled server starts
  (`python -m klea_utils.mcp.server.bundled --help`).

## Pros and Cons of the Options

### A. Single keyed provider

* Good, because one integration to write.
* Bad, because it needs a sign-up/key, so it is not a batteries-included
  bundled tool; and one provider's rate limit/cverage is a hard ceiling.

### B. Keyless-first pool with sequential fallback (chosen)

* Good, because zero setup and resilient to a single provider failing.
* Good, because keys upgrade limits without changing the contract.
* Bad, because it relies on anonymous, undocumented hosted endpoints.

### C. Self-hosted SearXNG

* Good, because it is free, private, and aggregates many engines.
* Bad, because it requires the user to run and maintain a service, which a
  bundled tool cannot assume.

### D. Keyless `ddgs` scraping

* Good, because it is a pure library with no key and no service.
* Bad, because it is unofficial HTML scraping (parser breakage, IP bans),
  a ToS grey area, and adds `primp`/`lxml` dependencies.

### E. `fastmcp.Client`

* Good, because it handles the MCP handshake/session/SSE and tracks the spec.
* Bad, because the keyless endpoints accept a direct `tools/call`, so a
  client mainly adds an `initialize` round-trip per search; it also couples a
  reusable layer to fastmcp, owns a second connection pool instead of reusing
  the lifespan session, and still needs retry/backoff and the honest UA
  supplied separately.  Rationale is recorded in `search/transport.py`.

### F. Parallel fan-out

* Good, because it could merge the best of several providers per query.
* Bad, because it multiplies rate-limit pressure per query for little gain
  over sequential fallback, and needs a field-level merge policy
  (cf. ADR-0027 option B).

## More Information

* Code: `utils_pkg/klea_utils/mcp/tool_impls/web_search.py` (tool impl),
  `klea_utils/mcp/tool_impls/search/{base,transport,hosted,resolver}.py` and
  the per-provider modules, `klea_utils/mcp/server/bundled_tools.py` (wrapper).
* Related: ADR-0027 (DOI resolver: sequential fallback over free services),
  ADR-0004 (bundled stdio server), ADR-0005 (httpx single stack),
  ADR-0037 (tool access levels), ADR-0040 (display convention),
  `devdocs/system/mcp-permissions.md` (network/SSRF boundary).
* Deferred: `general.web_search` JSON config and forwarding provider keys to
  the bundled subprocess (`devdocs/backlog.md`); optional explicit `rank`
  and truncation to `max_results` (Parallel ignores the count).
