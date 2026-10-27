---
status: "proposed"
date: 2026-10-27
decision-makers: [Architecture Team]
---

# ADR-0058: Metrics Collection & Aggregation

## Context and Problem Statement

The evaluation harness must track and aggregate metrics during run execution to enable reproducibility, debugging, and cost analysis. This involves:

- **Metric Collection Points**: Tool calls (name, arguments, duration, errors), latency per turn, token usage (input/output/cached), and evaluation results (rubric scores, pass/fail verdicts) must be captured at multiple execution stages without blocking execution or losing data on failure.
- **Collection Timing and Scope**: Metrics must flow from execution (tool caller, turn executor) through evaluation (scorer, rubric evaluator) to persistence (JSON, optional MLflow). Collection must support both per-turn granularity (for debugging) and per-run aggregation (for cost analysis and task success computation).
- **Aggregation Complexity**: Metrics from independent tool calls, turns, and evaluation steps must be aggregated into consistent per-run summaries (task success, pass@k, cost) without data loss or inconsistency.
- **Downstream Dependencies**: Result Storage (ADR-0059) depends on knowing what metrics to persist; Harness Execution & Orchestration (ADR-0060) depends on metrics to aggregate results across runs and enable real-time monitoring. Without a clear metrics schema and collection strategy, both downstream ADRs lack a foundation.

Existing approaches (in-memory only, distributed per-component, external event bus) each have trade-offs that make them unsuitable for this use case:
- **In-Memory Only**: Simple but loses data on crash; no real-time monitoring or crash recovery.
- **Distributed Per-Component**: Each component (executor, tool caller, evaluator) maintains local metrics. Aggregation is complex; requires reconciliation logic and risks data loss.
- **External Event Bus**: Decouples collection from execution but adds infrastructure complexity (message queue, broker) and operational overhead unsuitable for a dev-only harness.
- **Schema-Less JSON**: Flexible but lacks validation; downstream components cannot rely on consistent metric structure.

Without a clear metrics collection and aggregation strategy, the harness will lack reproducibility, enable data loss on failure, and complicate debugging and cost analysis.

## Decision Drivers

* **Reproducibility**: identical runs (same case_id, user_id and harness seed) must produce identical metrics, so that analysis and debugging are repeatable.
* **Isolation**: metrics must be collected per run with no cross-run interference and no data loss.
* **Debuggability**: per-turn metrics must let developers identify which turn or tool call caused a failure.
* **Extensibility**: custom metrics, LLM-as-judge integration and future distributed collection must be addable without refactoring.
* **Integration**: metrics must be persisted as JSONL so that result storage (ADR-0059) and the optional MLflow backend can consume them.
* **Operational simplicity**: no external infrastructure (databases, message queues) may be required for a dev-only harness.
* **Agent-agnostic collection**: metrics must be extractable from endpoints so that third-party agents can be evaluated.
* **Cost accuracy**: cost must depend only on tokens consumed, not on wall-clock latency.
* **Crash recovery and real-time monitoring**: per-turn metrics must be recoverable after a crash and observable while the run progresses.

## Considered Options

* Centralized collection
* Distributed collection
* In-process collection
* External event bus collection
* Eager aggregation
* Lazy aggregation
* Schema-first metrics
* Schema-less metrics

## Decision Outcome

Chosen option: "Centralized collection", because it is simpler to implement and debug, and for typical evaluation harnesses (sequential or moderate concurrency) the bottleneck is negligible; distributed metrics can be added later if needed (Phase 3 or later). It is combined with the companion choices of in-process collection, eager aggregation (with optional lazy per-turn streaming) and a schema-first metrics schema, as detailed below.

We adopt a **hybrid metrics collection architecture** with endpoint-based primary collection (decoupled from agent implementation) and optional in-process instrumentation hooks (Klea-internal optimization only):

### Metrics Collection Architecture

