# AgentOrchestra

IDE 无关的多 Agent 编排框架。  
当前推荐模式：**LangGraph 单一状态源（SSOT）+ IDE-first Session 流程**。

---

## 你现在应该怎么用（先看这个）

如果你是 `Windsurf + Antigravity + Codex` 这类组合，建议只用这条链路：

1. `ma session start` 初始化会话
2. `ma session pull` 给当前 agent 生成提示词
3. agent 在 IDE 里完成工作并写 JSON 文件
4. `ma session push` 提交 JSON，自动推进到下一角色

关键点：
- IDE 里只做代码与 JSON 输出，不需要在 IDE 里跑终端命令。
- 角色默认是 `builder -> reviewer`，且两者必须不同 agent。
- 所有共享状态以 LangGraph checkpoint 为准，不再维护第二套状态机。

---

## 1. 安装与初始化

```bash
git clone https://github.com/Furinaaa-Cancan/AgentOrchestra.git
cd AgentOrchestra
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

查看命令：

```bash
ma --help
ma session --help
```

---

## 2. 最小可用流程（MVP）

示例任务文件：`tasks/examples/task-code-implement.json`  
示例 task_id：`task-api-user-create`

### 2.1 启动会话

```bash
ma session start \
  --task tasks/examples/task-code-implement.json \
  --mode strict \
  --config config/workmode.yaml
```

首次启动会输出：
- `state`
- `current_agent`
- `current_role`
- `prompt_paths`

如果你要重新跑同一个 task_id（清理旧 checkpoint）：

```bash
ma session start \
  --task tasks/examples/task-code-implement.json \
  --mode strict \
  --config config/workmode.yaml \
  --reset
```

### 2.2 拉取 builder 提示词

```bash
ma session pull --task-id task-api-user-create --agent windsurf > prompts/current-windsurf.txt
```

然后在 Windsurf 里执行：
- 打开 `prompts/current-windsurf.txt`
- 按提示实现代码
- 输出 envelope JSON 到 `.multi-agent/outbox/builder.json`

### 2.3 提交 builder 结果

```bash
ma session push \
  --task-id task-api-user-create \
  --agent windsurf \
  --file .multi-agent/outbox/builder.json
```

提交成功后，状态通常变成 `VERIFYING`，owner 切到 reviewer。

### 2.4 reviewer 同样流程

```bash
ma session pull --task-id task-api-user-create --agent antigravity > prompts/current-antigravity.txt
ma session push \
  --task-id task-api-user-create \
  --agent antigravity \
  --file .multi-agent/outbox/reviewer.json
```

### 2.5 查看状态与轨迹

```bash
ma session status --task-id task-api-user-create
ma trace --task-id task-api-user-create --format tree
ma trace --task-id task-api-user-create --format mermaid
```

---

## 3. IDE 侧协议（必须遵守）

### 3.1 统一 envelope

```json
{
  "protocol_version": "1.0",
  "task_id": "task-api-user-create",
  "lane_id": "main",
  "agent": "windsurf",
  "role": "builder",
  "state_seen": "RUNNING",
  "result": {},
  "recommended_event": "builder_done",
  "evidence_files": [],
  "memory_candidates": [],
  "created_at": "2026-03-02T18:00:00Z"
}
```

### 3.2 builder 最小 `result`

```json
{
  "status": "completed",
  "summary": "实现摘要",
  "changed_files": ["/abs/path/file.py"],
  "check_results": {
    "lint": "pass",
    "unit_test": "pass",
    "contract_test": "pass",
    "artifact_checksum": "pass"
  },
  "risks": [],
  "handoff_notes": "给 reviewer 的说明"
}
```

### 3.3 reviewer 最小 `result`

```json
{
  "decision": "approve",
  "summary": "评审结论",
  "feedback": "",
  "issues": [],
  "evidence": [],
  "risks": []
}
```

说明：
- reviewer `decision` 仅允许：`approve | reject | request_changes`
- `memory_candidates` 支持放 envelope 顶层；为兼容旧输出，系统也会读取 `result.memory_candidates`

---

## 4. 状态模型（Session 视角）

核心状态投影：
- `ASSIGNED`
- `RUNNING`（builder 执行中）
- `VERIFYING`（reviewer 执行中）
- `DONE | FAILED | ESCALATED | CANCELLED`（终态）

说明：
- `session` 模式会把 graph 的 `final_status=approved` 投影为 `state=DONE`。
- 终态任务再次启动时，推荐用 `--reset`。

---

## 5. 项目结构（与会话相关）

```text
.multi-agent/
├── TASK.md
├── MEMORY.md
├── outbox/
│   ├── builder.json
│   └── reviewer.json
├── tasks/
│   └── <task_id>.yaml
├── history/
│   ├── <task_id>.events.jsonl
│   └── <task_id>.memory.pending.json
└── store.db

