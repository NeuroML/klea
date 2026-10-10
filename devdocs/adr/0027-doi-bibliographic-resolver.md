---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# DOI/Bibliographic resolver: round-robin tiered cascade with disk cache

## Context and Problem Statement

During ``klea-stores-create chunk`` (``StoresBuilder.chunk_all``) each
source document's ``metadata-map.template.json`` ``DEFAULT`` entry is
pre-filled with bibliographic metadata (authors, title, journal/year,
DOI, ``url*``).  The PDF Info dict (``pypdfium2``) is often empty;
Docling's layout may give only a title and hyperlinks; regex over the
first page can guess a DOI but with no publication record (no journal,
no author list).  A DOI found in the document must therefore be
resolved against bibliographic APIs to obtain authoritative metadata,
but each provider is rate-limited, occasionally 429/5xxs, and has a
different coverage/bias.

Where should the DOI be resolved, which services should be used, and
how should load be spread and cached so a re-ingest never re-queries?

## Decision Drivers

* Authoritative fields: Crossref, OpenAlex, and Semantic Scholar each
  carry title/authors/year/journal/DOI, but none is complete for every
  DOI; combining them gives the best record.
* Rate limits: each API throttles anonymous calls; the polite pool
  (``KLEA_INGEST_MAILTO``) has higher limits and should be used when
  available.
* Resumability: ``chunk_all`` is already worker-isolated and cached
  (``ADR-0001``); DOI resolution must not re-query on a re-run of
  ``chunk``/``build``.
* Tiered cascade: DOI resolution is the most authoritative tier, so it
  should override PDF Info, Docling, and regex-derived fields, not the
  other way around.

## Considered Options

* **A. Single provider (e.g. Crossref only), no cache** -- simplest.
  Rejected: Crossref alone misses many DOIs covered by OpenAlex/
  Semantic Scholar; rate-limiting is unmitigated and re-ingest would
  re-query every DOI.
* **B. Parallel fan-out to all providers, merge best fields** --
  query all three in parallel per DOI and merge per-field best values.
  Rejected: triples rate-limit pressure per DOI for little gain over
  sequential fallback; requires field-level conflict policy that is
  itself heuristic.
* **C. Round-robin sequential fallback with disk cache + tiered
  cascade (chosen)** -- ``klea_utils/biblio/doi.py`` ``DoiResolver``
  queries the three APIs in round-robin order (``crossref -> openalex ->
  semscholar`` rotation) per new DOI, falling back to the next when the
  previous returns no record or errors (HTTP 429/5xx are logged and the next
  service is tried; there is no per-provider backoff).  The
  resolved record's title/authors/year/journal/DOI override all lower
  tiers.  Results are cached to ``.klea-cache/doi-cache.json`` (and its
  backing ``.klea-cache`` file) on disk so a re-ingest never re-queries;
  the cache is used to seed ``metadata-map.template.json`` ``DEFAULT``
  and is also read by ``map-lint`` (``map_lint.py``).

  The broader cascade is ``doi-service > pdf-info > docling > regex``
  in ``klea_utils/biblio/extract.py`` (``_extract_biblio``): each tier
  only fills fields the tiers above it have not already set, so the DOI-
  service result always wins.

## Decision Outcome

Chosen option: "C. Round-robin sequential fallback with disk cache +
tiered cascade".

* ``utils_pkg/klea_utils/biblio/doi.py`` ``DoiResolver`` (used by
  ``klea_utils/stores/ingestion.py`` ``chunk_all`` as
  ``DoiResolver(cache_dir=...)``): a ``CROSSREF`` / ``OPENALEX`` /
  ``SEMANTIC_SCHOLAR`` service list queried via per-service
  ``_query_<service>`` methods in round-robin order with fallback to the
  next (no per-provider retry/backoff), ``KLEA_INGEST_MAILTO`` sent as a
  ``mailto`` query parameter to Crossref and OpenAlex for the polite pool
  (higher rate limits) when set, an honest ``User-Agent:
  klea-ingest/<version>`` on every request, and ``doi-cache.json``
  read/write (``json`` on disk, ``.klea-cache/doi-cache.json`` track in the
  original ``devdocs/system/store-create.md`` cache layout).
  Cache key: canonical DOI string; value: ``BiblioRecord`` (title,
  authors, year, journal, DOI).
