# MyGO — Multi-agent Yielding Group Orchestra

**你的 AI 乐队，一条命令开演。**

基于 **LangGraph 单一状态源（SSOT）** 驱动 4 节点工作流。v0.16.0

---

## 推荐架构：1 IDE + N CLI

**当前最佳实践是「一个 IDE 坐镇 + 多个 CLI agent 并行」**，而非多个 IDE 同时协作。

```
┌─────────────────────────────────────────────────┐
│  你 (开发者)                                     │
│  └─ Windsurf / Cursor (IDE)  ← 你在这里工作      │
│       └─ my go "..." --decompose --visible       │
│            ├─ Codex CLI × 4  (并行 builder)      │
│            ├─ Codex CLI × 4  (并行 reviewer)     │
│            └─ 全自动: build → review → decide    │
└─────────────────────────────────────────────────┘
```

### 为什么不推荐多 IDE 协作？

| 问题 | 说明 |
|------|------|
| **文件冲突** | 多个 IDE 同时编辑同一文件会互相覆盖，没有 git merge 级别的冲突解决 |
| **状态竞争** | 多个 IDE 各自维护独立的 AI 上下文，无法共享编辑历史和意图 |
| **Prompt 投递** | `driver: file` 模式需要用户手动告诉每个 IDE 去读 TASK.md，无法自动化 |
| **无法并行** | IDE 模式下一次只能等一个 agent 完成，无法真正并行 |

CLI agent（Codex、Claude CLI、Aider）天然支持自动化和并行，是多 agent 协作的正确选择。

### 乐手阵容

| 乐手 | パート | 驱动 | 角色 |
|------|--------|------|------|
| **Windsurf / Cursor** | 高松燈 · Vo. | `file` (手动) | 你的主 IDE，负责发起任务和最终决策 |
| **Codex CLI** | 千早愛音 · Gt. | `cli` (自动) | ⭐ 推荐，支持并行 builder + reviewer |
| **Claude CLI** | 長崎そよ · Ba. | `cli` (自动) | 全自动 builder/reviewer |
| **Aider** | 要楽奈 · Gt. | `cli` (自动) | 轻量 CLI agent |
| **Antigravity** | 椎名立希花 · Dr. | `file` (手动) | 独立 IDE reviewer（需手动操作） |

### 典型用法

```bash
# ⭐ 推荐：1 个 IDE 发起，4 个 Codex CLI 并行干活
my go "实现贪吃蛇游戏" --decompose --visible --builder codex --reviewer codex

# 也可以：IDE 做 builder，CLI 做 reviewer
my go "实现用户登录" --builder windsurf --reviewer codex

# 也可以：纯 CLI 全自动（不需要 IDE）
my go "修复 bug" --builder claude --reviewer codex
```

### 多 IDE 协作（实验性）

如果你确实想让两个 IDE 协作（如 Windsurf 写代码、Cursor 审查），可以使用 Session 模式手动编排：

```bash
my session start --task task.json --mode strict
my session pull --task-id <task_id> --agent windsurf     # Windsurf 写代码
my session push --task-id <task_id> --agent windsurf --file builder.json
my session pull --task-id <task_id> --agent cursor       # Cursor 审查
my session push --task-id <task_id> --agent cursor --file reviewer.json
```

但这需要你在两个 IDE 之间手动切换，效率远不如 1 IDE + N CLI 的全自动流程。

---

## 快速开始

### 安装

```bash
git clone https://github.com/Furinaaa-Cancan/MyGO-Multi-agent_Yielding_Group_Orchestra.git
cd MyGO-Multi-agent_Yielding_Group_Orchestra
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 初始化项目

```bash
my init          # 创建 skills/ + agents/ + .multi-agent/
my agents        # 检查 agent 健康状态
my list-skills   # 查看可用技能
```

### 一条命令跑起来

```bash
my go "实现用户登录功能"