prompts/
├── current-windsurf.txt
├── current-antigravity.txt
└── current-codex.txt

runtime/handoffs/<task_id>/
└── *.json
```

---

## 6. 命令速查

### 推荐命令（session 主链路）

| 命令 | 作用 |
|---|---|
| `ma session start --task <task.json> --mode strict` | 初始化会话 |
| `ma session status --task-id <id>` | 查看 owner/状态 |
| `ma session pull --task-id <id> --agent <agent>` | 生成 agent 提示词 |
| `ma session push --task-id <id> --agent <agent> --file <json>` | 提交结果并推进 |
| `ma trace --task-id <id> --format tree\|mermaid` | 查看事件轨迹 |

### 兼容命令（保留但不推荐作为主入口）

| 脚本 | 说明 |
|---|---|
| `scripts/ide_hub.py` | `ma session` 的兼容封装 |
| `scripts/workmode_ctl.py` | 配置校验与兼容输出（非主流转） |
| `scripts/emit_ide_prompt.py` | 纯 IDE 提示词输出 |

说明：`ma go / ma done / ma watch` 仍可用，但建议只作为旧链路兼容入口。

---

## 7. Windsurf / Antigravity 实战模板

下面是你当前场景的固定分工建议：

1. `windsurf` 只做 `builder`
2. `antigravity` 只做 `reviewer`
3. `codex` 负责 orchestrator（会话推进、修复、兜底）

每轮操作不超过 3 步：

1. 打开 prompt（`ma session pull`）
2. 在 IDE 执行并产出 envelope JSON
3. 提交（`ma session push`）

---

## 8. 常见问题（你会遇到的）

### Q1: `task 'xxx' is already active`

原因：同 task_id 已有活跃会话。  
处理：

```bash
ma session start --task <task.json> --mode strict --reset
```

### Q2: `current owner is 'A', not 'B'`

原因：不是当前轮到的 agent 提交。  
处理：

```bash
ma session status --task-id <id>
```

确认 `current_agent` 后由对应 agent 提交。

### Q2.1: `invalid role mapping: builder and reviewer must differ`

原因：角色映射把 builder/reviewer 配成了同一个 agent。  
处理：在 `config/workmode.yaml` 或 `agents/agents.yaml` 里改成不同 agent 后重试。

### Q3: lock 相关错误（`blocked` / `lock not found`）

先用同一个 DB 路径检查：

```bash
python3 scripts/lockctl.py --db runtime/locks.db list
python3 scripts/lockctl.py --db runtime/locks.db doctor
```

再按 owner 释放：

```bash
python3 scripts/lockctl.py --db runtime/locks.db release --task-id <holder> --file-path <same-file>
```

### Q4: reviewer 通过了但 MEMORY 没更新

确认 reviewer 输出里有 `memory_candidates`，并且 `decision=approve`。  
通过后会把 pending 候选提升到 `.multi-agent/MEMORY.md`。

### Q5: strict 模式下 reviewer 写了 `approve` 但任务没完成

如果 reviewer 输出被判定为 rubber-stamp（例如只有 `LGTM`/缺少独立验证证据），
strict 模式会自动降级为 `request_changes`，不会直接 `DONE`。  
处理方式：在 reviewer envelope 的 `result` 里补充具体 `reasoning` 和可核验证据，再提交。

---

## 9. 配置

### 9.1 workmode 角色

`config/workmode.yaml`（默认 strict）：

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

### 9.2 agent 配置

在 `agents/agents.yaml` 定义 agent 能力、driver、默认 builder/reviewer。

约束：
- `builder` 与 `reviewer` 不能是同一 agent。
- `driver: file` 适合 IDE 对话流。
- `driver: cli` 适合全自动 CLI agent。

---

## 10. 测试与验收

```bash
pytest tests/ -v
./scripts/smoke_mvp.sh
./scripts/workmode_demo.sh
```

建议在你每次改协议后至少跑：

```bash
pytest -q tests/test_session_cli.py tests/test_emit_ide_prompt.py tests/test_lockctl.py tests/test_memory.py tests/test_trace.py
```

---

## 11. 设计原则（当前版本）

1. **单入口**：`ma session` 是主入口。
2. **单状态源**：LangGraph checkpoint 是唯一真相。
3. **纯文件协议**：IDE 只读 prompt、写 JSON。
4. **可审计**：handoff、trace、memory 都落盘可追溯。
5. **低耦合**：兼容层可用，但不再承担核心编排逻辑。

---

## 12. License

CC BY-NC-SA 4.0，详见 `LICENSE`。

---

## Short English Note

AgentOrchestra is now documented and optimized for the **IDE-first session flow**:
`ma session start -> pull -> IDE writes envelope JSON -> ma session push`.
LangGraph is the only source of truth, and all artifacts are file-based/auditable.
