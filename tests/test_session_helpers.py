"""Unit tests for session.py helper functions — _task_requirement, _parse_json_payload,
_load_mode_cfg, _find_project_root_from_path, _clear_task_checkpoint, _save_handoff,
_mark_task_status, normalize_file_path_for_lock, session_trace."""

from __future__ import annotations

import sqlite3
from datetime import UTC
from unittest.mock import patch

import pytest
import yaml

# ── _task_requirement ────────────────────────────────────


class TestTaskRequirement:
    def test_from_input_payload_requirement(self):
        from multi_agent.session import _task_requirement
        task = {"input_payload": {"requirement": "  Build a REST API  "}}
        assert _task_requirement(task) == "Build a REST API"

    def test_from_endpoint_and_framework(self):
        from multi_agent.session import _task_requirement
        task = {"input_payload": {"endpoint": "/users", "framework": "FastAPI"}}
        assert _task_requirement(task) == "Implement /users with FastAPI"

    def test_from_done_criteria(self):
        from multi_agent.session import _task_requirement
        task = {"done_criteria": ["All tests pass", "Coverage > 80%"]}
        assert _task_requirement(task) == "All tests pass"

    def test_fallback_to_task_id(self):
        from multi_agent.session import _task_requirement
        task = {"task_id": "task-xyz"}
        assert "task-xyz" in _task_requirement(task)

    def test_empty_requirement_falls_through(self):
        from multi_agent.session import _task_requirement
        task = {"input_payload": {"requirement": "  "}}
        assert "unknown" in _task_requirement(task).lower() or "task" in _task_requirement(task).lower()

    def test_non_dict_input_payload(self):
        from multi_agent.session import _task_requirement
        task = {"input_payload": "not a dict"}
        assert "task" in _task_requirement(task).lower()

    def test_empty_done_criteria(self):
        from multi_agent.session import _task_requirement
        task = {"done_criteria": []}
        assert "unknown" in _task_requirement(task).lower() or "task" in _task_requirement(task).lower()

    def test_non_string_done_criteria(self):
        from multi_agent.session import _task_requirement
        task = {"done_criteria": [123, None]}
        assert "task" in _task_requirement(task).lower()


# ── _parse_json_payload ──────────────────────────────────


class TestParseJsonPayload:
    def test_direct_json(self):
        from multi_agent.session import _parse_json_payload
        result = _parse_json_payload('{"status": "done"}')
        assert result["status"] == "done"

    def test_fenced_json(self):
        from multi_agent.session import _parse_json_payload
        raw = 'Some text\n```json\n{"decision": "approve"}\n```\nMore text'
        result = _parse_json_payload(raw)
        assert result["decision"] == "approve"

    def test_fenced_without_lang(self):
        from multi_agent.session import _parse_json_payload
        raw = 'Prefix\n```\n{"key": "val"}\n```'
        result = _parse_json_payload(raw)
        assert result["key"] == "val"

    def test_empty_raises(self):
        from multi_agent.session import _parse_json_payload
        with pytest.raises(ValueError, match="empty"):
            _parse_json_payload("")

    def test_no_json_raises(self):
        from multi_agent.session import _parse_json_payload
        with pytest.raises(ValueError, match="parse"):
            _parse_json_payload("just plain text with no json")

    def test_array_not_accepted(self):
        from multi_agent.session import _parse_json_payload
        with pytest.raises(ValueError):
            _parse_json_payload("[1, 2, 3]")

    def test_multiple_fences_uses_last(self):
        from multi_agent.session import _parse_json_payload
        raw = '```json\n{"a": 1}\n```\n```json\n{"b": 2}\n```'
        result = _parse_json_payload(raw)
        # reversed() means last fence is tried first
        assert result["b"] == 2


# ── _load_mode_cfg ───────────────────────────────────────


