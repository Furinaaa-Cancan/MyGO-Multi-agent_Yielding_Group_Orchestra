# MyGO 框架改进知识库

> 基于顶刊论文调研 + 深度代码审计 + 架构分析，为后续框架演进提供检索基础。
> 最后更新：2026-03-15

---

## 目录

- [一、当前架构问题诊断](#一当前架构问题诊断)
- [二、顶刊论文知识图谱](#二顶刊论文知识图谱)
- [三、框架改进方案与新思考](#三框架改进方案与新思考)
- [四、实施路线图](#四实施路线图)

---

## 一、当前架构问题诊断

### 1.1 关键缺陷总览

| 等级 | 类别 | 问题数 | 影响 |
|------|------|--------|------|
| **P0 Critical** | 并发/状态/恢复 | 5 | 阻塞生产使用 |
| **P1 High** | 可扩展性/耦合 | 5 | 限制框架演进 |
| **P2 Medium** | 代码质量/可测试性 | 8 | 增加维护成本 |
| **P3 Low** | 可发现性/文档 | 4 | 影响开发者体验 |

### 1.2 P0 — 关键缺陷

#### 1.2.1 无类型状态管理 (S1)

**位置**: `graph.py`, `cli_watch.py`, `session.py`

**问题**: 整个状态机使用 `dict[str, Any]` 传递状态。`ConversationEvent` TypedDict 存在但从未强制执行。所有状态访问都是防御性 `.get()` 调用。

**影响**:
- 字段名拼写错误（如 `retry_count` vs `retry_cnt`）静默失败
- 无法在编译期发现缺失字段
- 图状态突变对类型检查器不可见

**论文启示**: MetaGPT 使用 `SharedEnvironment` 强类型共享状态；MASAI 每个 sub-agent 有独立的类型化上下文。

**建议方案**:
```python
# 当前
state["retry_count"] = state.get("retry_count", 0) + 1

# 改进: 使用 Pydantic BaseModel 替代 TypedDict
class WorkflowState(BaseModel):
    retry_count: int = 0
    conversation: list[ConversationEvent] = []
    builder_id: str | None = None
    # ...强制类型
```

---

#### 1.2.2 全局可变状态与竞态条件 (C1)

**位置**: `driver.py:59-60`, `cli_watch.py:37`, `semantic_memory.py`, `hooks.py:59`

**问题**: 线程本地和全局状态管理散布多个模块，锁机制临时拼凑：
- `_cli_lock` / `_active_agents` — driver 层
- `_resume_lock` — watch 层
- `fcntl` 文件锁 — memory 层
- `_registry` — hooks 层

**竞态场景**:
1. Check-then-act: `_active_agents.get(key)` → `exists.is_alive()` → 注册新线程（检查和注册之间存在 TOCTOU）
2. 并行分解：多个子任务同时写入全局 TASK.md/outbox
3. 语义记忆：文件锁不保护 JSONL 读-修改-写循环

**论文启示**: ChatDev 使用消息队列解耦 agent 通信；AutoGen 的 GroupChatManager 有集中式消息路由。

**建议方案**:
- 全局 dict → 线程安全队列（`queue.Queue`）
- 文件操作 → 乐观锁 + CAS（Compare-and-Swap）
- 引入集中式 StateCoordinator

---

#### 1.2.3 Checkpoint 恢复不完整 (R1)

**位置**: `meta_graph.py:29-86`, `graph.py`

**问题**:
1. Checkpoint 仅在任务级；子任务失败不保存中间状态直到完成
2. 无 checkpoint 版本控制——格式变化后旧 checkpoint 不可读
3. 分解循环崩溃 → 整个分解丢失
4. 无 WAL（预写日志）；checkpoint 写入可部分失败导致 JSON 损坏
5. `meta_graph.py:46-60` 有 tempfile + rename，但 rename 前无 fsync

**论文启示**: SWE-agent 使用 trajectory 快照实现精确恢复；OpenHands 的 event stream 支持任意时间点回放。

**建议方案**:
```
WAL 架构:
1. 所有状态变更先写入 WAL (append-only JSONL)
2. 定期合并为 checkpoint snapshot
3. 恢复时：加载最近 snapshot + 重放 WAL 增量
4. 每个子任务独立 snapshot
```

---

#### 1.2.4 无界对话膨胀 (Sc1)

**位置**: `graph.py:35-45`

**问题**: 对话列表通过 `Annotated[list[...], add]` reducer 无限累积。10+ 轮重试后对话可超 1MB。LangGraph checkpoint 序列化变慢（每次状态快照 O(n)）。

**论文启示**: MemGPT 使用分层记忆（主记忆 + 外部存储）；Generative Agents 使用记忆检索 + 遗忘机制。

**建议方案**:
- 滑动窗口：保留最近 N 条 + 摘要旧条目
- 参考 MemGPT: working memory (last 3 turns) + archival memory (compressed history)
- 实现 LRU 驱逐策略

---

#### 1.2.5 循环依赖与级联导入 (A5)

**导入链**:
- `graph.py` → `router.py` → `driver.py` → `graph.py`
- `cli.py` → `session.py` → `graph.py` → `cli_watch.py` → `cli.py`

**影响**:
- 无法独立单元测试 `driver.py`
- 重构 `router.py` 需触及 5+ 模块
- 导入顺序重要——未来改动可破坏初始化

**建议方案**: 严格分层
```
Layer 0: config, schema, _utils        (无外部依赖)
Layer 1: contract, prompt, memory       (仅依赖 L0)
Layer 2: workspace, trace, state_machine (仅依赖 L0-1)
Layer 3: router, driver                 (仅依赖 L0-2)
Layer 4: graph, session, orchestrator   (可依赖 L0-3)
Layer 5: cli, cli_*                     (入口层)
```

---

### 1.3 P1 — 高优先级问题

#### 1.3.1 分解子系统架构割裂 (A6)

**问题**: `meta_graph.py` + `decompose.py` 创建了绕过主图的并行执行模型。子任务有独立 `WorkflowState`，不与主图共享错误处理、重试策略、超时管理。

**论文启示**: MASAI 的 sub-agent 系统证明模块化带来 +7.67% 性能提升，但前提是 sub-agent 间有良好的状态传递协议。

**建议**: 使用 LangGraph SubGraph 统一分解和主任务。

#### 1.3.2 无分布式追踪 (O1)

**问题**: `trace_id` 始终为 `"0" * 16`，子任务间无关联 ID，调试需手动 grep 日志。

**论文启示**: SWE-Debate (ICSE 2026) 使用结构化 debate log 实现完整的推理追踪。

**建议**: 引入 OpenTelemetry spans 或 contextvars 关联。

#### 1.3.3 硬编码工作流逻辑 (E1)

**问题**: 重试策略、超时、质量门禁、复杂度估算全部硬编码在图节点中。无法通过配置自定义。

**论文启示**: DSPy 的 "programming over prompting" 理念——将 LLM 行为参数化。

**建议**: 提取为可插拔 Strategy 模式。

#### 1.3.4 Session/CLI 状态重复 (A7)

**问题**: 两条执行路径（`session.py` 的 pull/push 和 `cli.py` 的 watch loop）都调用同一个图但状态管理不同，bug 修复不互相传播。

**建议**: 统一执行引擎 + 可插拔输入/输出适配器。

#### 1.3.5 Router 过度耦合 (C4)

**问题**: Agent 选择与能力匹配紧耦合，无多目标优化（成本 vs 可靠性 vs 可用性），健康过滤硬编码。

**论文启示**: FrugalGPT 的级联路由——按成本从低到高尝试模型。

**建议**: 分离 agent 注册表、能力索引、健康监控为独立服务。

---

### 1.4 P2 — 中等问题

| ID | 问题 | 位置 | 建议 |
|----|------|------|------|
| C2 | 工作空间锁语义未定义，无锁超时 | `workspace.py` | 实现带心跳的分布式锁 |
| C5 | 语义记忆无 ACID 保证 | `semantic_memory.py` | 改用 SQLite 事务 |
| R2 | Hook 系统无错误隔离 | `hooks.py` | 异步执行 + 超时 |
| M1 | FinOps 定价硬编码 | `finops.py` | 外部配置 + API |
| R3 | 通知系统无幂等性 | `notify.py` | 幂等键 + 异步队列 |
| D1 | 输出无运行时 schema 校验 | `workspace.py`, `graph.py` | Pydantic 校验所有输出 |
| Rf1 | CLI watch 提取不完整 | `cli.py`, `cli_watch.py` | 移除重导出 |
| D2 | 多重状态表示 | `graph.py` vs `schema.py` | 统一为 schema.Task |

---

## 二、顶刊论文知识图谱

### 2.1 多 Agent 协作框架

#### [P01] MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework
- **作者**: Hong et al.
- **会议**: ICLR 2024
- **核心贡献**: 将 SOP（标准操作流程）编码为 multi-agent 协作框架。引入 SharedEnvironment 实现 agent 间信息共享，使用 Publish-Subscribe 模式解耦通信。
- **关键发现**: 结构化输出（SRS 文档、API 设计、代码）比自由文本交互产出质量高 30%+
- **对 MyGO 的启示**:
  - **可借鉴**: SharedEnvironment 的发布-订阅模式替代文件 I/O 通信
  - **可借鉴**: 角色间使用结构化中间产物（当前 MyGO 用自由文本 prompt）
  - **差异**: MetaGPT 直接调用 LLM API，MyGO 驱动外部 IDE 黑箱

#### [P02] ChatDev: Communicative Agents for Software Development
- **作者**: Qian et al.
- **会议**: ACL 2024
- **核心贡献**: 模拟软件公司的 chat-chain 工作流（CEO → CTO → Programmer → Tester）。引入 "hallucination-aware" 互审机制。
- **关键发现**: 角色对话比单 agent 减少 23.5% 代码错误
- **对 MyGO 的启示**:
  - **可借鉴**: 更细粒度的角色定义（当前仅 builder/reviewer 二分法）
  - **可借鉴**: 对话链中嵌入幻觉检测
  - **差异**: ChatDev agent 可自由对话，MyGO 的 IDE agent 只能读文件写文件

#### [P03] AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation
- **作者**: Wu et al. (Microsoft)
- **会议**: COLM 2024
- **核心贡献**: 可定制的 multi-agent 对话框架。支持人类参与、工具使用、嵌套对话。GroupChatManager 实现灵活的拓扑控制。
- **关键发现**: 对话式拓扑在探索性任务上优于固定管道
- **对 MyGO 的启示**:
  - **可借鉴**: 人类参与模式（human-in-the-loop）的 UX 设计
  - **差异**: AutoGen 的灵活拓扑不适合 MyGO 的文件 I/O 约束
  - **启发**: 考虑在 CLI 模式下支持有限的 agent 间消息传递

#### [P04] CAMEL: Communicative Agents for "Mind" Exploration of Large Language Model Society
- **作者**: Li et al.
- **会议**: NeurIPS 2023
- **核心贡献**: 角色扮演（role-playing）框架，通过 inception prompting 引导 agent 合作。系统研究了 agent 间的对齐和社会行为。
- **对 MyGO 的启示**: 角色扮演 prompt 设计可增强 builder/reviewer 的对抗性

#### [P05] CrewAI — Role-based Multi-agent Framework
- **作者**: Moura et al.
- **来源**: 开源框架 (2024)
- **核心贡献**: 角色驱动的 agent 协作，支持 sequential 和 hierarchical process。
- **对 MyGO 的启示**: MyGO 的 `role_strategy: manual` 可参考 CrewAI 的自动角色分配机制

---

### 2.2 代码生成与软件工程 Agent

#### [P06] MASAI: Modular Architecture for Software-engineering AI Agents
- **作者**: Arora et al.
- **会议**: NeurIPS 2024 Workshop / ICSE 2025
- **核心贡献**: 5 个专职 sub-agent（Test Generator → Issue Reproducer → Edit Localizer → Fixer → Ranker），每个使用不同推理策略（ReAct/CoT/单次）。
- **关键发现**: 拆分成 sub-agent 比单一 agent 性能高 **+7.67%**（SWE-bench Lite 28.33%）
- **对 MyGO 的启示**:
  - **直接可用**: 为不同子任务选择不同推理策略
  - **直接可用**: sub-agent 间的上下文隔离已在 MyGO 实现（独立工作区）
  - **可改进**: MyGO 当前对所有子任务使用相同的 builder prompt，应根据子任务类型定制

#### [P07] SWE-agent: Agent-Computer Interfaces Enable Automated Software Engineering
- **作者**: Yang et al.
- **会议**: ICLR 2025
- **核心贡献**: ACI（Agent-Computer Interface）设计——接口比架构更重要。自定义的文件查看/编辑命令显著提升 agent 性能。
- **关键发现**: 好的工具接口 > 复杂的 agent 架构
- **对 MyGO 的启示**:
  - **核心启发**: MyGO 给 IDE agent 的 prompt 质量（"接口"）可能比编排架构更重要
  - **可改进**: 当前 Jinja2 模板较通用，应针对不同 IDE agent 的工具能力定制 prompt

#### [P08] Agentless: Demystifying LLM-based Software Engineering Agents
- **作者**: Xia et al.
- **会议**: FSE 2025
- **核心贡献**: 证明不需要 agent，3 步 LLM 调用（Localize → Edit → Verify）即可达到 27.33% SWE-bench Lite。
- **关键发现**: 过度工程化（复杂 agent 架构）可能适得其反
- **对 MyGO 的启示**:
  - **警示**: 不要为了架构而架构——简单任务不需要 4 节点图
  - **可改进**: 为简单任务提供 "fast path"，跳过分解和审查

#### [P09] SWE-Debate: Multi-Agent Debate for Automated Program Repair
- **作者**: (ICSE 2026)
- **核心贡献**: 多 agent 辩论机制用于自动化程序修复。通过 agent 间的对抗性讨论提升 patch 质量。
- **对 MyGO 的启示**:
  - **可借鉴**: 将 reviewer 从"单次审查"升级为"辩论轮次"
  - **可改进**: builder 和 reviewer 之间可以有多轮交互而非单次

#### [P10] MapCoder: Multi-Agent Code Generation for Competitive Programming
- **作者**: Islam et al.
- **会议**: ACL 2024
- **核心贡献**: 4 agent 协作（Retrieval → Planning → Coding → Debugging），验证阶段独立化。
- **关键发现**: 将验证从编码中分离出来显著提升正确率
- **对 MyGO 的启示**: 当前 review 节点可拆分为"测试验证" + "代码审查"两个独立步骤

#### [P11] OpenHands (OpenDevin): An Open Platform for AI Software Developers
- **作者**: Wang et al.
- **来源**: arXiv 2024
- **核心贡献**: 统一的 AI 软件开发平台，CodeAct 2.1 架构支持 multi-agent delegation。SWE-bench Verified 72%。
- **对 MyGO 的启示**: Event stream 架构可解决 MyGO 的 checkpoint 恢复问题

#### [P12] AgentCoder: Multi-Agent-based Code Generation with Iterative Testing and Optimisation
- **作者**: Huang et al.
- **来源**: arXiv 2023 / ACL 2024
- **核心贡献**: Programmer + Test Designer + Test Executor 三 agent 协作。迭代生成和测试直到通过。
- **对 MyGO 的启示**: 独立的 Test Designer agent 可增强 MyGO 的自动测试能力

---

### 2.3 Agent 推理与规划

#### [P13] Reflexion: Language Agents with Verbal Reinforcement Learning
- **作者**: Shinn et al.
- **会议**: NeurIPS 2023
- **核心贡献**: Agent 通过自然语言自我反思（而非梯度更新）从失败中学习。反思记录保存在 episodic memory 中。
- **关键发现**: 自我反思 + 记忆 → HumanEval 从 80% 提升到 91%
- **对 MyGO 的启示**:
  - **直接可用**: MyGO 重试时注入上一轮 reviewer feedback（已实现）
  - **可改进**: 加入显式的 "self-reflection" 步骤，让 builder 在重试前生成失败分析
  - **可改进**: 反思结论存入语义记忆，跨任务复用

#### [P14] Self-Refine: Iterative Refinement with Self-Feedback
- **作者**: Madaan et al.
- **会议**: NeurIPS 2023
- **核心贡献**: 单 LLM 通过 generate → critique → refine 循环迭代改进输出。无需训练或外部反馈。
- **对 MyGO 的启示**: builder 在提交前可加入自审步骤（self-critique），减少进入 review 后被 reject 的概率

#### [P15] Tree of Thoughts: Deliberate Problem Solving with Large Language Models
- **作者**: Yao et al.
- **会议**: NeurIPS 2024
- **核心贡献**: 将 LLM 推理从线性 chain 扩展为树状搜索，支持回溯和分支评估。
- **对 MyGO 的启示**: 任务分解可使用 ToT 生成多个分解方案，评估后选最优

#### [P16] Graph of Thoughts: Solving Elaborate Problems with Large Language Models
- **作者**: Besta et al.
- **会议**: AAAI 2024
- **核心贡献**: 将思维链从树扩展为任意 DAG，支持思维的合并和循环改进。
- **对 MyGO 的启示**: 子任务间的依赖关系可用 GoT 建模，而非简单拓扑排序

---

### 2.4 Agent 记忆系统

#### [P17] MemGPT: Towards LLMs as Operating Systems
- **作者**: Packer et al.
- **来源**: ICLR 2024
- **核心贡献**: 分层记忆架构（主记忆 + 外部存储），LLM 自主管理记忆的换入换出。
- **关键发现**: 分层记忆使 LLM 突破上下文窗口限制，在长文档 QA 上显著优于基线
- **对 MyGO 的启示**:
  - **直接可用**: MyGO 的语义记忆可改为分层架构
  - Working memory: 当前任务上下文（最近 3 轮对话）
  - Archival memory: 历史任务经验（压缩后的语义记忆）
  - **可改进**: 当前 TF-IDF 检索 → 可升级为向量数据库 + 分层索引

#### [P18] Generative Agents: Interactive Simulacra of Human Behavior
- **作者**: Park et al.
- **会议**: UIST 2023
- **核心贡献**: 25 个 agent 在虚拟小镇中自主生活，使用记忆流（memory stream）+ 反思（reflection）+ 规划（planning）三层架构。
- **关键发现**: 记忆检索（recency × importance × relevance）比简单搜索更有效
- **对 MyGO 的启示**: 语义记忆的检索排序可加入 recency 和 importance 权重

---

### 2.5 成本优化与路由

#### [P19] FrugalGPT: How to Use Large Language Models While Reducing Cost and Improving Performance
- **作者**: Chen et al.
- **来源**: arXiv 2023 (Stanford)
- **核心贡献**: LLM 级联策略——先用便宜模型，不确定时升级到贵模型。可减少 98% 成本同时保持性能。
- **对 MyGO 的启示**:
  - **直接可用**: FinOps 模块可实现模型级联路由
  - 简单任务 → GPT-4o-mini builder
  - 复杂任务 → Claude Opus builder
  - **可改进**: 根据任务复杂度自动选择 agent 后端

#### [P20] RouteLLM: Learning to Route LLMs with Preference Data
- **作者**: Ong et al.
- **来源**: arXiv 2024
- **核心贡献**: 使用偏好数据训练路由模型，动态选择强/弱 LLM。
- **对 MyGO 的启示**: router.py 的 agent 选择可加入基于历史任务表现的学习机制

#### [P21] DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines
- **作者**: Khattab et al.
- **会议**: ICLR 2024
- **核心贡献**: 将 LLM 调用声明式编程化，自动优化 prompt 和参数。"Programming over prompting"。
- **对 MyGO 的启示**: prompt 模板可参数化，根据历史成功率自动调优

---

### 2.6 评测基准

#### [P22] SWE-bench: Can Language Models Resolve Real-world GitHub Issues?
- **作者**: Jimenez et al.
- **会议**: ICLR 2024
- **核心贡献**: 真实 GitHub issue 解决基准，2294 个任务，从 12 个流行 Python 仓库提取。
- **对 MyGO 的启示**: 可用 SWE-bench Lite/Verified 评估 MyGO 的端到端效果

#### [P23] GAIA: A Benchmark for General AI Assistants
- **作者**: Mialon et al.
- **会议**: ICLR 2024
- **核心贡献**: 通用 AI 助手基准，测试多步推理和工具使用能力。
- **对 MyGO 的启示**: 可参考 GAIA 的评估维度设计 MyGO 的任务模板

---

### 2.7 Agent 通信协议

#### [P24] Agent Communication Protocol (ACP) / Agent-to-Agent (A2A) Protocol
- **作者**: Google, IBM, et al.
- **来源**: 行业标准提案 2025
- **核心贡献**: 标准化 agent 间通信协议，类似微服务的 REST API。
- **对 MyGO 的启示**: MyGO 的文件 I/O 协议可演进为标准化 agent 协议

#### [P25] Model Context Protocol (MCP)
- **作者**: Anthropic
- **来源**: 开放标准 2024
- **核心贡献**: 统一的 LLM 工具调用协议，IDE 原生集成。
- **对 MyGO 的启示**: 已实现 `mcp_server.py`，可进一步扩展为完整的 MCP server

---

### 2.8 安全与对齐

#### [P26] AgentHarm: A Benchmark for Measuring Harmfulness of LLM Agents
- **会议**: ICLR 2025
- **核心贡献**: 证明单轮对话的安全对齐不能迁移到多步 agent 场景。随着 agent 能力增强，恶意使用风险增大。
- **对 MyGO 的启示**: MyGO 编排多个自主 agent 执行实际操作（写代码、跑测试），需要设计多步安全护栏

#### [P27] Adversarial Robustness of LLM-Based Multi-Agent Systems
- **来源**: OpenReview 2025
- **核心贡献**: LLM 多 agent 系统对对抗性影响高度敏感（0-100% 误导率）。Leader agent 角色和知识显著影响系统鲁棒性。
- **对 MyGO 的启示**: MyGO 的 decide 节点是 leader agent，需要加强其对抗性鲁棒性

#### [P28] A Multi-Agent LLM Defense Pipeline Against Prompt Injection
- **来源**: arXiv 2025
- **核心贡献**: 分层防御——analyzer + sanitizer + validator 三 agent 检测并阻断 prompt injection。
- **对 MyGO 的启示**: 可在 builder 提交后加入 sanitizer agent 验证输出

#### [P34] CodeAgent: Autonomous Communicative Agents for Code Review
- **作者**: Xunzhu Tang, Kisub Kim et al.
- **会议**: EMNLP 2024
- **核心贡献**: 首个面向实际代码审查的多 agent 系统（author/reviewer/decision-maker + QA-Checker 监督 agent 防止 prompt 漂移）。构建了 3500 条真实代码审查数据集。
- **对 MyGO 的启示**:
  - **直接可用**: QA-Checker 概念——在编排器中检测 agent 是否偏离任务，防止 prompt drift
  - **可改进**: MyGO 的 reviewer 可参考 CodeAgent 的结构化审查模板

#### [P35] HuggingGPT: Solving AI Tasks with ChatGPT and its Friends
- **作者**: Yongliang Shen et al.
- **会议**: NeurIPS 2023
- **核心贡献**: LLM 作为控制器编排专业 AI 模型，四阶段（任务规划 → 模型选择 → 任务执行 → 响应生成）。基于 DAG 的子任务分解 + 依赖追踪。
- **对 MyGO 的启示**: 四阶段编排与 MyGO 的 Plan/Build/Review/Decide 结构同构，验证了 LLM 作为元控制器的可行性

#### [P36] ToolLLM: Facilitating Large Language Models to Master 16000+ Real-world APIs
- **作者**: Yujia Qin et al.
- **会议**: ICLR 2024 (Spotlight)
- **核心贡献**: 通用 LLM 工具使用框架，16464 个真实 API。引入深度优先搜索决策树（DFSDT）进行多步 API 推理。
- **对 MyGO 的启示**: DFSDT 推理方式可增强 Build agent 的多步工具调用能力（git、linter、test runner）

---

### 2.9 动态角色分配与路由

#### [P29] Dynamic Role Assignment for Multi-Agent Debate (Meta-Debate)
- **作者**: Miao Zhang et al.
- **来源**: arXiv 2026
- **核心贡献**: 通过 proposal + peer review 阶段匹配模型专长到辩论角色。超越均匀分配 **+74.8%**，超越随机分配 **+29.7%**。
- **对 MyGO 的启示**: 当前 MyGO 固定分配 agent 角色，动态分配可显著提升性能

#### [P30] DyLAN: Dynamic LLM Agent Network
- **来源**: arXiv 2024
- **核心贡献**: Agent 按任务自适应组队。动态任务路由器根据 agent 置信度和负载分配子任务。
- **对 MyGO 的启示**: 从固定 "Codex x 4" → 动态选择几个什么类型的 agent

#### [P31] MasRouter: Multi-Agent System Routing
- **来源**: 2025
- **核心贡献**: 三层决策——协作模式判定 → 角色分配 → LLM 路由。使用强化学习优化路由。
- **对 MyGO 的启示**: 可替代手动 `--builder`/`--reviewer` 标志选择

---

### 2.10 成本优化（补充）

#### [P32] AgentDiet: Trajectory Reduction for LLM Agent Efficiency
- **来源**: arXiv 2025
- **核心贡献**: 发现 agent 轨迹中 **99% 是输入 token**（累积上下文）。通过移除无用、冗余、过期信息，减少 **39.9-59.7%** 输入 token，降低 **21.1-35.9%** 成本，性能无损。
- **对 MyGO 的启示**:
  - **直接可用**: MyGO 多轮 retry 累积大量上下文，AgentDiet 式裁剪可大幅降低成本
  - **关键**: 对 conversation 列表做"垃圾回收"——移除过期的中间状态

#### [P33] Self-Organized Agents (SoA): Ultra Large-Scale Code Generation
- **作者**: Yoichi Ishibashi et al.
- **来源**: arXiv 2024
- **核心贡献**: 根据问题复杂度自动增减 agent 数量，实现动态可扩展的大规模代码生成。
- **对 MyGO 的启示**: MyGO 的并行 agent 数量可从配置固定 → 自适应动态调整

---

### 2.11 综述论文索引

| 编号 | 论文 | 会议/来源 | 年份 |
|------|------|----------|------|
| S1 | Large Language Model based Multi-Agents: A Survey of Progress and Challenges | IJCAI 2024 | 2024 |
| S2 | A Survey on LLM-based Multi-Agent System: Recent Advances and New Frontiers | arXiv | 2024 |
| S3 | Agentic Large Language Models: A Survey | arXiv | 2025 |
| S4 | LLM-Based Multi-Agent Systems for Software Engineering: Literature Review | ACM TOSEM | 2025 |
| S5 | Understanding the Planning of LLM Agents: A Survey | arXiv | 2024 |
| S6 | Beyond Self-Talk: Communication-Centric Survey of LLM Multi-Agent Systems | arXiv | 2025 |
| S7 | Evaluation and Benchmarking of LLM Agents: A Survey | KDD 2025 | 2025 |
| S8 | Survey on Evaluation of LLM-based Agents | arXiv | 2025 |

---

## 三、框架改进方案与新思考

### 3.1 核心洞察：MyGO 的独特约束

在提出改进前，必须认清 MyGO 与其他框架的**本质差异**：

| 维度 | 典型框架 (MetaGPT/AutoGen) | MyGO |
|------|---------------------------|------|
| Agent 本质 | LLM API 调用（完全可控） | **外部 IDE 进程**（黑箱） |
| 通信方式 | 函数调用/消息传递 | **文件 I/O**（TASK.md → JSON） |
| 延迟 | 秒级 | **分钟级**（人/CLI 操作） |
| 推理策略 | 可为每个 agent 定制 | **不可控**（IDE 内部决定） |
| 并行性 | 轻松并行 | **受限**（IDE 同一时刻只能做一件事） |
| Sub-agent | 可自由嵌套 | **不可能**（IDE AI 不能再调用另一个 IDE） |

> **核心约束**: MyGO 的 "agent" 是不可控的外部黑箱。
> 不能照搬学术框架的架构，必须在黑箱约束下创新。

---

### 3.2 改进方案一：分层记忆架构 (受 MemGPT + Generative Agents 启发)

**问题**: 当前语义记忆是扁平的 JSONL + TF-IDF 检索，无分层、无遗忘、无重要性评分。

**方案**:

```
┌─────────────────────────────────────────────────────┐
│                    记忆架构 v2                        │
├─────────────────────────────────────────────────────┤
│  Layer 1: Working Memory (热记忆)                     │
│  ├── 当前任务上下文 (最近 3 轮对话)                     │
│  ├── 当前子任务依赖图                                  │
│  └── 存储: WorkflowState.conversation[-3:]            │
├─────────────────────────────────────────────────────┤
│  Layer 2: Episodic Memory (情景记忆)                   │
│  ├── 最近 N 个任务的完整经验                            │
│  ├── 包含: 需求 → 分解 → 实现 → 审查 → 结果             │
│  ├── 检索: recency × relevance × importance           │
│  └── 存储: SQLite (结构化)                             │
├─────────────────────────────────────────────────────┤
│  Layer 3: Semantic Memory (知识记忆)                   │
│  ├── 跨任务抽象知识 (最佳实践、常见错误)                  │
│  ├── 自动蒸馏: 从情景记忆中提取模式                      │
│  ├── 检索: 向量相似度 (FAISS/Chroma)                   │
│  └── 存储: 向量数据库                                   │
├─────────────────────────────────────────────────────┤
│  Layer 4: Procedural Memory (程序记忆)                 │
│  ├── 成功的任务模板和分解模式                            │
│  ├── Agent 能力画像 (哪个 agent 擅长什么)                │
│  └── 存储: YAML/JSON 配置                              │
└─────────────────────────────────────────────────────┘
```

**创新点**:
- 记忆蒸馏: 任务完成后自动从 episodic → semantic 提取抽象知识
- 重要性评分: 基于任务结果（approve → 高重要性，reject → 学习案例）
- 遗忘机制: 基于访问频率和时间衰减自动归档

---

### 3.3 改进方案二：自适应工作流拓扑 (受 MASAI + Agentless 启发)

**问题**: 当前所有任务都走固定的 4 节点图（plan → build → review → decide），简单任务浪费资源，复杂任务节点不够。

**方案**: 根据任务复杂度动态选择工作流

```
任务复杂度评估 (LLM + 规则)
    │
    ├── Simple (单文件 bug fix)
    │   └── Localize → Fix → Verify (3 步, 参考 Agentless)
    │       跳过 review, 自动验证测试通过即 approve
    │
    ├── Medium (多文件功能)
    │   └── Plan → Build → Review → Decide (当前 4 节点)
    │
    ├── Complex (跨模块重构)
    │   └── Decompose → [Sub-task × N] → Aggregate → Final Review
    │       参考 MASAI: 每个 sub-task 可用不同推理策略
    │
    └── Critical (安全/核心模块)
        └── Plan → Build → Self-Review → Peer-Review → Debate → Decide
            参考 SWE-Debate: 多轮对抗性审查
```

**创新点**:
- 复杂度评估器: 基于 changed files 数量、涉及模块、历史相似任务难度
- 策略注册表: 可插拔的工作流拓扑，通过配置选择
- 渐进升级: 任务失败后自动升级到更复杂的工作流

---

### 3.4 改进方案三：对抗性多轮审查 (受 SWE-Debate + Reflexion 启发)

**问题**: 当前 reviewer 只做一次审查，容易 rubber-stamp 或漏审。

**方案**: 引入辩论机制

```
Builder 提交代码
    │
    ▼
Reviewer A 审查 ──→ 发现 3 个问题
    │
    ▼
Builder 回应 ──→ 修复 2 个，对 1 个提出反驳
    │
    ▼
Reviewer A 二次审查 ──→ 接受反驳，确认修复
    │
    ▼
(可选) Reviewer B 独立审查 ──→ 发现 Reviewer A 遗漏的问题
    │
    ▼
最终决策
```

**创新点**:
- 结构化辩论: 每轮交互有明确的 claim → evidence → rebuttal 格式
- 跨 reviewer 冗余: 关键任务使用两个独立 reviewer
- 辩论记录: 完整的推理链存入 trace，可供后续学习

---

### 3.5 改进方案四：成本感知智能路由 (受 FrugalGPT + RouteLLM 启发)

**问题**: 当前 router.py 基于手动配置选择 agent，无成本/质量权衡。

**方案**:

```python
class SmartRouter:
    def route(self, task: Task) -> AgentAssignment:
        complexity = self.estimate_complexity(task)
        budget = self.finops.remaining_budget()
        history = self.memory.similar_tasks(task.requirement)

        # 级联策略
        if complexity == "simple" and budget < threshold:
            return AgentAssignment(builder="aider", reviewer="auto-test")
        elif complexity == "medium":
            return AgentAssignment(builder="codex", reviewer="claude")
        else:
            return AgentAssignment(
                builder="claude-opus",
                reviewer="claude-opus",
                strategy="debate"
            )
```

**创新点**:
- 成本-质量帕累托最优: 在预算约束下最大化任务成功率
- 历史学习: 基于类似任务的历史 agent 表现选择
- 动态降级: 预算不足时自动切换到更便宜的 agent

---

### 3.6 改进方案五：事件溯源架构 (受 OpenHands Event Stream 启发)

**问题**: 当前状态管理混乱——LangGraph checkpoint、task YAML、lock file、outbox JSON 多处记录状态，容易不一致。

**方案**: Event Sourcing

```
所有状态变更 → 追加到不可变事件流 (append-only)
    │
    ├── TaskCreated { id, requirement, skill }
    ├── AgentAssigned { builder: "codex", reviewer: "claude" }
    ├── BuildStarted { agent: "codex", prompt_hash: "abc" }
    ├── BuildCompleted { output: {...}, files: [...] }
    ├── ReviewStarted { agent: "claude" }
    ├── ReviewCompleted { decision: "reject", feedback: "..." }
    ├── RetryTriggered { attempt: 2, reflection: "..." }
    ├── TaskApproved { evidence: [...] }
    └── TaskCompleted { duration: 1200s, cost: $0.45 }

事件流 → 可计算出任意时刻的状态快照
事件流 → 可回放（replay）任何历史任务
事件流 → 可分析（analytics）任务模式
```

**创新点**:
- 单一事件源: 替代当前的 checkpoint + YAML + lock 多重状态
- 时间旅行调试: 可回到任何历史节点查看状态
- 审计完整性: 不可变日志，满足合规需求
- 实时投影: SSE Dashboard 直接消费事件流

---

### 3.7 改进方案六：Prompt 工程自动优化 (受 DSPy 启发)

**问题**: Jinja2 prompt 模板手工维护，没有基于效果的自动调优。

**方案**:

```
任务完成后:
    1. 记录 (prompt_template, task_type, agent_id, outcome)
    2. 统计每种 prompt 变体的成功率
    3. 使用 A/B 测试自动选择最优 prompt
    4. 基于失败案例生成 prompt 改进建议

prompt_variants:
  builder_v1: "请实现以下功能：{requirement}"    → 成功率 72%
  builder_v2: "分析需求，列出步骤，然后实现：..."  → 成功率 85%
  builder_v3: "参考以下类似任务的成功经验：..."    → 成功率 91%
```

---

### 3.8 改进方案七：可观测性全栈 (受 OpenTelemetry 启发)

**问题**: trace_id 始终为 0，无分布式追踪，调试靠 grep。

**方案**:

```
┌──────────────────────────────────────┐
│           Observability Stack         │
├──────────────────────────────────────┤
│  Traces: OpenTelemetry spans          │
│  ├── task_span                        │
│  │   ├── plan_span                    │
│  │   ├── build_span                   │
│  │   │   ├── agent_dispatch_span      │
│  │   │   └── agent_wait_span          │
│  │   ├── review_span                  │
│  │   └── decide_span                  │
│  └── subtask_span (parent: task_span) │
├──────────────────────────────────────┤
│  Metrics: Prometheus-style            │
│  ├── task_duration_seconds            │
│  ├── retry_count_total                │
│  ├── agent_utilization_ratio          │
│  ├── cost_per_task_usd                │
│  └── memory_entries_total             │
├──────────────────────────────────────┤
│  Logs: Structured JSON               │
│  ├── correlation_id (= trace_id)      │
│  ├── span_id                          │
│  └── level, message, context          │
└──────────────────────────────────────┘
```

---

## 四、实施路线图

### Phase 1: 基础加固 (v0.20)
**目标**: 修复 P0 缺陷，为后续改进打基础

| 任务 | 优先级 | 复杂度 | 论文参考 |
|------|--------|--------|---------|
| 强类型 WorkflowState (Pydantic) | P0 | Medium | — |
| 修复 driver/resume 竞态条件 | P0 | High | — |
| Checkpoint WAL + fsync | P0 | Medium | OpenHands |
| 对话滑动窗口 | P0 | Low | MemGPT |
| 解耦循环依赖（分层架构） | P0 | High | — |

### Phase 2: 智能增强 (v0.21)
**目标**: 引入学术界验证的机制

| 任务 | 优先级 | 复杂度 | 论文参考 |
|------|--------|--------|---------|
| 自适应工作流拓扑 | P1 | High | MASAI, Agentless |
| 分层记忆架构 | P1 | High | MemGPT, Generative Agents |
| 成本感知路由 | P1 | Medium | FrugalGPT, RouteLLM |
| Builder 自审步骤 | P1 | Low | Self-Refine, Reflexion |
| 多轮审查/辩论 | P1 | Medium | SWE-Debate |

### Phase 3: 平台化 (v0.22)
**目标**: 从工具升级为平台

| 任务 | 优先级 | 复杂度 | 论文参考 |
|------|--------|--------|---------|
| 事件溯源架构 | P1 | Very High | OpenHands |
| OpenTelemetry 集成 | P2 | Medium | — |
| Prompt 自动优化 | P2 | High | DSPy |
| Agent 通信标准化 (A2A/MCP) | P2 | Medium | A2A, MCP |
| SWE-bench 集成评测 | P2 | Medium | SWE-bench |

### Phase 4: 前沿探索 (v0.23+)
**目标**: 探索学术前沿

| 任务 | 方向 | 论文参考 |
|------|------|---------|
| GoT 任务分解 | 用图结构建模子任务关系 | Graph of Thoughts |
| 记忆蒸馏 | episodic → semantic 自动抽象 | Generative Agents |
| Agent 能力学习 | 从历史数据学习 agent 擅长领域 | RouteLLM |
| 跨项目知识迁移 | 将一个项目的经验迁移到另一个 | Memory Export/Import |

---

## 附录 A: 论文快速检索索引

| 关键词 | 相关论文 |
|--------|---------|
| 多 agent 协作 | P01 MetaGPT, P02 ChatDev, P03 AutoGen, P04 CAMEL, P05 CrewAI |
| 代码生成 | P06 MASAI, P07 SWE-agent, P08 Agentless, P10 MapCoder, P12 AgentCoder, P33 SoA, P34 CodeAgent |
| 自我反思 | P13 Reflexion, P14 Self-Refine |
| 记忆系统 | P17 MemGPT, P18 Generative Agents |
| 成本优化 | P19 FrugalGPT, P20 RouteLLM, P32 AgentDiet |
| 推理规划 | P15 ToT, P16 GoT, P21 DSPy |
| 辩论审查 | P09 SWE-Debate, P12 AgentCoder, P29 Meta-Debate |
| 评测基准 | P22 SWE-bench, P23 GAIA |
| 通信协议 | P24 A2A, P25 MCP |
| 动态路由 | P29 Meta-Debate, P30 DyLAN, P31 MasRouter, P20 RouteLLM |
| 安全对齐 | P26 AgentHarm, P27 Adversarial Robustness, P28 Prompt Injection Defense |
| 工具使用 | P35 HuggingGPT, P36 ToolLLM |
| 综述 | S1-S8 (见 2.11 节) |

## 附录 B: 竞品框架对比

| 维度 | MyGO | MetaGPT | AutoGen | CrewAI | ChatDev | OpenHands |
|------|------|---------|---------|--------|---------|-----------|
| Agent 类型 | 外部 IDE/CLI | LLM API | LLM API | LLM API | LLM API | 沙箱 Runtime |
| 通信方式 | 文件 I/O | Pub-Sub | 消息传递 | 函数调用 | Chat Chain | Event Stream |
| 状态管理 | LangGraph | SharedEnv | ConversableAgent | 无 | ChatChain | Event Log |
| 人类参与 | ✅ 原生 | ❌ | ✅ | ❌ | ❌ | ✅ |
| 任务分解 | ✅ 拓扑 | ✅ SOP | ❌ | ✅ Process | ✅ Phase | ❌ |
| 对抗审查 | ✅ Rubber-stamp | ❌ | ❌ | ❌ | ✅ 互审 | ❌ |
| 成本追踪 | ✅ FinOps | ❌ | ❌ | ❌ | ❌ | ❌ |
| 记忆系统 | ✅ 语义 | ❌ | ❌ | ✅ 简单 | ❌ | ❌ |
| 工具协议 | ✅ MCP | ❌ | ✅ 工具注册 | ✅ 工具 | ❌ | ✅ |
| 跨平台 | ✅ | ✅ | ✅ | ✅ | ✅ | Linux only |

**MyGO 的独特优势**:
1. **IDE 黑箱兼容** — 唯一支持外部 IDE 进程作为 agent 的框架
2. **人类原生** — 天然支持人工介入（file 模式），不是事后加的
3. **对抗审查** — Rubber-stamp 检测领先所有竞品
4. **FinOps** — 唯一内置成本追踪的多 agent 框架
5. **语义记忆** — 跨任务知识持久化，多数竞品没有

**MyGO 的差距**:
1. **类型安全** — 状态管理弱于 MetaGPT 的强类型
2. **通信效率** — 文件 I/O 远慢于内存消息传递
3. **可观测性** — 无分布式追踪，弱于 OpenHands 的 Event Stream
4. **工作流灵活性** — 固定 4 节点图，弱于 AutoGen 的灵活拓扑
5. **自动评测** — 无 SWE-bench 集成，无法量化改进效果
