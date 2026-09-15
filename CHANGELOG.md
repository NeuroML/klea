# Changelog

> **Note:** `klea_agent` (`klea` / `klea-serve`) and `neuroml_mcp` (`nml-mcp`) are WIP and unreleased  ---  no PyPI releases. Tagged releases below are `klea_utils` and `klea_rag` only (`klea_utils-v*`, `klea_rag-v*`).

## Unreleased  ---  `klea_utils` / `klea_rag`

### Added

- Custom model endpoints (`custom:<model>:<url>`) accept a full endpoint URL and select the wire API from its path (`/chat/completions`, `/responses`, or `/v1/messages`); a bare base URL still defaults to OpenAI Chat Completions.
- New optional `anthropic` extra (`langchain-anthropic`) for the native Anthropic provider and custom `/messages` endpoints.
- Tool access levels (`read_only` | `full`) derived from standard MCP annotations, enforced both when tools are disclosed to the model and before dispatch; deployments can override a tool's capability per tool via `general.tool_access` (ADR-0037).
- Shared base graph state `BaseGraphSchema` (`klea_utils.graph.state`) holding the fields common to all application graph states.
- General `run_command` bundled tool: shell command execution (full-mode only, destructive) so environment and coding tasks are answerable via the agent's task path; the timeout ceiling is configurable via `KLEA_RUN_COMMAND_MAX_TIMEOUT` (ADR-0038).
- Klea-authored MCP tools refuse to run when the server process is root (uid 0) unless `KLEA_ALLOW_ROOT_TOOLS` is set, with a startup warning; this covers the bundled and NeuroML servers (ADR-0038).

### Changed

- A custom `/v1/messages` endpoint uses the `anthropic` provider and copies `OPENAI_API_KEY` to `anthropic_api_key`, with an explicit per-chat `api_key` override taking precedence.
- `anthropic_api_key` and `huggingfacehub_api_token` are masked in log output.
- Tool dispatch takes the per-tool `ToolInfo` (path metadata plus the read-only/destructive capability) instead of a raw metadata map.
- RAG graphs run with `read_only` access: destructive tools are never offered or dispatched.

### Fixed

- Transient empty LLM responses (common with HuggingFace) are retried up to twice, and a persistently empty answer now returns a clear "please retry" message instead of a blank reply.

## Unreleased  ---  `klea_agent` (WIP, unreleased)

### Added

- Agent operating modes (Scientific / General) with `Mode{requested,resolved,note}`, mode router, CLI `--mode` and web selector/badge (ADR-0030).
- App-owned web mode UI (`ui/web/mode_ui.py`, `page.py`, `app.py`) and `GET /chat/{user}/{chat}/context` hydration on the agent side.
- General-path entry router (`RouteDecision`): a narrow, fail-closed `chat` vs `task` decision that answers trivial chat inline (2-call floor) and hands everything else to the `Planner`, which plans and fixes the goal but never answers the user (ADR-0035).
- Human plan-review step (`AwaitReview`; auto-approve stub, real pause/resume deferred) and an `in_review` plan state.
- Deterministic loop budgets and a terminal failure answer, so a stuck run stops and explains instead of looping.
- Per-request tool `access_level` (chat payload) plus `general.access_level` / `general.tool_access` config; the agent defaults to `full` (ADR-0037).
- Environment and coding requests (working directory, builds, scripts) are now answerable in full mode via the bundled `run_command` tool instead of ending `unplannable` (ADR-0038).

### Changed

- Tool selection: the picker prefers the planner's suggested tools and replans when none fit; per-step observations and run progress are kept in state and context.
- `klea_agent` graph and nodes synced to `BaseLangGraph`/`BaseLLMNode` contracts (shared `ToolsPicker`/`ToolsCaller`, lifecycle parity).

### Fixed

- The final delivery node never emits a blank reply: an empty `message_for_user` (e.g. a `chat` route whose inline answer was empty) falls back to a clear "please retry" message.

## v0.5.0 (2026-09-09)  ---  `klea_utils` / `klea_rag`

