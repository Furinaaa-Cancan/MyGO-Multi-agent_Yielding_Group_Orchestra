from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from multi_agent.cli import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def session_root(tmp_path, monkeypatch):
    (tmp_path / "skills" / "code-implement").mkdir(parents=True)
    (tmp_path / "agents").mkdir(parents=True)
    (tmp_path / "config").mkdir(parents=True)
    (tmp_path / "tasks").mkdir(parents=True)
    (tmp_path / "prompts").mkdir(parents=True)

    (tmp_path / "skills" / "code-implement" / "contract.yaml").write_text(
        "\n".join(
            [
                "id: code-implement",
                "version: 1.0.0",
                "description: implement code",
                "quality_gates:",
                "  - lint",
                "  - unit_test",
            ]
        )
        + "\n",
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
                "    capabilities: [implementation, testing]",
                "  - id: antigravity",
                "    driver: file",
                "    capabilities: [review, security]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    (tmp_path / "config" / "workmode.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "modes:",
                "  strict:",
                "    roles:",
                "      orchestrator: codex",
                "      builder: windsurf",
                "      reviewer: antigravity",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    task = {
        "task_id": "task-session-abc",
        "trace_id": "2a4e8d09-8a7f-49ca-8e56-cf16e8e177ab",
        "skill_id": "code-implement",
        "done_criteria": ["POST /users works"],
        "retry_budget": 2,
        "timeout_sec": 600,
        "input_payload": {"requirement": "Implement POST /users"},
    }
    task_path = tmp_path / "tasks" / "task.json"
    task_path.write_text(json.dumps(task, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setenv("MA_ROOT", str(tmp_path))
    from multi_agent.config import root_dir
    from multi_agent.graph import reset_graph

    root_dir.cache_clear()
    reset_graph()
    yield {"root": tmp_path, "task_file": task_path}
    reset_graph()
    root_dir.cache_clear()


def _write_json(path: Path, obj: dict) -> Path:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_session_cli_flow_done(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])

    good_config = session_root["root"] / "config" / "workmode.yaml"
    res = runner.invoke(
        main,
        ["session", "start", "--task", task_file, "--mode", "strict", "--config", str(good_config)],
    )
    assert res.exit_code == 0
    start_payload = json.loads(res.output)
    assert start_payload["state"] == "RUNNING"
    assert start_payload["current_role"] == "builder"
    assert start_payload["current_agent"] == "windsurf"

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder.json", builder_env)

    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(builder_path)],
    )
    assert res.exit_code == 0
    push_builder_payload = json.loads(res.output)
    assert push_builder_payload["state"] == "VERIFYING"
    assert push_builder_payload["current_role"] == "reviewer"

    reviewer_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "antigravity",
        "role": "reviewer",
        "state_seen": "VERIFYING",
        "result": {
            "decision": "approve",
            "summary": "Verified changed files and acceptance criteria; API behavior and tests are consistent.",
            "reasoning": "Reviewed endpoint contract, duplicate-email handling, and unit/contract test coverage.",
            "evidence": ["Verified test coverage and API contract."],
            "feedback": "",
            "memory_candidates": ["POST /users contract validated in reviewer stage"],
        },
        "recommended_event": "review_pass",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    reviewer_path = _write_json(session_root["root"] / "reviewer.json", reviewer_env)

    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "antigravity", "--file", str(reviewer_path)],
    )
    assert res.exit_code == 0
    push_reviewer_payload = json.loads(res.output)
    assert push_reviewer_payload["state"] == "DONE"
    assert push_reviewer_payload["final_status"] == "approved"

    res = runner.invoke(main, ["session", "status", "--task-id", "task-session-abc"])
    assert res.exit_code == 0
    status_payload = json.loads(res.output)
    assert status_payload["state"] == "DONE"

    memory_path = session_root["root"] / ".multi-agent" / "MEMORY.md"
    assert memory_path.exists()
    memory_text = memory_path.read_text(encoding="utf-8")
    assert "POST /users contract validated in reviewer stage" in memory_text


