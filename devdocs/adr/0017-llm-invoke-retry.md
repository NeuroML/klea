---
status: "accepted"
date: 2026-08-28
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# LLM invoke retry and token-window adaptation

## Context and Problem Statement

Every ``BaseLLMNode`` ``ainvoke`` can fail in three families:
transient (``429`` rate-limit, ``5xx``, ``TransportError`` /
``TimeoutError``), client error (other ``4xx``), and context-window
issues (``request too large`` overflow or ``finish_reason == "length"``
truncation).  HuggingFace, Ollama, OpenAI-compatible, and custom
endpoints behave differently (Ollama ``/api/show`` vs HuggingFace
``/models`` for limits, HTTP vs streaming, ``max_tokens`` vs
``max_output_tokens`` param names), and the token budget must be
respected (input + output within ``max_model_len``).  Crucially,
HuggingFace requires an explicit ``max_tokens``; without it the
provider reserves the *entire* remaining context window as output,
reporting an apparent ``~1M`` output window and causing rate-limiting
because the full budget is accounted for on every call (observed in
``.agents/2026-08-03.md`` Session 2).  Naively retrying with a fresh
full-size output window on every failure either blows the ``vLLM``
KV-cache (exponential ladder) or oscillates around ``max_model_len``.

How should Klea retry invocations, classify errors, and grow/shrink
the output window while ensuring HuggingFace never sees an unbounded
``max_tokens``?

## Decision Drivers

* HuggingFace requires a finite ``max_tokens`` on every invoke; leaving
  it unset makes the provider reserve the whole remaining context window
  as output (≈ ``context - input``), which inflated the reported output
  window to ``~1M`` and triggered rate-limit accounting for the full
  budget on every call.  Every invoke must therefore be bounded even
  when the model advertises a large context.
* Transient ``429``/``5xx``/``TransportError``/``TimeoutError`` should
  be retried; other ``4xx`` should fail fast.
* HuggingFace applies a *total* budget (``input + output <= context``);
  most native providers apply a separate output ceiling.  The always-
  bounded window (``resolve_output_token_limit``) clamps to ``catalog
  max_model_len - estimated input *1.05`` so HF never sees an unbounded
  ``max_tokens``.
* Truncation (output cut off a few hundred to thousands of tokens short)
  is common; the fix is to grow the reserved output window, but not
  exponentially (spikes ``vLLM`` KV-cache).
* Overflow (``input + output`` exceeds context) should shrink the
  output reservation to free headroom.  Bounded window + catalog clamping
  is what prevents the original ``~1M`` misreport.
* Model limits must be discovered dynamically: catalog
  ``models.dev`` ``max_model_len`` / provider ``api_key_env`` plus
  per-endpoint ``GET {base_url}/models`` probe (``probe_endpoint_model_limits``
  ``12h`` TTL).

## Considered Options

* **A. Simple tenacity ladder with full output window** -- ``AsyncRetrying``
  with the same ``max_tokens`` on every retry.  Rejected: overflow
  retries never shrink, truncation retries never grow, so the same
  failure repeats; exponential doubling of the output window spikes
  ``vLLM`` ``max_tokens`` reservations.
* **B. Two retry families + window ladder (chosen)** -- ``llm.py:
  classify_llm_invocation_error`` maps ``TransportError``/``TimeoutError``/``
  429``/``5xx`` -> ``RETRYABLE``, other ``4xx`` -> ``CLIENT_ERROR``,
  overflow vs truncation -> separate budget adjustments.  Overflow retries
  (``MAX_CONTEXT_OVERFLOW_RETRIES = 3``) shrink the reserved output
  window (down to ``MIN_OUTPUT_TOKENS = 64``); truncation retries
  (``MAX_TRUNCATION_RETRIES = 15``) grow it in two bounded phases
  (linear ``TRUNCATION_LINEAR_STEP = 2048`` up to ``TRUNCATION_LINEAR_CAP
  = 16384``, then fixed ``TRUNCATION_PHASE2_STEP = 32768``) up to
  ``MAX_OUTPUT_TOKENS_CEILING = 32768`` (or the endpoint's remaining
  context via ``_jump_output_target``).  Growth is always clamped by
  ``resolve_output_token_limit`` (catalog ``max_model_len`` - estimated
  input ``*1.05``).  Endpoint-aware probing raises the ceiling to the
  advertised ``max_model_len`` (e.g. ``262144``) when available.
