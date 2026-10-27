---
status: "proposed"
date: 2026-10-27
decision-makers: [Architecture Team]
---

# ADR-0057: Run Manager & Isolation

## Context and Problem Statement

The evaluation harness must initialize and manage isolated execution contexts ("runs") for each test case. This involves:

- **Run Context Creation**: Each run requires a unique identifier, user context, and chat session tracking for reproducibility and debugging.
- **Fixture Workspace Isolation**: Test cases may include fixtures (input files, configurations). These must be copied into isolated workspaces to prevent cross-case contamination and enable parallel execution.
- **Model Resolution**: Test cases specify roles (e.g., "assistant", "evaluator") that must be resolved to concrete model configurations (e.g., "gpt-4-turbo", "claude-3-opus"). Resolution must support both harness-level defaults and case-level overrides.
- **Metrics Initialization**: Each run must initialize a metrics collector to track tool calls, latency, token usage, and evaluation results. Collectors must be isolated per run and aggregatable across runs.
- **Reproducibility**: Runs must be deterministic and reproducible. Seeds, model parameters, and fixture state must be captured and versioned.

Without a clear run initialization and isolation strategy, the harness will lack reproducibility, enable cross-case interference, and complicate debugging and metrics aggregation.

## Decision Drivers

* **Reproducibility**: runs must be deterministic and reproducible; seeds, model parameters and fixture state must be captured and versioned so the same case + seed always produces identical outputs and metrics.
* **Isolation**: each run must execute in its own workspace so that cross-case contamination is impossible and parallel execution is safe.
* **Debuggability**: intermediate state, run metadata and metrics must be human-readable and inspectable so developers can compare runs and trace execution.
* **Scalability**: isolation must scale to large test suites (millions of cases) within disk-space limits.
* **Extensibility**: new isolation strategies, workspace backends and metrics collectors must be addable without modifying a core class.
* **Operational simplicity**: no external infrastructure (databases, message queues) may be required; standard filesystem operations and JSON libraries suffice.
* **Model-resolution determinism**: role-to-model resolution must be deterministic and fail clearly when a role cannot be resolved.
* **Metrics isolation and aggregation**: collectors must be isolated per run yet aggregatable across runs.

## Considered Options

* Monolithic run manager
* Modular run manager
* In-memory isolation
* Filesystem isolation
* Eager model resolution
* Lazy model resolution
* Centralized metrics collection
* Distributed metrics collection

## Decision Outcome

Chosen option: "Modular run manager", because modular design enables independent testing, easier debugging, and future extensibility (e.g., cloud-based workspaces, custom metrics collectors). It is combined with the companion choices of filesystem isolation, eager model resolution and centralized metrics collection, as detailed below.

We adopt a **modular, filesystem-based isolation strategy** for run initialization and management:

### Run Identification

Each run is identified by a tuple of immutable, deterministic identifiers:

```json
{
  "run_id": "string (UUID v5, derived from case_id + harness_seed)",
  "user_id": "string (from run_config or harness default)",
  "chat_id": "string (UUID v5, derived from run_id + turn_number)"
}
```

**Rationale**: UUID v5 (SHA-1 based) ensures deterministic, reproducible IDs without requiring external state. `run_id` is derived from `case_id` and harness seed; `chat_id` is derived from `run_id` and turn number. This enables reproducible tracing and debugging.

### Workspace Isolation

Each run gets an isolated filesystem workspace:

```
<harness_root>/runs/<run_id>/
├-- workspace/          # Isolated working directory for the run
|   ├-- fixtures/       # Copied from case fixtures (if present)
|   └-- outputs/        # Generated files (tool outputs)
├-- metadata.json       # Run metadata (case_id, user_id, seed, timestamps)
├-- metrics.jsonl       # Metrics log (tool calls, latency, tokens)
└-- trace.jsonl         # Execution trace (turns, tool calls, outputs)
```

**Fixture Copying Semantics**:
- If a case specifies `fixtures`, the harness copies the fixture directory into `workspace/fixtures/` at run initialization.
- Fixture paths in the case are resolved relative to `workspace/fixtures/`.
- Absolute paths in fixtures are rejected; only relative paths are allowed.
- Copying is shallow (no symlink resolution); fixtures are immutable post-copy.

