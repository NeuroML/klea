# Multi-Agent vs Single-Agent Systems: Evidence & Findings

## Overview

This document compiles findings from academic papers, industry benchmarks, and practitioner guides comparing multi-agent systems (MAS) and single-agent systems (SAS). The evidence is nuanced: neither architecture is universally superior. The choice depends on task structure, model capability, compute budget, and deployment context.

---

## Academic Papers

### 1. Gao et al. (2025) — "Single-agent or Multi-agent Systems? Why Not Both?"
- **Source:** arXiv:2505.18286 (UIUC, May 2025)
- **Scope:** 15 agentic tasks across 7 domains (code gen, math, travel planning, financial analysis, RAG QA, scientific discovery), 9 frameworks, proprietary + open-source models.
- **Key findings:**
  - MAS historically outperforms SAS, but the gap narrows as LLM capabilities improve.
  - On simple tasks, MAS can underperform SAS due to overthinking.
  - MAS continues to face significant efficiency challenges (higher latency, token cost).
  - Proposes a hybrid paradigm (agent routing + agent cascade) that improves accuracy by 1.1–12% while reducing costs by up to 20% (later revised to 88.1% in the full paper).
  - Identifies three defect types in MAS: node-level (critical agent bottleneck), edge-level (overthinking from upstream agents), and path-level (indecisive error propagation).

### 2. Tran & Kiela (2026) — "Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning Under Equal Thinking Token Budgets"
- **Source:** arXiv:2604.02460 (Stanford, Apr 2026)
- **Scope:** FRAMES and MuSiQue (4-hop) benchmarks; Qwen3, DeepSeek-R1, Gemini 2.5; 5 MAS architectures (Sequential, Subtask-parallel, Parallel-roles, Debate, Ensemble).
- **Key findings:**
  - Under matched thinking-token budgets, SAS consistently matches or outperforms MAS on multi-hop reasoning.
  - Provides an information-theoretic argument (Data Processing Inequality): MAS introduces communication bottlenecks that cause information loss.
  - SAS is the strongest default architecture except at very small budgets (100 tokens, where reasoning is useless anyway).
  - MAS becomes competitive only when single-agent context utilization is degraded (masking, substitution, distractors).
  - Identifies API budget-control artifacts (especially Gemini 2.5) that inflate apparent MAS gains.
  - Debate is the most consistently strong MAS variant; Ensemble is weakest at low budgets.

