# Prompt conventions for LLM nodes

Status: implemented convention.  Applies to every `BaseLLMNode` prompt
(`*_system.md` / `*_user.md`).  The `BaseLLMNode._optional_section` helper is
the mechanism.  Extend existing prompts to follow this contract rather than
inventing per-node patterns.

Last updated: 2026-09-19.

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
  * Exception: a node with no user prompt (e.g. the tools picker, whose
    `_get_human_prompt` returns `""`) must put volatile content at the very
    end of the system prompt - the ADR-0028 volatile-tail pattern - as a
    single optional block.
* **User prompt (`*_user.md`)** - per-invocation.  It carries the volatile
  data.  A conditional field appears only when it has content.

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

## Checklist for a new or edited node

1. System prompt: stable; describe all inputs and any conditionality.
2. User prompt: required fields with sentinels; conditional fields as bare
   `{..._block}` placeholders.
3. `_get_prompt_variables`: compose each conditional block with
   `self._optional_section(...)`.
4. Tests: render with the optional fields empty and assert their headings are
   absent; render with content and assert they appear.
