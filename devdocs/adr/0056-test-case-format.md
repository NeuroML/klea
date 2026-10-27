---
status: "proposed"
date: 2026-10-27
decision-makers: [Architecture Team]
---

# ADR-0056: Test Case Storage Format

## Context and Problem Statement

The evaluation harness requires a format for storing and loading test cases that supports:
- **Streaming-first processing**: Cases are independent, immutable, and processed sequentially; the format should enable line-by-line reads without loading entire files into memory.
- **Schema flexibility**: Test cases vary in structure—some have simple single-turn interactions, others have complex multi-turn conversations with fixtures, tool expectations, and rubrics. A rigid schema would force unnecessary nesting or null fields.
- **Human readability and debuggability**: Evaluators and developers need to inspect, edit, and validate test cases without specialized tools or binary decoders.
- **Mature tooling ecosystem**: The format should integrate with standard Unix tools (jq, grep, sed), Python libraries (pandas, json), and CI/CD pipelines without vendor lock-in.
- **Reproducibility and immutability**: Test cases must be read-only, versioned, and deterministic; no timestamps or random elements should leak into case definitions.

Existing formats (CSV, YAML, Protocol Buffers, SQLite, Parquet) each have trade-offs that make them unsuitable for this use case:
- **CSV**: Rigid schema, no nesting, poor for complex structures (fixtures, tool expectations, rubrics).
- **YAML**: Human-friendly but no streaming support; slower parsing and larger memory footprint.
- **Protocol Buffers**: Compact binary (~5–10% overhead) but requires code generation, strict schema, and no streaming.
- **SQLite**: Queryable and ACID but adds operational complexity, schema-strict, and no streaming.
- **Parquet**: Columnar format optimized for analytics, not line-by-line reads; schema-strict and no streaming.

Without a clear format decision, the harness will lack a standard way to represent, validate, and evolve test cases, leading to ad-hoc solutions and maintenance burden.

## Decision Drivers

* **Streaming-first processing**: cases are independent, immutable and processed sequentially, so the format must enable line-by-line reads without loading entire files into memory.
* **Schema flexibility**: test cases vary in structure (simple single-turn interactions versus complex multi-turn conversations with fixtures, tool expectations and rubrics), so a rigid schema would force unnecessary nesting or null fields.
* **Human readability and debuggability**: evaluators and developers must be able to inspect, edit and validate test cases without specialized tools or binary decoders.
* **Mature tooling ecosystem**: the format must integrate with standard Unix tools (jq, grep, sed), Python libraries (pandas, json) and CI/CD pipelines without vendor lock-in.
* **Reproducibility and immutability**: test cases must be read-only, versioned and deterministic; no timestamps or random elements may leak into case definitions.
* **Operational simplicity**: no additional infrastructure (databases, code generation) may be required.

## Considered Options

* JSONL
* CSV
* YAML
* Protocol Buffers
* SQLite
* Parquet

## Decision Outcome

Chosen option: "JSONL", because JSONL is the only format that combines streaming-first design, schema flexibility, human readability and mature tooling; the ~15–20% overhead compared with binary formats is acceptable given the gains in operational simplicity, debuggability and no vendor lock-in.

We adopt **JSONL (JSON Lines)** as the format for test case storage, with a versioned schema that enforces invariants and supports flexible case structures.

### Format Choice: JSONL

JSONL is a text-based format where each line is a valid JSON object. It is:
- **Streaming-native**: One case per line; the harness reads and processes cases sequentially without loading the entire file.
- **Schema-flexible**: Each case is a self-contained JSON object; turns, fixtures, and rubrics can vary per case without rigid nesting.
- **Human-readable**: Plain text; debuggable with standard tools (jq, grep, cat).
- **Mature ecosystem**: Widely used by OpenAI evals, LangChain, MLflow, and standard JSON tooling (Python json, jq).
- **Append-only log semantics**: Cases are immutable; new cases are appended; no in-place updates.

### Schema Definition

Each JSONL line is a test case object with the following top-level structure:

```json
{
  "case_id": "string (unique identifier)",
  "schema_version": "string (e.g., '1.0', immutable)",
  "description": "string (human-readable summary)",
  "fixtures": "object (optional, fixture definitions)",
  "run_config": "object (optional, case-specific overrides)",
  "turns": "array (conversation turns)",
  "rubrics": "array (optional, evaluation rubrics)"
}
```

#### Turn Structure

Each turn in the `turns` array:

```json
{
  "turn_number": "integer (1-indexed, sequential, no gaps)",
  "role": "string ('user', 'assistant', 'system')",
  "content": "string (turn content)",
  "expected_outputs": "array (optional, expected outputs)",
  "tool_expectations": "array (optional, tool call expectations)"
}
```

#### Expected Outputs Sub-Schema

Each expected output:

```json
{
  "file_path": "string (relative to case file directory)",
  "format": "string ('json', 'yaml', 'text', 'binary')",
  "validation_rule": "string (e.g., 'exact_match', 'schema_valid', 'contains')"
}
```

#### Tool Expectations Sub-Schema

Each tool expectation:

