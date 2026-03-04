#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TASK="tasks/examples/task-code-implement.json"
CONFIG="config/workmode.yaml"
PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi

echo "[1/7] Validate config"
"$PY_BIN" scripts/workmode_ctl.py validate-config --config "$CONFIG"

echo "[2/7] Reset runtime demo task"
mkdir -p runtime
cp "$TASK" runtime/task-workmode.json

echo "[3/7] Start session (LangGraph SSOT)"
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session start --task runtime/task-workmode.json --mode strict --config "$CONFIG" --reset

echo "[4/7] Pull builder prompt"
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session pull --task-id task-api-user-create --agent windsurf --json-meta

echo "[5/7] Push builder envelope"
cat > runtime/demo-builder.json <<'JSON'
{
  "protocol_version": "1.0",
  "task_id": "task-api-user-create",
  "lane_id": "main",
  "agent": "windsurf",
  "role": "builder",
  "state_seen": "RUNNING",
  "result": {
    "status": "completed",
    "summary": "demo builder output",
    "changed_files": ["/Volumes/Seagate/Multi-Agent/artifacts/task-api-user-create/app/main.py"],
    "check_results": {
      "lint": "pass",
      "unit_test": "pass",
      "contract_test": "pass",
      "artifact_checksum": "pass"
    },
    "handoff_notes": "demo handoff"
  },
  "recommended_event": "builder_done",
  "evidence_files": [],
  "created_at": "2026-03-02T00:00:00Z"
}
JSON
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session push --task-id task-api-user-create --agent windsurf --file runtime/demo-builder.json

echo "[6/7] Push reviewer envelope"
cat > runtime/demo-reviewer.json <<'JSON'
{
  "protocol_version": "1.0",
  "task_id": "task-api-user-create",
  "lane_id": "main",
  "agent": "antigravity",
  "role": "reviewer",
  "state_seen": "VERIFYING",
  "result": {
    "decision": "approve",
    "summary": "Reviewed implementation details and validation outcomes; behavior matches done_criteria.",
    "reasoning": "Checked builder evidence, expected API response contract, and failure-path handling.",
    "evidence": [
      "Reviewed builder changed_files and pass/fail check matrix."
    ],
    "feedback": ""
  },
  "recommended_event": "review_pass",
  "evidence_files": [],
  "created_at": "2026-03-02T00:00:00Z"
}
JSON
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session push --task-id task-api-user-create --agent antigravity --file runtime/demo-reviewer.json

echo "[7/7] Final status"
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session status --task-id task-api-user-create
