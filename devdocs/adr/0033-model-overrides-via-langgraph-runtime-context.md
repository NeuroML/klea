---
status: "accepted"
date: 2026-09-08
decision-makers: Ankur Sinha
consulted: "opencode agent"
informed: ""
---

# Per-request model overrides via LangGraph Runtime context

## Context and Problem Statement

ADR-0014 chose a hand-rolled ``contextvar`` (``model_overrides_ctx``,
``graph/base.py``) as the per-request seam that carries per-chat model /
API-key overrides from the API layer (``klea_utils.api.chat_core``) into
the shared node code (``_build_invoke_config`` / ``_invoke_llm``).

LangGraph now ships a first-class mechanism for exactly this use case:
per-run *Runtime context* -- ``StateGraph(context_schema=...)``, a
``context=`` argument on ``ainvoke`` / ``astream`` /
``astream_events``, and ambient ``get_runtime()`` access inside nodes.
The LangGraph documentation uses configuring the LLM at runtime as its
canonical example for this feature.

LangGraph distinguishes two run channels: ``config["configurable"]``
(read via ``get_config``) is for *static, read-only* configuration --
identifiers (``user_id``, ``thread_id``), feature flags, or
runtime-configurable model choices -- flowing downward with the
invocation config; the ``Runtime`` (read via ``get_runtime``) is the
framework's dependency-injection and *application payload* container
for the run, wrapping the schema-typed ``context`` together with the
long-term store (``BaseStore``), stream writers, and server/retry
metadata.  Per-request model overrides are a per-run application
payload, so they belong in the typed ``Runtime`` context, not in the
generic ``configurable`` bag.

Why replace the contextvar?  It duplicates framework machinery, is
invisible to the framework (no typing, no validation at the boundary, no
discovery in docs/tooling), requires a manual set/reset lifecycle per
request, and does not cross non-asyncio execution boundaries (a sync
node runs through ``run_in_executor``, where its ContextVar copy from
the event loop is not visible).  All Klea nodes are ``async``, so the
last point does not bite today, but the mechanism is nonetheless
framework-unaware.

How should per-request model overrides (and any future per-run
parameter an app may want) reach the nodes without an ad-hoc global?

## Decision Drivers

* Framework-native: use LangGraph's own per-run context channel rather
  than a hand-rolled global.
* Typed and validated at the boundary: the framework coerces a dict
  ``context`` into the declared schema (``context_schema(**context)``);
  a ``TypedDict`` schema would pass through unvalidated.
* Race-free for a shared, long-lived graph: node instances are
  singletons shared across concurrent requests, so the transport must
  be per-execution, never mutable state stored on shared objects.
* Generic (ADR-0031): ``klea_utils`` supplies infrastructure; apps
  define semantics.  The shared schema carries one conventional key
  consumed by the shared nodes (``model_overrides``); apps may extend
  or add their own keys.
* Remove the manual set/reset lifecycle.

## Considered Options

* **A. Keep the ad-hoc contextvar (status quo)** -- ``model_overrides_ctx``.
* **B. Raw ``config["configurable"]`` at graph invoke** -- put the
  override slice into the run config.  *Rejected:* ``configurable`` is
  the channel for static read-only parameters (identifiers, feature
  flags, runtime-choosable model names) and is untyped/unvalidated at
  the boundary; its public keys mix with checkpointer/thread plumbing
  (``thread_id``, ...).  Application payloads are delivered via the
  typed ``Runtime`` context, which is what this decision uses.
* **C. LangGraph Runtime context with a pydantic ``context_schema``**
  (chosen).

## Decision Outcome

Chosen option: "C. LangGraph Runtime context with a pydantic
``context_schema``".

* ``klea_utils.graph.context`` gains ``KleaRunContext(BaseModel)`` with
  ``model_config = ConfigDict(extra="allow")`` and
  ``model_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)``.
  ``extra="allow"`` keeps the schema generic: an app that wants its own
  keys validated at the boundary subclasses it and registers the
  subclass; an app that prefers to validate custom keys itself reads
  them from ``model_extra``.
