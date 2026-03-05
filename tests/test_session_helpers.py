"""Unit tests for session.py helper functions — _task_requirement, _parse_json_payload,
_load_mode_cfg, _find_project_root_from_path, _clear_task_checkpoint, _save_handoff,
_mark_task_status, normalize_file_path_for_lock, session_trace."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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
        assert "Implement /users with FastAPI" == _task_requirement(task)

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
