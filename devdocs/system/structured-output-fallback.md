# Structured-output fallback and capability cache

Status: implemented.  Mechanism: `BaseLLMNode._invoke_llm`
(`utils_pkg/klea_utils/nodes/base.py`) plus the helpers in
`utils_pkg/klea_utils/llm.py`.

Last updated: 2026-10-22.

## Scope

Every `BaseLLMNode` with an `output_schema` (planner, picker, evaluator,
reasoning, ...).  It governs how the node recovers when
`with_structured_output(...).ainvoke` cannot produce a usable object.  The
free-text answer nodes (`AnswerFromResults`, `AnswerFromContext`) add a
raw-text recovery step described below.

## The problem

`with_structured_output` is not portable across providers and endpoints:

* some deployments reject the `response_format` / `json_schema` parameter
  outright (a *capability* gap);
* some accept it but return content that fails JSON/schema validation
  (a *per-response* failure - the model emitted malformed or truncated JSON
  on this call, and may succeed next time).

Both used to abort the run.  Both also consumed an extra LLM round-trip on
every node call for an endpoint that will *always* reject the parameter.

## Behaviour

`_invoke_llm` first tries the structured call, then falls back to a plain
invoke.  The plain invoke reuses the same prompt (which already carries the
JSON schema as text) and is parsed tolerantly by `_process_output`, ending at
the node's typed fail-closed default if even that fails.  Both strategies
share one `_invoke_with_retries` loop, so context-overflow, truncation and
empty-output retries and infra-error classification apply uniformly.

`parse_output_with_thought` only retries the parse when `repair_json` returns
non-empty text; for prose it returns `""`, so the original
`OutputParserException` is re-raised instead of being replaced by a second
parse error on the empty string.

Three distinct outcomes, deliberately separated:

1. **Blank structured response** - re-raised so the retry loop's existing
   empty-output budget retries the structured path first
   (`is_empty_structured_parse_error`).
2. **Per-response parse/validation failure** (`ValidationError`,
   `OutputParserException`, `json.JSONDecodeError`) - latch to the plain
   invoke for the remainder of *this* call.  Not cached: the model does
   support structured output, it just produced bad JSON once.
3. **Capability rejection** - the endpoint refuses the parameter.  As (2),
   and *also* recorded in a process-local negative cache so later calls skip
   the structured attempt entirely.

Infra errors (rate limit, auth, model-not-found, timeout) still propagate to
`_invoke_with_retries` and are never misread as structured failures.

## Raw-text recovery for free-text answer nodes

The answer nodes are asked for a single free-text field, but a model can ignore
the JSON envelope and return the answer as prose (this is the normal shape on
the plain-invoke path, where the prompt instruction is advisory).  The JSON
parser then fails even though the prose is perfectly usable.

`_parse_structured_message` still asks the node for its default result on a
parse failure, and the answer nodes override `_get_default_error_result`:

* `AnswerFromResults` (agent) and `AnswerFromContext` (RAG) read the raw
  content via `extract_llm_output_content`, strip a `</think>` thought prefix,
  and use non-blank text as the `answer` field.  RAG leaves `references` empty.
* Text that looks like broken JSON (starts with `{` or `[`) is **not**
  recovered: it is not a user-facing answer.
* `AnswerFromResults` then returns an empty result, so `_update_state` falls
  back to a conditional outcome sentence plus the rendered plan (not a tool
  dump).  `AnswerFromContext` keeps its user-facing retry message.

Other structured nodes (planner, evaluator, router, ...) deliberately keep
their typed fail-closed defaults: prose is not a valid plan or verdict.

## Capability cache contract

* Key: `(provider, model, base_url)`, normalised (trim, lower-case,
  trailing `/` stripped).  The endpoint is part of the identity because the
  same `openai:model` name can point at different deployments with different
  support.
* Lifetime: process-local (`_STRUCTURED_UNSUPPORTED` in `llm.py`).  A
  long-lived server records a rejection once; a restart re-probes once.
  Deliberately not persisted: a stale negative would silently disable
  structured output after a deployment adds support, which is worse than one
  extra probe.
* Only `is_structured_capability_rejection` populates it - a strictened
  subset of `_STRUCTURED_OUTPUT_REJECTED_PATTERNS` that names
  `response_format` / `json_schema` / `structured output`.  The generic
  `400 invalid_request_error` pattern drives the per-call fallback but must
  not poison the cache, since it also matches unrelated bad requests.
* Cleared with `clear_structured_output_cache()` (used by tests).

## References

* `utils_pkg/klea_utils/nodes/base.py` - `_invoke_llm`, `_invoke_with_retries`,
  `_parse_structured_message`.
* `utils_pkg/klea_utils/llm.py` - `parse_output_with_thought`,
  `is_structured_output_failure`, `is_structured_capability_rejection`,
  `structured_output_known_unsupported`, `mark_structured_output_unsupported`.
* `agent_pkg/klea_agent/nodes/answer_from_results.py` and
  `rag_pkg/klea_rag/nodes/answer_from_context.py` - raw-text recovery and
  free-text fallbacks.
* `utils_pkg/tests/test_llm_invoke_retries.py` - fallback, latch and cache
  tests.
* `utils_pkg/tests/test_llm_utils.py` - parser repair and re-raise tests.
* `agent_pkg/tests/test_answer_from_results.py` and
  `rag_pkg/tests/test_answer_from_context.py` - prose recovery and fallback
  tests.
