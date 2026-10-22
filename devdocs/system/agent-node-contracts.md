# Agent node contracts

## 1. Purpose and contract boundary

This document defines the field-level and transition contracts of the Klea general operational agent graph. It covers:

- registered graph nodes and their exact labels;
- the state channels consumed and written by those nodes;
- decisions and deterministic fallbacks at node and router boundaries;
- conditional-edge route labels and their targets;
- the distinction between graph topology and field ownership;
- source and regression-test traceability; and
- artifacts that must be synchronized when a contract changes.

The document is descriptive of the cited implementation, schemas, tests, and architecture records. It does not guarantee that an LLM follows a prompt, that a tool succeeds, that a graph run reaches a particular branch, or that a user interface renders a particular event. Those are runtime outcomes and are outside this contract.

A route label is not itself a registered node. In particular, conditional-edge routers such as `_planner_router` and `_picker_router` are transition-time functions unless the graph explicitly registers them with `add_node`.

## 2. Scope and source hierarchy

### 2.1 Scope

The contract applies to the general operational path assembled in [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py), including the command, mode, safety, route, planning, tool, reasoning, evaluation, review, human-input, answer, cancellation, and memory nodes. Shared node behavior is described where the agent uses a node from `klea_utils`.

The generated Mermaid file is used only to verify topology. Field ownership and fallback behavior are taken from the node implementations and defining schemas, not inferred from the diagram.

### 2.2 Evidence hierarchy

When two artifacts differ, use evidence in the following order.

| Rank | Evidence | Contract use |
|---:|---|---|
| 1 | Node and router implementations under `agent_pkg` and `utils_pkg` | Authoritative runtime code path, update set, and fallback behavior. |
| 2 | [`schemas.py`](../../agent_pkg/klea_agent/schemas.py) and [`state.py`](../../utils_pkg/klea_utils/graph/state.py) | Authoritative field names, types, defaults, validators, and reducers. |
| 3 | `klea_agent.py` path functions, `path_map` entries, and graph registration calls | Authoritative node labels, route labels, and topology. |
| 4 | Focused unit and graph tests | Regression evidence for routing, state updates, and fallbacks. Tests demonstrate covered behavior, not behavior that the implementation does not provide. |
| 5 | Generated [`klea-agent-lang-graph.mmd`](../../agent_pkg/example-configs/klea-agent-lang-graph.mmd) | Topology artifact used to detect stale diagrams. It is not evidence for field-level claims. |
| 6 | ADRs and `devdocs/system` notes | Design rationale and synchronization requirements. Historical `.agents` notes are not normative evidence. |

A change to implementation without synchronized schemas, tests, diagrams, and documentation leaves the published contract incomplete. Conversely, a diagram must not be edited to imply a state update that the owning node does not perform.

### 2.3 Ownership rules

1. A producer owns writes to a state channel for the duration of its contract.
2. A consumer may read a channel without owning it.
3. A topology edge does not imply that the source node writes every field visible to the target node.
4. A prompt variable does not imply a graph-state field, and a state field does not imply that it is rendered into a prompt.
5. An inspection or progress event is not a graph-state update unless the node returns that field in its state update.
6. A router may emit progress or inspection data but normally does not write graph state. Any callback-side update must be stated explicitly.

## 3. Requiredness notation and field conventions

### 3.1 Requiredness

The requiredness column in node input and output tables uses the following notation.

| Notation | Meaning |
|---|---|
| `R` | Required for the stated branch or contract. A missing or unusable value invokes the documented fallback or prevents normal completion. |
| `C` | Conditional. The field is required only for the named branch, mode, lifecycle, or configuration and is otherwise absent or empty. |
| `O` | Optional. The node can proceed without the field and uses its documented default or absence behavior. |

Requiredness is branch-specific. For example, `message_for_user` is conditional on a terminal-answer branch but required before successful terminal delivery.

### 3.2 Types and semantics

- `str`, `int`, and `bool` denote the corresponding Python value shapes.
- `list` and `dict` describe collection shape; the owning schema remains authoritative for item types.
- Capitalized names such as `Mode`, `RouteSchema`, `Plan`, and `EvaluationResult` identify composite schema types.
- A mapping such as `step_outputs[step_id]` denotes a value scoped to one plan step.
- `None` means that the branch does not write the channel. It does not mean that the channel is cleared unless the implementation explicitly clears it.
- Empty string, empty collection, false, and absent are distinct unless a schema reducer makes them equivalent.
- `write timing` states when the channel changes. `lifetime` states when it may be cleared or replaced.

### 3.3 Exact labels

Labels in backticks are exact graph identifiers. They must match registered node names, conditional-edge labels, and `path_map` keys byte for byte. The empty command route is represented by the empty-string label and is not a registered application node.

## 4. State-channel glossary

The canonical definitions are in [`schemas.py`](../../agent_pkg/klea_agent/schemas.py) and [`state.py`](../../utils_pkg/klea_utils/graph/state.py). This glossary records the channels used by the contracts in this document; composite subfields remain governed by their owning schema.

| Channel | Type | Scope | Primary writer | Principal consumers | Lifetime and clearing | Evidence |
|---|---|---|---|---|---|---|
| `messages` | `list` | Conversation | Initialization and message-producing nodes | Mode, safety, route, planning, reasoning, and answer nodes | Conversation-scoped; individual producers may append. | `schemas.py`; `init_graph.py`; `test_general_path_graph.py` |
| `internal_messages` | `list` | Conversation or run | Initialization and internal-work nodes | Memory summarization and internal reasoning | Preserved or replaced according to the owning node and lifecycle. | `state.py`; `summarise_memory.py` |
| `mode` | `Mode` | Run | Mode decision | Mode router and informing branch | Recomputed for the current request. | `mode_router.py`; `test_mode_router.py` |
| `guard_decision` | `str` | Run | Safety guard | Guard router and declining branch | Replaced by each usable guard result or deterministic fallback. | `guard.py`; `guard_router.py` |
| `route` | `RouteSchema` | Run | Route decision | Router, ResetState, and Planner | Replaced on each route decision (`chat`, `continue`, or `task`; `continue` is the default). | `route_decision.py`; `test_route_decision.py`; ADR-0054 |
| `plan` | `Plan` or plan collection | Run | Planner and evaluator | Planner router, binder, tools, reasoning, evaluator, review, and answer composer | Replaced by planner or evaluator according to their contracts; cleared by `ResetState` on a task/continue boundary; cancelled state is preserved for the cancellation branch. | `planner.py`; `reset_state.py`; `test_planner.py`; ADR-0054 |
| `continuation_evidence` | `ContinuationEvidence` or `None` | Session | Answer composer | Planner (`previous_work`) | Written at a terminal answer; preserved by `continue`, cleared by `task` reset. Bounded summary only. | `schemas.py`; `answer_from_results.py`; `reset_state.py`; ADR-0054 |
| `plan_status` | Planner status | Run | Planner and evaluator | Planner and evaluation routers | Advances among planner and evaluation states; terminal values are replaced only by a new run or explicit lifecycle reset. | `planner.py`; `operational_evaluator.py` |
| `tool_calls` | `list` | Batch or run | Tool-call binder | Tool caller and triage | Rebound for a batch or retried group; cleared or replaced according to the graph state contract. | `tool_call_binder.py`; `tools_picker.py`; `tools_caller.py` |
| `tool_results` | `list` | Batch or run | Tool caller | Triage, evaluator, and answer composer | Replaced by the current dispatch result. | `tools_caller.py`; `test_nodes_tools_caller.py` |
| `step_outputs` | `dict` | Plan step | Binder, tools, reasoning, and evaluator as applicable | Evaluator, review preparation, and answer composer | Entries are scoped to plan steps and replaced when their producer writes the step. | `tool_call_binder.py`; `tools_caller.py`; `reasoning.py` |
| `step_errors` | `dict` | Plan step | Binder and tool caller | Triage, evaluator, and answer composer | Replaced by the current error outcome; cleared when the owning operation produces a usable result. | `tool_call_binder.py`; `tools_caller.py`; `triage_router.py` |
| `retry_counts` | `dict` | Plan step or batch | Triage callback | Binder, triage, and evaluator | Counts advance through the callback path and are reset by the applicable run or plan lifecycle. | `triage_router.py`; `test_general_path_graph.py` |
| `evaluation` | `EvaluationResult` or equivalent composite | Run | Operational evaluator | Evaluation router and answer composer | Replaced by the latest consistent evaluation. | `operational_evaluator.py`; `test_operational_evaluator.py` |
| `review_progress` | `str` | Turn or review | Review-progress node | Review and human-review nodes | Written only when pending work exists; absent for a skipped or empty digest. | `review_progress.py`; `test_review_progress.py` |
| `command_route` | `str` | Command turn | Command node | Command-route router | Replaced by each command result; an empty value represents the terminal command route. | `commands.py`; `commands/graph.py`; `test_commands.py` |
| `human_feedback` | `str` | Human interrupt | Awaiting-review node | Review router and planner | Written on review revision; the fixed revision prompt is non-empty when feedback is required. | `await_human.py`; `test_await_human.py` |
| `human_input` | `str` | Human interrupt | Awaiting-input node | Planner | Written when an answer is supplied; remains empty when no answer exists. | `await_human.py`; `test_await_human.py` |
| `user_cancelled` | `bool` | Run or interrupt | Awaiting-review or awaiting-input node | Review and input routers, cancellation node | Set on cancellation and retained through the cancellation route. | `await_human.py`; ADR-0046; ADR-0051 |
| `context_summary` | `str` | Session | Automatic or forced memory summarizer | Later LLM nodes | Replaced only by a usable summary; an unusable summary preserves the prior value. | `summarise_memory.py`; `test_nodes_memory.py` |
| `summarised_till` | `int` | Session | Automatic or forced memory summarizer | User-message summarization | Advances to the summarized user-message cursor only after a usable summary. | `summarise_memory.py` |
| `summarised_internal_till` | `int` | Session | Automatic or forced memory summarizer | Internal-message summarization | Advances to the summarized internal-message cursor only after a usable summary. | `summarise_memory.py` |
| `message_for_user` | `str` | Turn or terminal delivery | Informing, command, declining, answer, response, cancellation, and forced-memory nodes | Terminal delivery | Must be non-empty on terminal delivery; replaced by the relevant terminal producer. | `answer_from_results.py`; `answer_user.py`; `await_human.py` |
| `failure_reason` | `str` | Run | Planner and evaluator | Answer composer and failure router | Written for an unplannable or failed outcome and cleared by the applicable success lifecycle. | `planner.py`; `operational_evaluator.py` |
| `replan_reason` | `str` | Run | Binder, triage, and evaluator | Planner router and answer composer | Present only while replanning is required; replaced by a newer replan reason. | `operational_evaluator.py`; `triage_router.py`; ADR-0035 |