class TestLoadModeCfg:
    def test_no_config_path(self):
        from multi_agent.session import _load_mode_cfg
        assert _load_mode_cfg("strict", None) == {}

    def test_missing_file(self, tmp_path):
        from multi_agent.session import _load_mode_cfg
        assert _load_mode_cfg("strict", str(tmp_path / "missing.yaml")) == {}

    def test_valid_mode(self, tmp_path):
        from multi_agent.session import _load_mode_cfg
        f = tmp_path / "wm.yaml"
        f.write_text(yaml.dump({"modes": {"strict": {"timeout": 300}}}), encoding="utf-8")
        result = _load_mode_cfg("strict", str(f))
        assert result["timeout"] == 300

    def test_non_dict_cfg(self, tmp_path):
        from multi_agent.session import _load_mode_cfg
        f = tmp_path / "wm.yaml"
        f.write_text("- a list\n", encoding="utf-8")
        assert _load_mode_cfg("strict", str(f)) == {}

    def test_non_dict_modes(self, tmp_path):
        from multi_agent.session import _load_mode_cfg
        f = tmp_path / "wm.yaml"
        f.write_text(yaml.dump({"modes": "not a dict"}), encoding="utf-8")
        assert _load_mode_cfg("strict", str(f)) == {}

    def test_non_dict_mode_value(self, tmp_path):
        from multi_agent.session import _load_mode_cfg
        f = tmp_path / "wm.yaml"
        f.write_text(yaml.dump({"modes": {"strict": "not_dict"}}), encoding="utf-8")
        assert _load_mode_cfg("strict", str(f)) == {}

    def test_relative_path(self, tmp_path, monkeypatch):
        from multi_agent.session import _load_mode_cfg
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        cfg = tmp_path / "config" / "wm.yaml"
        cfg.parent.mkdir()
        cfg.write_text(yaml.dump({"modes": {"strict": {"x": 1}}}), encoding="utf-8")
        monkeypatch.setenv("MA_ROOT", str(tmp_path))
        from multi_agent.config import root_dir
        root_dir.cache_clear()
        try:
            result = _load_mode_cfg("strict", "config/wm.yaml")
            assert result == {"x": 1}
        finally:
            monkeypatch.delenv("MA_ROOT", raising=False)
            root_dir.cache_clear()


# ── _clear_task_checkpoint ───────────────────────────────


class TestClearTaskCheckpoint:
    def test_clears_rows(self, tmp_path):
        from multi_agent.session import _clear_task_checkpoint
        db = tmp_path / "store.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE writes (thread_id TEXT)")
        conn.execute("CREATE TABLE checkpoints (thread_id TEXT)")
        conn.execute("INSERT INTO writes VALUES ('t-1')")
        conn.execute("INSERT INTO checkpoints VALUES ('t-1')")
        conn.commit()
        conn.close()

        with patch("multi_agent.session.store_db_path", return_value=db):
            _clear_task_checkpoint("t-1")

        conn = sqlite3.connect(db)
        assert conn.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 0
        conn.close()

    def test_missing_db_noop(self, tmp_path):
        from multi_agent.session import _clear_task_checkpoint
        with patch("multi_agent.session.store_db_path", return_value=tmp_path / "nope.db"):
            _clear_task_checkpoint("t-1")  # should not raise


# ── _find_project_root_from_path ─────────────────────────


class TestFindProjectRootFromPath:
    def test_finds_root(self, tmp_path):
        from multi_agent.session import _find_project_root_from_path
        (tmp_path / "skills").mkdir()
        (tmp_path / "agents").mkdir()
        sub = tmp_path / "sub" / "dir"
        sub.mkdir(parents=True)
        assert _find_project_root_from_path(sub) == tmp_path.resolve()

    def test_no_root_found(self, tmp_path):
        from multi_agent.session import _find_project_root_from_path
        assert _find_project_root_from_path(tmp_path) is None


# ── normalize_file_path_for_lock ─────────────────────────


class TestNormalizeFilePathForLock:
    def test_absolute_path(self):
        from multi_agent.session import normalize_file_path_for_lock
        result = normalize_file_path_for_lock("/tmp/test.yaml")
        assert result.startswith("/")

    def test_relative_path(self, tmp_path):
        from multi_agent.session import normalize_file_path_for_lock
        result = normalize_file_path_for_lock("tasks/t.yaml", cwd=str(tmp_path))
        assert str(tmp_path) in result

    def test_tilde_expanded(self):
        from multi_agent.session import normalize_file_path_for_lock
        result = normalize_file_path_for_lock("~/test.yaml")
        assert "~" not in result


# ── session_trace ────────────────────────────────────────


class TestSessionTrace:
    @patch("multi_agent.session.render_trace", return_value="trace output")
    def test_returns_rendered(self, mock_render):
        from multi_agent.session import session_trace
        result = session_trace("task-001", "tree")
        assert result == "trace output"
        mock_render.assert_called_once_with("task-001", "tree")

    def test_invalid_task_id_raises(self):
        from multi_agent.session import session_trace
        with pytest.raises(ValueError):
            session_trace("../bad", "tree")