# 或使用预定义模板
my go --template auth
my go --template crud --var model=User --var endpoint=users
```

系统自动完成：锁定任务 → 生成 prompt → 自动/手动调用 builder → 等待输出 → 交给 reviewer → 决策 → 完成/重试。

---

## 命令速查

### 核心命令（自动化流程）

| 命令 | 作用 |
|---|---|
| `my go "<需求>"` | 启动任务（自动 watch） |
| `my go --template <id>` | 使用预定义模板启动任务 |
| `my go "..." --decompose --visible` | 分解为子任务 + 可视化终端并行执行 |
| `my done` | 提交当前角色的输出 |
| `my watch` | 恢复中断的自动检测 |
| `my status` | 查看当前任务状态 |
| `my cancel` | 取消当前任务 |
| `my dashboard` | 启动 Web 仪表板（实时任务监控） |

### Session 命令（多 IDE 手动编排）

适合需要精细控制每一步、或两个 IDE 协作的场景：

| 命令 | 作用 |
|---|---|
| `my session start --task <json> --mode strict` | 初始化会话 |
| `my session pull --task-id <id> --agent <agent>` | 为指定 IDE 生成 prompt |
| `my session push --task-id <id> --agent <agent> --file <json>` | 提交该 IDE 的结果 |
| `my session status --task-id <id>` | 查看会话状态 |

---

## Agent 配置

在 `agents/agents.yaml` 中注册你的 IDE/工具：

```yaml
version: 2
agents:
  - id: windsurf
    driver: file                    # IDE 手动模式
    capabilities: [planning, implementation, testing, docs]
  - id: codex
    driver: cli                     # CLI 全自动模式
    command: "codex exec '...' --full-auto --skip-git-repo-check"
    capabilities: [planning, implementation, testing, review, docs]
  - id: claude
    driver: cli                     # CLI 全自动模式
    command: "claude -p '...' --allowedTools Read,Edit,Bash,Write"
    capabilities: [planning, implementation, testing, review, docs, security]

role_strategy: manual
defaults:
  builder: windsurf
  reviewer: codex
```

三种驱动模式：

| 驱动 | 工作方式 | 适用 |
|------|---------|------|
| `driver: file` | 写 TASK.md，用户手动告诉 IDE 执行 | Windsurf, Cursor, Kiro |
| `driver: cli` | 自动 spawn 终端命令 | Codex, Claude CLI, Aider |
| `driver: gui`  | macOS AppleScript 自动控制桌面应用 | 桌面 IDE 应用 |

- `my go` 在 CLI/GUI 驱动下 builder/reviewer **可以是同一 agent**（自动使用不同实例）
- `my session` 模式要求 builder/reviewer 必须不同 agent
- GUI 模式需要 macOS + 辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能）
- 支持的 agent：windsurf, cursor, claude, codex, aider, antigravity, kiro

---

## 技能与任务分解

### 内置技能

| 技能 | 说明 |
|---|---|
| `code-implement` | 实现代码功能（默认） |
| `test-and-review` | 测试与代码审查 |
| `ui-design` | UI/UX 设计 |
| `task-decompose` | 任务分解 |

### 任务分解

复杂需求可自动分解为子任务，按依赖关系拓扑排序后执行：

```bash
my go "实现完整认证模块" --decompose
my go "重构支付系统" --decompose --auto-confirm    # 跳过确认
my go "..." --decompose-file result.json           # 从文件加载分解结果
```

每个子任务独立经历完整的 build → review → decide 循环，支持中断恢复（checkpoint）。

### 任务模板 (v0.9.0)

预定义常见任务配置，一条命令启动：

```bash
# 查看可用模板
my template list