## 5. Registered-node contracts

### 5.1 Registered-node inventory

The graph has the following registered application-node labels. Route labels are documented separately in section 7.

| Exact label | Kind | Primary implementation | Contract summary |
|---|---|---|---|
| `Initializing` | Initialization | [`init_graph.py`](../../agent_pkg/klea_agent/nodes/init_graph.py) | Establishes the graph's initial conversation and run context. |
| `Determining mode` | Decision | [`mode_router.py`](../../agent_pkg/klea_agent/nodes/mode_router.py) | Resolves the operating mode and any source requirement note. |
| `Informing about mode` | Terminal answer | [`mode_router.py`](../../agent_pkg/klea_agent/nodes/mode_router.py) | Delivers the explanation for a mode that cannot proceed. |
| `Running command` | Command | [`command.py`](../../utils_pkg/klea_utils/nodes/command.py), [`commands.py`](../../agent_pkg/klea_agent/commands.py) | Parses and dispatches a session command. |
| `Checking safety` | Guard | [`guard.py`](../../utils_pkg/klea_utils/nodes/guard.py) | Produces the safe or unsafe guard decision. |
| `Declining query` | Terminal answer | [`fixed_answer.py`](../../utils_pkg/klea_utils/nodes/fixed_answer.py) | Delivers the deterministic unsafe-query response. |
| `Deciding route` | Decision | [`route_decision.py`](../../agent_pkg/klea_agent/nodes/route_decision.py) | Chooses chat, continue (default), or task intent. |
| `Resetting state` | Task boundary | [`reset_state.py`](../../agent_pkg/klea_agent/nodes/reset_state.py) | Applies the full task reset or continuation reset. |
| `Planning` | Plan producer | [`planner.py`](../../agent_pkg/klea_agent/nodes/planner.py) | Authors and validates a plan or a structured planner status. |
| `Binding tool calls` | Binder | [`tool_call_binder.py`](../../agent_pkg/klea_agent/nodes/tool_call_binder.py), [`tools_picker.py`](../../utils_pkg/klea_utils/nodes/tools_picker.py) | Binds planned steps to disclosed tools. |
| `Running tools` | Tool dispatcher | [`tools_caller.py`](../../utils_pkg/klea_utils/nodes/tools_caller.py) | Dispatches bound calls and records results or synthetic errors. |
| `Reasoning` | Work step | [`reasoning.py`](../../agent_pkg/klea_agent/nodes/reasoning.py) | Produces the result for a reasoning plan step. |
| `Evaluating` | Evaluator | [`operational_evaluator.py`](../../agent_pkg/klea_agent/nodes/operational_evaluator.py) | Evaluates work and advances, fails, aborts, or requests replanning. |
| `Preparing review` | Review preparation | [`review_progress.py`](../../agent_pkg/klea_agent/nodes/review_progress.py) | Creates a review digest only when pending work exists. |
| `Composing answer` | Answer producer | [`answer_from_results.py`](../../agent_pkg/klea_agent/nodes/answer_from_results.py) | Synthesizes a non-empty terminal answer from run results. |
| `Preparing response` | Answer delivery | [`answer_user.py`](../../agent_pkg/klea_agent/nodes/answer_user.py) | Delivers the final response and prevents a blank terminal reply. |
| `Awaiting review` | Human interrupt | [`await_human.py`](../../agent_pkg/klea_agent/nodes/await_human.py) | Collects review approval, revision feedback, or cancellation. |
| `Awaiting input` | Human interrupt | [`await_human.py`](../../agent_pkg/klea_agent/nodes/await_human.py) | Collects a planner input answer or cancellation. |
| `Cancelled` | Terminal answer | [`fixed_answer.py`](../../utils_pkg/klea_utils/nodes/fixed_answer.py) | Delivers the cancellation response. |
| `Summarizing history` | Automatic memory instance | [`summarise_memory.py`](../../utils_pkg/klea_utils/nodes/summarise_memory.py) | Compacts conversation memory as part of the automatic lifecycle. |
| `Compacting memory` | Forced memory instance | [`summarise_memory.py`](../../utils_pkg/klea_utils/nodes/summarise_memory.py) | Performs command-forced compaction and confirms it to the user. |

### 5.2 `Initializing`

**Purpose.** Establish the request and conversation context before the normal operational path makes a mode, safety, or route decision.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `C` | Supplies the initial request context. | Absent conversation content is handled according to the graph input schema; later nodes must not invent user content. | `init_graph.py`; `schemas.py` |
| `internal_messages` | Internal conversation sequence | `O` | Supplies internal context when the caller has provided it. | Internal context is separate from user-visible content. | `init_graph.py`; `state.py` |
| `context_summary` | `str` | `C` | May supply retained session memory. | A prior summary is preserved unless a later usable summary replaces it. | `summarise_memory.py`; `test_nodes_memory.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `C` | Written at initialization when the node establishes the request conversation. | Must not contain fabricated user messages. | `init_graph.py`; `test_general_path_graph.py` |
| `internal_messages` | Internal conversation sequence | `O` | Initialized only when the run owns an internal trace. | Internal messages are not user-visible by definition. | `init_graph.py`; `state.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| A valid request context is available | Continue to mode determination. | Establishes only the channels owned by initialization. | `init_graph.py` |
| Required request context is absent or unusable | Apply the input or graph-level failure contract rather than inventing a request. | No downstream node may treat missing content as a factual user request. | `schemas.py`; `test_general_path_graph.py` |

### 5.3 `Determining mode`

