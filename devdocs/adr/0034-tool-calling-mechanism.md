---
status: "proposed"
date: 2026-09-09
decision-makers: Ankur Sinha
consulted: "opencode sources, aider benchmarks and edit-format docs, Cline system prompt docs, vLLM and TGI serving docs"
informed: ""
---

# Tool calling mechanism: prompt injection with structured emission

## Context and Problem Statement

ADR-0020 unified the tool-selection step (``ToolsPicker``) and the
tool-execution step (``ToolsCallerNode`` + ``dispatch_tool_calls``) into
shared ``klea_utils`` nodes serving both ``klea_rag`` and ``klea_agent``.
The picker advertises the available tools to the LLM as text (per-domain
``ToolInfo`` descriptions built by ``BaseLangGraph._build_tools_info``
from the fastmcp client) and consumes the LLM's answer as a validated
``ToolCallsSchema``.  With the general coding agent now being designed,
the same mechanism will serve its tool-emitting nodes.

An alternative exists in the wider ecosystem: provider-native tool
calling, where tool definitions ride a dedicated API field (``tools``
for OpenAI/Anthropic; ``bind_tools`` in langchain; AI SDK
``streamText(tools=...)`` in opencode) and the model's tool-call
signature is parsed from a structured response field.  langchain's
``bind_tools`` is the obvious candidate API for our stack.

Question: should tool signatures be emitted via provider-native tool
fields (adopting ``bind_tools`` and/or a converter for MCP tools), or
via prompt injection with structured-output parsing (the house
pattern)?  The decision applies to every LLM node in both graphs that
emits tool signatures.  It does not decide which tools exist
(ADR-0004) or how they are permission-gated (ADR-0007).

Evidence collected 2026-09-09:

* langchain-core 1.6.2: ``BaseChatModel.bind_tools`` raises
  ``NotImplementedError`` (``language_models/chat_models.py:2366``);
  it is entirely per-integration.  langchain-openai's implementation
  converts specs via ``convert_to_openai_tool`` (requiring
  ``BaseTool``/pydantic/JSON-schema-dict inputs) and passes them as
  OpenAI's ``tools`` API field, with the docstring "Assumes model is
  compatible with OpenAI tool-calling API"
  (``chat_models/base.py:2413``).
* opencode (reference coding agent) passes AI SDK ``tool()`` objects
  to ``streamText(tools=...)`` (``packages/opencode/src/session/
  tools.ts:99``, ``session/llm.ts:318``); its provider adapters
  serialise them into each provider's native ``tools`` field.  There
  is no prompt-injection fallback path anywhere in the codebase;
  model capability is tracked socially via the models.dev catalog
  (``toolcall`` flag, advisory, default true;
  ``provider/provider.ts:1524``).
* Aider's code-editing benchmark measured plain-text edit formats as
  more reliable than OpenAI's function-calling API across GPT-3.5/4
  models: complex output formats degrade both code quality and format
  adherence (aider.chat/docs/benchmarks.html).
* Cline (production agent) defines tools as a section of the system
  prompt and parses tool usage from the model's response text --
  prompt injection at production scale, across all models.
* Serving stacks are heterogeneous behind the same "OpenAI-compatible"
  shape: vLLM requires server-side opt-in (``--enable-auto-tool-choice
  --tool-call-parser <name>``) with per-model-family parsers; TGI
  implements tool/JSON support via grammar-guided constrained decoding
  (outlines/guidance).  A missing or unconfigured capability can fail
  loudly (4xx from OpenAI/Anthropic/Ollama for tool-less models) or
  silently (lax OpenAI-compatible servers drop the unknown ``tools``
  field, the model never sees tools, and no error exists anywhere).
* Token cost is not a differentiator between the two routes: the
  native field also charges input tokens for the definitions and adds
  hidden provider tool-use instruction overhead on top (Anthropic
  inserts tools and instructions into the system prompt); compact
  text descriptions are denser than the full JSON schemas the native
  route transmits; and both routes emit JSON tool arguments with the
  same string-escaping output penalty.

