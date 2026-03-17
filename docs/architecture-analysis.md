# Multi-Agent 编排架构深度对比分析

> 基于 6 篇论文 + 4 个开源框架的系统性调研

## 一、5 种主流架构模式

### 模式 A: 扁平管道 (Agentless, FSE 2025)

```
Localize → Edit → Verify
```

- **核心思想**: 不需要 agent，3 步 LLM 调用即可
- **SWE-bench Lite**: 27.33%
- **优点**: 极简，无状态，成本低
- **缺点**: 无重试，无错误恢复，无法处理复杂任务
- **适用场景**: 简单 bug fix，单文件修改

### 模式 B: 模块化 Sub-agent (MASAI, NeurIPS 2024)

```
Test Template Generator
        ↓
Issue Reproducer
        ↓
Edit Localizer
        ↓
Fixer (生成多个候选 patch)
        ↓
Ranker (排序 + 验证)
```

- **核心思想**: 5 个专职 sub-agent，各用不同策略 (ReAct/CoT/单次)
- **SWE-bench Lite**: 28.33%
- **优点**:
  - 每个 sub-agent 可独立调优策略
  - 避免单一超长 trajectory 导致上下文膨胀
  - 信息来源分散（README、test、source）各 sub-agent 独立获取
- **缺点**: sub-agent 之间的状态传递需要精心设计
- **关键发现**: 论文消融实验表明，使用差异化推理策略的 5 个 sub-agent 比单一 agent 高 7.67%。
  注意：该提升主要归因于**策略差异化**（ReAct/CoT/单次），而非单纯的任务拆分。
  对于黑箱 IDE agent（无法控制推理策略），此数据不可直接迁移

### 模式 C: 层级编排器 (AgentOrchestra/Skywork, 2025)

```
Central Planner (分解目标)
    ├── Data Analyst Agent
    ├── File Operations Agent
    ├── Web Navigator Agent
    └── Tool Manager Agent (动态创建工具)
```

- **核心思想**: TEA Protocol (Tool-Environment-Agent)，中央规划器分解并委派
- **GAIA benchmark**: 83.39% (SOTA)
- **优点**: 处理复杂多步任务，动态工具创建
- **缺点**: 中央规划器是单点故障，sub-agent 需要直接 API 调用
- **关键**: 每个 sub-agent 有独立的 context/memory，不污染彼此

### 模式 D: 有状态图 (LangGraph — 我们当前方案)

```
plan → build → review → decide
  ↑                         ↓
  └──── retry (条件分支) ←──┘
```

- **核心思想**: 显式状态机，条件路由，checkpoint + interrupt
- **优点**: 确定性控制流，可中断恢复，状态持久化
- **缺点**: 固定拓扑，需预定义所有可能路径
- **适用**: 需要人工介入的审批流程

### 模式 E: 对话式 (AutoGen)

```
Agent A ←→ Agent B ←→ Agent C
     (消息传递)
```

- **核心思想**: agents 通过消息互相对话，灵活拓扑
- **优点**: 灵活，支持群聊/嵌套对话
- **缺点**: 难以控制终止条件，调试困难
- **适用**: 探索性任务，brainstorming

---

## 二、我们的独特约束

**AgentOrchestra 不是典型的 multi-agent 框架**。核心差异：

| 维度 | 典型框架 (CrewAI/AutoGen) | AgentOrchestra |
|------|--------------------------|----------------|
| Agent 本质 | LLM API 调用 | **外部 IDE 进程** (Windsurf/Cursor/Claude) |
| 通信方式 | 函数调用/消息传递 | **文件 I/O** (TASK.md → outbox JSON) |
| 控制粒度 | 完全控制 (prompt/stop/tools) | **黑箱** — 给 prompt，等输出 |
| 延迟 | 秒级 | **分钟级** (人工操作 IDE) |
| 并行性 | 轻松并行 | **受限** — IDE 同一时刻只能做一件事 |
| sub-agent | 可自由嵌套 | **不可能** — IDE AI 不能自己再调用另一个 IDE AI |

> **关键洞察**: 我们的 "agent" 是不可控的外部黑箱。
> 不能像 MASAI 那样为每个 sub-agent 选择不同的推理策略，
> 也不能像 AutoGen 那样让 agents 自由对话。

---

## 三、结论：最佳架构

### ❌ 不适合的模式

| 模式 | 为什么不适合 |
|------|-------------|
| E 对话式 | IDE agents 不能互相对话，只能读文件写文件 |
| C 完整层级 | 无法嵌套 — IDE 里的 AI 不能自己再启动另一个 IDE |
| A 纯管道 | 太简单，没有重试和错误恢复 |

### ✅ 最佳方案：D (有状态图) + B (模块化 sub-task) 混合

