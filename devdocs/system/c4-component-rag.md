# C4 model: Level 3 -- Component diagram (RAG)

Status: architecture documentation. Reflects the RAG container at the
time of writing. This is the Level 3 view for the ``klea_rag`` container
(``rag_pkg``); the Level 1 system context is in `c4-system-context.md`
and the Level 2 container diagram is in `c4-container.md`.  The agent's
components are the sibling view in `c4-component-agent.md`.

## Scope and intent

A component diagram zooms into one container and shows its *components*
-- the major code units that make up the container -- plus how they
interact with each other and with the containers / external systems from
Level 2.  Per the C4 standard (https://c4model.com/diagrams/component)
components are deployable as part of their container.

**Container in scope:** ``klea_rag`` (the RAG pipeline: ``rag_pkg/klea_rag``).
The diagram shows the RAG graph nodes plus the retrieval, MCP, and
session/checkpoint components that the graph calls.  The graph itself
is implemented over ``klea_utils.graph.base.BaseLangGraph`` (ADR-0016
Template Method) and the nodes share ``klea_utils/nodes/abstract.py``
(ADR-0019) and ``ADR-0013`` inspection stream.

Level 3 for RAG is rendered as a Mermaid ``flowchart`` with
``layout: elk`` (orthogonal routing) for the same reason as Level 2.
The node/edge topology is sourced from the code (``rag_pkg/klea_rag/rag.py:
191`` ``BaseLangGraph._export_graph_png`` also writes the ``.mmd`` via
``graph.get_graph().draw_mermaid()``) and then augmented with the
component-to-container/external edges.

## Component diagram -- RAG container (flowchart, elk + auto-generated core)

The auto-generated LangGraph topology (faithful to code) is embedded as
the core.  The explicit diagram adds the retrieval, MCP, LLM, and store
edges that ``draw_mermaid`` does not see (``VSRetriever``,
``BM25RetrieverManager``, ``MCP Clients``, ``LLM Providers``,
``SQLite``).  Keep the node names in sync with ``rag-lang-graph.mmd``
(``rag_pkg/example-configs/rag-lang-graph.mmd``) -- that file is the
``rag.py:191`` artefact generated alongside ``rag-lang-graph.png``.

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
    llm["LLM Providers<br/>Ollama, OpenAI, HuggingFace,<br/>custom OpenAI-compatible"]
    vstores["Vector / BM25 Stores<br/>Chroma, Qdrant, pgvector + BM25<br/>URI: chroma:/, qdrant:http://, pgvector:postgresql://, .pkl"]
    vbackends["Vector Store Backends<br/>Chroma, Qdrant, pgvector engines"]
    mcpExt["nml-mcp + per-domain MCP Servers<br/>streamable-http / stdio<br/>via MCPConfig + tag filtering"]
    bundled["bundled klea-mcp<br/>stdio subprocess per app<br/>via BaseLangGraph._bundled_server_config()<br/>klea_utils.mcp.server.bundled"]
    sqlite["Session / Checkpoint Stores<br/>SQLite: checkpoints.db, sessions.db<br/>via BaseLangGraph._setup_checkpointer / sessions_db"]
    inspection["Inspection Stream<br/>NodeStreamData (inspect/state/usage)<br/>via _CustomChannelEnabler + SSE"]

    subgraph RAG ["klea_rag container (rag_pkg/klea_rag/rag.py:191, WIP)"]
        direction TB

        init["Initializing<br/>InitRAGState (non-LLM)<br/>seeds query_domains, context_summary"]
        guard["Checking safety<br/>GuardNode (guard, skip via _pre_exec when no model)"]
        guardR["Routing safety<br/>GuardRouterNode<br/>safe vs unsafe"]
        decline["Declining query<br/>FixedAnswer<br/>I cannot respond..."]
        classify["Classifying question<br/>ClassifyQuestion (chat)<br/>QueryDomainSchema: list[Literal[domains]]<br/>strip undefined when real domain present"]
        routeQ["Routing question<br/>RouteQuery<br/>domain_query vs non_domain_query vs non_domain_refuse"]
        refuse["Refusing query<br/>FixedAnswer<br/>not in permitted domains"]
        splitter["Splitting<br/>_splitter_node (fan-out)<br/>always retrieve (ADR-0008)"]
        genSearch["Generating search<br/>GenerateRetrievalQuery (chat)<br/>per-domain filter_fields → q + config_filters"]
        picker["Selecting tools<br/>ToolsPicker (chat, shared)<br/>per-app prompt registry, tools_info"]
        caller["Running tools<br/>ToolsCallerNode (shared)<br/>dispatch_tool_calls + checkpaths gate + isError"]
        retrieve["Retrieving information<br/>RetrieveInfoNode<br/>VSRetriever + BM25RetrieverManager<br/>restrict_metadata_filter per domain<br/>RRF + max_refs_size (ADR-0012, ADR-0022)"]
        answerCtx["Generating answer<br/>AnswerFromContext (chat)<br/>serialize_reference_material + citations"]
        evaluator["Evaluating answer<br/>Evaluator (chat)<br/>confidence/coverage/groundedness… → next_step"]
        routeEval["Routing evaluation<br/>RouteEvaluator<br/>continue / retrieve_more_info / rewrite_answer / modify_query / fallback / best_effort / undefined<br/>(fallback_to_training_data, max attempts)"]
        answerGen["Answering generally<br/>AnswerGeneral (chat, FallbackConfig)<br/>training-data with fallback_warning when domain-routed"]
        prep["Preparing response<br/>AnswerUser<br/>final message_for_user"]
        clarify["Requesting clarification<br/>FixedAnswer<br/>Apologies. I could not answer..."]
        summarise["Summarizing history<br/>SummariseMemoryNode<br/>structured BaseMessage memory (ADR-0018)<br/>context_summary + recent window"]
    end

    %% Graph edges (from auto-generated .mmd, same labels as code)
    init --> guard
    guard -. safe .-> classify
    guard -. unsafe .-> decline
    classify -. non_domain_query .-> answerGen
    classify -. non_domain_refuse .-> refuse
    classify -. domain_query .-> splitter
    splitter --> genSearch
    splitter --> picker
    picker --> caller
    genSearch --> retrieve
    caller --> answerCtx
    retrieve --> answerCtx
    answerCtx --> evaluator
    evaluator -. fallback .-> answerGen
    evaluator -. rewrite_answer .-> answerCtx
    evaluator -. modify_query .-> genSearch
    evaluator -. best_effort .-> prep
    evaluator -. undefined .-> clarify
    evaluator -. retrieve_more_info .-> retrieve
    answerGen --> summarise
    prep --> summarise
    clarify --> summarise
    decline --> ENDC
    refuse --> ENDC

    ENDC([__end__])

    summarise --> ENDC

    %% Component -> container/external edges (what draw_mermaid does not see)
    guard -. "LLM" .-> llm
    classify -. "LLM" .-> llm
    genSearch -. "LLM" .-> llm
    picker -. "LLM" .-> llm
    answerCtx -. "LLM" .-> llm
    evaluator -. "LLM" .-> llm
    answerGen -. "LLM" .-> llm

    caller -. "MCP" .-> mcpExt
    caller -. "MCP" .-> bundled

    retrieve -- "stores" --> vstores
    vstores --> vbackends

    guard -- "inspection" --> inspection
    classify -- "inspection" --> inspection
    genSearch -- "inspection" --> inspection
    picker -- "inspection" --> inspection
    answerCtx -- "inspection" --> inspection
    evaluator -- "inspection" --> inspection
    answerGen -- "inspection" --> inspection
    retrieve -- "inspection" --> inspection

    init -- "session" --> sqlite
    prep -- "session" --> sqlite
    clarify -- "session" --> sqlite
    summarise -- "session" --> sqlite
    decline -- "session" --> sqlite
    refuse -- "session" --> sqlite
    vstores -- "shared lib" --> utils["klea_utils<br/>BaseLangGraph (template), nodes (template),<br/>stores, biblio, API, plogging<br/>ADR-0016/0019"]
    sqlite -- "shared lib" --> utils
```

*Notes:* Solid `--> ` = normal graph edge (from ``rag.py:191``). Dotted `-. label .->` = conditional ``add_conditional_edges`` (``GuardRouter``, ``RouteQuery``, ``RouteEvaluator``). Double-dash `inspection` / `session` / `MCP` / `LLM` edges are component→container/external interactions that ``draw_mermaid`` omits; they are what make this a C4 Level 3 rather than a bare node graph. Dashed external `bundled` is auto-launched stdio per-app (ADR-0004). The auto-generated ``rag_pkg/example-configs/rag-lang-graph.mmd`` (``config: flowchart: curve: linear`` plus ``classDef first/last``) is the faithful ``draw_mermaid()`` artefact kept alongside the PNG; the node labels above are normalised to it (``Checking safety`` not ``Checking_safety``).

## Auto-generated LangGraph topology (faithful to code, for drift check)

This is the verbatim ``draw_mermaid()`` output from ``rag_pkg/klea_rag/rag.py:191`` (``rag_pkg/example-configs/rag-lang-graph.mmd``, also writes ``.mmd`` alongside ``.png`` via ``BaseLangGraph._export_graph_png``). Keep the explicit diagram above in sync with it; do not edit this block by hand.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	Checking\20safety(Checking safety)
	Declining\20query(Declining query)
	Initializing(Initializing)
	Classifying\20question(Classifying question)
	Generating\20search(Generating search)
	Selecting\20tools(Selecting tools)
	Running\20tools(Running tools)
	Answering\20generally(Answering generally)
	Refusing\20query(Refusing query)
	Retrieving\20information(Retrieving information)
	Generating\20answer(Generating answer)
	Evaluating\20answer(Evaluating answer)
	Preparing\20response(Preparing response)
	Requesting\20clarification(Requesting clarification)
	Summarizing\20history(Summarizing history)
	Splitting(Splitting)
	__end__([<p>__end__</p>]):::last
	Answering\20generally --> Summarizing\20history;
	Checking\20safety -. &nbsp;safe&nbsp; .-> Classifying\20question;
	Checking\20safety -. &nbsp;unsafe&nbsp; .-> Declining\20query;
	Classifying\20question -. &nbsp;non_domain_query&nbsp; .-> Answering\20generally;
	Classifying\20question -. &nbsp;non_domain_refuse&nbsp; .-> Refusing\20query;
	Classifying\20question -. &nbsp;domain_query&nbsp; .-> Splitting;
	Evaluating\20answer -. &nbsp;fallback&nbsp; .-> Answering\20generally;
	Evaluating\20answer -. &nbsp;rewrite_answer&nbsp; .-> Generating\20answer;
	Evaluating\20answer -. &nbsp;modify_query&nbsp; .-> Generating\20search;
	Evaluating\20answer -. &nbsp;best_effort&nbsp; .-> Preparing\20response;
	Evaluating\20answer -. &nbsp;undefined&nbsp; .-> Requesting\20clarification;
	Evaluating\20answer -. &nbsp;retrieve_more_info&nbsp; .-> Retrieving\20information;
	Generating\20answer --> Evaluating\20answer;
	Generating\20search --> Retrieving\20information;
	Initializing --> Checking\20safety;
	Preparing\20response --> Summarizing\20history;
	Requesting\20clarification --> Summarizing\20history;
	Retrieving\20information --> Generating\20answer;
	Running\20tools --> Generating\20answer;
	Selecting\20tools --> Running\20tools;
	Splitting --> Generating\20search;
	Splitting --> Selecting\20tools;
	__start__ --> Initializing;
	Declining\20query --> __end__;
	Refusing\20query --> __end__;
	Summarizing\20history --> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

## Components

| Component | File | Role | Key contracts |
|-----------|------|------|---------------|
| Initializing | ``klea_rag/nodes/init_rag.py`` | Seeds ``query_domains``, ``context_summary``, ``messages`` | Non-LLM, always runs; no ``_get_inspect`` |
| Checking safety | ``klea_utils/nodes/guard.py`` | ``GuardNode`` (``guard``, skip via ``_pre_exec`` when no model) | ``guard_decision`` ``safe/unsafe``; fail-open ``_get_default_error_result→safe`` |
| Routing safety | ``klea_utils/nodes/guard_router.py`` | ``GuardRouterNode`` | reads ``guard_decision`` → ``safe/unsafe`` edge |
| Declining / Refusing / Clarification | ``klea_utils/nodes/fixed_answer.py`` ``FixedAnswer`` | Canned refusals | ``_ask_user_for_clarification`` vs ``_refuse_answer`` vs ``Declining`` |
| Classifying question | ``klea_rag/nodes/classify_question.py`` | ``ClassifyQuestion`` (``chat``) | ``QueryDomainSchema: list[Literal[domains]]``; strips ``undefined`` when real domain present (ADR-0011) |
| Routing question | ``klea_rag/nodes/route_query.py`` | ``RouteQuery`` (non-LLM router) | ``domain_query`` / ``non_domain_query`` / ``non_domain_refuse`` |
| Splitting | ``rag.py:127`` ``_splitter_node`` | Fan-out (always retrieve) | ``always retrieve`` (ADR-0008): ``GenerateRetrievalQuery`` + ``ToolsPicker`` in parallel |
| Generating search | ``klea_rag/nodes/generate_retrieval_query.py`` | ``GenerateRetrievalQuery`` (``chat``) | per-domain ``filter_fields`` → ``q`` + ``config_filters`` (ADR-0022) |
| Selecting tools | ``klea_utils/nodes/tools_picker.py`` | ``ToolsPicker`` (shared, ``chat``) | per-app prompt registry, ``tools_info`` from ``BaseLangGraph`` (ADR-0020) |
| Running tools | ``klea_utils/nodes/tools_caller.py`` | ``ToolsCallerNode`` (shared) | ``dispatch_tool_calls`` + ``checkpaths`` gate (ADR-0007) + ``isError`` synthesis (ADR-0003) |
| Retrieving information | ``klea_rag/nodes/retrieve_info.py`` | ``RetrieveInfoNode`` | ``VSRetriever`` + ``BM25RetrieverManager`` per ``BaseKleaRetriever``; ``restrict_metadata_filter`` per domain; ``RRF`` + ``max_refs_size`` (ADR-0012) |
| Generating answer | ``klea_rag/nodes/answer_from_context.py`` | ``AnswerFromContext`` (``chat``) | ``serialize_reference_material`` + citations |
| Evaluating answer | ``klea_rag/nodes/evaluator.py`` | ``Evaluator`` (``chat``) | ``confidence/coverage/groundedness… → next_step`` |
| Routing evaluation | ``klea_rag/nodes/route_evaluator.py`` | ``RouteEvaluator`` | ``fallback_to_training_data`` / ``max attempts`` → ``fallback / best_effort / undefined`` (ADR-0009) |
| Answering generally | ``klea_utils/nodes/answer_general.py`` | ``AnswerGeneral`` (``chat``, ``FallbackConfig``) | ``format_alert(fallback_warning)`` only when domain-routed (ADR-0009) |
| Preparing response | ``klea_rag/nodes/answer_user.py`` | ``AnswerUser`` | ``message_for_user`` |
| Summarizing history | ``klea_utils/nodes/summarise_memory.py`` | ``SummariseMemoryNode`` | ``BaseMessage``-object memory (ADR-0018) + ``context_summary`` |
| Vector / BM25 Stores | ``klea_utils/stores/retrieval/*`` | Stores + ingestion | ``store-create.md`` cache layout; ``.klea-cache/*.pkl`` |
| Bundled / nml-mcp | ``klea_utils/mcp/server/bundled*`` + ``mcp_pkg`` | MCP servers | ``bundled`` stdio per app (ADR-0004), tag-filterable |
| Session / Checkpoint | ``klea_utils/api/sessions_db.py`` + ``graph/base.py`` | ``AsyncSqliteSaver`` | ``checkpoints.db``, ``sessions.db`` (ADR-0023) |

## How the components interact (mirrors Level 2)

* The RAG pipeline is the mature path (``c4-system-context.md:54`` ``Domain-configurable … RAG mature``); the agent's components are the sibling view in `c4-component-agent.md`.
* Every RAG component that needs an LLM (guard, classify, generate-search, picker, answer, evaluate, answer-general) is a ``BaseLLMNode`` (``ADR-0019``) and goes through the shared ``_make_retryer_httpx`` + token-window ladder (``ADR-0017``) and the ``BaseLangGraph`` per-request model switching (``ADR-0014``).
* ``RetrieveInfoNode`` is the composition point for ADR-0012 hybrid (vector+BM25, RRF, ``_source_scores`` debug, ``max_refs_size``) and ADR-0022 filter system (per-domain scoping); ``ToolsCallerNode`` is the composition point for ADR-0007 permissions and ADR-0003 ``isError``.
* Diagrams live in ``devdocs/system/`` as the single source of truth (``devdocs/README.md:27``); ``docs/developer-info.rst`` links to them on GitHub.

## Open items (Level 3+)

* The agent's component diagram is the sibling view in `c4-component-agent.md` (ADR-0035, superseding the ``ADR-0025`` topology proposal); the scientific-mode correctness layer (ADR-0029) it shows is not yet implemented.
* The deployment view (local ``uv``/Ollama ``:8005/:8006/:8542`` vs HuggingFace Space ``deployments/huggingface/Dockerfile`` three-service container) is a separate diagram.