# ── _load_json (line 85) ────────────────────────────────


class TestLoadJson:
    def test_non_dict_raises(self, tmp_path):
        from multi_agent.session import _load_json
        f = tmp_path / "list.json"
        f.write_text("[1, 2, 3]")
        with pytest.raises(ValueError, match="invalid JSON"):
            _load_json(f)


# ── activate_project_root_for_task_file (line 147) ──────


class TestActivateProjectRoot:
    def test_returns_none_when_no_root(self, tmp_path):
        from multi_agent.session import activate_project_root_for_task_file
        f = tmp_path / "task.yaml"
        f.write_text("{}")
        with patch("multi_agent.session._find_project_root_from_path", return_value=None):
            result = activate_project_root_for_task_file(str(f))
        assert result is None


# ── _resolve_roles fallbacks (lines 249-260) ────────────


class TestResolveRolesFallbacks:
    def test_empty_roles_get_defaults(self):
        from multi_agent.session import _resolve_roles
        with patch("multi_agent.session._load_mode_cfg", return_value={}), \
             patch("multi_agent.session.get_defaults", return_value={}):
            roles = _resolve_roles("normal", None)
        assert roles.builder == "builder"
        assert roles.reviewer == "reviewer"
        assert roles.orchestrator == "codex"

    def test_mode_cfg_provides_roles(self):
        from multi_agent.session import _resolve_roles
        cfg = {"roles": {"builder": "ws", "reviewer": "ag", "orchestrator": "codex"}}
        with patch("multi_agent.session._load_mode_cfg", return_value=cfg), \
             patch("multi_agent.session.get_defaults", return_value={}):
            roles = _resolve_roles("strict", None)
        assert roles.builder == "ws"
        assert roles.reviewer == "ag"


# ── _state_from_snapshot (line 294) ─────────────────────


class TestStateFromSnapshot:
    def test_none_snapshot(self):
        from multi_agent.session import _state_from_snapshot
        state, role, agent = _state_from_snapshot(None)
        assert state == "UNKNOWN"
        assert role is None
        assert agent is None


# ── _role_for_agent (line 332) ──────────────────────────


class TestRoleForAgent:
    def test_observer_fallback(self):
        from multi_agent.session import SessionRoles, _role_for_agent
        roles = SessionRoles(orchestrator="codex", builder="ws", reviewer="ag")
        assert _role_for_agent(roles, "unknown-agent") == "observer"


# ── _parse_json_payload fenced JSON (lines 509-510) ─────


class TestParseJsonPayloadFenced:
    def test_fenced_json_fallback(self):
        from multi_agent.session import _parse_json_payload
        text = 'Some text\n```json\n{"status": "done"}\n```\nmore text'
        result = _parse_json_payload(text)
        assert result["status"] == "done"

    def test_fenced_json_bad_block_skipped(self):
        from multi_agent.session import _parse_json_payload
        text = '```json\n{bad json}\n```\n```json\n{"ok": true}\n```'
        result = _parse_json_payload(text)
        assert result["ok"] is True


# ── _normalize_reviewer_decision aliases (lines 525, 527) ──


class TestNormalizeReviewerDecision:
    def test_pass_becomes_approve(self):
        from multi_agent.session import _normalize_reviewer_decision
        result = {"decision": "pass"}
        _normalize_reviewer_decision(result, {}, "normal", None)
        assert result["decision"] == "approve"

    def test_fail_becomes_reject(self):
        from multi_agent.session import _normalize_reviewer_decision
        result = {"decision": "fail"}
        _normalize_reviewer_decision(result, {}, "normal", None)
        assert result["decision"] == "reject"


# ── _normalize_envelope mismatches (lines 584, 586, 588, 595) ──