```
                    ┌─────────────────┐
                    │  Task Decomposer │  ← 借鉴 MASAI: 把大任务拆小
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Sub-task Queue  │  ← 多个独立的 build-review 循环
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │ Sub-task #1  │ │ Sub-task #2  │ │ Sub-task #3  │
    │ plan→build→  │ │ plan→build→  │ │ plan→build→  │
    │ review→decide│ │ review→decide│ │ review→decide│
    └──────────────┘ └──────────────┘ └──────────────┘
              │              │              │
              └──────────────┼──────────────┘
                             │
                    ┌────────▼────────┐
                    │   Aggregator    │  ← 汇总结果，生成最终报告
                    └─────────────────┘
```

### 为什么这是最佳方案：

1. **保持 LangGraph 状态图** (模式 D) 作为每个 sub-task 的执行引擎
   - ✅ 已经实现且经过充分测试 (109 tests)
   - ✅ interrupt/resume 完美支持 IDE 黑箱模式
   - ✅ checkpoint 实现断点续传

2. **借鉴 MASAI 的模块化** (模式 B) 做任务分解
   - ✅ 大任务拆成多个小的 build-review 循环
   - ✅ 每个 sub-task 有独立 context，不互相污染
   - ✅ MASAI 论证了模块化的优势；但其 +7.67% 来自策略差异化，
     对于黑箱 IDE agent，分解的收益假设需通过我们自己的对照实验验证
   - ✅ Agentless (FSE 2025) 验证了分步处理的有效性 (Localize→Edit→Verify)
   - ✅ MapCoder (ACL 2024) 验证了独立验证阶段 (verification isolation) 的价值

3. **不做的事** (借鉴 Agentless "简单即正确"):
   - ❌ 不嵌套 sub-agent（IDE 做不到）
   - ❌ 不做 agent 间实时对话（延迟太高）
   - ❌ 不用复杂层级（过度工程化）

---

## 四、当前架构 vs 改进后的差距

| 能力 | 当前 | 改进后 |
|------|------|--------|
| 单任务 build-review | ✅ | ✅ |
| 重试 + 反馈 | ✅ | ✅ |
| CLI/IDE 混合驱动 | ✅ | ✅ |
| **任务分解** | ❌ 只能处理单个任务 | ✅ 大任务→多个 sub-task |
| **顺序执行 sub-task** | ❌ | ✅ 按依赖顺序执行 |
| **sub-task 间上下文传递** | ❌ | ✅ 前一个的 output 传给下一个 |
| **最终聚合** | ❌ | ✅ 汇总所有 sub-task 结果 |

---

## 五、效度威胁与已知局限 (Threats to Validity)

> 本节主动列出架构选择和实验设计中的已知偏差，
> 遵循 Wohlin et al. (2012) "Experimentation in Software Engineering" 的威胁分类框架。

### 内部效度 (Internal Validity)

| 威胁 | 状态 | 缓解措施 |
|------|------|----------|
| MASAI +7.67% 的可迁移性 | **已修正** | 明确标注该数据来源于策略差异化，不直接适用于黑箱 IDE agent |
| simulate_architectures.py 预设结论 | **已修正** | 重新定义为说明性示例，不作为定量证据 |
| 缺乏 baseline 对照 | **已补齐** | experiment-protocol.md 设计了 single/multi/decompose 三组对照 |
| LLM 输出随机性 | **已处理** | 每个 (条件, 任务) 重复 3 次，使用非参数统计检验 |

### 外部效度 (External Validity)

| 威胁 | 承认 |
|------|------|
| 单一代码库 | 实验仅在 MyGO 项目上进行，跨项目泛化性待验证 |
| 自设计任务 | 未使用 SWE-bench 等公开 benchmark，存在选择偏差风险 |
| 单一 LLM | 仅使用 Claude CLI，其他 LLM 可能表现不同 |

### 构念效度 (Construct Validity)

| 威胁 | 缓解措施 |
|------|----------|
| return code 不等于代码质量 | 采用多维指标：functional tests + lint + type check |
| review 质量难以量化 | strict mode 的橡皮图章检测提供部分保障 |

---

## 六、参考文献

1. MASAI (NeurIPS 2024 / ICSE 2025): 模块化 sub-agent，5 个专职角色
2. Agentless (FSE 2025): 简单 3 步管道，证明过度工程化适得其反
3. SWE-agent (ICLR 2025): ACI 设计 — 接口比架构更重要
4. OpenHands CodeAct 2.1: multi-agent delegation，72% SWE-bench Verified
5. AgentOrchestra/Skywork (2025): 层级编排 + TEA Protocol
6. HULA (ICSE 2025): 最小摩擦人机交互
7. MapCoder (ACL 2024): 验证阶段独立化
8. CrewAI: 角色驱动，sequential/hierarchical process
9. AutoGen: 对话式 multi-agent
10. LangGraph: 有状态图，条件路由，checkpoint
11. Jimenez et al. "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?" ICLR 2024
12. Wohlin et al. "Experimentation in Software Engineering." Springer, 2012
