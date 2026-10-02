# Prompt conventions for LLM nodes

Status: implemented convention.  Applies to every `BaseLLMNode` prompt
(`*_system.md` / `*_user.md`).  The `BaseLLMNode._optional_section` helper is
the mechanism.  Extend existing prompts to follow this contract rather than
inventing per-node patterns.

Last updated: 2026-10-02.

## Scope

All file-based prompts loaded by `BaseLLMNode`
(`utils_pkg/klea_utils/nodes/base.py`) across `klea_agent`, `klea_rag` and
`klea_utils`.  It governs how a node's `_get_prompt_variables` populates the
template.

## The problem

`ChatPromptTemplate` has no conditionals: every placeholder always renders.
So a template line like `Failure reason: {failure_reason}` produces an empty
label on the many calls where a failure reason does not apply.  Beyond a few
tokens, empty labels:

* invite a weak model to fill the slot (inventing a failure, a question, ...);
* distinguish nothing (is it absent, or did the renderer drop it?);
* dilute attention with irrelevant sections.

This project targets non-frontier and small models, where those effects are
stronger.

## The core principle

A prompt contains only information relevant to *this* invocation.  A field
with no content is either **represented by a sentinel** or **omitted
entirely, heading included** - never rendered as a label with an empty value.

## Rule 1 - Two tiers

* **System prompt (`*_system.md`)** - stable for a given node and
  configuration.  It describes the role, the rules, and every input the node
  may receive, including conditionality ("only meaningful when `outcome` is
  `failure`").  It carries no per-invocation content, so it stays the
  cacheable prefix (ADR-0028).
* **User prompt (`*_user.md`)** - per-invocation.  It carries the volatile
  data.  A conditional field appears only when it has content.  Every node
  provides one: a system-only request has no user message and is rejected by
  the Anthropic Messages API.

## Rule 2 - Required vs conditional fields

* **Required input** - always present; when empty it renders a sentinel.  The
  slot always exists and its emptiness is itself information (for example
  `observations`, `goal`, `plan`, `query`).
* **Conditional input** - present only when applicable; omitted (heading and
  body) otherwise.  For example `failure_reason`, `pending_question`,
  `replan_reason`, `human_feedback`, `validation_feedback`, `picker_feedback`.

## Rule 3 - Mechanics: `_optional_section`

`ChatPromptTemplate` cannot branch, so the **node composes the whole section**
into one variable:

* the template has a bare placeholder on its own line, with no heading:
  `{field_block}`
* the node builds it with `self._optional_section(title, body)`, which returns
  `""` when `body` is empty, or `"## <title>\n\n<body>"` otherwise.

```python
variables["outcome_details"] = self._optional_section(
    "Outcome details", self._outcome_details(state)
)
```

`_optional_section` is part of the `BaseLLMNode` contract; do not hand-roll
the heading/empty check in a node.

## Rule 4 - Sentinels

* Missing scalar: `(none)`.
* Empty collection: `(no <items>)` (for example `(no observations)`,
  `(no artefacts)`).
* Use one spelling consistently within a prompt family.
* ASCII only (repository rule): no Unicode dashes, arrows, or ellipses.

Conditional fields must not use sentinels; omit them instead.  A sentinel is
only for a required slot whose absence is meaningful.

## Rule 5 - Cache

* Prefer the user prompt for volatile content.
* If volatile content must live in the system prompt, put it last (Rule 1
  exception).
* Omitting a conditional field from the user prompt is cache-neutral; never
  make the stable part of the system prompt conditional.

## Rule 6 - LLM output schemas are structure-only

A node's `output_schema` (the pydantic model passed to `with_structured_output`)
is **structure only**: field names, types, enums and defaults.  It carries **no
class docstring and no `Field(description=...)`**.

Pydantic emits a model's docstring as the JSON-schema `description` (and each
`Field(description=...)` verbatim), and that schema reaches the model twice:
`BaseLLMNode._format_output_schema_prompt` renders it into the system prompt
(only the root `title`/`description` are dropped, so nested models leak), and
`with_structured_output(..., method="json_schema")` sends it as the provider's
`response_format`.  Hand-written prose there duplicates the `*_system.md`
prompt and can silently **drift** from it, which misleads the model; it also
wastes tokens.

So, for an output model (and every model reachable from it):

* put the semantics in the node's `*_system.md`, not in the schema;
* keep developer notes (provenance, ADR references, rationale) in `#` comments
  above the class or field, never in a docstring/`description`;
* do not strip the schema of field names, enums or defaults - those are the
  contract.

Exception: the tools picker's per-run schema
(`klea_utils.mcp.call_schema.build_tool_call_schema`) generates field
descriptions from each MCP tool's `input_schema`.  Those are model-facing tool
documentation derived from the tool definition (a single source), not
hand-written prompt prose, so they are allowed.

Regression guard: `agent_pkg/tests/test_schemas.py`,
`rag_pkg/tests/test_schemas.py` and
`utils_pkg/tests/test_call_schema.py` assert every static output schema is
description-free.

## Checklist for a new or edited node

1. System prompt: stable; describe all inputs and any conditionality.
2. User prompt: required fields with sentinels; conditional fields as bare
   `{..._block}` placeholders.
3. `_get_prompt_variables`: compose each conditional block with
   `self._optional_section(...)`.
4. Tests: render with the optional fields empty and assert their headings are
   absent; render with content and assert they appear.