#### Primary: Endpoint-Based Collection (Agent-Agnostic)

The harness extracts metrics from Klea's existing API endpoints without instrumenting agent code. This keeps the harness decoupled from agent implementation and enables third-party agent evaluation:

1. **Tool Calls & Token Usage**: Parsed from SSE streams (Server-Sent Events) during run execution. Each SSE event contains tool name, arguments, duration, and token counts (input/output/cached).
2. **Task Success & Evaluation Results**: Extracted from run completion response (JSON). Contains rubric scores, pass/fail verdicts per turn, and overall task_success boolean.
3. **Cost Estimation**: Calculated from token_usage metrics (independent of wall-clock latency).

#### Secondary: Optional In-Process Hooks (Klea-Internal Optimization Only)

For Klea-specific internal optimization (not required for core evaluation), optional instrumentation hooks measure:

1. **Fixture Setup Overhead**: Time spent initializing test fixtures, environment setup.
2. **Retry Tracking**: Number of retries per tool call, retry latency distribution.
3. **Internal State Transitions**: Time spent in agent state transitions, message passing overhead.

These metrics are useful for Klea developers to optimize internal performance but are not required for evaluating agent behavior or comparing across agents.

#### Third-Party Agent Evaluation Strategy

For third-party agents (where source code cannot be modified), the harness collects metrics exclusively from endpoints:

1. **Agent-as-Endpoint**: Third-party agent exposes a standardized API (HTTP, gRPC, or OpenTelemetry/OTLP) that returns tool calls, token usage, and evaluation results.
2. **Endpoint-Only Metrics**: The harness parses responses without instrumenting agent code. Mandatory metrics (task_success, pass@k, token_usage, cost_estimate) are extracted from responses; optional metrics (latency, fixture overhead) are measured at the harness boundary.
3. **Fallback for Non-Compliant Agents**: If an agent does not expose metrics via endpoints, the harness can only measure task_success and evaluation_results (from run completion); token_usage and cost_estimate are unavailable.

#### Centralized RunMetricsCollector

Each run initializes a centralized `RunMetricsCollector` that aggregates metrics from endpoints and optional hooks into a per-run `metrics.jsonl` file and final `RunMetrics` summary at run completion:

```json
{
  "run_id": "string (UUID v5, from ADR-0057)",
  "case_id": "string (from test case)",
  "metrics": {
    "_primary": {
      "task_success": "boolean (all turns passed; evaluates agent correctness)",
      "pass_at_k": "object (pass@1, pass^k over N repetitions; evaluates agent reliability)",
      "token_usage": "object (input, output, cached tokens per turn; evaluates agent efficiency and cost)",
      "cost_estimate": "number (estimated cost in USD, derived from token_usage only)"
    },
    "_secondary": {
      "llm_invocation_count": "integer (number of LLM calls; secondary efficiency metric, not comparable across agent architectures)",
      "latency_ms": "object (per-turn and total execution time; infrastructure-dependent, not evaluative)"
    },
    "tool_calls": "array (tool call records; for debugging and audit)",
    "evaluation_results": "object (rubric scores, pass/fail verdicts per turn; for detailed analysis)"
  }
}
```

### Instrumentation Hooks

Metrics are collected at key execution points via in-process instrumentation hooks:

1. **Tool Call Completion**: Record tool name, arguments, duration, and any errors.
2. **Turn Execution End**: Record turn latency and token usage (input/output/cached).
3. **Evaluation Completion**: Record rubric scores and pass/fail verdicts per turn.
4. **Run Completion**: Aggregate per-turn metrics into final RunMetrics summary.

### Metrics Schema

Each metric category has a versioned schema:

#### Tool Call Record

```json
{
  "tool_name": "string (e.g., 'write_file', 'read_file')",
  "call_index": "integer (0-indexed call number in run)",
  "turn_number": "integer (1-indexed turn)",
  "arguments": "object (tool arguments, sanitized for privacy)",
  "duration_ms": "number (wall-clock execution time)",
  "error": "string or null (error message if failed)",
  "timestamp": "string (ISO 8601, for ordering)"
}
```

