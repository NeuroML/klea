---
status: "accepted"
date: 2026-10-01
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Strict-safe dynamic tool-call schema for the tools picker

## Context and Problem Statement

The tools picker (`klea_utils/nodes/tools_picker.py`) asked the model for a
generic structured output: a `tool_calls` list whose entries were
`{tool: str, args: dict[str, Any], reason: str, step: int}`.  The
`args` field is a free-form object (its keys are the chosen tool's
parameters, which vary per tool).

That shape is incompatible with **strict structured output**.  Every
provider's strict mode requires objects to be closed
(`additionalProperties: false` with declared `properties`).  A free-form
object has no declared properties, so it is rewritten to an object that may
contain nothing:

* Anthropic (`anthropic.transform_schema`): `dict[str, Any]` and
  `dict[str, str]` both become
  `{"type": "object", "properties": {}, "additionalProperties": false}`.
* OpenAI (`convert_to_openai_tool(..., strict=True)`):
  `{"additionalProperties": false, "type": "object"}`.

The model is then grammar-constrained to emit `args: {}`; it cannot bind
arguments at all.  Observed live: Opus and Haiku both emitted `args: {}`
on every picker call, even while writing the intended parameters into
`reason` (for example `read_file` with no `path`).  The picker retried, the
planner replanned, and the run exhausted its revision budget.  Weaker
models are not the cause - the output contract was unsatisfiable.

## Decision Drivers

* The picker's output must be usable under strict structured output on
  every provider, and must not silently degrade to empty arguments.
* Per-tool required parameters should be **schema-enforced**, not merely
  requested in the prompt.
