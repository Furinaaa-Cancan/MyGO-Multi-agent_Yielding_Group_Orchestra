# AgentOrchestra

IDE 无关的多 Agent 编排框架 (v0.6.0)。  
基于 **LangGraph 单一状态源（SSOT）** 驱动 4 节点工作流（plan → build → review → decide），  
支持全自动 CLI 和手动 IDE 两种运行模式。

---

## 快速开始

### 安装

```bash
git clone https://github.com/Furinaaa-Cancan/AgentOrchestra.git
cd AgentOrchestra
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 初始化项目

```bash
ma init          # 创建 skills/ + agents/ + .multi-agent/
ma agents        # 检查 agent 健康状态
ma list-skills   # 查看可用技能
```

### 一条命令跑起来

```bash
ma go "实现用户登录功能"
```

系统自动完成：锁定任务 → 生成 prompt → 自动/手动调用 builder → 等待输出 → 交给 reviewer → 决策 → 完成/重试。

---

## 两种运行模式

### 模式 A：自动化流程（推荐）

```bash
# 单任务：一条命令完成 build → review → decide
ma go "实现 REST API 用户注册接口"

# 复杂任务：自动分解为子任务并依次执行
ma go "实现完整用户认证模块" --decompose

# 自定义角色
ma go "修复登录 bug" --builder windsurf --reviewer cursor --mode strict
```

关键命令：

| 命令 | 作用 |
|---|---|
| `ma go "<需求>"` | 启动任务（自动 watch） |
| `ma done` | 提交当前角色的输出 |
| `ma watch` | 恢复中断的自动检测 |
| `ma status` | 查看当前任务状态 |
| `ma cancel` | 取消当前任务 |

### 模式 B：IDE-first Session 流程

适合需要精细控制每一步的场景：

```bash
ma session start --task tasks/examples/task-code-implement.json --mode strict
ma session pull --task-id <id> --agent windsurf     # 生成 builder prompt
# → IDE 中完成工作，输出 JSON 到 .multi-agent/outbox/builder.json
ma session push --task-id <id> --agent windsurf --file .multi-agent/outbox/builder.json
ma session pull --task-id <id> --agent antigravity   # 生成 reviewer prompt
# → reviewer 完成审查
ma session push --task-id <id> --agent antigravity --file .multi-agent/outbox/reviewer.json
```

| 命令 | 作用 |
|---|---|
| `ma session start --task <json> --mode strict` | 初始化会话 |
| `ma session pull --task-id <id> --agent <agent>` | 生成 prompt |
| `ma session push --task-id <id> --agent <agent> --file <json>` | 提交结果 |
| `ma session status --task-id <id>` | 查看状态 |

---

## Agent 配置

在 `agents/agents.yaml` 中注册你的 IDE/工具：

```yaml
version: 2
agents:
  - id: windsurf
    driver: file                    # IDE 手动模式
    capabilities: [planning, implementation, testing, docs]
  - id: claude
    driver: cli                     # CLI 全自动模式
    command: "claude -p '...' --allowedTools Read,Edit,Bash,Write"
    capabilities: [planning, implementation, testing, review, docs]

role_strategy: manual
defaults:
  builder: windsurf
  reviewer: antigravity
```

- **`driver: file`** — 写 TASK.md，用户在 IDE 中手动告诉 AI 执行
- **`driver: cli`** — 自动 spawn CLI 进程完成任务
- builder/reviewer **必须不同 agent**（对抗性审查）
- 支持的 agent：windsurf, cursor, claude, codex, aider, antigravity, kiro

---

## 技能与任务分解

### 内置技能

| 技能 | 说明 |
|---|---|
| `code-implement` | 实现代码功能（默认） |
| `test-and-review` | 测试与代码审查 |
| `task-decompose` | 任务分解 |

### 任务分解

复杂需求可自动分解为子任务，按依赖关系拓扑排序后顺序执行：

```bash
ma go "实现完整认证模块" --decompose
ma go "重构支付系统" --decompose --auto-confirm    # 跳过确认
ma go "..." --decompose-file result.json           # 从文件加载分解结果
```

每个子任务独立经历完整的 build → review → decide 循环，支持中断恢复（checkpoint）。

---

## 工作流架构

```
plan → build → review → decide
  ↑                        │
  └── retry (budget > 0) ──┘