#### Latency Record (Optional/Secondary)

**Note**: Latency metrics are infrastructure-dependent and not evaluative of agent behavior. Wall-clock execution time varies 20–50% across identical runs due to hardware load, system scheduling, and network conditions. Latency is useful for deployment planning and infrastructure analysis, but should not be used to compare agent quality or behavior.

```json
{
  "per_turn": "object (turn_number -> latency_ms)",
  "total_ms": "number (total run execution time)",
  "tool_overhead_ms": "number (time spent in tool calls)",
  "model_overhead_ms": "number (time spent in LLM calls)"
}
```

**Use Cases**:
- **Deployment Planning**: Estimate infrastructure requirements (CPU, memory, concurrency limits) for production workloads.
- **Infrastructure Analysis**: Identify bottlenecks in tool execution or LLM invocation; optimize resource allocation.
- **Cost Estimation**: Correlate latency with infrastructure costs (e.g., compute-hours, API call duration).

**Not Suitable For**:
- Comparing agent behavior across runs or configurations (use token_usage and evaluation_results instead).
- Measuring agent quality or reasoning capability (use evaluation_results and task_success instead).

#### Token Usage Record

```json
{
  "per_turn": "object (turn_number -> {input_tokens, output_tokens, cached_tokens})",
  "total_input_tokens": "number",
  "total_output_tokens": "number",
  "total_cached_tokens": "number",
  "model": "string (model name for this turn)"
}
```

#### Cost Estimation Record

**Cost is calculated solely from token usage, independent of wall-clock latency:**

```json
{
  "model": "string (model identifier)",
  "input_tokens": "number (total input tokens across all turns)",
  "output_tokens": "number (total output tokens across all turns)",
  "cached_tokens": "number (total cached tokens, if applicable)",
  "input_cost_per_1k_tokens": "number (USD, from model pricing)",
  "output_cost_per_1k_tokens": "number (USD, from model pricing)",
  "cached_cost_per_1k_tokens": "number (USD, from model pricing, typically 10% of input cost)",
  "total_cost_usd": "number (calculated as: (input_tokens / 1000) * input_cost_per_1k_tokens + (output_tokens / 1000) * output_cost_per_1k_tokens + (cached_tokens / 1000) * cached_cost_per_1k_tokens)"
}
```

**Key principle**: Cost depends only on tokens consumed, not on inference latency or wall-clock execution time. A run that takes 10 seconds and a run that takes 100 seconds with identical token usage have identical cost. Latency is an infrastructure and deployment concern, not an agent behavior or cost concern.

#### Evaluation Result Record

```json
{
  "per_turn": "object (turn_number -> {rubric_id -> score})",
  "per_rubric": "object (rubric_id -> {scores: array, pass: boolean})",
  "task_success": "boolean (all turns passed)",
  "pass_at_k": "object (pass@1, pass^k over N repetitions)"
}
```

### Collection Flow

Metrics flow through the following stages:

1. **Execution Layer** (tool_caller, turn_executor): Instrumentation hooks record metrics at completion.
2. **Centralized Collector** (RunMetricsCollector): Receives metrics from hooks, validates, and buffers in memory.
3. **JSONL Persistence** (metrics.jsonl): Per-turn metrics written immediately after each turn; final RunMetrics summary written at run completion.
4. **Optional MLflow Backend**: If configured, RunMetrics summary logged to MLflow for experiment tracking and dashboarding.

### Invariants

The metrics collection enforces the following invariants:

