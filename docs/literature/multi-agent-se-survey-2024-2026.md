# Multi-Agent Systems for Software Engineering: State-of-the-Art Survey (2024--2026)

> Compiled: 2026-03-20 | Scope: ICSE, FSE, ASE, NeurIPS, ICLR, ICML, AAAI, ACL, EMNLP, ISSTA, CHI, arXiv preprints

---

## 1. Framework Comparison Table

| Framework | Venue | Agents / Roles | Communication | Task Decomposition | State Mgmt | Memory / Context | Code Review / QA | Tool Use | Key Benchmark Results | Key Innovation |
|---|---|---|---|---|---|---|---|---|---|---|
| **ChatDev** | ACL 2024 | CEO, CTO, Programmer, Tester, Art Designer | Sequential chat chains (role-pair dialogues) | Static waterfall phases (Design -> Code -> Test -> Doc) | Phase-gated; chat-chain state | Shared chat history per phase; communicative dehallucination | Cross-role review via chat; Tester agent runs & reports bugs | Code interpreter, git | HumanEval: competitive; 67% fewer hallucinations; 89% faster dev cycles (self-reported) | Waterfall-as-chat-chain; SOP-driven role-pair dialogues; MacNet (DAG-based collab, 2024); puppeteer-style RL orchestrator (2025) |
| **MetaGPT** | ICLR 2024 (Oral) | Product Manager, Architect, Project Manager, Engineer, QA | Structured artifact passing (SOPs); publish-subscribe message pool | Static SOP pipeline with executable feedback | SOP-gated pipeline; shared message pool | Global message pool; structured artifacts (PRD, design docs, code) as persistent memory | Self-correction loop; executable verification | Code execution, file I/O | HumanEval pass@1 +4.2%; MBPP +5.4% vs ablation; outperforms ChatDev on executability & cost | Meta-programming: SOPs encoded as prompt sequences; artifact-centric communication reduces hallucination |
| **SWE-agent** | NeurIPS 2024 | Single agent (with custom ACI tools) | N/A (single agent) | N/A (single agent; iterative tool use) | Trajectory-based; tool-call history | History processor compresses context; informative error messages | Linter / syntax checks as guardrails | Custom ACI: file viewer/editor, search_file, search_dir, find_file, bash | SWE-bench: 12.5% (original); SWE-bench Verified: up to 77.4% with Live-SWE-agent | Agent-Computer Interface (ACI) design; demonstrates interface design > model choice |
| **AutoCodeRover** | ISSTA 2024 | Single agent (two-stage) | N/A (single agent) | Two stages: context collection -> patch generation | Stage-gated | AST-based code search; spectrum-based fault localization | Patch retry on failure; test-suite validation | AST search (class/method), fault localization, code navigation | SWE-bench Lite: 19% (pass@1); avg $0.43/issue; 4 min/issue | AST-aware code search + spectrum fault localization; structure-aware context gathering |
| **AgentCoder** | arXiv 2024 | Programmer, Test Designer, Test Executor | Feedback loop: Executor -> Programmer; Test Designer independent | Static 3-agent pipeline with iterative refinement | Iterative cycle until tests pass | Chain-of-Thought decomposition in programmer | Test executor validates; iterative feedback to programmer | Code execution sandbox | HumanEval: 91.5% (GPT-4); MBPP: 84.1% (GPT-4); 56.9K tokens (vs MetaGPT 138.2K) | Separation of test design from code generation; minimal token overhead |
| **MapCoder** | ACL 2024 | Retrieval, Plan, Code, Debug agents | Pipeline with adaptive traversal schema | Dynamic: retrieval -> plan -> code -> debug with adaptive re-traversal | Agent traversal graph | In-context learning from retrieved examples; augmented signals cascade | Debug agent iterative repair | Code execution for testing | HumanEval: 93.9%; MBPP: 83.1%; APPS: 22.0%; CodeContests: 28.5% | Replicates human programming cycle; adaptive agent traversal; retrieval-augmented planning |
| **CodeR** | arXiv 2024 | Manager, Reproducer, Fault Localizer, Editor, Verifier | Pre-defined task graph connecting agents | Static task graphs (Plan A/B/C with optional loops) | Task graph state; execution plan selection | Repository-level context; structured task handoffs | Verifier agent runs integration/reproduction tests | Bash, file editing, test execution | SWE-bench Lite: 28.33% | Pre-defined task graphs for issue resolution; multiple execution plans (A/B/C); implicit > explicit patch generation |
| **MASAI** | arXiv 2024 | Modular sub-agents with well-defined objectives | Sub-agent coordination; independent info gathering | Modular: each sub-agent tuned for specific sub-problem | Modular; sub-agents avoid long trajectories | Distributed context across sub-agents; avoids single long trajectory | Sub-agent verification | Repository navigation, code search, editing | SWE-bench Lite: 28.33% (highest at time of publication) | Modular architecture enabling per-sub-agent strategy tuning; avoids monolithic trajectory inflation |
| **MAGIS** | NeurIPS 2024 | Manager, Repository Custodian, Developer, QA Engineer | Multi-agent planning + iterative QA feedback | Manager decomposes; agents execute specialized sub-tasks | Manager-coordinated pipeline | Repository-level context via Custodian agent | QA Engineer provides task-specific, timely feedback during iteration | Code editing, repository search, test execution | SWE-bench: 13.94% (8x over direct GPT-4) | QA-in-the-loop during development (not just post-hoc); Custodian agent for repo context |
| **OpenHands** | arXiv 2024; ICLR 2025 Workshop | CodeActAgent, BrowsingAgent, custom agents | Event-stream abstraction; AgentDelegateAction for multi-agent | Dynamic delegation via AgentDelegateAction | Event-sourced state; session-based | Context window management; event-sourced history | Agent-level review; sandbox execution | Shell, code editor, web browser (Docker sandbox) | SWE-bench Verified: competitive (depends on underlying model) | Event-stream architecture; agent delegation protocol; containerized sandboxes; Agent SDK for composability |
| **Devin** | Cognition Labs (proprietary, Mar 2024) | Single autonomous agent (multi-agent in later versions) | Natural language chat interface; API for external integration | Autonomous planning from NL prompt | Long-term planning with RL | Long-term recall; learns from mistakes over time | Self-testing and error correction | Shell, code editor, web browser (sandboxed) | SWE-bench: 13.86% (at launch) | First marketed "AI software engineer"; end-to-end autonomous workflow; later added multi-agent dispatch |
| **Self-Organized Agents (SoA)** | arXiv 2024 | Mother agent + dynamically spawned Child agents | Hierarchical delegation; child agents operate independently | Dynamic: auto-multiplies agents based on problem complexity | Hierarchical; per-agent code scope | Each agent manages constant-sized context; scales via agent count | Collaborative review among siblings | Code generation and editing | Scalable code generation (beyond single-context limits) | Dynamic agent multiplication; constant per-agent context regardless of total code scale; hierarchical Mother/Child |
| **AppAgent** | CHI 2025 | Single agent (exploration + deployment phases) | N/A (single agent) | Two-phase: explore then deploy | Knowledge base state; RAG retrieval | Structured knowledge base built during exploration; RAG for deployment | N/A (GUI testing focus) | Smartphone GUI interaction (tap, swipe, type) | 65+ real-world mobile app tasks | Multimodal GUI agent; autonomous exploration to build knowledge base; RAG-based deployment |
| **Aider** | Open-source tool | Architect + Editor (dual-model) | Architect describes solution -> Editor applies edits | Architect plans, Editor executes | Git-tracked file state | Repository map; git diff context; selective file inclusion | Git-based change tracking; linting | File editing, git, shell commands | Competitive on SWE-bench Verified; SOTA with Architect mode | Architect/Editor dual-model pattern; git-native workflow; repo-map for context management |
| **ALMAS** | arXiv Oct 2025 | Sprint Agent, Supervisor, Developer, Summary, Control Agent | Agile team hierarchy; dual-mode (autonomous + interactive) | Agile sprint-based decomposition | Sprint-cycle state management | NL replicas of codebase kept in sync; token-efficient | Peer review agent; QA feedback | Code generation, testing, repository management | End-to-end SDLC coverage | Agile-aligned agent roles; dual autonomous/interactive modes; cost-efficient NL codebase replicas |

