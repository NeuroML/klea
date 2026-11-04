---
status: "accepted"
date: 2026-10-27
decision-makers: [Architecture Team]
---

# ADR-0057: Run Manager & Isolation

## Context and Problem Statement

The evaluation harness must initialize and manage isolated execution contexts ("runs") for each test case. This involves:

- **Run Context Creation**: Each run requires a unique identifier, user context, and chat session tracking for reproducibility and debugging.
- **Fixture Workspace Isolation**: Test cases may include fixtures (input files, configurations). These must be copied into isolated workspaces to prevent cross-case contamination and enable parallel execution.
- **Model Resolution**: Test cases specify roles (e.g., "assistant", "evaluator") that must be resolved to concrete model configurations (e.g., "gpt-4-turbo", "claude-3-opus"). Resolution must support both harness-level defaults and case-level overrides.
- **Metrics Initialization**: Each run instantiates the ADR-0058 RunMetricsCollector to track tool calls, latency, token usage and evaluation results. The metric record schema, collection and per-run aggregation are owned by ADR-0058, and the on-disk layout, write protocol and retention by ADR-0059; this ADR only requires that each run gets its own collector.
- **Reproducibility**: Runs must be deterministic and reproducible. Seeds, model parameters, and fixture state must be captured and versioned.

Without a clear run initialization and isolation strategy, the harness will lack reproducibility, enable cross-case interference, and complicate debugging and metrics aggregation.

## Decision Drivers

* **Reproducibility**: runs must be reproducible; seeds, model parameters and fixture state must be captured and versioned so the same case + user + seed always produces the same run identity, model resolution and ordering. Model output itself is stochastic, so identical turn content is not promised.
* **Isolation**: each run must execute in its own workspace so that cross-case contamination is impossible and parallel execution is safe.
* **Debuggability**: intermediate state, run metadata and metrics must be human-readable and inspectable so developers can compare runs and trace execution.
* **Scalability**: isolation must scale to large test suites (millions of cases) within disk-space limits.
* **Extensibility**: new isolation strategies, workspace backends and metrics collectors must be addable without modifying a core class.
* **Operational simplicity**: no external infrastructure (databases, message queues) may be required; standard filesystem operations and JSON libraries suffice.
* **Model-resolution determinism**: role-to-model resolution must be deterministic and fail clearly when a role cannot be resolved.
* **Metrics isolation**: each run must carry its own metrics collector so records cannot mix across runs; the schema and aggregation are owned by ADR-0058.

## Considered Options

* Monolithic run manager
* Modular run manager
* In-memory isolation
* Filesystem isolation
* Eager model resolution
* Lazy model resolution

## Decision Outcome

Chosen option: "Modular run manager", because modular design enables independent testing, easier debugging, and future extensibility (e.g., cloud-based workspaces, custom metrics collectors). It is combined with the companion choices of filesystem isolation and eager model resolution, as detailed below.

We adopt a **modular, filesystem-based isolation strategy** for run initialization and management:

### Run Identification

Each run is identified by a tuple of immutable, deterministic identifiers:

```json
{
  "run_id": "string (UUID v5, derived from case_id + harness_seed)",
  "user_id": "string (from run_config or harness default)",
  "chat_id": "string (UUID v5, derived from run_id + user_id; one chat session per run)"
}
```

**Rationale**: UUID v5 (SHA-1 based) gives deterministic, reproducible IDs without requiring external state. `run_id` stays stable for a given `case_id` and harness seed, so re-running a suite with the same seed is idempotent: the same `run_id` is derived and the same stored run is reused. `run_id` plus seed alone can repeat across users, so `chat_id` folds in `user_id`; the tuple (`run_id`, `user_id`, `chat_id`) is therefore collision-free even when two users evaluate the same case with the same seed, and one run is always exactly one chat session. This enables reproducible tracing and debugging.

### Workspace Isolation

Each evaluation run gets its own isolated directory under a dedicated, configurable evaluation workspace root that lives outside the source tree, so run data never mixes with code:

```
<eval_workspace_root>/runs/<run_id>/
├-- run.json            # Run metadata (case_id, user_id, seed, timestamps, status)
├-- metrics.jsonl       # Metrics log, at the run root, never under workspace/
├-- transcript.jsonl    # Conversation turns and tool-call records
├-- workspace/          # Isolated working directory for the run
|   ├-- fixtures/       # Copied from the case fixtures (if present)
|   └-- outputs/        # Generated files (tool outputs)
└-- COMPLETE            # Zero-byte marker written last; presence means the run is final
```

The run-directory file set, the write protocol (temporary directory plus atomic rename) and the retention policy are defined by ADR-0059 and only referenced here.