**Workspace Cleanup**:
- Workspaces are retained for debugging and metrics aggregation.
- A separate cleanup policy (e.g., retention period, explicit deletion) is defined in run configuration.

### Model Resolution

Model resolution follows a **role -> model map** strategy:

1. **Harness-Level Defaults**: The harness defines a default role-to-model mapping (e.g., `{"assistant": "gpt-4-turbo", "evaluator": "claude-3-opus"}`).
2. **Case-Level Overrides**: A case's `run_config` can override the mapping for specific roles (e.g., `{"assistant": "gpt-4"}`).
3. **Resolution Order**: For each role in a turn, resolve as follows:
   - Check case-level `run_config.model_overrides[role]`.
   - Fall back to harness-level default `role_model_map[role]`.
   - If neither exists, reject the turn with a clear error.

**Model Configuration**:
Each resolved model includes:
```json
{
  "model_name": "string (e.g., 'gpt-4-turbo')",
  "provider": "string (e.g., 'openai', 'anthropic')",
  "parameters": "object (temperature, max_tokens, etc.)"
}
```

### Metrics Initialization

Each run initializes a metrics collector:

```json
{
  "run_id": "string",
  "case_id": "string",
  "metrics": {
    "tool_calls": "array (tool call records)",
    "latency_ms": "number (total execution time)",
    "token_usage": "object (input, output, total tokens)",
    "evaluation_results": "object (rubric scores, pass/fail)"
  }
}
```

**Collector Lifecycle**:
- Initialized at run start.
- Updated after each tool call and turn execution.
- Finalized and persisted to `metrics.jsonl` at run completion.
- Aggregatable across runs via simple JSONL concatenation and aggregation scripts.

### Invariants

The run manager enforces the following invariants:

1. **Run ID Uniqueness**: Each `run_id` is unique within a harness execution. Duplicate runs are rejected.
2. **Workspace Isolation**: Each run's workspace is independent; no cross-run file access is allowed.
3. **Fixture Immutability**: Fixtures are copied at run initialization and treated as read-only. Modifications are not persisted.
4. **Model Resolution Determinism**: Model resolution is deterministic; the same case + harness config always resolves to the same model.
5. **Metrics Completeness**: All tool calls and turns are recorded in metrics; no metrics are dropped or lost.
6. **Reproducibility**: Runs with the same `case_id`, `user_id`, and harness seed are reproducible; outputs and metrics are identical.

### Consequences

* Good, because runs are deterministic and reproducible; the same case + seed always produces identical outputs and metrics.
* Good, because complete filesystem isolation prevents cross-case contamination, so parallel execution is safe.
* Good, because workspace directories and metrics logs are human-readable; developers can inspect intermediate state, compare runs, and trace execution.
* Good, because filesystem isolation scales to millions of cases, and metrics are stored in JSONL format, enabling efficient aggregation.
* Good, because the modular design enables future enhancements without major refactoring.
* Bad, because copying fixtures and creating workspace directories adds I/O overhead, which may be noticeable for very large test suites (millions of cases with large fixtures).
* Bad, because if a role is not defined in the harness-level or case-level model map the run fails with an error, requiring careful configuration management.
* Bad, because workspaces are retained for debugging and consume disk space, so a cleanup policy must be defined and enforced.
* Bad, because metrics are stored in separate JSONL files per run, so aggregation requires external tools (jq, pandas) or custom scripts.

## Pros and Cons of the Options

### Monolithic run manager

Single `RunManager` class handles all initialization, isolation, metrics.

* Good, because it has lower initial complexity.
* Bad, because it is hard to test in isolation; requires full harness setup.
* Bad, because adding new isolation strategies requires modifying the core class.
* Bad, because it carries a higher long-term maintenance burden.

### Modular run manager

Separate `RunInitializer`, `WorkspaceManager`, `ModelResolver`, `MetricsCollector`.

* Good, because each component can be tested independently.
* Good, because new strategies can be added as plugins.
* Neutral, because it has higher initial complexity but lower long-term maintenance burden.
* Good, because modular design enables independent testing, easier debugging, and future extensibility (e.g., cloud-based workspaces, custom metrics collectors).