### Added

- Graph-level context events: `context_snapshot` projection, `extra_state` on runners, generic `GET /chat/{user}/{chat}/context` with checkpoint hydration and streamed `context` events (ADR-0032).
- Per-request model overrides via LangGraph Runtime `KleaRunContext` (`context_schema`) replacing hand-rolled contextvar (ADR-0033).
- App-owned API/UI composition: each app mounts its own chat/context/web pages; utils provides `chat_core`, `context` router and reusable NiceGUI components (ADR-0031).
- JSON-repair fallback for LLM structured-output parsing and import checker for optional dependencies.
- `localonly` live test for Anthropic prompt caching.

### Changed

- Chat streaming split into `chat_core` + `context` router; `stream_events`/`run_graph_stream` carry generic `context_fields`/`extra_state`.
- `mask_sensitive` now recursive so nested `model_overrides.*.api_key` is redacted in logs.
- API/server emit structured INFO logging for `/query`, `/query/stream`, lifespan and readiness.

### Fixed

- Anthropic `cache_control` now placed on structured system content blocks rather than `additional_kwargs` which `langchain-anthropic` drops.
- Docs API pages updated for new `chat_core` and `context`/`graph.context` modules.

## v0.4.0 (2026-09-02)  ---  `klea_utils` / `klea_rag`

### Breaking changes

- Person-name metadata fields (currently `authors`) are now expanded at
  store time with per-word and lowercase variants (e.g. an author stored
  as `"Ankur Sinha"` also matches filters for `"Sinha"`, `"sinha"`,
  `"Ankur"` or `"ankur sinha"`).  Existing stores predate this and are
  stale -- regenerate them (`klea-stores-create store --force`,
  re-running `chunk` first).
- The built-in bibliographic retrieval filter fields (`journal`,
  `authors`, `keywords`, `year_from`/`year_to`) are removed.  Filter
  fields are now declared per domain under `filter_fields`; deployments
  that relied on automatic bibliographic filtering must declare the
  fields they want (e.g. `journal`, `authors`, `year`) explicitly.
- Package `klea_code` renamed to `klea_agent`; CLIs `klea-code` ->
  `klea`, `klea-code-serve` -> `klea-serve`; log ids `klea-code-tui`/
  `klea-code-web` -> `klea-tui`/`klea-web`; env prefix `KLEA_CODE_` ->
  `KLEA_AGENT_`. Agent data moves from `~/.local/share/klea-code/` to
  `~/.local/share/klea/`.
- Store builder renamed: `VSBuilder`/`klea-vs-create` ->
  `StoresBuilder`/`klea-stores-create`; `build|store --bm25-store`
  writes the combined corpus for keyword retrieval.
- `setup_llm()` replaced by `create_configurable_model()`.
- Chunk text and headings are now normalized for retrieval; existing
  `.klea-cache` entries are stale -- re-run
  `klea-stores-create ... --force` once to re-chunk.
- Bibliographic metadata key `venue` renamed to `journal` (matches the
  retrieval filter schema); existing `metadata-map.template.json`,
  `metadata-map.json`, `.klea-cache` and `doi-cache.json` entries with
  `venue` are stale -- re-run `klea-stores-create chunk --force` once to
  regenerate.
- Ingestion now enforces a stored-metadata key policy: `_`-prefixed
  internal keys and the provenance keys `source_path`, `source_type`,
  `source_url` are never stored.  `build` is a one-shot quick start: it
  generates `metadata-map.template.json` from the extraction and consumes
  it without a review step.  `store` requires a metadata map (an explicit
  `--metadata-map` or the template generated by `klea-stores-create
  chunk` -- run `chunk` first), aborting when none exists, when the map is
  empty, or when a source file is missing from it.
- `neuroml_mcp` tool names dropped the `_tool` suffix (e.g.
  `get_models_from_neuromldb_tool` -> `get_models_from_neuromldb`); clients
  that call tools by hardcoded name must update them.
