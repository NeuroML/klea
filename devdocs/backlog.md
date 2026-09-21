# Klea backlog

Consolidated open-work backlog, so deferred items are not lost across dated
session logs (`.agents/`).  Add items here when a session defers something;
remove them when implemented (git log records the work).

Last updated: 2026-09-19.

## HITL / plan review

- Replace the `AwaitReview` stub (`STUB_REVIEW`, canned "Looks good,
  proceed") with real LangGraph `interrupt()` / `Command(resume=...)`; add
  the API and web UI resume path.  `agent_pkg/klea_agent/nodes/await_review.py`.
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

- ADR-0041 draft (plan-step granularity and parallelism): settle the
  plan-state model, the fail-safe dependency default, the picker multi-call
  allowance, and whether to measure the parallelism win; dependency-frontier
  batching.
- Phase-2 deterministic tool-substitution backstop; planner
  catalogue-membership validation.
- `add_artefact` tool; promote `_slug` / artefact-id derivation to a shared
  helper or `ArtefactSchema`.
- Session-as-invocation / "what next" ADR; cached project explorer.
- Revisit the `max_automated_plan_revisions` default (4 automated replans).
- Absence handling as a class: bound search escalation when a requested path
  is missing (an agent may reach for `find /`).  `run_command` is not
  boundary-checked like the file tools, so consider a guard; gather a concrete
  case before coding.
- Picker error feedback for weak models (T3 residual): make it more
  actionable or accept as a model-capability limit.

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

- Shared-instance `_last_*` snapshot leak (per-run `_last_*` state on shared
  node instances).
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

## Testing

- E2E: re-run `reasoning` to confirm the picker leaves `pattern` unset and
  lists all three files, then run `review` and `missing_file` (previously
  timed out) one at a time.
- `rag_pkg/klea_rag/nodes/generate_retrieval_query.py`:
  `_get_default_error_result` returns an all-default `RetrievalQueryOutput()`
  (empty `search_query`); decide whether that should degrade to a clear
  failure/known sentinel.