**Purpose.** Resolve the requested operating mode and determine whether the request can proceed under that mode.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `R` | Supplies the request text used by the mode decision. | Empty or unusable request content cannot be silently converted into a scientific verified request. | `mode_router.py`; `test_mode_router.py` |
| `context_summary` | `str` | `O` | May provide retained context. | It is memory, not new user input. | `summarise_memory.py`; `mode_router.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `mode` | `Mode` | `R` | Written by the mode decision. | The resolved mode and its `note` are consumed by `_mode_router_node`. | `mode_router.py`; `test_mode_router.py` |
| `mode.note` | Nested `str` | `C` | Written within `mode` when the selected mode needs explanation or lacks a required source. | Empty note means proceed; non-empty note means inform. | `mode_router.py`; ADR-0030 |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Request can be handled by the selected mode | `proceed` candidate | Writes resolved `Mode` with an empty note. | `mode_router.py`; ADR-0030 |
| A scientific request lacks its required source | Inform rather than silently treat the request as verified | Writes the resolved mode and a non-empty note. | `mode_router.py`; `test_mode_router.py` |
| Mode processing fails | Apply the mode implementation's documented conservative fallback | The result must leave `mode.note` and `mode` in a router-consumable state. | `mode_router.py` |

### 5.4 `Informing about mode`

**Purpose.** Deliver the explanation produced by the mode decision.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `mode` | `Mode` | `R` | Identifies the mode being explained. | The node does not reinterpret the mode. | `mode_router.py` |
| `mode.note` | Nested `str` | `R` | Supplies the explanation text. | The informing route is selected because the note is non-empty. | `mode_router.py`; `klea_agent.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `R` | Written for the terminal informing branch. | Must be non-empty for delivery. | `mode_router.py`; `answer_user.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| `mode.note` is non-empty | Inform the user. | Writes a non-empty `message_for_user`. | `mode_router.py`; `klea_agent.py` |
| Explanation text is unusable | Use the node's deterministic non-empty fallback | Do not emit a blank terminal response. | `mode_router.py`; `test_general_path_graph.py` |

### 5.5 `Running command`

**Purpose.** Recognize a valid session-command query, execute the command framework, and expose the command result route.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `R` | Supplies the candidate command text. | A non-command query does not enter command dispatch. | `command.py`; `commands.py`; `test_commands.py` |
| `command_route` | `str` | `O` | May contain the prior command result for lifecycle context. | The current command result replaces it when the command node writes a result. | `commands/graph.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `command_route` | `str` | `R` | Written with the command result route. | Allowed routes include `init`, `compact`, and the empty-string terminal route; unknown or unavailable commands produce a direct reply instead of an invalid handoff. | `commands/graph.py`; `test_commands.py` |
| `message_for_user` | `str` | `C` | Written when the command itself produces user-visible output. | Direct replies must be non-empty. | `commands/graph.py`; `test_commands.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Query has valid command syntax | Run the command node. | Writes the command result and route. | `commands/graph.py` |
| Query is not a command | Continue to mode determination. | Does not create a command result. | `klea_agent.py`; `commands/graph.py` |
| Command is unknown or unavailable | Return a direct reply | Does not emit an invalid command handoff route. | `commands/graph.py`; `test_commands.py` |

### 5.6 `Checking safety`

**Purpose.** Produce the deterministic safety decision for the request.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `R` | Supplies the request text checked by the guard. | The guard does not authorize unsafe content. | `guard.py`; `test_general_path_graph.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `guard_decision` | `str` | `R` | Written with `safe` or `unsafe`. | Consumes no conclusion from the safety model and remains `safe`. | `guard.py`; `guard_router.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Guard returns a usable safe result | `safe` | Writes `guard_decision` as `safe`. | `guard.py` |
| Guard returns a usable unsafe result | `unsafe` | Writes `guard_decision` as `unsafe`. | `guard.py` |
| Safety model produces no usable result | Deterministic safe fallback | `guard_decision` remains or becomes `safe`. | `guard.py`; `test_general_path_graph.py` |

### 5.7 `Declining query`

**Purpose.** Deliver the deterministic response for an unsafe request.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `guard_decision` | `str` | `R` | Establishes the unsafe branch selected by `GuardRouterNode`. | The node does not reclassify the decision. | `guard_router.py`; `fixed_answer.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `R` | Written for the declining branch. | Must be non-empty for terminal delivery. | `fixed_answer.py`; `test_general_path_graph.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|---|
| `guard_decision` is `unsafe` | Decline and deliver the fixed answer. | Writes `message_for_user`. | `guard_router.py`; `fixed_answer.py` |
| Fixed-answer synthesis is blank | Use the deterministic non-empty fallback | The terminal response must not be blank. | `fixed_answer.py`; `test_general_path_graph.py` |

### 5.8 `Deciding route`

**Purpose.** Choose whether the request is answered directly as chat, continues
the ongoing work using prior evidence (the default), or starts an unrelated new
goal/plan.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `R` | Supplies the request and conversational context used by route selection. | Missing content does not justify inventing a route rationale. | `route_decision.py`; `test_route_decision.py` |
| `route` | `RouteSchema` | `O` | May contain the previous route during a run. | The current decision replaces the stale route. | `route_decision.py` |
| Forced-chat input | External branch input | `C` | Requires a direct chat answer even if a stale task route exists. | The forced answer must be non-empty before the chat branch is accepted. | `route_decision.py`; `test_route_decision.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `route` | `RouteSchema` | `R` | Written with the selected `chat`, `continue`, or `task` route. | Absent or invalid route output uses the fail-closed continue route. | `route_decision.py`; `test_route_decision.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Route output is usable | Select `chat`, `continue`, or `task` | Writes `route`. | `route_decision.py` |
| Route output is absent or invalid | Select the fail-closed `continue` route | Writes a continue route rather than entering chat accidentally. | `route_decision.py`; `test_route_decision.py` |
| Forced chat has no non-empty answer | Reject the result and retry according to the route contract | Does not enter the task path accidentally. | `route_decision.py`; `test_route_decision.py` |

### 5.9 `Planning`

**Purpose.** Author, validate, and classify the operational plan.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `route` | `RouteSchema` | `R` for the task branch | Establishes that planning is required. | A chat route does not invoke the task planner. | `planner.py`; `klea_agent.py` |
| `messages` | Conversation sequence | `R` | Supplies the objective and conversation context. | The planner may request input but must not invent a missing answer. | `planner.py`; `test_planner.py` |
| `plan` | `Plan` or plan collection | `C` | Supplies prior or revised plan context when replanning. | Revisions are validated like newly authored plans. | `planner.py`; ADR-0035 |
| `human_feedback` | `str` | `C` | Supplies review revision feedback when present. | Empty feedback does not select a revision. | `await_human.py`; `planner.py` |
| `failure_reason` | `str` | `O` | May provide prior failure context during replanning. | It is evidence for planning, not a substitute for a user answer. | `planner.py`; `operational_evaluator.py` |
| `replan_reason` | `str` | `O` | Explains why planning was re-entered. | The reason is consumed by the planner path. | `operational_evaluator.py`; `triage_router.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `C` | Written when the planner produces a structurally valid plan. | Invalid structure or tool-access violations are not executed. | `planner.py`; `test_planner.py` |
| `plan_status` | Planner status | `R` | Written for every classified planner result. | Values are `chat`, `needs_input`, `unplannable`, or `in_progress`. | `planner.py`; `test_planner.py` |
| `failure_reason` | `str` | `C` | Written when the planner fails closed as `unplannable`. | Must explain the cause for the answer composer. | `planner.py`; `test_planner.py` |
| `replan_reason` | `str` | `C` | Preserved or written when planning is entered because of a prior failure. | Consumed by the planner path. | `operational_evaluator.py`; ADR-0035 |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Plan is structurally valid and tool-accessible | `in_progress` | Writes the complete plan and runnable step data. | `planner.py`; `test_planner.py` |
| Planner determines the request needs an answer | `needs_input` | Writes the planner status and the request for input; does not invent the answer. | `planner.py`; `await_human.py` |
| Planner determines no task plan is needed | `chat` | Writes chat planner status and routes to route decision. | `planner.py`; `klea_agent.py` |
| Plan violates structure or tool-access rules | Retry with validation feedback within budget | Invalid plan is not executed. | `planner.py`; `test_planner.py` |
| Planning budget is exhausted or no viable plan exists | `unplannable` | Writes `failure_reason`; no invalid plan is executed. | `planner.py`; ADR-0035 |

### 5.10 `Binding tool calls`