1. **Completeness**: All tool calls, turns, and evaluation results are recorded. No metrics are dropped or lost.
2. **Determinism**: Metrics are recorded in deterministic order (by turn number, then call index). Same run + seed produces identical metrics.
3. **Immutability**: Metrics are append-only; once written to metrics.jsonl, they are not modified.
4. **Consistency**: Per-turn metrics are consistent with per-run aggregates (e.g., total tokens = sum of per-turn tokens).
5. **Reproducibility**: Runs with the same case_id, user_id, and harness seed produce identical metrics.

### Consequences

* Good, because all metrics are captured deterministically; identical re-runs produce identical metrics, enabling reproducible analysis and debugging across runs.
* Good, because per-turn metrics in JSONL enable inspection, comparison, and root-cause analysis, so developers can trace which turn or tool call caused a failure.
* Good, because in-process instrumentation hooks support custom metrics, LLM-as-judge integration, and future distributed collection without major refactoring; new metrics are added as new hook points or fields in the RunMetrics schema.
* Bad, because instrumentation hooks add CPU/memory cost per tool call and turn (mitigated by optional sampling, e.g. record every Nth call for high-volume scenarios, and by lazy aggregation in Phase 2).
* Bad, because if a collection point fails (e.g. an exception in a hook) metrics may be incomplete (mitigated by defensive recording, try-catch around hooks, and validation at aggregation time).
* Bad, because per-run JSONL files require external tools (jq, pandas) or custom scripts for cross-run analysis (mitigated by an optional MLflow backend for experiment tracking and built-in aggregation utilities).
* Bad, because adding new metrics requires schema versioning and backward-compatibility handling (mitigated by a versioned RunMetrics schema and migration tooling in Phase 2).

## Pros and Cons of the Options

### Centralized collection

Single `RunMetricsCollector` per run; all metrics flow through one object.

* Good, because aggregation is simple; all metrics are in one place.
* Good, because consistency is strong; all metrics are synchronized.
* Good, because it is easy to inspect and debug; a single source of truth.
* Neutral, because it is a potential bottleneck for high-concurrency scenarios, which is negligible for typical evaluation harnesses (sequential or moderate concurrency).
* Good, because it is simpler to implement and debug; distributed metrics can be added later if needed (Phase 3 or later).

### Distributed collection

Each component (executor, tool caller, evaluator) maintains local metrics.

* Good, because scalability is better; each component is independent.
* Bad, because aggregation is complex; requires aggregation logic and reconciliation.
* Bad, because consistency is only eventual; requires reconciliation and may lose data.
* Bad, because debugging is hard; metrics are scattered across components.

### In-process collection

Instrumentation hooks in the execution layer; metrics recorded directly to `RunMetricsCollector`.

* Good, because no infrastructure is required; it uses standard Python objects.
* Good, because latency is low; direct function calls.
* Good, because complexity is lower; no external dependencies.
* Bad, because it tightly couples execution and collection.
* Good, because it avoids infrastructure complexity and aligns with the harness's streaming-first design.

### External event bus collection

External event bus (message queue, broker); components emit events and the collector subscribes.

* Good, because it is loosely coupled; components are independent.
* Neutral, because it is suitable only for distributed, high-concurrency scenarios (deferred for Phase 3 or later).
* Bad, because it requires a message queue (RabbitMQ, Kafka) or event bus (Redis Pub/Sub).
* Bad, because latency is higher; network round-trip per metric.
* Bad, because complexity is higher; requires broker setup, error handling and retry logic.

### Eager aggregation

Aggregate metrics at run end; write the final RunMetrics summary to JSON.

* Good, because implementation is simpler; collect in memory and aggregate once at the end.
* Good, because storage is one final RunMetrics JSON per run.
* Good, because it is simpler for the MVP.
* Bad, because there is a risk of data loss if the run crashes before aggregation.
* Bad, because there is no real-time monitoring; metrics are only available after the run completes.

### Lazy aggregation

Stream per-turn metrics to metrics.jsonl immediately after each turn; aggregate at query time.

