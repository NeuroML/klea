# C4 model: Level 3 -- Component diagram (Agent)

Status: architecture documentation. Reflects the agent container at the
time of writing. This is the Level 3 view for the ``klea_agent`` container
(``agent_pkg``); the Level 1 system context is in `c4-system-context.md`
and the Level 2 container diagram is in `c4-container.md`.  The RAG
container's sibling view is `c4-component-rag.md`.

## Scope and intent

A component diagram zooms into one container and shows its *components*
-- the major code units that make up the container -- plus how they
interact with each other and with the containers / external systems from
Level 2.  Per the C4 standard (https://c4model.com/diagrams/component)
components are deployable as part of their container.

**Container in scope:** ``klea_agent`` (the general-purpose research
agent: ``agent_pkg/klea_agent``).  The diagram shows the agent's LangGraph
nodes (the general operational path, ADR-0035) plus the supporting
components that own the graph (the ``KleaAgent`` orchestrator and its
routers/callbacks), load configuration, define the state, and expose the
API and UI.  It also shows the MCP, LLM, session/checkpoint and inspection
interactions that the graph calls.  The graph is implemented over
``klea_utils.graph.base.BaseLangGraph`` (ADR-0016 Template Method) and the
nodes share ``klea_utils/nodes/abstract.py`` (ADR-0019) and the ADR-0013 /
ADR-0040 inspection stream.

Level 3 for the agent is rendered as a Mermaid ``flowchart`` with
``layout: elk`` (orthogonal routing) for the same reason as Levels 2 and
3-RAG.  The node/edge topology is sourced from the code (``klea_agent.py:
422`` ``_create_graph``; ``klea_agent.py:699`` ``_export_graph_png`` also
writes the ``.mmd`` via ``graph.get_graph().draw_mermaid()``) and then
augmented with the component-to-container/external edges that
``draw_mermaid`` does not see.

## Component diagram -- Agent container (flowchart, elk + auto-generated core)

The auto-generated LangGraph topology (faithful to code) is embedded as
the core.  The explicit diagram adds the supporting components, the
deterministic router components (which have no generated box because they
are attached via ``add_conditional_edges`` rather than ``add_node``), and
the LLM, MCP, session and inspection edges that ``draw_mermaid`` does not
see.  Keep the node names in sync with ``klea-agent-lang-graph.mmd``
(``agent_pkg/example-configs/klea-agent-lang-graph.mmd``) -- that file is
the ``klea_agent.py:699`` artefact generated alongside
``klea-agent-lang-graph.png``.

```mermaid
---
config:
  layout: elk
  elk:
    mergeEdges: false
    nodeSpacing: 35
    rankSpacing: 45
---
flowchart TD

    %% External / container-level
    llm["LLM Providers<br/>Ollama, OpenAI, HuggingFace,<br/>custom OpenAI-compatible<br/>roles: chat, plan, guard"]
    mcpExt["MCP Servers<br/>nml-mcp + configured servers<br/>streamable-http / stdio<br/>via MCPConfig + tag filtering"]
    bundled["bundled klea-mcp<br/>stdio subprocess per app<br/>via BaseLangGraph._bundled_server_config()<br/>klea_utils.mcp.server.bundled"]
    sqlite["Session / Checkpoint Stores<br/>SQLite: checkpoints.db, sessions.db<br/>via BaseLangGraph._setup_checkpointer / sessions_db"]
    inspection["Inspection Stream<br/>NodeStreamData (inspect/state/usage)<br/>via _CustomChannelEnabler + SSE"]

    subgraph AGENT ["klea_agent container (agent_pkg/klea_agent/klea_agent.py:422, WIP)"]
        direction TB

        init["Initializing<br/>InitGraphState (non-LLM)<br/>per-turn fields; task state retained; ADR-0054"]
        modeDec["Determining mode<br/>ModeDecision (non-LLM)<br/>requested to resolved mode + note; ADR-0030"]
        modeInf["Informing about mode<br/>ModeInformer<br/>terminal: requested mode cannot run"]
        guard["Checking safety<br/>GuardNode (guard, shared)<br/>guard_decision safe/unsafe; ADR-0010"]
        guardR["Routing safety<br/>GuardRouterNode (shared router)<br/>safe vs unsafe"]
        decline["Declining query<br/>FixedAnswer (shared)<br/>I cannot respond..."]
        route["Deciding route<br/>RouteDecision (chat)<br/>RouteSchema: chat/continue/task (fail-closed; continue default)"]
        reset["Resetting state<br/>ResetState (non-LLM)<br/>task reset or continuation evidence; ADR-0054"]
        planner["Planning<br/>Planner (plan)<br/>sole author of goal + plan; ADR-0035"]
        reviewProgress["Preparing review<br/>ReviewProgress (chat)<br/>synthesizes the since-last-review digest; ADR-0051"]
        review["Awaiting review<br/>AwaitHuman (non-LLM)<br/>a review step: approve/revise; ADR-0046/0051"]
        awaitInput["Awaiting input<br/>AwaitHuman (non-LLM)<br/>answers a blocked step's questions; ADR-0046"]
        cancelled["Cancelled<br/>FixedAnswer (shared)<br/>terminal: run cancelled"]
        picker["Binding tool calls<br/>ToolCallBinder (tool_picker)<br/>binds args for one suggested-tool group; ADR-0020/0034/0035"]
        caller["Running tools<br/>ToolsCallerNode (shared)<br/>dispatch + access gate; ADR-0007/0037"]
        triage["Triaging<br/>TriageRouter (deterministic router)<br/>retry vs evaluate vs replan"]
        reasoning["Reasoning<br/>ReasoningNode (chat)<br/>conclusion as str StepOutput"]
        evaluator["Evaluating<br/>OperationalEvaluator (chat)<br/>EvaluationSchema verdict"]
        compose["Composing answer<br/>AnswerFromResults (chat)<br/>message_for_user + deliverable to artefacts"]
        prepare["Preparing response<br/>AnswerUser (non-LLM)<br/>final user-facing message"]
        summarise["Summarizing history<br/>SummariseMemoryNode (chat, shared)<br/>context_summary + recent window; ADR-0018"]
        command["Running command<br/>CommandNode (non-LLM)<br/>dispatches a /command, may hand off; ADR-0047"]
        compact["Compacting memory<br/>SummariseMemoryNode (chat, shared)<br/>forced compaction for /compact"]
    end

    subgraph SUPPORT ["Supporting components (not graph nodes)"]
        direction TB
        orchestrator["KleaAgent orchestrator<br/>klea_agent.py:_create_graph, _*_router,<br/>_record_tool_round / _record_picker_failure,<br/>context_snapshot; ADR-0016/0032/0035"]
        config["AppConfig + config loader<br/>config.py: bundled_tools, access_level,<br/>tool_access; env file + profile JSON; ADR-0015"]
        state["KleaAgentState + schemas<br/>schemas.py: goal, plan, plan_history,<br/>continuation_evidence, step_outputs, artefacts,<br/>internal history, retry budgets; ADR-0032/0054"]
        api["Agent API<br/>api/main.py make_app + api/chat.py ChatPayload<br/>POST /query, /query/stream; ADR-0031"]
        ui["Client UI / CLI<br/>ui/cli.py (klea, klea-serve) + ui/web/*<br/>mode + access selectors; ADR-0030/0037"]
    end

    %% Graph edges (from auto-generated .mmd, same labels as code)
    init -. continue .-> modeDec
    init -. command .-> command
    modeDec -. proceed .-> guard
    modeDec -. inform .-> modeInf
    guard --> guardR
    guardR -. safe .-> route
    guardR -. unsafe .-> decline
    route -. task/continue .-> reset
    route -. chat .-> prepare
    reset --> planner
    planner -. chat .-> route
    planner -. review .-> reviewProgress
    planner -. failure .-> compose
    planner -. needs_input .-> awaitInput
    planner -. tool .-> picker
    planner -. reasoning .-> reasoning
    reviewProgress --> review
    review -. planner .-> planner
    review -. tool .-> picker
    review -. reasoning .-> reasoning
    review -. review .-> reviewProgress
    review -. plan_done .-> compose
    review -. cancelled .-> cancelled
    awaitInput -. planner .-> planner
    awaitInput -. cancelled .-> cancelled
    cancelled --> ENDC
    picker -. bind_next .-> picker
    picker -. dispatch .-> caller
    picker -. retry_picker .-> picker
    picker -. replan .-> planner
    caller --> triage
    triage -. retry .-> picker
    triage -. evaluate .-> evaluator
    triage -. replan .-> planner
    reasoning --> evaluator
    evaluator -. tool .-> picker
    evaluator -. reasoning .-> reasoning
    evaluator -. review .-> reviewProgress
    evaluator -. need_replan .-> planner
    evaluator -. plan_done .-> compose
    evaluator -. abort .-> compose
    compose --> prepare
    prepare --> summarise
    summarise --> ENDC
    decline --> ENDC
    modeInf --> ENDC
    command -. compact .-> compact
    command -. init .-> modeDec
    command -. terminal .-> ENDC
    compact --> ENDC

    STARTN([__start__]) --> init
    ENDC([__end__])

    %% Supporting-component wiring
    ui --> api
    api --> orchestrator
    orchestrator -. "compiles / runs" .-> init
    orchestrator --> config
    orchestrator --> state

    %% Component -> container/external edges (what draw_mermaid does not see)
    guard -. "LLM" .-> llm
    route -. "LLM" .-> llm
    planner -. "LLM" .-> llm
    picker -. "LLM" .-> llm
    reasoning -. "LLM" .-> llm
    evaluator -. "LLM" .-> llm
    compose -. "LLM" .-> llm
    summarise -. "LLM" .-> llm
    compact -. "LLM" .-> llm

    caller -. "MCP" .-> mcpExt
    caller -. "MCP" .-> bundled

    guard -- "inspection" --> inspection
    route -- "inspection" --> inspection
    planner -- "inspection" --> inspection
    picker -- "inspection" --> inspection
    caller -- "inspection" --> inspection
    triage -- "inspection" --> inspection
    reasoning -- "inspection" --> inspection
    evaluator -- "inspection" --> inspection
    compose -- "inspection" --> inspection
    review -- "inspection" --> inspection
    awaitInput -- "inspection" --> inspection
    summarise -- "inspection" --> inspection
    command -- "inspection" --> inspection
    compact -- "inspection" --> inspection

    init -- "session" --> sqlite
    review -- "session" --> sqlite
    awaitInput -- "session" --> sqlite
    prepare -- "session" --> sqlite
    summarise -- "session" --> sqlite
    command -- "session" --> sqlite
    compact -- "session" --> sqlite
    decline -- "session" --> sqlite
    modeInf -- "session" --> sqlite

    orchestrator -- "shared lib" --> utils["klea_utils<br/>BaseLangGraph (template), nodes (template),<br/>mcp, api, llm, plogging<br/>ADR-0016/0019/0020"]
    sqlite -- "shared lib" --> utils
```

*Notes:* Solid `-->` = normal graph edge (from ``klea_agent.py:_create_graph``).
Dotted `-. label .->` = conditional ``add_conditional_edges``
(``GuardRouter``, ``RouteDecision``, ``Planner``, the review/input await
routers, the picker router, the ``TriageRouter``, the evaluator router, and
the command router/handoff: ``Initializing`` -> ``Running command`` ->
``Compacting memory`` (``/compact``), ``Determining mode`` (``/init``), or
``__end__``).  Double-dash `inspection` / `session` / `MCP` / `LLM` edges are
component-to-container/external interactions that ``draw_mermaid`` omits;
they are what make this a C4 Level 3 rather than a bare node graph.  The
deterministic routers (``Routing safety``, ``Triaging``) are augmentations:
in the generated topology the routers are conditional-edge functions attached
to ``Checking safety`` and ``Running tools``, so they have no box.  The HITL
nodes (``Awaiting review``, ``Awaiting input``, ``Cancelled``) are real graph
nodes (ADR-0046): review approval dispatches into the batch entry, a revision
returns to the Planner, and a cancel is terminal.  The supporting components
are shown as a separate block because C4 components are inside the container,
but they are not graph nodes.  The auto-generated
``agent_pkg/example-configs/klea-agent-lang-graph.mmd`` (``config:
flowchart: curve: linear`` plus ``classDef first/last``) is the faithful
``draw_mermaid()`` artefact kept alongside the PNG; the node labels above
are normalised to it (``Summarizing history`` not ``Summarizing_history``).

## Auto-generated LangGraph topology (faithful to code, for drift check)

This is the verbatim ``draw_mermaid()`` output from ``klea_agent.py``
(``agent_pkg/example-configs/klea-agent-lang-graph.mmd``, also writes
``.mmd`` alongside ``.png`` via ``BaseLangGraph._export_graph_png``). Keep
the explicit diagram above in sync with it; do not edit this block by
hand.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	Initializing(Initializing)
	Determining\20mode(Determining mode)
	Informing\20about\20mode(Informing about mode)
	Running\20command(Running command)
	Checking\20safety(Checking safety)
	Declining\20query(Declining query)
	Deciding\20route(Deciding route)
	Resetting\20state(Resetting state)
	Planning(Planning)
	Binding\20tool\20calls(Binding tool calls)
	Running\20tools(Running tools)
	Reasoning(Reasoning)
	Evaluating(Evaluating)
	Preparing\20review(Preparing review)
	Composing\20answer(Composing answer)
	Preparing\20response(Preparing response)
	Awaiting\20review(Awaiting review)
	Awaiting\20input(Awaiting input)
	Cancelled(Cancelled)
	Summarizing\20history(Summarizing history)
	Compacting\20memory(Compacting memory)
	__end__([<p>__end__</p>]):::last
	Awaiting\20input -. &nbsp;cancelled&nbsp; .-> Cancelled;
	Awaiting\20input -. &nbsp;planner&nbsp; .-> Planning;
	Awaiting\20review -. &nbsp;tool&nbsp; .-> Binding\20tool\20calls;
	Awaiting\20review -. &nbsp;cancelled&nbsp; .-> Cancelled;
	Awaiting\20review -. &nbsp;plan_done&nbsp; .-> Composing\20answer;
	Awaiting\20review -. &nbsp;planner&nbsp; .-> Planning;
	Awaiting\20review -. &nbsp;review&nbsp; .-> Preparing\20review;
	Awaiting\20review -. &nbsp;reasoning&nbsp; .-> Reasoning;
	Binding\20tool\20calls -. &nbsp;replan&nbsp; .-> Planning;
	Binding\20tool\20calls -. &nbsp;dispatch&nbsp; .-> Running\20tools;
	Checking\20safety -. &nbsp;safe&nbsp; .-> Deciding\20route;
	Checking\20safety -. &nbsp;unsafe&nbsp; .-> Declining\20query;
	Composing\20answer --> Preparing\20response;
	Deciding\20route -. &nbsp;chat&nbsp; .-> Preparing\20response;
	Deciding\20route -. &nbsp;continue&nbsp; .-> Resetting\20state;
	Determining\20mode -. &nbsp;proceed&nbsp; .-> Checking\20safety;
	Determining\20mode -. &nbsp;inform&nbsp; .-> Informing\20about\20mode;
	Evaluating -. &nbsp;tool&nbsp; .-> Binding\20tool\20calls;
	Evaluating -. &nbsp;abort&nbsp; .-> Composing\20answer;
	Evaluating -. &nbsp;need_replan&nbsp; .-> Planning;
	Evaluating -. &nbsp;review&nbsp; .-> Preparing\20review;
	Evaluating -. &nbsp;reasoning&nbsp; .-> Reasoning;
	Initializing -. &nbsp;continue&nbsp; .-> Determining\20mode;
	Initializing -. &nbsp;command&nbsp; .-> Running\20command;
	Planning -. &nbsp;needs_input&nbsp; .-> Awaiting\20input;
	Planning -. &nbsp;tool&nbsp; .-> Binding\20tool\20calls;
	Planning -. &nbsp;failure&nbsp; .-> Composing\20answer;
	Planning -. &nbsp;chat&nbsp; .-> Deciding\20route;
	Planning -. &nbsp;review&nbsp; .-> Preparing\20review;
	Planning -. &nbsp;reasoning&nbsp; .-> Reasoning;
	Preparing\20response --> Summarizing\20history;
	Preparing\20review --> Awaiting\20review;
	Reasoning --> Evaluating;
	Resetting\20state --> Planning;
	Running\20command -. &nbsp;compact&nbsp; .-> Compacting\20memory;
	Running\20command -. &nbsp;init&nbsp; .-> Determining\20mode;
	Running\20command -. &nbsp;&nbsp; .-> __end__;
	Running\20tools -. &nbsp;retry&nbsp; .-> Binding\20tool\20calls;
	Running\20tools -. &nbsp;evaluate&nbsp; .-> Evaluating;
	Running\20tools -. &nbsp;replan&nbsp; .-> Planning;
	__start__ --> Initializing;
	Cancelled --> __end__;
	Compacting\20memory --> __end__;
	Declining\20query --> __end__;
	Informing\20about\20mode --> __end__;
	Summarizing\20history --> __end__;
	Binding\20tool\20calls -. &nbsp;bind_next&nbsp; .-> Binding\20tool\20calls;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

## Components

Field-level node contracts are maintained in [Agent node contracts](agent-node-contracts.md).
This component diagram intentionally shows component ownership and flow rather than
field-level details.

| Component | File | Role | Key contracts |
|-----------|------|------|---------------|
| KleaAgent orchestrator | ``klea_agent/klea_agent.py`` | ``BaseLangGraph`` subclass: ``_create_graph``, the conditional-edge routers, the tool-round/picker callbacks, ``context_snapshot`` | ``env_prefix``/config discovery (ADR-0015); graph exported as ``klea-agent-lang-graph.{png,mmd}`` (ADR-0016/0032/0035) |
| AppConfig / config loader | ``klea_agent/config.py`` | JSON app config + env file/profile resolution | ``general.bundled_tools``, ``general.access_level``, ``general.tool_access``, ``general.commands``, ``general.memory``, ``mcp_servers``, ``providers`` (ADR-0015/0037/0047) |
| KleaAgentState + schemas | ``klea_agent/schemas.py`` | Graph state and structured outputs | ``goal``, ``plan``, ``plan_history``, ``continuation_evidence``, ``step_outputs``, ``artefacts``, ``messages``/``messages_internal``, retry budgets, ``mode``, ``route``, ``evaluation`` (ADR-0032/0035/0054) |
| Agent API | ``klea_agent/api/{main,chat,server}.py`` | FastAPI app via ``make_app`` + the agent ``ChatPayload`` chat router | ``POST /query``, ``/query/stream``; ``mode``/``access_level`` in the payload; generic routers from ``klea_utils.api`` (ADR-0031) |
| Client UI / CLI | ``klea_agent/ui/cli.py`` + ``ui/web/*`` | Typer CLI (``klea``, ``klea-serve``) and NiceGUI pages over shared components | mode selector, tool-access selector, status pane (ADR-0030/0031/0037) |
| Initializing | ``klea_agent/nodes/init_graph.py`` | ``InitGraphState``: per-turn fields and query history; task state retained | Non-LLM; no plan-scoped reset; task boundary moves to ``ResetState`` (ADR-0054) |
| Resetting state | ``klea_agent/nodes/reset_state.py`` | ``ResetState``: applies the ``task``/``continue`` boundary before planning | Non-LLM; ``task`` full reset; ``continue`` clears goal/active plan/transients but keeps ``continuation_evidence`` (ADR-0054) |
| Determining mode / Informing | ``klea_agent/nodes/mode_router.py`` | ``ModeDecision`` resolves requested -> resolved; ``ModeInformer`` is terminal | Non-LLM; ``mode.note`` routes to inform instead of downgrading (ADR-0030) |
| Checking safety | ``klea_utils/nodes/guard.py`` | ``GuardNode`` (``guard`` role, shared) | ``guard_decision`` ``safe``/``unsafe`` (ADR-0010) |
| Routing safety | ``klea_utils/nodes/guard_router.py`` | ``GuardRouterNode`` (shared router) | reads ``guard_decision`` -> ``safe``/``unsafe`` edge |
| Declining query | ``klea_utils/nodes/fixed_answer.py`` | ``FixedAnswer``: canned refusal | terminal; writes ``message_for_user`` |
| Deciding route | ``klea_agent/nodes/route_decision.py`` | ``RouteDecision`` (``chat``): narrow intent router, fail-closed | ``RouteSchema`` ``chat`` (inline answer) / ``continue`` (ongoing work; re-plan from prior evidence, default) / ``task`` (unrelated new goal); no tool list (ADR-0035/0054) |
| Planning | ``klea_agent/nodes/planner.py`` | ``Planner`` (``plan``): sole author of the goal + plan | ``PlannerOutput``/``PlannerPlanSchema``; ``previous_work`` evidence on continuations; structural validation + validation-retry; ``unplannable`` fail-closed (ADR-0035/0054) |
| Preparing review | ``klea_agent/nodes/review_progress.py`` | ``ReviewProgress`` (``chat``): synthesizes the human-facing digest of work since the last review, immediately before each review gate | ``ReviewProgressSchema``; no tools; reads ``step_outputs`` since the last approved gate; skips with no new work (ADR-0051) |
| Awaiting review / input | ``klea_agent/nodes/await_human.py`` | ``AwaitHuman`` (ADR-0046/ADR-0051): reusable HITL node, two instances -- review pauses at a ``review`` step (plan approval or checkpoint; presents the digest + all pending steps; writes ``human_feedback``); input answers a blocked step's questions (writes ``human_input``) | Non-LLM; ``interrupt``/``Command(resume=...)``; approve resolves the review step -> execution, revise -> Planner, cancel -> terminal |
| Binding tool calls | ``klea_agent/nodes/tool_call_binder.py`` | ``ToolCallBinder`` (``tool_picker``): a ``ToolsPicker`` subclass that binds arguments for one contiguous group of steps sharing ``suggested_tools`` | ``ToolCallBinder_*`` prompts; only the group's tools are disclosed; accumulates ``binding_calls`` until the batch is bound; ``on_unusable`` callback; never substitutes a tool (ADR-0020/0034/0035) |
| Running tools | ``klea_utils/nodes/tools_caller.py`` | ``ToolsCallerNode`` (shared): dispatches the batch | ``dispatch_tool_calls`` (parallel), ``checkpaths``/access gate, ``isError`` synthesis; ``post_dispatch`` callback (ADR-0003/0007/0037) |
| Triaging | ``klea_agent/nodes/triage_router.py`` | ``TriageRouter`` (deterministic router): mechanical tool-error triage | ``retry`` (re-pick same tool) / ``evaluate`` / ``replan``; ADaPT counter via ``update_tool_retry_counts`` (ADR-0035) |
| Reasoning | ``klea_agent/nodes/reasoning.py`` | ``ReasoningNode`` (``chat``): produces a step conclusion | ``ReasoningSchema``; records a ``str`` ``StepOutput`` + ``rationale``; no picker/caller (ADR-0035) |
| Evaluating | ``klea_agent/nodes/operational_evaluator.py`` | ``OperationalEvaluator`` (``chat``): judges the current step | ``EvaluationSchema`` verdict ``step_incomplete``/``step_done``/``plan_done``/``need_replan``/``abort`` (ADR-0035) |
| Composing answer | ``klea_agent/nodes/answer_from_results.py`` | ``AnswerFromResults`` (``chat``): synthesises the reply | ``message_for_user``; success persists the deliverable to ``artefacts``; never judges (ADR-0035) |
| Preparing response | ``klea_agent/nodes/answer_user.py`` | ``AnswerUser``: delivers the final user-facing message | Non-LLM |
| Summarizing history | ``klea_utils/nodes/summarise_memory.py`` | ``SummariseMemoryNode`` (``chat``, shared) | ``context_summary`` + recent window; preserves state on a blank summary; reports the outcome via ``inspect`` (ADR-0018) |
| Running command | ``klea_utils/nodes/command.py`` | ``CommandNode`` (shared): dispatches a server-side ``/command`` | parses/resolves/dispatches; emits ``inspect``; may hand off via ``GraphCommandResult.route`` (ADR-0047) |
| Compacting memory | ``klea_utils/nodes/summarise_memory.py`` | ``SummariseMemoryNode`` instance (``chat``, shared): forced compaction for ``/compact`` | threshold ``0``; optional chat confirmation; reports via ``inspect`` (ADR-0018/0047) |
| LLM Providers | external | chat, plan and guard inference | provider/model resolution via ``klea_utils.llm`` including catalog ``provider:<model>``; per-request model switching (ADR-0014) |
| MCP servers (``nml-mcp`` + configured) | ``mcp_pkg`` + operator config | domain tools over streamable-http/stdio | ``MCPConfig`` + tag filtering; reached only by ``Running tools`` |
| bundled ``klea-mcp`` | ``klea_utils.mcp.server.bundled`` | shared file/web tools, stdio subprocess per app | ``BaseLangGraph._bundled_server_config()`` (ADR-0004) |
| Session / Checkpoint | ``klea_utils/api/sessions_db.py`` + ``graph/base.py`` | ``AsyncSqliteSaver`` | ``checkpoints.db``, ``sessions.db`` (ADR-0023) |
| Inspection stream | ``klea_utils`` node stream + SSE | per-node ``inspect``/``state``/``usage`` events | ``NodeStreamData`` via ``_CustomChannelEnabler`` (ADR-0013/0040) |

## How the components interact (mirrors Level 2)

* The agent is the WIP path (``c4-system-context.md:36``, ``klea_agent
  WIP``); its control flow is documented in
  `agent-general-path-control-flow.md` and its topology decision in
  ADR-0035 (which supersedes the ADR-0025 proposal).
* Every agent component that needs an LLM (guard, route, plan, binder,
  reasoning, evaluate, review progress, compose, summarise, compact) is a
  ``BaseLLMNode`` (``klea_utils/nodes/base.py``, ADR-0019) and goes through the
  shared retryer + token-window ladder (ADR-0017), the structured-output
  fallback (``devdocs/system/structured-output-fallback.md``), and the
  ``BaseLangGraph`` per-request model switching (ADR-0014).
* ``ToolsPicker``/``ToolsCallerNode`` are shared with RAG (ADR-0020); the
  agent uses ``ToolCallBinder``, a ``ToolsPicker`` subclass that binds one
  suggested-tool group at a time and accumulates calls until the batch is
  bound (``on_unusable`` -> synthetic error + ``replan_reason``), and passes
  evaluator ``step_incomplete`` reasons back as ``step_feedback``, plus the
  ADaPT retry counter.
* ``KleaAgent`` is the composition point for the whole container: it
  creates the nodes, wires the conditional edges (the routers are methods
  or ``AbstractRouterNode`` classes), keeps the per-round/picker callbacks
  (``_record_tool_round``, ``_record_picker_failure``), and projects the
  session context onto the stream (ADR-0032).
* Diagrams live in ``devdocs/system/`` as the single source of truth
  (``devdocs/README.md:21``); ``docs/developer-info.rst`` links to them on
  GitHub.

## Open items (Level 3+)

* The scientific-mode correctness layer (ADR-0029/0036) is not yet
  implemented: it inserts grounding, evidence, provenance and an
  independent verifier additively on this general path, and adds the
  scientific answer variant.  This diagram shows the general path only.
* Parallel step execution (ADR-0041, accepted 2026-09-21) and
  dependency-frontier batching will change the step-entry structure.
* The retrieval tool (``search_stores``) that wires RAG stores into the
  agent is deferred per the control-flow note; when added it is a new
  ``Running tools`` target (an MCP/tool component, not a graph node).
* The ``nml-mcp`` tool/sandbox layout and the ``klea_utils`` API/stores
  internals are future code-level views.