**Purpose.** Bind the planned tool steps in the current batch to the disclosed tool implementations suggested by the plan.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Supplies the frontier step or grouped steps to bind. | Only planned tool steps are eligible. | `tool_call_binder.py`; `test_tool_call_binder.py` |
| Available tool context | Tool descriptions and permitted implementations | `R` | Restricts the binder to tools planned and disclosed for the step. | The binder must not substitute an unplanned tool. | `tools_picker.py`; ADR-0020; ADR-0034 |
| Prior binder feedback | Plan, tool, and retry context | `C` | Supports a retry of the same planned tool group. | A retry does not broaden tool access. | `tools_picker.py`; `test_nodes_tools_picker.py` |
| `retry_counts` | `dict` | `O` | Indicates whether the empty-selection retry budget remains. | Count updates occur in the caller callback, not in the router path function. | `triage_router.py`; `klea_agent.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `tool_calls` | `list` | `C` | Written when at least one planned step is bound. | Calls must correspond to the planned tool group. | `tool_call_binder.py`; `tools_picker.py` |
| `step_outputs` | `dict` | `C` | Written with a synthetic error record for an unbindable step when required by the binder contract. | The synthetic record explains the failure without claiming tool execution. | `tool_call_binder.py`; ADR-0035 update |
| `step_errors` | `dict` | `C` | Written for a deliberate binder failure. | Contains one empty-tool failure with a reason. | `tool_call_binder.py` |
| `replan_reason` | `str` | `C` | Written when binder failure requires Planner re-entry. | Must explain that the suggested tool could not be bound. | `tool_call_binder.py`; ADR-0035 update |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| A usable call exists and another batch step is unbound | `bind_next` | Writes the bound call and leaves the remaining step for the next binding pass. | `tools_picker.py`; `klea_agent.py` |
| All batch steps are bound | `dispatch` | Writes the complete group for tool execution. | `tools_picker.py`; `klea_agent.py` |
| Selection is empty or malformed and retry budget remains | `retry_picker` | Advances the attempt counter; no server call occurs. | `tools_picker.py`; `test_nodes_tools_picker.py` |
| Binder cannot bind a suggested tool | Deliberate failure and Planner escalation | Writes one synthetic error, step output, and replan reason; does not substitute another tool. | `tool_call_binder.py`; ADR-0035 update |
| Retry budget is exhausted | `replan` | Leaves a failure reason for the Planner. | `tools_picker.py`; `klea_agent.py` |

### 5.11 `Running tools`

**Purpose.** Dispatch the bound calls and record each tool result or synthetic error.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `tool_calls` | `list` | `R` | Supplies the calls to dispatch. | Only bound and access-approved calls may reach the MCP server. | `tools_caller.py`; `mcp-permissions.md` |
| `plan` | `Plan` or plan collection | `O` | Provides step identity for associating results and errors. | Results are associated with the originating planned step. | `tools_caller.py`; `triage_router.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `tool_results` | `list` | `R` | Written after dispatch of the current group. | A denied or failed call is represented by the contract's error result rather than a fabricated success. | `tools_caller.py`; `test_nodes_tools_caller.py` |
| `step_outputs` | `dict` | `C` | Written for a successful or usable tool result associated with a step. | Step-scoped output is replaced by the current result. | `tools_caller.py`; `operational_evaluator.py` |
| `step_errors` | `dict` | `C` | Written for a denied or failed call. | Denial is detected before dispatch; execution failure is detected after dispatch. | `tools_caller.py`; `mcp-permissions.md` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Call passes the access and path policy | Dispatch to the MCP server. | Writes the tool result and any step-scoped output. | `tools_caller.py`; `mcp-permissions.md` |
| Call is denied by access or path policy | Return a non-halting synthetic error. | The MCP server is not invoked. | `mcp-permissions.md`; ADR-0007; ADR-0037 |
| Tool execution fails | Record the error for normal triage. | Writes the tool result and step error; retry count changes occur in the caller callback. | `tools_caller.py`; `triage_router.py` |

### 5.12 `Reasoning`

**Purpose.** Produce the result for a planned reasoning step.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `R` | Supplies the conversational context used by the reasoning node. | Model output does not replace missing user facts. | `reasoning.py`; `test_reasoning.py` |
| `plan` | `Plan` or plan collection | `R` | Supplies the reasoning step and its objective. | Only a planned reasoning step is executed. | `reasoning.py`; `klea_agent.py` |
| `step_outputs` | `dict` | `O` | Supplies prior tool or work results relevant to the reasoning step. | Missing prior work is handled by the node's documented behavior. | `reasoning.py`; `operational_evaluator.py` |
| `evaluation` | Evaluation result | `O` | May provide evaluator context on a later pass. | It does not bypass evaluator validation. | `operational_evaluator.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `step_outputs` | `dict` | `R` | Written with the reasoning step's result or explicit fallback marker. | The step remains available to evaluator handling. | `reasoning.py`; `test_reasoning.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Reasoning model returns a usable conclusion | Record the conclusion for the step. | Writes the step output. | `reasoning.py` |
| Reasoning model returns no conclusion | Emit the explicit no-conclusion fallback marker. | Does not answer the user from this node; the step remains subject to evaluator handling. | `reasoning.py`; `test_reasoning.py` |

### 5.13 `Evaluating`