* Good, because crash recovery is robust; per-turn metrics are already persisted.
* Good, because real-time monitoring is possible; metrics can be monitored as the run progresses.
* Neutral, because storage is per-turn metrics.jsonl plus a final summary.
* Bad, because implementation is more complex; requires append-only JSON or separate per-turn files.
* Neutral, because it is an optional Phase 2 enhancement; both approaches can coexist (eager aggregation for the final summary, lazy streaming for per-turn debugging).

### Schema-first metrics

Versioned RunMetrics schema with required fields (tool_calls, latency_ms, token_usage, evaluation_results).

* Good, because validation is strict; unknown fields are rejected and required fields enforced.
* Good, because downstream compatibility is strong; downstream components (ADR-0059/0060) can rely on a consistent structure.
* Good, because debuggability is high; schema violations are caught early.
* Bad, because evolution requires versioning and migration tooling.
* Good, because it ensures consistency, validation and downstream compatibility, and the versioned schema enables backward-compatible evolution.

### Schema-less metrics

Flexible JSON; any metrics can be added without schema changes.

* Good, because it is flexible; any metrics can be added without schema changes.
* Good, because evolution is easy; just add new fields.
* Bad, because validation is permissive; any structure is accepted.
* Bad, because downstream compatibility is weak; downstream components must handle variable structures.
* Bad, because debuggability is low; inconsistencies are discovered late.
* Neutral, because this flexibility is deferred for future extensibility (Phase 3 or later).

## More Information

### Rationale

The chosen architecture aligns with the evaluation harness's core requirements:

1. **Reproducibility**: Centralized, deterministic collection ensures all metrics are captured consistently. Same run + seed produces identical metrics, enabling reproducible analysis and debugging.
2. **Isolation**: Per-run RunMetricsCollector (from ADR-0057) ensures metrics are isolated per run; no cross-run interference or data loss.
3. **Debuggability**: Per-turn metrics in JSONL enable inspection, comparison, and root-cause analysis. Developers can easily identify which turn or tool call caused a failure.
4. **Extensibility**: In-process instrumentation hooks support custom metrics, LLM-as-judge integration, and future distributed collection without refactoring. New metrics are added as new hook points or fields in RunMetrics.
5. **Integration**: JSONL persistence (from ADR-0056) integrates seamlessly with result storage (ADR-0059) and optional MLflow backend for experiment tracking and dashboarding.
6. **Operational Simplicity**: No external infrastructure (databases, message queues) required. The harness uses standard Python objects and JSON libraries.

### Implementation Notes

#### Instrumentation Hook Placement

**Tool Caller** (`tools_caller.py`):
```python
def dispatch_tool_calls(tool_calls, ...):
    for call in tool_calls:
        start_time = time.time()
        try:
            result = invoke_tool(call)
            duration_ms = (time.time() - start_time) * 1000
            metrics_collector.record_tool_call(
                tool_name=call.tool,
                arguments=call.arguments,
                duration_ms=duration_ms,
                error=None
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            metrics_collector.record_tool_call(
                tool_name=call.tool,
                arguments=call.arguments,
                duration_ms=duration_ms,
                error=str(e)
            )
            raise
```

**Turn Executor** (`turn_executor.py`):
```python
def execute_turn(turn, run_context, ...):
    start_time = time.time()
    # Execute turn (tool calls, model invocation, etc.)
    response = invoke_model(turn.content)
    duration_ms = (time.time() - start_time) * 1000

    # Record turn latency and token usage
    metrics_collector.record_turn_metrics(
        turn_number=turn.turn_number,
        latency_ms=duration_ms,
        token_usage=response.usage_metadata
    )
    return response
```

**Evaluation** (`scorer.py`):
```python
def evaluate_turn(turn, expected_output, actual_output, ...):
    # Evaluate turn against expected output
    verdict = score_output(expected_output, actual_output)

    # Record evaluation result
    metrics_collector.record_evaluation_result(
        turn_number=turn.turn_number,
        rubric_id=rubric.rubric_id,
        score=verdict.score,
        pass_fail=verdict.pass_fail
    )
    return verdict
```