```

**4 个节点**：
- **plan** — 加载技能合约、分配 builder/reviewer
- **build** — 等待 builder 提交代码（interrupt）
- **review** — 等待 reviewer 审查（interrupt）
- **decide** — 根据审查结果 approve / retry / escalate

**安全机制**：
- **Rubber-stamp 检测**：strict 模式下自动拦截浅层审批（无实质 reasoning/evidence）
- **重试预算**：默认 2 次，超出后 escalate
- **request_changes 上限**：连续 3 次 request_changes 后 escalate（防 DDI 衰减）
- **超时保护**：单步超时 + 全局 2h 上限（OWASP LLM10 DoW 防护）
- **取消检测**：每次 interrupt 返回后检查 `ma cancel` 状态

---

## 状态机

```
DRAFT → QUEUED → ASSIGNED → RUNNING → VERIFYING → APPROVED → MERGED → DONE
                     │          │          │
                     ↓          ↓          ↓
                  CANCELLED   FAILED    ESCALATED
                                ↑
                              RETRY
```

终态：`DONE`、`FAILED`、`ESCALATED`、`CANCELLED`

---

## 项目结构

```text
src/multi_agent/           # 核心包（25 个模块）
├── cli.py                 # CLI 入口 (ma go/done/watch/cancel/status)
├── cli_admin.py           # 管理命令 (history/init/doctor/agents/...)
├── cli_decompose.py       # 任务分解执行
├── cli_watch.py           # 自动轮询 + agent 调度
├── cli_queue.py           # 任务队列
├── graph.py               # LangGraph 4 节点图
├── graph_infra.py         # 图基础设施 (hooks/stats/trim)
├── session.py             # IDE-first 会话服务
├── orchestrator.py        # 统一任务生命周期管理
├── schema.py              # Pydantic 数据模型
├── router.py              # Agent 路由 (manual/auto)
├── driver.py              # Agent 驱动 (file/cli)
├── workspace.py           # 工作空间文件管理 (原子写入)
├── config.py              # 路径解析、配置加载
├── contract.py            # 技能合约加载
├── prompt.py              # Jinja2 提示词渲染
├── memory.py              # 长期记忆管理
├── trace.py               # 事件追踪 (JSONL)
├── decompose.py           # 任务分解逻辑
├── meta_graph.py          # 子任务编排 + checkpoint
├── dashboard.py           # 目标看板生成
├── watcher.py             # Outbox 文件轮询器
├── state_machine.py       # 状态转移验证
├── _utils.py              # 共享工具函数
└── templates/             # Jinja2 prompt 模板

agents/agents.yaml         # Agent 注册表
config/workmode.yaml       # 工作模式配置
skills/                    # 技能定义 (contract.yaml)
├── code-implement/
├── task-decompose/
└── test-and-review/

.multi-agent/              # 运行时工作空间
├── TASK.md                # 当前任务描述
├── MEMORY.md              # 长期记忆
├── dashboard.md           # 目标看板
├── inbox/                 # Agent prompt 文件
├── outbox/                # Agent 输出 JSON
├── tasks/                 # 任务状态 YAML
├── history/               # 对话历史 JSON
├── checkpoints/           # 分解任务 checkpoint
└── store.db               # LangGraph checkpoint DB
```

---

## 全部命令速查

### 核心命令

| 命令 | 说明 |
|---|---|
| `ma go "<需求>"` | 启动任务并自动 watch |
| `ma done` | 提交当前角色输出 |
| `ma watch` | 恢复自动检测循环 |
| `ma status` | 查看任务状态 |
| `ma cancel` | 取消当前任务 |

### Session 命令

| 命令 | 说明 |
|---|---|
| `ma session start` | 初始化会话 |
| `ma session pull` | 生成 agent prompt |
| `ma session push` | 提交 agent 输出 |
| `ma session status` | 查看会话状态 |

### 管理与诊断

| 命令 | 说明 |
|---|---|
| `ma init` | 初始化项目 |
| `ma history` | 查看历史任务 |
| `ma trace --task-id <id>` | 事件轨迹 (tree/mermaid) |
| `ma doctor` | 工作空间健康检查 |
| `ma agents` | Agent 状态 |
| `ma list-skills` | 可用技能 |
| `ma render "<需求>"` | 预览 prompt |
| `ma schema` | 导出 JSON Schema |
| `ma export <task_id>` | 导出任务结果 |
| `ma replay <task_id>` | 重放任务历史 |
| `ma cleanup` | 清理旧文件 |
| `ma version` | 版本信息 |

---

## IDE 侧协议

### builder 输出格式

```json
{
  "status": "completed",
  "summary": "实现摘要",
  "changed_files": ["src/auth.py", "tests/test_auth.py"],
  "check_results": { "lint": "pass", "unit_test": "pass" },
  "risks": [],
  "handoff_notes": "给 reviewer 的说明"
}
```

### reviewer 输出格式

```json
{
  "decision": "approve",
  "summary": "评审结论",
  "feedback": "具体反馈",
  "issues": [],
  "evidence": ["pytest 全部通过", "代码覆盖率 95%"]
}
```

- `decision` 仅允许：`approve` | `reject` | `request_changes`
- strict 模式要求 `evidence` 非空，否则触发 rubber-stamp 拦截
- `memory_candidates` 可放顶层或 `result` 内，approve 后自动写入 MEMORY.md

---

## 配置

### workmode.yaml

```yaml
version: 1
modes:
  strict:
    roles:
      orchestrator: codex
      builder: windsurf
      reviewer: antigravity
    review_policy:
      rubber_stamp:
        generic_summary_max_len: 50
        shallow_summary_max_len: 30
        block_on_strict: true
      reviewer:
        require_evidence_on_approve: true
        min_evidence_items: 1