# 使用模板启动任务
my go --template auth                              # 用户认证模块
my go --template crud --var model=User              # CRUD API（自定义模型）
my go --template bugfix --var bug_description="..." # Bug 修复
my go --template test --var target_module=auth      # 测试补全
my go --template refactor --var target_module=api   # 代码重构
my go --template api-endpoint --var method=POST --var path=/api/users
```

内置模板：

| 模板 ID | 名称 | 分解模式 | 说明 |
|-----------|------|----------|------|
| `auth` | 用户认证模块 | ✅ | 注册/登录/JWT/中间件 |
| `crud` | CRUD API | ✅ | 完整 RESTful CRUD + 分页 |
| `bugfix` | Bug 修复 | ✖ | 根因分析 + 回归测试 |
| `refactor` | 代码重构 | ✖ | 保持接口兼容的重构 |
| `test` | 测试补全 | ✖ | 补充单元/集成测试 |
| `api-endpoint` | API Endpoint | ✖ | 单个 endpoint 实现 |

模板支持 `${var}` 变量占位符，通过 `--var key=value` 覆盖。自定义模板放在 `task-templates/` 目录即可。

**并行执行 (v0.7.0)**：无依赖的子任务自动并行——系统使用 `topo_sort_grouped()` 将子任务分组，同组任务通过 `ThreadPoolExecutor` 并发执行，每个 CLI agent 在隔离的 `.multi-agent/subtasks/<id>/` 工作区运行，互不干扰。

```
组 1 (可并行): sub-1, sub-2, sub-3   ← 3 个 Codex CLI 同时运行
组 2:          sub-4                  ← 依赖组 1，顺序执行
组 3 (可并行): sub-5, sub-6          ← 2 个并行
```

**可视化终端 `--visible` (v0.7.0)**：每个 CLI agent 在独立 Terminal.app 窗口运行，实时可见输出。终端窗口以 MyGO!!!!! 乐队成员命名（高松燈 / 千早愛音 / 長崎そよ / 要楽奈 / 椎名立希花）。

**终端池复用 (v0.7.1)**：跨组复用终端窗口——组 1 的 4 个终端在组 2 自动复用，不再反复开新窗口。每个 slot 对应一个持久终端，wrapper 脚本循环等待新触发。

```bash
# 3 个 Codex 并行 + 每个开独立终端窗口
my go "实现贪吃蛇" --decompose --visible --builder codex --reviewer codex

# 同一 agent 可同时做 builder 和 reviewer (cli/gui 驱动)
my go "修复 bug" --visible --builder codex --reviewer codex
```

自定义终端名称（`.ma.yaml`）：

```yaml
agent_names: ["Alice", "Bob", "Charlie", "Diana", "Eve"]
```

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
- **取消检测**：每次 interrupt 返回后检查 `my cancel` 状态

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
src/multi_agent/           # 核心包（26 个模块）
├── cli.py                 # CLI 入口 (my go/done/watch/cancel/status/dashboard)
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
├── driver.py              # Agent 驱动 (file/cli/gui)
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
├── git_ops.py             # Git 集成 (auto-commit/branch/tag/test)
├── _utils.py              # 共享工具函数
├── web/                   # Web 仪表板
│   ├── app.js             # Node.js/Express 后端 (REST + SSE + chokidar)
│   ├── package.json       # Node.js 依赖
│   ├── server.py          # Python/FastAPI 降级后端
│   └── static/index.html  # 前端 (TailwindCSS + i18n)
└── templates/             # Jinja2 prompt 模板

task-templates/            # 任务模板 (auth, crud, bugfix, ...)
agents/agents.yaml         # Agent 注册表
config/workmode.yaml       # 工作模式配置
skills/                    # 技能定义 (contract.yaml)
├── code-implement/
├── test-and-review/
├── ui-design/
└── task-decompose/

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

## 管理与诊断

| 命令 | 说明 |
|---|---|
| `my init` | 初始化项目 |
| `my history` | 查看历史任务 |
| `my trace --task-id <id>` | 事件轨迹 (tree/mermaid) |
| `my doctor` | 工作空间健康检查 |
| `my agents` | Agent 状态 |
| `my list-skills` | 可用技能 |
| `my render "<需求>"` | 预览 prompt |
| `my schema` | 导出 JSON Schema |
| `my export <task_id>` | 导出任务结果 |
| `my replay <task_id>` | 重放任务历史 |
| `my cleanup` | 清理旧文件 |
| `my dashboard` | Web 仪表板（实时任务监控 + SSE 事件流） |
| `my version` | 版本信息 |
| `my template list` | 列出可用任务模板 |
| `my template show <id>` | 查看模板详情 |

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
  - id: codex
    driver: cli
    command: "codex exec '...' --full-auto --skip-git-repo-check"
    capabilities: [planning, implementation, testing, review, docs]
  - id: claude
    driver: cli
    command: "claude -p '...' --allowedTools Read,Edit,Bash,Write"
    capabilities: [planning, implementation, testing, review, docs, security]

role_strategy: manual
defaults:
  builder: windsurf
  reviewer: codex
```

