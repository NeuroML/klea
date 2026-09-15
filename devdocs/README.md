# devdocs -- internal development notes

Internal development notes for the Klea team.  This folder is *for us*:
architecture research, design discussions, and decisions that guide future
implementation.  It is intentionally kept separate from `docs/`, which is
the public, user-facing documentation site.

Rules:

- One file per topic, `kebab-case.md` (e.g. `mcp-permissions.md`).
- Keep notes high-level: decisions, trade-offs, pointers to source and
  commits.  Omit routine work (git log has the step-by-step edits).
- ASCII-only text, matching the repo file conventions in `AGENTS.md`.
- When an idea here gets implemented, update the public `docs/` and
  `CHANGELOG.md` at that point -- not before.

Structure:

- `system/` -- architecture and component contracts.  Mermaid diagrams
  and data-flow notes that explain how a subsystem works.  See
  `system/store-create.md` for ingestion.
- `adr/` -- Architecture Decision Records in MADR format, numbered as
  `NNNN-<slug>.md` (e.g. `0001-chunk-workers.md`) so they can be
  referenced by number.  Each file records one decision: context, options
  considered, outcome, and consequences.  See `adr/0001-chunk-workers.md`
  for the chunking/mass-ingestion decisions.  The template is
  `adr-template.md` at the `devdocs/` root.
- `.agents/` -- session logs (see `AGENTS.md`).

## Index

| File | Topic |
|------|-------|
| `system/c4-system-context.md` | C4 model Level 1: system context diagram (whole Klea product as the system in scope) |
| `system/c4-container.md` | C4 model Level 2: container diagram (Klea packages/services, datastores, shared lib, and their interactions + external systems) |
| `system/store-create.md` | Store creation pipeline: chunk, store, build, worker isolation, and cache layout |
| `system/mcp-permissions.md` | Filesystem permissions for MCP tools: the current in-tool check, why it cannot cover third-party servers, and what opencode does instead |
| `system/edit-format-strategy.md` | Edit-format strategy for coding tools: write (whole-file) + edit (search/replace) dual tools with model-guided selection |
| `system/agent-topology-literature-review.md` | Literature review: agent loop topology, cost-aware inference, planning/verification and small-model agency |
| `system/agent-evaluation-harness.md` | Planning note: task-level evaluation harness for comparing agent topologies on correctness and cost |
| `system/agent-general-path-control-flow.md` | General-path control flow: nodes, failure triage, evaluator routing and escalation (ADR-0035 mechanics; future C4 diagram) |
| `system/web-theming.md` | Web UI theming: design tokens, the `@layer overrides` cascade-layer contract, and semantic classes |
| `adr/0001-chunk-workers.md` | ADR-0001: Subprocess chunk workers and DOI-cache batching for large-corpus ingestion |
| `adr/0002-worker-retry.md` | ADR-0002: Retry worker batches that die with no results instead of marking them failed |
| `adr/0003-mcp-iserror-compliance.md` | ADR-0003: Strictly require MCP isError for tool execution failures |
| `adr/0004-bundled-stdio-server.md` | ADR-0004: Bundled stdio MCP server and tag-filterable tool filtering |
| `adr/0005-httpx-single-stack.md` | ADR-0005: Single HTTP stack on httpx with shared retry and lifespan session |
| `adr/0006-monorepo.md` | ADR-0006: Monorepo for all Klea packages |
| `adr/0007-mcp-permissions.md` | ADR-0007: Declarative path permissions with dual-layer check and deferred interactive policy |
| `adr/0008-always-retrieve.md` | ADR-0008: Always retrieve for RAG queries |
| `adr/0009-no-answer-fallback.md` | ADR-0009: Configurable fallback when no grounded answer can be generated |
| `adr/0010-guard-node.md` | ADR-0010: Cheap guard node for production deployments |
| `adr/0011-multiple-query-domains.md` | ADR-0011: Multiple query domains per RAG query |
| `adr/0012-bm25-hybrid.md` | ADR-0012: BM25 hybrid retrieval for exact string matches |
| `adr/0013-inspection-features.md` | ADR-0013: Inspection features for validating RAG output |
| `adr/0014-runtime-model-switching.md` | ADR-0014: Runtime per-request model switching with user-supplied API keys |
| `adr/0015-profile-env-config.md` | ADR-0015: Layered config via env file and profile-resolved JSON |
| `adr/0016-baselanggraph-orchestrator.md` | ADR-0016: BaseLangGraph as single model/MCP/VS orchestrator (Template Method) |
| `adr/0017-llm-invoke-retry.md` | ADR-0017: LLM invoke retry and token-window adaptation |
| `adr/0018-message-memory.md` | ADR-0018: Structured message memory for nodes |
| `adr/0019-shared-abstract-nodes.md` | ADR-0019: Shared abstract node hierarchy (Template Method for nodes) |
| `adr/0020-unified-tool-caller.md` | ADR-0020: Unified shared tool picker/caller for agent and RAG |
| `adr/0021-metadata-map.md` | ADR-0021: Compulsory metadata map with hierarchical folding |
| `adr/0022-filter-system.md` | ADR-0022: Filter system: declarative fields, per-domain scoping, dialect translation |
| `adr/0023-sqlite-checkpointer.md` | ADR-0023: SQLite checkpointer and session store for graph resumption |
| `adr/0024-file-gateway.md` | ADR-0024: File-gateway pattern for external-repository tools |
| `adr/0025-agent-topology.md` | ADR-0025: Agent loop topology: plan->explore->toolpick->observe->evaluate vs flat ReAct |
| `adr/0026-client-server.md` | ADR-0026: Client-server architecture over monolithic app |
| `adr/0027-doi-bibliographic-resolver.md` | ADR-0027: DOI/Bibliographic resolver: round-robin tiered cascade with disk cache |
| `adr/0028-prompt-cache.md` | ADR-0028: Prompt caching via stable system prefix and forward-stable conversation history |
| `adr/0029-agent-correctness-architecture.md` | ADR-0029: Agent correctness architecture: evidence, provenance and verification as architectural objects |
| `adr/0030-agent-operating-modes-and-assurance-levels.md` | ADR-0030: Agent operating modes and assurance levels |
| `adr/0031-apps-own-api-and-ui-composition.md` | ADR-0031: Apps own API contracts and UI composition; klea_utils is a components/helpers library |
| `adr/0032-graph-context-events-and-state-ownership.md` | ADR-0032: Graph-level context events and state ownership |
| `adr/0033-model-overrides-via-langgraph-runtime-context.md` | ADR-0033: Per-request model overrides via LangGraph Runtime context |
| `adr/0034-tool-calling-mechanism.md` | ADR-0034: Tool calling mechanism: prompt injection with structured emission (bind_tools rejected) |
| `adr/0035-general-operational-agent-path.md` | ADR-0035: General (operational) agent path: topology, plan evolution, failure signals and escalation |
| `adr/0036-grounding-enforcement-general-vs-scientific.md` | ADR-0036: Grounding enforcement: optional in general mode, mandatory in scientific mode |
| `adr/0037-tool-access-levels.md` | ADR-0037: Tool access levels (read_only \| full) from MCP annotations, with documented trust limits |
| `adr/0038-run-command-tool.md` | ADR-0038: General shell command tool (`run_command`): full-mode only, advisory path checking |
| `system/c4-component-rag.md` | C4 model Level 3: RAG component diagram (auto-generated Mermaid core + elk augmentation) |
| `system/c4-deployment.md` | C4 model Deployment: build-time vs local vs container platform (Docker with HuggingFace Spaces node) |