---

## 2. Detailed Framework Analysis

### 2.1 ChatDev (ACL 2024)

- **Paper**: "Communicative Agents for Software Development" ([arXiv:2307.07924](https://arxiv.org/abs/2307.07924))
- **Architecture**: Waterfall pipeline divided into Design, Coding, Testing, Documentation phases. Each phase is a "chat chain" where two role-assigned agents engage in multi-turn dialogue.
- **Key Innovation**: Chat-chain paradigm that structures multi-agent collaboration as sequential role-pair conversations. The 2024 MacNet extension ([Multi-Agent Collaboration Networks](https://github.com/OpenBMB/ChatDev)) introduced DAG-based agent topologies. A 2025 "puppeteer-style" paradigm uses RL to optimize a central orchestrator.
- **Limitation**: Static waterfall decomposition; chat chains can be rigid for non-linear workflows.

### 2.2 MetaGPT (ICLR 2024 Oral)

- **Paper**: "Meta Programming for A Multi-Agent Collaborative Framework" ([arXiv:2308.00352](https://arxiv.org/abs/2308.00352))
- **Architecture**: Assembly-line paradigm with SOPs encoded as prompt sequences. Agents pass structured artifacts (PRDs, design documents, code) via a global message pool.
- **Key Innovation**: Meta-programming approach where human SOPs are formalized into agent workflows. Artifact-centric communication (vs. free-form chat) reduces information loss and hallucination.
- **Strength**: Strong software engineering methodology alignment; executability significantly higher than ChatDev.

### 2.3 SWE-agent (NeurIPS 2024)

- **Paper**: "Agent-Computer Interfaces Enable Automated Software Engineering" ([arXiv:2405.15793](https://arxiv.org/abs/2405.15793))
- **Architecture**: Single-agent with carefully designed Agent-Computer Interface (ACI). Custom tools for search, navigation, file viewing/editing with syntax guardrails.
- **Key Innovation**: Demonstrates that *interface design* matters more than model choice. The ACI concept (analogous to HCI for humans) is a foundational contribution. Spawned Live-SWE-agent (77.4% on SWE-bench Verified).
- **Note**: Not multi-agent, but its ACI principles are adopted by multi-agent frameworks.

### 2.4 AutoCodeRover (ISSTA 2024)

- **Paper**: "Autonomous Program Improvement" ([arXiv:2404.05427](https://arxiv.org/abs/2404.05427))
- **Architecture**: Two-stage single agent. Stage 1: iterative AST-based code search for context. Stage 2: single-shot patch generation with retry.
- **Key Innovation**: Structure-aware code search using AST and spectrum-based fault localization. Extremely cost-efficient ($0.43/issue avg).

### 2.5 AgentCoder (arXiv 2024)

- **Paper**: "Multi-Agent-based Code Generation with Iterative Testing and Optimisation" ([arXiv:2312.13010](https://arxiv.org/abs/2312.13010))
- **Architecture**: Three-agent pipeline (Programmer, Test Designer, Test Executor) with feedback loop.
- **Key Innovation**: Decoupling test design from code generation ensures test objectivity. Achieves high pass rates with significantly lower token overhead than MetaGPT/ChatDev.

### 2.6 MapCoder (ACL 2024)

- **Paper**: "Multi-Agent Code Generation for Competitive Problem Solving" ([ACL Anthology](https://aclanthology.org/2024.acl-long.269/))
- **Architecture**: Four-agent pipeline (Retrieval, Plan, Code, Debug) with adaptive traversal.
- **Key Innovation**: Retrieval-augmented planning that mimics the human programming cycle. Adaptive agent traversal allows dynamic re-routing through the pipeline.

### 2.7 CodeR (arXiv 2024)

- **Paper**: "Issue Resolving with Multi-Agent and Task Graphs" ([arXiv:2406.01304](https://arxiv.org/abs/2406.01304))
- **Architecture**: Pre-defined task graphs connecting specialized agents (Manager, Reproducer, Fault Localizer, Editor, Verifier).
- **Key Innovation**: Multiple execution plans (A/B/C) with varying complexity. Demonstrates implicit patch generation (agent edits code) outperforms explicit patch generation.

### 2.8 MASAI (arXiv 2024)

- **Paper**: "Modular Architecture for Software-engineering AI Agents" ([arXiv:2406.11638](https://arxiv.org/abs/2406.11638))
- **Architecture**: Modular sub-agents, each with well-defined objectives and independently tuned strategies.
- **Key Innovation**: Avoids monolithic trajectory problem by distributing work across specialized sub-agents. Each sub-agent can use different strategies and gather information independently.

### 2.9 MAGIS (NeurIPS 2024)

- **Paper**: "LLM-Based Multi-Agent Framework for GitHub Issue Resolution" ([NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/5d1f02132ef51602adf07000ca5b6138-Abstract-Conference.html))
- **Architecture**: Four agents (Manager, Repository Custodian, Developer, QA Engineer) with iterative QA feedback.
- **Key Innovation**: QA-in-the-loop during development rather than post-hoc testing. Repository Custodian agent specializes in codebase navigation.

### 2.10 OpenHands (arXiv 2024)

- **Paper**: "An Open Platform for AI Software Developers as Generalist Agents" ([arXiv:2407.16741](https://arxiv.org/abs/2407.16741))
- **Architecture**: Event-stream abstraction with AgentDelegateAction for multi-agent composition. Docker-sandboxed execution.
- **Key Innovation**: Event-sourced state management; composable agent SDK with 9 interlocking components; agent delegation protocol for specialization.

### 2.11 Devin (Cognition Labs, 2024)

- **Source**: [cognition.ai](https://cognition.ai/blog/introducing-devin)
- **Architecture**: Autonomous agent with shell, editor, browser in sandboxed environment. Later versions added multi-agent dispatch.
- **Key Innovation**: First commercially marketed "AI software engineer." End-to-end autonomy with long-term planning via RL. Multi-agent dispatch added in later revisions.

### 2.12 Self-Organized Agents (arXiv 2024)

- **Paper**: "A LLM Multi-Agent Framework toward Ultra Large-Scale Code Generation" ([arXiv:2404.02183](https://arxiv.org/abs/2404.02183))
- **Architecture**: Hierarchical Mother/Child agent spawning. Agents auto-multiply based on problem complexity.
- **Key Innovation**: Dynamic scalability -- each agent handles constant-sized context, total code volume scales with agent count. Addresses context-length limitations.

### 2.13 Aider (Open-source, 2024-2025)

- **Source**: [aider.chat](https://aider.chat/)
- **Architecture**: Architect/Editor dual-model pattern. Architect (reasoning model) plans changes; Editor (fast model) applies file edits. Git-native workflow.
- **Key Innovation**: Dual-model separation of planning and execution. Repository map for efficient context selection. Git-integrated change tracking.

### 2.14 ALMAS (arXiv Oct 2025)

- **Paper**: "An Autonomous LLM-based Multi-Agent Software Engineering Framework" ([arXiv:2510.03463](https://arxiv.org/abs/2510.03463))
- **Architecture**: Agile-aligned agents (Sprint, Supervisor, Developer, Summary, Control) with dual autonomous/interactive modes.
- **Key Innovation**: Agile methodology integration; cost-efficient NL codebase replicas; supports human-in-the-loop via "three Cs" (Context-aware, Collaborative, Cost-effective).

---

## 3. Key Dimensions Deep Dive

### 3.1 Agent Role Design Patterns

| Pattern | Examples | Pros | Cons |
|---|---|---|---|
| **Software-team mirroring** | ChatDev, MetaGPT, MAGIS, ALMAS | Natural mapping to SDLC; clear responsibilities | Can be rigid; roles may not map to LLM strengths |
| **Functional specialization** | AgentCoder, MapCoder, MASAI | Tuned strategies per sub-task; lower overhead | Requires careful interface design between agents |
| **Hierarchical delegation** | SoA, CodeR | Scalable; dynamic complexity handling | Coordination overhead; potential bottleneck at root |
| **Dual-model (Architect/Editor)** | Aider | Leverages model strengths; cost-efficient | Limited to two-role decomposition |
| **Single agent + tools** | SWE-agent, AutoCodeRover, Devin | Simple; avoids coordination cost | Limited by single context window |

### 3.2 Communication Protocols

| Protocol | Frameworks | Description |
|---|---|---|
| **Chat chains** | ChatDev | Sequential role-pair dialogues |
| **Artifact passing (SOP)** | MetaGPT | Structured documents passed through pipeline |
| **Event stream** | OpenHands | Actions and observations as events |
| **Task graph** | CodeR | Pre-defined DAG of agent interactions |
| **Feedback loops** | AgentCoder, MAGIS | Test/QA results fed back to developer agent |
| **Hierarchical delegation** | SoA, ALMAS | Parent agents dispatch to child agents |
| **Shared blackboard** | MetaGPT (message pool) | Global shared state accessible by all agents |

### 3.3 Task Decomposition

| Strategy | Frameworks | Notes |
|---|---|---|
| **Static pipeline (waterfall)** | ChatDev, MetaGPT | Fixed phase sequence |
| **Static task graph** | CodeR | Pre-defined but with plan selection |
| **Modular sub-agents** | MASAI | Each sub-agent has own objective |
| **Dynamic spawning** | SoA | Agents multiply based on complexity |
| **Adaptive traversal** | MapCoder | Dynamic re-routing through agent pipeline |
| **Agile sprints** | ALMAS | Sprint-based iterative decomposition |
| **Two-stage** | AutoCodeRover | Context gathering -> patch generation |

### 3.4 Self-Reflection & Iterative Refinement

| Mechanism | Frameworks |
|---|---|
| **Test-driven feedback loop** | AgentCoder, CodeR, MAGIS, MapCoder |
| **Communicative dehallucination** | ChatDev |
| **Self-correction via execution** | MetaGPT |
| **QA-in-the-loop** | MAGIS, ALMAS |
| **Linter/syntax guardrails** | SWE-agent |
| **Retry on patch failure** | AutoCodeRover |
| **Peer review agent** | ALMAS |
| **Debug agent** | MapCoder |

### 3.5 Memory & Context Management

| Approach | Frameworks | Description |
|---|---|---|
| **Per-phase chat history** | ChatDev | Context resets between phases |
| **Global message pool** | MetaGPT | All agents can read shared artifacts |
| **Event-sourced history** | OpenHands | Full event log with context window management |
| **AST-based retrieval** | AutoCodeRover | Structure-aware code search |
| **Repository map** | Aider | Selective file inclusion based on relevance |
| **NL codebase replicas** | ALMAS | Compressed natural language summaries |
| **Knowledge base + RAG** | AppAgent | Exploration-built knowledge base |
| **Agent multiplication** | SoA | Constant context per agent; scale via agent count |

---

## 4. Benchmark Landscape

### 4.1 SWE-bench Family (Repository-Level Issue Resolution)

| Benchmark | Description | Top Results (as of early 2026) |
|---|---|---|
| **SWE-bench** (full) | 2,294 real GitHub issues | Early systems: 1-14% |
| **SWE-bench Lite** | 300 curated issues | MASAI/CodeR: 28.33%; AutoCodeRover: 19% |
| **SWE-bench Verified** | 500 human-validated issues | Claude Opus 4.5: ~80.9%; Live-SWE-agent + Claude: 79.2% |
| **SWE-bench Pro** | Long-horizon, harder tasks | Claude Opus 4.5: 45.9%; Live-SWE-agent: 45.8% |
| **SWE-bench Live** | Continuously updated | Active leaderboard |

**Note**: OpenAI stopped reporting Verified scores due to training data contamination concerns across all frontier models.

### 4.2 Code Generation Benchmarks

| Benchmark | Description | Top Multi-Agent Results |
|---|---|---|
| **HumanEval** | 164 Python problems | MapCoder: 93.9%; AgentCoder: 91.5% (GPT-4) |
| **MBPP** | 974 Python problems | AgentCoder: 84.1% (GPT-4); MapCoder: 83.1% |
| **APPS** | Competitive programming | MapCoder: 22.0% |
| **CodeContests** | Competition-level | MapCoder: 28.5% |
| **HumanEvalFix** | Bug-fixing variant | SWE-agent: 87.7% |

---

## 5. Survey Papers

### 5.1 Major Surveys on LLM-Based Agents

| Survey | Venue | Focus | Key Contribution |
|---|---|---|---|
| "A Survey on Large Language Model based Autonomous Agents" ([arXiv:2308.11432](https://arxiv.org/abs/2308.11432)) | Frontiers of Computer Science 2024 | General LLM agents | Unified framework: profile, memory, planning, action modules |
| "Large Language Model based Multi-Agents: A Survey of Progress and Challenges" ([arXiv:2402.01680](https://arxiv.org/abs/2402.01680)) | IJCAI 2024 | Multi-agent LLM systems | Taxonomy of communication, coordination, and application domains |
| "LLM-Based Multi-Agent Systems for Software Engineering" ([arXiv:2404.04834](https://arxiv.org/abs/2404.04834)) | ACM TOSEM 2025 | SE-specific multi-agent | Maps LMA applications across SDLC stages; identifies vision and road ahead |
| "Large Language Model-Based Agents for Software Engineering: A Survey" ([arXiv:2409.02977](https://arxiv.org/abs/2409.02977)) | Fudan SE Lab 2024 | SE-specific agents | 124 papers; dual perspective (SE + agent) taxonomy |
| "From LLMs to LLM-based Agents for SE" ([arXiv:2408.02479](https://arxiv.org/abs/2408.02479)) | arXiv 2024 | SE agent evolution | Covers requirement eng, code gen, decision-making, testing, maintenance |
| "A survey on LLM-based multi-agent systems: workflow, infrastructure, and challenges" | Springer 2024 | General MAS infrastructure | 5-component structure: profile, perception, self-action, interaction, evolution |

### 5.2 Key Themes from Surveys

1. **Transition from single-agent to multi-agent**: Most 2024+ systems adopt multi-agent architectures for complex SE tasks.
2. **SOP/workflow formalization**: Encoding human processes (waterfall, agile) into agent coordination protocols is a dominant pattern.
3. **Context management is the bottleneck**: All surveys highlight context window limitations as the primary scaling challenge.
4. **Evaluation gaps**: SWE-bench dominates but has contamination issues; no consensus benchmark for full SDLC evaluation.
5. **Cost vs. capability tradeoff**: Multi-agent systems use more tokens but achieve higher quality; token efficiency is a design priority.

---

## 6. Architectural Pattern Taxonomy

Based on the surveyed frameworks, five architectural patterns emerge:

### Pattern 1: Pipeline / Assembly Line
**ChatDev, MetaGPT, AgentCoder**
- Fixed sequence of agent roles
- Artifacts flow unidirectionally (with optional feedback loops)
- Strength: Predictable, easy to debug
- Weakness: Inflexible for non-linear workflows

### Pattern 2: Task Graph / DAG
**CodeR, ChatDev MacNet, MASAI**
- Agents connected via directed acyclic graphs
- Supports parallel execution of independent sub-tasks
- Strength: Parallelism, modularity
- Weakness: Requires upfront graph design

### Pattern 3: Hierarchical Delegation
**SoA, ALMAS, Devin (later versions)**
- Parent agents decompose and delegate to child agents
- Dynamic scaling via agent spawning
- Strength: Scales to large codebases
- Weakness: Coordination overhead, potential bottleneck at root

### Pattern 4: Event-Driven / Reactive
**OpenHands**
- Event-stream abstraction; agents react to observations
- Flexible multi-agent composition via delegation actions
- Strength: Composable, extensible
- Weakness: Harder to reason about global state

### Pattern 5: Single Agent + Rich Tools
**SWE-agent, AutoCodeRover, Aider, Devin (original)**
- One agent with well-designed tool interfaces
- Strength: Simple, low coordination cost, effective with strong models
- Weakness: Context window limits scalability

---

## 7. Implications for Custom Framework Design

### What the best systems share:
1. **Clear role separation** with well-defined interfaces between agents
2. **Feedback loops** (test execution, QA review) that drive iterative improvement
3. **Structure-aware context** (AST, repo maps, fault localization) over raw text
4. **Token efficiency** as a first-class design concern
5. **Containerized/sandboxed execution** for safety and reproducibility

### Open challenges:
1. **Benchmark contamination**: SWE-bench Verified may be compromised; Pro/Live variants are emerging
2. **Long-horizon tasks**: Current systems struggle beyond single-issue resolution
3. **Dynamic team composition**: Most frameworks use static role assignments; SoA and ALMAS are exceptions
4. **Cross-repository reasoning**: No framework handles multi-repo dependencies well
5. **Human-AI collaboration**: ALMAS's dual-mode is promising but underexplored
6. **Cost scaling**: Multi-agent token costs grow quickly; efficient routing is critical

---

## Sources

- [ChatDev - ACL 2024](https://aclanthology.org/2024.acl-long.810/)
- [ChatDev GitHub](https://github.com/OpenBMB/ChatDev)
- [MetaGPT - ICLR 2024](https://openreview.net/forum?id=VtmBAGCN7o)
- [MetaGPT arXiv](https://arxiv.org/abs/2308.00352)
- [SWE-agent - NeurIPS 2024](https://arxiv.org/abs/2405.15793)
- [SWE-agent GitHub](https://github.com/SWE-agent/SWE-agent)
- [AutoCodeRover - ISSTA 2024](https://dl.acm.org/doi/10.1145/3650212.3680384)
- [AgentCoder arXiv](https://arxiv.org/abs/2312.13010)
- [MapCoder - ACL 2024](https://aclanthology.org/2024.acl-long.269/)
- [CodeR arXiv](https://arxiv.org/abs/2406.01304)
- [MASAI arXiv](https://arxiv.org/abs/2406.11638)
- [MAGIS - NeurIPS 2024](https://proceedings.neurips.cc/paper_files/paper/2024/hash/5d1f02132ef51602adf07000ca5b6138-Abstract-Conference.html)
- [OpenHands arXiv](https://arxiv.org/abs/2407.16741)
- [OpenHands Agent SDK](https://arxiv.org/html/2511.03690v1)
- [Devin - Cognition Labs](https://cognition.ai/blog/introducing-devin)
- [Self-Organized Agents arXiv](https://arxiv.org/abs/2404.02183)
- [AppAgent - CHI 2025](https://dl.acm.org/doi/full/10.1145/3706598.3713600)
- [Aider](https://aider.chat/)
- [ALMAS arXiv](https://arxiv.org/abs/2510.03463)
- [Survey: LLM-based Autonomous Agents](https://arxiv.org/abs/2308.11432)
- [Survey: LLM Multi-Agents Progress & Challenges - IJCAI 2024](https://arxiv.org/abs/2402.01680)
- [Survey: LLM-Based MAS for SE - ACM TOSEM](https://dl.acm.org/doi/10.1145/3712003)
- [Survey: LLM-Based Agents for SE](https://arxiv.org/abs/2409.02977)
- [Survey: LLMs to LLM-based Agents for SE](https://arxiv.org/abs/2408.02479)
- [SWE-bench Verified Leaderboard](https://epoch.ai/benchmarks/swe-bench-verified)
- [SWE-bench Official](https://www.swebench.com/)
- [Live-SWE-agent](https://live-swe-agent.github.io/)
- [SALLMA - ICSE 2025](https://conf.researchr.org/details/icse-2025/satrends-2025-papers/7/SALLMA-A-Prototypical-Software-Architecture-for-LLM-Based-Multi-Agent-Systems)
- [MAS-GAIN Workshop - ASE 2025](https://masgain.github.io/masgain2025/)