**Purpose.** Evaluate completed work and decide whether to finish, continue, fail, abort, or replan.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Supplies plan status, step state, and pending work. | Inconsistent completion cannot silently discard pending steps. | `operational_evaluator.py`; `test_operational_evaluator.py` |
| `tool_results` | `list` | `O` | Supplies dispatch outcomes for evaluation. | Absent tool results are interpreted through the plan and step channels, not fabricated. | `operational_evaluator.py` |
| `step_outputs` | `dict` | `O` | Supplies tool and reasoning outputs. | Outputs are evaluated as step-scoped evidence. | `operational_evaluator.py` |
| `step_errors` | `dict` | `O` | Supplies failures for per-step triage. | Errors remain distinguishable from successful outputs. | `triage_router.py`; `operational_evaluator.py` |
| `retry_counts` | `dict` | `O` | Supplies retry state for per-step failure decisions. | Count updates occur in the caller callback rather than the path function. | `triage_router.py`; `klea_agent.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan_status` | Planner status | `R` | Written after a consistent evaluation. | May reflect completed, aborted, or an intermediate plan state. | `operational_evaluator.py`; `test_operational_evaluator.py` |
| `evaluation` | Evaluation result | `R` | Written with the per-step verdict and optional overall outcome. | Empty or inconsistent evaluation is not accepted silently. | `operational_evaluator.py`; `test_operational_evaluator.py` |
| `retry_counts` | `dict` | `C` | Updated for a per-step failure when the caller callback advances the count. | The evaluator does not claim that a router path function updates counts. | `triage_router.py`; `operational_evaluator.py` |
| `failure_reason` | `str` | `C` | Written when evaluation fails closed or aborts. | Explains the failure to the answer composer. | `operational_evaluator.py` |
| `replan_reason` | `str` | `C` | Written when the evaluator requests a new plan. | A newer reason replaces an older one. | `operational_evaluator.py`; `klea_agent.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Evaluation is usable and all required work is complete | `plan_done` | Writes completed plan status and evaluation. | `operational_evaluator.py` |
| Evaluation is usable and the plan is aborted | `abort` | Writes aborted status and a failure explanation where required. | `operational_evaluator.py`; `klea_agent.py` |
| A replan reason is present | `need_replan` | Preserves the reason for Planner re-entry. | `operational_evaluator.py`; `klea_agent.py` |
| Evaluation is empty or inconsistent | Retry with feedback, then fail closed | No pending work is silently discarded and no inconsistent plan completion is accepted. | `operational_evaluator.py`; control-flow note |

### 5.14 `Preparing review`

**Purpose.** Create the progress digest presented when a review step pauses the plan.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Identifies the review step and current work context. | A review with no pending work is resolved defensively rather than pausing. | `review_progress.py`; `_next_dispatch`; ADR-0051 |
| `review_progress` | `str` | `O` | May contain the previous digest for comparison. | A skipped or empty digest does not create stale progress. | `review_progress.py`; `test_review_progress.py` |
| `step_outputs` | `dict` | `O` | Supplies completed work since the prior review. | Missing work means there may be no digest to write. | `review_progress.py`; `operational_evaluator.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `review_progress` | `str` | `C` | Written only when work exists since the last review. | A skipped or empty digest leaves no stale progress. | `review_progress.py`; `test_review_progress.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Work exists since the prior review | Generate a digest. | Writes `review_progress`. | `review_progress.py` |
| No pending work exists | Skip the digest and allow defensive plan completion. | Does not create an interrupt or stale progress. | `_next_dispatch`; ADR-0051 |
| Digest generation is empty | Skip or fail according to the review-progress contract. | Does not write an empty replacement. | `review_progress.py`; `test_review_progress.py` |

### 5.15 `Composing answer`

**Purpose.** Synthesize the final user-facing answer from the plan, evaluation, and completed step results.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Supplies the completed or failed plan context. | The composer distinguishes success from failure. | `answer_from_results.py`; `test_answer_from_results.py` |
| `evaluation` | Evaluation result | `O` | Supplies the evaluator's overall and step outcomes. | Absent evaluation is handled by the success or failure fallback. | `operational_evaluator.py` |
| `step_outputs` | `dict` | `O` | Supplies completed work to synthesize. | Missing results must not be fabricated. | `answer_from_results.py` |
| `step_errors` | `dict` | `O` | Supplies failure context. | Failure is described from recorded errors and status. | `answer_from_results.py` |
| `failure_reason` | `str` | `O` | Supplies the planner or evaluator failure explanation. | The composer explains the recorded failure. | `planner.py`; `operational_evaluator.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `R` | Written for the terminal answer branch. | Must be non-empty; a blank synthesis uses the deterministic success or failure fallback. | `answer_from_results.py`; `test_answer_from_results.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Results support a successful answer | Compose from recorded results. | Writes non-empty `message_for_user`. | `answer_from_results.py` |
| The plan failed or was aborted | Compose the failure explanation. | Writes non-empty `message_for_user` and preserves the recorded failure. | `answer_from_results.py`; `planner.py` |
| Final synthesis is blank | Use the deterministic success or failure fallback | `message_for_user` remains non-empty and no failure artefact is fabricated. | `answer_from_results.py`; `test_answer_from_results.py` |

### 5.16 `Preparing response`

**Purpose.** Deliver the composed terminal response to the user.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `C` | Supplies the composed terminal payload when present. | A blank payload is replaced by the delivery node's fixed non-empty retry message. | `answer_user.py`; `test_general_path_graph.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `R` | Written or preserved as the final delivery payload. | The user never receives a blank final reply. | `answer_user.py`; `test_general_path_graph.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Composed response is non-empty | Deliver it. | Preserves the non-empty terminal payload. | `answer_user.py` |
| Final response is blank | Substitute the delivery node's non-empty retry message | `message_for_user` remains non-empty. | `answer_user.py`; `test_general_path_graph.py` |

### 5.17 `Awaiting review`

**Purpose.** Collect a human decision for a planned review step -- a **same-run approval gate** for the work that runs after it. Presenting a plan for the user to read is a deliverable, not a review. This is a distinct graph instance from `Awaiting input`, although both use `await_human.py`.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Identifies the pending review step and plan state. | A cancelled plan takes the cancellation route. | `await_human.py`; ADR-0051 |
| `review_progress` | `str` | `O` | Supplies the review digest when work exists. | No stale digest is created for an empty review. | `review_progress.py`; `await_human.py` |
| `human_feedback` | `str` | `O` | May contain a prior review response. | Revision requires feedback. | `await_human.py`; `test_await_human.py` |
| `user_cancelled` | `bool` | `O` | May identify an existing cancellation. | Cancellation takes priority over approval or revision. | `await_human.py`; ADR-0046 |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `human_feedback` | `str` | `C` | Written when the reviewer approves or supplies revision feedback. | Revision without feedback receives the fixed non-empty revision prompt. | `await_human.py`; `test_await_human.py` |
| `user_cancelled` | `bool` | `C` | Set when the review is cancelled. | The plan is routed to cancellation and remaining steps do not execute. | `await_human.py`; ADR-0046 |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Reviewer approves | Resolve the review step and continue with the next frontier step. | Writes the approval response state. | `await_human.py`; ADR-0051 |
| Reviewer requests revision | Re-enter Planner with feedback. | Writes non-empty `human_feedback` and selects the `planner` route. | `await_human.py`; ADR-0051 |
| Reviewer cancels | Route to cancellation. | Sets `user_cancelled`; remaining steps do not execute. | `await_human.py`; ADR-0046 |
| Revision is requested without feedback | Use the fixed non-empty revision prompt | `human_feedback` remains non-empty and Planner is selected. | `await_human.py`; `test_await_human.py` |

### 5.18 `Awaiting input`

**Purpose.** Collect a planner-requested answer from a human. This is a distinct graph instance from `Awaiting review`, although both use `await_human.py`.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `plan` | `Plan` or plan collection | `R` | Carries the planner's input request and current plan context. | The node does not infer an answer from the plan. | `planner.py`; `await_human.py` |
| `human_input` | `str` | `O` | May contain a prior answer. | A missing answer remains empty rather than being invented. | `await_human.py`; `test_await_human.py` |
| `user_cancelled` | `bool` | `O` | May identify an existing cancellation. | Cancellation takes priority over an answer. | `await_human.py`; ADR-0046 |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `human_input` | `str` | `C` | Written when the human supplies an answer. | Missing answers remain empty. | `await_human.py`; `test_await_human.py` |
| `user_cancelled` | `bool` | `C` | Set when the input request is cancelled. | The plan is routed to cancellation and remaining steps do not execute. | `await_human.py`; ADR-0046 |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| An answer is supplied | Re-enter Planner. | Writes `human_input` and selects the `planner` route. | `await_human.py`; ADR-0046 |
| The user cancels | Route to cancellation. | Sets `user_cancelled`. | `await_human.py`; ADR-0046 |
| No answer is supplied | Keep the input unanswered and do not invent facts. | `human_input` remains empty. | `await_human.py`; `test_await_human.py` |

### 5.19 `Cancelled`

**Purpose.** Deliver the deterministic response after a human cancels a review or input request.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `user_cancelled` | `bool` | `R` | Establishes the cancellation branch. | The node does not resume pending work. | `await_human.py`; `fixed_answer.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `message_for_user` | `str` | `R` | Written for terminal cancellation delivery. | Must be non-empty. | `fixed_answer.py`; `test_await_human.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| `user_cancelled` is true | Deliver the cancellation answer and end the run. | Writes `message_for_user`; remaining steps do not execute. | `await_human.py`; `fixed_answer.py` |
| Cancellation text is blank | Use the fixed-answer fallback | The terminal response remains non-empty. | `fixed_answer.py`; `test_general_path_graph.py` |

### 5.20 `Summarizing history`

**Purpose.** Perform automatic conversation-memory compaction as part of the normal memory lifecycle. This node is distinct from the command-forced `Compacting memory` instance even though both use `summarise_memory.py`.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `O` | Supplies user-visible history eligible for summarization. | Existing messages are not erased by an unusable summary. | `summarise_memory.py`; `test_nodes_memory.py` |
| `internal_messages` | Internal conversation sequence | `O` | Supplies internal history eligible for the internal cursor. | Internal and user cursors advance independently. | `summarise_memory.py`; `test_nodes_memory.py` |
| `context_summary` | `str` | `C` | Supplies the prior retained summary. | Preserved when the new summary is blank. | `summarise_memory.py`; `test_nodes_memory.py` |
| `summarised_till` | `int` | `C` | Supplies the prior user-message cursor. | Advanced only after a usable summary. | `summarise_memory.py` |
| `summarised_internal_till` | `int` | `C` | Supplies the prior internal-message cursor. | Advanced only after a usable summary. | `summarise_memory.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `context_summary` | `str` | `C` | Replaced only by a usable new summary. | A blank summary preserves the prior summary. | `summarise_memory.py`; `test_nodes_memory.py` |
| `summarised_till` | `int` | `C` | Advanced to the summarized user-message cursor. | Not advanced for an unusable summary. | `summarise_memory.py` |
| `summarised_internal_till` | `int` | `C` | Advanced to the summarized internal-message cursor. | Not advanced for an unusable summary. | `summarise_memory.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Summary is usable | Replace the retained summary and advance the applicable cursors. | Writes the three summary channels. | `summarise_memory.py` |
| Summary is blank | Preserve the prior summary and both cursors. | No state is erased by the unusable summary. | `summarise_memory.py`; `test_nodes_memory.py` |
| Memory is disabled or unavailable | Do not invoke the summarizer as if it had succeeded. | The automatic lifecycle remains outside the active memory path. | `summarise_memory.py`; `test_commands.py` |

### 5.21 `Compacting memory`

**Purpose.** Perform command-forced memory compaction and deliver a confirmation. This is a distinct graph instance from the automatic `Summarizing history` node even though both use `summarise_memory.py`.

#### Inputs

| Field | Kind | Requiredness | Effect and write timing | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `messages` | Conversation sequence | `O` | Supplies user-visible history eligible for compaction. | Existing history is preserved when the summary is unusable. | `summarise_memory.py`; `test_nodes_memory.py` |
| `internal_messages` | Internal conversation sequence | `O` | Supplies internal history eligible for compaction. | Internal and user cursors remain independent. | `summarise_memory.py` |
| `context_summary` | `str` | `C` | Supplies the prior retained summary. | Preserved for a blank summary. | `summarise_memory.py` |
| `summarised_till` | `int` | `C` | Supplies the prior user-message cursor. | Advanced only after a usable summary. | `summarise_memory.py` |
| `summarised_internal_till` | `int` | `C` | Supplies the prior internal-message cursor. | Advanced only after a usable summary. | `summarise_memory.py` |

#### Outputs

| Field | Kind | Requiredness | Write timing and effect | Validation, fallback, and lifetime | Evidence |
|---|---|---|---|---|---|
| `context_summary` | `str` | `C` | Replaced only by a usable forced-compaction summary. | Preserves the prior value for a blank summary. | `summarise_memory.py`; `test_nodes_memory.py` |
| `summarised_till` | `int` | `C` | Advanced to the summarized user-message cursor. | Not advanced for an unusable summary. | `summarise_memory.py` |
| `summarised_internal_till` | `int` | `C` | Advanced to the summarized internal-message cursor. | Not advanced for an unusable summary. | `summarise_memory.py` |
| `message_for_user` | `str` | `R` | Written after successful forced compaction to confirm the operation. | Confirms compaction rather than implying that a blank summary succeeded. | `summarise_memory.py`; `test_commands.py` |

#### Decision and failure behavior

| Condition | Decision or failure result | State effect | Evidence |
|---|---|---|---|
| Memory is enabled and forced compaction produces a usable summary | Complete forced compaction and confirm it. | Writes the summary, cursors, and `message_for_user`. | `summarise_memory.py`; `test_commands.py` |
| Summary is blank | Preserve the prior summary and cursors and do not claim successful compaction. | No summary channel is erased. | `summarise_memory.py`; `test_nodes_memory.py` |
| Memory is disabled or unavailable | Do not route to a successful compaction instance. | The command framework returns an unavailable or direct response rather than an invalid handoff. | `commands/graph.py`; `test_commands.py` |

## 6. Decision and failure contracts

### 6.1 Decision contracts

| Subject | Decision input | Decision result | State effect | Failure or ambiguity behavior | Evidence |
|---|---|---|---|---|---|
| Mode decision | Requested mode and source availability | Proceed or inform | Writes resolved `Mode` and note. | A scientific request without the required source is informed rather than silently treated as verified. | `mode_router.py`; ADR-0030 |
| Safety guard | Request text | `safe` or `unsafe` | Writes `guard_decision`. | Processing failure uses the guard's deterministic `safe` fallback. | `guard.py`; `guard_router.py` |
| Entry route | Request and conversation | `chat`, `continue`, or `task` | Writes `RouteSchema`; chat may write the inline answer; continue/task proceed through `ResetState`. | Defaults to `continue`; forced chat ignores a stale route and requires a non-empty answer; continue preserves bounded evidence, task resets it. | `route_decision.py`; `reset_state.py`; `test_route_decision.py`; ADR-0054 |
| Planner status | Authored plan | `chat`, `needs_input`, `unplannable`, or `in_progress` | Writes the complete plan and related run signals. | Invalid or exhausted planning becomes `unplannable` with a failure reason; budget exhaustion can become `needs_input`. | `planner.py`; ADR-0035 |
| Binder selection | Group plan, tools, prior feedback | Usable call, deliberate failure, or empty glitch | Writes tool calls and, on deliberate failure, a synthetic error and replan reason. | Empty glitch retries within budget; deliberate failure escalates immediately to Planner. | `_picker_router`; ADR-0035 update |
| Tool triage | Tool results and retry counts | `retry`, `evaluate`, or `replan` | Reads state; count updates occur in the caller callback, not the path function. | Per-step errors retry while within budget and otherwise replan. | `triage_router.py` |
| Review response | Human action and feedback | Approve, revise, or cancel | Approval resolves review steps; revision writes feedback; cancellation sets `user_cancelled`. | Revision without feedback receives the fixed non-empty revision prompt. | `await_human.py`; ADR-0051 |
| Input response | User answers or cancellation | Planner re-entry or cancellation | Writes `human_input` or `user_cancelled`. | Missing answers remain empty rather than inventing facts. | `await_human.py`; ADR-0046 |
| Evaluator verdict | Batch outcome and criteria | Per-step verdict plus optional overall outcome | Updates plan statuses, counters, evaluation, and failure or replan signals. | Empty or inconsistent results are retried and then fail closed according to the consistency rules. | `operational_evaluator.py`; control-flow note |
| Review progress | Work since last review | Generate or skip digest | Writes `review_progress` only when work exists. | A skipped or empty digest does not create stale progress. | `review_progress.py` |
| Command handoff | Command result route | Terminal, `init`, or `compact` | Writes `command_route`. | Unknown or unavailable commands produce a direct reply instead of an invalid handoff. | `commands/graph.py`; session-command note |

### 6.2 Failure and fallback contracts

| Failure | Detection point | Contractual response | State effect | Evidence |
|---|---|---|---|---|
| Safety model produces no usable result | Guard processing | Return the deterministic safe fallback. | `guard_decision` remains safe. | `guard.py` |
| Route output is absent or invalid | Route schema and forced-chat validation | Use the fail-closed continue route; on a forced-chat retry, reject and retry the model. | Invalid forced chat does not enter the task path accidentally. | `route_decision.py`; `test_route_decision.py` |
| Plan violates structure or tool-access rules | Planner validation | Retry with validation feedback; after the bounded retry budget, fail closed as unplannable. | Failure reason is written and no invalid plan is executed. | `planner.py`; `test_planner.py` |
| Planner cannot produce a viable plan | Planner result | Emit `unplannable` and explain the cause. | `failure_reason` is written; the answer composer explains failure. | `planner.py`; control-flow note |
| Binder cannot bind a suggested tool | Binder normalization | Record one empty-tool failure with a reason and escalate to Planner. | Synthetic error, step output, and replan reason are written; no unplanned tool is substituted. | `_record_picker_failure`; ADR-0035 update |
| Binder emits an empty or malformed selection | Binder normalization | Retry the same planned tool group within the picker budget, then escalate. | Attempt counter advances; no server call occurs. | `_picker_router`; `tools_picker.py` |
| Tool call is denied by access or path policy | Pre-dispatch gate | Return a non-halting synthetic error and follow normal triage. | The MCP server is not invoked. | `mcp-permissions.md`; ADR-0007/0037 |
| Tool execution fails | Post-dispatch result | Triage per originating step: retry within budget or replan. | Tool result, step output, retry count, and possibly replan reason are updated. | `triage_router.py`; control-flow note |
| Reasoning model returns no conclusion | Reasoning processing | Emit the explicit no-conclusion fallback marker; do not answer the user from this node. | The step remains subject to evaluator handling. | `reasoning.py` |
| Evaluator returns an empty or inconsistent result | Evaluator validation and state consistency checks | Retry with feedback; then apply the documented fail-closed route. | Empty evaluation escalates to replan; inconsistent plan completion cannot silently discard pending work. | `operational_evaluator.py`; control-flow note |
| Review revision has no feedback | Review response | Write the fixed request to revise the plan. | `human_feedback` remains non-empty so the router selects Planner. | `await_human.py` |
| User cancels a review or input | HITL response | Mark the plan `user_cancelled` and route to `Cancelled`. | Remaining steps do not execute. | `await_human.py`; ADR-0046 |
| Memory summary is blank | Summarizer update | Preserve the prior summary and cursors. | No state is erased by an unusable summary. | `summarise_memory.py`; `test_nodes_memory.py` |
| Final answer synthesis is blank | Answer composer | Use the deterministic success or failure fallback. | `message_for_user` remains non-empty; failure persists no artefact. | `answer_from_results.py`; `test_answer_from_results.py` |
| Final response is blank | `AnswerUser` | Substitute the delivery node's non-empty retry message. | The user never receives a blank final reply. | `answer_user.py` |
| Review step has no pending work after it | Router defensive check | Resolve it as plan completion rather than pausing a pointless gate. | No review interrupt is created. | `_next_dispatch`; ADR-0051 |

## 7. Conditional-edge router contracts

A router is a transition-time path function. Unless explicitly noted, it does not write graph state. A router may emit progress or inspection data, but that is not a state update. The route labels below are exact and must match the `path_map` keys in `klea_agent.py`.

### 7.1 Router ownership summary

| Router | Kind | Primary implementation | State effect |
|---|---|---|---|
| `_command_router` | Entry path function | `klea_agent.py` | None |
| `command_query_router` | Command-query path function | `commands/graph.py` and command routing code | None |
| `command_route_router` | Command-result path function | `commands/graph.py` | None |
| `_mode_router_node` | Mode path function | `klea_agent.py` | None |
| `GuardRouterNode.execute` | Router node | `guard_router.py` | None; emits progress only |
| `_route_decision_router` | Route path function | `klea_agent.py` | None |
| `_planner_router` | Planner path function | `klea_agent.py` | None |
| `_review_router` | Review path function | `klea_agent.py` | None |
| `_await_human_router` | Human-input path function | `klea_agent.py` | None |
| `_picker_router` | Binder path function | `klea_agent.py` | None |
| `TriageRouter.execute` | Router node | `triage_router.py` | None; the caller callback may update retry counts |
| `_evaluation_router` | Evaluation path function | `klea_agent.py` | None |

### 7.2 Entry and command routers

#### `_command_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Query has valid command syntax | `command` | `Running command` | None | `klea_agent.py`; `commands/graph.py` |
| Query is not a command | `continue` | `Determining mode` | None | `klea_agent.py`; `commands/graph.py` |

