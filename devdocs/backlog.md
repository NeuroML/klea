# Klea backlog

Consolidated open-work backlog, so deferred items are not lost across dated
session logs (`.agents/`).  Add items here when a session defers something;
remove them when implemented (git log records the work).

Last updated: 2026-09-30.

## HITL / plan review

- Replace the `AwaitReview` stub (`STUB_REVIEW`, canned "Looks good,
  proceed") with real LangGraph `interrupt()` / `Command(resume=...)`; add
  the API and web UI resume path.  `agent_pkg/klea_agent/nodes/await_review.py`.
  The failure-resume path already exists (`resume=true` on `/query/stream`,
  checkpoint continuation, ADR-0043) and should be reused: an interrupt resume
  is the same `Command(resume=...)` over the same thread, so only the
  interrupt source and the payload differ.
- Decide the semantics: resume the **same** execution with state intact vs
  start a new turn.
- Generalise `AwaitReview` into a reusable await-user node and wire
  `needs_input` / `pending_question` to resume with the plan and state intact
  (today `needs_input` ends the run with the question; the next turn is a
  fresh run).
- Decide whether `needs_input` should also be a step kind.
- Write the ADR (next ADR); update
  `devdocs/system/agent-general-path-control-flow.md` and ADR-0035 when
  implemented.  Older session logs cite stale ADR numbers for this - use the
  next free one.

## Agent general path

- ADR-0041 residual: reasoning steps remain serial (one `ReasoningNode`
  conclusion per round); batching a reasoning prefix into one call (a per-step
  conclusion map) is deferred.
- ADR-0041 measurement: the `parallel_writes` E2E scenario observes a
  two-independent-write batch; a dedicated evaluation-harness case still needs
  the `eval_pkg` harness (below).
- Phase-2 deterministic tool-substitution backstop; planner
  catalogue-membership validation.
- `add_artefact` tool; promote `_slug` / artefact-id derivation to a shared
  helper or `ArtefactSchema`.
- Session-as-invocation / "what next" ADR; cached project explorer.
- Revisit the `max_automated_plan_revisions` default (4 automated replans).
- Absence handling as a class: bound search escalation when a requested path
  is missing (an agent may reach for `find /`).  `read_file`/`edit_file` now
  report nearby entries on a missing target, and the picker escalates identical
  failed calls, but `run_command` is still not boundary-checked like the file
  tools, so consider a guard; gather a concrete case before coding.
- Planner completion gap: the Planner can mark a plan's only/all steps `done`
  with `status=in_progress` (it has no `completed` status), which routes to the
  step entry with no current step and forces an empty-picker replan (observed in
  the `missing_file` E2E).  Consider a deterministic completion path.

## Tools

- Retrieval tool: no `search_stores`-style tool wires the RAG vector stores
  into the agent yet (retrieval is optional/deferred per the control-flow
  note).
- ADR-0039 deferred items: graph-level read/staleness gate; `apply_patch` /
  multi-edit; per-model edit-format auto-selection; post-edit LSP
  diagnostics/auto-format.
- Offload sync file tools to `asyncio.to_thread` so the tool-call wall-clock
  backstop can fire (one slow/huge edit can otherwise stall the server).
  Precedent: `klea_utils/mcp/tool_impls/ssrf.py:88`.
- Prompt tidy-up: `Planner_system.md` still says "Do not invent tools or
  arbitrary shell commands" (the picker prompt was clarified separately).

## Scientific mode / correctness / evaluation

- Scientific-mode correctness layer (ADR-0029): grounding, evidence,
  provenance and independent verification are not enforced; `assurance` is
  always `unverified` (ADR-0030/0036).
- Independent inline-answer verifier for `chat`-route answers (ADR-0029
  phase).
- `eval_pkg` harness (see `devdocs/system/agent-evaluation-harness.md`).

## Infrastructure / correctness

- Retrieval depth (`k`) is not per-invocation.  `BaseKleaRetriever._k` lives on
  the process-wide `self.stores` / `self.bm25_stores` managers (built once in
  `BaseLangGraph._get_vector_stores`), not in `RAGState`, so it is shared
  across every `thread_id` and concurrent run; `reset_k()` only fires on the
  accepted-answer route (`RouteEvaluator`), so `k` bleeds between invocations.
  Fix requires moving `k` into `RAGState` and redesigning the retriever API so
  `retrieve` / `inc_k` / `can_inc_k` / `reset_k` become graph/node methods
  taking the state.  Deferred: larger refactor across `base.py`/`vs.py`/
  `bm25.py`, `RouteEvaluator`, `RetrieveInfoNode` and `RAGState`.
- Token-usage tracking / benchmarking: the `usage` stream event and DEBUG log
  carry per-node counts, but no consumer aggregates them per node (CLI drops
  `usage`; web UI sums one total; graph state keeps a run-level total).
  Decide the durable mechanism (consume stream events in a benchmark utility
  vs persist a per-run per-node summary) and whether to record usage per LLM
  call so retries/truncations count (today only the final call per node is
  measured).  `TokenUsage` now carries `reasoning_tokens`/`cached_tokens`/
  `role`; a stable node key is still needed.

## Configuration / UX

- TUI/CLI mode and access selectors (the web UI has them; TUI/CLI do not).
- Optional `KLEA_AGENT_ACCESS_LEVEL` env default (deferred; JSON config
  default used instead).
- Third-party trust roadmap (consent loop, sandbox-by-default, curated server
  registry), deferred per ADR-0037.
- Model/provider picker: replace the free-text model field with a
  models.dev-catalog-backed picker (via `klea_utils.models_catalog`), exposed
  through a backend catalog endpoint so the browser does not call models.dev
  directly, with an "Other (custom)" free-text fallback and local providers
  marked by an offline probe.  Design captured with ADR-0042.
- Credentials follow-ups (ADR-0042): per-request/session-only keys that are
  never persisted server-side (web/TUI parity); OS keyring support (desktop
  only, absent on headless servers); `{env:VAR}` references in place of stored
  secrets; and encryption at rest with a key from the deployment's auth
  provider (Keycloak) once deployments authenticate.
- Model variants (design/ADR first): `ParsedModelName.variant` is unused.  The
  de-facto standard is opencode's `provider/model#variant`, a named request
  overlay deep-merged into provider/model settings.  opencode sources variant
  names from models.dev `reasoning_options` (Klea's catalog already carries
  this field; the client just does not read it yet), plus hardcoded heuristics
  and config `variants` overrides, and maps each variant to provider-specific
  request kwargs.  Klea would need: `reasoning_options` access in
  `klea_utils/models_catalog.py`; provider -> LangChain kwargs mapping (e.g.
  ChatOpenAI `reasoning_effort`/`reasoning`, ChatAnthropic
  `thinking`/`reasoning_effort`); a config `variants` section; `#variant`
  parsing in `parse_model_name` (`provider:model#variant[:suffix]`); picker
  support (dropdown from `reasoning_options`, free-text fallback); and an ADR.
- HuggingFace model discovery: the picker's model list is models.dev's
  `huggingface` provider (78 curated, mostly large models); small/on-device
  models (SmolLM2, Qwen2.5-0.5B, ...) are absent and must be typed as free
  text.  Consider a curated small-model suggestion list (offline) and/or an HF
  Hub-backed list
  (`GET https://huggingface.co/api/models?filter=text-generation&sort=downloads`)
  behind a cached server endpoint with an offline fallback.
- Verify the HuggingFace local (`:local`) backend end to end once a working
  local `torch` is available; the maintainer's env has a broken CUDA build
  (`undefined symbol: ncclCommResume`).

## Testing

- Fault-injection hook to exercise the resume path end to end: LangGraph has
  no external interrupt/pause API (only `interrupt()` inside a node, or
  LangSmith), so a mid-run node failure is hard to trigger by hand.  Add a
  deterministic test hook (e.g. a debug/fault-injection env var that makes a
  chosen node raise once) so `resume=true` can be tested: the web Retry action,
  checkpoint continuation from the failed node (not the entry node), and
  single-turn persistence (one user row, one assistant row).
- Add a NiceGUI element-rendering test harness (e.g. `nicegui.testing.User`
  / `Screen` fixtures) so UI wiring can be asserted without a browser:
  dialogs rebuilding on credential/model change, the chat-area welcome CTA
  appearing/disappearing, status-pane refresh, and send-button gating.
  Today only pure helpers (`utils_pkg/tests/test_ui_state.py`) and
  registration smoke tests (`agent_pkg/tests/test_access_ui.py`) exist.
- `rag_pkg/klea_rag/nodes/generate_retrieval_query.py`:
  `_get_default_error_result` returns an all-default `RetrievalQueryOutput()`
  (empty `search_query`); decide whether that should degrade to a clear
  failure/known sentinel.
