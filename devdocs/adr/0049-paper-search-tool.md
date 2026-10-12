---
status: "accepted"
date: 2026-10-12
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Paper search tool: peer-reviewed-first scholarly source pool with preprint gating

## Context and Problem Statement

The bundled server exposes `web_fetch` and `web_search`, but nothing that
finds **academic papers** by keyword.  A research agent needs title, authors,
year, journal, abstract, DOI and a link, plus a signal for whether a hit has
been peer reviewed.  General web search returns publisher pages and PDFs, not
structured records.

Every scholarly API with a usable index is free and keyless (OpenAlex,
Crossref, Europe PMC, PubMed via NCBI E-utilities, arXiv) except Semantic
Scholar, whose search endpoint answers HTTP 429 to nearly every request
without a key.  Which sources should the tool use, how should it order and
degrade across them, how should preprints be handled, and how should the
records be built without duplicating the DOI resolver's parsing?

## Decision Drivers

* Zero setup: a bundled tool must work with no account or key; a key may only
  raise limits (matching the web-search tool, ADR-0048).
* Peer-reviewed first: a search result must be trustworthy by default, with
  preprints clearly marked and opt-in, not silently equivalent.
* Reuse: the DOI resolver (ADR-0027) already normalises Crossref/OpenAlex/
  Semantic-Scholar responses to `BiblioRecord`; paper search should reuse
  those normalisers rather than re-implement them.
* Resilience: a single throttled or down API must not fail the search
  (sequential fallback over a pool, matching ADR-0027/0048).
* Coverage: general (physical/life/engineering) and life-sciences queries
  need different primary sources.
* Framework-agnostic impls over the app lifespan `httpx` session (ADR-0005);
  fixed request hosts, so no SSRF guard (the model cannot choose the URL).

## Considered Options

* **A. Single provider (OpenAlex only).**
* **B. A keyed/paid index (Scopus/Web of Science).**
* **C. Peer-reviewed-first pool with per-call rotation plus opt-in preprint
  sources (`PaperSearchResolver`) (chosen).**
* **D. Preprint-first (arXiv only).**

## Decision Outcome

Chosen option: "**C. Peer-reviewed-first pool with rotation and preprint
gating**", because it is the only option that needs no setup while giving
authoritative, peer-reviewed results by default and still covering preprints
when asked.

* A `search_papers` bundled tool (`utils_pkg/klea_utils/mcp/tool_impls/papers/
  search.py`, wrapped in `klea_utils/mcp/server/bundled_tools.py`) is tagged
  `{bundled, web}`, annotated read-only + open-world (ADR-0037), and returns
  `{query, domain, preprints, results, error, note}`; each result is a
  `PaperRecord` (a `BiblioRecord` plus `url`, `peer_reviewed`, `source`).
* `domain="general"` queries the peer-reviewed general sources `openalex` and
  `crossref`, plus `semantic_scholar` when `SEMANTIC_SCHOLAR_API_KEY` is set;
  the pool **rotates which general source is primary across calls** (like
  ADR-0027) so no single API takes every request.  `domain="life-sciences"`
  prepends `europepmc` then `pubmed` before the general pool.
* The first source that returns results **with abstracts** wins; results
  without abstracts (common for Crossref) are kept only as a last resort.
  Failures are collected into `note`; if no source responded, `error` is set.
* Preprints are searched **only** when `preprints=True`, and are listed after
  the peer-reviewed results: arXiv for general, plus Europe PMC's preprint
  index (`SRC:PPR`, bioRxiv/medRxiv/Research Square) for life sciences.
  Records are flagged `peer_reviewed=False`, and a preprint server in the
  venue overrides an API's "journal article" filter (Crossref SSRN, Semantic
  Scholar).
* Results are deduped by DOI (or title when there is no DOI) and abstracts are
  truncated to keep a page small.
* The tool reuses `klea_utils.biblio.normalize`'s Crossref/OpenAlex/Semantic-
  Scholar normalisers and `klea_utils.biblio.models.BiblioRecord`; the
  PaperRecord conversion (`papers/record.py`) only adds the search-specific
  fields.
* Per-tool identity is `klea-paper-search/<version>`
  (`klea_utils.mcp.tool_impls.user_agent`), and the polite pool email
  (`KLEA_INGEST_MAILTO`) is sent to Crossref/OpenAlex/PubMed when set.
* **No disk cache**: unlike DOIs (immutable, ADR-0027), search results are
  time-sensitive.

### Consequences

* Good, because it works with no account or key and degrades to the next
  source on a rate limit or outage.
* Good, because peer-reviewed results are the default and preprints are
  explicit and marked, so the model can warn the user.
* Good, because the normalisers and record model are shared with the DOI
  resolver, so parsing fixes benefit both.
* Good, because the common pool shape is documented once in
  `system/external-source-pools.md`.
* Bad, because it depends on free APIs whose terms, fields and rate limits can
  change without notice; the pool and keys are the mitigation.
* Bad, because preprint detection is a venue-name heuristic (for example
  `rxiv`), so a false positive/negative is possible.
* Bad, because the module-level `search_papers` entry point keeps rotation
  state on a shared `PaperSearchResolver`, so the primary source varies
  between calls (intended, but a source of test nondeterminism that tests pin
  via an explicit rotation).

### Confirmation

* `utils_pkg/tests/test_papers_search.py` covers each source's parsing, the
  rotation/fallback and abstract handling, preprint gating and dedupe, the
  bundled wrapper's `Context`/`to_result` contract, and a tolerant live check
  that distinctive queries (NeuroML, Open Source Brain) return the expected
  papers.
* `utils_pkg/tests/test_bundled_server.py` / `test_bundled_config.py` assert
  the tool's name, `{bundled, web}` tags, read-only + open-world annotations,
  and that it is exposed under the `web` scope.
* `ruff`, `ty` and the docs build are clean for the package.

## Pros and Cons of the Options

### A. Single provider (OpenAlex only)

* Good, because one integration to maintain.
* Bad, because OpenAlex's coverage/index is incomplete and its rate limit is a
  hard ceiling; misses life-sciences/preprint coverage.

### B. Keyed/paid index (Scopus/Web of Science)

* Good, because broad, curated coverage.
* Bad, because it needs an institutional subscription, which a bundled
  batteries-included tool cannot assume.

### C. Peer-reviewed-first pool with rotation and preprint gating (chosen)

* Good, because zero setup, resilient to a single provider, and trustworthy by
  default (preprints opt-in and marked).
* Bad, because it relies on several anonymous, rate-limited free APIs and a
  venue heuristic for preprints.

### D. Preprint-first (arXiv only)

* Good, because arXiv is free, keyless and complete within its scope.
* Bad, because it excludes peer-reviewed literature and non-physics fields,
  which is exactly what a general research agent needs first.

## More Information

* Code: `utils_pkg/klea_utils/mcp/tool_impls/papers/{search,record,http}.py`
  and the per-source modules (`openalex.py`, `crossref.py`,
  `semantic_scholar.py`, `europepmc.py`, `pubmed.py`, `arxiv.py`);
  `klea_utils/mcp/server/bundled_tools.py` (wrapper);
  `klea_utils/biblio/{normalize,models}.py` (shared normalisers/record).
* Related: ADR-0027 (DOI resolver: round-robin fallback, normalisers reused
  here), ADR-0048 (web-search pool), ADR-0005 (httpx single stack), ADR-0037
  (access levels), `system/external-source-pools.md` (the shared pool
  pattern), `system/mcp-permissions.md` (network boundary).
