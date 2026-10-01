---
status: "accepted"
date: 2026-10-01
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Per-run NodeContext replaces shared LLM node state

## Context and Problem Statement

Node instances are singletons: a graph builds one instance per node and
`LangGraph` may run several graphs (threads) through the same instances
concurrently.  `AbstractLLMNode.execute` stored every per-run value on
`self` -- `_last_state`, `_last_human_prompt`, `_last_system_prompt`,
`_last_template`, `_last_variables`, `_last_prompt`, `_last_llm`,
`_last_config`, `_last_output`, `_last_result`, `_last_state_updates`,
plus `_validation_feedback`, `_token_usage`, `_final_state` and the
per-run dynamic `_output_schema`.  Streaming and inspection hooks
(`_get_inspect`, `_get_status`, `_post_exec_stream`) read those fields
back.

Two concurrent runs through one instance therefore interleave: one run's
snapshot can be observed (and overwritten) by another.  The
`_last_*` fields are not persisted state -- they are internal scratch
local to a single `execute` call -- so they do not belong on the shared
instance.

## Decision Drivers

* Correctness under concurrent runs: per-run scratch must not be shared.
* Preserve the Template Method (`execute`) contract and the existing hook
  surface; no behaviour change for a single run.
* Keep a uniform, typed interface for node authors: a hook that needs
  per-run values must receive them explicitly.
* Do not duplicate graph `state`: it is already an explicit argument to
  the hooks that need it.
* Preserve prompt caching (ADR-0028) and the picker's dynamic schema
  (ADR-0044).

## Considered Options

* Keep `_last_*` instance state (status quo).
* Thread individual per-run values to each hook as separate arguments.
* Introduce a single per-run context object passed to every hook.
* Store per-run scratch in graph state.

## Decision Outcome

Chosen option: "Introduce a single per-run context object passed to every
hook", because it removes the shared mutable state with one uniform,
typed parameter while keeping `state` a separate explicit argument.

`klea_utils/nodes/context.py` defines:

* `NodeContext` -- base per-run scratch (deliberately has no `state`
  field).
* `LLMNodeContext[TOutput]` -- resolved schema, prompt pipeline
  (`system_prompt`, `human_prompt`, `template`, `variables`, `prompt`),
  invocation (`llm`, `config`, `output`, `result`), `validation_feedback`,
  `state_updates`, `final_state`, `token_usage`.
* `ToolCallerContext` -- tool results and display flags.

`execute` constructs `ctx` for the run and passes it as the final
positional argument to every overridable hook:

* lifecycle/streaming: `_pre_exec(state, ctx)`, `_pre_exec_stream(ctx)`,
  `_post_exec_stream(state, ctx)`, `_get_inspect(state, ctx)`,
  `_get_status(state, ctx)`;
* schema/prompt: `_get_output_schema(state, ctx)`,
  `_get_human_prompt(state, ctx)`, `_get_system_prompt(state, ctx)`,
  `_create_prompt_template(system, human, ctx)`,
  `_get_prompt_variables(state, ctx)`,
  `_invoke_prompt(template, variables, ctx)`;
* invoke/process: `_configure_llm(ctx)`, `_invoke_llm(ctx)`,
  `_process_output(ctx)`, `_get_default_error_result(ctx)`,
  `_extract_usage(ctx)`, `_get_usage(ctx)`;
* result: `_validate_result(result, state, ctx)`,
  `_update_state(result, state, ctx)`.

The picker's per-run schema is resolved with
`ctx.output_schema = self._get_output_schema(state, ctx)` and consumed
from `ctx` (schema block, `with_structured_output`, parsing,
`_is_empty_result`, default error result).  `_update_output_window` /
`_jump_output_target` receive the prompt explicitly instead of reading a
`_last_prompt` attribute, threading it from `_invoke_with_retries`.

Node instance attributes are configuration only: they are set once in
`__init__` (or a one-shot `set_*` config injector / `@property` setter)
and never mutated by `execute` or a hook.  This is enforced by an
architecture test (`utils_pkg/tests/test_node_state_hygiene.py`), which
parses every class under the three `nodes/` packages and fails on a
`self.<attr> = ...` assignment outside those construction-time methods.

### Consequences

* Good, because concurrent runs on shared instances no longer clobber
  each other's scratch.
* Good, because `state` stays explicit and is not duplicated into the
  context.
* Good, because the hook interface is uniform: a per-run value is always
  reachable via the final `ctx` argument.
* Good, because the picker's dynamic schema no longer round-trips through
  a shared `_output_schema` attribute.
* Bad, because every hook signature and its overrides/call sites changed
  (a large, mechanical change across `utils_pkg`/`agent_pkg`/`rag_pkg`).
* Neutral, because concrete overrides type `ctx` as `Any` where they do
  not need the generic context type.

### Confirmation

* `utils_pkg/tests/` node, picker, caller, retry, and graph tests pass.
* `agent_pkg/tests/` and `rag_pkg/tests/` pass.
* `utils_pkg/tests/test_node_state_hygiene.py` enforces that node classes
  assign `self.<attr>` only at construction time.
* `ty check` reports the pre-existing baseline only; `ruff check`/`format`
  clean in all packages.

## More Information

Related: ADR-0019 (shared abstract nodes), ADR-0028 (prompt cache
ordering), ADR-0033 (thread-isolated state), ADR-0044 (dynamic tool-call
schema).  The context lives in `utils_pkg/klea_utils/nodes/context.py`;
the Template Method is `AbstractLLMNode.execute` in
`utils_pkg/klea_utils/nodes/abstract.py`.  The previous backlog item
"shared-instance `_last_*` snapshot leak" is resolved by this decision.