#### `command_query_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Query has valid command syntax | `command` | `Running command` | None | `commands/graph.py`; `test_commands.py` |
| Query is not a command | `continue` | `Determining mode` | None | `commands/graph.py`; `klea_agent.py` |

#### `command_route_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Command result route is empty | empty-string label | `END` | None | `commands/graph.py`; `klea_agent.py` |
| Command result route is `init` | `init` | `Determining mode` | None | `commands/graph.py`; `klea_agent.py` |
| Command result route is `compact` and memory is enabled | `compact` | `Compacting memory` | None | `commands/graph.py`; `klea_agent.py` |

The empty command route is represented by the empty-string key and maps to `END`; it is not an application-node label. Unknown or unavailable commands do not create an invalid handoff.

#### `_mode_router_node`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| `mode.note` is empty | `proceed` | `Checking safety` | None | `klea_agent.py`; `mode_router.py` |
| `mode.note` is non-empty | `inform` | `Informing about mode` | None | `klea_agent.py`; `mode_router.py` |

#### `GuardRouterNode.execute`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Guard decision is safe | `safe` | `Deciding route` | None; emits progress only | `guard_router.py`; `klea_agent.py` |
| Guard decision is unsafe | `unsafe` | `Declining query` | None; emits progress only | `guard_router.py`; `klea_agent.py` |