```

### agents.yaml

```yaml
version: 2
agents:
  - id: windsurf
    driver: file
    capabilities: [planning, implementation, testing, docs]
  - id: cursor
    driver: file
    capabilities: [planning, implementation, testing, review, docs]
  - id: claude
    driver: cli
    command: "claude -p '...' --allowedTools Read,Edit,Bash,Write"
    capabilities: [planning, implementation, testing, review, docs, security]

role_strategy: manual
defaults:
  builder: windsurf
  reviewer: antigravity
```

---

## 常见问题

### Q1: `task 'xxx' is already active`

```bash
ma session start --task <task.json> --mode strict --reset
# 或
ma cancel && ma go "..."
```

### Q2: `current owner is 'A', not 'B'`

```bash
ma status   # 确认 current_agent
```

由对应 agent 提交即可。

### Q3: builder/reviewer 是同一个 agent

`agents.yaml` 的 `defaults.builder` 和 `defaults.reviewer` 必须不同。  
也可在命令行指定：`ma go "..." --builder windsurf --reviewer cursor`

### Q4: lock 相关错误

```bash
ma doctor --fix   # 自动修复常见状态不一致
```

### Q5: strict 模式下 approve 被拦截

reviewer 输出被判定为 rubber-stamp（缺少具体 reasoning/evidence）。  
补充 `evidence` 字段后重新提交。

---

## 技术栈

- **Python 3.11+**, hatchling 构建
- **LangGraph** + langgraph-checkpoint-sqlite（状态图 / checkpoint）
- **Pydantic v2**（数据模型验证）
- **Click**（CLI 框架）
- **Jinja2**（prompt 模板渲染）
- **PyYAML**（配置解析）

开发依赖：pytest, ruff, mypy

---

## 测试

```bash
pytest tests/ -q            # 1121 tests, 全通过
python3 -m mypy src/        # 类型检查
python3 -m ruff check src/  # Lint
```

测试覆盖：
- 单元测试：workspace, session, graph, router, schema, driver, memory, trace, decompose, watcher, ...
- 集成测试：approve flow, reject-retry, budget exhausted, timeout, cancel, rubber stamp, request_changes cap
- 回归测试：原子写入, TOCTOU 竞态, 锁泄漏, spawn 竞态

---

## 设计原则

1. **单状态源**：LangGraph checkpoint 是唯一真相
2. **对抗性审查**：builder/reviewer 必须不同 agent，防止自我审批
3. **纯文件协议**：IDE 只读 prompt、写 JSON，无需终端操作
4. **原子写入**：关键文件使用 tempfile + os.replace，防崩溃损坏
5. **并发安全**：文件锁（O_CREAT|O_EXCL）、线程锁、TOCTOU 防护
6. **输入验证**：所有文件路径边界点验证 task_id/agent_id，防路径遍历
7. **可审计**：handoff、trace、memory、conversation 全部落盘
8. **优雅降级**：CLI agent 不可用时降级为手动模式

---

## License

CC BY-NC-SA 4.0，详见 `LICENSE`。

---

## English Summary

**AgentOrchestra** is an IDE-agnostic multi-agent orchestration framework (v0.6.0).
It drives a LangGraph 4-node workflow (plan → build → review → decide) with:
- Two modes: automated (`ma go`) and IDE-first (`ma session`)
- Task decomposition with dependency-aware execution
- CLI agent auto-spawning with graceful degradation
- Rubber-stamp detection, retry budgets, timeout guards
- Atomic file writes, TOCTOU race protection, input validation
- 1121 tests, full mypy/ruff compliance