**Fixture Copying Semantics**:
- Case fixture paths resolve against the case file directory (the directory holding the case JSONL, ADR-0056), never against the run workspace.
- The resolved fixture files are copied into `runs/<run_id>/workspace/fixtures/` at run initialization.
- Absolute paths in fixtures are rejected; only relative paths are allowed.
- Copying is shallow (no symlink resolution); fixtures are immutable post-copy.

**Workspace Cleanup**:
- Run directories are retained subject to the retention policy defined in ADR-0059.
- This ADR only requires that a run directory be deletable as one unit.

### Model Resolution

Model resolution follows a **role -> model map** strategy covering only the model-backed roles: `plan`, `execute`, `eval`, and `chat`. The `user` role is human input taken from the case and is never model-resolved.

1. **Harness-Level Defaults**: The harness defines a default role-to-model mapping for the model-backed roles only (e.g., `{"plan": "gpt-4-turbo", "execute": "gpt-4-turbo", "eval": "claude-3-opus", "chat": "gpt-4-turbo"}`).
2. **Case-Level Overrides**: A case's `run_config.model_overrides` can override the mapping for specific model-backed roles; for example, `{"chat": "gpt-4"}` is one entry of that `run_config.model_overrides` map.
3. **Resolution Order**: For each model-backed role in a turn, resolve as follows:
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

Each run instantiates its own ADR-0058 `RunMetricsCollector`, fed with the run's `run_id`, `case_id` and run directory. The record schema, collection points and per-run aggregation are owned by ADR-0058, while the on-disk layout, write protocol and retention are owned by ADR-0059. This ADR defines neither a metrics schema nor a write protocol and does not itself persist `metrics.jsonl`.

### Invariants

The run manager enforces the following invariants:

1. **Run Identity Uniqueness**: The tuple `(run_id, user_id, chat_id)` is unique within a harness execution. Duplicate identities are rejected.
2. **Workspace Isolation**: Each run's workspace is independent; no cross-run file access is allowed.
3. **Fixture Immutability**: Fixtures are copied at run initialization and treated as read-only. Modifications are not persisted.
4. **Model Resolution Determinism**: Model resolution is deterministic; the same case + harness config always resolves to the same model.
5. **Metrics Completeness**: Every tool call and turn is recorded by the ADR-0058 `RunMetricsCollector`; the record schema, collection points and completeness guarantees are owned by ADR-0058, and metric persistence by ADR-0059.
6. **Reproducibility**: Runs with the same `case_id`, `user_id` and harness seed resolve to the same identifiers (`run_id`, `chat_id`), the same models and the same ordering. Model output is stochastic, so turn content is not byte-identical across runs; only the derived metrics of a given transcript are reproducible.

### Consequences

* Good, because run identity and model resolution are deterministic: the same case + user + seed always produces the same `run_id`, `chat_id` and resolved models, while turn content and latency vary because LLM sampling is stochastic.
* Good, because complete filesystem isolation prevents cross-case contamination, so parallel execution is safe.
* Good, because workspace directories and metric logs (layout owned by ADR-0059) are human-readable; developers can inspect intermediate state, compare runs, and trace execution.
* Good, because filesystem isolation scales to millions of cases, and the resulting per-run metric files (layout in ADR-0059) aggregate efficiently with the tooling in ADR-0058.
* Good, because the modular design enables future enhancements without major refactoring.
* Bad, because copying fixtures and creating workspace directories adds I/O overhead, which may be noticeable for very large test suites (millions of cases with large fixtures).
* Bad, because if a role is not defined in the harness-level or case-level model map the run fails with an error, requiring careful configuration management.
* Bad, because workspaces are retained for debugging and consume disk space, so a cleanup policy must be defined and enforced.
* Bad, because per-run metric storage (ADR-0059) means cross-run aggregation requires external tools (jq, pandas) or custom scripts, as set out in ADR-0058.

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

## More Information

### Rationale

The chosen strategy aligns with the evaluation harness's core requirements:

1. **Reproducibility**: UUID v5-based IDs and deterministic model resolution make run identity reproducible. The same case + user + seed always produces the same `run_id`, `chat_id`, resolved models and ordering, while LLM sampling makes turn content non-identical across runs.
2. **Isolation**: Filesystem-based workspaces provide complete isolation; cross-case interference is impossible. Fixtures are copied, not shared.
3. **Debuggability**: Workspace directories and metric logs (layout owned by ADR-0059) are human-readable and inspectable. Developers can easily compare runs, trace execution, and identify issues.
4. **Scalability**: Filesystem isolation scales to millions of cases. Metric persistence and the aggregation tooling that keeps large volumes queryable are defined in ADR-0059 and ADR-0058 respectively.
5. **Extensibility**: Modular design enables future enhancements (e.g., cloud-based workspaces, custom metrics collectors, distributed execution).
6. **Operational Simplicity**: No external infrastructure (databases, message queues) is required. The harness uses standard filesystem operations and JSON libraries.

### Implementation Notes