- The auto-reload flag was renamed to `--reload` so `--debug` could mean
  verbose logging across all CLIs: the NiceGUI web clients
  (`klea web`, `klea-rag web`) dropped `--debug`/`-d` in favour of
  `--reload`/`-r`, and the serve commands (`klea-serve`,
  `klea-rag-serve`) dropped `--dev` in favour of `--reload`.

### Added

- Read-only sqlite query tools in `klea_utils` (`sqlite_query`,
  `sqlite_schema`): single `SELECT` statements only (statement separators
  inside quoted strings or comments are ignored), database opened in
  read-only mode, a row-return limit and a configurable VM-instruction
  work budget (`max_ops`) to bound pathological queries, permission-gated
  database path, and helpful unknown-table/column errors.  Usable directly
  by agents and wrapped by deployments into MCP tools.
- Configurable per-domain retrieval filter fields (`filter_fields` in the
  domain config): the query generator only proposes the declared metadata
  fields, and each domain only receives the filter clauses on its own
  fields at retrieval (per-domain filter routing), so cross-domain
  queries never apply one domain's fields to another's stores.
- Automatic bibliographic metadata extraction (`klea_utils.biblio`;
  DOI services, PDF Info, Docling, regex tiers) pre-fills the `DEFAULT`
  entries of `metadata-map.template.json`; `KLEA_INGEST_MAILTO` opts
  into DOI polite pools; `--no-ocr` speeds up text-based PDFs.
- `klea-stores-create map-lint <dir>`: LLM-free health checks on the
  metadata map (missing fields, suspicious titles/DOIs, year/filename
  mismatches, stale `venue` keys, excess `url*` keys, placeholder
  counts), printed automatically at the end of `chunk`.
- `klea-stores-create pre-check <dir>`: classifies each PDF by whether it
  needs OCR (based on whether it carries an embedded text layer) instead
  of guessing by publication year; `--organise` copies files into
  `ocr/`/`no-ocr/` subdirectories (originals untouched) so each can be
  chunked with the right `--ocr`/`--no-ocr` flag and stored into the same
  collection.
- Ingestion warns when a file converts to zero chunks (typically a
  scanned PDF with OCR disabled) on both the `chunk` and `store` paths.
- Metadata-map resolution now matches the autogenerated heading-chain
  keys (most specific first), walking up to the nearest populated
  ancestor when a leaf section has no metadata, with full fall-through
  debug logging.
- `klea-stores-create store-lint <corpus.pkl>`: LLM-free post-store
  review of a BM25 corpus (summary, suspicious chunks, structural
  checks) plus `--samples` evenly-spaced windows of contiguous chunks;
  auto-printed at the end of `store`.
- `klea_agent` is now the main application (general-purpose agent with
  coding capabilities, WIP/unreleased); `klea_rag` is consumed by it.
- Configurable model system (per-node `model_defaults`, dynamic provider
  field filtering, HuggingFace auto-derivation); optional guard node
  (skipped when `guard_model` unset, `guard_decision` now defaults to
  `safe`).
- BM25 keyword retrieval fused with vector search via reciprocal rank
  fusion; per-domain `bm25_stores`; new deps `langchain-community`,
  `rank_bm25`.
- NiceGUI web UI (3-column layout, model config, inspector); SSE
  streaming endpoint (`/query/stream`) with per-node progress consumed
  by the CLI and Streamlit frontends.
- API and UI code consolidated into shared `klea_utils` routers.
- `max_refs_size` config option (default 20000 chars): the reference
  material sent to the answer LLM is now bounded by total characters
  instead of a fixed 10-document cap, so raising `k` surfaces more chunks
  up to the character budget.
- Shared MCP tool implementations in `klea_utils` (`web_fetch`,
  `list_files`, `download_file`) that any app can wrap into FastMCP
  tools, plus shared tool-registration helpers
  (`klea_utils.mcp.registry`) and an httpx session lifespan
  (`make_http_session_lifespan`); new `[mcp]` extra (`httpx[http2]`,
  `beautifulsoup4`). `klea_agent`'s bundled tools server and
  `neuroml_mcp` both use them.