#### RunMetricsCollector Interface

```python
class RunMetricsCollector:
    def __init__(self, run_id: str, case_id: str, workspace_path: Path):
        self.run_id = run_id
        self.case_id = case_id
        self.metrics_file = workspace_path / "metrics.jsonl"
        self.metrics = RunMetrics(run_id=run_id, case_id=case_id)

    def record_tool_call(
        self, tool_name: str, arguments: dict, duration_ms: float, error: Optional[str]
    ):
        """Record a tool call metric."""
        record = {
            "tool_name": tool_name,
            "call_index": len(self.metrics.tool_calls),
            "arguments": arguments,
            "duration_ms": duration_ms,
            "error": error,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self.metrics.tool_calls.append(record)

    def record_turn_metrics(
        self, turn_number: int, latency_ms: float, token_usage: dict
    ):
        """Record turn latency and token usage."""
        self.metrics.latency_ms[f"turn_{turn_number}"] = latency_ms
        self.metrics.token_usage[f"turn_{turn_number}"] = token_usage

    def record_evaluation_result(
        self, turn_number: int, rubric_id: str, score: float, pass_fail: bool
    ):
        """Record evaluation result for a turn."""
        if f"turn_{turn_number}" not in self.metrics.evaluation_results:
            self.metrics.evaluation_results[f"turn_{turn_number}"] = {}
        self.metrics.evaluation_results[f"turn_{turn_number}"][rubric_id] = {
            "score": score,
            "pass_fail": pass_fail,
        }

    def finalize(self):
        """Aggregate metrics and write to JSON."""
        # Compute aggregates
        self.metrics.task_success = all(
            all(r.get("pass_fail", False) for r in turn_results.values())
            for turn_results in self.metrics.evaluation_results.values()
        )
        self.metrics.total_latency_ms = sum(self.metrics.latency_ms.values())
        self.metrics.total_tokens = sum(
            usage.get("total_tokens", 0) for usage in self.metrics.token_usage.values()
        )

        # Write to metrics.jsonl
        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(self.metrics.to_dict()) + "\n")
```

#### Validation

The harness includes a validator that checks:
- **Schema Version Compatibility**: Rejects metrics with unknown schema versions.
- **Required Fields**: Ensures `run_id`, `case_id`, `tool_calls`, `latency_ms`, `token_usage`, `evaluation_results` are present.
- **Consistency**: Verifies per-turn metrics are consistent with per-run aggregates (e.g., total tokens = sum of per-turn tokens).
- **Determinism**: Ensures metrics are in deterministic order (by turn number, then call index).

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

**Metrics Collection**:

1. **Turn 1 Execution** (user input):
   - Latency: 50 ms (model invocation)
   - Token usage: {input_tokens: 20, output_tokens: 0, cached_tokens: 0}

2. **Turn 2 Execution** (assistant response):
   - Tool call: write_file(path="output.txt", content="Hello, World!")
     - Duration: 10 ms
     - Error: null
   - Latency: 150 ms (model invocation + tool call)
   - Token usage: {input_tokens: 50, output_tokens: 30, cached_tokens: 0}

3. **Evaluation**:
   - Turn 2 output matches expected output
   - Rubric score: 1.0 (pass)

