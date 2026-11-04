---
status: "accepted"
date: 2026-08-31
decision-makers: Ankur Sinha
consulted: opencode (Muse Spark 1.2)
informed: klea contributors
---

# Prompt caching via stable system prefix and forward-stable conversation history

## Context and Problem Statement

Klea's RAG runs 4 LLM calls per turn (`classify_question`, `generate_retrieval_query`, `answer_from_context`, `evaluator` via `BaseLLMNode`). Each rebuilds a `ChatPromptTemplate` from a node-local `system` (`load_prompt` + `output_schema` + `filter_fields`) plus per-turn volatile state: `context_summary` (`SummariseMemoryNode` threshold `10_000` -> `2k`), `recent` `BaseMessage` window (`get_recent_messages` `10_000` chars, last N), `reference_material` (`20k` `truncate_reference_material`), and `tool_results` (`2500` per tool). Providers (`OpenAI` `prompt_cache`, `Anthropic` `prompt_caching`, `vLLM`/`ollama` KV-cache) reuse KV blocks for a **byte-identical prefix** `(model, system+human)` until the first differing token; today `system` contains `context_summary` *before* `output_schema` and `recent` is a *sliding* last-N (`messages[-N:]`), so every turn busts the prefix even though `RAG` must send the whole `20k` `reference_material` anyway. At `23k` per `RAG` turn (`3k` system + `20k` human), a `2-3k` `system` hit per node across turns is `~13%` saving for `100` chats x `5` loops.

How can the framework keep `system` cacheable across turns while preserving faithful `RAG` context (full `reference_material` must still be sent)?

## Decision Drivers

* Faithfulness: `RAG` must still send full `reference_material` and `tool_results` per query; not negotiable.
* Cache hit rate: `system` should be stable for `5m` `TTL` (`Anthropic`/`OpenAI` `5-10m`) across turns of the same `(user_id:chat_id, model)` thread; `human` (`query` + `reference_material`) is per-query miss as intended.
* Simplicity: One `SystemMessage` prefix, not per-node bespoke `cache_control` plumbing in every node.
* No `0.2` churn: change must be a `BaseLLMNode` shard, not a graph rewrite.

## Considered Options

* **A. Stable `system` prefix, volatile suffix in `human` (chosen).** Order `system` `load_prompt -> output_schema -> context_summary -> recent` by stability; `context_summary` stays in `system` as tail  ---  prefix cache hits until first differing token, so tail volatility only costs its tail.
* **B. Whole `system+human` as one cached prefix.** Put `reference_material` in `system` to cache `20k`  ---  only hits on exact repeated `query_domains`+`retrieved docs` (rare, `truncate` round-robin makes exact repeat unlikely).
* **C. No cache, keep `last N` sliding.** As shipped (`get_recent_messages` `10k` sliding `messages[-N:]`), whole `23k` recomputed each turn; simplest but `0%` hit.

## Decision Outcome

Chosen option: **A**, because it gives a `2-3k` `system` hit per node across turns on the same `https://...` `model` (per-node cache entries under one model) without changing `RAG` faithfulness  ---  `human` (`reference_material` + `query`) stays miss as required.

Implementation:

* `BaseLLMNode._get_system_prompt` reorder `system` by stability (`load_prompt` → `output_schema` → `context_summary` tail); `recent` stays as real `BaseMessage`s after `system`, so `system` prefix is cached, `recent` is miss.
* `BaseLLMNode.get_recent_messages` → `get_last_n_conversations` via `RAGState.summarised_till` (forward-stable): `recent = messages[max(0, summarised_till - N):]` (`N=2` overlap) with fallback `if summarised_till not in state → all messages` and skip `SummariseMemoryNode`; remove `num_history_chars` from `BaseLLMNode`.

## Consequences

* Good, because `2-3k` `system` per `classify`/`generate` hits for `5` turns until `SummariseMemoryNode` jumps; `human` `20k` still miss per-query as required for faithfulness; no graph rewrite.
* Good, because `recent` forward via `summarised_till` keeps prefix stable for `5` turns vs `last N` which drifts every turn; becomes `~3k` hit + `20k` miss per `RAG` turn.
* Bad, because `TTL` `5m` still expires on idle chats; local `ollama` has no billing cache, only `vLLM` KV reuse.

## Confirmation

* `pytest` `not localonly` plus `localonly` graph run with `cache_control` mocked and `cached_tokens >0` on turn 2 `classify_question` `system` hit.
* Manual: `RAG` `domain_query` x2 identical `query_domains`+`context_summary` -> second `classify` `cached_tokens` `~2k`, `answer_from_context` `human` miss `20k`.

## Pros and Cons of the Options

### A. Stable system prefix, volatile suffix

* Good, because `system` `load_prompt+output_schema` is deployment-stable and cacheable for `5m` across turns.
* Good, because `recent` forward via `summarised_till` keeps prefix stable vs `last N` sliding miss.
* Bad, because `context_summary` at `system` tail still busts `output_schema`'s cache on `summarise` every `~5` turns (mitigated by putting `output_schema` *before* `memory`).
* Neutral, because `tool_results` per-tool remains `human` miss (as intended).

### B. Whole system+human cached

* Good, because `20k` `reference_material` could be cached on exact repeated query.
* Bad, because `truncate` round-robin makes exact `20k` repeat rare; `0%` hit in practice.

### C. No cache

* Good, because simplest, no provider branching.
* Bad, because `0%` hit, pays full `23k` per turn.

## More Information

* Current `truncate` round-robin and `textualize` per-tool already bound `human` to `20k+5k`; `ContextVar` isolates per-chat `model`.
* Deferred: `tool_results` unbounded -> already per-tool; `reference_material` must stay `human`, not `system`.
* Revisit on `v0.5` when provider `prompt_caching` pricing stabilizes.
* Unmeasured: extracting the volatile `context_summary` / `Model in use:` line
  out of the single cached, tagged block is expected to pay only on Anthropic
  and only under frequent compaction.  Tracked as a deferred A/B benchmark in
  `devdocs/backlog.md`; no assembly change before it is measured.
