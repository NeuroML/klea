# Agent topology for cost and correctness: a literature review

Status: literature review, not an ADR.  Input to the general-path topology
decision (a future ADR) and to the agent evaluation harness design (see
`agent-evaluation-harness.md`).  Written 2026-09-10 by opencode (model:
deepseek-flash); revised same day to add the operational/epistemic boundary,
mode tags, and the single-model constraint on escalation.

## Purpose

Klea's general (non-scientific) agent path must be decided.  The project
constraint is explicit: Klea targets academics and non-corporate users who do
not have frontier-model token budgets, so the general path must optimise for
correctness and cost efficiency at the same time, and must work with
non-frontier (including small, local) models.  "Does not require a frontier
model" is the intended selling point.

This note reviews the primary literature and major engineering write-ups on
agent loop topology, cost-aware inference, planning and verification, and
small-model agency, and extracts the implications for Klea.  It deliberately
does **not** decide the topology; that is the ADR's job.

## The question

Should the general path replicate the industry-standard flat ReAct loop, or
use a different topology?  Is flat ReAct the most efficient design, or merely
the easiest to implement?

## Scope and method

Sources collected 2026-09-09/10 from arXiv abstracts and vendor engineering
blogs.  Evidence is largely software-engineering, QA and web-agent benchmarks
using frontier or fine-tuned mid-size models.  Transfer to a local small model
doing scientific assistance is plausible but not proven (see Caveats).

Findings are tagged by where they apply:

- **[G]** general path (operational agency).
- **[S]** scientific path (epistemic agency; ADR-0029).
- **[I]** infrastructure, applies to both.

## Design frame: operational vs epistemic agency

The general and scientific paths are not distinguished by *capability* but by
*enforcement*.  The general path **may** retrieve from curated stores, search
the web, and use any tool; the scientific path **must** ground claims in
approved curated evidence with inspection and provenance.  Retrieval is an
optional tool for general mode and a mandatory stage for scientific mode.
The strictness of grounding is the differentiator, not its presence.

| Aspect | General mode [G] | Scientific mode [S] |
|---|---|---|
| Goal | task success / reliability | epistemic correctness, auditability |
| Curated source and retrieval | optional tool (RAG, web search, files) | mandatory stage |
| Plan | mutable working plan, no approval | versioned, evidence-linked, approval-gated |
| Evaluator | operational (did the step work / done / escalate / ask) | epistemic (groundedness, coverage, confidence; ADR-0009) |
| Replanning | fix a failed approach (as needed) | evidence-driven invalidation (inv. 9-10) |
| Verification | optional (task tests/tools) | mandatory independent verification (inv. 7) |
| Provenance | not required | closed provenance chain (inv. 8) |
| Human approval | safety gate on consequential tool actions | plan review gate before execution (inv. 5) |
| Output label | unverified | verified |

Two consequences frame everything below:

1. General mode's planner/evaluator are **operational, never epistemic**.  The
   node names may be shared; the contracts and the enforcement differ.
2. Scientific mode is the general base plus an additive correctness layer
   (retrieval, evidence inspection, gating, verification, provenance).  The
   general path must not contain scientific-only stages, so drift is
   structurally prevented.

## Findings

### F1. Cost-aware inference is proven, but learned routing does not fit Klea [I]

Learned routing:

- **RouteLLM**: trains a small classifier to predict whether a strong model
  would be preferred over a weak one, then routes per query against a
  threshold that sets the cost/quality operating point.  Training uses **human
  preference data** (Chatbot-Arena-style) plus data augmentation.  The
  headline generalisation is across **model pairs** (train on one pair, reuse
  on another); it is not claimed to transfer across **domains**.
- **FrugalGPT**: an LLM **cascade** (try cheap, escalate on failure) matches
  GPT-4 with up to 98% cost reduction, or beats it by 4% at equal cost.

Why learned routing does not fit: it requires a preference corpus, a training
pipeline, and threshold calibration.  Klea's users are not expected to
provide any of that (building the stores is already enough), and a
chat-preference router would not know that a NeuroML modelling task is hard.

What we can do instead, with no training and no extra user setup:

1. **Static role assignment** (current): `llm_models[role]` selects the model
   per role.  No adaptation.
2. **Zero-shot routing**: a cheap LLM classifies difficulty and picks a tier.
   No training; one cheap extra call.
3. **Failure-triggered escalation** (cascade/ADaPT): run cheap first, escalate
   when the evaluator reports failure.  No training, no classifier.

#### Two axes of escalation (important)