* ``klea_utils/biblio/extract.py`` ``_extract_biblio`` tiered cascade:
  ``1`` ``doi-service`` (via ``DoiResolver``), ``2`` ``pdf-info``
  (``pypdfium2`` Info dict), ``3`` ``docling`` (free structured signals
  from Docling's layout), ``4`` ``layout-regex`` (first-page header
  region via layout bounding boxes), ``5`` ``regex`` (first ~3000 chars).
  Each tier's ``BiblioRecord`` is merged with ``_merge_biblio`` so a
  lower tier never overwrites a higher tier's field.  The merged record
  is written once per file into ``metadata-map.template.json``
  ``DEFAULT`` plus ``_metadata_complete``/``_sources`` provenance keys.
* The cascade is invoked per file in the parent (cache-hit path) and in
  the worker (cache-miss path) via ``_extract_metadata_fallback``;
  workers each get a fresh ``DoiResolver`` (own ``httpx`` client,
  own ``doi-cache.json`` load at start, batched atomic flush per
  ``ADR-0001``).

### Consequences

* Good, because a DOI found anywhere in the document yields a full
  authoritative record without flooding a single provider (round-robin
  spreads load, fallback still succeeds when one is throttled).
* Good, because re-ingest is free for DOI-mapped files: ``doi-cache.json``
  is the on-disk source of truth and the worker batching is shared with
  ``ADR-0001``'s atomic JSON hygiene.
* Good, because lower tiers (``pdf-info``/``docling``/``regex``) only
  fill what the DOI service did not, so an authoritative record is not
  diluted by OCR artifacts.
* Bad, because ``DoiResolver`` keeps its own short-lived synchronous
  ``httpx.Client`` (it does not use the app's shared lifespan
  ``http_session``, ``ADR-0005``) and runs inside the worker that also
  serves ingestion; calls are sequential per file and the batch is small
  (25 per batch), so contention is limited.
* Bad, because ``doi-cache.json`` is human-readable JSON (not a SQLite
  store) so very large DOI sets pay a full-file rewrite at the ``25``
  batch boundary; acceptable for the current corpus sizes (< 10k DOIs).

### Confirmation

* ``utils_pkg/tests/test_biblio_doi.py`` covers ``DoiResolver`` round-
  robin ordering, the honest ``klea-ingest/<version>`` ``User-Agent``, and
  ``doi-cache.json`` read/write without re-query; ``test_stores_ingestion.py``
  covers the ``DEFAULT`` gap-fill + ``_sources`` provenance.
* ``klea-stores-create chunk`` writes ``metadata-map.template.json``
  with the merged ``DEFAULT`` per file; ``store-lint``/``map-lint``
  surface missing ``DEFAULT`` fields (``AGENTS.md`` file conventions).
* ``ty`` ``extra-paths`` for ``biblio``/``stores/ingestion``; ``ruff``
  clean for ``doi.py``/``extract.py``.

## Pros and Cons of the Options

### Round-robin tiered cascade with disk cache (chosen)

* Good, because authoritative record from the best available provider
* Good, because load spread + fallback on 429/5xx
* Good, because re-ingest never re-queries (disk cache)
* Bad, because brief HTTP contention in the worker (small batch)

### Single provider, no cache

* Good, because minimal code
* Bad, because incomplete coverage and re-query on every run

## More Information

* Code: ``utils_pkg/klea_utils/biblio/doi.py`` (``DoiResolver``,
  round-robin + ``doi-cache.json``), ``biblio/extract.py``
  (``_extract_biblio`` cascade + ``_merge_biblio`` gap-fill),
  ``stores/ingestion.py`` (``chunk_all`` per-file ``DEFAULT``
  pre-fill), ``biblio/docling.py`` / ``biblio/pdf.py``/``biblio/regex.py``
  (lower tiers); ``AGENTS.md`` HTTP conventions (httpx; this resolver uses
  its own short-lived synchronous client).
* Related: ``ADR-0001`` (worker-isolated chunking that calls the
  cascade per file), ``devdocs/system/store-create.md`` (chunk/store/
  build cache layout with ``doi-cache.json``), ``docs/concepts/rag.rst``
  174 (reference-material grouping by file with bibliographic header).
* Commits: ``90844d4`` (tiered cascade), ``8160381`` (heading-chain
  template: DOI-derived ``url_doi`` + ``_sources``), ``908cb4e``
  (regex tier), ``dois`` batching + atomic JSON hygiene alongside
  ``083e7c6``.
* Codified ``2026-08-28``; resolver hardened ``2026-08-11..15`` during
  the bibliographic extraction sprint.