- `web_fetch` hardened: status-aware retries, 5 MB download cap
  (separate from the character cap), safe decoding, SSRF guard,
  browser User-Agent rotation (daily-refreshed from an upstream list
  with an honest-client fallback) and a Cloudflare-challenge retry.
- `download_file` / `download_file_to_cache` shared implementations for
  downloading URLs to disk with transient-error retries.
- `download_files` shared helper: downloads a list of files (as returned
  by the repository source functions below) into a chosen destination
  directory with bounded concurrency and per-file results.
- Repository file/version listing in `klea_utils`
  (`github_list_files`/`github_list_versions`, `figshare_list_files`/
  `figshare_list_versions`, `dandi_list_files`/`dandi_list_versions`,
  `biomodels_list_files`/`biomodels_list_versions`): framework-agnostic
  functions that list the versions and files of archival repositories
  (GitHub, FigShare, DANDI Archive, BioModels) with direct download URLs,
  ready to be wrapped into MCP tools.
- `read_file` shared MCP tool implementation: converts documents
  (PDF/Office/EPUB/CSV via anydoc, HTML via BeautifulSoup) and plain text
  files to text with paged, line-numbered output (`offset`/`limit`,
  `line_start`/`line_end`/`total_lines`); document conversion results are
  cached per process.  Adds `firecrawl-anydoc` to the `[mcp]` extra.
- Path-permission layer for MCP tools: tools can declare which arguments
  are file paths (`checkpaths` in `ToolInfo`); `klea_agent` and `klea_rag`
  now check those paths against the working directory before dispatch and
  refuse out-of-boundary calls without invoking the tool.
- The agent's bundled tool server now also serves `read_file` (paged
  document and plain-text reading).
- At startup the server logs a warning listing any required model roles
  that have no default model set, along with the model env vars to set,
  instead of crashing; the web UI status pane marks unset roles "Not set".
- `GET /chat/{user}/{chat}/models/active` now reports whether each model
  role is `required`.
- Standalone `klea-mcp` CLI: runs the shared bundled tools server
  (web fetch, file list/read, download) over stdio or HTTP, reusing the
  same server that apps auto-launch in-process.
- Tag-based MCP tool filtering: a server entry can list
  `include_tags`/`exclude_tags`, and the shared bundled server is
  configured under `general.bundled_tools` (`enabled`, `include_tags`,
  `exclude_tags`) -- enabled for `klea_agent` by default (batteries
  included) and opt-in for `klea_rag` (RAGs are domain specific).  Tags
  carry a documented scope (`local`/`web`) and domain/functional
  vocabulary (`files`, `code`, `download`, `echo`, `neuroml`,
  `neuroml-db`, `osb`, `bundled`).
- Standard MCP tool annotations: `ToolInfo` gains `read_only` /
  `destructive` / `idempotent` / `open_world` flags that `register_tools`
  folds into the tool's `readOnlyHint` / `destructiveHint` /
  `idempotentHint` / `openWorldHint`, so any MCP host sees the
  read-only/destructive contract of Klea's tools without adopting Klea's
  tag vocabulary.  Read-only tools (search, file read/list, web fetch)
  and destructive tools (download, code/simulation execution) are
  annotated accordingly.

### Changed

- All Klea CLIs now default to `INFO` console logging (progress on
  stdout, warnings/errors on stderr) instead of debug noise.  Pass
  `--debug` or set the `KLEA_LOG_LEVEL` env var (a level name or
  number) on any CLI to see full debug logs; `--debug` wins over the
  env var.  The rotating log file still captures `DEBUG` regardless of
  the console level.
- `klea-stores-create map-lint <dir>` now verifies the map's top-level
  keys are the actual source filenames: a source file with no entry is
  fatal (the `store` step would fail), so the full report is printed
  first and the command then exits non-zero; keys that are not source
  files (heading-keyed or stale entries) are flagged, and old
  heading-keyed flat maps are reported as a single structural issue
  instead of per-heading missing-field noise.

