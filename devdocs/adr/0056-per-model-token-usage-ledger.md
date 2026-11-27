# Per-model token usage ledger

- Status: Accepted
- Date: 2026-11-27
- Supersedes: the `usage_metrics` parts of ADR-0032 (that ADR remains a
  historical record and is not edited; so do ADR-0019's flow notes)
- Related: ADR-0019 (shared abstract nodes), ADR-0032 (graph context events
  and state ownership)

## Context

Token usage is currently accumulated in a single thread-level counter:
`BaseGraphSchema.usage_metrics` (`utils_pkg/klea_utils/graph/state.py`), a
`TokenUsage` reduced by `add_token_usage` (`graph/reducers.py`).  Every node
folds its own usage into that one aggregate through
`_update_usage_metrics` (`nodes/abstract.py`), and the context endpoint
(`api/context.py`) projects the summed total back to the frontends.

The aggregate answers "how many tokens did this thread cost" and nothing
else.  It cannot answer "how many tokens did each model cost", which is the
question a benchmarking or cost-attribution consumer asks: a thread may mix
roles (`chat`, `plan`, `guard`) across several providers, and the reducer
deliberately drops `role` because identity is not a quantity.

The per-node `usage` stream event already carries the per-call counters, but
it is live-stream only: it is not persisted, and it is keyed by node, not by
model.  Reconstructing a per-model split from it would mean replaying a
stream that hydration does not keep.

Versioning: `klea_agent` is unreleased, and `klea_utils`/`klea_rag` are 0.x
packages with no backward-compatibility promise.  There is no external
consumer of the checkpointed `usage_metrics` field, and no stored data that
anyone is entitled to keep reading.

## Decision

A single authoritative usage channel, `token_usage_ledger`, replaces
`usage_metrics` outright.

* `TokenUsageLedger` (`utils_pkg/klea_utils/graph/schemas.py`) holds
  `by_model: dict[str, TokenUsage]`, one entry per model that produced a
  call.  `TokenUsage` itself is unchanged and remains the per-model value
  type.  `TokenUsageLedger` is registered beside `TokenUsage` in
  `get_allowed_msgpack_modules()`, so a checkpointed ledger round-trips.
* `total()` folds `by_model` with the existing `add_token_usage` reducer, so
  per-model and thread-level summation cannot drift.  The import is
  function-local because `reducers.py` imports `schemas.py`; a module-level
  reverse import would be circular.
* `total_by_model()` returns a copy of the map, so callers (the context
  endpoint, a future UI) cannot mutate channel state.
* A fresh `TokenUsageLedger()` yields an all-zero `TokenUsage` from
  `total()`, so a new thread and a thread checkpointed before this change
  both project a valid zero total rather than raising or returning `None`.
* `add_usage_ledger` (`graph/reducers.py`) validates both sides with
  `TokenUsageLedger.model_validate(...)` (so dict-shaped snapshot values are
  accepted, matching `add_token_usage`), copies `left.by_model`, and unions
  the right-hand keys, folding each with `add_token_usage`.  Two nodes
  writing different models therefore coexist, and per-key totals are
  additive.
* The ledger key is `BaseLLMNode._effective_model_name()` verbatim -- the
  accessor that already mirrors the model-selection layers
  (`model_defaults` > per-run role override > `_llm_entry.model_name`) and
  returns the provider-prefixed string when known -- with the literal
  `"unknown"` when it is empty.  No new normaliser or prefix synthesis is
  introduced: a second spelling of the model identity would only create keys
  nothing else can reconcile.
* The node writer is renamed `_update_usage_ledger` (called from
  `AbstractLLMNode.execute`) and emits a one-key delta,
  `token_usage_ledger = TokenUsageLedger(by_model={key: token_usage})`, with
  no `usage_metrics` key.  The live `usage` event `details`
  (`_get_usage`) is unchanged; it stays a live-stream projection of the
  node's own calls.
* `BaseGraphSchema.usage_metrics` is deleted and replaced by
  `token_usage_ledger: Annotated[TokenUsageLedger, add_usage_ledger]`.  The
  context endpoint projects `total()` (and may expose `total_by_model()`)
  so frontends keep restoring the session total on hydration.

### Why remove rather than deprecate

Keeping `usage_metrics` as a deprecated mirror would require dual writes in
every node, a second reduced channel in every checkpoint, and a migration
path for data nobody is entitled to keep.  With an unreleased agent and 0.x
shared packages, that is pure tech debt: the cost is paid on every run
forever, and the benefit is zero for a consumer that does not exist.  One
channel, one writer, one reducer.

## Consequences

* Existing checkpoints carry a `usage_metrics` key that no longer maps to a
  state channel.  LangGraph ignores unknown keys on read, so a thread
  checkpointed before this change is still loadable and simply reports no
  token totals; the ledger starts accumulating from its next run.  No
  msgpack deserialisation error arises, because `TokenUsage` is still an
  allowed module and the stale value is dropped, not decoded into a removed
  type.
* Per-model totals are now available in checkpointed state, which is what a
  benchmark or cost-attribution consumer needs.  Rendering them per model in
  the web UI and the TUI is deferred to the backlog; the frontends keep
  showing the thread total seeded from the context endpoint.
* Documentation and tests that name `usage_metrics` must be updated in the
  same change: `docs/api/utils/graph.rst`, `devdocs/system/streams.md`,
  `utils_pkg/tests/test_graph_state.py`, `agent_pkg/tests/test_schemas.py`,
  `rag_pkg/tests/test_schemas.py` and `utils_pkg/tests/test_api_context.py`.
* ADR-0032's state-ownership decision still stands; only its
  `usage_metrics` details are superseded here.  ADR-0019's description of
  the node flow (`_update_usage_metrics`) becomes historical.