The cascade idea hides two different things, and only one of them needs
multiple models:

- **Structural escalation [I]**: escalate the *process* on the same model --
  more reasoning, planning/decomposition, retrieval, retries.  Always
  available; requires no extra configuration.  This is the default mechanism.
- **Model escalation [I]**: escalate the *model* (cheap -> strong).  Requires
  more than one configured model.  **Optional enhancement**, never a
  requirement.

Implication for Klea [G]: the general path must be correct and useful with
**exactly one configured model**.  The role split (guard/chat/plan) stays, but
every role may point at that one model; a user with only a Claude/Codex key
gets the full topology.  Model escalation is unlocked only when additional
models are configured.  Simplicity is preserved by making the single-model
case the default and the multi-model case an opt-in.

### F2. Interleaved flat loops are the least token-efficient loop shape [G][I]

- **ReWOO**: decoupling reasoning from observation (plan, then execute) gives
  5x token efficiency and +4% accuracy on HotpotQA vs interleaved ReAct.  The
  saving comes precisely from removing observations from the reasoning step,
  which also removes mid-plan adaptivity; ReWOO trades adaptivity for cost.
- **LLM Compiler**: planner + parallel executor gives up to 3.7x latency
  speedup, 6.7x cost savings and ~9% accuracy gain vs ReAct.
- **Plan-and-Solve**: plan-then-solve beats zero-shot CoT on all ten evaluated
  datasets; matches 8-shot CoT on math.  This is a prompting method, not a
  topology.
- **Agentless**: a fixed three-phase pipeline (localize, repair, validate)
  achieved the best SWE-bench Lite score (32%) and the lowest cost ($0.70) of
  all open-source agents at the time.  Scope caveat: the three phases assume a
  code repository and a test oracle.  Academic workflows (research, hypothesis
  generation) have no automated oracle, so the result does **not** generalise
  to them.  The transferable lesson is narrow: when an objectively verifiable
  oracle exists for a subtask, a fixed interpretable pipeline can beat
  agentic improvisation.  That "oracle" is exactly what scientific mode
  supplies, so this belongs to [S], not [G].

Implication [G]: plan-first (or at least not re-planning every step) is
measurably cheaper, but the plan must stay **evolvable** from step feedback.
Pure ReWOO is too rigid for our needs; ADaPT (F5) is the better target.
Prompt caching (ADR-0028) mitigates but does not remove the cost of a growing
interleaved trace.

### F3. LLMs do not reliably plan or self-verify; verification should be external [S][G]

- **PlanBench**: plan generation falls short even for state-of-the-art models.
- **LLM-Modulo**: autoregressive LLMs cannot, by themselves, plan or
  self-verify; they belong in a loop with external model-based verifiers.
- **Self-critique of plans**: with GPT-4, self-critiquing worsened plan
  generation relative to external sound verifiers, and the LLM verifiers
  produced many false positives; feedback granularity mattered little.
- **Let's Verify Step by Step**: process (per-step) supervision beats outcome
  supervision; 78% on a MATH subset.
- **Chain-of-Verification (CoVe)**: draft, generate independent verification
  questions, answer them separately, then revise, reducing hallucination.
- **Reflexion**: act, evaluate, reflect verbally, retry; 91% HumanEval pass@1
  vs 80% for GPT-4.
- **Self-Refine**: same-model iterative feedback improves outputs by about 20%
  absolute across seven tasks, but it is same-model feedback and does not
  overturn the planning critique above.

Implication for scientific mode [S]: independent verification is mandatory and
must not be same-model self-critique (ADR-0029 inv. 7).  Reflexion/CoVe show
the *mechanism* (grade, feed language feedback, retry) works, but the grader
must be grounded independently.