* ``BaseLangGraph`` run methods (``run_graph_invoke``,
  ``run_graph_astream_events``, ``run_graph_stream``, ``graph_stream``)
  gain ``context: KleaRunContext | None = None``, forwarded verbatim to
  ``graph.ainvoke(...)`` / ``astream(...)`` / ``astream_events(context=...)``.
  The runner stays app-agnostic: no "model" vocabulary in its contract.
* The graph is built with ``StateGraph(..., context_schema=KleaRunContext)``
  in both apps (either the base schema or an app subclass).
* Nodes read the override slice via
  ``model_overrides_from_context(get_runtime().context)`` in
  ``_build_invoke_config``; ``get_runtime()`` is ambient, so no node
  signature changes are needed.  With no ``context`` passed,
  ``Runtime.context`` is ``None`` and the helper returns ``{}``.
* ``chat_core.run_query`` / ``stream_response`` build
  ``KleaRunContext(model_overrides=store.get_overrides(...))`` and pass
  it to the run methods; the request-scoped set/reset disappears.
* ``model_overrides_ctx`` is deleted.  The LLM layer is unchanged: per
  invocation it still receives a fully-merged ``RunnableConfig`` with
  ``configurable`` populated (ADR-0014's ``configurable_fields="any"``
  decision remains in force -- only the *transport* changes).

### Consequences

* Good, because the transport is framework-native and documented: the
  LangGraph Runtime context is the sanctioned channel for per-run LLM
  configuration.
* Good, because the override slice is validated at the run boundary:
  LangGraph coerces the ``context`` dict into ``KleaRunContext``
  (``context_schema(**context)``), so mistakes fail on the way in, not
  inside a node.
* Good, because it stays race-free: LangGraph injects a per-execution
  ``Runtime``; nothing is stored on the shared graph/node instances.
* Good, because it is generic: apps can carry arbitrary per-run
  parameters in the same context without touching the runner or the
  shared nodes, and shared nodes only depend on the conventional
  ``model_overrides`` key.
* Good, because the manual set/reset lifecycle and its
  "must clear per request" foot-gun are gone.
* Bad, because ``get_runtime()`` is only valid inside a runnable
  context: unit tests that call ``_build_invoke_config`` directly
  outside a graph run need a runtime harness (or a mini compiled
  graph).
* Bad, because per-run context does not cross non-asyncio boundaries: a
  *sync* node executed via ``run_in_executor`` would not inherit it.
  All current Klea nodes are ``async``; this is documented so that a
  future sync node does not silently lose overrides.

## Confirmation

* Probes (2026-09-08, throwaway scripts): ambient ``get_runtime()``
  returns the per-run context inside an async node without a
  ``runtime`` parameter on both ``ainvoke(context=...)`` and
  ``astream_events(version="v3", context=...)``; a dict ``context`` is
  coerced to the pydantic schema; ``extra="allow"`` keys ride through
  as ``model_extra``; no-context yields ``Runtime.context is None``.
* To land with implementation: an ambient-runtime integration test
  (mini ``StateGraph`` with pydantic ``context_schema`` driven via
  ``ainvoke`` and ``astream_events`` v3), run-method ``context=``
  forwarding tests, ``_build_invoke_config`` override-source test via a
  runtime harness, and ``chat_core`` override-flow tests in both apps.

## More Information

* Supersedes: the ``model_overrides_ctx`` transport seam chosen in
  ADR-0014 (Runtime per-request model switching with user-supplied API
  keys).  ADR-0014's remaining decisions -- the
  ``configurable_fields="any"`` model, the sessions-db per-chat
  override sourcing, the three-layer merge, and key masking -- remain
  governing and are incorporated by reference.
* References: LangGraph docs "Use the graph API -> Add runtime
  configuration" (``context_schema``, ``Runtime``, ambient
  ``get_runtime``); the docs' configuration-vs-runtime distinction
  (``config["configurable"]`` = static read-only parameters;
  ``Runtime`` = dependency-injection/application-payload container);
  installed langgraph ``_coerce_context``.