def test_session_next_reports_actionability_and_ide_message(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    res = runner.invoke(
        main,
        ["session", "next", "--task-id", "task-session-abc", "--agent", "windsurf"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["actionable"] is True
    assert payload["state"] == "RUNNING"
    assert payload["event_hint"] == "builder_done"
    assert payload["outbox_rel_path"] == ".multi-agent/outbox/builder.json"
    assert "@.multi-agent/TASK.md" in payload["ide_message"]

    res = runner.invoke(
        main,
        ["session", "next", "--task-id", "task-session-abc", "--agent", "antigravity"],
    )
    assert res.exit_code == 0
    standby = json.loads(res.output)
    assert standby["actionable"] is False
    assert "current owner is windsurf/builder" in standby["reason"]

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder-next.json", builder_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(builder_path)],
    )
    assert res.exit_code == 0

    res = runner.invoke(
        main,
        ["session", "next", "--task-id", "task-session-abc", "--agent", "antigravity"],
    )
    assert res.exit_code == 0
    reviewer_turn = json.loads(res.output)
    assert reviewer_turn["actionable"] is True
    assert reviewer_turn["state"] == "VERIFYING"
    assert reviewer_turn["event_hint"] == "review_pass|review_fail"
    assert reviewer_turn["outbox_rel_path"] == ".multi-agent/outbox/reviewer.json"


def test_session_pull_json_meta_contains_ide_paths_and_template(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    res = runner.invoke(
        main,
        ["session", "pull", "--task-id", "task-session-abc", "--agent", "windsurf", "--json-meta"],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["actionable"] is True
    assert payload["outbox_rel_path"] == ".multi-agent/outbox/builder.json"
    assert payload["task_md_path"].endswith("/.multi-agent/TASK.md")
    assert isinstance(payload["result_template"], dict)
    assert payload["result_template"]["status"] == "completed|blocked"


def test_session_start_failure_releases_lock_and_marks_failed(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    bad_config = session_root["root"] / "config" / "workmode-bad.yaml"
    bad_config.write_text(
        "\n".join(
            [
                "version: 1",
                "modes:",
                "  strict:",
                "    roles:",
                "      orchestrator: codex",
                "      builder: windsurf",
                "      reviewer: windsurf",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    res = runner.invoke(
        main,
        ["session", "start", "--task", task_file, "--mode", "strict", "--config", str(bad_config), "--reset"],
    )
    assert res.exit_code != 0
    assert "builder and reviewer must differ" in res.output

    from multi_agent.workspace import read_lock

    assert read_lock() is None

    task_yaml = session_root["root"] / ".multi-agent" / "tasks" / "task-session-abc.yaml"
    assert task_yaml.exists()
    task_yaml_text = task_yaml.read_text(encoding="utf-8")
    assert "status: failed" in task_yaml_text

    good_config = session_root["root"] / "config" / "workmode.yaml"
    res = runner.invoke(
        main,
        ["session", "start", "--task", task_file, "--mode", "strict", "--config", str(good_config)],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["state"] == "RUNNING"


def test_session_start_reset_does_not_clear_runtime_when_foreign_lock_exists(runner: CliRunner, session_root):
    from multi_agent.workspace import acquire_lock, read_lock

    task_file = str(session_root["task_file"])
    ws_outbox = session_root["root"] / ".multi-agent" / "outbox"
    ws_outbox.mkdir(parents=True, exist_ok=True)
    sentinel = ws_outbox / "builder.json"
    sentinel.write_text('{"sentinel": true}\n', encoding="utf-8")

    acquire_lock("task-foreign-999")
    assert read_lock() == "task-foreign-999"

    res = runner.invoke(
        main,
        ["session", "start", "--task", task_file, "--mode", "strict", "--reset"],
    )
    assert res.exit_code != 0
    assert "another task is active: task-foreign-999" in res.output
    assert sentinel.exists()
    assert sentinel.read_text(encoding="utf-8").strip() == '{"sentinel": true}'
    assert read_lock() == "task-foreign-999"


def test_session_start_reset_clears_old_trace_and_handoff_artifacts(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    task_id = "task-session-abc"
    history_dir = session_root["root"] / ".multi-agent" / "history"
    history_dir.mkdir(parents=True, exist_ok=True)
    trace_path = history_dir / f"{task_id}.events.jsonl"
    trace_path.write_text('{"event_id":"old","event_type":"stale"}\n', encoding="utf-8")

    handoff_dir = session_root["root"] / "runtime" / "handoffs" / task_id
    handoff_dir.mkdir(parents=True, exist_ok=True)
    stale_handoff = handoff_dir / "old.json"
    stale_handoff.write_text('{"stale": true}\n', encoding="utf-8")

    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict", "--reset"])
    assert res.exit_code == 0

    assert not stale_handoff.exists()
    lines = [line for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["event_type"] == "session_start"


def test_session_push_rejects_missing_required_fields(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    bad_builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed"
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    bad_path = _write_json(session_root["root"] / "bad-builder.json", bad_builder_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(bad_path)],
    )
    assert res.exit_code != 0
    assert "missing 'summary' field" in res.output

    from multi_agent.workspace import read_lock

    assert read_lock() == "task-session-abc"


def test_session_push_rejects_state_seen_mismatch_and_keeps_lock(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    bad_state_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "VERIFYING",
        "result": {
            "status": "completed",
            "summary": "should fail by state mismatch",
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    bad_state_path = _write_json(session_root["root"] / "bad-state.json", bad_state_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(bad_state_path)],
    )
    assert res.exit_code != 0
    assert "payload.state_seen mismatch" in res.output

    from multi_agent.workspace import read_lock

    assert read_lock() == "task-session-abc"


def test_session_push_reviewer_approve_requires_evidence_in_strict(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder-evidence.json", builder_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(builder_path)],
    )
    assert res.exit_code == 0
    assert json.loads(res.output)["state"] == "VERIFYING"

    reviewer_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "antigravity",
        "role": "reviewer",
        "state_seen": "VERIFYING",
        "result": {
            "decision": "approve",
            "summary": "Detailed review completed.",
            "reasoning": "Checked endpoint behavior and unit tests.",
        },
        "recommended_event": "review_pass",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    reviewer_path = _write_json(session_root["root"] / "reviewer-no-evidence.json", reviewer_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "antigravity", "--file", str(reviewer_path)],
    )
    assert res.exit_code != 0
    assert "reviewer approve requires evidence" in res.output

    res = runner.invoke(main, ["session", "status", "--task-id", "task-session-abc"])
    assert res.exit_code == 0
    status_payload = json.loads(res.output)
    assert status_payload["state"] == "VERIFYING"
    assert status_payload["current_agent"] == "antigravity"


def test_session_push_reviewer_approve_evidence_rule_can_be_disabled_by_config(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    cfg = session_root["root"] / "config" / "workmode-no-evidence.yaml"
    cfg.write_text(
        "\n".join(
            [
                "version: 1",
                "modes:",
                "  strict:",
                "    roles:",
                "      orchestrator: codex",
                "      builder: windsurf",
                "      reviewer: antigravity",
                "    review_policy:",
                "      reviewer:",
                "        require_evidence_on_approve: false",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    res = runner.invoke(
        main,
        ["session", "start", "--task", task_file, "--mode", "strict", "--config", str(cfg), "--reset"],
    )
    assert res.exit_code == 0

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder-no-evidence-policy.json", builder_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(builder_path)],
    )
    assert res.exit_code == 0

    reviewer_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "antigravity",
        "role": "reviewer",
        "state_seen": "VERIFYING",
        "result": {
            "decision": "approve",
            "summary": "Reviewed implementation details and checks.",
            "reasoning": "Validated builder summary and done criteria.",
        },
        "recommended_event": "review_pass",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    reviewer_path = _write_json(session_root["root"] / "reviewer-no-evidence-policy.json", reviewer_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "antigravity", "--file", str(reviewer_path)],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["state"] == "DONE"
    assert payload["final_status"] == "approved"


def test_session_push_skips_memory_promote_when_final_not_approved(session_root, monkeypatch):
    import multi_agent.session as session_mod

    reviewer_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "antigravity",
        "role": "reviewer",
        "state_seen": "VERIFYING",
        "result": {
            "decision": "approve",
            "summary": "looks good",
            "memory_candidates": ["do-not-promote-on-failed"],
        },
        "recommended_event": "review_pass",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    reviewer_path = _write_json(session_root["root"] / "reviewer-failed.json", reviewer_env)

    class _FakeApp:
        def get_state(self, cfg):
            return object()

        def invoke(self, *args, **kwargs):
            return None

    monkeypatch.setattr(session_mod, "compile_graph", lambda: _FakeApp())
    monkeypatch.setattr(
        session_mod,
        "_state_from_snapshot",
        lambda snapshot: ("VERIFYING", "reviewer", "antigravity"),
    )
    monkeypatch.setattr(
        session_mod,
        "_build_task_status",
        lambda task_id: {
            "task_id": task_id,
            "state": "FAILED",
            "current_role": None,
            "current_agent": None,
            "roles": {
                "orchestrator": "codex",
                "builder": "windsurf",
                "reviewer": "antigravity",
            },
            "final_status": "failed",
            "retry_count": 0,
            "retry_budget": 2,
            "prompt_paths": {"codex": "", "windsurf": "", "antigravity": ""},
        },
    )
    monkeypatch.setattr(session_mod, "_write_all_prompts", lambda *args, **kwargs: {})
    monkeypatch.setattr(session_mod, "add_pending_candidates", lambda *args, **kwargs: {"added": 1})
    monkeypatch.setattr(session_mod, "save_task_yaml", lambda *args, **kwargs: None)
    monkeypatch.setattr(session_mod, "read_lock", lambda: None)
    monkeypatch.setattr(session_mod, "release_lock", lambda: None)

    promoted = {"called": False}

    def _fake_promote(*args, **kwargs):
        promoted["called"] = True
        return {"applied": 1}

    monkeypatch.setattr(session_mod, "promote_pending_candidates", _fake_promote)

    trace_events: list[dict] = []

    def _fake_trace(**kwargs):
        trace_events.append(kwargs)
        return kwargs

    monkeypatch.setattr(session_mod, "append_trace_event", _fake_trace)

    payload = session_mod.session_push("task-session-abc", "antigravity", str(reviewer_path))
    assert payload["state"] == "FAILED"
    assert payload["final_status"] == "failed"
    assert promoted["called"] is False
    assert any(e.get("event_type") == "memory_skip" for e in trace_events)


def test_session_push_strict_rubber_stamp_approve_returns_to_builder(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder-strict.json", builder_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "windsurf", "--file", str(builder_path)],
    )
    assert res.exit_code == 0
    assert json.loads(res.output)["state"] == "VERIFYING"

    reviewer_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "antigravity",
        "role": "reviewer",
        "state_seen": "VERIFYING",
        "result": {
            "decision": "approve",
            "summary": "LGTM",
            "evidence": ["Reviewed changed files and test report."],
            "feedback": "",
        },
        "recommended_event": "review_pass",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    reviewer_path = _write_json(session_root["root"] / "reviewer-rubber.json", reviewer_env)
    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "antigravity", "--file", str(reviewer_path)],
    )
    assert res.exit_code == 0
    payload = json.loads(res.output)
    assert payload["state"] == "RUNNING"
    assert payload["current_role"] == "builder"
    assert payload["current_agent"] == "windsurf"
    assert payload["final_status"] is None


def test_session_pull_rejects_invalid_agent_id(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    res = runner.invoke(
        main,
        ["session", "pull", "--task-id", "task-session-abc", "--agent", "../evil"],
    )
    assert res.exit_code != 0
    assert "invalid agent_id" in res.output


def test_session_push_rejects_invalid_agent_id(runner: CliRunner, session_root):
    task_file = str(session_root["task_file"])
    res = runner.invoke(main, ["session", "start", "--task", task_file, "--mode", "strict"])
    assert res.exit_code == 0

    builder_env = {
        "protocol_version": "1.0",
        "task_id": "task-session-abc",
        "lane_id": "main",
        "agent": "windsurf",
        "role": "builder",
        "state_seen": "RUNNING",
        "result": {
            "status": "completed",
            "summary": "implemented endpoint",
            "changed_files": ["/tmp/app/main.py"],
            "check_results": {"lint": "pass", "unit_test": "pass"},
        },
        "recommended_event": "builder_done",
        "evidence_files": [],
        "created_at": "2026-03-02T00:00:00Z",
    }
    builder_path = _write_json(session_root["root"] / "builder-invalid-agent.json", builder_env)

    res = runner.invoke(
        main,
        ["session", "push", "--task-id", "task-session-abc", "--agent", "../evil", "--file", str(builder_path)],
    )
    assert res.exit_code != 0
    assert "invalid agent_id" in res.output