### In-memory isolation

Processes share memory; requires careful synchronization.

* Bad, because processes share memory and therefore require careful synchronization.
* Bad, because it is hard to inspect intermediate state; requires logging.
* Bad, because scalability is limited by available RAM; not suitable for large test suites.
* Bad, because fixtures are loaded into memory, which has high overhead for large files.
* Neutral, because it is suitable only for small, tightly-coupled test suites.

### Filesystem isolation

Complete isolation via separate directories.

* Good, because filesystem isolation provides complete isolation through separate directories.
* Good, because it is easy to inspect files and compare runs.
* Good, because it scales to millions of cases; limited only by disk space.
* Good, because fixtures are copied to disk, which is efficient for large files.
* Good, because it is more debuggable, more scalable, and aligns with the harness's streaming-first design.

### Eager model resolution

Resolve all models at run initialization.

* Good, because errors are caught early with clear error messages.
* Good, because early error detection and clearer debugging are enabled; the initialization overhead is negligible compared to the benefits of catching configuration errors upfront.
* Bad, because it is less flexible; all models must be available upfront.
* Neutral, because it has slightly higher initialization overhead.

### Lazy model resolution

Resolve models on-demand during turn execution.

* Good, because it is more flexible; models can be resolved dynamically.
* Neutral, because it has slightly lower initialization overhead.
* Bad, because errors are caught during execution and are harder to debug.

### Centralized metrics collection

Single `MetricsCollector` instance per harness.

* Good, because aggregation is simple; all metrics are in one place.
* Good, because consistency is strong; all metrics are synchronized.
* Good, because it is simpler to implement and debug; for typical evaluation harnesses (sequential or moderate concurrency) the bottleneck is negligible.
* Neutral, because it is a potential bottleneck for high-concurrency scenarios.

### Distributed metrics collection

Each component (executor, tool caller) maintains local metrics.

* Good, because scalability is better; each component is independent.
* Neutral, because distributed metrics can be added later if needed.
* Bad, because aggregation is complex; requires aggregation logic.
* Bad, because consistency is eventual; requires reconciliation.

## More Information

### Rationale

The chosen strategy aligns with the evaluation harness's core requirements:

1. **Reproducibility**: UUID v5-based IDs and deterministic model resolution ensure runs are reproducible. The same case + seed always produces the same run ID and model configuration.
2. **Isolation**: Filesystem-based workspaces provide complete isolation; cross-case interference is impossible. Fixtures are copied, not shared.
3. **Debuggability**: Workspace directories and metrics logs are human-readable and inspectable. Developers can easily compare runs, trace execution, and identify issues.
4. **Scalability**: Filesystem isolation scales to millions of cases. Metrics are stored in JSONL format (from ADR-0056), enabling efficient aggregation and querying.
5. **Extensibility**: Modular design enables future enhancements (e.g., cloud-based workspaces, custom metrics collectors, distributed execution).
6. **Operational Simplicity**: No external infrastructure (databases, message queues) is required. The harness uses standard filesystem operations and JSON libraries.

### Implementation Notes

#### Run Initialization Flow

1. **Load Case**: Read case from JSONL file (ADR-0056).
2. **Generate Run ID**: Compute UUID v5 from `case_id` and harness seed.
3. **Create Workspace**: Create `runs/<run_id>/workspace/` directory.
4. **Copy Fixtures**: If case specifies fixtures, copy to `workspace/fixtures/`.
5. **Resolve Models**: For each role in the case, resolve to a concrete model using the role-to-model map.
6. **Initialize Metrics**: Create a metrics collector for the run.
7. **Return Run Context**: Return a `RunContext` object with run ID, workspace path, resolved models, and metrics collector.

#### RunContext Object

```python
class RunContext:
    run_id: str
    case_id: str
    user_id: str
    workspace_path: Path
    fixtures_path: Path
    resolved_models: Dict[str, ModelConfig]
    metrics_collector: MetricsCollector
    metadata: Dict[str, Any]  # case_id, seed, timestamps, etc.
```

#### Workspace Structure

