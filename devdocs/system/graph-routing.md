# Graph routing: conditional edges vs `Command(goto=...)`

Status: reference note.  Records why Klea routes with conditional-edge path
functions rather than LangGraph `Command(goto=...)`, and when the latter would
be warranted.  Companion to `agent-general-path-control-flow.md` (the agent's
concrete edges) and `graph-resume.md` (confirmed LangGraph semantics).

Last updated: 2026-10-13.

## Scope

How a LangGraph node transition is decided in Klea, and the two mechanisms
available.  Applies to every orchestrator (`klea_agent`, `klea_rag`); the
concrete edge list is app-specific.

## The two mechanisms

- **Conditional edge** -- `add_conditional_edges(source, path_fn, path_map)`.
  `path_fn(state) -> label` is a pure function of state and `path_map` maps
  labels to node names.  It is *not* a graph node: it runs during edge
  evaluation, adds no super-step and writes no state.  Klea's
  `AbstractRouterNode` (`utils_pkg/klea_utils/nodes/abstract.py`) is the shared
  base (`execute(state) -> str`); `GuardRouterNode`, `TriageRouter` and the
  `_xxx_router` methods/functions in `klea_agent.py` are instances.
- **`Command(goto=...)`** -- a *node return* (`from langgraph.types import
  Command`).  The node returns `Command(goto=<node|list|Send>, update=...)`,
  routing and updating state in one object.  The graph builder cannot see the
  targets statically unless they are declared (see below).

## Why Klea uses conditional edges

1. **The LLM nodes cannot return `Command`.**  `AbstractLLMNode.execute` is
   `@final` and returns a state-update dict via `_update_state`
   (`utils_pkg/klea_utils/nodes/abstract.py`).  Every LLM-driven branch
   (`Planner`, `OperationalEvaluator`, `RouteDecision`, `GuardNode`,
   `ReasoningNode`) must therefore route through a separate path function over
   the state the node wrote.  `Command` is not available to them.
2. **Static topology.**  `path_map` declares every target at build time, so
   `get_graph()` and the exported `klea-agent-lang-graph.png` show the edges,
   and targets are validated at compile.  `Command(goto=...)` hides them unless
   declared.
3. **No extra node / super-step / checkpoint write.**  A path function runs as
   part of the edge; a `Command`-returning router would have to be a real graph
   node, adding a super-step (and a checkpoint write) per routing decision --
   costly across the ~10 routers in the agent graph.
4. **Routing stays separately testable.**  A path function is a pure
   `state -> label`, unit-tested directly.

## When `Command(goto=...)` would be warranted

- **Runtime-dynamic targets** -- the destination is chosen from a set not known
  at build time (e.g. a handoff to one of many sub-agents named at runtime).
  The choice is still codified: code logic, or a codified mapping of an LLM
  output *label* to a node name (the LLM never names graph nodes itself).
- **Cross-graph handoffs** -- `graph=Command.PARENT` routes to a parent graph
  with updates, which conditional edges cannot do.
- **`Send` fan-out** alongside the state update.
- A node genuinely owning its transition, where a separate path function adds
  no value.

If `Command` is ever used, declare its targets for rendering with
`add_node(..., destinations=(...))` (or a `dict` for edge labels); the
docstring notes this is render-only with no runtime effect
(`langgraph/graph/state.py`).  A node's `Command[Literal[...]]` return
annotation is the other render-only declaration, but a shared node cannot know
an app's node labels, so `destinations` is the app-side choice.

## Decision: the session-command handoff

The session-command node (`utils_pkg/klea_utils/nodes/command.py`) can hand off
to another node (e.g. `/compact` -> the summarise node).  We route that with a
**transient state signal + conditional edge**, not `Command(goto=...)`:

- `GraphCommandResult.route` carries a logical route name; the command node
  writes it to a transient `command_route` state field; a shared
  `command_route_router` maps it (`""` -> `END`).
- The target set is fixed at build time (a command's routes are known), so
  `Command`'s dynamic-target benefit does not apply; the router keeps the
  topology static and consistent with the rest of the graph.

See `session-commands.md` and ADR-0047.

## Revisit

Re-open if any "warranted" case above appears -- most likely a command or node
that needs a runtime-chosen target or a cross-graph handoff.  If the decision
hardens into a framework-wide rule, graduate it to an ADR.

## Pointers

- `utils_pkg/klea_utils/nodes/abstract.py` -- `AbstractRouterNode`,
  `AbstractLLMNode.execute` (`@final`).
- `agent_pkg/klea_agent/klea_agent.py` -- the agent's conditional edges.
- `devdocs/system/agent-general-path-control-flow.md`,
  `devdocs/system/graph-resume.md`, `devdocs/system/session-commands.md`.
- LangGraph: `add_conditional_edges`, `add_node(destinations=...)`,
  `langgraph.types.Command`.