* The tool set is dynamic (access level, and RAG's per-query domains), but
  the output schema is embedded in the system prompt, so it must be stable
  for a given run/config to preserve prompt caching (ADR-0028).
* The picker's architecture is preserved: the planner owns tool identity,
  the picker binds arguments, calls are batched with a per-call `step` and
  `reason`, and "no suggested tool fits" escalates to the planner.
* The dispatch/caller contract (`ToolCallSchema`, checkpointed state) must
  not change, and dynamic classes must not enter graph state.

## Considered Options

* Keep the free-form `args` object and rely on the prompt / prompt-parse
  fallback.
* Make `args` a strict-safe list of `{name, value}` pairs.
* Make `args` a JSON-encoded string.
* Build a **dynamic per-tool discriminated union** as the picker's output
  schema.
* Use native tool calling (`bind_tools`) with the real tool schemas.

## Decision Outcome

Chosen option: "Build a dynamic per-tool discriminated union", because it
is the only option that is simultaneously strict-safe, provider-agnostic,
and schema-enforcing of each tool's required parameters, while keeping the
rest of the picker contract unchanged.

The picker overrides `_get_output_schema(state, ctx)` to build, per run, a
schema from the **same disclosed tool set** as the prompt descriptions:

```
ToolPickerOutput = {tool_calls: list[ToolCall]}
ToolCall         = {step: int, reason: str, call: <union>}
<union>          = Annotated[Union[<tool_1>_call, ..., NoTool],
                              Field(discriminator="tool")]
<tool>_call      = {tool: Literal["<name>"], <param>: <type>, ...}
NoTool           = {tool: Literal["no_tool"], reason: str}
```

* Parameter types are mapped strict-safely: `string`/`integer`/`number`/
  `boolean` directly; `anyOf` (including `T | null`) to a union; arrays of
  supported types to `list[...]`; object/unknown parameters to a
  JSON-encoded `str`.
* Required vs optional follows the schema's `required` and each property's
  `default` (optional with no default becomes `T | None`).
* Scope is the disclosed set (access-level filtered; RAG domain-filtered),
  not the per-step `suggested_tools`, so the schema embedded in the system
  prompt stays stable for the run/config and prompt caching is preserved.
  The per-step tool restriction remains prompt-only, as before.
* Models are built in a deterministic (sorted) order and cached by a
  canonical tool signature, so the serialized schema is byte-stable.
* `NoTool` preserves the "cannot carry out the step; replan" signal;
  `_update_state` normalizes each union branch (and `NoTool`) back to the
  stable `ToolCallSchema` used by the caller/triage/state layers.
* The generated per-tool models carry a concrete `json_schema_extra`
  example, and `_schema_to_example` now prefers `examples`, resolves
  `$ref`, and picks an `anyOf`/`oneOf` branch (previously it emitted
  `null` for a union field).

### Consequences

* Good, because arguments bind correctly on every provider (verified live
  with Opus and Haiku in strict Anthropic mode).
* Good, because each tool's required parameters are enforced by the schema.
* Good, because the caller/dispatch/triage/checkpoint contract is
  unchanged; the dynamic classes never enter graph state.
* Good, because caching is preserved (same scope and stability as the
  existing tool-description block).
* Bad, because the schema is larger (a branch per tool).
* Bad, because a provider whose strict mode rejects `anyOf` falls back to
  prompt-parse (the schema still guides the prompt).
* Neutral, because Anthropic drops `const`/`discriminator` (the tag becomes
  a description) but keeps each branch's `required`, so the discriminator
  is not strictly enforced while required parameters are.
* Good, because the per-run schema is resolved into the run's
  `NodeContext` (`ctx.output_schema`) and no longer round-trips through a
  shared `_output_schema` attribute (ADR-0045).

### Confirmation

* `utils_pkg/tests/test_call_schema.py` - builder unit tests (required/
  defaults, `T|null`, arrays, object fallback, `NoTool`, determinism,
  no open objects, OpenAI/Anthropic converter acceptance).
* `utils_pkg/tests/test_nodes_tools_picker.py` - the picker builds and
  consumes the union; `NoTool` normalizes to the empty-tool signal.
* `utils_pkg/tests/test_node_base_schema.py` - `_schema_to_example` unions/
  refs/examples.
* Live end-to-end (Opus, Haiku) - parameters appear on the call with no
  `args`.

## Pros and Cons of the Options

### Keep the free-form `args` object

* Bad, because strict structured output closes it to `{}` on Anthropic and
  OpenAI, silently dropping every argument.

### `args` as a list of `{name, value}` pairs

* Good, because it is strict-safe.
* Bad, because it does not enforce per-tool required keys (`args: []` is
  valid), and it loses value typing (values are strings).

### `args` as a JSON-encoded string

* Good, because it is strict-safe and grammar-enforced.
* Bad, because nested JSON must be escaped inside a string, which is
  error-prone.

### Dynamic per-tool discriminated union (chosen)

* Good, because parameters are real typed fields and required keys are
  enforced.
* Good, because it keeps the picker's batch/`step`/`reason`/`NoTool`
  contract and the stable dispatch type.
* Bad, because building per-tool models from JSON Schema is more code and
  the schema is larger.

### Native tool calling (`bind_tools`)

* Good, because the provider binds each tool's own schema.
* Bad, because it needs forced tool choice (unsupported for some models /
  disabled with thinking), cannot carry `step`/`reason`, and would retire
  the picker's separate role (ADR-0020/0041) - a redesign, not a fix.

### Update (2026-10-02)

The dynamic-key collapse is not picker-specific: it applies to **every** LLM
output schema.  The same defect was found in the Evaluator's verdict map
(`evaluations`, keyed by step number) and the RAG query generator's `filters`
(both silently `{}` on Anthropic).  `EvaluationSchema` now uses a typed
`list[StepEvaluation]` (each carrying `step_number`), and the RAG query
generator builds a per-run schema from its configured filter fields
(`klea_utils.stores.query_schema.build_retrieval_query_schema`, overriding
`_get_output_schema`).  The general rule and its architecture guard now live in
`devdocs/system/prompt-conventions.md` (Rule 7) and
`utils_pkg/tests/test_schema_hygiene.py`.

## More Information

Related: ADR-0020 (unified tool caller), ADR-0028 (prompt cache ordering),
ADR-0035 (general agent path), ADR-0041 (plan step granularity and
parallelism), ADR-0037 (tool access levels).  The dynamic output schema is
resolved by `BaseLLMNode._get_output_schema`; the builder lives in
`klea_utils/mcp/call_schema.py`.  The picker prompt (`ToolsPicker_system.md`)
was updated so parameters are fields on the call and the "cannot carry out"
signal is the `no_tool` branch.