---

## Git 集成 (v0.9.0)

自动 commit、branch、tag 和测试运行，通过 EventHooks 触发，零侵入核心逻辑。

### 快速启用

```bash
# 方式 1: CLI 一次性 flag
my go "实现用户注册" --git-commit

# 方式 2: .ma.yaml 持久配置
```

### `.ma.yaml` 配置

```yaml
git:
  auto_commit: true           # builder/reviewer 完成后自动 commit
  auto_branch: true           # 每个 task 创建 feature 分支
  branch_prefix: "task/"      # 分支名前缀 (默认 task/)
  commit_on: [build, approve] # 触发时机
  auto_tag: true              # approve 后打 tag

auto_test:
  enabled: true
  command: "pytest tests/ -q --tb=short"
  inject_evidence: true       # 测试结果注入 reviewer evidence
  fail_action: warn           # warn | block
```

### 工作流程

```
plan_node ──► auto_branch("task/xxx")
build_node ──► auto_commit("build(codex): ...")
review_node ──► run_tests() → evidence 注入
decide_node ──► auto_commit("approved: xxx") + auto_tag("task/xxx")
```

### 安全保障

- 检测 `.git` 存在后才执行
- 不在 detached HEAD 上操作
- 不 force-push
- commit 失败不阻塞主流程（hook 错误静默记录日志）

---

## Web 仪表板 (v0.9.0)

实时任务监控 Dashboard，基于 **Node.js/Express**（主后端） + SSE + TailwindCSS。Python/uvicorn 作为降级备选。

```bash
# 启动仪表板（自动检测 Node.js，没有则降级到 Python/uvicorn）
my dashboard
my dashboard --port 9000
my dashboard --host 0.0.0.0   # ⚠️ 建议配合 --token 使用
my dashboard --token auto     # 🔒 自动生成认证 token
my dashboard --token mysecret # 🔒 指定认证 token
```

### 功能

- **实时状态** — 当前活跃任务、dashboard.md 内容（支持 Markdown 表格渲染）
- **Workflow Pipeline** — Plan → Build → Review → Decide 可视化流水线
- **任务列表** — 历史任务 + 状态筛选（All/Active/Done/Failed）
- **事件流** — SSE 推送 dashboard 变更、trace 更新、状态变化、心跳
- **Trace 查看** — 每个任务的 JSONL 事件时间线
- **中英文切换** — 完整 i18n 支持，localStorage 持久化语言偏好
- **Hero + Footer** — 高端深色主题，带导航、状态横幅、多栏页尾

### API 端点

| 端点 | 说明 |
|------|------|
| `GET /` | 仪表板 UI |
| `GET /api/status` | 当前活跃任务 + 项目信息 |
| `GET /api/tasks` | 任务列表 |
| `GET /api/tasks/{id}` | 任务详情 + trace 事件 |
| `GET /api/tasks/{id}/trace` | Trace 事件 |
| `GET /api/events` | SSE 实时事件流 |

### 依赖

```bash
# Node.js 后端（推荐，首次运行自动 npm install）
node >= 18

# 或 Python 降级后端
pip install 'multi-agent[web]'   # 安装 FastAPI + uvicorn
```

---

## 常见问题

### Q1: `task 'xxx' is already active`