**Final RunMetrics**:
```json
{
  "run_id": "550e8400-e29b-41d4-a716-446655440000",
  "case_id": "simple-001",
  "metrics": {
    "tool_calls": [
      {
        "tool_name": "write_file",
        "call_index": 0,
        "turn_number": 2,
        "arguments": {"path": "output.txt", "content": "Hello, World!"},
        "duration_ms": 10,
        "error": null,
        "timestamp": "2025-01-01T00:00:00Z"
      }
    ],
    "latency_ms": {
      "per_turn": {"1": 50, "2": 150},
      "total_ms": 200,
      "tool_overhead_ms": 10,
      "model_overhead_ms": 190
    },
    "token_usage": {
      "per_turn": {
        "1": {"input_tokens": 20, "output_tokens": 0, "cached_tokens": 0},
        "2": {"input_tokens": 50, "output_tokens": 30, "cached_tokens": 0}
      },
      "total_input_tokens": 70,
      "total_output_tokens": 30,
      "total_cached_tokens": 0
    },
    "evaluation_results": {
      "per_turn": {
        "2": {"accuracy": {"score": 1.0, "pass_fail": true}}
      },
      "task_success": true,
      "pass_at_k": {"pass@1": 1.0}
    },
    "cost_estimate": 0.0015
  }
}
```

#### Example 2: Complex Multi-Model Run with Evaluation

**Case**:
```json
{
  "case_id": "complex-001",
  "schema_version": "1.0",
  "description": "Multi-turn conversation with file operations and tool expectations",
  "fixtures": {
    "input_dir": "fixtures/complex-001",
    "files": ["data.json", "config.yaml"]
  },
  "run_config": {
    "timeout": 30,
    "seed": 42,
    "model_overrides": {"evaluator": "claude-3-opus"}
  },
  "turns": [
    {
      "turn_number": 1,
      "role": "user",
      "content": "Read the data.json file and summarize its contents"
    },
    {
      "turn_number": 2,
      "role": "assistant",
      "content": "The data contains..."
    },
    {
      "turn_number": 3,
      "role": "user",
      "content": "Write the summary to a file named summary.txt"
    }
  ]
}
```

**Metrics Collection**:

1. **Turn 1 Execution** (user input):
   - Latency: 60 ms
   - Token usage: {input_tokens: 30, output_tokens: 0, cached_tokens: 0}

2. **Turn 2 Execution** (assistant response with tool call):
   - Tool call 1: read_file(path="fixtures/data.json")
     - Duration: 20 ms
     - Error: null
   - Latency: 200 ms (model invocation + tool call)
   - Token usage: {input_tokens: 80, output_tokens: 50, cached_tokens: 10}

3. **Turn 3 Execution** (user input):
   - Latency: 50 ms
   - Token usage: {input_tokens: 40, output_tokens: 0, cached_tokens: 0}

4. **Turn 4 Execution** (assistant response with tool call):
   - Tool call 2: write_file(path="summary.txt", content="...")
     - Duration: 15 ms
     - Error: null
   - Latency: 180 ms (model invocation + tool call)
   - Token usage: {input_tokens: 100, output_tokens: 60, cached_tokens: 20}

5. **Evaluation**:
   - Turn 2 output: read_file succeeded, summary generated
     - Rubric "accuracy": score 0.9 (pass)
     - Rubric "tool_usage": score 1.0 (pass)
   - Turn 4 output: write_file succeeded, summary written
     - Rubric "accuracy": score 0.95 (pass)
     - Rubric "tool_usage": score 1.0 (pass)