- Default models are now set per role through environment variables
  (`KLEA_<APP>_<ROLE>_MODEL`, e.g. `KLEA_AGENT_CHAT_MODEL`); the env file
  is optional, falling back to shell env vars.  The env schema is generated
  from the graph's `llm_models` declaration, so roles and env vars cannot
  drift.  Agent role `reasoning_model` renamed to `plan_model`.

- JSON config files are now selected by profile: `--profile <name>`
  loads `<name>.json` from the current directory, then the per-app
  config directory, instead of the `*_APP_CONFIG_FILE` key in the env
  file (the env var is still honored when set in the environment).
  `--profile template` scaffolds a ready-to-fill config into the current
  directory.
- `neuroml_mcp` and `klea_agent` register their tools through the shared
  `klea_utils.mcp.registry` (`tool_meta` / `register_tools`) instead of
  package-local copies; tools are discovered by their `@tool_meta`
  decoration (function name is the tool name) rather than a `_tool` name
  suffix.
- `download_file` now sends an honest User-Agent and refuses internal and
  private hosts by default (shared SSRF guard, shared with `web_fetch`;
  `allow_internal_hosts` opts out).
- Reference material is serialized grouped by source file: shared
  bibliographic metadata (authors, year, journal, ...) is emitted once
  per file with chunks numbered beneath, each carrying its own inline
  relevance score; `serialize_vs_retrieval` renamed
  `serialize_reference_material`.
- Conversation memory is bounded by characters instead of message counts:
  recent history is injected as real message objects (a character-budgeted
  verbatim window between the system and human prompts), older history is
  compressed into a running context summary that never overlaps that
  window, and summarisation triggers on accumulated characters.
- HTTP stack consolidated on httpx: aiohttp removed everywhere; shared
  sessions use HTTP/2 (`http2=True`) with tuned connection limits; a
  single `_make_retryer_httpx` helper backs the bundled tools.
- Shared MCP tool implementations moved from `klea_utils.mcp.tools` to
  `klea_utils.mcp.tool_impls`, and the bundled tools server (wrappers +
  server) now lives in `klea_utils.mcp.server` (`bundled.py` +
  `bundled_tools.py`); `neuroml_mcp` tools were retagged with the
  documented scope/domain vocabulary.
- Retrieved references are now re-ranked with a small recency bias:
  `rrf_merge` stays pure relevance, then `rerank_by_recency` normalizes
  the RRF score to `[0, 1]` and blends it
  `0.9 * relevance + 0.1 * time`, where the time term is
  `(year - year_min) / (year_max - year_min)` across the retrieved set
  (missing year -> `0.5`).  Newer work outranks older work of equal
  relevance, reflecting that papers build on and correct earlier results.
- Relevance scores are no longer included in the reference material sent
  to the answer/evaluator LLM (the ranked order conveys priority); they
  remain visible in the UI/reference display and the per-source scores
  stay in document metadata for debugging.
- `klea-stores-create chunk` no longer accumulates every file's chunks
  in memory: each file's chunks are released once cached, so chunk runs
  stay bounded in memory regardless of corpus size.
- `klea-stores-create chunk` and `build` now convert uncached files in
  short-lived subprocess workers bounded by `--worker-mem-limit`
  (default 4 GiB, includes the models) and `--worker-batch-size`
  (default 200, whichever comes first); `build` now composes the two
  bounded paths (worker chunk + streaming store) instead of holding the
  whole corpus.
- `klea-stores-create store` streams cached chunks per file instead of
  holding the whole corpus in memory.  BM25 corpora are written inline
  in bounded per-batch pickles and read back with a looping unpickle;
  existing single-list corpora remain loadable.
- `DoiResolver` cache writes are now atomic and batched (every 25
  resolutions plus on close), so a crash can no longer tear the cache.

### Fixed

- A query with no model configured now returns a clear "No model configured
  for role 'x'" error instead of a misleading provider-level
  "Missing credentials" error.