### 3. Xu et al. (2026) — "Rethinking the Value of Multi-Agent Workflow: A Strong Single Agent Baseline"
- **Source:** arXiv:2601.12307 (Amazon / UT Austin, Jan 2026)
- **Scope:** 7 benchmarks (coding, math, QA, domain reasoning, planning, tool use); homogeneous vs. heterogeneous workflows.
- **Key findings:**
  - A single agent can simulate homogeneous multi-agent workflows with comparable performance, leveraging KV cache reuse for efficiency.
  - Proposes OneFlow: automatically designs streamlined workflows for single-agent execution, reducing inference cost without sacrificing accuracy.
  - Single-LLM methods cannot capture heterogeneous workflows (different models can't share KV caches), highlighting a genuine open direction for MAS.

### 4. Żywot et al. (2026) — "Can Small Agents Collaborate to Beat a Single Large Language Model?"
- **Source:** arXiv:2601.11327 (U. Amsterdam / ETH Zürich / U. Copenhagen, Apr 2026)
- **Scope:** GAIA, GPQA, AIME, MuSiQue, HLE; Qwen3 1.7B–32B; orchestrator + specialized sub-agents design.
- **Key findings:**
  - Small multi-agent systems can outperform substantially larger single-agent models, even when the latter have direct tool access.
  - Orchestrator capacity drives overall performance more than sub-agent capacity ("planner-limited, not executor-limited").
  - Explicit thinking at the orchestrator level yields the largest gains; thinking in sub-agents provides limited or negative benefits.
  - MAS is faster than single-agent direct despite more model calls (due to structured memory and parallel tool use).

### 5. Shen et al. (2026) — "An Empirical Study of Multi-Agent Collaboration for Automated Research"
- **Source:** arXiv:2603.29632 (UT Sydney / SUSTech, Mar 2026)
- **Scope:** Automated ML research (hyperparameter search); single-agent vs. subagent (parallel) vs. agent team (pre-execution handoffs); fixed compute budgets (300s, 600s).
- **Key findings:**
  - Subagent mode: high-throughput, resilient, optimal for broad/shallow optimizations under strict time constraints.
  - Agent team mode: higher operational fragility (multi-author code generation) but achieves deeper theoretical alignment for complex refactoring with extended budgets.
  - Advocates for dynamically routed architectures that adapt collaboration structure to real-time task complexity.

### 6. Sobhani et al. (2026) — "Do Multi-Agents Solve Better Than Single? Evaluating Agentic Frameworks for Diagram-Grounded Geometry Problem Solving"
- **Source:** ACL Anthology 2026.eacl-srw.4 (EACL 2026, Mar 2026)
- **Scope:** Geometry3K, MathVerse, OlympiadBench, We-Math; open-source (Qwen-2.5-VL) vs. closed-source (Gemini-2.0-Flash).
- **Key findings:**
  - For open-source models, multi-agent consistently improves performance (+6.8 pts for Qwen-2.5-VL 7B on Geometry3K).
  - For closed-source Gemini-2.0-Flash, single-agent generally performs better on classic benchmarks; multi-agent yields only modest improvements on newer We-Math.
  - Agentic decomposition is not universally optimal; benefits depend on model capability and benchmark familiarity.

### 7. Jin et al. (2026) — "Two Heads are Better Than One: Test-time Scaling of Multi-agent Collaborative Reasoning"
- **Source:** ACL Anthology 2026.findings-emnlp.427 (EMNLP 2026, Oct 2026)
- **Scope:** Test-time scaling (TTS); M500 dataset of 500 multi-agent collaborative reasoning traces; fine-tuning open-source models.
- **Key findings:**
  - Single-agent TTS faces scalability bottlenecks: excessively long reasoning traces increase inference costs and cause context management failures.
  - MAS decomposes monolithic reasoning chains into manageable contexts across agents, offering a more robust scaling framework.
  - Fine-tuned models (Qwen2.5-32B-MAS, Phi4-14B-MAS) significantly outperform base counterparts.
  - Proposes adaptive scaling with a "CEO" agent to dynamically guide collaboration depth.

### 8. Yin et al. (2025) — "A Comprehensive Empirical Evaluation of Agent Frameworks on Code-centric Software Engineering Tasks"
- **Source:** arXiv:2511.00872 (Chinese Academy of Sciences / Northeastern U., Nov 2025)
- **Scope:** 7 agent frameworks across software development (SRDD), vulnerability detection (LLM-SmartAudit), and program repair (SWE-bench Lite).
- **Key findings:**
  - Single-agent systems demonstrate superior repair effectiveness across all 12 repositories compared to multi-agent systems.
  - Single-agent systems show greater effectiveness in software development, excelling in code executability and consistency.
  - Multi-agent frameworks incur higher coordination overhead (AgentOrchestra has longest trajectories, most correction attempts).
  - Planning and reflection stages dominate multi-agent workflow costs; single-agent costs concentrate on execution and editing.

### 9. ECER 2026 — "When Do Multi-Agent LLM Systems Outperform Single-Agent Approaches?"
- **Source:** ECER 2026 conference paper
- **Scope:** 6 task families (bug fixing, multi-file coding, CSV analysis, document QA, web research, email-style workflow); 48 scored runs.
- **Key findings:**
  - Single-agent outperformed multi-agent on every task family: mean review score 8.91/10 (100% pass rate) vs. 7.90/10 (83.3% pass rate).
  - Single-agent was cheaper ($0.93 vs. $1.90 mean cost) and faster (41.7s vs. 83.7s mean latency).
  - Multi-agent may be justified when tasks genuinely require decomposition, role specialization, or explicit cross-checking.

### 10. MedAgentBoard (2025) — Benchmarking Multi-Agent Collaboration for Medical Tasks
- **Source:** NeurIPS 2025 (Zhu et al.)
- **Scope:** 4 medical task categories (medical VQA, lay summary generation, EHR predictive modeling, clinical workflow automation).
- **Key findings:**
  - Multi-agent collaboration benefits are task-specific and not universal.
  - Multi-agent enhances task completeness in clinical workflow automation but does not consistently outperform advanced single LLMs in textual medical QA.
  - Specialized conventional methods maintain better performance in medical VQA and EHR-based prediction.
  - The inherent complexity and overhead of multi-agent collaboration must be carefully weighed against performance gains.

---

## Industry & Practitioner Sources

### 11. PromptABCD (2026) — "Benchmarking Multi-Agent vs Single-Agent Systems"
- **Source:** promptabcd.com blog (Oct 2026)
- **Key points:**
  - The most common benchmarking error is a weak single-agent baseline that makes multi-agent look better.
  - A genuinely strong single-agent baseline (excellent prompt, right context, good tool access) is a formidable competitor.
  - Benchmark on your hardest cases, not average ones — multi-agent justifies itself on the difficult tail.
  - Re-run benchmarks whenever models upgrade; an architecture that was right for a weaker model may be wrong for a stronger one.
  - Hidden costs: maintenance burden (5 prompts to keep tuned, 5 sets of failure modes), debugging difficulty, and reliability cost of more failure surface.

### 12. Dataiku (2026) — "Single-agent vs. multi-agent systems: Enterprise AI tradeoffs"
- **Source:** dataiku.com blog (Mar 2026)
- **Key points:**
  - Single-agent: simpler to govern, cost-effective for well-defined sequential tasks, easier to monitor and audit.
  - Multi-agent: distributes work across specialized agents, improves scalability, fault tolerance, and parallel execution.
  - Cites Cornell study: coordinated MAS achieved 42.68% success rate on complex planning tasks vs. 2.92% for single-agent GPT-4.
  - Multi-agent requires centralized oversight, audit trails, explicit role boundaries, and communication protocols.
  - Hybrid approaches (central orchestrator + specialist agents) often deliver the best of both.

### 13. LangChain (2025) — "Benchmarking Multi-Agent Architectures"
- **Source:** langchain.com blog (Jun 2025)
- **Key points:**
  - Tested Single Agent, Swarm, and Supervisor architectures on a modified τ-bench with distractor domains.
  - Single agent baseline falls off sharply with 2+ distractor domains; swarm slightly outperforms supervisor.
  - Supervisor architecture underperforms due to "translation" issues (supervisor paraphrases sub-agent responses, introducing errors).
  - Key improvements to supervisor: remove handoff messages from sub-agent context, add forward_message tool, test tool naming.
  - Multi-agent becomes more prevalent as models improve, making generic architectures sufficiently reliable.

### 14. Augment Code (2026) — "Single-Agent vs Multi-Agent AI: When to Scale Your Dev Workflow"
- **Source:** augmentcode.com guide (Apr 2026)
- **Key points:**
  - Google Research scaling study: +81% improvement on parallelizable tasks (Finance-Agent) vs. up to 70% degradation on sequential tasks (PlanCraft).
  - Multi-agent consumes 4–220x more tokens than single-agent (UIUC study).
  - Microsoft Azure SRE team built toward multi-agent specialization, then reversed course after finding handoffs hurt reliability.
  - Single-file edits and debugging: single-agent wins decisively.
  - Cross-service refactoring: multi-agent wins (with caveats — only when modules are independently transformable).
  - Breadth-first research: multi-agent wins decisively (+90.2% over single-agent, Anthropic data).
  - Standard PR review: single-agent; cross-cutting specialized review: multi-agent.
  - Environmental isolation (separate git worktrees) is the defining pattern across vendor implementations.

### 15. Splunk (2026) — "Benchmarks for Multi-Agent AI Systems"
- **Source:** splunk.com blog (Sep 2026)
- **Key points:**
  - Multi-agent benchmarks measure communication, coordination, delegation, and shared decision-making; single-agent benchmarks focus on individual task completion.
  - CLEAR framework (Cost, Latency, Efficacy, Assurance, Reliability): 0.83 correlation with production success vs. 0.41 for accuracy-only evals.
  - pass^k testing (multiple independent attempts) is a more reliable predictor of real-world stability than single-run success.
  - ToolFailBench: best model achieved only 86.33% clean tool-use rate; failures include skipping tools, ignoring results, fabricating outputs.
  - Key benchmarks: MultiAgentBench (MARBLE), BattleAgentBench, SOTOPIA-π, MARL-EVAL, AgentVerse, ST-WebAgentBench.

### 16. Galileo.ai (2025) — "Choosing the Right AI Agent Architecture"
- **Source:** galileo.ai blog (Mar 2025)
- **Key points:**
  - Single-agent: simple to design/manage, low computational resources, best for stable/predictable tasks.
  - Multi-agent: scalable for large/dynamic environments, higher resource demand, adapts quickly to changing conditions.
  - Single-agent best for: customer service chatbots, simple workflow automation, static recommendation systems.
  - Multi-agent best for: autonomous drones, financial systems adjusting to market fluctuations, healthcare AI adapting to patient data.

### 17. Microsoft Cloud Adoption Framework (2026) — "Choosing Between Single-Agent and Multi-Agent Systems"
- **Source:** learn.microsoft.com (Dec 2025, updated Feb 2026)
- **Key points:**
  - Default to single-agent unless specific criteria mandate multi-agent.
  - Multi-agent criteria: (1) crossing security/compliance boundaries, (2) multiple teams involved, (3) future growth planned (3–5+ distinct functions).
  - Single-agent first when: clear roles involved (persona switching may suffice), rapid time-to-market needed, low-cost priority, large amounts of data processed (adjust retrieval first), high-demand process (coordination overhead negates concurrency), different modalities involved.
  - Multi-agent trade-offs: protocol design, error handling, state synchronization, separate prompt engineering, monitoring, debugging, expanded security surface, redundant context processing, latency at each handoff.

### 18. Medium / Mjgmario (2026) — "Single-Agent vs Multi-Agent Systems: When Coordination Helps, Hurts, and Pays Off"
- **Source:** medium.com (@mjgmario)
- **Key points:**
  - Across 260 controlled configurations, performance ranged from -70.0% (PlanCraft Independent) to +80.8% (Finance-Agent Centralized).
  - Multi-agent coordination only helps under specific structural conditions.
  - Industry case: one customer service deployment reported $47,000/month for multi-agent (94.3% accuracy) vs. $22,700/month for single-agent (92.2% accuracy).
  - Token cost multipliers of roughly 4x; significant increases in development time, operational complexity, and debugging effort.

---

## Synthesis: When Does Each Architecture Win?

### Strong Case for Single-Agent
- Well-defined, sequential tasks (single-file edits, debugging, standard code review)
- Tasks that fit within one context window
- Cost-sensitive or latency-sensitive deployments
- When a genuinely strong single-agent baseline can be built
- Multi-hop reasoning under matched compute budgets
- Simple/structured workflows (data entry, classification, FAQ bots)
- When governance, auditing, and monitoring must be simple

### Strong Case for Multi-Agent
- Genuinely parallelizable subtasks (breadth-first research, cross-service refactoring)
- Tasks exceeding one context window's effective utilization
- Complex planning tasks where decomposition is structurally necessary
- Cross-cutting specialized review (security, performance, API contracts)
- When security/compliance boundaries mandate separation
- When multiple teams must maintain domain-specific agents
- When orchestrator capacity (not model size) is the primary driver of performance

### Hybrid / Conditional
- Agent routing/cascade: route simple requests to SAS, complex ones to MAS (Gao et al. 2025)
- Dynamic topology adaptation based on real-time task complexity (Shen et al. 2026)
- Starting with single-agent, transitioning to multi-agent only when testing reveals unresolvable limitations (Microsoft CAF)
- Context degradation regimes: MAS becomes competitive when single-agent context utilization deteriorates (Tran & Kiela 2026)

---

## Key Metrics for Benchmarking

| Dimension | What to Measure |
|---|---|
| **Quality** | Task success rate, accuracy, review score, pass@k, pass^k |
| **Cost** | Token usage, API cost, estimated monthly maintenance hours |
| **Latency** | Time from input to final output, handoff overhead |
| **Reliability** | Consistency across runs, failure surface, graceful degradation |
| **Maintainability** | Engineer-hours/month, number of prompts to tune, debugging difficulty |
| **Coordination** | Communication overhead, handoff loss, error propagation |

---

## Recurring Caveats Across Sources

1. **Weak baseline problem:** Most benchmarks that show MAS superiority use weak single-agent baselines. A strong single-agent baseline often reveals you don't need MAS.
2. **Compute confounding:** Many MAS gains are explained by unaccounted computation (more tokens, longer reasoning traces). When budgets are matched, SAS often matches or beats MAS.
3. **Task structure dependency:** The +81%/-70% asymmetry (Google Research) is the central empirical finding — parallelizable tasks favor MAS, sequential tasks favor SAS.
4. **Model capability interaction:** MAS benefits diminish as base models improve. An architecture that was right for a weaker model may be wrong for a stronger one.
5. **Hidden costs:** Maintenance burden, debugging difficulty, and reliability cost of more failure surface often dominate token costs in production.
6. **Context degradation:** MAS becomes competitive when single-agent effective context utilization is degraded (noisy, long, or misleading contexts).

---

## Sources List

| # | Source | Type | Date |
|---|---|---|---|
| 1 | Gao et al., "Single-agent or Multi-agent Systems? Why Not Both?" (arXiv:2505.18286) | Academic | May 2025 |
| 2 | Tran & Kiela, "Single-Agent LLMs Outperform Multi-Agent Systems on Multi-Hop Reasoning" (arXiv:2604.02460) | Academic | Apr 2026 |
| 3 | Xu et al., "Rethinking the Value of Multi-Agent Workflow" (arXiv:2601.12307) | Academic | Jan 2026 |
| 4 | Żywot et al., "Can Small Agents Collaborate to Beat a Single Large Language Model?" (arXiv:2601.11327) | Academic | Apr 2026 |
| 5 | Shen et al., "An Empirical Study of Multi-Agent Collaboration for Automated Research" (arXiv:2603.29632) | Academic | Mar 2026 |
| 6 | Sobhani et al., "Do Multi-Agents Solve Better Than Single?" (ACL 2026.eacl-srw.4) | Academic | Mar 2026 |
| 7 | Jin et al., "Two Heads are Better Than One: Test-time Scaling of Multi-agent Collaborative Reasoning" (ACL 2026.findings-emnlp.427) | Academic | Oct 2026 |
| 8 | Yin et al., "A Comprehensive Empirical Evaluation of Agent Frameworks on Code-centric Software Engineering Tasks" (arXiv:2511.00872) | Academic | Nov 2025 |
| 9 | ECER 2026, "When Do Multi-Agent LLM Systems Outperform Single-Agent Approaches?" | Academic | 2026 |
| 10 | Zhu et al., MedAgentBoard (NeurIPS 2025) | Academic | 2025 |
| 11 | PromptABCD, "Benchmarking Multi-Agent vs Single-Agent Systems" | Practitioner | Oct 2026 |
| 12 | Dataiku, "Single-agent vs. multi-agent systems: Enterprise AI tradeoffs" | Industry | Mar 2026 |
| 13 | LangChain, "Benchmarking Multi-Agent Architectures" | Industry | Jun 2025 |
| 14 | Augment Code, "Single-Agent vs Multi-Agent AI: When to Scale Your Dev Workflow" | Industry | Apr 2026 |
| 15 | Splunk, "Benchmarks for Multi-Agent AI Systems" | Industry | Sep 2026 |
| 16 | Galileo.ai, "Choosing the Right AI Agent Architecture" | Industry | Mar 2025 |
| 17 | Microsoft CAF, "Choosing Between Building a Single-Agent System or Multi-Agent System" | Industry | Dec 2025 |
| 18 | Mjgmario (Medium), "Single-Agent vs Multi-Agent Systems: When Coordination Helps, Hurts, and Pays Off" | Practitioner | 2026 |