## Decision Drivers

* Provider portability: ``create_configurable_model`` spans
  OpenAI-compatible endpoints, HuggingFace inference, and ollama; the
  ``tools`` field cannot be assumed to exist or be honoured, and
  silent dropping is indistinguishable from "model chose not to act".
* MCP tool surface: tools arrive as fastmcp ``Tool`` objects surfaced
  as ``ToolInfo`` descriptions; there is no ``BaseTool``/pydantic
  conversion layer in our stack (``langchain-mcp-adapters`` is
  declared in ``agent_pkg/setup.cfg`` but currently unimported);
  adopting it or writing one is a new code path for no functional
  gain -- in both designs the tool descriptions end up as text in the
  model's context, the only difference is who injects and who parses
  (server vs application).
* Single emission mechanism: ADR-0020's unified
  ``ToolsPicker``/``ToolCallsSchema`` is the shared source of truth
  for RAG and agent; a second, parallel emission path would fork
  parsing, validation, fallback, and inspection (ADR-0013) logic.
* Small-model robustness: CI runs ``qwen3:0.6b``; failures must
  surface uniformly through the existing structured-output fallback
  and retry machinery (ADR-0017).
* Unavoidable dependency: in every design the model must emit a
  parseable format; the wire format only moves the parse boundary.

## Considered Options

* **A. Provider-native tool fields** -- adopt ``bind_tools`` (with an
  MCP-to-tool-spec conversion) so tool definitions ride the provider's
  ``tools`` API field and signatures come back as structured tool
  calls.
* **B. Prompt injection + structured emission** -- advertise tools as
  text (existing ``tools_info`` descriptions), model emits
  ``ToolCallsSchema``, parsed and validated by the existing
  structured-output machinery.
* **C. Hybrid** -- try native fields when the provider supports them,
  fall back to injection otherwise.

## Decision Outcome

Chosen option: **B. Prompt injection + structured emission**, because
it is the only mechanism that works uniformly across the inference
stacks ``create_configurable_model`` supports -- including endpoints
without any tool API and endpoints that silently ignore the ``tools``
field -- it reuses the ADR-0020 shared path unchanged, and it adds no
new dependency or converter.

### Consequences

* Good, because it works on every endpoint ``create_configurable_model``
  supports: text injection needs no server-side feature.
* Good, because a single emission/parse path (ADR-0020) serves RAG,
  the agent's picker nodes, and the future coding-loop nodes; fallback,
  retry, and inspection stay unified.
* Good, because server-side grammar-constrained decoding (vLLM
  ``structured_outputs``, TGI guidance) improves schema adherence when
  present -- a provider-conditional bonus, never a dependency.
* Good, because failures (malformed JSON, unknown tool names) surface
  as parse/validation errors that the existing structured-output
  fallback and retry machinery handle uniformly, and can be fed back
  to the model for repair (e.g. opencode's ``invalid`` tool pattern,
  noted as future polish).
* Bad, because adherence depends on instruction-following rather than
  provider-side constrained decoding; weak models may mangle complex
  nested arguments (code inside JSON strings).  Mitigations: prompt
  guidance, the structured-output fallback, and grammar-constrained
  servers where available.
* Neutral, because token cost is comparable to native tool fields:
  providers also charge for definitions passed via the ``tools`` field
  and add hidden tool-use instruction overhead (hundreds of additional
  input tokens on Anthropic-class APIs), while the native route
  transmits full JSON schemas where our ``ToolInfo`` descriptions are
  compact text.  Output-token cost is a wash -- native tool calls and
  our ``ToolCallsSchema`` both emit JSON with the same string-escaping
  penalty.  A static prompt prefix keeps either route cacheable
  (ADR-0028).

### Confirmation