#### `_route_decision_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Normal route is chat, or a forced-chat plan status selects chat | `chat` | `Preparing response` | None | `klea_agent.py`; `route_decision.py` |
| Normal route is continue (default) | `continue` | `Resetting state` | None | `klea_agent.py`; `route_decision.py` |
| Normal route is task | `task` | `Resetting state` | None | `klea_agent.py`; `route_decision.py` |

### 7.3 Planner and human-interrupt routers

#### `_planner_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Plan is unplannable or has no runnable step | `failure` | `Composing answer` | None | `klea_agent.py`; `planner.py` |
| Plan status is chat | `chat` | `Deciding route` | None | `klea_agent.py`; `planner.py` |
| Plan status is needs input | `needs_input` | `Awaiting input` | None | `klea_agent.py`; `planner.py` |
| Plan status is completed | `plan_done` | `Composing answer` | None | `klea_agent.py`; `operational_evaluator.py` |
| Frontier begins with a review step | `review` | `Preparing review` | None | `klea_agent.py`; `review_progress.py` |
| Frontier begins with a tool step | `tool` | `Binding tool calls` | None | `klea_agent.py`; `tool_call_binder.py` |
| Frontier begins with a reasoning step | `reasoning` | `Reasoning` | None | `klea_agent.py`; `reasoning.py` |

#### `_review_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Plan was cancelled | `cancelled` | `Cancelled` | None | `klea_agent.py`; `await_human.py` |
| Revision feedback is present | `planner` | `Planning` | None | `klea_agent.py`; `await_human.py` |
| Approval and next frontier is a tool step | `tool` | `Binding tool calls` | None | `klea_agent.py`; `tool_call_binder.py` |
| Approval and next frontier is a reasoning step | `reasoning` | `Reasoning` | None | `klea_agent.py`; `reasoning.py` |
| Approval and next frontier is another review | `review` | `Preparing review` | None | `klea_agent.py`; `review_progress.py` |
| Approval and no step remains | `plan_done` | `Composing answer` | None | `klea_agent.py`; `operational_evaluator.py` |

#### `_await_human_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Plan was cancelled | `cancelled` | `Cancelled` | None | `klea_agent.py`; `await_human.py` |
| Input was supplied | `planner` | `Planning` | None | `klea_agent.py`; `await_human.py` |

`Awaiting review` and `Awaiting input` use separate graph instances even though their response handling is implemented by the same shared `await_human.py` module. Review feedback selects `_review_router`; input answers select `_await_human_router`.

### 7.4 Work-loop routers

#### `_picker_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| A usable call exists and another batch step is unbound | `bind_next` | `Binding tool calls` | None | `klea_agent.py`; `tools_picker.py` |
| All batch steps are bound | `dispatch` | `Running tools` | None | `klea_agent.py`; `tools_picker.py` |
| Selection is empty and the retry budget remains | `retry_picker` | `Binding tool calls` | None | `klea_agent.py`; `tools_picker.py` |
| Deliberate failure or retry budget exhausted | `replan` | `Planning` | None | `klea_agent.py`; `tools_picker.py` |

#### `TriageRouter.execute`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Every errored step is within its retry budget | `retry` | `Binding tool calls` | None; the caller callback already updated counts | `triage_router.py`; `klea_agent.py` |
| No tool error occurred | `evaluate` | `Evaluating` | None; emits progress and inspection data | `triage_router.py`; `klea_agent.py` |
| Any errored step is over budget | `replan` | `Planning` | None; emits progress and inspection data | `triage_router.py`; `klea_agent.py` |

#### `_evaluation_router`

| Condition | Exact route label | Target | State effect | Evidence |
|---|---|---|---|---|
| Plan status is completed | `plan_done` | `Composing answer` | None | `klea_agent.py`; `operational_evaluator.py` |
| Plan status is aborted | `abort` | `Composing answer` | None | `klea_agent.py`; `operational_evaluator.py` |
| A replan reason is present | `need_replan` | `Planning` | None | `klea_agent.py`; `operational_evaluator.py` |
| Next frontier is a tool step | `tool` | `Binding tool calls` | None | `klea_agent.py`; `tool_call_binder.py` |
| Next frontier is a reasoning step | `reasoning` | `Reasoning` | None | `klea_agent.py`; `reasoning.py` |
| Next frontier is a review step | `review` | `Preparing review` | None | `klea_agent.py`; `review_progress.py` |

## 8. Traceability index

### 8.1 Registered nodes

