from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def emit_root(tmp_path, monkeypatch):
    (tmp_path / "skills" / "code-implement").mkdir(parents=True)
    (tmp_path / "agents").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "tasks").mkdir(parents=True)
    (tmp_path / "prompts").mkdir(parents=True)

    (tmp_path / "skills" / "code-implement" / "contract.yaml").write_text(
        "id: code-implement\nversion: 1.0.0\ndescription: implement code\nquality_gates: []\n",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "agents.yaml").write_text(
        "\n".join(
            [
                "version: 2",
                "role_strategy: manual",
                "defaults:",
                "  builder: windsurf",
                "  reviewer: antigravity",
                "agents:",
                "  - id: windsurf",
                "    driver: file",
                "    capabilities: [implementation]",
                "  - id: antigravity",
                "    driver: file",
                "    capabilities: [review]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "workmode.yaml").write_text(
        "version: 1\nmodes:\n  strict:\n    roles:\n      orchestrator: codex\n      builder: windsurf\n      reviewer: antigravity\n",
        encoding="utf-8",
    )
    task = {
        "task_id": "task-emit-abc",
        "trace_id": "2a4e8d09-8a7f-49ca-8e56-cf16e8e177ab",
        "skill_id": "code-implement",
        "done_criteria": ["do something"],
        "timeout_sec": 600,
        "retry_budget": 2,
        "input_payload": {"requirement": "do something"},
    }
    task_file = tmp_path / "tasks" / "task.json"
    task_file.write_text(json.dumps(task, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    from multi_agent.config import root_dir
    from multi_agent.graph import reset_graph
    from multi_agent.session import start_session

    root_dir.cache_clear()
    reset_graph()
    start_session(str(task_file), mode="strict", config_path=str(tmp_path / "config" / "workmode.yaml"), reset=True)
    yield {"root": tmp_path, "task_file": task_file}
    reset_graph()
    root_dir.cache_clear()


def test_emit_ide_prompt_has_no_terminal_commands(emit_root):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "emit_ide_prompt.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    res = subprocess.run(
        [sys.executable, str(script), "--task", str(emit_root["task_file"]), "--agent", "windsurf"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    out = res.stdout
    assert "python3" not in out
    assert "ide_hub submit" not in out
    assert "outbox" in out