```json
{
  "tool_name": "string (e.g., 'write_file', 'read_file')",
  "call_count": "[min, max] (range, e.g., [1, 3])",
  "argument_constraints": "object (optional, constraints on tool arguments)"
}
```

#### Rubrics Sub-Schema

Each rubric:

```json
{
  "rubric_id": "string (unique within case)",
  "type": "string ('binary', 'scale', 'categorical')",
  "criteria": "array (structured list of evaluation criteria)"
}
```

### Invariants

The schema enforces the following invariants:

1. **Immutability**: Cases are read-only post-creation. The validator rejects any attempt to modify a case in-place; new versions are appended as new lines.
2. **Schema Versioning**: Each case has an immutable `schema_version` field (e.g., "1.0"). The validator checks compatibility; unknown required fields are rejected.
3. **Fixture Path Resolution**: Fixture paths are relative to the case file directory. Absolute paths are rejected. The harness resolves paths at load time.
4. **Run Config Merge Semantics**: Case-level `run_config` overrides harness-level defaults via shallow merge. Case defaults take precedence.
5. **Reproducibility**: No timestamps or random elements in case definitions. Deterministic turn ordering (sequential, no gaps). Seeds are in `run_config` only.
6. **Case ID Uniqueness**: Each `case_id` is unique within a test suite. Duplicates are rejected at load time.
7. **Turn Ordering**: Turns are sequential (1-indexed, no gaps). Out-of-order turns are rejected.

### Schema Versioning Strategy

- **Immutable Version Field**: Each case includes a `schema_version` (e.g., "1.0"). This field is immutable and identifies the schema generation.
- **Backward-Compatible Evolution**: New schema versions add optional fields or deprecate old ones. Validators support multiple versions.
- **Validator Rejects Unknown Required Fields**: If a case specifies `schema_version: "2.0"` but the harness only supports "1.0", the validator rejects it with a clear error.
- **Deprecation Notes**: When a field is deprecated, the schema includes a note and a migration path.

### Consequences

* Good, because the harness can process cases line-by-line without loading entire files into memory, enabling efficient evaluation of large test suites.
* Good, because cases can vary in structure (simple single-turn, complex multi-turn with fixtures and rubrics) without forcing unnecessary nesting or null fields.
* Good, because evaluators and developers can inspect, edit and validate cases with standard tools (jq, grep, cat, text editors).
* Good, because JSONL integrates seamlessly with Python (json, pandas), Unix tools (jq, sed) and CI/CD pipelines.
* Good, because cases are append-only and schema versioning enables backward-compatible evolution.
* Good, because the plain text format makes it easy to identify issues, compare cases and trace evaluation results.
* Bad, because JSONL is ~15–20% larger than binary formats (Protocol Buffers, Parquet); for very large test suites (millions of cases), storage and network transfer may be slower.
* Bad, because unlike SQLite or Parquet, JSONL requires external tools (jq, pandas) for complex queries; simple filtering is easy, but complex aggregations require scripting.
* Bad, because the harness must validate each case at load time (schema version, required fields, invariants), adding CPU overhead that is negligible for typical test suites.
* Bad, because JSONL is append-only, so concurrent writes require external coordination (file locking, distributed consensus); for single-writer scenarios (typical in CI/CD) this is not a concern.

## Pros and Cons of the Options

### JSONL

Text-based format where each line is a valid JSON object; streaming-native, schema-flexible, human-readable, with append-only log semantics.

* Good, because it is streaming-native; one case per line, so the harness reads and processes cases sequentially without loading the entire file.
* Good, because it is schema-flexible; each case is a self-contained JSON object and turns, fixtures and rubrics can vary per case without rigid nesting.
* Good, because it is human-readable; plain text, debuggable with standard tools (jq, grep, cat).
* Good, because it has a mature ecosystem; widely used by OpenAI evals, LangChain, MLflow, and standard JSON tooling (Python json, jq).
* Neutral, because it carries ~15–20% overhead compared to binary formats, which is acceptable given the gains in operational simplicity, debuggability and no vendor lock-in.
* Good, because JSONL is the only format that combines streaming-first design, schema flexibility, human readability and mature tooling, and is the industry standard for streaming structured data (OpenAI evals, LangChain, MLflow).

### CSV

* Good, because it is human-readable and has mature tooling.
* Neutral, because it has low overhead (~5%).
* Bad, because it has a rigid schema and no nesting, which is poor for complex structures (fixtures, tool expectations, rubrics).
* Bad, because it has no streaming support.

### YAML

* Good, because it is human-friendly and schema-flexible.
* Neutral, because it has ~20–30% overhead.
* Bad, because it has no streaming support, and parsing is slower with a larger memory footprint.

### Protocol Buffers

* Good, because it is compact binary (~5–10% overhead).
* Bad, because it requires code generation and a strict schema.
* Bad, because it has no streaming support.

### SQLite

* Good, because it is queryable and ACID.
* Neutral, because it has ~10% overhead.
* Bad, because it adds operational complexity and is schema-strict.
* Bad, because it has no streaming support.

### Parquet