**Final RunMetrics**:
```json
{
  "run_id": "660e8400-e29b-41d4-a716-446655440000",
  "case_id": "complex-001",
  "metrics": {
    "tool_calls": [
      {
        "tool_name": "read_file",
        "call_index": 0,
        "turn_number": 2,
        "arguments": {"path": "fixtures/data.json"},
        "duration_ms": 20,
        "error": null,
        "timestamp": "2025-01-01T00:00:00Z"
      },
      {
        "tool_name": "write_file",
        "call_index": 1,
        "turn_number": 4,
        "arguments": {"path": "summary.txt", "content": "..."},
        "duration_ms": 15,
        "error": null,
        "timestamp": "2025-01-01T00:00:01Z"
      }
    ],
    "latency_ms": {
      "per_turn": {"1": 60, "2": 200, "3": 50, "4": 180},
      "total_ms": 490,
      "tool_overhead_ms": 35,
      "model_overhead_ms": 455
    },
    "token_usage": {
      "per_turn": {
        "1": {"input_tokens": 30, "output_tokens": 0, "cached_tokens": 0},
        "2": {"input_tokens": 80, "output_tokens": 50, "cached_tokens": 10},
        "3": {"input_tokens": 40, "output_tokens": 0, "cached_tokens": 0},
        "4": {"input_tokens": 100, "output_tokens": 60, "cached_tokens": 20}
      },
      "total_input_tokens": 250,
      "total_output_tokens": 110,
      "total_cached_tokens": 30
    },
    "evaluation_results": {
      "per_turn": {
        "2": {"accuracy": {"score": 0.9, "pass_fail": true}, "tool_usage": {"score": 1.0, "pass_fail": true}},
        "4": {"accuracy": {"score": 0.95, "pass_fail": true}, "tool_usage": {"score": 1.0, "pass_fail": true}}
      },
      "per_rubric": {
        "accuracy": {"scores": [0.9, 0.95], "pass": true},
        "tool_usage": {"scores": [1.0, 1.0], "pass": true}
      },
      "task_success": true,
      "pass_at_k": {"pass@1": 1.0}
    },
    "cost_estimate": 0.0045
  }
}
```

### Primary vs. secondary metrics hierarchy

| Aspect | Primary (Evaluative) | Secondary (Operational) |
|--------|---------------------|------------------------|
| **Metrics** | Token usage (input/output/cached), evaluation results (rubric scores, pass/fail), task success, pass@k | Latency (per-turn, total), LLM invocation count (turn count), tool call count |
| **Purpose** | Measure agent behavior, reasoning quality, and task success independent of infrastructure | Measure infrastructure efficiency, deployment planning, and internal agent optimization |
| **Comparability** | Cross-architecture comparable; depends only on agent logic and model capability | Infrastructure-dependent; varies 20–50% across identical runs due to hardware/system load |
| **Reproducibility** | Deterministic; identical runs produce identical results | Non-deterministic; varies with system state, network latency, hardware contention |
| **Use Case** | Evaluate agent correctness, compare models, assess reasoning quality | Optimize deployment, analyze infrastructure bottlenecks, debug performance issues |
| **Verdict** | ✓ Chosen as primary | ✓ Chosen as secondary |

**Rationale**: Klea's distributed architecture (ADR-0025, ADR-0032) decouples agent reasoning from infrastructure execution. Unlike flat ReAct loops where turn count directly reflects reasoning steps, Klea's multi-agent topology with concurrent tool execution and asynchronous message passing means turn count is not directly comparable across different agent designs. LLM invocation count (turn count) is meaningful for internal agent optimization (e.g., "this agent uses 5 turns on average") but is secondary for cross-architecture evaluation. Latency is infrastructure-dependent and should not be used to evaluate agent quality; it varies significantly across identical runs due to hardware/system load and is useful only for deployment planning and infrastructure analysis.

### Related Decisions

- **ADR-0056 (Test Case Format)**: Defines JSONL format for test cases; ADR-0058 uses JSONL for metrics persistence.
- **ADR-0057 (Run Manager & Isolation)**: Defines run initialization and workspace isolation; ADR-0058 uses RunContext to initialize RunMetricsCollector.
- **ADR-0059 (Result Storage)**: Depends on ADR-0058 to define what metrics to persist and how to aggregate them.
- **ADR-0060 (Harness Execution & Orchestration)**: Depends on ADR-0058 to aggregate metrics across runs and enable real-time monitoring.

### References

- [JSONL Format Specification](https://jsonlines.org/)
- [MLflow Tracking API](https://mlflow.org/docs/latest/tracking.html)
- [Python time module](https://docs.python.org/3/library/time.html)
- [ISO 8601 Timestamp Format](https://en.wikipedia.org/wiki/ISO_8601)
