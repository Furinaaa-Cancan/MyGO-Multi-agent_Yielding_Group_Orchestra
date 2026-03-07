# MyGO 系统架构完整链路

> 这份文档从头到尾解释整个系统是怎么运作的，适合想理解项目的人阅读。

---

## 一、30 秒概览

```
你在终端输入:  my go "实现用户登录功能"
                    │
                    ▼
┌─────────────────────────────────────────────┐
│              CLI (cli.py)                   │
│  解析参数 → 加载配置 → 编译状态图 → 启动任务  │
└────────────────────┬────────────────────────┘
                     ▼
┌─────────────────────────────────────────────┐
│         LangGraph 状态机 (graph.py)          │
│                                             │
│   ┌──────┐   ┌──────┐   ┌──────┐   ┌─────┐ │
│   │ Plan │──▶│Build │──▶│Review│──▶│Decide│ │
│   └──────┘   └──┬───┘   └──┬───┘   └──┬──┘ │
│                 │          │          │     │
│            interrupt   interrupt   ┌──┴──┐  │
│            (等IDE)     (等IDE)    │retry?│  │
│                                   └──┬──┘  │
│                                  yes→Plan  │
│                                   no→END   │
└─────────────────────────────────────────────┘
                     ▲
                     │ 文件系统 (inbox/outbox)
                     ▼
┌─────────────────────────────────────────────┐
│            IDE AI (Windsurf/Cursor/Codex)    │
│  读 TASK.md → 执行任务 → 写 outbox/xxx.json  │
└─────────────────────────────────────────────┘
```

**核心思路**: 系统不直接调用 AI API，而是通过**文件系统**与 IDE 中的 AI 通信。
写文件 = 发消息，读文件 = 收回复。LangGraph 负责编排流程。

---

## 二、完整链路 — 一步步拆解

### 第 1 步：用户输入命令

```bash
my go "实现 POST /users endpoint" --builder windsurf --reviewer codex
```

**入口文件**: `src/multi_agent/cli.py` → `go()` 函数 (line 354)

这个函数做了什么：
1. `ensure_workspace()` — 确保 `.multi-agent/` 目录存在
2. `load_project_config()` — 从 `.ma.yaml` 读项目配置
3. `load_agent_names_from_config()` — 加载自定义 agent 名称
4. `register_git_hooks()` — 注册 Git 自动提交钩子
5. `compile_graph()` — **编译 LangGraph 状态图**（关键！）
6. `_ensure_no_active_task()` — 检查没有其他任务在跑
7. `acquire_lock(task_id)` — 加锁，写 `.multi-agent/.lock` 文件
8. `start_task(app, task_id, initial_state)` — 启动图执行

### 第 2 步：编译状态图

**文件**: `src/multi_agent/graph.py` → `build_graph()` (line 911)

```python
g = StateGraph(WorkflowState)

g.add_node("plan",   plan_node)     # 规划节点
g.add_node("build",  build_node)    # 构建节点（等 Builder IDE）
g.add_node("review", review_node)   # 审查节点（等 Reviewer IDE）
g.add_node("decide", decide_node)   # 决策节点

# 连线
START → plan → build → review → decide
                                   │
                              route_decision()
                              ├── "end"   → END (任务完成)
                              └── "retry" → plan (重新来过)
```

这就是个**有向图**，每个节点是个 Python 函数，边定义了流转方向。
LangGraph 帮你管理状态持久化（SQLite checkpoint）和中断恢复。

### 第 3 步：Plan 节点 — 规划

**函数**: `graph.py → plan_node()` (line 227)

做了什么：
1. 加载技能合约 (`skills/code-implement/contract.yaml`) — 定义质量标准
2. 选择 Builder 和 Reviewer:
   - `router.py → resolve_builder()` — 根据配置/能力选 IDE
   - `router.py → resolve_reviewer()` — 选另一个 IDE 做审查
3. 渲染 Builder 的任务 prompt（用 Jinja2 模板）
4. 写入文件:
   - `inbox/builder.md` — Builder 的任务描述
   - `TASK.md` — 给 IDE AI 看的单一入口文件
   - `dashboard.md` — 仪表板看板内容
5. 返回新状态（builder_id, reviewer_id, started_at...）

**状态流转**: Plan 完成 → 自动进入 Build

### 第 4 步：Build 节点 — 等待 IDE 执行

**函数**: `graph.py → build_node()` (line 413)

这是**最关键的一步**：

```python
result = interrupt({
    "role": "builder",
    "agent": builder_id,  # 比如 "windsurf"
})
```

`interrupt()` 让 **整个状态图暂停**，等待外部输入。

此时系统在做什么：
- LangGraph 把当前状态保存到 SQLite checkpoint
- 控制权回到 CLI
- CLI 进入 watch 循环（`_run_watch_loop`）
- 每隔 1 秒检查 `outbox/builder.json` 文件是否存在