* Good, because it is a columnar format, efficient for analytics.
* Neutral, because it has ~10–15% overhead.
* Bad, because it is optimized for analytics rather than line-by-line reads; it is schema-strict and has no streaming.

## More Information

### Rationale

JSONL is chosen because it aligns with the evaluation harness's core requirements:

1. **Streaming-First Design**: Test cases are independent, immutable, and processed sequentially. JSONL's line-oriented design is a natural fit; each case is self-contained and can be validated/executed independently.
2. **Schema Flexibility**: Turns, fixtures, and rubrics vary per case. JSONL's flexible JSON structure avoids the rigidity of CSV or Parquet.
3. **Human Readability and Debuggability**: Plain text format enables easy inspection, editing, and validation without specialized tools.
4. **Mature Ecosystem**: JSONL is the industry standard for streaming structured data. It integrates with Python, Unix tools, and CI/CD pipelines without vendor lock-in.
5. **Immutability and Versioning**: JSONL's append-only semantics align with the harness's immutability requirement. Schema versioning enables backward-compatible evolution.
6. **Operational Simplicity**: No additional infrastructure (databases, code generation) is required. The harness can load and validate cases with standard JSON libraries.

The ~15–20% overhead compared to binary formats is acceptable given the gains in simplicity, debuggability, and ecosystem maturity. For typical test suites (hundreds to thousands of cases), the overhead is negligible.

### Implementation Notes

#### Validation

The harness includes a validator that checks:
- **Schema Version Compatibility**: Rejects cases with unknown schema versions.
- **Required Fields**: Ensures `case_id`, `schema_version`, `turns` are present.
- **Case ID Uniqueness**: Rejects duplicate `case_id` values within a test suite.
- **Turn Ordering**: Ensures turns are sequential (1-indexed, no gaps).
- **Fixture Path Resolution**: Resolves relative paths against the case file directory; rejects absolute paths.
- **Run Config Merge**: Validates that case-level `run_config` is a valid override object.
- **Tool Expectations**: Validates `call_count` ranges and argument constraints.
- **Rubric Criteria**: Ensures rubric types and criteria are well-formed.

#### Tooling

- **Loading**: Python `json` library for line-by-line reads; `pandas` for bulk operations.
- **Querying**: `jq` for command-line filtering; `pandas` for complex aggregations.
- **Validation**: Custom validator in the harness; can be run standalone or as part of CI/CD.
- **Editing**: Standard text editors; JSON schema validation in IDEs (VS Code, PyCharm).

#### Examples

##### Simple Case (Single Turn, File Output)

```json
{
  "case_id": "simple-001",
  "schema_version": "1.0",
  "description": "Write a simple text file",
  "turns": [
    {
      "turn_number": 1,
      "role": "user",
      "content": "Write 'Hello, World!' to a file named output.txt",
      "expected_outputs": [
        {
          "file_path": "output.txt",
          "format": "text",
          "validation_rule": "exact_match"
        }
      ]
    }
  ]
}
```

##### Complex Case (Multi-Turn, Fixtures, Tool Expectations, Rubrics)

```json
{
  "case_id": "complex-001",
  "schema_version": "1.0",
  "description": "Multi-turn conversation with file operations and tool expectations",
  "fixtures": {
    "input_dir": "fixtures/complex-001",
    "files": [
      "data.json",
      "config.yaml"
    ]
  },
  "run_config": {
    "timeout": 30,
    "seed": 42
  },
  "turns": [
    {
      "turn_number": 1,
      "role": "user",
      "content": "Read the data.json file and summarize its contents",
      "tool_expectations": [
        {
          "tool_name": "read_file",
          "call_count": [1, 1],
          "argument_constraints": {
            "path": "data.json"
          }
        }
      ]
    },
    {
      "turn_number": 2,
      "role": "assistant",
      "content": "The data contains...",
      "expected_outputs": [
        {
          "file_path": "summary.txt",
          "format": "text",
          "validation_rule": "contains"
        }
      ]
    },
    {
      "turn_number": 3,
      "role": "user",
      "content": "Write the summary to a file named summary.txt",
      "tool_expectations": [
        {
          "tool_name": "write_file",
          "call_count": [1, 1],
          "argument_constraints": {
            "path": "summary.txt"
          }
        }
      ]
    }
  ],
  "rubrics": [
    {
      "rubric_id": "accuracy",
      "type": "scale",
      "criteria": [
        "Summary accurately reflects data contents",
        "No hallucinations or fabricated details"
      ]
    },
    {
      "rubric_id": "tool_usage",
      "type": "binary",
      "criteria": [
        "Correct tools called with correct arguments"
      ]
    }
  ]
}
```

### Related Decisions

- **ADR-0057 (Turn Executor)**: Depends on this format for loading and executing turns.
- **ADR-0058 (Run Config Semantics)**: Depends on this format for case-level `run_config` overrides.

### References

- [JSONL Format Specification](https://jsonlines.org/)
- [OpenAI Evals Format](https://github.com/openai/evals)
- [LangChain Evaluation Framework](https://python.langchain.com/docs/guides/evaluation/)
- [MLflow Logging Format](https://mlflow.org/docs/latest/tracking.html)
