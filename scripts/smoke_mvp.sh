#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PY_BIN=".venv/bin/python"
fi

echo "[1/7] Validate skills"
python3 scripts/mvp_ctl.py validate-skill --skill-dir skills/task-decompose
python3 scripts/mvp_ctl.py validate-skill --skill-dir skills/code-implement
python3 scripts/mvp_ctl.py validate-skill --skill-dir skills/test-and-review

echo "[2/7] Validate task"
python3 scripts/mvp_ctl.py validate-task --task tasks/examples/task-code-implement.json

echo "[3/7] Route task"
python3 scripts/mvp_ctl.py route --task tasks/examples/task-code-implement.json --agents agents/profiles.json

echo "[4/7] Verify checks (pass case)"
python3 scripts/mvp_ctl.py verify-checks --task tasks/examples/task-code-implement.json --results tasks/examples/check-results-pass.json

echo "[5/7] Run state machine transitions"
python3 - <<'PY'
import json
from pathlib import Path

src = Path("tasks/examples/task-code-implement.json")
dst = Path("runtime/task-run.json")
data = json.loads(src.read_text(encoding="utf-8"))
data["state"] = "QUEUED"
data["owner"] = "planner"
data["consumer"] = "pending"
data["updated_at"] = data["created_at"]
data.pop("error", None)
dst.write_text(json.dumps(data, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
PY
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state ASSIGNED --actor orchestrator --reason "dispatch"
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state RUNNING --actor codex --reason "worker-start"
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state VERIFYING --actor codex --reason "impl-finished"
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state APPROVED --actor reviewer --reason "checks-pass"
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state MERGED --actor orchestrator --reason "merge"
python3 scripts/mvp_ctl.py transition --task runtime/task-run.json --to-state DONE --actor orchestrator --reason "complete"

echo "[6/7] Validate lock lifecycle"
python3 scripts/lockctl.py acquire --task-id task-api-user-create --file-path scripts/mvp_ctl.py --ttl-sec 30
python3 scripts/lockctl.py list
python3 scripts/lockctl.py renew --task-id task-api-user-create --file-path scripts/mvp_ctl.py --ttl-sec 30
python3 scripts/lockctl.py release --task-id task-api-user-create --file-path scripts/mvp_ctl.py

echo "[7/8] Session-mode smoke (LangGraph SSOT)"
cp tasks/examples/task-code-implement.json runtime/task-session-smoke.json
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session start --task runtime/task-session-smoke.json --mode strict --config config/workmode.yaml --reset >/dev/null
cat > runtime/session-builder.json <<'JSON'
{
  "protocol_version": "1.0",
  "task_id": "task-api-user-create",
  "lane_id": "main",
  "agent": "windsurf",
  "role": "builder",
  "state_seen": "RUNNING",
  "result": {
    "status": "completed",
    "summary": "smoke builder",
    "changed_files": ["/Volumes/Seagate/Multi-Agent/artifacts/task-api-user-create/app/main.py"],
    "check_results": {
      "lint": "pass",
      "unit_test": "pass",
      "contract_test": "pass",
      "artifact_checksum": "pass"
    }
  },
  "recommended_event": "builder_done",
  "evidence_files": [],
  "created_at": "2026-03-02T00:00:00Z"
}
JSON
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session push --task-id task-api-user-create --agent windsurf --file runtime/session-builder.json >/dev/null
cat > runtime/session-reviewer.json <<'JSON'
{
  "protocol_version": "1.0",
  "task_id": "task-api-user-create",
  "lane_id": "main",
  "agent": "antigravity",
  "role": "reviewer",
  "state_seen": "VERIFYING",
  "result": {
    "decision": "approve",
    "summary": "Reviewed implementation scope, changed files, and checks; acceptance criteria are satisfied.",
    "reasoning": "Cross-checked builder summary with expected endpoint behavior and required validation paths.",
    "evidence": [
      "Validated builder check_results and endpoint contract expectations."
    ]
  },
  "recommended_event": "review_pass",
  "evidence_files": [],
  "created_at": "2026-03-02T00:00:00Z"
}
JSON
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session push --task-id task-api-user-create --agent antigravity --file runtime/session-reviewer.json >/dev/null
PYTHONPATH=src "$PY_BIN" -m multi_agent.cli session status --task-id task-api-user-create | rg '"state": "DONE"' >/dev/null

echo "[8/8] Smoke test completed"