* Code: ``BaseLangGraph._build_tools_info`` builds ``ToolInfo``
  descriptions from the fastmcp client; ``ToolsPicker`` (ADR-0020)
  injects them via its prompt and parses ``ToolCallsSchema`` through
  ``BaseLLMNode``'s structured-output path with fallback;
  ``ToolsCallerNode``/``dispatch_tool_calls`` execute the signatures.
* Tests: ``utils_pkg/tests/test_nodes_tools_picker.py``,
  ``test_nodes_tools_caller.py``, ``test_mcp_dispatch.py``, and the
  structured-output fallback tests cover the parse/fallback path.
* Fitness function: no ``bind_tools`` usage anywhere in the four
  packages (grep).  Future coding-loop nodes (e.g. the agent's Act
  node) must follow the same emission mechanism; this is a review
  checkpoint when those nodes land.

## Pros and Cons of the Options

### A. Provider-native tool fields (bind_tools)

* Good, because providers that implement tool calling can constrain
  emission server-side (higher fidelity for complex arguments).
* Good, because it is the route opencode and most TypeScript agents
  take; large ecosystem validation.
* Bad, because ``bind_tools`` is a per-integration stub in
  langchain-core and requires converting MCP tool schemas into
  ``BaseTool``/pydantic specs (the ``langchain-mcp-adapters`` path) --
  a new dependency and code path.
* Bad, because the ``tools`` field is not universal: silent dropping
  on lax OpenAI-compatible servers leaves the agent inert with nothing
  to catch; vLLM needs per-model-family parser opt-in; TGI uses a
  grammar mechanism.
* Bad, because a second emission path would fork ADR-0020's shared
  parsing/fallback/inspection logic.
* Neutral, because the model-training dependency (emit a parseable
  format) exists in this option too.

### B. Prompt injection + structured emission (chosen)

* Good, because it is provider-agnostic by construction: the only
  requirement is instruction-following, which all chat models have.
* Good, because it reuses the ADR-0020 machinery unchanged; zero new
  dependencies.
* Good, because the aider benchmark is direct evidence that
  structured/API-native formats are not automatically better, and can
  be worse.
* Bad, because complex nested arguments (code inside JSON strings)
  are the weak spot for small models; mitigated by prompt guidance and
  grammar-constrained servers where available.
* Neutral, because input-token cost is comparable to or lower than
  native tool fields (compact text descriptions vs full JSON schemas
  plus hidden provider tool-use instructions); output escaping is a
  wash since both routes emit JSON arguments.

### C. Hybrid (native when available, injection otherwise)

* Good, because in principle it gains native constrained emission
  where the endpoint supports it.
* Bad, because capability detection is unreliable: loud failures (4xx)
  are catchable, but silent dropping of the ``tools`` field is
  indistinguishable from model choice; a capability probe or a
  per-model capability catalog (opencode's models.dev approach) would
  be real ongoing engineering to avoid a single injection call.
* Bad, because two live code paths must both be maintained, tested,
  and inspected (ADR-0013).

## More Information

* Related: ADR-0020 (unified picker/caller this decision governs),
  ADR-0004 (bundled MCP server), ADR-0007 (permission gate), ADR-0003
  (isError compliance), ADR-0016/0019 (graph and node templates),
  ADR-0017 (LLM invoke retry), ADR-0028 (prompt cache), ADR-0013
  (inspection).
* The edit-format strategy (whole-file vs search/replace editing
  tools, model-guided selection) is recorded separately as a system
  note (``devdocs/system/edit-format-strategy.md``); it depends on
  this decision: edits are emitted as tool signatures, never parsed
  from free text response.
* Sources: aider edit formats and benchmarks
  (aider.chat/docs/more/edit-formats.html, aider.chat/docs/
  benchmarks.html), Cline system prompt fundamentals
  (cline.ghost.io/system-prompt/), vLLM OpenAI-compatible server docs
  (docs.vllm.ai), TGI messages API
  (huggingface.co/docs/text-generation-inference/en/messages_api),
  langchain-core 1.6.2 sources, opencode sources (reviewed
  2026-09-09).