```
runs/<run_id>/
├-- workspace/
|   ├-- fixtures/          # Copied from case (if present)
|   └-- outputs/           # Generated by tools
├-- metadata.json          # Run metadata
├-- metrics.jsonl          # Metrics log
└-- trace.jsonl            # Execution trace
```

### Examples

#### Example 1: Simple Single-Model Run

**Case**:
```json
{
  "case_id": "simple-001",
  "schema_version": "1.0",
  "description": "Write a simple text file",
  "turns": [
    {
      "turn_number": 1,
      "role": "user",
      "content": "Write 'Hello, World!' to output.txt"
    },
    {
      "turn_number": 2,
      "role": "assistant",
      "content": "I'll write the file now."
    }
  ]
}
```

**Harness Config**:
```json
{
  "role_model_map": {
    "user": "gpt-4-turbo",
    "assistant": "gpt-4-turbo"
  },
  "seed": 42
}
```

**Run Initialization**:
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "case_id": "simple-001",
  "user_id": "default",
  "chat_id": "550e8400-e29b-41d4-a716-446655440001",
  "workspace_path": "runs/550e8400-e29b-41d4-a716-446655440000/workspace",
  "resolved_models": {
    "user": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "assistant": {"model_name": "gpt-4-turbo", "provider": "openai"}
  },
  "metadata": {
    "case_id": "simple-001",
    "seed": 42,
    "initialized_at": "2025-01-01T00:00:00Z"
  }
}
```

#### Example 2: Complex Multi-Model Run with Overrides

**Case**:
```json
{
  "case_id": "complex-001",
  "schema_version": "1.0",
  "description": "Multi-turn with fixtures and model overrides",
  "fixtures": {
    "input_dir": "fixtures/complex-001",
    "files": ["data.json", "config.yaml"]
  },
  "run_config": {
    "timeout": 30,
    "seed": 123,
    "model_overrides": {
      "evaluator": "claude-3-opus"
    }
  },
  "turns": [
    {
      "turn_number": 1,
      "role": "user",
      "content": "Read data.json and summarize"
    },
    {
      "turn_number": 2,
      "role": "assistant",
      "content": "Summary: ..."
    },
    {
      "turn_number": 3,
      "role": "evaluator",
      "content": "Evaluate the summary"
    }
  ]
}
```

**Harness Config**:
```json
{
  "role_model_map": {
    "user": "gpt-4-turbo",
    "assistant": "gpt-4-turbo",
    "evaluator": "gpt-4-turbo"
  },
  "seed": 42
}
```

**Run Initialization**:
```json
{
  "run_id": "660e8400-e29b-41d4-a716-446655440000",
  "case_id": "complex-001",
  "user_id": "default",
  "chat_id": "660e8400-e29b-41d4-a716-446655440001",
  "workspace_path": "runs/660e8400-e29b-41d4-a716-446655440000/workspace",
  "fixtures_path": "runs/660e8400-e29b-41d4-a716-446655440000/workspace/fixtures",
  "resolved_models": {
    "user": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "assistant": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "evaluator": {"model_name": "claude-3-opus", "provider": "anthropic"}
  },
  "metadata": {
    "case_id": "complex-001",
    "seed": 123,
    "fixtures_copied": true,
    "initialized_at": "2025-01-01T00:00:00Z"
  }
}
```

**Workspace Structure**:
```
runs/660e8400-e29b-41d4-a716-446655440000/
├-- workspace/
|   ├-- fixtures/
|   |   ├-- data.json
|   |   └-- config.yaml
|   └-- outputs/
├-- metadata.json
├-- metrics.jsonl
└-- trace.jsonl
```

### Related Decisions

- **ADR-0056 (Test Case Format)**: Defines the JSONL format for test cases; ADR-0057 loads and initializes runs from this format.
- **ADR-0058 (Run Config Semantics)**: Defines the semantics of `run_config` overrides; ADR-0057 applies these overrides during model resolution and metrics initialization.

### References

- [UUID v5 Specification](https://tools.ietf.org/html/rfc4122#section-4.3)
- [JSONL Format Specification](https://jsonlines.org/)
- [Python pathlib Documentation](https://docs.python.org/3/library/pathlib.html)
- [Reproducible Research Best Practices](https://the-turing-way.netlify.app/reproducible-research/overview.html)
