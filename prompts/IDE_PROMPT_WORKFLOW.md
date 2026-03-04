# IDE Prompt Workflow（Session 模式）

统一使用 `ma session`，不再手工拼事件命令。

## Step 1: Start

```bash
PYTHONPATH=/Volumes/Seagate/Multi-Agent/src \
python3 -m multi_agent.cli session start \
  --task /Volumes/Seagate/Multi-Agent/tasks/examples/task-code-implement.json \
  --mode strict
```

该命令会：
- 初始化/刷新 session（LangGraph SSOT）
- 自动生成三端提示词：
  - `/Volumes/Seagate/Multi-Agent/prompts/current-windsurf.txt`
  - `/Volumes/Seagate/Multi-Agent/prompts/current-antigravity.txt`
  - `/Volumes/Seagate/Multi-Agent/prompts/current-codex.txt`
- 指出当前 owner（谁该执行）

## Step 2: 给 owner IDE 粘贴提示词

把 `current-<agent>.txt` 复制到对应 IDE（Claude Opus）。

## Step 3: 提交 IDE 返回结果

```bash
PYTHONPATH=/Volumes/Seagate/Multi-Agent/src \
python3 -m multi_agent.cli session push \
  --task-id task-api-user-create \
  --agent windsurf \
  --file /Volumes/Seagate/Multi-Agent/.multi-agent/outbox/builder.json
```

说明：
- `--file` 支持 envelope JSON（推荐）。
- 提交后自动推进状态并刷新提示词。

## Step 4: 查看当前状态（可选）

```bash
PYTHONPATH=/Volumes/Seagate/Multi-Agent/src \
python3 -m multi_agent.cli session status \
  --task-id task-api-user-create
```