Implication for general mode [G]: the operational evaluator ("did the step
work / is the task done / should I escalate or ask?") is justified for
reliability, but it is not an epistemic verifier and must not be presented as
one.  Klea already has this pattern in RAG: `RouteEvaluator` grades the
answer, `GenerateRetrievalQuery` consumes the evaluator's summary as feedback,
under bounded `max_retrieval_attempts`/`max_rewrite_attempts`
(`rag_pkg/klea_rag/nodes/generate_retrieval_query.py:114-141`,
`rag_pkg/klea_rag/rag.py:434-446`).  That is a Reflexion-like loop, but
intra-query and budgeted rather than a persistent episodic memory.

### F4. Non-frontier models are weakest at exactly what a flat loop demands [G]

- **Small LLMs Are Weak Tool Learners**: small models fail at tool use;
  decomposing the capability into planner, caller and summarizer (with
  continuous fine-tuning) beats a single LLM on tool-use benchmarks.
- **Small Language Models are the Future of Agentic AI**: position paper
  arguing SLMs are sufficient, more suitable and more economical for the
  repetitive, specialised invocations that dominate agentic systems.
  Caveat for us: the "heterogeneous multi-model" framing assumes several
  models are available; we cannot require that (see F1).
- **AgentBench**: a significant gap between top commercial models and open
  models up to 70B; the main obstacles are long-term reasoning,
  decision-making and instruction following.
- **Self-RAG**: 7B/13B models with explicit reflection and retrieval tokens
  outperform ChatGPT and retrieval-augmented Llama2-chat on open-domain QA,
  reasoning and fact verification.
- **tau-bench**: even gpt-4o succeeds on less than 50% of tool-agent-user
  tasks and is inconsistent (`pass^8 < 25%` in retail); reliability, not peak
  success, is the bottleneck.
- **GAIA**: humans 92% vs GPT-4 with plugins 15% on apparently simple,
  tool-heavy real tasks.

Implication [G]: capability decomposition across **stages** is what makes weak
models viable; a single monolithic ReAct node asks one weak model to do the
very things it is worst at (plan, pick, call, summarize, self-check).  The
stages do not require different models -- with one configured model they all
use it.  This favours Klea's existing stage split and the shared picker/caller.

### F5. The efficient frontier is as-needed decomposition, not always-plan or never-plan [G]

- **ADaPT**: plan and decompose a subtask only when the executor cannot do it,
  recursively, adapting to both task complexity and model capability;
  +28.3% ALFWorld, +27% WebShop and +33% TextCraft over iterative-executor and
  plan-and-execute baselines.

Implication [G]: the escalation trigger should be executor failure/capability,
not an upfront task classification.  This is the structural-escalation axis of
F1 applied to planning: same model, more process when needed.  It is the
published analogue of the "escalation ladder" and is aimed precisely at weak
executors.

### F6. Multi-agent only pays for breadth-first parallelism; sequential work prefers a single thread [G]

- **Anthropic multi-agent Research (2025)**: an orchestrator-worker system beat
  a single agent by 90.2% on an internal breadth-first research evaluation, but
  uses roughly 15x chat tokens and is described as a poor fit for coding (few
  truly parallelizable tasks).
- **Magentic-One**: orchestrator plus specialised agents, competitive on
  GAIA/AssistantBench/WebArena; modular, no per-task prompt tuning.
- **Cognition, "Don't Build Multi-Agents" (2025)**: single-threaded linear
  agents with fully shared context are the reliable default; split contexts
  produce conflicting implicit decisions.
- **SWE-agent**: its state-of-the-art result came from the Agent-Computer
  Interface (tool/interface design), not from the loop shape.
- **Anthropic, "Building Effective Agents" (2024)**: start simple; add
  complexity only when it demonstrably improves outcomes; agents for
  open-ended tasks, workflows for well-defined ones.

Decision for Klea [G]: **drop multi-agent**.  Sequential general tasks are
better served by a single thread with **parallel tool calls** (already
available via `dispatch_tool_calls`/`asyncio.gather`, ADR-0020) and an
**evolvable plan** (plan as state, replan edges driven by step feedback).
Multi-agent is reconsidered only for breadth-first research, and then likely
under scientific mode.  Tool/interface design (ADR-0034, edit-format strategy)
has as much leverage as topology.

### F7. The benchmark landscape shows the headroom [I]

GAIA, AgentBench, tau-bench and SWE-bench all show that tool reliability and
long-horizon robustness remain unsolved, and that the gap to human or frontier
performance is largest for tool use and consistency.  This argues for a
task-level evaluation harness for Klea; see the companion planning note
`agent-evaluation-harness.md`.

## Synthesis for Klea

The evidence supports a specific shape for the general path, and it is not
naive flat ReAct:

1. **Cheap by default**: trivial input must not pay planning.
2. **Escalate structurally, as needed**, triggered by executor
   failure/capability rather than an upfront classifier; model escalation is
   an optional enhancement, never a requirement.
3. **One configured model must suffice**: every role defaults to it; the
   topology is fully functional single-model.
4. **Keep specialised stages** (plan, pick/call, summarize, operational
   evaluate), because that is what makes weak models work; stages are not
   distinct models.
5. **Plan is evolvable state**: plan-first for cost, with replan edges driven
   by step feedback (ADaPT), not a frozen ReWOO-style plan.
6. **Single-threaded**: no multi-agent; parallel tool calls plus an evolvable
   plan.
7. **Retrieval is an optional general tool** (RAG, web search, files) and a
   mandatory scientific stage.  The general path must not enforce epistemic
   stages.

This corresponds to a minimal general base (guard -> act loop with inline
answer or tool calls -> operational evaluator -> as-needed decompose/replan ->
answer) that shares its substrate with the scientific path.  Scientific mode
is the base plus the ADR-0029 correctness layer (retrieval, evidence
inspection, gating, independent verification, provenance), inserted
additively.  Because the scientific-only stages are absent from the general
path, general mode cannot silently acquire scientific complexity.

## Caveats

- Most results use frontier or fine-tuned mid-size models on SWE/QA/web
  benchmarks, not a local small model doing scientific assistance.  Transfer is
  plausible, not proven.
- Several sources (Anthropic multi-agent, the SLM position paper, Cognition)
  are industry positions rather than peer-reviewed head-to-head comparisons.
- ADaPT's gains involve fine-tuned executors in some settings; prompting plus
  structured emission may yield a different magnitude.
- The single-model constraint means the "small model" result from Shen et al.
  (planner/caller/summarizer split) is the most directly relevant, but it used
  fine-tuning; our setting uses prompting only.
- There is no head-to-head of these topologies on Klea's target models and
  tasks.  The direction is well supported; the magnitude for Klea is unknown
  and must be measured.

## References

Mode column: G = general, S = scientific, I = infrastructure.

| Short name | Source | Mode | Key claim used |
|------|--------|------|----------------|
| ReAct | arXiv:2210.03629 | G | interleaved reason+act baseline |
| Reflexion | arXiv:2303.11366 | S | evaluate + verbal reflect loop; 91% HumanEval |
| Self-Refine | arXiv:2303.17651 | S | same-model iterative refinement; ~20% absolute gain |
| CoVe | arXiv:2309.11495 | S | independent verification questions reduce hallucination |
| Self-RAG | arXiv:2310.11511 | S | 7B/13B reflection tokens beat ChatGPT on QA |
| Let's Verify Step by Step | arXiv:2305.20050 | S | process supervision beats outcome; 78% MATH |
| PlanBench | arXiv:2206.10498 | S | LLM planning falls short even for SOTA |
| LLM-Modulo | arXiv:2402.01817 | S | LLMs cannot plan or self-verify; external verifiers |
| Self-critique of plans | arXiv:2310.08118 | S | self-critique worsens plans vs external verifiers |
| Plan-and-Solve | arXiv:2305.04091 | G | plan-then-solve beats zero-shot CoT |
| ReWOO | arXiv:2305.18323 | G | 5x token efficiency, +4% accuracy vs ReAct; loses adaptivity |
| LLM Compiler | arXiv:2312.04511 | G | 3.7x latency, 6.7x cost, ~9% accuracy vs ReAct |
| ADaPT | arXiv:2311.05772 | G | as-needed decomposition; +27% to +33% success |
| Agentless | arXiv:2407.01489 | S | fixed pipeline best SWE-bench Lite at lowest cost (then); needs oracle |
| SWE-agent | arXiv:2405.15793 | G | ACI/tool design drives results |
| FrugalGPT | arXiv:2305.05176 | I | LLM cascades; up to 98% cost reduction |
| RouteLLM | arXiv:2406.18665 | I | learned routing needs preference data; not usable without setup |
| SLMs for agentic AI | arXiv:2506.02153 | G | SLMs sufficient/suitable/economical for specialised invocations |
| Small LLMs weak tool learners | arXiv:2401.07324 | G | planner/caller/summarizer split beats single LLM |
| AgentBench | arXiv:2308.03688 | G | wide gap commercial vs open <=70B |
| tau-bench | arXiv:2406.12045 | G | gpt-4o <50%; `pass^8 <25%`; reliability gap |
| GAIA | arXiv:2311.12983 | G | humans 92% vs GPT-4+plugins 15% |
| Magentic-One | arXiv:2411.04468 | G | orchestrator + specialised agents; modular |
| Anthropic, Building Effective Agents | anthropic.com/engineering/building-effective-agents (2024-12) | G | start simple; workflows vs agents |
| Anthropic multi-agent Research | anthropic.com/engineering/built-multi-agent-research-system (2025-06) | G | 90.2% breadth-first gain; ~15x tokens; weak for coding |
| Cognition, Don't Build Multi-Agents | cognition.ai/blog/dont-build-multi-agents (2025-06) | G | single-threaded linear default; multi-agent fragile |