此时 IDE AI 在做什么：
- 用户在 Windsurf/Cursor 里说："帮我完成 TASK.md 里的任务"
- IDE AI 读取 `.multi-agent/TASK.md`
- AI 写代码、改文件
- 完成后保存结果到 `.multi-agent/outbox/builder.json`

### 第 5 步：检测到输出 → 自动提交

**文件**: `src/multi_agent/cli_watch.py`

Watch 循环检测到 `outbox/builder.json` 存在：

```
1. 读取 JSON 内容
2. 验证格式 (status, summary, changed_files...)
3. 调用 resume_task(app, task_id, output_data)
4. LangGraph 恢复执行，build_node 从 interrupt() 返回
5. build_node 验证输出 → 生成 Reviewer prompt
6. 写入 inbox/reviewer.md + 更新 TASK.md
7. 进入 Review 节点
```

**或者**手动提交：
```bash
my done  # 从 outbox 自动读取并提交
```

### 第 6 步：Review 节点 — 等待审查

**函数**: `graph.py → review_node()` (line 527)

跟 Build 一样的机制：
1. `interrupt()` 暂停，等 Reviewer IDE
2. Reviewer IDE (比如 Codex) 读 TASK.md
3. 审查代码，写结论到 `outbox/reviewer.json`
4. Watch 检测 → `resume_task()` → 恢复执行

Reviewer 输出的关键字段：
```json
{
  "decision": "approve" | "reject" | "request_changes",
  "feedback": "具体反馈...",
  "evidence": ["测试通过截图", "代码审查结论"]
}
```

### 第 7 步：Decide 节点 — 流转决策

**函数**: `graph.py → decide_node()` (line 600+)

根据 Reviewer 的 decision 决定下一步：

```
decision = "approve"
  → final_status = "approved"
  → route_decision() 返回 "end"
  → 任务完成！🎉

decision = "reject" 且 retry_count < retry_budget
  → retry_count += 1
  → route_decision() 返回 "retry"
  → 回到 Plan 节点（带 feedback 重新来过）

decision = "reject" 且 retry_count >= retry_budget
  → final_status = "failed"
  → route_decision() 返回 "end"
  → 任务失败（重试耗尽）
```

### 第 8 步：任务结束

```
1. release_lock()         — 删除 .lock 文件
2. clear_runtime()        — 清理 inbox/outbox
3. save_task_yaml()       — 保存最终状态到 tasks/task-xxx.yaml
4. Git hooks 触发         — auto_commit + auto_tag（如果配置了）
```

---

## 三、文件系统 — 通信协议

这是整个系统的"通信总线"——不用网络，用文件：

```
.multi-agent/
├── TASK.md              ← IDE AI 读这个文件（唯一入口）
├── .lock                ← 锁文件（内容=当前 task_id）
├── dashboard.md         ← 看板内容（Web Dashboard 读取展示）
│
├── inbox/               ← 系统写、AI读
│   ├── builder.md       ← Builder 的完整任务 prompt
│   └── reviewer.md      ← Reviewer 的完整审查 prompt
│
├── outbox/              ← AI写、系统读
│   ├── builder.json     ← Builder 的输出（代码变更摘要）
│   └── reviewer.json    ← Reviewer 的输出（审查决策）
│
├── tasks/               ← 任务状态持久化
│   └── task-abc123.yaml ← 每个任务的 YAML 状态
│
└── history/             ← 事件追踪
    └── task-abc123.events.jsonl  ← JSONL 格式事件日志
```

### 数据流向

```
系统 → inbox/builder.md → IDE AI 读取 → 执行 → outbox/builder.json → 系统读取
系统 → inbox/reviewer.md → IDE AI 读取 → 审查 → outbox/reviewer.json → 系统读取
```

---

## 四、Agent 驱动模式

**文件**: `src/multi_agent/driver.py`

三种方式让 IDE AI 执行任务：

### 1. File 模式（手动）
```
写 inbox/builder.md → 用户手动告诉 IDE "帮我完成 TASK.md"
```
最简单，但需要人工操作。

### 2. CLI 模式（自动）
```python
codex exec "请完成 .multi-agent/TASK.md 中的任务" --full-auto
```
系统自动调用 Codex CLI，无需人工。`spawn_cli_agent()` 在后台线程运行。

### 3. GUI 模式（macOS）
```python
osascript → 打开 IDE 应用 → 粘贴 prompt → 发送
```
用 AppleScript 自动操控桌面应用。仅 macOS 支持。

**配置在哪**: `agents/agents.yaml`
```yaml
- id: windsurf
  driver: file
  capabilities: [implementation, review]

- id: codex
  driver: cli
  command: "codex exec '{prompt}' --full-auto --skip-git-repo-check"
  capabilities: [implementation, review]
```

---

## 五、状态管理

### LangGraph Checkpoint（核心状态）
- 存储在 SQLite: `.multi-agent/store.db`
- 每个节点执行后自动保存 snapshot
- `interrupt()` 暂停时保存，`resume()` 时恢复
- 支持 crash recovery — 进程挂了重启后能接着跑