| Exact label | Primary implementation | Regression coverage | Architectural or topology reference |
|---|---|---|---|
| `Initializing` | [`init_graph.py`](../../agent_pkg/klea_agent/nodes/init_graph.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [Control flow](agent-general-path-control-flow.md) |
| `Determining mode` | [`mode_router.py`](../../agent_pkg/klea_agent/nodes/mode_router.py) | [`test_mode_router.py`](../../agent_pkg/tests/test_mode_router.py), [`test_mode_graph.py`](../../agent_pkg/tests/test_mode_graph.py) | [ADR-0030](../adr/0030-agent-operating-modes-and-assurance-levels.md) |
| `Informing about mode` | [`mode_router.py`](../../agent_pkg/klea_agent/nodes/mode_router.py) | [`test_mode_graph.py`](../../agent_pkg/tests/test_mode_graph.py) | [ADR-0030](../adr/0030-agent-operating-modes-and-assurance-levels.md) |
| `Running command` | [`command.py`](../../utils_pkg/klea_utils/nodes/command.py), [`commands.py`](../../agent_pkg/klea_agent/commands.py) | [`test_commands.py`](../../agent_pkg/tests/test_commands.py) | [ADR-0047](../adr/0047-session-command-framework.md), [Session commands](session-commands.md) |
| `Checking safety` | [`guard.py`](../../utils_pkg/klea_utils/nodes/guard.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [C4 agent component](c4-component-agent.md) |
| `Declining query` | [`fixed_answer.py`](../../utils_pkg/klea_utils/nodes/fixed_answer.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [C4 agent component](c4-component-agent.md) |
| `Deciding route` | [`route_decision.py`](../../agent_pkg/klea_agent/nodes/route_decision.py) | [`test_route_decision.py`](../../agent_pkg/tests/test_route_decision.py), [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md) |
| `Planning` | [`planner.py`](../../agent_pkg/klea_agent/nodes/planner.py) | [`test_planner.py`](../../agent_pkg/tests/test_planner.py), [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md), [ADR-0041](../adr/0041-plan-step-granularity-and-parallelism.md) |
| `Binding tool calls` | [`tool_call_binder.py`](../../agent_pkg/klea_agent/nodes/tool_call_binder.py), [`tools_picker.py`](../../utils_pkg/klea_utils/nodes/tools_picker.py) | [`test_tool_call_binder.py`](../../agent_pkg/tests/test_tool_call_binder.py), [`test_nodes_tools_picker.py`](../../utils_pkg/tests/test_nodes_tools_picker.py) | [ADR-0020](../adr/0020-unified-tool-caller.md), [ADR-0034](../adr/0034-tool-calling-mechanism.md) |
| `Running tools` | [`tools_caller.py`](../../utils_pkg/klea_utils/nodes/tools_caller.py) | [`test_nodes_tools_caller.py`](../../utils_pkg/tests/test_nodes_tools_caller.py), [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [ADR-0020](../adr/0020-unified-tool-caller.md), [MCP permissions](mcp-permissions.md) |
| `Reasoning` | [`reasoning.py`](../../agent_pkg/klea_agent/nodes/reasoning.py) | [`test_reasoning.py`](../../agent_pkg/tests/test_reasoning.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md) |
| `Evaluating` | [`operational_evaluator.py`](../../agent_pkg/klea_agent/nodes/operational_evaluator.py) | [`test_operational_evaluator.py`](../../agent_pkg/tests/test_operational_evaluator.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md), [ADR-0041](../adr/0041-plan-step-granularity-and-parallelism.md) |
| `Preparing review` | [`review_progress.py`](../../agent_pkg/klea_agent/nodes/review_progress.py) | [`test_review_progress.py`](../../agent_pkg/tests/test_review_progress.py) | [ADR-0051](../adr/0051-review-as-plan-step.md) |
| `Composing answer` | [`answer_from_results.py`](../../agent_pkg/klea_agent/nodes/answer_from_results.py) | [`test_answer_from_results.py`](../../agent_pkg/tests/test_answer_from_results.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md) |
| `Preparing response` | [`answer_user.py`](../../agent_pkg/klea_agent/nodes/answer_user.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [C4 agent component](c4-component-agent.md) |
| `Awaiting review` | [`await_human.py`](../../agent_pkg/klea_agent/nodes/await_human.py) | [`test_await_human.py`](../../agent_pkg/tests/test_await_human.py) | [ADR-0046](../adr/0046-hitl-interrupt-resume.md), [ADR-0051](../adr/0051-review-as-plan-step.md) |
| `Awaiting input` | [`await_human.py`](../../agent_pkg/klea_agent/nodes/await_human.py) | [`test_await_human.py`](../../agent_pkg/tests/test_await_human.py) | [ADR-0046](../adr/0046-hitl-interrupt-resume.md) |
| `Cancelled` | [`fixed_answer.py`](../../utils_pkg/klea_utils/nodes/fixed_answer.py) | [`test_await_human.py`](../../agent_pkg/tests/test_await_human.py) | [ADR-0046](../adr/0046-hitl-interrupt-resume.md) |
| `Summarizing history` | [`summarise_memory.py`](../../utils_pkg/klea_utils/nodes/summarise_memory.py) | [`test_nodes_memory.py`](../../utils_pkg/tests/test_nodes_memory.py) | [ADR-0018](../adr/0018-conversation-memory.md) |
| `Compacting memory` | [`summarise_memory.py`](../../utils_pkg/klea_utils/nodes/summarise_memory.py) | [`test_commands.py`](../../agent_pkg/tests/test_commands.py), [`test_nodes_memory.py`](../../utils_pkg/tests/test_nodes_memory.py) | [ADR-0047](../adr/0047-session-command-framework.md), [Session commands](session-commands.md) |

### 8.2 Supporting contracts and routers

| Subject | Primary implementation | Regression coverage | Architectural or topology reference |
|---|---|---|---|
| State channels | [`schemas.py`](../../agent_pkg/klea_agent/schemas.py), [`state.py`](../../utils_pkg/klea_utils/graph/state.py) | [`test_schemas.py`](../../agent_pkg/tests/test_schemas.py), [`test_graph_state.py`](../../utils_pkg/tests/test_graph_state.py) | [ADR-0032](../adr/0032-graph-context-events-and-state-ownership.md) |
| Conditional-edge mechanism | [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [Graph routing](graph-routing.md), [ADR-0047](../adr/0047-session-command-framework.md) |
| Prompt-variable conventions | LLM node `_get_prompt_variables` methods | Prompt and schema hygiene tests under [`utils_pkg/tests`](../../utils_pkg/tests) | [Prompt conventions](prompt-conventions.md) |
| Generated topology | [`klea-agent-lang-graph.mmd`](../../agent_pkg/example-configs/klea-agent-lang-graph.mmd) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [C4 agent component](c4-component-agent.md), [Graph routing](graph-routing.md) |
| Command routers | [`commands/graph.py`](../../agent_pkg/klea_agent/commands/graph.py), [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py) | [`test_commands.py`](../../agent_pkg/tests/test_commands.py) | [Session commands](session-commands.md), [ADR-0047](../adr/0047-session-command-framework.md) |
| Mode and route routers | [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py) | [`test_mode_graph.py`](../../agent_pkg/tests/test_mode_graph.py), [`test_route_decision.py`](../../agent_pkg/tests/test_route_decision.py) | [ADR-0030](../adr/0030-agent-operating-modes-and-assurance-levels.md), [ADR-0035](../adr/0035-general-operational-agent-path.md) |
| Guard and triage routers | [`guard_router.py`](../../utils_pkg/klea_utils/nodes/guard_router.py), [`triage_router.py`](../../utils_pkg/klea_utils/nodes/triage_router.py) | [`test_general_path_graph.py`](../../agent_pkg/tests/test_general_path_graph.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md) |
| Planner, review, and input routers | [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py), [`await_human.py`](../../agent_pkg/klea_agent/nodes/await_human.py) | [`test_planner.py`](../../agent_pkg/tests/test_planner.py), [`test_await_human.py`](../../agent_pkg/tests/test_await_human.py) | [ADR-0046](../adr/0046-hitl-interrupt-resume.md), [ADR-0051](../adr/0051-review-as-plan-step.md) |
| Picker and evaluation routers | [`klea_agent.py`](../../agent_pkg/klea_agent/klea_agent.py), [`tools_picker.py`](../../utils_pkg/klea_utils/nodes/tools_picker.py) | [`test_nodes_tools_picker.py`](../../utils_pkg/tests/test_nodes_tools_picker.py), [`test_operational_evaluator.py`](../../agent_pkg/tests/test_operational_evaluator.py) | [ADR-0035](../adr/0035-general-operational-agent-path.md), [ADR-0041](../adr/0041-plan-step-granularity-and-parallelism.md) |

## 9. Synchronization rules

### 9.1 Mandatory update sets

| Change | Required synchronized artifacts |
|---|---|
| Add, remove, or rename a registered node label | `klea_agent.py`, this document's inventory and node section, graph tests, generated `klea-agent-lang-graph.mmd`, [C4 agent component](c4-component-agent.md), and [control flow](agent-general-path-control-flow.md). |
| Add, remove, or rename an edge or route label | The relevant path function and `path_map`, router contract tables, graph tests, generated Mermaid artifact, C4 diagram, control-flow note, and [graph-routing.md](graph-routing.md) when the routing mechanism changes. |
| Change a state field type, default, validator, or reducer | The defining schema, state-channel glossary, affected node input/output tables, prompt variables, schema/state tests, and relevant ADR or system note. |
| Change a node's prompt input or requiredness | The node's `_get_prompt_variables`, its system/user prompt files, this contract, prompt rendering tests, and [prompt-conventions.md](prompt-conventions.md) when the convention changes. |
| Change a node's output or fallback | The node implementation, its state update table, focused unit tests, this contract's decision/failure tables, and downstream consumers that rely on the old fallback. |
| Change a route label or path-map target | All direct uses of the label in conditional edges, path-function tests, graph topology tests, generated Mermaid artifact, C4 diagram, and control-flow note. |
| Change a shared node in `klea_utils` | The shared node tests, every consuming app's use of that node, this contract, and the RAG contract if the shared behavior is also used there. |
| Change HITL request or response semantics | Agent and shared AwaitHuman implementation, API/stream contract tests, `ReviewResponse`, this contract, ADR-0046, and ADR-0051 where review semantics changed. |
| Change a tool disclosure, binding, or permission rule | Planner/binder/caller implementations, tool and permission tests, relevant ADRs, and the tool-related portions of this contract. |
| Change memory or artifact lifetime | State schema, initialization/actor reset behavior, summarizer tests, this contract's glossary and node contracts, and the control-flow persistence section. |
| Distinguish or rename the automatic and forced memory instances | Command routing, both graph registrations, memory and command tests, this contract's glossary and node sections, topology artifacts, and ADR-0047 where command behavior changed. |

### 9.2 Diagram and contract rules

1. The generated Mermaid artifact is a topology artifact. Regenerate it from the compiled graph; do not use it as the source for field-level claims.
2. The explicit C4 component diagram may augment topology with supporting components and external interactions, but its node labels and edges must be checked against the compiled graph.
3. A topology edge does not imply that a node writes every field visible to its successor. Field ownership remains defined by the node's update contract.
4. A prompt variable does not imply a state field, and a state field does not imply that it is rendered into a prompt.
5. An inspection event is not a graph-state update unless the node explicitly returns that field in its state update.
6. A conditional-edge router is not a registered node unless `_create_graph` registers it with `add_node`.
7. A route label is a transition contract and must not be changed without updating its exact `path_map` key.
8. The empty command route is represented by the empty-string label and maps to `END`; it is not an application node label.
9. Shared implementations do not erase instance identity. `Awaiting review` and `Awaiting input` are distinct instances, and `Summarizing history` and `Compacting memory` are distinct instances.

### 9.3 Review checklist

Before accepting a graph or node-contract change, verify that:

- every registered label appears exactly once in the inventory and has one detailed node section;
- every conditional-edge label appears in the router contract tables and in the corresponding `path_map`;
- every input and output table names type, origin or kind, requiredness, write timing, effect or downstream purpose, fallback or lifetime and clearing, validation where applicable, and source evidence;
- the state-channel glossary and the defining schema agree on names and types;
- the generated topology, explicit C4 diagram, and control-flow note use the same exact labels;
- focused tests cover changed routing, fallback, and state-update behavior;
- no statement relies only on a historical `.agents` note;
- no unsupported runtime, provider, or UI behavior has been added.

## 10. Claim boundary

This document records contracts visible in the cited source, tests, and architecture documents. It does not claim that an LLM always follows a prompt, that a tool always succeeds, that a graph run always reaches a particular route, or that an interface always renders a stream event. Those are runtime outcomes and must not be encoded as node-contract guarantees.