- `run_graph_stream` now iterates the async generator correctly.
- `AnswerGeneral` / `GenerateRetrievalQuery` handle list-form content
  blocks from newer langchain-ollama.
- `ToolsCaller` no longer emits a stream event when pre-exec checks skip.
- `k` was increased twice per `retrieve_more_info` cycle (the router and
  the retrieval node both incremented it); it is now increased once per
  cycle, at retrieval.
- `download_file` no longer corrupts binary downloads by writing them
  decoded as text; the raw response body is saved as bytes.
- Empty `{}` heading placeholders in the metadata map no longer strip
  the `DEFAULT` metadata from matching chunks; per-heading entries are
  merged over `DEFAULT` (gap-fill), so a heading that sets only a `url`
  keeps the file's authors/year/journal.
- Orphaned `.klea-cache` entries (whose source file no longer exists)
  are now auto-pruned on every `chunk`/`store`/`build` run.
- `klea-stores-create store` is now cache-only: it no longer converts
  files on the fly (conversion settings `--max-tokens` and `--ocr`
  were removed; `chunk` owns them).  Run `chunk` (or `build`) first so
  every file is cached.
  Internally, cache loading + metadata-map folding were extracted from
  `chunk_all` into a shared `_fold_metadata_map` helper and a dedicated
  cache-only `_load_and_fold_results` path used by `store`.
- `store` is now incremental via a per-collection store manifest
  (`.klea-cache/<collection>.manifest.json`): unchanged files are
  skipped, changed files have their old chunk IDs deleted and are
  re-added in place, and files absent from the source are never pruned.
  Chunk IDs are deterministic (`<file_name>:<chunk_index>`).  `store
  --force` drops the whole collection and rebuilds it (portable across
  backends via a `drop_collection` dispatch helper), which fixes the
  previous `--force` duplicate-on-re-add behaviour.
- `metadata-map.template.json` is now generated into
  `<source_dir>/.klea-cache/` instead of the source directory; copy it
  out to review and pass the reviewed copy via `--metadata-map`.
- A corrupt or truncated `.klea-cache` entry (e.g. left by a crash or
   an interrupted run) no longer aborts the whole `chunk`/`build` run:
   it is moved aside as `<hash>.pkl.corrupt`, the source is re-converted
   on the fly, and the artifact is pruned once the entry is regenerated.
   Cache writes are now atomic, so an interrupted run can no longer
   leave a truncated entry behind.
- MCP tool execution failures now correctly report `isError: true` per the
   MCP spec (previously every failure returned `isError: false`).
- `list_files` now returns a boolean `truncated` flag for consistency with
   other tools (previously `"True"`/`"False"` strings).

---

## v0.3.0 (2026-07-02)

### Changed

- Uniform `label` attribute on all nodes (~25 classes) -- doubles as the
  LangGraph node name for UI progress display; `write_custom_stream()`
  helper added for streaming node events.

### Fixed

- Fast CLI `--help` via lazy imports; improved error handling and logging
  throughout.

---

## v0.2.0 (development, unreleased)

### Added

- Vector store backends (Chroma, PGVector, Qdrant) in a `stores`
  sub-package; document ingestion pipeline (`VSBuilder`, chunking,
  metadata) with the `klea-vs-create` CLI.
- MCP client integration (FastMCP) with per-domain vector store and MCP
  server configuration.
- Evaluator node with re-ranking and query-modification loop; structured
  output schemas for LLM nodes.
- Memory support (context summary, conversation history); session
  checkpointing via InMemorySaver.

---

## v0.1.0 (2026-06-30)

Initial release of `klea_utils` and `klea_rag` to PyPI:

- `BaseLangGraph` orchestrator with setup/run template methods
- RAG pipeline: guard, classify, retrieve, generate, evaluate
- Ollama and HuggingFace inference providers; Chroma, PGVector, Qdrant
  vector store backends
- CLI client and Streamlit web interface; health check endpoints
- Sphinx documentation at https://neuroklea.org