#### Run Initialization Flow

1. **Load Case**: Read case from JSONL file (ADR-0056).
2. **Derive Identifiers**: Compute `run_id` as a UUID v5 over `case_id` and the harness seed, then compute `chat_id` as a UUID v5 over `run_id` and `user_id`.
3. **Create Run Directory**: Create `runs/<run_id>/` with its `workspace/` subdirectory; the full run-directory file set is defined in ADR-0059.
4. **Copy Fixtures**: If the case specifies fixtures, resolve them against the case file's directory and copy them into `workspace/fixtures/`.
5. **Resolve Models**: For each model-backed role (`plan`, `execute`, `eval`, `chat`) in the case, resolve to a concrete model using the role-to-model map.
6. **Initialize Metrics**: Instantiate the ADR-0058 `RunMetricsCollector` for this run.
7. **Return Run Context**: Return a `RunContext` object carrying the run directory, derived identifiers, resolved models and metrics collector.

#### RunContext Object

```python
class RunContext:
    run_id: str
    chat_id: str
    case_id: str
    user_id: str
    run_dir: Path
    fixtures_path: Path  # run_dir/workspace/fixtures
    resolved_models: Dict[str, ModelConfig]
    metrics_collector: MetricsCollector
    metadata: Dict[str, Any]  # case_id, seed, timestamps, etc.
```

#### Workspace Structure

```
runs/<run_id>/
├-- run.json               # Run metadata (ADR-0059)
├-- metrics.jsonl          # Metrics log (ADR-0058)
├-- transcript.jsonl       # Execution trace (ADR-0059)
├-- workspace/
|   ├-- fixtures/          # Copied from case (if present)
|   └-- outputs/           # Generated by tools
└-- COMPLETE               # Completion marker (ADR-0059)
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
      "role": "chat",
      "content": "I'll write the file now."
    }
  ]
}
```

**Harness Config**:
```json
{
  "role_model_map": {
    "plan": "gpt-4-turbo",
    "execute": "gpt-4-turbo",
    "eval": "gpt-4-turbo",
    "chat": "gpt-4-turbo"
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
  "run_dir": "runs/550e8400-e29b-41d4-a716-446655440000",
  "resolved_models": {
    "plan": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "execute": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "eval": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "chat": {"model_name": "gpt-4-turbo", "provider": "openai"}
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
      "eval": "claude-3-opus"
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
      "role": "chat",
      "content": "Summary: ..."
    },
    {
      "turn_number": 3,
      "role": "eval",
      "content": "Evaluate the summary"
    }
  ]
}
```

**Harness Config**:
```json
{
  "role_model_map": {
    "plan": "gpt-4-turbo",
    "execute": "gpt-4-turbo",
    "eval": "gpt-4-turbo",
    "chat": "gpt-4-turbo"
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
  "run_dir": "runs/660e8400-e29b-41d4-a716-446655440000",
  "fixtures_path": "runs/660e8400-e29b-41d4-a716-446655440000/workspace/fixtures",
  "resolved_models": {
    "plan": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "execute": {"model_name": "gpt-4-turbo", "provider": "openai"},
    "eval": {"model_name": "claude-3-opus", "provider": "anthropic"},
    "chat": {"model_name": "gpt-4-turbo", "provider": "openai"}
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
├-- run.json
├-- metrics.jsonl
├-- transcript.jsonl
├-- workspace/
|   ├-- fixtures/
|   |   ├-- data.json
|   |   └-- config.yaml
|   └-- outputs/
└-- COMPLETE
```

### Related Decisions

- **ADR-0056 (Test Case Format)**: Defines the JSONL format for test cases; ADR-0057 loads and initializes runs from this format.
- **ADR-0058 (Metrics Collection & Aggregation)**: Owns the versioned `RunMetrics` schema, the collection of metrics and their per-run aggregation; ADR-0057 only instantiates one `RunMetricsCollector` per run and feeds it the run-scoped identifiers (`run_id`, `case_id`) and the run root `run_dir`. This ADR does not define the schema, does not aggregate metrics and does not persist them itself; layout, write protocol and retention belong to ADR-0059.

* ADR-0059 (Result Storage & Persistence): Owns the run-directory file set, the single on-disk write protocol and the retention policy; ADR-0057 defers to it for how run artefacts are laid out, written and retained.
* ADR-0060 (Harness Execution & Orchestration): Owns orchestration of the runs defined here -- loading, scheduling, execution, scoring, persistence and suite-level aggregation (pass@k).

### References

- [UUID v5 Specification](https://tools.ietf.org/html/rfc4122#section-4.3)
- [JSONL Format Specification](https://jsonlines.org/)
- [Python pathlib Documentation](https://docs.python.org/3/library/pathlib.html)
- [Reproducible Research Best Practices](https://the-turing-way.netlify.app/reproducible-research/overview.html)