* **C. Single exponential ladder for all errors** -- one ladder for both
  overflow and truncation (double window).  Rejected: truncation from a
  heavy-thinking model (tens of thousands of thinking tokens plus a tiny
  answer) would oscillate around the context ceiling and burn budget on
  a failing path.

## Decision Outcome

Chosen option: "B. Two retry families + window ladder with catalog +
endpoint limits".

* Classification: ``llm.py:34`` ``LLMInvocationErrorCategory``
  (``RETRYABLE``/``CLIENT_ERROR``/``OVERFLOW``/``TRUNCATION``) via
  ``classify_llm_invocation_error``.  ``nodes/base.py`` uses
  ``tenacity.AsyncRetrying`` with ``retry_if_exception`` checking the
  category, so ``4xx`` fail-fast while ``429``/``5xx`` retry.
* Token math: ``estimate_input_tokens(chars // 4)``; ``resolve_output_token_limit``
  bounds the reserved output to context minus input estimate ``*1.05``;
  ``resolve_langchain_endpoint`` (via private ``_ConfigurableModel._model(config)``
  cycling ``openai_api_base``/``api_base``/``endpoint``/``anthropic_api_url``)
  supplies the per-invoke ``base_url`` for probing.
* Catalog + endpoint: ``models_catalog.py:85`` ``get_catalog_model_limits`` /
  ``probe_endpoint_model_limits`` (``GET {base_url}/models`` ``max_model_len``
  + provider ``api_key_env``) with 12h in-memory cache; fallback to
  ``models.dev`` when the endpoint does not advertise limits.  Shared by
  ``BaseLLMNode`` before each ``_jump_output_target``.
* Ladder: ``nodes/base.py:52`` ``MAX_CONTEXT_OVERFLOW_RETRIES``,
  ``MAX_TRUNCATION_RETRIES``, ``TRUNCATION_LINEAR_STEP``/``CAP``/
  ``TRUNCATION_PHASE2_STEP``, ``MAX_OUTPUT_TOKENS_CEILING``.  Growth is
  always clamped; fallback when context is unknown is
  ``MAX_OUTPUT_TOKENS_CEILING`` mirrored from ``opencode``'s
  ``OUTPUT_TOKEN_MAX`` per ``nodes/base.py:86`` comment.
* Wiring: ``nodes/abstract.py:218`` ``AbstractLLMNode`` template still
  owns the per-node ``@final execute`` (pre-check -> prompt -> invoke ->
  post-stream); the retry/window ladder lives in ``nodes/base.py``'s
  ``BaseLLMNode`` subclass (file-based prompt loading + memory), so the
  orchestrator (ADR-0016) and node hierarchy (ADR-0019) both delegate to
  this ladder without forking.

### Consequences

* Good, because transient errors (rate-limit, server ``5xx``, socket
  timeout) are retried while client bugs (``4xx``) fail fast; the two
  families are a single source of truth via ``classify_llm_invocation_error``.
* Good, because the two-phase linear ladder handles heavy-thinking
  models (tens of thousands of tokens before the answer) without KV-cache
  spikes: linear phase covers the common few-hundred-to-few-thousand
  shortfall predictably, fixed ``32768`` phase covers the heavy tail,
  both bounded by the endpoint/catalog ceiling (e.g. ``262144``).
* Good, because overflow vs truncation are opposite directions (shrink
  vs grow) and are now separated, so retries do not oscillate around
  the context ceiling.
* Bad, because ``estimate_input_tokens`` is a simple ``chars // 4``
  heuristic (``*1.05`` overhead) rather than the tokenizer's exact count;
  tighter estimates would require tokenizer access per node.
* Bad, because endpoint probing relies on ``GET {base_url}/models``
  advertised ``max_model_len`` and the private
  ``_ConfigurableModel._model(config)`` internals to discover
  ``base_url`` -- brittle to LangChain API changes.

### Confirmation

* ``utils_pkg/tests/test_llm_model.py`` covers
  ``classify_llm_invocation_error`` (``429``/``5xx`` -> ``RETRYABLE``,
  other ``4xx`` -> ``CLIENT_ERROR``, overflow/truncation categories),
  ``resolve_output_token_limit`` bounds, and ``api_key ->
  huggingfacehub_api_token`` mapping.
* ``rag_pkg/klea_rag/nodes/evaluator.py`` / ``generate_retrieval_query.py``
  still exercise the ladder via ``BaseLLMNode`` template (``_invoke_llm``
  must be ``await ainvoke`` for streaming callbacks).
* ``ty`` extra-paths for ``llm.py``/``nodes/base.py``/``models_catalog.py``;
  ``ruff`` clean for the three modules; ``docs: make html`` renders the
  same pipeline figure.

## Pros and Cons of the Options

### Two retry families + window ladder with catalog + endpoint (chosen)

* Good, because transient retries vs client fail-fast are explicit
* Good, because two-phase linear ladder bounds ``vLLM`` reservations
* Bad, because ``chars // 4`` heuristic and private ``_model(config)``
  endpoint discovery are approximations

### Simple full-window ladder

* Good, because zero window math
* Bad, because overflow/truncation repeat the same failure; KV-cache
  spikes

## More Information

* Code: ``utils_pkg/klea_utils/llm.py`` (``parse_model_name``,
  ``classify_llm_invocation_error``, ``estimate_input_tokens``,
  ``resolve_output_token_limit`` -- always-bounded ``max_tokens`` for
  HuggingFace -- ``resolve_langchain_endpoint``), ``nodes/base.py:52``
  (``MAX_*``/``TRUNCATION_*`` constants + ``_invoke_llm`` ladder),
  ``models_catalog.py:85`` (catalog + endpoint probe, 12h cache),
  ``nodes/abstract.py:218`` (``AbstractLLMNode`` template still owns the
  per-node ``@final execute``).
* Related: ``ADR-0016`` (``BaseLangGraph`` that runs the nodes that use
  this ladder), ``ADR-0019`` (abstract node hierarchy this ladder
  extends), ``ADR-0013`` (inspection stream that shows ``usage`` per
  node), ``AGENTS.md`` workflow (verification via ``ty``).
* Commits: ``5ab1812`` / ``35df92d`` (retry handling), ``dc61164`` (window
  growth to 2-phase linear), ``fbde555`` (``max_token`` handling),
  ``914c6c0`` / ``9c69dbf`` (catalog + endpoint probing), ``e371a81``
  (memory threshold characters), ``2026-08-03.md`` Session 2
  (``.agents/2026-08-03.md:49`` HuggingFace whole-window reservation and
  ``max_tokens`` rate-limit cause; always-bounded fix in
  ``resolve_output_token_limit``).
* Codified ``2026-08-28``; ladder hardened ``2026-08-18..21`` during the
  LLM-retry + catalog hardening sprint; always-bounded HuggingFace fix
  from ``2026-08-03`` Session 2.

## Update (2026-10-19): larger first-attempt output budgets

The retry ladder stays, but it should be the exception rather than the normal
path: each truncation retry costs a full model round-trip, which dominates the
loop's latency.  The first-attempt defaults are therefore larger:

* ``DEFAULT_MAX_OUTPUT_TOKENS = 32768`` and the ``chat``/``plan``/
  ``tool_picker`` roles default to it (``guard`` stays at 2048).
* ``MAX_OUTPUT_TOKENS_CEILING = 65536``, so a truncated response still has
  headroom to grow.
* HuggingFace keeps the conservative
  ``HUGGINGFACE_MAX_OUTPUT_TOKENS = 16384`` because its total-budget
  accounting penalises large reservations (the original rate-limit cause);
  the grow ladder is primarily for that provider.  Other providers do not
  rate-limit larger input/output requests, so they get the larger default
  and usually complete on the first attempt.

Explicit ``max_tokens``/``max_output_tokens`` and per-provider
``providers.<provider>.<role>`` config still win; catalog output and
context-headroom clamps still bound every value.