```bash
my session start --task <task.json> --mode strict --reset
# 或
my cancel && my go "..."
```

### Q2: `current owner is 'A', not 'B'`

```bash
my status   # 确认 current_agent
```

由对应 agent 提交即可。

### Q3: builder/reviewer 是同一个 agent

- **CLI/GUI 模式**：builder 和 reviewer 可以是同一 agent（系统自动启动不同实例）  
  `my go "..." --builder codex --reviewer codex`
- **IDE 手动模式**：建议使用不同 agent，避免自审自批  
  `my go "..." --builder windsurf --reviewer cursor`

### Q4: lock 相关错误

```bash
my doctor --fix   # 自动修复常见状态不一致
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
- **Node.js** + **Express**（Web 仪表板主后端）
- **FastAPI** + **uvicorn**（Web 仪表板降级后端，可选依赖 `[web]`）

开发依赖：pytest, ruff, mypy

---

## 测试

```bash
pytest tests/ -q            # 1274 tests, 全通过
python3 -m mypy src/        # 类型检查
python3 -m ruff check src/  # Lint
```

测试覆盖：
- 单元测试：workspace, session, graph, router, schema, driver, memory, trace, decompose, watcher, ...
- 集成测试：approve flow, reject-retry, budget exhausted, timeout, cancel, rubber stamp, request_changes cap
- 回归测试：原子写入, TOCTOU 竞态, 锁泄漏, spawn 竞态

---

## 安全 (v0.9.1)

全代码库企业级安全审计，覆盖 31 个文件，修复 22 个漏洞。详见 [`docs/SECURITY_AUDIT.md`](docs/SECURITY_AUDIT.md)。

### 输入验证

| 输入 | 校验 | 正则 |
|------|------|------|
| task_id | ✅ | `[a-z0-9][a-z0-9-]{2,63}` |
| agent_id | ✅ | `[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}` |
| skill_id | ✅ | `[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}` |
| subtask_id | ✅ | `[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}` |
| branch_prefix | ✅ | `[a-zA-Z0-9][a-zA-Z0-9/_.-]{0,30}` |
| webhook URL | ✅ | scheme ∈ {http, https} |

### 防护措施

- **命令注入**：所有 subprocess 调用 `shell=False`；git 命令使用 `--` 分隔符防 flag 注入
- **路径遍历**：所有 ID 类输入正则校验 + 工作空间符号链接保护
- **YAML 安全**：全面使用 `yaml.safe_load` / `JSON_SCHEMA`，禁止 `!!python/object` 等危险标签
- **DoS 防护**：所有文件读取有大小限制（1MB-10MB），SSE 连接数上限 10
- **SSRF 防护**：webhook URL 仅允许 http/https 协议
- **XSS 防护**：Web Dashboard HTML 转义后再渲染 markdown
- **CORS 保护**：仅允许 localhost 来源的跨域请求
- **原子写入**：checkpoint 等关键文件使用 tempfile + os.replace，防崩溃损坏
- **TOCTOU 防护**：文件操作使用 open 而非 exists()+open 模式
- **AppleScript 注入**：GUI 驱动模式转义双引号、反斜杠、换行符

> ✅ Web Dashboard 支持 **可选 token 认证**（`my dashboard --token auto`）。未启用 token 时仅限可信网络使用。

---

## 设计原则

1. **单状态源**：LangGraph checkpoint 是唯一真相
2. **对抗性审查**：CLI/GUI 模式下同 agent 可同时做 builder+reviewer（不同实例）；IDE 模式建议不同 agent
3. **纯文件协议**：IDE 只读 prompt、写 JSON，无需终端操作
4. **原子写入**：关键文件使用 tempfile + os.replace，防崩溃损坏
5. **并发安全**：文件锁（O_CREAT|O_EXCL）、线程锁、TOCTOU 防护
6. **输入验证**：所有文件路径边界点验证 task_id/agent_id，防路径遍历
7. **可审计**：handoff、trace、memory、conversation 全部落盘
8. **优雅降级**：CLI/GUI agent 不可用时降级为手动模式

---

## 平台兼容性

| 功能 | macOS | Windows | Linux |
|------|-------|---------|-------|
| file 模式（纯手动 IDE 协作） | ✅ | ✅ | ✅ |
| CLI 自动模式（`--builder codex`） | ✅ | ⚠️ 需 WSL/Git Bash | ✅ |
| `--visible`（终端窗口可视化） | ✅ | ✅ wt.exe/cmd | ✅ gnome-terminal等 |
| GUI 驱动（AppleScript 自动化） | ✅ | ❌ 不适用 | ❌ 不适用 |
| 图引擎 / 状态管理 / 并行分解 | ✅ | ✅ | ✅ |

`--visible` 模式已支持跨平台终端窗口：
- **macOS**：Terminal.app via AppleScript
- **Windows**：Windows Terminal (`wt.exe`) 或 `cmd.exe`（需 Git Bash / WSL）
- **Linux**：gnome-terminal / konsole / xfce4-terminal / xterm（自动检测）
- 无可用终端时自动降级到后台 CLI 模式

---

## License

AGPL-3.0，详见 `LICENSE`。

---

## English Summary

**MyGO — Multi-agent Yielding Group Orchestra** is your AI band for code delivery (v0.16.0).
Recommended: **1 IDE + N CLI agents** — one IDE orchestrates, multiple Codex/Claude CLI agents work in parallel.
- Three driver modes: manual (file), auto CLI, and GUI automation (macOS AppleScript)
- **Parallel execution**: independent sub-tasks run concurrently via ThreadPoolExecutor
- **Slot-based terminal pool**: persistent Terminal.app windows reused across groups
- **Evidence auto-fill**: reviewer summaries auto-populate as evidence for streamlined CLI reviews
- Two workflow modes: automated (`my go`) and IDE-first (`my session`)
- Task decomposition with dependency-aware topological execution
- Rubber-stamp detection, retry budgets, timeout guards
- Atomic file writes, TOCTOU race protection, input validation
- **Platform**: macOS / Windows / Linux — `--visible` mode cross-platform (Terminal.app / wt.exe / gnome-terminal)
- **Task templates**: 6 built-in templates (auth, crud, bugfix, refactor, test, api-endpoint) with `${var}` substitution
- **Git integration**: auto-commit, auto-branch, auto-tag via EventHooks; auto-test runner with evidence injection
- **Web Dashboard**: real-time task monitoring via Node.js/Express + SSE, TailwindCSS frontend, Chinese/English i18n, optional token auth
- **FinOps**: token usage tracking, cost estimation, budget alerts (`my finops`), Dashboard visualization
- **Semantic Memory**: cross-task knowledge persistence with TF-IDF retrieval (`my memory search|add|list`)
- **Dashboard Control**: approve/reject/cancel tasks from web UI (bidirectional)
- **Smart Retry**: injects relevant semantic memory (past bugfixes, conventions) into retry prompts
- **MCP Write Tools**: submit_review, memory_search/store/list, finops_summary — full IDE-native control
- **Webhook Notifications**: Slack/Discord native formatting, auto-detect, exponential backoff retry
- **Enhanced Doctor**: `my doctor` — 5-check system (workspace, config, agents, memory, webhook)
- **OpenAI Embeddings**: optional upgrade for semantic memory search (auto-fallback to TF-IDF)
- **Batch Mode**: `my batch tasks.yaml` — run multiple tasks from YAML manifest with dry-run support
- **Memory Export/Import**: `my memory export/import` — share team knowledge across projects
- **Config Profiles**: `my go --profile fast` — named presets for common task configs
- **Memory Auto-Prune**: `my memory prune` — TTL expiry + entry cap for semantic memory
- **Daemon Mode**: `my serve` — long-running background task processor with graceful shutdown
- **Task Queue**: `my submit/jobs` — priority-based task queue (high/normal/low), FIFO within priority
- 1327 tests, full mypy/ruff compliance