class TestNormalizeEnvelopeMismatches:
    def test_task_id_mismatch(self):
        from multi_agent.session import _normalize_envelope
        raw = {"protocol_version": "1.0", "task_id": "wrong", "result": {"status": "done"}}
        with pytest.raises(ValueError, match="task_id mismatch"):
            _normalize_envelope(raw, task_id="task-1", agent="ws",
                                current_role="builder", current_state="RUNNING",
                                workflow_mode="normal")

    def test_agent_mismatch(self):
        from multi_agent.session import _normalize_envelope
        raw = {"protocol_version": "1.0", "task_id": "t1", "agent": "wrong", "result": {"status": "done"}}
        with pytest.raises(ValueError, match="agent mismatch"):
            _normalize_envelope(raw, task_id="t1", agent="ws",
                                current_role="builder", current_state="RUNNING",
                                workflow_mode="normal")

    def test_role_mismatch(self):
        from multi_agent.session import _normalize_envelope
        raw = {"protocol_version": "1.0", "task_id": "t1", "agent": "ws",
               "role": "reviewer", "result": {"status": "done"}}
        with pytest.raises(ValueError, match="role mismatch"):
            _normalize_envelope(raw, task_id="t1", agent="ws",
                                current_role="builder", current_state="RUNNING",
                                workflow_mode="normal")

    def test_result_not_dict(self):
        from multi_agent.session import _normalize_envelope
        raw = {"protocol_version": "1.0", "task_id": "t1", "agent": "ws",
               "role": "builder", "result": "not a dict"}
        with pytest.raises(ValueError):
            _normalize_envelope(raw, task_id="t1", agent="ws",
                                current_role="builder", current_state="RUNNING",
                                workflow_mode="normal")

    def test_non_protocol_envelope_wrapped(self):
        from multi_agent.session import _normalize_envelope
        raw = {"status": "completed", "summary": "done"}
        env = _normalize_envelope(raw, task_id="t1", agent="ws",
                                  current_role="builder", current_state="RUNNING",
                                  workflow_mode="normal")
        assert env["protocol_version"] == "1.0"
        assert env["result"]["status"] == "completed"


# ── _mark_task_status corrupt YAML (lines 631-633) ──────


class TestUpdateTaskYamlStatusCorruptYaml:
    def test_corrupt_yaml_ignored(self, tmp_path):
        from multi_agent.session import _update_task_yaml_status
        td = tmp_path / "tasks"
        td.mkdir()
        (td / "task-bad.yaml").write_text(":::\nbad: [yaml")
        with patch("multi_agent.config.tasks_dir", return_value=td), \
             patch("multi_agent.session.save_task_yaml"):
            _update_task_yaml_status("task-bad", "failed")


# ── _save_handoff collision (lines 647-648) ─────────────


class TestSaveHandoffCollision:
    def test_collision_increments(self, tmp_path):
        from multi_agent.session import _save_handoff
        with patch("multi_agent.session.root_dir", return_value=tmp_path):
            _save_handoff("task-1", "ws", {"test": 1})
            # Write to force collision on next call with same timestamp
            with patch("multi_agent.session.datetime") as mock_dt:
                mock_dt.now.return_value = mock_dt.now.return_value
                from datetime import datetime
                fixed = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
                mock_dt.now.return_value = fixed
                mock_dt.side_effect = None
                # Create first file with fixed timestamp
                handoff_dir = tmp_path / "runtime" / "handoffs" / "task-col"
                handoff_dir.mkdir(parents=True, exist_ok=True)
                ts = "20250101T000000000000Z"
                (handoff_dir / f"{ts}-ws.json").write_text("{}")
                p2 = _save_handoff("task-col", "ws", {"test": 2})
            assert p2.exists()


# ── _acquire_session_lock (lines 702, 706) ──────────────


class TestAcquireSessionLock:
    def test_already_active_raises(self):
        from multi_agent.session import _acquire_session_lock
        existing = type("S", (), {"next": True, "values": {}})()
        with patch("multi_agent.session.read_lock", return_value="task-1"), \
             pytest.raises(ValueError, match="already active"):
            _acquire_session_lock("task-1", existing, "RUNNING", reset=False)

    def test_acquires_when_no_lock(self):
        from multi_agent.session import _acquire_session_lock
        with patch("multi_agent.session.read_lock", return_value=None), \
             patch("multi_agent.session.acquire_lock"):
            acquired = _acquire_session_lock("task-1", None, "UNKNOWN", reset=False)
        assert acquired is True

    def test_returns_false_when_already_locked(self):
        from multi_agent.session import _acquire_session_lock
        with patch("multi_agent.session.read_lock", return_value="task-1"):
            acquired = _acquire_session_lock("task-1", None, "UNKNOWN", reset=False)
        assert acquired is False


# ── _extract_memory_candidates (line 899) ────────────────


class TestSubmitMemoryCandidates:
    def test_nested_candidates_from_result(self):
        from multi_agent.session import _submit_memory_candidates
        envelope = {}
        result = {"memory_candidates": ["item1"]}
        with patch("multi_agent.session.add_pending_candidates") as mock_add:
            _submit_memory_candidates("t1", "ws", envelope, result)
        mock_add.assert_called_once()