### Task YAML（辅助状态）
- `.multi-agent/tasks/task-xxx.yaml`
- 记录 status, skill, builder, reviewer 等元数据
- Web Dashboard 读取这些文件展示任务列表

### Lock 文件（并发控制）
- `.multi-agent/.lock` 写入当前 task_id
- 同一时间只能有一个活跃任务
- Web Dashboard 读取 `.lock` 显示当前活跃任务

---

## 六、Web Dashboard 链路

```
  浏览器                Node.js (app.js)           文件系统
    │                       │                        │
    │── GET /api/status ──▶│── 读 .lock ──────────▶│
    │◀── {active_task} ────│◀── "task-abc123" ─────│
    │                       │                        │
    │── GET /api/tasks ───▶│── 扫描 tasks/*.yaml ─▶│
    │◀── [{task_id,..}] ──│◀── [21个任务] ─────────│
    │                       │                        │
    │── SSE /api/events ──▶│── chokidar 监听 ─────▶│
    │◀── (实时推送) ───────│◀── 文件变化事件 ───────│
```

### SSE 事件推送机制
1. 浏览器建立 SSE 长连接 (`EventSource`)
2. Node.js 用 `chokidar` 监听 `.multi-agent/` 目录
3. 文件变化时推送事件：
   - `dashboard.md` 变了 → 推送 `dashboard_update`
   - `.jsonl` 文件变了 → 推送 `trace_update`
   - `.lock` 文件变了 → 推送 `status_update`
4. 每 15 秒发 `heartbeat` 保活

### CLI 启动流程
```
my dashboard
  → _launch_dashboard_node()
    → 检查 node 是否可用
    → 有: node app.js --port 8765 (传环境变量 MYGO_WORKSPACE_DIR 等)
    → 无: 降级到 Python uvicorn (server.py)
```

---

## 七、Git 集成链路

**文件**: `src/multi_agent/git_ops.py`

通过 EventHooks 系统在关键节点自动触发：

```
Plan 节点开始 → _on_plan_start()  → auto_branch("task/xxx")
Build 节点完成 → _on_build_submit() → auto_commit("build: ...")
Decide 批准时  → _on_decide_approve() → auto_commit() + auto_tag("task/xxx")
```

### EventHooks 机制
```python
# graph_infra.py
class EventHooks:
    def fire_enter(self, node, state): ...  # 节点进入时
    def fire_exit(self, node, state, result): ...  # 节点退出时

# 注册钩子
graph_hooks.on_node_enter("plan", _on_plan_start)
graph_hooks.on_node_exit("build", _on_build_submit)
graph_hooks.on_node_exit("decide", _on_decide_approve)
```

配置来自 `.ma.yaml`:
```yaml
git:
  auto_commit: true
  auto_branch: true
  auto_tag: true
  commit_on: [build, approve]
```

---

## 八、任务分解链路（--decompose）

```bash
my go "实现完整用户认证模块" --decompose
```

**文件**: `src/multi_agent/cli_decompose.py` + `decompose.py`

```
复杂需求
  → Codex AI 分解为子任务 JSON
  → topo_sort_grouped() 拓扑排序
  → 依赖组内并行执行 (ThreadPoolExecutor)
  → 每个子任务走独立的 Plan→Build→Review→Decide 循环
  → 全部完成后汇总
```

子任务隔离在独立目录：
```
.multi-agent/subtasks/
├── subtask-1/
│   ├── TASK.md
│   └── outbox/
├── subtask-2/
│   ├── TASK.md
│   └── outbox/
```

---

## 九、关键模块速查

| 文件 | 职责 |
|------|------|
| `cli.py` | CLI 入口，所有 `my xxx` 命令 |
| `graph.py` | LangGraph 状态图 (Plan→Build→Review→Decide) |
| `orchestrator.py` | 启动/恢复任务的统一接口 |
| `router.py` | 选择 Builder/Reviewer 的路由逻辑 |
| `driver.py` | Agent 驱动（file/cli/gui 三种模式） |
| `session.py` | IDE-first 模式的 Session 管理 |
| `contract.py` | 技能合约加载与验证 |
| `config.py` | 配置管理（路径、.ma.yaml、workmode） |
| `workspace.py` | 文件系统操作（inbox/outbox/lock） |
| `git_ops.py` | Git 自动化（commit/branch/tag） |
| `trace.py` | JSONL 事件追踪 |
| `dashboard.py` | 看板 Markdown 生成 |
| `web/app.js` | Node.js Dashboard 后端 |
| `web/server.py` | Python Dashboard 降级后端 |

---

## 十、一句话总结

**MyGO 是一个通过文件系统协调多个 IDE AI 完成代码任务的编排系统。**

它不调用 AI API，而是：
1. 写任务描述到文件（inbox）
2. 等 IDE AI 写结果到文件（outbox）
3. 用 LangGraph 状态机驱动 Plan→Build→Review→Decide 循环
4. 直到 Reviewer 批准或重试耗尽
